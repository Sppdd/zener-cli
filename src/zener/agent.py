"""
agent.py — Google ADK multi-agent definitions for Zener.

Agent hierarchy (OpenCode-style specialisation):

  OrchestratorAgent          (gemini-2.5-flash — deep reasoning, delegation)
    ├── ScreenAgent           (gemini-2.5-flash-lite — multimodal vision)
    ├── InputAgent            (gemini-2.5-flash-lite — mouse/keyboard actions)
    ├── WindowAgent           (gemini-2.5-flash-lite — yabai window/space control)
    └── ShellAgent            (gemini-2.5-flash-lite — filesystem + shell)

Each sub-agent exposes a focused set of FunctionTools defined in executor.py.
The orchestrator receives the user task plus a desktop context snapshot (from
yabai.get_desktop_context()) and delegates to the appropriate specialist.

Model selection is fully configurable via environment variables:
  ZENER_ORCHESTRATOR_MODEL, ZENER_SCREEN_MODEL, ZENER_INPUT_MODEL,
  ZENER_WINDOW_MODEL, ZENER_SHELL_MODEL
"""
import logging
import os
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.tools import load_memory

from . import config, executor

logger = logging.getLogger(__name__)

# ── Agent singletons (built lazily on first use) ───────────────────────────────

_screen_agent: Optional[LlmAgent] = None
_input_agent: Optional[LlmAgent] = None
_window_agent: Optional[LlmAgent] = None
_shell_agent: Optional[LlmAgent] = None
_orchestrator: Optional[LlmAgent] = None


# ── Sub-agent: ScreenAgent ────────────────────────────────────────────────────

_SCREEN_INSTRUCTION = """You are ScreenAgent, a specialist in macOS screen analysis.

Your tools:
  - take_screenshot: Capture the current screen state
  - describe_screenshot: Get a natural-language description of a screenshot

RULES:
- Always take a screenshot before describing
- Be concise and factual: list visible windows, apps, text, buttons
- Include coordinates of key UI elements when relevant
- After UI-changing actions (click, type, open), always take a new screenshot to verify

Return a clear description of what you observe. Do NOT attempt to control the mouse or keyboard."""

_INPUT_INSTRUCTION = """You are InputAgent, a specialist in macOS mouse and keyboard control.

Your tools:
  - mouse_click: Left-click at (x, y)
  - mouse_double_click: Double-click at (x, y)
  - mouse_right_click: Right-click at (x, y)
  - mouse_scroll: Scroll at (x, y) in a direction
  - mouse_drag: Drag from one point to another
  - keyboard_type: Type a string of text
  - keyboard_press_key: Press a key or combo (e.g. "return", "cmd+c")
  - wait: Pause for N seconds

RULES:
- Use coordinates from the most recent screen description
- Prefer precise, targeted clicks over broad gestures
- After each action, request a new screenshot to verify the result
- For text entry: click the field first, then type
- For key combos use format: "cmd+c", "shift+tab", "cmd+shift+3"

NEVER open applications or URLs — delegate those to the orchestrator."""

_WINDOW_INSTRUCTION = """You are WindowAgent, a specialist in macOS window and space management using yabai.

Your tools:
  - get_desktop_context: Get full snapshot of windows, spaces, displays
  - yabai_query_windows: List all windows (optionally filtered by space)
  - yabai_query_spaces: List all spaces
  - yabai_query_displays: List all displays
  - yabai_focus_window: Focus a window by ID
  - yabai_focus_window_by_app: Focus the first window of an app
  - yabai_move_to_space: Move a window to a space index
  - yabai_focus_space: Switch to a space
  - yabai_move_and_follow: Move window to space and switch there
  - yabai_toggle_fullscreen: Toggle fullscreen on a window
  - yabai_toggle_float: Toggle floating mode on a window
  - yabai_balance_space: Balance window sizes on current space
  - yabai_rotate_space: Rotate layout (90/180/270 degrees)
  - yabai_resize_window: Resize a window by adjusting an edge
  - yabai_warp_window: Warp window in BSP tree direction
  - yabai_swap_window: Swap window with neighbour

RULES:
- Always query current state before making changes
- If yabai is unavailable, say so clearly and suggest installing it
- Prefer the most targeted command (e.g. focus by app name, not by guessing ID)
- Report window IDs, app names and space indices in your responses"""

_SHELL_INSTRUCTION = """You are ShellAgent, a specialist in shell commands and filesystem operations.

Your tools:
  - shell_run: Run a zsh command (returns stdout/stderr/returncode)
  - file_read: Read a file's contents
  - file_write: Write content to a file
  - file_list_dir: List a directory's contents

RULES:
- NEVER run destructive commands (rm -rf, dd, mkfs, shutdown, reboot, fork bomb)
- Prefer targeted commands over broad ones
- Always check the returncode — report errors clearly
- Truncate output longer than 2000 characters and note the truncation
- When writing files, confirm the path with the user task before proceeding"""

