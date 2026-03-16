"""
executor.py — ADK FunctionTool definitions grouped by agent.

Each function in this module becomes a tool available to a specific
sub-agent. Functions are plain Python — ADK wraps them automatically
via FunctionTool when passed in the tools= list.

Tool groups:
  screen_tools()  → ScreenAgent
  input_tools()   → InputAgent
  window_tools()  → WindowAgent
  shell_tools()   → ShellAgent

All functions return plain dicts (serialisable to JSON for the LLM).
Errors are surfaced as {"ok": False, "error": "..."} — never raised.

Dangerous shell commands are blocked via DANGEROUS_COMMANDS.
"""
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import macos, yabai

logger = logging.getLogger(__name__)

# ── Safety ────────────────────────────────────────────────────────────────────

DANGEROUS_COMMANDS = [
    "rm -rf",
    "rm -r /",
    "dd if=",
    "mkfs.",
    "shutdown",
    "reboot",
    "chmod -x",
    "> /dev/",
    "curl | sh",
    "wget | sh",
    "chmod 777",
    "killall",
    "pkill -9",
    ":(){ :|:& };:",  # fork bomb
]


def _is_dangerous(command: str) -> bool:
    return any(d in command for d in DANGEROUS_COMMANDS)


# ── Screen tools (ScreenAgent) ────────────────────────────────────────────────

def take_screenshot() -> Dict[str, Any]:
    """Take a screenshot of the current screen.

    Returns:
        {"ok": True, "path": "<absolute path to PNG>"}
        {"ok": False, "error": "<reason>"}
    """
    try:
        path = macos.take_screenshot()
        return {"ok": True, "path": str(path)}
    except Exception as e:
        logger.error(f"take_screenshot failed: {e}")
        return {"ok": False, "error": str(e)}


def describe_screenshot(path: str) -> Dict[str, Any]:
    """Describe what is visible in a screenshot file.

    The ScreenAgent calls this after take_screenshot to get a text
    description of the current screen state.

    Args:
        path: Absolute path to a PNG screenshot file.

    Returns:
        {"ok": True, "description": "<text>"}
        {"ok": False, "error": "<reason>"}
    """
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"File not found: {path}"}
    try:
        from . import _vision
        description = _vision.describe_image(p)
        return {"ok": True, "description": description}
    except Exception as e:
        logger.error(f"describe_screenshot failed: {e}")
        return {"ok": False, "error": str(e)}


def screen_tools() -> List[Any]:
    """Return the FunctionTool list for ScreenAgent."""
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(func=take_screenshot),
        FunctionTool(func=describe_screenshot),
    ]


# ── Input tools (InputAgent) ──────────────────────────────────────────────────

def mouse_click(x: int, y: int) -> Dict[str, Any]:
    """Left-click at the given screen coordinates.

    Args:
        x: Horizontal coordinate in logical pixels (0 = left edge).
        y: Vertical coordinate in logical pixels (0 = top edge).
    """
    ok = macos.click_at(x, y)
    return {"ok": ok, "message": f"Clicked ({x}, {y})" if ok else f"Click failed at ({x}, {y})"}


def mouse_double_click(x: int, y: int) -> Dict[str, Any]:
    """Double-click at the given screen coordinates.

    Args:
        x: Horizontal coordinate.
        y: Vertical coordinate.
    """
    ok = macos.double_click_at(x, y)
    return {"ok": ok, "message": f"Double-clicked ({x}, {y})" if ok else f"Double-click failed at ({x}, {y})"}


def mouse_right_click(x: int, y: int) -> Dict[str, Any]:
    """Right-click at the given screen coordinates.

    Args:
        x: Horizontal coordinate.
        y: Vertical coordinate.
    """
    ok = macos.right_click_at(x, y)
    return {"ok": ok, "message": f"Right-clicked ({x}, {y})" if ok else f"Right-click failed at ({x}, {y})"}


def mouse_scroll(x: int, y: int, direction: str, amount: int = 3) -> Dict[str, Any]:
    """Scroll at the given coordinates.

    Args:
        x: Horizontal coordinate to scroll at.
        y: Vertical coordinate to scroll at.
        direction: One of 'up', 'down', 'left', 'right'.
        amount: Number of scroll clicks (default 3).
    """
    ok = macos.scroll_at(x, y, direction, amount)
    return {"ok": ok, "message": f"Scrolled {direction} at ({x}, {y})" if ok else "Scroll failed"}


def mouse_drag(x1: int, y1: int, x2: int, y2: int) -> Dict[str, Any]:
    """Drag from (x1, y1) to (x2, y2) while holding the left mouse button.

    Args:
        x1: Start horizontal coordinate.
        y1: Start vertical coordinate.
        x2: End horizontal coordinate.
        y2: End vertical coordinate.
    """
    ok = macos.drag_from_to(x1, y1, x2, y2)
    return {"ok": ok, "message": f"Dragged ({x1},{y1}) → ({x2},{y2})" if ok else "Drag failed"}


