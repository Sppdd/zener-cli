"""
server/live_loop.py
Gemini Live API session manager for Zener.

Replaces the ADK multi-agent stack (adk_loop.py + adk_agent.py) with a
single Gemini Live API WebSocket session. This eliminates:
  - 2-4 model round-trips per screenshot (Orchestrator → sub-agent chains)
  - Cold-start latency per agent delegation
  - In-memory-only session state that crashes on multi-step tasks

Architecture:
  CLI ←── wss ──→ Server ←── Gemini Live API (bidirectional WebSocket)
                   │
                   ├── Receives tool_calls from Gemini
                   ├── Forwards as action_request to CLI
                   ├── Receives action_result from CLI
                   └── Returns toolResponse to Gemini

The Live API maintains a stateful session with full context in one connection.
SessionResumptionTokens allow reconnecting mid-task without losing history.

Rate limits (Vertex AI):
  - 5,000 concurrent sessions per project
  - 4M tokens per minute (one session amortizes far better than N sub-agents)
  - Default max session: 10 minutes (extendable via resumption)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, Optional

from fastapi import WebSocket
from google import genai
from google.genai import types as genai_types

from .image_utils import compress_screenshot
from .safety import LoopDetector

logger = logging.getLogger(__name__)

# ── Model selection ───────────────────────────────────────────────────────────

# Primary: best Live model with function calling + vision
LIVE_MODEL_PRIMARY = os.environ.get(
    "ZENER_LIVE_MODEL",
    "gemini-2.0-flash-live-preview-04-09",
)
# Fallback: if primary quota is exhausted or unavailable
LIVE_MODEL_FALLBACK = os.environ.get(
    "ZENER_LIVE_MODEL_FALLBACK",
    "gemini-2.0-flash-live-preview-04-09",
)

# ── Timeouts ──────────────────────────────────────────────────────────────────

ACTION_TIMEOUT_DEFAULT = 45.0   # seconds for normal actions
ACTION_TIMEOUT_INPUT   = 120.0  # seconds for actions that require UI settling
KEEPALIVE_INTERVAL     = 25.0   # seconds between keepalive pings
MAX_STEPS              = 150    # hard cap to prevent runaway sessions
SESSION_TIMEOUT        = 540.0  # 9 min — reconnect before 10 min Live limit

# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are Zener, an AI agent with full hands-on control of the user's Mac.

You receive screenshots and task descriptions, then use your tools to accomplish desktop tasks.
You can see the screen, control the mouse and keyboard, open apps, run shell commands,
read and write files, manage windows and spaces.

CORE WORKFLOW:
1. Always take_screenshot first to understand the current state
2. Identify what needs to happen and take the most direct action
3. After every UI-changing action, take_screenshot again to verify the result
4. If something didn't work, try a different approach — never repeat the same failed action
5. When fully done, state the result clearly

SCREENSHOT STRATEGY:
- Compress screenshots are sent as JPEG — coordinates are in logical (non-retina) pixels
- The screen is the user's actual Mac display — coordinates matter precisely
- Always verify UI state after click/type actions before moving on

INPUT RULES:
- Click fields before typing into them
- Use keyboard_press_key for navigation: "tab", "return", "escape", "cmd+c", "cmd+v"
- For dropdowns/menus: click to open, then click the item
- For text entry: click field → keyboard_type text → press "return" if needed

SHELL RULES:
- shell_run_local runs on the USER'S MAC (zsh) — use it for file operations, scripting
- Commands are blocked if dangerous (rm -rf /, shutdown, etc.)
- Always check the result; retry with a different approach on failure

FILE RULES:
- file_write and file_delete require user confirmation — the CLI will prompt
- file_read is always allowed
- Use ~/Desktop, ~/Documents, ~/Downloads for user files

WINDOW MANAGEMENT:
- get_desktop_context tells you all open windows, spaces, and displays
- switch_space(index) switches to a macOS Space (virtual desktop)
- focus_window(app_name) brings an app to the front

COMPLETION:
- When the task is fully done, your FINAL message MUST end with exactly: "Task complete."
- Before saying "Task complete." always take_screenshot to confirm the result is visible
- Do NOT say "Task complete." mid-task or after a single step
- If you cannot complete the task, say what you see and end with: "Task complete."
- ALWAYS keep taking actions until the task is genuinely done

STYLE:
- Be concise — short reasoning, direct actions
- Never repeat the same (action, params) twice in a row
- If stuck after 3 attempts, explain what you see and ask for guidance"""

