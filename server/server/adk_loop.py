"""
server/adk_loop.py
ADK Runner wrapper that drives the Zener multi-agent loop over a WebSocket.

Protocol (JSON messages):

  CLI → Server:
    { "type": "task",          "task": str, "screenshot_b64": str }
    { "type": "action_result", "action": str, "result": dict }

  Server → CLI:
    { "type": "thought",        "author": str, "text": str }
    { "type": "tool_call",      "step": int, "tool": str, "input": dict }
    { "type": "tool_result",    "step": int, "tool": str, "ok": bool, "summary": str }
    { "type": "screenshot",     "description": str }
    { "type": "action_request", "action": str, "params": dict }
    { "type": "final",          "text": str }
    { "type": "done",           "success": bool }
    { "type": "error",          "message": str }

Action round-trip (synchronous wait):
  1. ADK tool coroutine calls _request_action(action, params)
  2. _request_action sends action_request over the WebSocket
  3. _request_action awaits on a per-session asyncio.Queue
  4. CLI executes the action and sends action_result
  5. WebSocket receive loop puts result in the Queue
  6. _request_action returns the result to the tool
  7. ADK loop continues
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
import contextvars
from typing import Any

from fastapi import WebSocket
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.memory import InMemoryMemoryService
from google.genai import types as genai_types

from . import adk_agent

logger = logging.getLogger(__name__)

APP_NAME = "zener"
USER_ID  = "local"

# ── Session services (per Cloud Run instance — reset on restart) ──────────────
_session_service = InMemorySessionService()
_memory_service  = InMemoryMemoryService()


async def run_agent_loop(websocket: WebSocket, session_id: str, message: dict) -> None:
    """
    Drive the full ADK multi-agent loop for one task.

    This coroutine:
    - Registers WebSocket channels in adk_agent
    - Starts the ADK Runner
    - Streams all events back to the CLI
    - Handles action_request ↔ action_result round-trips
    - Calls on_done when finished
    """
    task          = message.get("task", "")
    screenshot_b64 = message.get("screenshot_b64", "")

    if not task:
        await _send(websocket, {"type": "error", "message": "No task provided"})
        return

    # ── Register action channels for this session ────────────────────────────
    action_queue: asyncio.Queue[dict] = asyncio.Queue()

    async def ws_sender(payload: dict) -> None:
        await _send(websocket, payload)

    adk_agent.register_session(session_id, action_queue, ws_sender)

    # ── Build ADK session ────────────────────────────────────────────────────
    adk_session_id = f"{session_id}-{uuid.uuid4().hex[:8]}"
    await _session_service.create_session(
        app_name=APP_NAME,
        user_id=USER_ID,
        session_id=adk_session_id,
    )

    runner = Runner(
        agent=adk_agent.get_orchestrator(),
        app_name=APP_NAME,
        session_service=_session_service,
        memory_service=_memory_service,
    )

    # ── Build enriched task message with initial screenshot context ──────────
    from .image_utils import compress_screenshot

    context_parts = [f"Task: {task}"]
    if screenshot_b64:
        context_parts.insert(0, "[Initial screenshot attached as first message]")

    user_content_parts = [genai_types.Part(text="\n".join(context_parts))]

    if screenshot_b64:
        try:
            import base64
            img_bytes = base64.b64decode(screenshot_b64)
            compressed_bytes, mime_type = compress_screenshot(img_bytes)
            user_content_parts.append(
                genai_types.Part(
                    inline_data=genai_types.Blob(
                        data=compressed_bytes,
                        mime_type=mime_type,
                    )
                )
            )
        except Exception as e:
            logger.warning("Could not decode initial screenshot: %s", e)

    user_message = genai_types.Content(role="user", parts=user_content_parts)

    # ── Run ADK loop + receive loop concurrently ─────────────────────────────
    adk_task   = asyncio.create_task(
        _run_adk(runner, adk_session_id, user_message, websocket, session_id)
    )
    recv_task  = asyncio.create_task(
        _receive_loop(websocket, action_queue, adk_task)
    )

    try:
        # Wait for the ADK loop to finish; receive loop runs alongside
        await adk_task
    except asyncio.CancelledError:
        pass
    finally:
        recv_task.cancel()
        adk_agent.unregister_session(session_id)
        # Commit memory
        try:
            await _memory_service.add_session_to_memory(
                await _session_service.get_session(
                    app_name=APP_NAME,
                    user_id=USER_ID,
                    session_id=adk_session_id,
                )
            )
        except Exception:
            pass


async def _run_adk(
    runner: Runner,
    adk_session_id: str,
    user_message: genai_types.Content,
    websocket: WebSocket,
    session_id: str,
) -> None:
    """Run the ADK Runner event loop and stream events to the CLI."""
    step      = 0
    success   = False
    final_text = ""

    # Set the context var so tool coroutines can find their session
    token = adk_agent._current_session.set(session_id)

    try:
        async for event in runner.run_async(
            user_id=USER_ID,
            session_id=adk_session_id,
            new_message=user_message,
        ):
            author  = getattr(event, "author", "Zener")
            content = getattr(event, "content", None)

            if not content:
                continue

            for part in content.parts or []:
                # ── Thought / text ──────────────────────────────────────────
                if hasattr(part, "text") and part.text:
                    text = part.text.strip()
                    if text and not event.is_final_response():
                        await _send(websocket, {
                            "type":   "thought",
                            "author": author,
                            "text":   text,
                        })

                # ── Tool call ────────────────────────────────────────────────
                if hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    step += 1
                    tool_input: dict[str, Any] = {}
                    try:
                        if hasattr(fc, "args") and fc.args:
                            tool_input = dict(fc.args)
                    except Exception:
                        pass
                    await _send(websocket, {
                        "type":  "tool_call",
                        "step":  step,
                        "tool":  fc.name,
                        "input": tool_input,
                    })

                # ── Tool result ──────────────────────────────────────────────
                if hasattr(part, "function_response") and part.function_response:
                    fr = part.function_response
                    result_data = getattr(fr, "response", {}) or {}
                    ok      = True
                    summary = ""

                    if isinstance(result_data, dict):
                        ok = result_data.get("ok", "error" not in result_data)
                        if "message" in result_data:
                            summary = str(result_data["message"])[:200]
                        elif "description" in result_data:
                            summary = str(result_data["description"])[:200]
                            await _send(websocket, {
                                "type":        "screenshot",
                                "description": summary,
                            })
                        elif "error" in result_data:
                            summary = str(result_data["error"])[:200]
                            ok = False
                        elif "stdout" in result_data:
                            summary = str(result_data["stdout"])[:200].strip() or "(no output)"
                        elif "result" in result_data:
                            summary = str(result_data["result"])[:200]
                        else:
                            try:
                                summary = json.dumps(result_data)[:200]
                            except Exception:
                                summary = str(result_data)[:200]
                    else:
                        summary = str(result_data)[:200]

                    await _send(websocket, {
                        "type":    "tool_result",
                        "step":    step,
                        "tool":    fr.name,
                        "ok":      ok,
                        "summary": summary,
                    })

            # ── Final response ───────────────────────────────────────────────
            if event.is_final_response():
                if content and content.parts:
                    final_text = "".join(
                        p.text for p in content.parts
                        if hasattr(p, "text") and p.text
                    ).strip()
                success = True

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.exception("ADK runner error for session %s", session_id)
        await _send(websocket, {"type": "error", "message": str(e)})
    finally:
        adk_agent._current_session.reset(token)

    if final_text:
        await _send(websocket, {"type": "final", "text": final_text})

    await _send(websocket, {"type": "done", "success": success})


async def _receive_loop(
    websocket: WebSocket,
    action_queue: asyncio.Queue,
    adk_task: asyncio.Task,
) -> None:
    """
    Continuously receive messages from the CLI WebSocket.
    Puts action_result payloads into the queue for tool coroutines to consume.
    """
    try:
        while not adk_task.done():
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Non-JSON message from CLI: %s", raw[:100])
                continue

            msg_type = msg.get("type", "")

            if msg_type == "action_result":
                result = msg.get("result", {})
                await action_queue.put(result)
            else:
                logger.debug("Unhandled CLI message type: %s", msg_type)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("Receive loop error: %s", e)


async def _send(websocket: WebSocket, payload: dict) -> None:
    """Send a JSON payload to the CLI, ignoring send errors."""
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception as e:
        logger.debug("WebSocket send failed: %s", e)
