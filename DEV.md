# Zener CLI - Developer Guide

This document provides comprehensive information for developers working on Zener CLI.

## Project Overview

**Zener CLI** is an AI-powered CLI assistant for macOS that uses Gemini Vision to understand screen context and execute actions on your Mac.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| CLI Framework | Click + prompt_toolkit |
| AI | Gemini 2.5 Flash (Google AI Studio) |
| Auth | Firebase Auth (Google Sign-In) |
| Storage | Firestore |
| Automation | AppleScript, subprocess |
| Screenshots | macOS `screencapture` |

## Project Structure

```
zener-cli/
├── pyproject.toml          # Python project config
├── .env                    # Local environment ( secrets)
├── .env.example            # Environment template
├── .gitignore
├── README.md               # User-facing documentation
├── DEV.md                  # This file - developer documentation
└── src/
    └── zener/
        ├── __init__.py     # Package init
        ├── __main__.py     # CLI entry point
        ├── config.py       # Settings & credentials loader
        ├── firebase.py     # Firebase Auth + Firestore
        ├── macos.py        # AppleScript + screenshot + shell
        ├── agent.py        # Gemini Vision interaction
        ├── executor.py     # Action execution
        └── cli.py          # REPL interface
```

## Credentials & API Keys

### Gemini API (Primary)

- **Get Key**: https://aistudio.google.com/app/apikey
- **Model**: `gemini-2.5-flash`
- **API**: Google AI Studio (generativelanguage.googleapis.com)

### Firebase Project

- **Project ID**: Get from your Firebase console
- **Auth Domain**: `{project-id}.firebaseapp.com`
- **API Key**: Get from Firebase console → Project Settings → General

### Google Cloud (Optional - for Vertex AI)

- **Project ID**: Your GCP project
- **Location**: e.g., `us-central1`
- **Note**: Vertex AI requires Workload Identity or service account with `roles/aiplatform.user`

## Environment Variables

Create a `.env` file in the project root:

```bash
# Firebase Configuration
FIREBASE_API_KEY=your_firebase_api_key
FIREBASE_PROJECT_ID=your_firebase_project_id
FIREBASE_AUTH_DOMAIN=your_project.firebaseapp.com

# Google Cloud Configuration (optional - for Vertex AI)
GOOGLE_CLOUD_PROJECT=your_gcp_project
GCP_LOCATION=us-central1

# Gemini API (get from https://aistudio.google.com/app/apikey)
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash
```

## Firebase Firestore Structure

```
# users/{uid}
{
  "email": "user@gmail.com",
  "displayName": "User Name",
  "usageMinutes": 12.5,
  "plan": "free",
  "createdAt": timestamp
}

# sessions/{sessionId}
{
  "uid": "user123",
  "actionType": "open_app",
  "details": {"app": "Safari"},
  "timestamp": timestamp
}
```

## Development Setup

### 1. Install Dependencies

```bash
cd zener-cli
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### 2. Set Up Environment

```bash
cp .env.example .env
# Edit .env with your keys (or use the provided keys for testing)
```

### 3. Run Locally

```bash
# Interactive shell
zener shell

# Single task
zener run "open safari"

# Screenshot analysis
zener screenshot
```

## Code Architecture

### Flow

```
User Input → CLI (cli.py)
    ↓
Agent (agent.py) - Gemini Vision analysis
    ↓
Actions (JSON with type + params)
    ↓
Executor (executor.py) - Execute actions
    ↓
macOS (macos.py) - AppleScript, shell, screenshots
```

### Key Files

#### `config.py`

- Loads environment variables
- Manages singleton config
- Provides cache/temp directories

#### `agent.py`

- `get_client()` - Initializes Gemini client (API key or Vertex AI)
- `analyze_task(task, screenshot)` - Sends task + screenshot to Gemini
- `analyze_screenshot(path)` - Describes screenshot content
- `SYSTEM_PROMPT` - Instructions for Gemini (modify to change behavior)

#### `executor.py`

- `ActionExecutor.execute(action)` - Executes a single action
- Handles: `open_app`, `click`, `type`, `press_key`, `open_url`, `run_shell`, `read_file`, `write_file`, `list_dir`, `screenshot`, `done`
- `DANGEROUS_COMMANDS` - List of commands requiring confirmation

#### `macos.py`

- `take_screenshot()` - Uses `screencapture` command
- `run_applescript(script)` - Executes AppleScript
- `open_application(name)` - Opens app by name
- `run_shell_command(cmd)` - Runs shell command

#### `firebase.py`

- `login_with_google(id_token)` - Verifies Firebase token
- `get_usage()` - Gets user's usage from Firestore
- `log_action()` - Logs actions to Firestore

### Adding New Actions

1. Add action type to `agent.py` ActionType enum
2. Add to SYSTEM_PROMPT instructions
3. Add executor method in `executor.py`
4. Implement in `macos.py` if needed

## Common Issues

### 404 Model Not Found

If you see `Publisher Model not found`:
- For Vertex AI: Ensure project has `roles/aiplatform.user`
- For Gemini API: Check model name is valid (use `gemini-2.5-flash`)

### AppleScript Permission Denied

- Go to System Preferences → Security & Privacy → Privacy → Accessibility
- Add Terminal (or your IDE) to allowed apps

### Screenshot Permission Denied

- Go to System Preferences → Security & Privacy → Privacy → Screen Recording
- Add Terminal to allowed apps

## Deployment

### PyPI (Future)

```bash
pip build
pip upload
```

### Homebrew (Future)

Add to homebrew-core formula.

## Testing

```bash
# Test screenshot
zener screenshot

# Test opening app
zener run "open safari"

# Test shell command
zener run "say hello"
```

## Useful Commands

```python
# Check available Gemini models
from google import genai
client = genai.Client(api_key='YOUR_KEY')
models = client.models.list()
for m in models:
    print(m.name)
```

## Contact

- GitHub: https://github.com/Sppdd/zener-cli
- Project: Zener AI Hackathon