# ── Tool declarations (everything the CLI can execute locally) ─────────────────

def _make_tools() -> list[genai_types.Tool]:
    """Build the FunctionDeclarations for all local Mac actions."""

    def _fn(name: str, description: str, params: dict) -> genai_types.FunctionDeclaration:
        return genai_types.FunctionDeclaration(
            name=name,
            description=description,
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    k: genai_types.Schema(**v) for k, v in params.items()
                },
                required=[k for k, v in params.items() if v.get("required_")],
            ) if params else None,
        )

    declarations = [
        # ── Screen ──────────────────────────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="take_screenshot",
            description="Capture the current Mac screen. Returns base64 JPEG. Always call this before and after UI actions.",
        ),
        # ── Mouse ────────────────────────────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="mouse_click",
            description="Left-click at screen coordinates (x, y).",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "x": genai_types.Schema(type="INTEGER", description="Horizontal pixel coordinate"),
                    "y": genai_types.Schema(type="INTEGER", description="Vertical pixel coordinate"),
                },
                required=["x", "y"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="mouse_double_click",
            description="Double-click at screen coordinates (x, y).",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "x": genai_types.Schema(type="INTEGER"),
                    "y": genai_types.Schema(type="INTEGER"),
                },
                required=["x", "y"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="mouse_right_click",
            description="Right-click at screen coordinates (x, y). Opens context menus.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "x": genai_types.Schema(type="INTEGER"),
                    "y": genai_types.Schema(type="INTEGER"),
                },
                required=["x", "y"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="mouse_scroll",
            description="Scroll at (x, y). direction: 'up', 'down', 'left', 'right'. amount: scroll clicks.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "x": genai_types.Schema(type="INTEGER"),
                    "y": genai_types.Schema(type="INTEGER"),
                    "direction": genai_types.Schema(type="STRING", enum=["up", "down", "left", "right"]),
                    "amount": genai_types.Schema(type="INTEGER", description="Number of scroll clicks, default 3"),
                },
                required=["x", "y", "direction"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="mouse_drag",
            description="Drag from (x1,y1) to (x2,y2) holding the left mouse button.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "x1": genai_types.Schema(type="INTEGER"),
                    "y1": genai_types.Schema(type="INTEGER"),
                    "x2": genai_types.Schema(type="INTEGER"),
                    "y2": genai_types.Schema(type="INTEGER"),
                },
                required=["x1", "y1", "x2", "y2"],
            ),
        ),
        # ── Keyboard ─────────────────────────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="keyboard_type",
            description="Type a string of text at the current cursor position.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "text": genai_types.Schema(type="STRING", description="Text to type"),
                },
                required=["text"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="keyboard_press_key",
            description="Press a key or combo. Examples: 'return', 'escape', 'tab', 'cmd+c', 'cmd+v', 'shift+tab', 'ctrl+up'.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "key": genai_types.Schema(type="STRING", description="Key name or combo like 'cmd+c'"),
                },
                required=["key"],
            ),
        ),
        # ── Apps / URLs ───────────────────────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="open_application",
            description="Open or bring to front a macOS application by name (e.g. 'Safari', 'Finder', 'Terminal').",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "name": genai_types.Schema(type="STRING", description="Application name"),
                },
                required=["name"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="open_url",
            description="Open a URL in the default browser.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "url": genai_types.Schema(type="STRING", description="Full URL including https://"),
                },
                required=["url"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="wait",
            description="Wait for N seconds before the next action. Use after launching apps or submitting forms.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "seconds": genai_types.Schema(type="NUMBER", description="Seconds to wait (0.5 - 10)"),
                },
                required=["seconds"],
            ),
        ),
        # ── Shell (runs on user's Mac) ────────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="shell_run_local",
            description=(
                "Run a zsh command on the USER'S MAC (not the cloud container). "
                "Dangerous commands are blocked. User will be prompted to confirm risky commands. "
                "Returns: {stdout, stderr, returncode}."
            ),
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "command": genai_types.Schema(type="STRING", description="zsh command to run"),
                    "timeout": genai_types.Schema(type="INTEGER", description="Timeout in seconds (default 30)"),
                },
                required=["command"],
            ),
        ),
        # ── File operations (runs on user's Mac) ──────────────────────────────
        genai_types.FunctionDeclaration(
            name="file_read",
            description="Read a file from the user's Mac. Returns file content as text.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "path": genai_types.Schema(type="STRING", description="Absolute or ~/relative path"),
                },
                required=["path"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="file_write",
            description="Write text to a file on the user's Mac. Creates parent directories. User confirmation required.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "path": genai_types.Schema(type="STRING", description="Absolute or ~/relative path"),
                    "content": genai_types.Schema(type="STRING", description="Text content to write"),
                },
                required=["path", "content"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="file_delete",
            description="Delete a file on the user's Mac. ALWAYS requires user confirmation.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "path": genai_types.Schema(type="STRING", description="Absolute or ~/relative path"),
                },
                required=["path"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="file_list_dir",
            description="List directory contents on the user's Mac.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "path": genai_types.Schema(type="STRING", description="Absolute or ~/relative path (default: ~)"),
                },
            ),
        ),
        genai_types.FunctionDeclaration(
            name="file_mkdir",
            description="Create a directory (and parents) on the user's Mac.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "path": genai_types.Schema(type="STRING", description="Absolute or ~/relative path"),
                },
                required=["path"],
            ),
        ),
        # ── Desktop / Window management ────────────────────────────────────────
        genai_types.FunctionDeclaration(
            name="get_desktop_context",
            description=(
                "Get the current desktop state: frontmost app, screen size, all open windows, "
                "all spaces/virtual desktops, and displays. Call this at the start of any "
                "window management task."
            ),
        ),
        genai_types.FunctionDeclaration(
            name="switch_space",
            description="Switch to a macOS Space (virtual desktop) by index (1-based).",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "index": genai_types.Schema(type="INTEGER", description="Space number (1-based)"),
                },
                required=["index"],
            ),
        ),
        genai_types.FunctionDeclaration(
            name="focus_window",
            description="Bring a window to the front by application name.",
            parameters=genai_types.Schema(
                type="OBJECT",
                properties={
                    "app_name": genai_types.Schema(type="STRING", description="Application name, e.g. 'Safari'"),
                },
                required=["app_name"],
            ),
        ),
    ]

    return [genai_types.Tool(function_declarations=declarations)]


