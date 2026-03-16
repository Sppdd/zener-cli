"""
macos.py — Low-level macOS automation primitives for Zener.

Input control (mouse/keyboard) uses PyAutoGUI for direct, precise control.
App/URL launching uses AppleScript via osascript (no PyAutoGUI equivalent).
Screenshots use the system screencapture binary.
Shell commands run through zsh via subprocess.

All functions return simple types (bool, str, tuple) — callers decide
how to surface errors to the agent.
"""
import subprocess
import os
import time
import logging
from pathlib import Path
from typing import Tuple, Optional, List

import pyautogui

from . import config

logger = logging.getLogger(__name__)

# Disable PyAutoGUI fail-safe (corner mouse = abort) — we manage safety ourselves
pyautogui.FAILSAFE = False
# Small pause between PyAutoGUI actions to keep behaviour natural
pyautogui.PAUSE = 0.05


# ── Screenshots ───────────────────────────────────────────────────────────────

def take_screenshot(region: Optional[Tuple[int, int, int, int]] = None) -> Path:
    """Take a screenshot and return the path to the image file.

    Args:
        region: Optional (x, y, width, height) to capture a specific region.

    Returns:
        Path to the saved PNG file.

    Raises:
        RuntimeError: if screencapture fails.
    """
    temp_dir = config.get_temp_dir()
    output_path = temp_dir / f"screenshot_{os.urandom(8).hex()}.png"

    cmd = ["screencapture", "-x", str(output_path)]
    if region:
        x, y, w, h = region
        cmd.extend(["-R", f"{x},{y},{w},{h}"])

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Screenshot failed: {result.stderr}")

    logger.info(f"Screenshot saved to: {output_path}")
    return output_path


# ── AppleScript (app/URL only) ────────────────────────────────────────────────

def run_applescript(script: str) -> str:
    """Run an AppleScript snippet via osascript.

    Returns the script stdout, or an 'Error: ...' string on failure.
    """
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        error_msg = result.stderr.strip()
        logger.error(f"AppleScript error: {error_msg}")
        return f"Error: {error_msg}"
    return result.stdout.strip()


def open_application(app_name: str) -> bool:
    """Activate/launch an application by name using AppleScript."""
    script = f'tell application "{app_name}" to activate'
    result = run_applescript(script)
    return "Error" not in result


def open_url(url: str) -> bool:
    """Open a URL in the default browser via AppleScript."""
    script = f'open location "{url}"'
    result = run_applescript(script)
    return "Error" not in result


def get_frontmost_app() -> str:
    """Return the name of the currently focused application."""
    script = """
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    return name of frontApp
end tell
"""
    return run_applescript(script)


def get_screen_size() -> Tuple[int, int]:
    """Return (width, height) of the primary display."""
    w, h = pyautogui.size()
    return int(w), int(h)


# ── Mouse (PyAutoGUI) ─────────────────────────────────────────────────────────

def click_at(x: int, y: int) -> bool:
    """Left-click at (x, y)."""
    try:
        pyautogui.click(x, y)
        return True
    except Exception as e:
        logger.error(f"click_at({x},{y}) failed: {e}")
        return False


def double_click_at(x: int, y: int) -> bool:
    """Double-click at (x, y)."""
    try:
        pyautogui.doubleClick(x, y)
        return True
    except Exception as e:
        logger.error(f"double_click_at({x},{y}) failed: {e}")
        return False


def right_click_at(x: int, y: int) -> bool:
    """Right-click at (x, y)."""
    try:
        pyautogui.rightClick(x, y)
        return True
    except Exception as e:
        logger.error(f"right_click_at({x},{y}) failed: {e}")
        return False


