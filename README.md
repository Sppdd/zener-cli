# Zener CLI

AI-powered CLI assistant for macOS with Gemini Vision.

## Features

- **Desktop Automation**: Open apps, click, type, control windows via AppleScript
- **Screen Awareness**: Gemini Vision analyzes screenshots to understand context
- **Shell Access**: Run any shell command (with safety confirmations)
- **File Operations**: Read, write, and navigate the filesystem
- **Firebase Auth**: Google sign-in with usage tracking in Firestore

## Requirements

- macOS (required for AppleScript automation)
- Python 3.11+
- Google Cloud project with Vertex AI enabled
- Firebase project for authentication

## Setup

1. **Clone and install dependencies**:
   ```bash
   cd zener-cli
   pip install -e .
   ```

2. **Configure environment**:
   ```bash
   cp .env.example .env
   # Edit .env with your Firebase and GCP credentials
   ```

3. **Set up Google Cloud Application Default Credentials**:
   ```bash
   gcloud auth application-default login
   gcloud auth application-default set-quota-project zener-ai-hackathon
   ```

4. **Enable required APIs**:
   - Vertex AI API
   - Firebase Authentication
   - Cloud Firestore

## Usage

### Interactive REPL

```bash
zener shell
```

Commands:
- `exit` - Exit Zener
- `screenshot` - Take and analyze screenshot
- `whoami` - Show current user
- `usage` - Show usage stats
- Any other text is sent to the AI agent

### Single Task

```bash
zener run "open safari and go to github"
```

### Screenshot

```bash
zener screenshot
```

## Examples

```
$ zener shell

┌─ Zener AI ─────────────────────────────┐
│  Logged in as user@example.com          │
│  Usage: 0.0 / 60 minutes                │
──────────────────────────────────────────│

> open safari and search for "gemini AI"

[Zener] Analyzing task...
[Zener] Executing 3 action(s)...
[1] Opening Safari ✓
[2] Opening https://www.google.com/search?q=gemini+AI ✓
[3] Task completed ✓

> take a screenshot
```

## Architecture

```
┌──────────────────┐
│  User (Terminal) │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│   CLI (REPL)     │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Agent (Gemini)   │
│  - Analyze task  │
│  - Plan actions  │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Executor        │
│  - AppleScript   │
│  - Shell         │
│  - Files         │
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  macOS System    │
└──────────────────┘
```

## Safety

- Dangerous shell commands (rm -rf, dd, etc.) require confirmation
- File operations are sandboxed to user-accessible paths
- Firebase usage tracking for quota management