# ── Gemini client singleton ────────────────────────────────────────────────────

_genai_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "zener-ai-hackathon")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        _genai_client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=genai_types.HttpOptions(api_version="v1beta1"),
        )
    return _genai_client


# ── Per-session resumption token store ────────────────────────────────────────

_resumption_tokens: dict[str, str] = {}


def _store_token(session_id: str, token: str) -> None:
    _resumption_tokens[session_id] = token


def _get_token(session_id: str) -> Optional[str]:
    return _resumption_tokens.get(session_id)


def _clear_token(session_id: str) -> None:
    _resumption_tokens.pop(session_id, None)


# ── WebSocket message helpers ─────────────────────────────────────────────────

async def _ws_send(websocket: WebSocket, payload: dict) -> None:
    try:
        await websocket.send_text(json.dumps(payload))
    except Exception as e:
        logger.debug("WebSocket send failed: %s", e)


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_agent_loop(websocket: WebSocket, session_id: str, message: dict) -> None:
    """
    Drive the full Gemini Live API loop for one task.

    This coroutine:
      1. Opens a Gemini Live session with tool declarations
      2. Sends the initial task + screenshot
      3. Relays tool_call ↔ action_result round-trips with the CLI
      4. Streams thought text back to the CLI
      5. Handles reconnection via SessionResumptionToken
      6. Cleans up on task completion or disconnect
    """
    task          = message.get("task", "")
    screenshot_b64 = message.get("screenshot_b64", "")

    if not task:
        await _ws_send(websocket, {"type": "error", "message": "No task provided"})
        return

    await _ws_send(websocket, {"type": "thought", "author": "Zener", "text": f"Starting: {task}"})

    # Action queue: CLI sends action_results → these go back to Live API
    action_queue: asyncio.Queue[dict] = asyncio.Queue()

    success = False
    try:
        success = await _run_live_session(
            websocket=websocket,
            session_id=session_id,
            task=task,
            screenshot_b64=screenshot_b64,
            action_queue=action_queue,
        )
    except Exception as e:
        logger.exception("Live session error for %s: %s", session_id, e)
        await _ws_send(websocket, {"type": "error", "message": str(e)})

    await _ws_send(websocket, {"type": "done", "success": success})


