# Zener: Your AI Hands on the Screen

<p align="center">
  <img src="https://img.shields.io/badge/Version-0.3.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/Platform-macOS-green" alt="Platform">
  <img src="https://img.shields.io/badge/AI-Google%20ADK%20%2B%20Gemini-orange" alt="AI">
  <img src="https://img.shields.io/badge/Cloud-Google%20Cloud%20Run-red" alt="Cloud">
</p>

Zener is an AI desktop automation agent that acts as **your hands on the screen**. Using Google's ADK multi-agent framework and Gemini Vision, Zener observes your desktop, reasons about what it sees, and executes actions—opening apps, clicking buttons, typing text, and more.

---

## Quick Start

### Requirements

- macOS
- Python 3.11+
- Google Cloud SDK (`gcloud`) — [install](https://cloud.google.com/sdk/docs/install)

### 1. Install the CLI

```bash
git clone https://github.com/etharo/zener-web.git
cd zener-cli

c
```

### 2. Authenticate

```bash
# One-time Google login (opens browser)
gcloud auth login
gcloud auth application-default login
```

### 3. Run Zener

```bash
zener setup        # verifies auth, saves server URL
zener shell        # open the interactive REPL
```

```
  ╔══════════════════════════════════════════════════╗
  ║  Z E N E R — your hands on the screen          ║
  ╚══════════════════════════════════════════════════╝
  status: ready  (Vertex AI / Cloud Run)
  server: https://zener-server-902816427420.us-central1.run.app
  type:   your task, or help / exit

  ❯ open Safari and go to github.com
```

### Commands

```bash
zener shell                         # interactive REPL
zener run "open Calculator"         # single task, exits 0/1
zener screenshot                    # describe current screen
zener setup                         # re-run auth setup
```

---

## Architecture

```
╔══════════════════════════════════════════════════════════════════╗
║                        USER'S MAC                                ║
║                                                                  ║
║   ┌─────────────────────────────────────────────────────────┐   ║
║   │  zener CLI  (Python, prompt_toolkit)                    │   ║
║   │                                                         │   ║
║   │  1. Takes screenshot  ──▶  screencapture (macOS)       │   ║
║   │  2. Describes screen  ──▶  Gemini Vision (local call)  │   ║
║   │  3. Sends task + screenshot over WebSocket              │   ║
║   │  4. Receives action_request  ──▶  executes locally     │   ║
║   │     • mouse_click / double_click / right_click         │   ║
║   │     • mouse_scroll / mouse_drag                        │   ║
║   │     • keyboard_type / keyboard_press_key               │   ║
║   │     • open_application / open_url                      │   ║
║   │  5. Sends action_result back to server                  │   ║
║   └────────────────────┬────────────────────────────────────┘   ║
║                        │                                         ║
║              WebSocket (wss://)  +  Google Identity Token        ║
╚════════════════════════╪═════════════════════════════════════════╝
                         │
                         ▼
╔══════════════════════════════════════════════════════════════════╗
║                   GOOGLE CLOUD RUN                               ║
║                                                                  ║
║   ┌─────────────────────────────────────────────────────────┐   ║
║   │  FastAPI  ──  /ws/agent/{session_id}  (WebSocket)       │   ║
║   └────────────────────┬────────────────────────────────────┘   ║
║                        │                                         ║
║   ┌────────────────────▼────────────────────────────────────┐   ║
║   │  ADK Multi-Agent Loop  (adk_loop.py)                    │   ║
║   │                                                         │   ║
║   │   ┌──────────────────────────────────────────────────┐  │   ║
║   │   │  Orchestrator  (gemini-2.5-pro)                  │  │   ║
║   │   │    Reasons about the task, delegates to agents   │  │   ║
║   │   └──────┬───────────────────────────────────────────┘  │   ║
║   │          │                                               │   ║
║   │    ┌─────┼──────────────────────┐                       │   ║
║   │    ▼     ▼                      ▼                       │   ║
║   │  ScreenAgent   InputAgent    WindowAgent  ShellAgent     │   ║
║   │  (Flash)       (Flash)       (Flash)      (Flash)        │   ║
║   │  screenshots   clicks/keys   windows      shell cmds     │   ║
║   └─────────────────────────────────────────────────────────┘   ║
║                        │                                         ║
║   ┌────────────────────▼────────────────────────────────────┐   ║
║   │  Vertex AI API  (gemini-2.5-pro / gemini-2.5-flash)     │   ║
║   └─────────────────────────────────────────────────────────┘   ║
╚══════════════════════════════════════════════════════════════════╝
```

### Data Flow (one task cycle)

```
User types task
      │
      ▼
CLI takes screenshot ──▶ screencapture (local, ~0.5s)
      │
      ▼
CLI describes screen ──▶ Gemini Vision API (local, ~1–2s)
      │                   prints "Screen: ..."
      ▼
CLI opens WebSocket ──▶ Cloud Run /ws/agent/{session_id}
      │                  Auth: Google Identity Token (gcloud ADC)
      ▼
Server: ADK loop starts
  Orchestrator (gemini-2.5-pro) reasons about task + screenshot
  Delegates to sub-agent (ScreenAgent / InputAgent / ...)
  Sub-agent sends  action_request ──▶ CLI
      │
      ▼
CLI executes action locally (PyAutoGUI / AppleScript / osascript)
CLI sends  action_result ──▶ Server
      │
      ▼
Agent verifies result via new screenshot ──▶ repeats if needed
      │
      ▼
Server sends  done  ──▶ CLI prints result
```

---

## Cloud Deployment

### Live Backend

```
Service : zener-server
Region  : us-central1
URL     : https://zener-server-902816427420.us-central1.run.app
Image   : gcr.io/zener-ai-hackathon/zener-server:latest
Memory  : 2Gi  |  CPU: 2  |  Max instances: 10
Auth    : Google Identity Token (Cloud Run IAM)
```

### Automated Deployment (Infrastructure as Code)

The entire backend is deployed with a single script — no manual Cloud Console steps.

**`server/deploy.sh`** provisions everything from scratch, idempotently:

| Step | What it does |
|------|-------------|
| 1 | Enables Cloud Build, Cloud Run, Container Registry, Vertex AI APIs |
| 2 | Creates `zener-server-sa` service account (if not exists) |
| 3 | Grants IAM roles: `aiplatform.user`, `logging.logWriter`, `monitoring.metricWriter`, `storage.objectViewer` |
| 4 | Builds Docker image via Cloud Build and pushes to GCR |
| 5 | Deploys to Cloud Run with all runtime flags |
| 6 | Prints the live service URL |

```bash
# Deploy from scratch (or update) with one command:
cd server
./deploy.sh

# Override project or region:
PROJECT_ID=my-project REGION=us-east1 ./deploy.sh
```

**`server/cloudbuild.yaml`** is used by Cloud Build for CI/CD — build, push, and deploy on every `gcloud builds submit`:

```yaml
steps:
  - name: "gcr.io/cloud-builders/docker"
    args: ["build", "-t", "gcr.io/$PROJECT_ID/zener-server:latest", ...]
  - name: "gcr.io/cloud-builders/docker"
    args: ["push", "--all-tags", "gcr.io/$PROJECT_ID/zener-server"]
  - name: "gcr.io/google.com/cloudsdktool/cloud-sdk"
    entrypoint: gcloud
    args: ["run", "deploy", "zener-server", "--image=...", "--region=us-central1", ...]
```

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.11+ |
| CLI | Click + prompt_toolkit |
| AI Framework | Google ADK (Agent Development Kit) |
| Vision | Gemini 2.5 Flash (Vertex AI) |
| Orchestrator | Gemini 2.5 Pro (Vertex AI) |
| Auth | Google ADC — Identity Tokens (no API keys) |
| Cloud | Google Cloud Run, Vertex AI |
| macOS Input | PyAutoGUI + AppleScript |
| IaC | `deploy.sh` + `cloudbuild.yaml` |
| Container | Docker + Cloud Build |

---

## Architecture Details

### Sub-Agents

**ScreenAgent** (gemini-2.5-flash)
- `take_screenshot` — Requests CLI to capture the screen and return base64 PNG
- `describe_screenshot` — Gemini Vision analysis of what's visible

**InputAgent** (gemini-2.5-flash)
- `mouse_click`, `mouse_double_click`, `mouse_right_click`
- `mouse_scroll`, `mouse_drag`
- `keyboard_type`, `keyboard_press_key`
- `open_application`, `open_url`

**WindowAgent** (gemini-2.5-flash)
- Window and space management via yabai (optional — graceful fallback if not installed)

**ShellAgent** (gemini-2.5-flash)
- `shell_run` — Execute zsh commands in the cloud container
- `file_read`, `file_write`, `file_list_dir`

### Model Config

Override via environment variables before running `zener shell`:

```bash
export ZENER_SERVER_URL=https://zener-server-902816427420.us-central1.run.app
export ZENER_ORCHESTRATOR_MODEL=gemini-2.5-pro
export ZENER_SCREEN_MODEL=gemini-2.5-flash
```

---

## Safety

- Dangerous shell commands (`rm -rf`, `dd`, `shutdown`, etc.) are blocked at the executor level
- Risky operations prompt for terminal confirmation before executing
- All mouse/keyboard actions execute locally on your Mac — the cloud never touches your files directly

---

## Project Structure

```
zener-cli/                  # Full project (CLI + Cloud backend)
├── pyproject.toml          # CLI package config
├── src/zener/             # CLI Python package
│   ├── cli.py              # Click commands, REPL, Spinner UX
│   ├── loop.py             # WebSocket client, streaming event loop
│   ├── config.py           # Config dataclasses, server URL
│   ├── macos.py            # screencapture, AppleScript, PyAutoGUI
│   └── _vision.py          # Local Gemini Vision describe call
│
└── server/                 # Cloud Run backend
    ├── deploy.sh           # One-command IaC deploy script
    ├── cloudbuild.yaml     # Cloud Build CI/CD pipeline
    ├── Dockerfile          # Container definition
    ├── main.py             # FastAPI app entry point
    └── server/
        ├── adk_agent.py    # ADK multi-agent definitions (Vertex AI)
        ├── adk_loop.py     # WebSocket event streamer + action round-trips
        └── session.py      # /ws/agent/{session_id} WebSocket endpoint
```

---

## Key Findings

1. **Thin client pattern** — Moving all AI reasoning to Cloud Run eliminates local API key management and enables higher Vertex AI quotas
2. **WebSocket action round-trips** — `action_request` / `action_result` protocol lets the cloud agent control the local Mac without any VNC or remote desktop
3. **Spinner UX** — The CLI uses a live-updating spinner during the silent phases (auth, screenshot, connect) so the user always sees progress
4. **Model stability** — gemini-2.0-flash was deprecated mid-development; pinning to gemini-2.5-pro/flash via env vars makes future migrations easy

---

## License

MIT License

---

*Submitted for the Gemini Live Agent Challenge 2026*
