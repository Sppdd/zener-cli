"""
server/executor.py
xdotool-based action executor for the Xvfb virtual display.
Maps Gemini Computer Use action names → shell commands on DISPLAY=:99.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import shlex
from typing import Any

logger = logging.getLogger(__name__)

DISPLAY = ":99"
ACTION_TIMEOUT = 5  # seconds per action


def _xdo(cmd: str) -> str:
    """Run an xdotool command synchronously on the virtual display."""
    full_cmd = f"DISPLAY={DISPLAY} xdotool {cmd}"
    try:
        result = subprocess.run(
            full_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=ACTION_TIMEOUT,
        )
        return result.stdout.strip() or "ok"
    except subprocess.TimeoutExpired:
        logger.warning("xdotool timed out: %s", cmd)
        return "timeout"
    except Exception as exc:
        logger.error("xdotool error: %s — %s", cmd, exc)
        return f"error: {exc}"


def _xdg_open(url: str) -> str:
    """Open a URL in Chromium on the virtual display."""
    safe_url = shlex.quote(url)
    cmd = (
        f"DISPLAY={DISPLAY} chromium-browser --no-sandbox --disable-gpu "
        f"--disable-dev-shm-usage --disable-setuid-sandbox {safe_url}"
    )
    subprocess.Popen(cmd, shell=True)
    return "opened"


async def execute_action(name: str, args: dict[str, Any]) -> str:
    """
    Execute a Computer Use action asynchronously.
    Maps Gemini CU function names to xdotool / shell commands.

    Returns a short result string for logging.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _execute_sync, name, args)


def _execute_sync(name: str, args: dict[str, Any]) -> str:
    """Synchronous dispatch — runs in thread pool via execute_action()."""
    logger.debug("Executing action: %s %s", name, args)

    match name:
        case "click":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            return _xdo(f"mousemove {x} {y} click 1")

        case "double_click":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            return _xdo(f"mousemove {x} {y} click --repeat 2 1")

        case "right_click":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            return _xdo(f"mousemove {x} {y} click 3")

        case "type":
            text = args.get("text", "")
            safe = shlex.quote(text)
            return _xdo(f"type --clearmodifiers -- {safe}")

        case "key":
            keys = args.get("key", args.get("keys", ""))
            return _xdo(f"key {keys}")

        case "press":
            key = args.get("key", "")
            return _xdo(f"key {key}")

        case "scroll":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            direction = args.get("direction", "down")
            btn = "4" if direction == "up" else "5"
            return _xdo(f"mousemove {x} {y} click {btn}")

        case "move_mouse":
            x, y = int(args.get("x", 0)), int(args.get("y", 0))
            return _xdo(f"mousemove {x} {y}")

        case "screenshot":
            # Gemini CU will call this to request a new screenshot.
            # The caller (gemini_loop.py) actually captures the screen;
            # returning "ok" signals the loop to take a fresh capture.
            return "ok"

        case "open_url":
            url = args.get("url", "")
            return _xdg_open(url)

        case "wait":
            import time
            ms = int(args.get("ms", 500))
            time.sleep(ms / 1000)
            return "ok"

        case "done":
            result = args.get("result", "Task completed")
            return f"done: {result}"

        case _:
            logger.warning("Unknown action: %s", name)
            return f"unknown_action:{name}"


# Known risky actions that need user confirmation
RISKY_ACTIONS = {
    "key",          # Ctrl+W, Ctrl+Q, etc. can close windows
    "double_click", # Could trigger file opens / deletions
}

def is_risky(name: str, args: dict[str, Any]) -> bool:
    """
    Heuristic: flag certain actions for user confirmation.
    Real safety decisions come from Gemini's safety_decision field.
    """
    if name == "key":
        keys = str(args.get("key", args.get("keys", ""))).lower()
        dangerous = {"ctrl+w", "ctrl+q", "ctrl+alt+t", "super", "delete"}
        return any(d in keys for d in dangerous)
    return False
