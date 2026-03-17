"""
loop.py — WebSocket client loop for Zener CLI.

The CLI is a thin local executor. All AI reasoning runs on the Cloud Run
server via the Gemini Live API. This module:

  1. Gets a Google identity token via ADC (gcloud auth application-default login)
  2. Connects to wss://<server>/ws/agent/<session_id> with the token
  3. Takes an initial screenshot and sends { type: "task", task, screenshot_b64 }
  4. Streams events back and dispatches them to LoopCallbacks (terminal rendering)
  5. When the server sends action_request, executes the action locally (macOS)
     and sends back { type: "action_result", action, result }
  6. Exits when { type: "done" } arrives

Local actions supported:
  - take_screenshot           → screencapture (macOS)
  - mouse_click/double/right  → PyAutoGUI
  - mouse_scroll/drag         → PyAutoGUI
  - keyboard_type/press_key   → PyAutoGUI
  - open_application          → AppleScript
  - open_url                  → AppleScript
  - wait                      → time.sleep
  - shell_run_local           → zsh (with dangerous-command block + confirmation)
  - file_read                 → reads from Mac filesystem
  - file_write                → writes to Mac filesystem (confirmation required)
  - file_delete               → deletes from Mac filesystem (always confirmed)
  - file_list_dir             → lists Mac directory
  - file_mkdir                → creates directory on Mac
  - get_desktop_context       → yabai + AppleScript desktop state
  - switch_space              → yabai / keyboard shortcut
  - focus_window              → yabai / AppleScript

Auth: Google identity token sent as Authorization: Bearer <token> header.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from . import config, macos
from websockets.exceptions import ConnectionClosedError as WSConnectionClosedError

logger = logging.getLogger(__name__)


# ── Callbacks interface ────────────────────────────────────────────────────────

class LoopCallbacks:
    """Hooks the CLI attaches to observe loop progress in real-time."""
    def on_status(self, text: str) -> None: ...
    def on_thought(self, author: str, text: str) -> None: ...
    def on_tool_call(self, step: int, tool_name: str, tool_input: Dict[str, Any]) -> None: ...
    def on_tool_result(self, step: int, tool_name: str, ok: bool, summary: str) -> None: ...
    def on_screenshot(self, description: str) -> None: ...
    def on_final(self, text: str) -> None: ...
    def on_done(self, success: bool) -> None: ...

    # Permission gates — return True to allow, False to deny
    def confirm_shell(self, command: str) -> bool:
        return False

    def confirm_file_write(self, path: str, content_preview: str) -> bool:
        return False

    def confirm_file_delete(self, path: str) -> bool:
        return False


# ── Dangerous shell command patterns (blocked outright) ───────────────────────

_BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -r /",
    "dd if=",
    "mkfs.",
    "shutdown",
    "reboot",
    "halt",
    "poweroff",
    ":(){ :|:& };:",   # fork bomb
    "> /dev/sda",
    "chmod -x /",
    "chown -R root /",
]

# Patterns that need user confirmation but aren't outright blocked
_WARN_PATTERNS = [
    "rm ",
    "sudo ",
    "curl | sh",
    "curl | bash",
    "wget | sh",
    "wget | bash",
    "chmod 777",
    "killall",
    "pkill",
    "launchctl",
]


def _is_blocked(command: str) -> bool:
    return any(p in command for p in _BLOCKED_PATTERNS)


def _needs_confirm(command: str) -> bool:
    return any(p in command for p in _WARN_PATTERNS)


# ── Auth helpers ───────────────────────────────────────────────────────────────

def _get_identity_token(server_url: str) -> str:
    """
    Get a Google identity token for the Cloud Run audience.

    Strategy (in order):
    1. google.oauth2.id_token.fetch_id_token  — works on GCP / SA key
    2. gcloud auth print-identity-token       — works for any logged-in user
    """
    try:
        import google.auth.transport.requests
        import google.oauth2.id_token
        request = google.auth.transport.requests.Request()
        token = google.oauth2.id_token.fetch_id_token(request, server_url)
        if token:
            return str(token)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["gcloud", "auth", "print-identity-token",
             f"--audiences={server_url}", "--quiet"],
            capture_output=True, text=True, timeout=15,
        )
        token = result.stdout.strip()
        if token and result.returncode == 0:
            return token

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


# ── Local action executor ──────────────────────────────────────────────────────

def _execute_local_action(
    action: str,
    params: dict,
    callbacks: LoopCallbacks,
) -> dict:
    """
    Execute an action on the local Mac and return the result dict.
    All actions are dispatched here; blocking I/O is run in executor thread.
    """
    try:
        # ── Screen ────────────────────────────────────────────────────────────
        if action == "take_screenshot":
            path = macos.take_screenshot()
            img_bytes = path.read_bytes()
            # Compress before sending back — reduce WebSocket payload
            b64 = base64.b64encode(img_bytes).decode()
            # Get a quick description from local vision (fast, informational only)
            description = ""
            try:
                from . import _vision
                description = _vision.describe_image(path)
            except Exception:
                pass
            return {
                "ok":           True,
                "screenshot_b64": b64,
                "description":  description,
                "path":         str(path),
            }

        # ── Mouse ─────────────────────────────────────────────────────────────
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

        # ── Keyboard ──────────────────────────────────────────────────────────
        elif action == "keyboard_type":
            ok = macos.type_text(params["text"])
            return {"ok": ok}

        elif action == "keyboard_press_key":
            ok = macos.press_key(params["key"])
            return {"ok": ok}

        # ── Apps / URLs ────────────────────────────────────────────────────────
        elif action == "open_application":
            ok = macos.open_application(params["name"])
            return {"ok": ok}

        elif action == "open_url":
            ok = macos.open_url(params["url"])
            return {"ok": ok}

        elif action == "wait":
            seconds = float(params.get("seconds", 1.0))
            seconds = max(0.1, min(seconds, 30.0))  # clamp 0.1–30s
            macos.wait(seconds)
            return {"ok": True, "message": f"Waited {seconds}s"}

        # ── Shell (runs on user's Mac) ─────────────────────────────────────────
        elif action == "shell_run_local":
            command = params.get("command", "")
            timeout = int(params.get("timeout", 30))

            if _is_blocked(command):
                return {"ok": False, "error": f"Blocked: dangerous command pattern in: {command[:80]}"}

            if _needs_confirm(command):
                if not callbacks.confirm_shell(command):
                    return {"ok": False, "error": "User denied shell command execution"}

            returncode, stdout, stderr = macos.run_shell_command(command, timeout)
            return {
                "ok":         returncode == 0,
                "stdout":     stdout[:5000],
                "stderr":     stderr[:1000],
                "returncode": returncode,
            }

        # ── File operations ────────────────────────────────────────────────────
        elif action == "file_read":
            path_str = params.get("path", "")
            p = Path(os.path.expanduser(path_str))
            if not p.exists():
                return {"ok": False, "error": f"File not found: {path_str}"}
            try:
                content = macos.read_file(p)
                return {"ok": True, "content": content[:20000], "size": len(content)}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        elif action == "file_write":
            path_str = params.get("path", "")
            content   = params.get("content", "")
            p = Path(os.path.expanduser(path_str))

            # Always require confirmation for writes
            preview = content[:300] + ("..." if len(content) > 300 else "")
            if not callbacks.confirm_file_write(str(p), preview):
                return {"ok": False, "error": "User denied file write"}

            try:
                macos.write_file(p, content)
                return {"ok": True, "path": str(p), "bytes_written": len(content.encode())}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        elif action == "file_delete":
            path_str = params.get("path", "")
            p = Path(os.path.expanduser(path_str))

            if not p.exists():
                return {"ok": False, "error": f"File not found: {path_str}"}

            # Always require confirmation for deletes
            if not callbacks.confirm_file_delete(str(p)):
                return {"ok": False, "error": "User denied file deletion"}

            try:
                if p.is_dir():
                    import shutil
                    shutil.rmtree(p)
                else:
                    p.unlink()
                return {"ok": True, "message": f"Deleted: {p}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        elif action == "file_list_dir":
            path_str = params.get("path", "~")
            p = Path(os.path.expanduser(path_str))
            if not p.exists():
                return {"ok": False, "error": f"Directory not found: {path_str}"}
            try:
                entries = macos.list_directory(p)
                return {"ok": True, "path": str(p), "entries": entries}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        elif action == "file_mkdir":
            path_str = params.get("path", "")
            p = Path(os.path.expanduser(path_str))
            try:
                p.mkdir(parents=True, exist_ok=True)
                return {"ok": True, "message": f"Created directory: {p}"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        # ── Desktop context ────────────────────────────────────────────────────
        elif action == "get_desktop_context":
            from . import yabai
            ctx = yabai.get_desktop_context()
            return {"ok": True, **ctx}

        elif action == "switch_space":
            index = int(params.get("index", 1))
            from . import yabai
            result = yabai.focus_space(index)
            return result

        elif action == "focus_window":
            app_name = params.get("app_name", "")
            from . import yabai
            result = yabai.focus_window_by_app(app_name)
            return result

        else:
            return {"ok": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        logger.exception("Local action %s failed", action)
        return {"ok": False, "error": str(e)}


# ── Main loop ──────────────────────────────────────────────────────────────────

class AgentLoop:
    def __init__(self, callbacks: Optional[LoopCallbacks] = None):
        self.callbacks = callbacks or LoopCallbacks()

    def run(self, task: str) -> bool:
        return asyncio.run(self.run_async(task))

    async def run_async(self, task: str) -> bool:
        cfg        = config.get_config()
        server_url = cfg.server_url
        loop       = asyncio.get_event_loop()

        # ── Get auth token ──────────────────────────────────────────────────
        self.callbacks.on_status("Authenticating...")
        try:
            token = await loop.run_in_executor(None, _get_identity_token, server_url)
        except RuntimeError as e:
            self.callbacks.on_status("")
            self.callbacks.on_final(str(e))
            self.callbacks.on_done(False)
            return False

        # ── Take initial screenshot ─────────────────────────────────────────
        screenshot_b64 = ""
        try:
            self.callbacks.on_status("Capturing screen...")
            ss_path = await loop.run_in_executor(None, macos.take_screenshot)
            screenshot_b64 = base64.b64encode(ss_path.read_bytes()).decode()
            self.callbacks.on_status("")
        except Exception as e:
            self.callbacks.on_status("")
            logger.warning("Initial screenshot failed: %s", e)

        # ── Build WebSocket URL ─────────────────────────────────────────────
        session_id = uuid.uuid4().hex[:12]
        ws_url = server_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = f"{ws_url}/ws/agent/{session_id}"

        # ── Connect with retry ──────────────────────────────────────────────
        import websockets

        max_retries = 3
        base_delay  = 1.5
        success     = False

        for attempt in range(max_retries):
            try:
                self.callbacks.on_status("Connecting...")
                async with websockets.connect(
                    ws_url,
                    additional_headers={"Authorization": f"Bearer {token}"},
                    ping_interval=20,
                    ping_timeout=60,
                    open_timeout=30,
                    max_size=cfg.websocket_max_size,
                ) as ws:
                    self.callbacks.on_status("Sending task...")
                    await ws.send(json.dumps({
                        "type":           "task",
                        "task":           task,
                        "screenshot_b64": screenshot_b64,
                    }))
                    self.callbacks.on_status("Agent thinking...")

                    success = await self._event_loop(ws)
                    break  # clean exit — don't retry

            except WSConnectionClosedError as e:
                code = getattr(e, "code", None)
                if code == 1009:
                    self.callbacks.on_status("")
                    self.callbacks.on_final(
                        "Server message too large. Try a smaller task or reduce screenshot resolution."
                    )
                    self.callbacks.on_done(False)
                    return False
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    self.callbacks.on_status(f"Connection lost, retrying ({attempt + 2}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue
                self.callbacks.on_status("")
                self.callbacks.on_final(f"Connection failed: {e}")
                self.callbacks.on_done(False)
                return False

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning("Connection error (attempt %d), retrying: %s", attempt + 1, e)
                    self.callbacks.on_status(f"Connection error, retrying ({attempt + 2}/{max_retries})...")
                    await asyncio.sleep(delay)
                    continue
                self.callbacks.on_status("")
                logger.exception("WebSocket connection failed")
                self.callbacks.on_final(f"Connection error: {e}")
                self.callbacks.on_done(False)
                return False

        return success

    async def _event_loop(self, ws) -> bool:
        """Process server events until done."""
        step    = 0
        success = False
        loop    = asyncio.get_event_loop()

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
                    action  = msg.get("action", "")
                    params  = msg.get("params", {})
                    call_id = msg.get("call_id", "")

                    # Execute locally in a thread (blocking I/O)
                    result = await loop.run_in_executor(
                        None,
                        _execute_local_action,
                        action,
                        params,
                        self.callbacks,
                    )

                    await ws.send(json.dumps({
                        "type":    "action_result",
                        "action":  action,
                        "call_id": call_id,
                        "result":  result,
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

        except WSConnectionClosedError as e:
            logger.warning("WebSocket closed: %s", e)
            code = getattr(e, "code", None)
            if code == 1009:
                self.callbacks.on_final(
                    "Server message exceeded size limit. Try a simpler task."
                )
            else:
                self.callbacks.on_final(f"Connection closed unexpectedly: {e}")
            success = False

        except Exception as e:
            logger.exception("Event loop error")
            self.callbacks.on_final(f"Connection lost: {e}")
            success = False

        self.callbacks.on_done(success)
        return success