async def _run_live_session(
    websocket: WebSocket,
    session_id: str,
    task: str,
    screenshot_b64: str,
    action_queue: asyncio.Queue,
    model: str = LIVE_MODEL_PRIMARY,
) -> bool:
    """
    Open a Gemini Live session and run the perception-action loop.
    Returns True on successful task completion.
    """
    client = _get_client()

    # Build session resumption config
    resumption_token = _get_token(session_id)
    resumption_config = None
    if resumption_token:
        logger.info("Resuming Live session %s with token", session_id)
        resumption_config = genai_types.SessionResumptionConfig(handle=resumption_token)

    live_config = genai_types.LiveConnectConfig(
        response_modalities=[genai_types.Modality.TEXT],
        system_instruction=genai_types.Content(
            parts=[genai_types.Part(text=SYSTEM_PROMPT)],
            role="user",
        ),
        tools=_make_tools(),
        session_resumption=resumption_config or genai_types.SessionResumptionConfig(),
        realtime_input_config=genai_types.RealtimeInputConfig(
            turn_coverage=genai_types.TurnCoverage.TURN_INCLUDES_ALL_INPUT,
        ),
    )

    step         = 0
    success      = False
    loop_detector = LoopDetector()
    session_start = time.monotonic()

    try:
        async with client.aio.live.connect(model=model, config=live_config) as live_session:
            logger.info("Live session opened for %s (model=%s)", session_id, model)

            # ── Send initial task + screenshot ───────────────────────────────
            parts: list[genai_types.Part] = [genai_types.Part(text=f"Task: {task}")]

            if screenshot_b64:
                try:
                    img_bytes = base64.b64decode(screenshot_b64)
                    compressed, mime = compress_screenshot(img_bytes)
                    parts.append(genai_types.Part(
                        inline_data=genai_types.Blob(data=compressed, mime_type=mime)
                    ))
                except Exception as e:
                    logger.warning("Screenshot decode failed: %s", e)

            await live_session.send_client_content(
                turns=genai_types.Content(role="user", parts=parts),
                turn_complete=True,
            )

            # ── Three concurrent tasks ───────────────────────────────────────
            # 1. Receive loop: model events → CLI
            # 2. CLI receive loop: action_results → model
            # 3. Session keepalive

            receive_task = asyncio.create_task(
                _receive_from_cli(websocket, action_queue),
                name=f"cli-recv-{session_id}",
            )

            try:
                # ── Process model events ─────────────────────────────────────
                async for response in live_session.receive():

                    # ── Session timeout guard ────────────────────────────────
                    elapsed = time.monotonic() - session_start
                    if elapsed > SESSION_TIMEOUT:
                        logger.info("Session %s approaching 10min limit, graceful wrap", session_id)
                        await _ws_send(websocket, {
                            "type": "thought", "author": "Zener",
                            "text": "Session nearing time limit — wrapping up.",
                        })
                        break

                    # ── Resumption token ─────────────────────────────────────
                    if response.session_resumption_update:
                        update = response.session_resumption_update
                        if update.resumable and update.new_handle:
                            _store_token(session_id, update.new_handle)
                            logger.debug("Stored resumption token for %s", session_id)

                    # ── Text / thought ────────────────────────────────────────
                    if response.text:
                        text = response.text.strip()
                        if text:
                            await _ws_send(websocket, {
                                "type":   "thought",
                                "author": "Zener",
                                "text":   text,
                            })

                    # ── Tool calls ────────────────────────────────────────────
                    if response.tool_call:
                        for fn_call in response.tool_call.function_calls:
                            step += 1
                            tool_name = fn_call.name
                            tool_input: dict[str, Any] = dict(fn_call.args) if fn_call.args else {}

                            # Loop detection on non-screenshot actions
                            if tool_name != "take_screenshot":
                                if loop_detector.check(tool_name, tool_input):
                                    logger.warning("Loop detected on %s: %s", tool_name, tool_input)
                                    await _ws_send(websocket, {
                                        "type":   "thought",
                                        "author": "Zener",
                                        "text":   f"I notice I'm repeating '{tool_name}' — changing approach.",
                                    })
                                    # Send error back to break the loop
                                    response_part = genai_types.FunctionResponse(
                                        id=fn_call.id,
                                        name=fn_call.name,
                                        response={"error": "Repeated action detected. Try a different approach."},
                                    )
                                    await live_session.send_tool_response(
                                        function_responses=[response_part]
                                    )
                                    continue

                            # Notify CLI of tool call (for display)
                            await _ws_send(websocket, {
                                "type":  "tool_call",
                                "step":  step,
                                "tool":  tool_name,
                                "input": tool_input,
                            })

                            # Request action execution on CLI
                            await _ws_send(websocket, {
                                "type":   "action_request",
                                "action": tool_name,
                                "params": tool_input,
                                "call_id": fn_call.id,
                            })

                            # Wait for CLI to execute and return result
                            timeout = _action_timeout(tool_name)
                            try:
                                result = await asyncio.wait_for(
                                    action_queue.get(), timeout=timeout
                                )
                            except asyncio.TimeoutError:
                                logger.warning("Action %s timed out after %.0fs", tool_name, timeout)
                                result = {"ok": False, "error": f"Action timed out after {timeout:.0f}s"}

                            # Surface result to CLI
                            ok      = result.get("ok", "error" not in result)
                            summary = _result_summary(tool_name, result)

                            await _ws_send(websocket, {
                                "type":    "tool_result",
                                "step":    step,
                                "tool":    tool_name,
                                "ok":      ok,
                                "summary": summary,
                            })

                            # If this was a screenshot, also emit screenshot event
                            if tool_name == "take_screenshot" and "description" in result:
                                await _ws_send(websocket, {
                                    "type":        "screenshot",
                                    "description": result["description"],
                                })

                            # Build the tool response for Gemini
                            # Strip screenshot bytes from response to save tokens
                            response_data = _sanitize_result_for_model(tool_name, result)

                            fn_response = genai_types.FunctionResponse(
                                id=fn_call.id,
                                name=fn_call.name,
                                response=response_data,
                            )

                            await live_session.send_tool_response(
                                function_responses=[fn_response]
                            )

                    # ── Turn complete / final ─────────────────────────────────
                    # Only exit when the model says the explicit sentinel "Task complete."
                    # Do NOT exit on any other turn_complete — the Live API fires
                    # turn_complete after every tool response turn; we keep going.
                    if response.server_content and response.server_content.turn_complete:
                        check_text = response.text or ""
                        if step > 0 and "task complete" in check_text.lower():
                            success = True
                            await _ws_send(websocket, {"type": "final", "text": check_text.strip()})
                            break
                        # Otherwise: model turn ended but task isn't done — keep listening

                    # ── Step cap ──────────────────────────────────────────────
                    if step >= MAX_STEPS:
                        logger.warning("Hit MAX_STEPS=%d for session %s", MAX_STEPS, session_id)
                        await _ws_send(websocket, {
                            "type": "final",
                            "text": f"Reached maximum step limit ({MAX_STEPS}). Stopping.",
                        })
                        break

            finally:
                receive_task.cancel()
                try:
                    await receive_task
                except asyncio.CancelledError:
                    pass

    except Exception as e:
        err_str = str(e)
        logger.exception("Live API error for session %s", session_id)

        # Try fallback model if primary quota exhausted
        if "RESOURCE_EXHAUSTED" in err_str or "429" in err_str:
            if model == LIVE_MODEL_PRIMARY and LIVE_MODEL_FALLBACK != LIVE_MODEL_PRIMARY:
                logger.info("Primary model quota exhausted, trying fallback: %s", LIVE_MODEL_FALLBACK)
                await _ws_send(websocket, {
                    "type": "thought", "author": "Zener",
                    "text": "Switching to backup model due to quota limits...",
                })
                return await _run_live_session(
                    websocket=websocket,
                    session_id=session_id,
                    task=task,
                    screenshot_b64=screenshot_b64,
                    action_queue=action_queue,
                    model=LIVE_MODEL_FALLBACK,
                )
        raise

    _clear_token(session_id)
    return success