_ORCHESTRATOR_INSTRUCTION = """You are Zener, an AI agent with complete control over the user's Mac.

You coordinate four specialist agents to accomplish any desktop task:

  ScreenAgent  — takes screenshots, describes what's on screen
  InputAgent   — controls mouse and keyboard (click, type, scroll, drag)
  WindowAgent  — manages yabai windows, spaces, and displays
  ShellAgent   — runs shell commands, reads/writes files

WORKFLOW:
1. Read the desktop context provided in the task (windows, spaces, frontmost app)
2. Take an initial screenshot via ScreenAgent to see the current state
3. Break the task into steps, delegating each to the right specialist
4. After any UI-changing step, get a new screenshot to verify the result
5. If something fails, reassess and retry with a different approach
6. When the task is fully complete, state that clearly

RULES:
- Always verify state with a screenshot before and after UI actions
- Never expose internal tool names or JSON to the user
- Keep replies concise — focus on what you're doing and what you observed
- You have full memory of previous tasks in this session via the load_memory tool

You have access to:
  - All four sub-agents (delegate via their agent tools)
  - load_memory: recall facts from earlier tasks in this session"""


# ── Factory functions (lazy init, config-driven) ──────────────────────────────

def get_screen_agent() -> LlmAgent:
    global _screen_agent
    if _screen_agent is None:
        cfg = config.get_config()
        _screen_agent = LlmAgent(
            name="ScreenAgent",
            model=cfg.screen_model,
            description="Takes screenshots and describes what is visible on the macOS screen.",
            instruction=_SCREEN_INSTRUCTION,
            tools=executor.screen_tools(),
        )
        logger.debug(f"ScreenAgent initialised with model={cfg.screen_model}")
    return _screen_agent


def get_input_agent() -> LlmAgent:
    global _input_agent
    if _input_agent is None:
        cfg = config.get_config()
        _input_agent = LlmAgent(
            name="InputAgent",
            model=cfg.input_model,
            description="Controls mouse clicks, keyboard typing, scrolling, and drag operations.",
            instruction=_INPUT_INSTRUCTION,
            tools=executor.input_tools(),
        )
        logger.debug(f"InputAgent initialised with model={cfg.input_model}")
    return _input_agent


def get_window_agent() -> LlmAgent:
    global _window_agent
    if _window_agent is None:
        cfg = config.get_config()
        _window_agent = LlmAgent(
            name="WindowAgent",
            model=cfg.window_model,
            description="Manages macOS windows and spaces using yabai window manager.",
            instruction=_WINDOW_INSTRUCTION,
            tools=executor.window_tools(),
        )
        logger.debug(f"WindowAgent initialised with model={cfg.window_model}")
    return _window_agent


def get_shell_agent() -> LlmAgent:
    global _shell_agent
    if _shell_agent is None:
        cfg = config.get_config()
        _shell_agent = LlmAgent(
            name="ShellAgent",
            model=cfg.shell_model,
            description="Executes shell commands and reads/writes files on the filesystem.",
            instruction=_SHELL_INSTRUCTION,
            tools=executor.shell_tools(),
        )
        logger.debug(f"ShellAgent initialised with model={cfg.shell_model}")
    return _shell_agent


def get_orchestrator() -> LlmAgent:
    """Return the root orchestrator agent, building sub-agents if needed."""
    global _orchestrator
    if _orchestrator is None:
        cfg = config.get_config()

        from google.adk.tools.agent_tool import AgentTool

        _orchestrator = LlmAgent(
            name="Zener",
            model=cfg.orchestrator_model,
            description="Zener desktop automation orchestrator — controls macOS via vision, input, windows, and shell.",
            instruction=_ORCHESTRATOR_INSTRUCTION,
            tools=[
                AgentTool(agent=get_screen_agent()),
                AgentTool(agent=get_input_agent()),
                AgentTool(agent=get_window_agent()),
                AgentTool(agent=get_shell_agent()),
                load_memory,  # recall cross-task context
            ],
        )
        logger.debug(f"Orchestrator initialised with model={cfg.orchestrator_model}")
    return _orchestrator


def reset_agents() -> None:
    """Reset all agent singletons (useful after config changes)."""
    global _screen_agent, _input_agent, _window_agent, _shell_agent, _orchestrator
    _screen_agent = None
    _input_agent = None
    _window_agent = None
    _shell_agent = None
    _orchestrator = None
    logger.debug("All agent singletons reset")
