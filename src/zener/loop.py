"""
loop.py — WebSocket client loop for Zener CLI.

The CLI is now a thin client.  All AI reasoning runs on the Cloud Run server.
This module:
  1. Gets a Google identity token via ADC (gcloud auth application-default login)
  2. Connects to wss://<server>/ws/agent/<session_id> with the token
  3. Takes an initial screenshot and sends { type: "task", task, screenshot_b64 }
  4. Streams events back and dispatches them to LoopCallbacks (terminal rendering)
  5. When the server sends action_request, executes the action locally (macOS)
     and sends back { type: "action_result", action, result }
  6. Exits when { type: "done" } arrives

Auth: Google identity token sent as WebSocket subprotocol "bearer.<token>".
This works with gcloud auth application-default login — no Firebase needed.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from . import config, macos

logger = logging.getLogger(__name__)


# ── Callbacks interface (same shape as before — cli.py unchanged) ─────────────

class LoopCallbacks:
    """Hooks the CLI attaches to observe loop progress in real-time."""
    def on_thought(self, author: str, text: str) -> None: ...
    def on_tool_call(self, step: int, tool_name: str, tool_input: Dict[str, Any]) -> None: ...
    def on_tool_result(self, step: int, tool_name: str, ok: bool, summary: str) -> None: ...
    def on_screenshot(self, description: str) -> None: ...
    def on_final(self, text: str) -> None: ...
    def on_done(self, success: bool) -> None: ...
    def confirm_dangerous(self, message: str) -> bool: return False


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_identity_token(server_url: str) -> str:
    """
    Get a Google identity token for the Cloud Run audience.

    Strategy (in order):
    1. google.oauth2.id_token.fetch_id_token  — works on GCP or with a SA key
    2. gcloud auth print-identity-token       — works for any logged-in user

    Requires: gcloud auth login  (one-time)
    """
    # Strategy 1: google-auth library (works with service account / GCE metadata)
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token

        request = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(request, server_url)
        if token:
            return str(token)
    except Exception:
        pass

    # Strategy 2: gcloud CLI fallback — works for any `gcloud auth login` user
    try:
        import subprocess
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token",
             f"--audiences={server_url}", "--quiet"],
            capture_output=True, text=True, timeout=15,
        )
        token = result.stdout.strip()
        if token and result.returncode == 0:
            return token

        # Some gcloud versions don't support --audiences; try without it
        result2 = subprocess.run(
            ["gcloud", "auth", "print-identity-token", "--quiet"],
            capture_output=True, text=True, timeout=15,
        )
        token2 = result2.stdout.strip()
        if token2 and result2.returncode == 0:
            return token2
    except Exception:
        pass

    raise RuntimeError(
        "Could not obtain a Google identity token.\n"
        "Run: gcloud auth login\n"
        "Then retry."
    )


# ── Local action executor ─────────────────────────────────────────────────────

def _execute_local_action(action: str, params: dict) -> dict:
    """
    Execute an action on the local Mac and return the result dict.
    Called when the server sends an action_request event.
    """
    try:
        if action == "take_screenshot":
            path = macos.take_screenshot()
            data = path.read_bytes()
            b64  = base64.b64encode(data).decode()
            return {"ok": True, "screenshot_b64": b64, "path": str(path)}

        elif action == "describe_screenshot":
            # Optionally use a provided screenshot, or take a fresh one
            b64 = params.get("screenshot_b64", "")
            if b64:
                # Save to temp, describe
                tmp = config.get_temp_dir() / f"desc_{uuid.uuid4().hex[:8]}.png"
                tmp.write_bytes(base64.b64decode(b64))
            else:
                tmp = macos.take_screenshot()

            from . import _vision
            desc = _vision.describe_image(tmp)
            return {"ok": True, "description": desc}

        elif action == "mouse_click":
            ok = macos.click_at(params["x"], params["y"])
            return {"ok": ok}

        elif action == "mouse_double_click":
            ok = macos.double_click_at(params["x"], params["y"])
            return {"ok": ok}

        elif action == "mouse_right_click":
            ok = macos.right_click_at(params["x"], params["y"])
            return {"ok": ok}

        elif action == "mouse_scroll":
            ok = macos.scroll_at(
                params["x"], params["y"],
                params.get("direction", "down"),
                params.get("amount", 3),
            )
            return {"ok": ok}

        elif action == "mouse_drag":
            ok = macos.drag_from_to(
                params["x1"], params["y1"],
                params["x2"], params["y2"],
            )
            return {"ok": ok}

        elif action == "keyboard_type":
            ok = macos.type_text(params["text"])
            return {"ok": ok}

        elif action == "keyboard_press_key":
            ok = macos.press_key(params["key"])
            return {"ok": ok}

        elif action == "open_application":
            ok = macos.open_application(params["name"])
            return {"ok": ok}

        elif action == "open_url":
            ok = macos.open_url(params["url"])
            return {"ok": ok}

        else:
            return {"ok": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        logger.exception("Local action %s failed", action)
        return {"ok": False, "error": str(e)}


# ── Main loop ─────────────────────────────────────────────────────────────────

class AgentLoop:
    def __init__(self, callbacks: Optional[LoopCallbacks] = None):
        self.callbacks = callbacks or LoopCallbacks()

    def run(self, task: str) -> bool:
        return asyncio.run(self.run_async(task))

    async def run_async(self, task: str) -> bool:
        cfg        = config.get_config()
        server_url = cfg.server_url

        # ── Get auth token ───────────────────────────────────────────────────
        try:
            token = _get_identity_token(server_url)
        except RuntimeError as e:
            self.callbacks.on_final(str(e))
            self.callbacks.on_done(False)
            return False

        # ── Take initial screenshot ──────────────────────────────────────────
        screenshot_b64 = ""
        try:
            ss_path = macos.take_screenshot()
            screenshot_b64 = base64.b64encode(ss_path.read_bytes()).decode()
            # Describe it locally so the user sees "Screen: ..." immediately
            from . import _vision
            desc = _vision.describe_image(ss_path)
            self.callbacks.on_screenshot(desc)
        except Exception as e:
            logger.warning("Initial screenshot failed: %s", e)

        # ── Build WebSocket URL (https → wss) ────────────────────────────────
        session_id = uuid.uuid4().hex[:12]
        ws_url = server_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/ws/agent/{session_id}"

        # ── Connect and run ──────────────────────────────────────────────────
        try:
            import websockets
            async with websockets.connect(
                ws_url,
                additional_headers={"Authorization": f"Bearer {token}"},
                ping_interval=20,
                ping_timeout=60,
                open_timeout=30,
            ) as ws:
                # Send task
                await ws.send(json.dumps({
                    "type":           "task",
                    "task":           task,
                    "screenshot_b64": screenshot_b64,
                }))

                success = await self._event_loop(ws)

        except Exception as e:
            logger.exception("WebSocket connection failed")
            self.callbacks.on_final(f"Connection error: {e}")
            self.callbacks.on_done(False)
            return False

        return success

    async def _event_loop(self, ws) -> bool:
        """Process server events until done."""
        step    = 0
        success = False

        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Non-JSON from server: %s", str(raw)[:100])
                    continue

                msg_type = msg.get("type", "")

                if msg_type == "thought":
                    self.callbacks.on_thought(
                        msg.get("author", "Zener"),
                        msg.get("text", ""),
                    )

                elif msg_type == "tool_call":
                    step = msg.get("step", step + 1)
                    self.callbacks.on_tool_call(
                        step,
                        msg.get("tool", ""),
                        msg.get("input", {}),
                    )

                elif msg_type == "tool_result":
                    step = msg.get("step", step)
                    self.callbacks.on_tool_result(
                        step,
                        msg.get("tool", ""),
                        msg.get("ok", True),
                        msg.get("summary", ""),
                    )

                elif msg_type == "screenshot":
                    self.callbacks.on_screenshot(msg.get("description", ""))

                elif msg_type == "action_request":
                    # Execute locally and send result back
                    action = msg.get("action", "")
                    params = msg.get("params", {})
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, _execute_local_action, action, params
                    )
                    await ws.send(json.dumps({
                        "type":   "action_result",
                        "action": action,
                        "result": result,
                    }))

                elif msg_type == "final":
                    self.callbacks.on_final(msg.get("text", ""))

                elif msg_type == "done":
                    success = msg.get("success", False)
                    break

                elif msg_type == "error":
                    self.callbacks.on_final(f"Server error: {msg.get('message', '')}")
                    success = False
                    break

        except Exception as e:
            logger.exception("Event loop error")
            self.callbacks.on_final(f"Connection lost: {e}")
            success = False

        self.callbacks.on_done(success)
        return success