async def _receive_from_cli(
    websocket: WebSocket,
    action_queue: asyncio.Queue,
) -> None:
    """
    Continuously receive messages from the CLI WebSocket.
    Puts action_result payloads into the queue for the main loop to consume.
    Also handles confirm_response messages.
    """
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
            except asyncio.TimeoutError:
                continue
            except Exception:
                break

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Non-JSON from CLI: %s", raw[:100])
                continue

            msg_type = msg.get("type", "")

            if msg_type == "action_result":
                await action_queue.put(msg.get("result", {}))
            elif msg_type == "confirm_response":
                # Handled inline in action execution — put in queue
                await action_queue.put({"confirmed": msg.get("confirmed", False)})
            else:
                logger.debug("Unhandled CLI message: %s", msg_type)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning("CLI receive loop error: %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _action_timeout(tool_name: str) -> float:
    """Longer timeouts for actions that trigger UI settling or user prompts."""
    if tool_name in ("file_write", "file_delete", "shell_run_local"):
        return ACTION_TIMEOUT_INPUT   # user confirmation may be required
    if tool_name in ("open_application", "open_url", "wait"):
        return ACTION_TIMEOUT_INPUT   # app launch / page load
    return ACTION_TIMEOUT_DEFAULT


def _result_summary(tool_name: str, result: dict) -> str:
    """Extract a short human-readable summary from a tool result dict."""
    if "error" in result:
        return str(result["error"])[:200]
    if "description" in result:
        return str(result["description"])[:200]
    if "stdout" in result:
        out = result["stdout"].strip()
        return out[:200] if out else "(no output)"
    if "content" in result:
        return f"{len(result['content'])} chars read"
    if "entries" in result:
        return f"{len(result['entries'])} entries"
    if "message" in result:
        return str(result["message"])[:200]
    if result.get("ok"):
        return "ok"
    return str(result)[:200]


def _sanitize_result_for_model(tool_name: str, result: dict) -> dict:
    """
    Strip large binary payloads (base64 screenshots) from tool results
    before sending back to Gemini — saves tokens.
    The screenshot is already embedded as a vision turn; model doesn't
    need the raw bytes again in the function response.
    """
    if tool_name == "take_screenshot":
        # Return just the description and ok status
        return {
            "ok":          result.get("ok", True),
            "description": result.get("description", "Screenshot captured"),
        }
    # Truncate very long text outputs
    sanitized = {}
    for k, v in result.items():
        if isinstance(v, str) and len(v) > 4000:
            sanitized[k] = v[:4000] + "\n...[truncated]"
        else:
            sanitized[k] = v
    return sanitized


def _looks_done(text: str) -> bool:
    """
    Heuristic: does the model's text signal task completion?
    Uses whole-word / phrase matching to avoid false positives like
    "Okay" matching "ok", or "finished loading" matching "finished".
    """
    import re
    t = text.lower()
    # Explicit multi-word completion phrases (substring match is fine)
    phrases = [
        "task complete",
        "task is complete",
        "task accomplished",
        "task is done",
        "all done",
        "i have completed",
        "i've completed",
        "i have finished",
        "i've finished",
        "completed successfully",
        "successfully completed",
        "the result is",
        "the answer is",
    ]
    if any(p in t for p in phrases):
        return True
    # Single-word completions: must be standalone words, not substrings
    single_words = ["done", "finished", "complete"]
    for w in single_words:
        if re.search(r"\b" + w + r"\b", t):
            return True
    return False
