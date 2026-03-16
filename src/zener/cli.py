"""
cli.py — Zener CLI entry point.

User flow (v3 — Cloud-powered):
  1. zener setup      — one-time: authenticate with Google (gcloud ADC)
  2. zener shell      — interactive REPL, tasks run on Cloud Run via Vertex AI
  3. zener run        — single task, exits 0/1
  4. zener screenshot — take a screenshot and describe it

All AI reasoning runs on the Cloud Run server (Vertex AI, gemini-2.5-pro).
The CLI is a thin terminal client: it takes screenshots, executes local
macOS actions (click/type/scroll), and renders live streaming output.

Auth: Google Application Default Credentials (ADC).
  gcloud auth application-default login   ← run once
No Gemini API key needed.
"""
import sys
import logging
import subprocess
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

from . import config as config_module, macos
from . import loop as loop_module

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
        "open_application":          "open-app",
        "open_url":                  "open-url",
        "wait":                      "wait",
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
        elif "name" in tool_input:
            hint = str(tool_input["name"])
        elif "url" in tool_input:
            hint = str(tool_input["url"])[:60]

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
    click.echo(f"\n  {click.style('─' * 48, fg='bright_black')}")
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
    cfg = config_module.get_config()
    click.echo("")
    click.echo(click.style("  ╔" + "═" * width + "╗", fg="cyan"))
    click.echo(
        click.style("  ║", fg="cyan")
        + click.style("  Z E N E R ", fg="cyan", bold=True)
        + click.style("— your hands on the screen", fg="bright_black")
        + click.style(" " * (width - 36) + "║", fg="cyan")
    )
    click.echo(click.style("  ╚" + "═" * width + "╝", fg="cyan"))

    auth_ok = _check_adc_fast()
    if auth_ok:
        status = click.style("ready  (Vertex AI / Cloud Run)", fg="green")
    else:
        status = click.style(
            "not authenticated — run: gcloud auth application-default login",
            fg="yellow",
        )

    click.echo(f"  {click.style('status:', fg='bright_black')} {status}")
    click.echo(f"  {click.style('server:', fg='bright_black')} {click.style(cfg.server_url, fg='bright_black')}")
    click.echo(f"  {click.style('type:', fg='bright_black')}   your task, or {click.style('help', fg='cyan')} / {click.style('exit', fg='cyan')}\n")


def _check_adc_fast() -> bool:
    """Quick check: does ADC exist on disk (no network call)?"""
    import os
    from pathlib import Path
    # Standard ADC file locations
    candidates = [
        Path.home() / ".config" / "gcloud" / "application_default_credentials.json",
        Path(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "/nonexistent")),
    ]
    return any(p.exists() for p in candidates)


# ── Spinner ───────────────────────────────────────────────────────────────────

class Spinner:
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, label: str = "Working..."):
        self._label = label
        self._lock  = threading.Lock()
        self._stop  = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    @property
    def label(self) -> str:
        with self._lock:
            return self._label

    @label.setter
    def label(self, value: str) -> None:
        with self._lock:
            self._label = value

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = click.style(self.FRAMES[i % len(self.FRAMES)], fg="cyan")
            with self._lock:
                lbl = self._label
            txt = click.style(lbl, fg="bright_black")
            sys.stdout.write(f"\r  {frame} {txt}   ")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1
        sys.stdout.write("\r" + " " * 50 + "\r")
        sys.stdout.flush()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    def __enter__(self) -> "Spinner":
        return self

    def __exit__(self, *_) -> None:
        self.stop()


# ── CLI callbacks ─────────────────────────────────────────────────────────────

class TerminalCallbacks(loop_module.LoopCallbacks):
    def __init__(self) -> None:
        self._spinner: Optional[Spinner] = None

    def on_status(self, text: str) -> None:
        """Show/update/clear the spinner with a transient status label."""
        if text:
            if self._spinner is None:
                self._spinner = Spinner(text)
            else:
                self._spinner.label = text
        else:
            # Empty text → stop spinner
            if self._spinner is not None:
                self._spinner.stop()
                self._spinner = None

    def _clear_spinner(self) -> None:
        """Stop spinner before printing anything permanent."""
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    def on_thought(self, author: str, text: str) -> None:
        if text:
            self._clear_spinner()
            print_thought(author, text)

    def on_tool_call(self, step: int, tool_name: str, tool_input: Dict[str, Any]) -> None:
        self._clear_spinner()
        print_tool_start(step, tool_name, tool_input)

    def on_tool_result(self, step: int, tool_name: str, ok: bool, summary: str) -> None:
        print_tool_done(ok, summary)

    def on_screenshot(self, description: str) -> None:
        self._clear_spinner()
        print_screenshot_desc(description)

    def on_final(self, text: str) -> None:
        if text:
            self._clear_spinner()
            print_final(text)

    def on_done(self, success: bool) -> None:
        self._clear_spinner()
        if success:
            print_success("Done")
        else:
            print_warning("Stopped")

    def confirm_dangerous(self, message: str) -> bool:
        self._clear_spinner()
        return confirm_dangerous(message)


