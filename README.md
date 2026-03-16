# Zener CLI

AI desktop automation agent for macOS. Observes your screen and controls your computer using Google ADK multi-agent architecture, Gemini Vision, PyAutoGUI, and optional yabai window management.

## What's New in v0.2

- **Multi-agent architecture** — Google ADK orchestrator delegates to 4 specialist sub-agents (Screen, Input, Window, Shell)
- **Session memory** — the agent recalls context from earlier tasks within the same `zener shell` session
- **PyAutoGUI input** — direct, precise mouse and keyboard control (replaces AppleScript for all input)
- **Yabai integration** — manage windows, spaces, and displays (optional, graceful degradation if not installed)
- **Desktop context** — every task starts with a live snapshot of open apps, windows, spaces, and a screenshot description
- **Per-agent model selection** — each sub-agent uses a configurable Gemini model

## Requirements

- macOS
- Python 3.11+
- A free Gemini API key from [aistudio.google.com](https://aistudio.google.com/app/apikey)
- *(Optional)* [yabai](https://github.com/koekeishiya/yabai) for window and space management

## Install

```bash
git clone <repo>
cd zener-cli
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

## Setup

```bash
zener setup
# Enter your Gemini API key when prompted.
# Saved to ~/.zener/config.json (chmod 600).
```

## Usage

### Interactive REPL

```bash
zener shell
```

REPL built-in commands:

| Command | Description |
|---|---|
| `exit` / `quit` / `q` | Exit Zener |
| `help` | Show available commands |
| `screenshot` | Take and describe the current screen |
| `models` | Show configured models per agent |
| `setup` | Reminder to run `zener setup` |

Anything else is sent to the AI agent.

### Single task (non-interactive)

```bash
zener run "open Safari and go to github.com"
# exits 0 on success, 1 on failure
```

### Describe current screen

```bash
zener screenshot
```

## Architecture

```
User task (zener shell or zener run)
         │
         ▼
  Desktop context snapshot
  ├── frontmost app (AppleScript)
  ├── screen size (PyAutoGUI)
  ├── open windows / spaces / displays (yabai)
  └── live screenshot description (Gemini Vision)
         │
         ▼
  OrchestratorAgent  [gemini-2.5-flash]
  ├── ScreenAgent    [gemini-2.0-flash] → take_screenshot, describe_screenshot
  ├── InputAgent     [gemini-2.0-flash] → mouse_click, keyboard_type, mouse_scroll, …
  ├── WindowAgent    [gemini-2.0-flash] → yabai query/focus/move/resize/swap
  └── ShellAgent     [gemini-2.0-flash] → shell_run, file_read, file_write, file_list_dir
         │
         ▼
  ADK Session memory (cross-task recall within one shell session)
```

## Agent tools

### ScreenAgent
`take_screenshot` · `describe_screenshot`

### InputAgent
`mouse_click` · `mouse_double_click` · `mouse_right_click` · `mouse_scroll` · `mouse_drag` · `keyboard_type` · `keyboard_press_key` · `wait`

### WindowAgent (requires yabai)
`get_desktop_context` · `yabai_query_windows` · `yabai_query_spaces` · `yabai_query_displays` · `yabai_focus_window` · `yabai_focus_window_by_app` · `yabai_move_to_space` · `yabai_focus_space` · `yabai_move_and_follow` · `yabai_toggle_fullscreen` · `yabai_toggle_float` · `yabai_balance_space` · `yabai_rotate_space` · `yabai_resize_window` · `yabai_warp_window` · `yabai_swap_window`

### ShellAgent
`shell_run` · `file_read` · `file_write` · `file_list_dir`

## Model configuration

Each agent uses a separate configurable model. Override via environment variables:

```bash
export ZENER_ORCHESTRATOR_MODEL=gemini-2.5-flash-preview-04-17
export ZENER_SCREEN_MODEL=gemini-2.0-flash
export ZENER_INPUT_MODEL=gemini-2.0-flash
export ZENER_WINDOW_MODEL=gemini-2.0-flash
export ZENER_SHELL_MODEL=gemini-2.0-flash
```

Or check what's active inside the REPL: type `models`.

## Optional: yabai window management

```bash
brew install koekeishiya/formulae/yabai
brew services start yabai
```

Without yabai, window-management tasks return a clear install hint and the agent falls back to AppleScript for basic app activation. Everything else works normally.

## Safety

- Dangerous shell commands (`rm -rf`, `dd`, `shutdown`, `reboot`, fork bomb, etc.) are blocked outright
- Dangerous commands that pass the blocklist still require explicit terminal confirmation
- PyAutoGUI `FAILSAFE` is disabled — Zener manages safety through its agent reasoning

## Testing the new features

### 1. Screen description

```bash
zener screenshot
# Should describe exactly what's on screen using Gemini Vision
```

### 2. Multi-agent tool calls (watch the step output)

```bash
zener shell
❯ open Calculator
# Expected output:
#   Zener (thought block)
#   1. [ScreenAgent] ...  ✓
#   2. [open]  ✓
#   3. [screenshot]  ✓
#   ─────────────────────
#   Calculator is now open.
```

### 3. Session memory across tasks

```bash
zener shell
❯ create a file called /tmp/zener-test.txt with the content "hello from zener"
# Agent creates the file

❯ what did we just write to the filesystem?
# Agent should recall the file without needing another tool call
```

### 4. Desktop context awareness

```bash
zener shell
❯ what apps do I have open right now?
# Agent reads context snapshot (frontmost app, open windows) before responding
```

### 5. Shell agent

```bash
zener shell
❯ run "ls -la ~" and tell me how many items are in my home directory
```

### 6. Window management (yabai required)

```bash
brew install koekeishiya/formulae/yabai && brew services start yabai
zener shell
❯ show me all my spaces
❯ move Safari to space 2
❯ balance the windows on this space
```

### 7. Model inspection

```bash
zener shell
❯ models
# Lists orchestrator + all sub-agent models
```

### 8. Single-task mode

```bash
zener run "open Terminal and type echo hello"
echo $?   # 0 = success, 1 = failure
```
