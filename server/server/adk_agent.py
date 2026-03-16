"""
server/adk_agent.py
ADK multi-agent definitions for Zener Cloud.

Agent hierarchy:
  OrchestratorAgent (gemini-2.5-pro)
    ├── ScreenAgent  (gemini-2.5-flash) — screenshot description (images arrive from CLI)
    ├── InputAgent   (gemini-2.5-flash) — sends action_request back to CLI
    ├── WindowAgent  (gemini-2.5-flash) — sends action_request back to CLI
    └── ShellAgent   (gemini-2.5-flash) — linux shell + file I/O inside the container

All Gemini calls use Vertex AI (Application Default Credentials via Cloud Run
Workload Identity — no API key required).

The server NEVER executes macOS actions directly.  Mouse/keyboard/screenshots
are sent as action_request events over the WebSocket and executed by the CLI.
The CLI sends back action_result.  adk_loop.py handles the round-trip; these
tool functions raise ActionRequestSignal which adk_loop catches.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import subprocess
import shlex
from pathlib import Path
from typing import Any, Optional

from google.adk.agents import LlmAgent
from google.adk.tools import load_memory
from google.adk.tools.agent_tool import AgentTool

logger = logging.getLogger(__name__)

# ── Model names (Vertex AI) ───────────────────────────────────────────────────

ORCHESTRATOR_MODEL = os.environ.get("ZENER_ORCHESTRATOR_MODEL", "gemini-2.5-pro")
SCREEN_MODEL       = os.environ.get("ZENER_SCREEN_MODEL",       "gemini-2.5-flash")
INPUT_MODEL        = os.environ.get("ZENER_INPUT_MODEL",        "gemini-2.5-flash")
WINDOW_MODEL       = os.environ.get("ZENER_WINDOW_MODEL",       "gemini-2.5-flash")
SHELL_MODEL        = os.environ.get("ZENER_SHELL_MODEL",        "gemini-2.5-flash")

# ── Action-request signal ─────────────────────────────────────────────────────

class ActionRequestSignal(Exception):
    """
    Raised by a tool function to request that the CLI execute an action locally.
    adk_loop intercepts this, sends action_request over WebSocket, awaits result.
    """
    def __init__(self, action: str, params: dict[str, Any]) -> None:
        self.action = action
        self.params = params
        super().__init__(f"action_request:{action}")


# ── Per-request context (session_id → asyncio.Queue for action results) ───────
# adk_loop.py sets this before running the ADK Runner for a session.
# Tools read it to send/receive action_request round-trips.

_action_queues: dict[str, asyncio.Queue] = {}
_action_senders: dict[str, Any] = {}  # callable that sends JSON over WS


def register_session(session_id: str, queue: asyncio.Queue, sender: Any) -> None:
    """Called by adk_loop to register the WebSocket channels for a session."""
    _action_queues[session_id] = queue
    _action_senders[session_id] = sender


def unregister_session(session_id: str) -> None:
    _action_queues.pop(session_id, None)
    _action_senders.pop(session_id, None)


# adk_loop sets the active session_id in a context var before calling runner.run_async
import contextvars
_current_session: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_session", default=""
)


async def _request_action(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """
    Send action_request to CLI and wait for action_result.
    Must be called from an async context within adk_loop.
    """
    session_id = _current_session.get()
    if not session_id:
        return {"error": "no active session"}

    sender = _action_senders.get(session_id)
    queue = _action_queues.get(session_id)
    if not sender or not queue:
        return {"error": "session not registered"}

    await sender({
        "type": "action_request",
        "action": action,
        "params": params,
    })

    # Wait for CLI to respond (with timeout)
    try:
        result = await asyncio.wait_for(queue.get(), timeout=30.0)
        return result
    except asyncio.TimeoutError:
        return {"error": f"action_request timed out after 30s: {action}"}


# ── Screen tools ──────────────────────────────────────────────────────────────

async def take_screenshot() -> dict[str, Any]:
    """Request the user's Mac to take a screenshot. Returns base64 PNG."""
    return await _request_action("take_screenshot", {})