# ── Core task runner ──────────────────────────────────────────────────────────

def process_task(task: str) -> bool:
    click.echo(f"\n  {click.style('Task:', fg='cyan', bold=True)} {task}\n")

    # Fresh callbacks per task so spinner state is always clean
    agent_loop = loop_module.AgentLoop(callbacks=TerminalCallbacks())

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
    """Zener — AI agent that controls your Mac via the cloud."""
    pass


@cli.command()
def setup() -> None:
    """One-time setup: authenticate with Google for Vertex AI access."""
    click.echo("\n  Zener Setup — Cloud Edition\n")
    click.echo("  Zener uses your Google account to access Vertex AI on Cloud Run.")
    click.echo("  No API key needed — just your Google login.\n")

    # Step 1: Check gcloud is installed
    result = subprocess.run(["which", "gcloud"], capture_output=True, text=True)
    if result.returncode != 0:
        print_error("gcloud CLI not found.")
        click.echo("  Install it from: https://cloud.google.com/sdk/docs/install")
        click.echo("  Then re-run: zener setup\n")
        sys.exit(1)

    click.echo("  " + click.style("Step 1:", fg="cyan", bold=True) + "  Checking gcloud login...")
    login_result = subprocess.run(
        ["gcloud", "auth", "print-identity-token", "--quiet"],
        capture_output=True, text=True,
    )

    if login_result.returncode != 0:
        click.echo("  " + click.style("→", fg="yellow") + "  Not logged in. Opening browser for Google login...")
        subprocess.run(["gcloud", "auth", "login", "--update-adc"])
    else:
        click.echo("  " + click.style("✓", fg="green") + "  Already logged in with gcloud.\n")

    # Step 2: Application Default Credentials
    click.echo("  " + click.style("Step 2:", fg="cyan", bold=True) + "  Setting up Application Default Credentials...")
    adc_ok = _check_adc_fast()
    if not adc_ok:
        click.echo("  " + click.style("→", fg="yellow") + "  Configuring ADC...")
        subprocess.run(["gcloud", "auth", "application-default", "login"])
    else:
        click.echo("  " + click.style("✓", fg="green") + "  ADC already configured.\n")

    # Step 3: Verify token works
    click.echo("  " + click.style("Step 3:", fg="cyan", bold=True) + "  Verifying authentication...")
    cfg = config_module.get_config()
    try:
        token = loop_module._get_identity_token(cfg.server_url)
        if token:
            click.echo("  " + click.style("✓", fg="green") + f"  Token obtained successfully.\n")
        else:
            raise ValueError("Empty token")
    except Exception as e:
        print_error(f"Could not get token: {e}")
        click.echo("  Try running: gcloud auth application-default login\n")
        sys.exit(1)

    # Save server URL to config
    config_module.save_config_value("ZENER_SERVER_URL", cfg.server_url)

    print_success("Setup complete! Run: zener shell")
    click.echo(f"  Server: {click.style(cfg.server_url, fg='bright_black')}")
    click.echo(f"  Models: gemini-2.5-pro (orchestrator) + gemini-2.5-flash (agents)\n")


@cli.command()
def shell() -> None:
    """Start the interactive Zener REPL."""
    config_module.load_saved_config()

    history_path = config_module.get_cache_dir() / "history"
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
    models          Show server model config
    setup           Re-run authentication setup

  Anything else is sent to the AI agent running on Cloud Run.
  The agent uses Vertex AI (gemini-2.5-pro) and controls your Mac remotely.
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
            cfg = config_module.get_config()
            click.echo(f"""
  {click.style('Models (Vertex AI on Cloud Run)', fg='cyan', bold=True)}
    orchestrator  {click.style(cfg.orchestrator_model, fg='bright_black')}
    screen        {click.style(cfg.screen_model, fg='bright_black')}
    input         {click.style(cfg.input_model, fg='bright_black')}
    window        {click.style(cfg.window_model, fg='bright_black')}
    shell         {click.style(cfg.shell_model, fg='bright_black')}

  Server: {click.style(cfg.server_url, fg='bright_black')}
  Override: ZENER_SERVER_URL, ZENER_ORCHESTRATOR_MODEL, etc.
""")
            continue

        if cmd == "setup":
            click.echo("\n  Run: zener setup\n")
            continue

        process_task(user_input)


@cli.command()
@click.argument("task")
@click.option("--max-steps", default=30, hidden=True)
def run(task: str, max_steps: int) -> None:
    """Execute a single task and exit."""
    config_module.load_saved_config()
    success = process_task(task)
    sys.exit(0 if success else 1)


@cli.command()
def screenshot() -> None:
    """Take a screenshot and describe what's on screen."""
    config_module.load_saved_config()
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


def main() -> None:
    cli()
