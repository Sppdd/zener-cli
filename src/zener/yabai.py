"""
yabai.py — Yabai window manager integration for Zener.

All functions degrade gracefully: if yabai is not installed or not running,
they return an error dict instead of raising exceptions. The WindowAgent
knows how to handle these responses.

All underlying calls go through macos.run_shell_command — no direct
subprocess calls here.

Yabai CLI reference:
  yabai -m query --windows       → JSON list of all windows
  yabai -m query --spaces        → JSON list of all spaces
  yabai -m query --displays      → JSON list of all displays
  yabai -m window --focus <id>   → focus a window
  yabai -m window --space <idx>  → move focused window to space
  yabai -m space --focus <idx>   → switch to a space
  yabai -m window --toggle zoom-fullscreen
  yabai -m space --balance
  yabai -m space --rotate 90
"""
import json
import logging
import shutil
from typing import Any, Dict, List, Optional

from . import macos

logger = logging.getLogger(__name__)

# ── Availability check ────────────────────────────────────────────────────────

def _yabai_available() -> bool:
    """Return True if the yabai binary is on PATH."""
    return shutil.which("yabai") is not None


def _run(args: str) -> Dict[str, Any]:
    """Run a yabai command and return a result dict.

    Returns:
        {"ok": True, "output": <str or parsed JSON>} on success
        {"ok": False, "error": <str>}                on failure
    """
    if not _yabai_available():
        return {"ok": False, "error": "yabai is not installed. Install via: brew install koekeishiya/formulae/yabai"}

    cmd = f"yabai {args}"
    try:
        returncode, stdout, stderr = macos.run_shell_command(cmd, timeout=10)
        if returncode != 0:
            msg = stderr.strip() or stdout.strip() or f"yabai exited {returncode}"
            logger.warning(f"yabai command failed: {cmd!r} → {msg}")
            return {"ok": False, "error": msg}
        output = stdout.strip()
        # Try to parse JSON output (query commands return JSON)
        if output.startswith("[") or output.startswith("{"):
            try:
                return {"ok": True, "output": json.loads(output)}
            except json.JSONDecodeError:
                pass
        return {"ok": True, "output": output}
    except Exception as e:
        logger.error(f"yabai run error: {e}")
        return {"ok": False, "error": str(e)}


# ── Query functions (used by DesktopContext + WindowAgent) ────────────────────

def query_windows(space: Optional[int] = None) -> Dict[str, Any]:
    """List all windows, optionally filtered to a specific space index.

    Returns a dict with key 'windows' (list) or 'error' (str).
    """
    args = "-m query --windows"
    if space is not None:
        args += f" --space {space}"
    result = _run(args)
    if not result["ok"]:
        return {"error": result["error"], "windows": []}
    windows = result["output"]
    if not isinstance(windows, list):
        return {"error": "unexpected yabai output", "windows": []}
    # Return only the most useful fields to keep payload small
    trimmed = [
        {
            "id": w.get("id"),
            "app": w.get("app"),
            "title": w.get("title"),
            "space": w.get("space"),
            "display": w.get("display"),
            "frame": w.get("frame"),
            "is-floating": w.get("is-floating"),
            "has-focus": w.get("has-focus"),
            "is-minimized": w.get("is-minimized"),
            "is-fullscreen": w.get("is-fullscreen"),
        }
        for w in windows
    ]
    return {"windows": trimmed}


def query_spaces() -> Dict[str, Any]:
    """List all spaces across all displays.

    Returns {'spaces': [...]} or {'error': str, 'spaces': []}.
    """
    result = _run("-m query --spaces")
    if not result["ok"]:
        return {"error": result["error"], "spaces": []}
    spaces = result["output"]
    if not isinstance(spaces, list):
        return {"error": "unexpected yabai output", "spaces": []}
    trimmed = [
        {
            "index": s.get("index"),
            "label": s.get("label"),
            "display": s.get("display"),
            "windows": s.get("windows"),
            "has-focus": s.get("has-focus"),
            "is-visible": s.get("is-visible"),
            "type": s.get("type"),
        }
        for s in spaces
    ]
    return {"spaces": trimmed}


def query_displays() -> Dict[str, Any]:
    """List all displays.

    Returns {'displays': [...]} or {'error': str, 'displays': []}.
    """
    result = _run("-m query --displays")
    if not result["ok"]:
        return {"error": result["error"], "displays": []}
    displays = result["output"]
    if not isinstance(displays, list):
        return {"error": "unexpected yabai output", "displays": []}
    return {"displays": displays}