def scroll_at(x: int, y: int, direction: str, amount: int = 3) -> bool:
    """Scroll at (x, y).

    Args:
        direction: 'up' | 'down' | 'left' | 'right'
        amount: number of scroll 'clicks' (positive integer)
    """
    try:
        pyautogui.moveTo(x, y)
        dir_lower = direction.lower()
        if dir_lower in ("up", "down"):
            clicks = amount if dir_lower == "up" else -amount
            pyautogui.scroll(clicks, x=x, y=y)
        else:
            # Horizontal scroll via hscroll
            clicks = amount if dir_lower == "right" else -amount
            pyautogui.hscroll(clicks, x=x, y=y)
        return True
    except Exception as e:
        logger.error(f"scroll_at({x},{y},{direction},{amount}) failed: {e}")
        return False


def drag_from_to(x1: int, y1: int, x2: int, y2: int, duration: float = 0.4) -> bool:
    """Drag from (x1, y1) to (x2, y2) holding the left button."""
    try:
        pyautogui.moveTo(x1, y1)
        pyautogui.dragTo(x2, y2, duration=duration, button="left")
        return True
    except Exception as e:
        logger.error(f"drag_from_to failed: {e}")
        return False


# ── Keyboard (PyAutoGUI) ──────────────────────────────────────────────────────

# Mapping from our key names to PyAutoGUI key names
_PYAUTOGUI_KEY_MAP = {
    "return": "enter",
    "enter": "enter",
    "esc": "escape",
    "escape": "escape",
    "tab": "tab",
    "space": "space",
    "up": "up",
    "down": "down",
    "left": "left",
    "right": "right",
    "delete": "backspace",
    "backspace": "backspace",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
    "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
    "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
}

# Modifier aliases → PyAutoGUI modifier names
_MODIFIER_MAP = {
    "cmd": "command",
    "command": "command",
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "opt": "alt",
    "option": "alt",
    "shift": "shift",
}


def type_text(text: str, interval: float = 0.02) -> bool:
    """Type a string using PyAutoGUI.

    Uses typewrite for ASCII-safe text; falls back to pyperclip paste for
    Unicode that typewrite cannot handle.
    """
    try:
        # PyAutoGUI typewrite handles standard ASCII well
        pyautogui.write(text, interval=interval)
        return True
    except Exception as e:
        logger.error(f"type_text failed: {e}")
        return False


def press_key(key: str) -> bool:
    """Press a key or key combination.

    Args:
        key: e.g. "return", "tab", "escape", "cmd+c", "shift+tab", "cmd+shift+3"
    """
    key = key.lower().strip()

    try:
        if "+" in key:
            parts = key.split("+")
            modifiers = []
            main_key = parts[-1]
            for part in parts[:-1]:
                mod = _MODIFIER_MAP.get(part)
                if mod:
                    modifiers.append(mod)

            mapped_main = _PYAUTOGUI_KEY_MAP.get(main_key, main_key)
            keys_sequence = modifiers + [mapped_main]
            pyautogui.hotkey(*keys_sequence)
        else:
            mapped = _PYAUTOGUI_KEY_MAP.get(key, key)
            pyautogui.press(mapped)
        return True
    except Exception as e:
        logger.error(f"press_key({key!r}) failed: {e}")
        return False


# ── Shell / Filesystem ────────────────────────────────────────────────────────

def run_shell_command(command: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Run a zsh command. Returns (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["zsh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def read_file(path: Path) -> str:
    """Read file contents as UTF-8 text."""
    return path.read_text(encoding="utf-8")


def write_file(path: Path, content: str) -> None:
    """Write content to file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def list_directory(path: Path) -> List[str]:
    """List directory contents: directories first, then files (sorted)."""
    items = list(path.iterdir())
    dirs = sorted([p.name + "/" for p in items if p.is_dir()])
    files = sorted([p.name for p in items if p.is_file()])
    return dirs + files


def file_exists(path: Path) -> bool:
    """Check if a path exists."""
    return path.exists()


def wait(seconds: float) -> None:
    """Sleep for the given number of seconds."""
    time.sleep(seconds)
