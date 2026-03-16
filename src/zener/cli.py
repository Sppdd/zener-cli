"""
cli.py — Zener CLI entry point.

User flow (unchanged from v1):
  1. zener setup      — one-time: enter Gemini API key, saved to ~/.zener/config.json
  2. zener shell      — interactive REPL (session memory persists across tasks)
  3. zener run        — single task, exits 0/1
  4. zener screenshot — take a screenshot and describe it

v2 changes (internal — UX is identical):
  - Agent loop now uses Google ADK multi-agent Runner
  - TerminalCallbacks wires ADK events (on_thought, on_tool_call, etc.) to terminal
  - Spinner shown during initial context gathering
  - Tool calls rendered with step number and tool name tag
"""
import sys
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML

from . import config, macos, loop as loop_module

logging.basicConfig(level=logging.WARNING, format="%(message)s")
logger = logging.getLogger(__name__)


# ── Prompt style ──────────────────────────────────────────────────────────────

_prompt_style = Style.from_dict({
    "prompt": "ansicyan bold",
})


# ── Print helpers ─────────────────────────────────────────────────────────────

def print_thinking(text: str) -> None:
    click.echo(f"  {click.style('~', fg='yellow')} {text}")


def print_thought(author: str, text: str) -> None:
    """Show an agent's reasoning block."""
    words = text.split()
    lines = []
    line: list = []
    for word in words:
        if sum(len(w) + 1 for w in line) + len(word) > 76:
            lines.append(" ".join(line))
            line = [word]
        else:
            line.append(word)
    if line:
        lines.append(" ".join(line))

    click.echo(f"\n  {click.style(author, fg='cyan', bold=True)}")
    for ln in lines:
        click.echo(f"  {click.style('│', fg='cyan')} {click.style(ln, fg='bright_black')}")
    click.echo()


def print_tool_start(step: int, tool_name: str, tool_input: Dict[str, Any]) -> None:
    """Render a tool call line — no newline, waiting for result."""
    # Friendly label for known tools
    label_map = {
        "take_screenshot":           "screenshot",
        "describe_screenshot":       "describe",
        "mouse_click":               "click",
        "mouse_double_click":        "dbl-click",
        "mouse_right_click":         "r-click",
        "mouse_scroll":              "scroll",
        "mouse_drag":                "drag",
        "keyboard_type":             "type",
        "keyboard_press_key":        "key",
        "wait":                      "wait",
        "get_desktop_context":       "desktop-ctx",
        "yabai_query_windows":       "windows",
        "yabai_query_spaces":        "spaces",
        "yabai_query_displays":      "displays",
        "yabai_focus_window":        "focus-win",
        "yabai_focus_window_by_app": "focus-app",
        "yabai_move_to_space":       "move-space",
        "yabai_focus_space":         "focus-space",
        "yabai_move_and_follow":     "move+follow",
        "yabai_toggle_fullscreen":   "fullscreen",
        "yabai_toggle_float":        "float",
        "yabai_balance_space":       "balance",
        "yabai_rotate_space":        "rotate",
        "yabai_resize_window":       "resize",
        "yabai_warp_window":         "warp",
        "yabai_swap_window":         "swap",
        "shell_run":                 "shell",
        "file_read":                 "read",
        "file_write":                "write",
        "file_list_dir":             "ls",
        "ScreenAgent":               "ScreenAgent",
        "InputAgent":                "InputAgent",
        "WindowAgent":               "WindowAgent",
        "ShellAgent":                "ShellAgent",
        "load_memory":               "memory",
    }
    label = label_map.get(tool_name, tool_name)
    tag = click.style(f"[{label}]", fg="cyan")

    # Build a one-line hint from input params
    hint = ""
    if tool_input:
        if "command" in tool_input:
            hint = str(tool_input["command"])[:60]
        elif "text" in tool_input:
            hint = f'"{str(tool_input["text"])[:40]}"'
        elif "x" in tool_input and "y" in tool_input:
            hint = f"({tool_input['x']}, {tool_input['y']})"
        elif "path" in tool_input:
            hint = str(tool_input["path"])[:50]
        elif "key" in tool_input:
            hint = str(tool_input["key"])
        elif "app_name" in tool_input:
            hint = str(tool_input["app_name"])

    click.echo(
        f"  {click.style(str(step), fg='bright_black', bold=True)}. {tag} {hint}",
        nl=False,
    )


def print_tool_done(ok: bool, summary: str) -> None:
    if ok:
        mark = click.style(" ✓", fg="green", bold=True)
        detail = click.style(f"  {summary}", fg="bright_black") if summary else ""
        click.echo(f"{mark}{detail}")
    else:
        mark = click.style(" ✗", fg="red", bold=True)
        click.echo(f"{mark} {click.style(summary, fg='red')}")