async def describe_screenshot(screenshot_b64: str = "") -> dict[str, Any]:
    """
    Describe the current screen. Pass screenshot_b64 if you have one,
    or leave empty to request a fresh screenshot from the CLI first.
    """
    if not screenshot_b64:
        result = await _request_action("take_screenshot", {})
        if "error" in result:
            return result
        screenshot_b64 = result.get("screenshot_b64", "")
    return await _request_action("describe_screenshot", {"screenshot_b64": screenshot_b64})


# ── Input tools ───────────────────────────────────────────────────────────────

async def mouse_click(x: int, y: int) -> dict[str, Any]:
    """Left-click at screen coordinates (x, y)."""
    return await _request_action("mouse_click", {"x": x, "y": y})


async def mouse_double_click(x: int, y: int) -> dict[str, Any]:
    """Double-click at screen coordinates (x, y)."""
    return await _request_action("mouse_double_click", {"x": x, "y": y})


async def mouse_right_click(x: int, y: int) -> dict[str, Any]:
    """Right-click at screen coordinates (x, y)."""
    return await _request_action("mouse_right_click", {"x": x, "y": y})


async def mouse_scroll(x: int, y: int, direction: str, amount: int = 3) -> dict[str, Any]:
    """Scroll at (x, y). direction: 'up', 'down', 'left', 'right'. amount: number of clicks."""
    return await _request_action("mouse_scroll", {
        "x": x, "y": y, "direction": direction, "amount": amount,
    })