def get_desktop_context() -> Dict[str, Any]:
    """Return a combined snapshot of the desktop for agent context.

    Combines windows, spaces, and displays into a single dict.
    Always includes the frontmost app from AppleScript (works without yabai).
    """
    ctx: Dict[str, Any] = {}

    # frontmost app — works without yabai
    ctx["frontmost_app"] = macos.get_frontmost_app()
    ctx["screen_size"] = list(macos.get_screen_size())

    # yabai data (graceful degradation)
    ctx.update(query_windows())
    ctx.update(query_spaces())
    ctx.update(query_displays())

    return ctx


# ── Window control ────────────────────────────────────────────────────────────

def focus_window(window_id: int) -> Dict[str, Any]:
    """Focus a window by its yabai window ID."""
    result = _run(f"-m window {window_id} --focus")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}


def focus_window_by_app(app_name: str) -> Dict[str, Any]:
    """Focus the first window belonging to an app (case-insensitive match)."""
    windows_data = query_windows()
    if "error" in windows_data and not windows_data.get("windows"):
        # Fall back to AppleScript activate
        ok = macos.open_application(app_name)
        return {"ok": ok, "message": f"Activated {app_name} via AppleScript"}

    for w in windows_data.get("windows", []):
        if w.get("app", "").lower() == app_name.lower():
            return focus_window(w["id"])

    # App not found in yabai — try AppleScript
    ok = macos.open_application(app_name)
    return {"ok": ok, "message": f"Activated {app_name} via AppleScript (not in yabai)"}


def move_window_to_space(space_index: int, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Move a window to the given space index.

    If window_id is None, moves the currently focused window.
    """
    target = str(window_id) + " " if window_id else ""
    result = _run(f"-m window {target}--space {space_index}")
    if result["ok"]:
        return {"ok": True, "message": f"Moved window to space {space_index}"}
    return {"ok": False, "message": result.get("error", "move failed")}


def focus_space(space_index: int) -> Dict[str, Any]:
    """Switch focus to the given space index."""
    result = _run(f"-m space --focus {space_index}")
    if result["ok"]:
        return {"ok": True, "message": f"Focused space {space_index}"}
    return {"ok": False, "message": result.get("error", "focus space failed")}


def move_window_to_space_and_follow(space_index: int, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Move window to space and switch focus there."""
    move_result = move_window_to_space(space_index, window_id)
    if not move_result["ok"]:
        return move_result
    focus_result = focus_space(space_index)
    return {"ok": focus_result["ok"], "message": f"Moved and followed to space {space_index}"}


def toggle_fullscreen(window_id: Optional[int] = None) -> Dict[str, Any]:
    """Toggle zoom-fullscreen on the focused (or specified) window."""
    target = str(window_id) + " " if window_id else ""
    result = _run(f"-m window {target}--toggle zoom-fullscreen")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}


def toggle_float(window_id: Optional[int] = None) -> Dict[str, Any]:
    """Toggle floating state on the focused (or specified) window."""
    target = str(window_id) + " " if window_id else ""
    result = _run(f"-m window {target}--toggle float")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}


def balance_space() -> Dict[str, Any]:
    """Balance window sizes on the current space."""
    result = _run("-m space --balance")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}


def rotate_space(degrees: int = 90) -> Dict[str, Any]:
    """Rotate the layout of the current space (90, 180, 270)."""
    result = _run(f"-m space --rotate {degrees}")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}


def resize_window(
    edge: str,
    dx: int,
    dy: int,
    window_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Resize a window by adjusting an edge.

    Args:
        edge: 'left' | 'right' | 'top' | 'bottom'
        dx: horizontal delta in pixels
        dy: vertical delta in pixels
    """
    target = str(window_id) + " " if window_id else ""
    result = _run(f"-m window {target}--resize {edge}:{dx}:{dy}")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}


def warp_window(direction: str, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Warp a window in a direction (west/south/north/east) in the BSP tree."""
    target = str(window_id) + " " if window_id else ""
    result = _run(f"-m window {target}--warp {direction}")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}


def swap_window(direction: str, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Swap a window with its neighbour in a direction."""
    target = str(window_id) + " " if window_id else ""
    result = _run(f"-m window {target}--swap {direction}")
    return {"ok": result["ok"], "message": result.get("output", result.get("error", ""))}