def print_screenshot_desc(desc: str) -> None:
    click.echo(f"\n  {click.style('Screen', fg='cyan', bold=True)}: {desc}\n")


def print_final(text: str) -> None:
    """Show the orchestrator's final answer."""
    click.echo(f"\n  {click.style('─' * 48, fg='bright_black')}")
    # Word-wrap at 76 chars
    words = text.split()
    lines = []
    line: list = []
    for word in words:
        if sum(len(w) + 1 for w in line) + len(word) > 76:
            lines.append(" ".join(line))
            line = [word]
        else:
            line.append(word)
    if line:
        lines.append(" ".join(line))
    for ln in lines:
        click.echo(f"  {ln}")
    click.echo()


def print_success(text: str) -> None:
    click.echo(f"\n  {click.style('✓', fg='green', bold=True)} {text}\n")


def print_error(text: str) -> None:
    click.echo(f"\n  {click.style('✗', fg='red', bold=True)} {text}\n")


def print_warning(text: str) -> None:
    click.echo(f"  {click.style('!', fg='yellow')} {text}")


def confirm_dangerous(message: str) -> bool:
    click.echo(f"\n  {click.style('WARNING', fg='red', bold=True)}: {message}")
    return click.confirm("  Allow this?", default=False)


def print_header() -> None:
    width = 46
    click.echo("")
    click.echo(click.style("  ╔" + "═" * width + "╗", fg="cyan"))
    click.echo(
        click.style("  ║", fg="cyan")
        + click.style("  Z E N E R ", fg="cyan", bold=True)
        + click.style("— your hands on the screen", fg="bright_black")
        + click.style(" " * (width - 36) + "║", fg="cyan")
    )
    click.echo(click.style("  ╚" + "═" * width + "╝", fg="cyan"))

    api_ok = bool(config.get_config().gemini_api_key)
    status = (
        click.style("ready", fg="green")
        if api_ok
        else click.style("no API key — run: zener setup", fg="yellow")
    )
    click.echo(f"  {click.style('status:', fg='bright_black')} {status}")
    click.echo(f"  {click.style('type:', fg='bright_black')}   your task, or {click.style('help', fg='cyan')} / {click.style('exit', fg='cyan')}\n")


# ── CLI callbacks ─────────────────────────────────────────────────────────────

class TerminalCallbacks(loop_module.LoopCallbacks):
    """Wires ADK Runner events to terminal output (OpenCode-style)."""

    def on_thought(self, author: str, text: str) -> None:
        if text:
            print_thought(author, text)

    def on_tool_call(self, step: int, tool_name: str, tool_input: Dict[str, Any]) -> None:
        print_tool_start(step, tool_name, tool_input)

    def on_tool_result(self, step: int, tool_name: str, ok: bool, summary: str) -> None:
        print_tool_done(ok, summary)

    def on_screenshot(self, description: str) -> None:
        print_screenshot_desc(description)

    def on_final(self, text: str) -> None:
        if text:
            print_final(text)

    def on_done(self, success: bool) -> None:
        if success:
            print_success("Done")
        else:
            print_warning("Stopped")

    def confirm_dangerous(self, message: str) -> bool:
        return confirm_dangerous(message)


# ── Spinner ───────────────────────────────────────────────────────────────────