def keyboard_type(text: str) -> Dict[str, Any]:
    """Type a string of text at the current cursor position.

    Args:
        text: The text to type. Supports standard ASCII and most Unicode.
    """
    ok = macos.type_text(text)
    preview = text[:60] + ("..." if len(text) > 60 else "")
    return {"ok": ok, "message": f'Typed: "{preview}"' if ok else "Type failed"}


def keyboard_press_key(key: str) -> Dict[str, Any]:
    """Press a key or key combination.

    Args:
        key: Key name such as 'return', 'tab', 'escape', 'space',
             or a combo like 'cmd+c', 'shift+tab', 'cmd+shift+3'.
             Modifier names: cmd, ctrl, alt/opt, shift.
    """
    ok = macos.press_key(key)
    return {"ok": ok, "message": f"Pressed: {key}" if ok else f"Key press failed: {key}"}


def wait(seconds: float) -> Dict[str, Any]:
    """Pause execution for the given number of seconds.

    Args:
        seconds: Duration to wait (can be fractional, e.g. 0.5).
    """
    try:
        macos.wait(seconds)
        return {"ok": True, "message": f"Waited {seconds}s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def input_tools() -> List[Any]:
    """Return the FunctionTool list for InputAgent."""
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(func=mouse_click),
        FunctionTool(func=mouse_double_click),
        FunctionTool(func=mouse_right_click),
        FunctionTool(func=mouse_scroll),
        FunctionTool(func=mouse_drag),
        FunctionTool(func=keyboard_type),
        FunctionTool(func=keyboard_press_key),
        FunctionTool(func=wait),
    ]


# ── Window tools (WindowAgent) ────────────────────────────────────────────────

def get_desktop_context() -> Dict[str, Any]:
    """Get a full snapshot of the current desktop state.

    Returns a combined dict containing:
      - frontmost_app: name of the currently focused application
      - screen_size: [width, height] of the primary display
      - windows: list of all windows (from yabai, if available)
      - spaces: list of all spaces (from yabai, if available)
      - displays: list of all displays (from yabai, if available)

    Works with or without yabai installed.
    """
    return yabai.get_desktop_context()


def yabai_query_windows(space: Optional[int] = None) -> Dict[str, Any]:
    """List all windows visible to yabai.

    Args:
        space: Optional space index to filter by. If None, returns all windows.
    """
    return yabai.query_windows(space=space)


def yabai_query_spaces() -> Dict[str, Any]:
    """List all spaces across all displays."""
    return yabai.query_spaces()


def yabai_query_displays() -> Dict[str, Any]:
    """List all connected displays."""
    return yabai.query_displays()


def yabai_focus_window(window_id: int) -> Dict[str, Any]:
    """Focus a specific window by its yabai window ID.

    Args:
        window_id: The integer ID from yabai_query_windows.
    """
    return yabai.focus_window(window_id)


def yabai_focus_window_by_app(app_name: str) -> Dict[str, Any]:
    """Focus the first window belonging to the named application.

    Args:
        app_name: Application name, e.g. 'Safari', 'Terminal', 'Visual Studio Code'.
    """
    return yabai.focus_window_by_app(app_name)