async def mouse_drag(x1: int, y1: int, x2: int, y2: int) -> dict[str, Any]:
    """Drag from (x1, y1) to (x2, y2)."""
    return await _request_action("mouse_drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})


async def keyboard_type(text: str) -> dict[str, Any]:
    """Type a string of text at the current cursor position."""
    return await _request_action("keyboard_type", {"text": text})


async def keyboard_press_key(key: str) -> dict[str, Any]:
    """Press a key or combo. Examples: 'return', 'escape', 'cmd+c', 'shift+tab'."""
    return await _request_action("keyboard_press_key", {"key": key})


async def open_application(name: str) -> dict[str, Any]:
    """Open a macOS application by name (e.g. 'Safari', 'Terminal', 'Finder')."""
    return await _request_action("open_application", {"name": name})


async def open_url(url: str) -> dict[str, Any]:
    """Open a URL in the default browser on the user's Mac."""
    return await _request_action("open_url", {"url": url})


async def wait(seconds: float) -> dict[str, Any]:
    """Wait for N seconds before taking the next action."""
    await asyncio.sleep(seconds)
    return {"ok": True, "message": f"Waited {seconds}s"}


# ── Shell tools (run inside the Cloud Run container — Linux) ──────────────────

DANGEROUS_PATTERNS = [
    "rm -rf /", "mkfs", "dd if=", ":(){:|:&};:",
    "shutdown", "reboot", "halt", "poweroff",
    "chmod 777 /", "chown -R root",
]


def shell_run(command: str, timeout: int = 30) -> dict[str, Any]:
    """
    Run a shell command inside the Cloud Run container (Linux/zsh).
    NOT on the user's Mac — use action_request for that.
    Returns: {stdout, stderr, returncode}.
    """
    for pat in DANGEROUS_PATTERNS:
        if pat in command:
            return {"error": f"Refused: dangerous pattern '{pat}'", "returncode": -1}
    try:
        result = subprocess.run(
            ["zsh", "-c", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = result.stdout[:4000]
        stderr = result.stderr[:1000]
        return {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "ok": result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout}s", "returncode": -1}
    except Exception as e:
        return {"error": str(e), "returncode": -1}


def file_read(path: str) -> dict[str, Any]:
    """Read a file from the container filesystem. Returns {content} or {error}."""
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"File not found: {path}"}
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > 8000:
            content = content[:8000] + "\n...[truncated]"
        return {"content": content, "ok": True}
    except Exception as e:
        return {"error": str(e)}


def file_write(path: str, content: str) -> dict[str, Any]:
    """Write content to a file in the container filesystem."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"ok": True, "message": f"Written {len(content)} bytes to {path}"}
    except Exception as e:
        return {"error": str(e)}


def file_list_dir(path: str = ".") -> dict[str, Any]:
    """List directory contents in the container filesystem."""
    try:
        p = Path(path)
        if not p.exists():
            return {"error": f"Path not found: {path}"}
        entries = []
        for item in sorted(p.iterdir()):
            entries.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None,
            })
        return {"entries": entries, "path": str(p.resolve()), "ok": True}
    except Exception as e:
        return {"error": str(e)}


# ── Agent instruction strings ─────────────────────────────────────────────────

_SCREEN_INSTRUCTION = """You are ScreenAgent, a specialist in macOS screen analysis.

Your tools:
  - take_screenshot: Ask the user's Mac to capture the current screen
  - describe_screenshot: Get a natural-language description of the current screen

RULES:
- Always take_screenshot before describe_screenshot (unless you already have a fresh one)
- Be concise and factual: list visible windows, apps, text, buttons
- Include coordinates of key UI elements when relevant
- Return a clear description. Do NOT attempt to control the mouse or keyboard."""


_INPUT_INSTRUCTION = """You are InputAgent, a specialist in macOS mouse and keyboard control.

Your tools:
  - mouse_click(x, y): Left-click at coordinates
  - mouse_double_click(x, y): Double-click
  - mouse_right_click(x, y): Right-click
  - mouse_scroll(x, y, direction, amount): Scroll up/down/left/right
  - mouse_drag(x1, y1, x2, y2): Drag
  - keyboard_type(text): Type text
  - keyboard_press_key(key): Press a key or combo (e.g. 'return', 'cmd+c')
  - open_application(name): Open a macOS app
  - open_url(url): Open a URL in the default browser
  - wait(seconds): Pause

RULES:
- Use coordinates from the most recent screen description
- After each action, request a new screenshot to verify the result
- For text entry: click the field first, then type"""


_WINDOW_INSTRUCTION = """You are WindowAgent, a specialist in macOS window management.

You do NOT have direct yabai access — all window operations go through
action_request messages to the user's Mac.

Your tools:
  - mouse_click(x, y): Click on window title bars, dock icons, etc.
  - keyboard_press_key(key): Use Mission Control shortcuts (ctrl+up, ctrl+1, etc.)
  - open_application(name): Bring an app to the front
  - take_screenshot / describe_screenshot: Check current window layout

RULES:
- Always check current screen state before acting
- Use keyboard shortcuts for space/window management (ctrl+1..9, cmd+tab, etc.)
- Report what you observe clearly"""


_SHELL_INSTRUCTION = """You are ShellAgent, a specialist in shell commands.

IMPORTANT: Your shell_run tool executes commands inside the CLOUD RUN CONTAINER
(Linux), NOT on the user's Mac. For Mac-side operations, ask the orchestrator
to use InputAgent or ScreenAgent.

Your tools:
  - shell_run(command, timeout): Run a zsh command in the container
  - file_read(path): Read a file in the container
  - file_write(path, content): Write a file in the container
  - file_list_dir(path): List a directory in the container

RULES:
- NEVER run destructive commands (rm -rf /, dd, mkfs, shutdown)
- Always check returncode — report errors clearly
- Truncate output longer than 2000 characters"""


_ORCHESTRATOR_INSTRUCTION = """You are Zener, an AI agent with full control over the user's Mac.

You coordinate four specialist sub-agents to accomplish any desktop task:

  ScreenAgent  — takes screenshots, describes what's on screen
  InputAgent   — controls mouse and keyboard (click, type, scroll, drag, open apps/URLs)
  WindowAgent  — manages windows and spaces via keyboard shortcuts
  ShellAgent   — runs commands inside the cloud container (NOT on the user's Mac)

WORKFLOW:
1. Start by getting a screenshot via ScreenAgent to understand the current state
2. Break the task into steps, delegating to the right specialist
3. After any UI-changing step, get a new screenshot to verify
4. If something fails, reassess and retry with a different approach
5. When the task is fully complete, state that clearly

RULES:
- Always verify state with a screenshot before and after UI actions
- Keep replies concise — focus on what you're doing and what you observed
- The user's Mac screen size is in the desktop context you receive"""


# ── Tool lists per agent ──────────────────────────────────────────────────────

def screen_tools() -> list:
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(take_screenshot),
        FunctionTool(describe_screenshot),
    ]


def input_tools() -> list:
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(take_screenshot),
        FunctionTool(describe_screenshot),
        FunctionTool(mouse_click),
        FunctionTool(mouse_double_click),
        FunctionTool(mouse_right_click),
        FunctionTool(mouse_scroll),
        FunctionTool(mouse_drag),
        FunctionTool(keyboard_type),
        FunctionTool(keyboard_press_key),
        FunctionTool(open_application),
        FunctionTool(open_url),
        FunctionTool(wait),
    ]


def window_tools() -> list:
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(take_screenshot),
        FunctionTool(describe_screenshot),
        FunctionTool(mouse_click),
        FunctionTool(keyboard_press_key),
        FunctionTool(open_application),
    ]