class Spinner:
    """Braille-dot spinner shown while gathering desktop context."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, label: str = "Thinking"):
        self.label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = click.style(self.FRAMES[i % len(self.FRAMES)], fg="cyan")
            label = click.style(self.label, fg="bright_black")
            sys.stdout.write(f"\r  {frame} {label}  ")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1
        sys.stdout.write("\r" + " " * 40 + "\r")
        sys.stdout.flush()

    def __enter__(self) -> "Spinner":
        self._thread.start()
        return self

    def __exit__(self, *_) -> None:
        self._stop.set()
        self._thread.join()


# ── Core task runner ──────────────────────────────────────────────────────────

# Single AgentLoop instance per shell session — preserves the ADK Runner
# (which holds the memory service) across tasks.
_session_loop: Optional[loop_module.AgentLoop] = None


def _get_session_loop() -> loop_module.AgentLoop:
    global _session_loop
    if _session_loop is None:
        _session_loop = loop_module.AgentLoop(callbacks=TerminalCallbacks())
    return _session_loop


def process_task(task: str) -> bool:
    """Run the agent loop for a task, showing live progress."""
    cfg = config.get_config()
    if not cfg.gemini_api_key:
        print_error("No Gemini API key. Run: zener setup")
        return False

    click.echo(f"\n  {click.style('Task:', fg='cyan', bold=True)} {task}\n")

    agent_loop = _get_session_loop()

    try:
        success = agent_loop.run(task)
    except KeyboardInterrupt:
        click.echo()
        print_warning("Interrupted")
        success = False
    except Exception as e:
        print_error(f"Agent error: {e}")
        logger.exception("Agent loop failed")
        success = False

    return success


# ── Click CLI ─────────────────────────────────────────────────────────────────

@click.group()
def cli() -> None:
    """Zener — AI agent that controls your Mac."""
    pass


@cli.command()
def setup() -> None:
    """One-time setup: save your Gemini API key."""
    import json
    import os

    click.echo("\n  Zener Setup\n")
    click.echo("  Get a free API key at: https://aistudio.google.com/app/apikey\n")

    key = click.prompt("  Gemini API key", hide_input=True, type=str).strip()
    if not key:
        print_error("API key cannot be empty")
        sys.exit(1)

    cache_dir = config.get_cache_dir()
    cfg_path = cache_dir / "config.json"

    existing: dict = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text())
        except Exception:
            pass

    existing["GEMINI_API_KEY"] = key
    cfg_path.write_text(json.dumps(existing, indent=2))
    cfg_path.chmod(0o600)

    os.environ["GEMINI_API_KEY"] = key
    config._config = None  # reset singleton

    print_success("API key saved. You're ready — run: zener shell")


@cli.command()
def shell() -> None:
    """Start the interactive Zener REPL (with session memory across tasks)."""
    _load_saved_config()

    history_path = config.get_cache_dir() / "history"
    session = PromptSession(
        history=FileHistory(str(history_path)),
        auto_suggest=AutoSuggestFromHistory(),
        style=_prompt_style,
        message=HTML("<prompt>  ❯ </prompt>"),
    )

    print_header()

    while True:
        try:
            user_input = session.prompt()
        except KeyboardInterrupt:
            click.echo()
            continue
        except EOFError:
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        cmd = user_input.lower()

        if cmd in ("exit", "quit", "q"):
            print_success("Goodbye")
            break

        if cmd in ("help", "h", "?"):
            click.echo("""
  Commands:
    exit / quit     Exit Zener
    help            Show this help
    screenshot      Describe current screen
    models          Show configured models
    setup           Update your API key

  Anything else is sent to the AI agent.
  Memory of previous tasks persists for the session.
""")
            continue

        if cmd == "screenshot":
            try:
                print_thinking("Taking screenshot...")
                path = macos.take_screenshot()
                from . import _vision
                desc = _vision.describe_image(path)
                print_screenshot_desc(desc)
            except Exception as e:
                print_error(f"Screenshot failed: {e}")
            continue

        if cmd == "models":
            cfg = config.get_config()
            click.echo(f"""
  {click.style('Models', fg='cyan', bold=True)}
    orchestrator  {click.style(cfg.orchestrator_model, fg='bright_black')}
    screen        {click.style(cfg.screen_model, fg='bright_black')}
    input         {click.style(cfg.input_model, fg='bright_black')}
    window        {click.style(cfg.window_model, fg='bright_black')}
    shell         {click.style(cfg.shell_model, fg='bright_black')}

  Override with env vars: ZENER_ORCHESTRATOR_MODEL, ZENER_SCREEN_MODEL, etc.
""")
            continue

        if cmd == "setup":
            click.echo("\n  Run: zener setup\n")
            continue

        process_task(user_input)


@cli.command()
@click.argument("task")
@click.option("--max-steps", default=30, help="Maximum number of agent steps (unused in ADK mode, kept for compatibility)")
def run(task: str, max_steps: int) -> None:
    """Execute a single task and exit."""
    _load_saved_config()
    success = process_task(task)
    sys.exit(0 if success else 1)


@cli.command()
def screenshot() -> None:
    """Take a screenshot and describe what's on screen."""
    _load_saved_config()
    try:
        print_thinking("Taking screenshot...")
        path = macos.take_screenshot()
        from . import _vision
        desc = _vision.describe_image(path)
        click.echo(f"\n  {click.style(path.name, fg='bright_black')}")
        print_screenshot_desc(desc)
    except Exception as e:
        print_error(f"Failed: {e}")
        sys.exit(1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_saved_config() -> None:
    """Load API key (and other config) from ~/.zener/config.json into environment."""
    import json
    import os

    if os.getenv("GEMINI_API_KEY"):
        return

    cfg_path = config.get_cache_dir() / "config.json"
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            for key, val in data.items():
                if key not in os.environ:
                    os.environ[key] = val
            config._config = None  # reset singleton so it re-reads env
        except Exception as e:
            logger.warning(f"Could not load saved config: {e}")


def main() -> None:
    cli()
