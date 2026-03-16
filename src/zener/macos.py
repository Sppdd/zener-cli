import subprocess
import os
import tempfile
import logging
from pathlib import Path
from typing import Tuple, Optional

from PIL import Image

from . import config

logger = logging.getLogger(__name__)


def take_screenshot(region: Optional[Tuple[int, int, int, int]] = None) -> Path:
    """Take a screenshot and return the path to the image file.
    
    Args:
        region: Optional (x, y, width, height) to capture specific region
        
    Returns:
        Path to the screenshot file
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


def run_applescript(script: str) -> str:
    """Run an AppleScript script and return the result.
    
    Args:
        script: AppleScript source code
        
    Returns:
        Script output or error message
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
    """Open an application by name."""
    script = f'tell application "{app_name}" to activate'
    result = run_applescript(script)
    return "Error" not in result


def click_at(x: int, y: int) -> bool:
    """Click at specific screen coordinates using mouse."""
    script = f'''
tell application "System Events"
    set mousePos to current application's (do shell script "echo $(/usr/bin/python3 -c 'import Quartz; pos = Quartz.NSEvent.mouseLocation(); print(int(pos.x), int(1080 - pos.y))')")
    set x to item 1 of mousePos
    set y to item 2 of mousePos
    click at {{x, y}}
end tell
'''
    script = f"click at {{{x}, {y}}}"
    result = run_applescript(f'tell application "System Events" to {script}')
    return "Error" not in result


def type_text(text: str) -> bool:
    """Type text using System Events."""
    escaped_text = text.replace('"', '\\"')
    script = f'tell application "System Events" to keystroke "{escaped_text}"'
    result = run_applescript(script)
    return "Error" not in result


def press_key(key: str) -> bool:
    """Press a modifier key or special key.
    
    Args:
        key: Key name like "return", "enter", "tab", "escape", 
             or modifier combos like "cmd c", "shift tab"
    """
    key = key.lower()
    
    key_mapping = {
        "return": "return",
        "enter": "enter",
        "tab": "tab",
        "escape": "escape",
        "esc": "escape",
        "space": "space",
        "up": "up arrow",
        "down": "down arrow",
        "left": "left arrow",
        "right": "right arrow",
        "delete": "delete",
        "backspace": "delete",
    }
    
    mapped_key = key_mapping.get(key, key)
    script = f'tell application "System Events" to keystroke {mapped_key}'
    result = run_applescript(script)
    return "Error" not in result


def get_frontmost_app() -> str:
    """Get the name of the frontmost application."""
    script = '''
tell application "System Events"
    set frontApp to first application process whose frontmost is true
    return name of frontApp
end tell
'''
    return run_applescript(script)


def get_screen_size() -> Tuple[int, int]:
    """Get the screen size."""
    script = '''
tell application "Finder"
    set deskSize to bounds of window of desktop
    return item 3 of deskSize & "x" & item 4 of deskSize
end tell
'''
    result = run_applescript(script)
    if "x" in result:
        width, height = result.split("x")
        return int(width), int(height)
    return 1920, 1080


def open_url(url: str) -> bool:
    """Open a URL in the default browser."""
    script = f'open location "{url}"'
    result = run_applescript(script)
    return "Error" not in result


def run_shell_command(command: str, timeout: int = 60) -> Tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["zsh", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout, result.stderr


def read_file(path: Path) -> str:
    """Read file contents."""
    return path.read_text()


def write_file(path: Path, content: str) -> None:
    """Write content to file."""
    path.write_text(content)


def list_directory(path: Path) -> list:
    """List directory contents."""
    return [str(p.name) for p in path.iterdir()]


def file_exists(path: Path) -> bool:
    """Check if file exists."""
    return path.exists()
