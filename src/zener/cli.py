import sys
import logging
import time
from pathlib import Path

import click
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML

from . import config, firebase, agent, executor, macos

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


style = Style.from_dict({
    "prompt": "ansicyan bold",
    "output": "ansigreen",
    "error": "ansired",
    "warning": "ansiyellow",
})


def print_header():
    click.echo("")
    click.echo(click.style("┌─ ", fg="cyan") + click.style("Zener AI", fg="green", bold=True) + click.style(" ─────────────────────────────", fg="cyan"))
    user = config.get_user()
    if user:
        usage = firebase.get_usage()
        click.echo(click.style("│ ", fg="cyan") + f"Logged in as {user.email}")
        click.echo(click.style("│ ", fg="cyan") + f"Usage: {usage:.1f} / 60 minutes")
    else:
        click.echo(click.style("│ ", fg="cyan") + "Not logged in - some features limited")
    click.echo(click.style("─" * 45, fg="cyan"))
    click.echo("")


def print_thinking(text: str):
    click.echo(click.style(f"[Zener] ", fg="yellow") + text)


def print_success(text: str):
    click.echo(click.style(f"[Zener] ", fg="green") + text)


def print_error(text: str):
    click.echo(click.style(f"[Zener] ", fg="red") + text)


def print_warning(text: str):
    click.echo(click.style(f"[Zener] ", fg="yellow") + text)


def print_action(action_num: int, description: str, success: bool):
    status = click.style("✓", fg="green") if success else click.style("✗", fg="red")
    click.echo(f"[{action_num}] {description} {status}")


def confirm_dangerous(message: str) -> bool:
    click.echo(click.style(f"⚠️  ", fg="yellow") + message)
    response = click.confirm("Confirm?", default=False)
    return response


def take_screenshot_if_needed() -> Path | None:
    """Take a screenshot for Gemini Vision analysis if user has seen screen."""
    try:
        return macos.take_screenshot()
    except Exception as e:
        logger.warning(f"Failed to take screenshot: {e}")
        return None


def process_task(task: str) -> bool:
    """Process a user task through the AI agent."""
    print_thinking("Analyzing task...")
    
    screenshot_path = take_screenshot_if_needed()
    
    actions = agent.analyze_task(task, screenshot_path)
    
    if not actions:
        print_error("No actions returned from AI")
        return False
    
    print_thinking(f"Executing {len(actions)} action(s)...")
    
    exec = executor.ActionExecutor(confirm_callback=confirm_dangerous)
    
    for i, action in enumerate(actions, 1):
        print_action(i, action.description, True)
        
        result = exec.execute(action)
        
        if result.status == executor.ExecutionStatus.SUCCESS:
            print_success(result.message)
        elif result.status == executor.ExecutionStatus.FAILED:
            print_error(result.message)
            return False
        elif result.status == executor.ExecutionStatus.CONFIRMATION_REQUIRED:
            if not confirm_dangerous(result.message):
                print_warning("Action cancelled")
                return False
        
        if action.type == agent.ActionType.SCREENSHOT and result.data:
            screenshot_path = Path(result.data.get("path", ""))
            if screenshot_path.exists():
                description = agent.analyze_screenshot(screenshot_path)
                print(f"\n{description}\n")
        
        time.sleep(0.3)
    
    return True


def run_repl():
    """Run the interactive REPL."""
    history_path = config.get_cache_dir() / "history"
    
    session = PromptSession(
        history=FileHistory(str(history_path)),
        auto_suggest=AutoSuggestFromHistory(),
        style=style,
        message=HTML("<prompt>❯ </prompt>"),
    )
    
    print_header()
    
    while True:
        try:
            user_input = session.prompt()
            
            if not user_input.strip():
                continue
            
            user_input = user_input.strip().lower()
            
            if user_input in ["exit", "quit", "q", "logout"]:
                print_success("Goodbye!")
                break
            
            if user_input in ["help", "h", "?"]:
                print("""
Commands:
  exit, quit    - Exit Zener
  help          - Show this help
  screenshot    - Take a screenshot
  whoami        - Show current user
  usage         - Show usage stats

Anything else will be sent to the AI agent.
                """)
                continue
            
            if user_input == "screenshot":
                try:
                    path = macos.take_screenshot()
                    description = agent.analyze_screenshot(path)
                    print(f"\n📷 {path.name}")
                    print(f"\n{description}\n")
                except Exception as e:
                    print_error(f"Failed: {e}")
                continue
            
            if user_input == "whoami":
                user = config.get_user()
                if user:
                    print(f"Logged in as: {user.email}")
                else:
                    print("Not logged in")
                continue
            
            if user_input == "usage":
                usage = firebase.get_usage()
                print(f"Usage: {usage:.1f} / 60 minutes")
                continue
            
            process_task(user_input)
            
        except KeyboardInterrupt:
            print("\nUse 'exit' to quit")
            continue
        except EOFError:
            break
    
    firebase.logout()


@click.group()
def cli():
    """Zener AI - Your AI-powered CLI assistant for macOS."""
    pass


@cli.command()
def login():
    """Login with Google via Firebase."""
    import uuid
    import http.server
    import socketserver
    import threading
    import webbrowser
    
    cfg = config.get_config()
    
    print("Starting local server for authentication...")
    print(f"Please open: http://localhost:8765/auth")
    print("\nEnter the Firebase ID token from the web login below:")
    
    id_token = click.prompt("ID Token", type=str)
    
    try:
        user = firebase.login_with_google(id_token)
        print_success(f"Logged in as {user.email}")
    except Exception as e:
        print_error(f"Login failed: {e}")
        sys.exit(1)


@cli.command()
def logout():
    """Logout from Firebase."""
    firebase.logout()
    print_success("Logged out")


@cli.command()
def shell():
    """Start the interactive Zener REPL."""
    run_repl()


@cli.command()
@click.argument("task")
def run(task):
    """Execute a single task."""
    process_task(task)


@cli.command()
def screenshot():
    """Take a screenshot and analyze it."""
    try:
        path = macos.take_screenshot()
        description = agent.analyze_screenshot(path)
        print(f"\n📷 {path.name}")
        print(f"\n{description}\n")
    except Exception as e:
        print_error(f"Failed: {e}")


def main():
    cli()