def shell_tools() -> list:
    from google.adk.tools import FunctionTool
    return [
        FunctionTool(shell_run),
        FunctionTool(file_read),
        FunctionTool(file_write),
        FunctionTool(file_list_dir),
    ]


# ── Agent singletons ──────────────────────────────────────────────────────────

_screen_agent: Optional[LlmAgent] = None
_input_agent: Optional[LlmAgent] = None
_window_agent: Optional[LlmAgent] = None
_shell_agent: Optional[LlmAgent] = None
_orchestrator: Optional[LlmAgent] = None


def get_screen_agent() -> LlmAgent:
    global _screen_agent
    if _screen_agent is None:
        _screen_agent = LlmAgent(
            name="ScreenAgent",
            model=SCREEN_MODEL,
            description="Takes screenshots and describes what is visible on the macOS screen.",
            instruction=_SCREEN_INSTRUCTION,
            tools=screen_tools(),
        )
    return _screen_agent


def get_input_agent() -> LlmAgent:
    global _input_agent
    if _input_agent is None:
        _input_agent = LlmAgent(
            name="InputAgent",
            model=INPUT_MODEL,
            description="Controls mouse clicks, keyboard typing, scrolling, drag, open apps/URLs.",
            instruction=_INPUT_INSTRUCTION,
            tools=input_tools(),
        )
    return _input_agent


def get_window_agent() -> LlmAgent:
    global _window_agent
    if _window_agent is None:
        _window_agent = LlmAgent(
            name="WindowAgent",
            model=WINDOW_MODEL,
            description="Manages macOS windows and spaces via keyboard shortcuts and mouse.",
            instruction=_WINDOW_INSTRUCTION,
            tools=window_tools(),
        )
    return _window_agent


def get_shell_agent() -> LlmAgent:
    global _shell_agent
    if _shell_agent is None:
        _shell_agent = LlmAgent(
            name="ShellAgent",
            model=SHELL_MODEL,
            description="Executes shell commands and reads/writes files inside the cloud container.",
            instruction=_SHELL_INSTRUCTION,
            tools=shell_tools(),
        )
    return _shell_agent


def get_orchestrator() -> LlmAgent:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = LlmAgent(
            name="Zener",
            model=ORCHESTRATOR_MODEL,
            description="Zener desktop automation orchestrator.",
            instruction=_ORCHESTRATOR_INSTRUCTION,
            tools=[
                AgentTool(agent=get_screen_agent()),
                AgentTool(agent=get_input_agent()),
                AgentTool(agent=get_window_agent()),
                AgentTool(agent=get_shell_agent()),
                load_memory,
            ],
        )
    return _orchestrator


def reset_agents() -> None:
    global _screen_agent, _input_agent, _window_agent, _shell_agent, _orchestrator
    _screen_agent = _input_agent = _window_agent = _shell_agent = _orchestrator = None
