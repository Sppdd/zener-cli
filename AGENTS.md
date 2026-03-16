# AGENTS.md — Zener CLI

This file provides orientation for AI coding agents working in this repository.

---

## Project Overview

**Zener** is a macOS desktop automation agent powered by Gemini multimodal vision.
The agent observes the screen, reasons about what to do next, and executes actions as the user's hands on the machine.

**User flow:** `zener setup` (enter API key once) → `zener shell` (interactive REPL) → type any task.

**Architecture:** Continuous perception-action loop.

```
User task
    │
    ▼
Take screenshot  ──────────────────────────────────┐
    │                                               │
    ▼                                               │
Gemini Vision                                       │
  → thought (reasoning)                            │
  → actions (list)                                 │
    │                                               │
    ▼                                               │
Execute each action                                 │
  (click, type, scroll, shell, …)                  │
    │                                               │
    ├── if DONE → finish                            │
    │                                               │
    └── take new screenshot ────────────────────────┘
         (verify state, re-plan if needed)
```

**Platform:** macOS only (AppleScript, `screencapture`, `osascript`).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Build | Hatchling (`pyproject.toml`, src layout) |
| CLI | Click + prompt_toolkit |
| Vision AI | Gemini 2.5 Flash via `google-genai` SDK |
| Auth / Storage | Firebase Admin SDK + Firestore (optional) |
| macOS automation | AppleScript via `osascript`, `screencapture` |
| Config | `python-dotenv` + `~/.zener/config.json` |

---

## Directory Structure

```
zener-cli/
├── pyproject.toml          # Build config, deps, entry point
├── .env.example            # Optional env vars template
└── src/
    └── zener/
        ├── __init__.py     # __version__
        ├── __main__.py     # python -m zener entry
        ├── agent.py        # Gemini client, ActionType enum, Action/Plan dataclasses
        ├── cli.py          # Click commands, REPL, live UX callbacks
        ├── config.py       # Config/User dataclasses, singleton getters
        ├── executor.py     # ActionExecutor dispatch table, DANGEROUS_COMMANDS
        ├── firebase.py     # Firebase app init, Firestore helpers (optional)
        ├── loop.py         # Continuous perception-action loop controller
        └── macos.py        # AppleScript, screencapture, shell, file I/O
```

Runtime (created automatically, gitignored):
- `~/.zener/`             — cache dir
- `~/.zener/config.json`  — saved API key (chmod 600)
- `~/.zener/history`      — REPL command history
- `~/.zener/temp/`        — screenshot temp files
- `venv/`                 — virtual environment

---

## Build / Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .

# One-time setup (saves key to ~/.zener/config.json)
zener setup
```

Required env (or set via `zener setup`):
```
GEMINI_API_KEY=...    # from aistudio.google.com — only required field
```

Optional (only if using Firebase features):
```
FIREBASE_API_KEY=...
FIREBASE_PROJECT_ID=...
GOOGLE_CLOUD_PROJECT=...
GCP_LOCATION=us-central1
GEMINI_MODEL=gemini-2.5-flash
```

---

## Running

```bash
zener setup               # one-time: save Gemini API key
zener shell               # interactive REPL
zener run "open safari"   # single task, exits 0/1
zener screenshot          # capture + describe screen
```

REPL built-in commands: `exit`, `screenshot`, `help`. Free text → agent loop.

---

## Testing

No test framework configured. Manual testing:

```bash
zener screenshot
zener run "open Safari and go to github.com"
zener run "say hello in Terminal"
```

When adding tests, use **pytest**:

```bash
pip install pytest
pytest tests/                          # all tests
pytest tests/test_agent.py             # single file
pytest tests/test_agent.py::test_name  # single test
```

---

## Linting / Formatting

Not configured. Recommended:

```bash
pip install ruff black
ruff check src/
black src/
```

Configure in `pyproject.toml` under `[tool.ruff]` and `[tool.black]`.

---

## Code Style

### Imports

Order: stdlib → third-party → internal (relative). One blank line between groups.

```python
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any

from google import genai
from google.genai import types

from . import config, macos
```

### Type Hints

Use `typing` module style (not `X | Y` — maintain Python 3.11 compat):

```python
from typing import Optional, List, Dict, Any, Tuple

def analyze_task(task: str, screenshot_path: Optional[Path] = None) -> Tuple[str, List[Action]]:
```

Use `# type: ignore[arg-type]` only when third-party stubs are wrong (e.g. `google-genai` `List[Content]`).

### Naming