def yabai_move_to_space(space_index: int, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Move a window to the specified space.

    Args:
        space_index: Target space number (1-based).
        window_id: Window to move. If None, moves the currently focused window.
    """
    return yabai.move_window_to_space(space_index, window_id)


def yabai_focus_space(space_index: int) -> Dict[str, Any]:
    """Switch focus to the specified space.

    Args:
        space_index: Space number to switch to (1-based).
    """
    return yabai.focus_space(space_index)


def yabai_move_and_follow(space_index: int, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Move a window to a space and switch focus to that space.

    Args:
        space_index: Target space number (1-based).
        window_id: Window to move. If None, moves the currently focused window.
    """
    return yabai.move_window_to_space_and_follow(space_index, window_id)


def yabai_toggle_fullscreen(window_id: Optional[int] = None) -> Dict[str, Any]:
    """Toggle zoom-fullscreen on a window.

    Args:
        window_id: Window ID. If None, applies to the focused window.
    """
    return yabai.toggle_fullscreen(window_id)


def yabai_toggle_float(window_id: Optional[int] = None) -> Dict[str, Any]:
    """Toggle floating (non-tiled) mode on a window.

    Args:
        window_id: Window ID. If None, applies to the focused window.
    """
    return yabai.toggle_float(window_id)


def yabai_balance_space() -> Dict[str, Any]:
    """Balance window sizes equally on the current space."""
    return yabai.balance_space()


def yabai_rotate_space(degrees: int = 90) -> Dict[str, Any]:
    """Rotate the BSP layout of the current space.

    Args:
        degrees: Rotation amount — 90, 180, or 270.
    """
    return yabai.rotate_space(degrees)


def yabai_resize_window(edge: str, dx: int, dy: int, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Resize a window by moving one of its edges.

    Args:
        edge: Which edge to move — 'left', 'right', 'top', or 'bottom'.
        dx: Horizontal delta in pixels (positive = move right).
        dy: Vertical delta in pixels (positive = move down).
        window_id: Window ID. If None, applies to the focused window.
    """
    return yabai.resize_window(edge, dx, dy, window_id)


def yabai_warp_window(direction: str, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Warp a window in a BSP direction, reordering the tree.

    Args:
        direction: 'west', 'south', 'north', or 'east'.
        window_id: Window ID. If None, applies to the focused window.
    """
    return yabai.warp_window(direction, window_id)


def yabai_swap_window(direction: str, window_id: Optional[int] = None) -> Dict[str, Any]:
    """Swap a window with its neighbour in a direction.

    Args:
        direction: 'west', 'south', 'north', or 'east'.
        window_id: Window ID. If None, applies to the focused window.
    """
    return yabai.swap_window(direction, window_id)


def window_tools() -> List[Any]:
    """Return the FunctionTool list for WindowAgent."""
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(func=get_desktop_context),
        FunctionTool(func=yabai_query_windows),
        FunctionTool(func=yabai_query_spaces),
        FunctionTool(func=yabai_query_displays),
        FunctionTool(func=yabai_focus_window),
        FunctionTool(func=yabai_focus_window_by_app),
        FunctionTool(func=yabai_move_to_space),
        FunctionTool(func=yabai_focus_space),
        FunctionTool(func=yabai_move_and_follow),
        FunctionTool(func=yabai_toggle_fullscreen),
        FunctionTool(func=yabai_toggle_float),
        FunctionTool(func=yabai_balance_space),
        FunctionTool(func=yabai_rotate_space),
        FunctionTool(func=yabai_resize_window),
        FunctionTool(func=yabai_warp_window),
        FunctionTool(func=yabai_swap_window),
    ]


# ── Shell tools (ShellAgent) ──────────────────────────────────────────────────

def shell_run(command: str, timeout: int = 60) -> Dict[str, Any]:
    """Run a zsh shell command and return its output.

    Dangerous commands (rm -rf, dd, shutdown, etc.) are blocked.

    Args:
        command: The shell command to execute (runs under zsh -c).
        timeout: Maximum seconds to wait before killing the command (default 60).

    Returns:
        {"ok": True, "stdout": "...", "stderr": "...", "returncode": 0}
        {"ok": False, "error": "blocked/timeout/..."}
    """
    if _is_dangerous(command):
        return {"ok": False, "error": f"Blocked: dangerous command pattern detected in: {command[:80]}"}

    try:
        returncode, stdout, stderr = macos.run_shell_command(command, timeout)
        return {
            "ok": returncode == 0,
            "returncode": returncode,
            "stdout": stdout[:3000],
            "stderr": stderr[:500],
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def file_read(path: str) -> Dict[str, Any]:
    """Read a file and return its contents as text.

    Args:
        path: Absolute or home-relative path to the file (e.g. ~/notes.txt).

    Returns:
        {"ok": True, "content": "...", "size": <bytes>}
        {"ok": False, "error": "..."}
    """
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return {"ok": False, "error": f"File not found: {path}"}
    try:
        content = macos.read_file(p)
        return {"ok": True, "content": content[:10000], "size": len(content)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def file_write(path: str, content: str) -> Dict[str, Any]:
    """Write text content to a file (creates parent directories as needed).

    Args:
        path: Absolute or home-relative path to write to.
        content: Text content to write.

    Returns:
        {"ok": True, "path": "...", "bytes_written": N}
        {"ok": False, "error": "..."}
    """
    p = Path(os.path.expanduser(path))
    try:
        macos.write_file(p, content)
        return {"ok": True, "path": str(p), "bytes_written": len(content.encode())}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def file_list_dir(path: str = ".") -> Dict[str, Any]:
    """List the contents of a directory.

    Args:
        path: Absolute or home-relative path to the directory (default: current dir).

    Returns:
        {"ok": True, "path": "...", "entries": ["subdir/", "file.txt", ...]}
        {"ok": False, "error": "..."}
    """
    p = Path(os.path.expanduser(path))
    if not p.exists():
        return {"ok": False, "error": f"Directory not found: {path}"}
    try:
        entries = macos.list_directory(p)
        return {"ok": True, "path": str(p), "entries": entries}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def shell_tools() -> List[Any]:
    """Return the FunctionTool list for ShellAgent."""
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(func=shell_run),
        FunctionTool(func=file_read),
        FunctionTool(func=file_write),
        FunctionTool(func=file_list_dir),
    ]