| Kind | Style | Example |
|---|---|---|
| Functions / variables | `snake_case` | `analyze_task`, `screenshot_path` |
| Classes | `PascalCase` | `ActionExecutor`, `ExecutionResult` |
| Constants | `SCREAMING_SNAKE_CASE` | `SYSTEM_PROMPT`, `DANGEROUS_COMMANDS` |
| Private symbols | `_` prefix | `_client`, `_execute_open_app` |
| Enums | `PascalCase` class, `UPPER_CASE` members | `ActionType.OPEN_APP` |

### Dataclasses and Enums

```python
@dataclass
class Action:
    type: ActionType
    params: Dict[str, Any]
    description: str

    def to_dict(self) -> dict:
        return {"type": self.type.value, "params": self.params, "description": self.description}
```

### Singleton Pattern

```python
_client: Optional[genai.Client] = None

def get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(...)
    return _client
```

### Logging

Every module gets `logger = logging.getLogger(__name__)`. Only `cli.py` calls `logging.basicConfig`.

### Error Handling

- **`executor.py`**: catch `Exception`, return `ExecutionResult(status=FAILED, ...)`. Never raise.
- **`macos.py`**: return `bool`. Raise `RuntimeError` only for OS failures (screenshot).
- **`agent.py`**: catch `json.JSONDecodeError`, return fallback DONE action.
- **`loop.py`**: catch per-action failures, feed error back to Gemini for replanning.
- **`cli.py`**: bare `except Exception as e`, call `print_error(...)`.

### CLI Output — always use these helpers (never raw `print`)

```python
print_thinking(text)      # ~ yellow — agent working
print_thought(text)       # Gemini reasoning block
print_action_start(n, a)  # step number + action label (no newline)
print_action_done(ok, msg)# ✓ or ✗ appended to same line
print_success(text)       # ✓ green
print_error(text)         # ✗ red
print_warning(text)       # ! yellow
print_screenshot_desc(d)  # Screen: description
```

### Shell and AppleScript

- All AppleScript → `macos.run_applescript(script)`.
- All shell → `macos.run_shell_command(command, timeout)` (uses `zsh -c`).
- Never call `subprocess` directly outside `macos.py`.

### Dangerous Commands

New shell-executing code must check against `DANGEROUS_COMMANDS` in `executor.py` and invoke `confirm_callback`. Extend the list when adding new dangerous patterns.

---

## Action Types (complete list)

Defined in `agent.ActionType`, dispatched in `executor.ActionExecutor.execute()`, implemented in `macos.py`:

| type | params | description |
|---|---|---|
| `open_app` | `{name}` | Activate an application |
| `click` | `{x, y}` | Left-click at coordinates |
| `double_click` | `{x, y}` | Double-click |
| `right_click` | `{x, y}` | Right-click |
| `scroll` | `{x, y, direction, amount}` | Scroll up/down/left/right |
| `drag` | `{x1, y1, x2, y2}` | Drag |
| `type` | `{text}` | Type text |
| `press_key` | `{key}` | Key or combo (`cmd+c`, `shift+tab`) |
| `open_url` | `{url}` | Open URL in default browser |
| `run_shell` | `{command, timeout}` | Run zsh command |
| `screenshot` | `{}` | Capture screen, feed back to loop |
| `read_file` | `{path}` | Read file contents |
| `write_file` | `{path, content}` | Write file |
| `list_dir` | `{path}` | List directory |
| `wait` | `{seconds}` | Sleep |
| `done` | `{}` | Task complete — stops the loop |

### Adding a New Action Type

1. Add member to `ActionType` enum in `agent.py`
2. Document params in `SYSTEM_PROMPT`
3. Add `_execute_<name>` method to `ActionExecutor` in `executor.py`
4. Add it to the `dispatch` dict in `execute()`
5. Add underlying call in `macos.py` if needed

---

## Loop Architecture (`loop.py`)

`AgentLoop.run(task)` drives everything:

1. Take initial screenshot
2. Call `agent.analyze_task(task, screenshot, history)` → `(thought, actions)`
3. For each action: call `executor.execute(action)`, notify callbacks
4. After UI-changing actions: sleep `UI_SETTLE_DELAY` (0.6s)
5. On `SCREENSHOT` action: describe result, add to history
6. On `DONE`: return `True`
7. After each planning round: take a new screenshot for next iteration
8. Stop at `max_steps` (default 30)

`LoopCallbacks` interface — override in CLI to render live output:

```python
class LoopCallbacks:
    def on_thought(self, thought: str) -> None: ...
    def on_action_start(self, step: int, action: Action) -> None: ...
    def on_action_done(self, step: int, action: Action, result: ExecutionResult) -> None: ...
    def on_screenshot(self, path: Path, description: str) -> None: ...
    def on_done(self, success: bool, steps: int) -> None: ...
    def confirm_dangerous(self, message: str) -> bool: ...
```
