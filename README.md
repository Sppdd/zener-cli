# Zener: Your AI Hands on the Screen

<p align="center">
  <img src="https://img.shields.io/badge/Version-0.3.0-blue" alt="Version">
  <img src="https://img.shields.io/badge/Platform-macOS-green" alt="Platform">
  <img src="https://img.shields.io/badge/AI-Google%20ADK%20%2B%20Gemini-orange" alt="AI">
  <img src="https://img.shields.io/badge/Cloud-Google%20Cloud%20Run-red" alt="Cloud">
</p>

Zener is an AI desktop automation agent that acts as **your hands on the screen**. Using Google's ADK multi-agent framework and Gemini Vision, Zener observes your desktop, reasons about what it sees, and executes actions—opening apps, clicking buttons, typing text, and more.

---


### 📃 Text Description

**Project Overview**

Zener is an AI desktop automation agent for macOS that combines cloud-powered AI reasoning with local desktop control. Unlike chatbots that just talk, Zener **does the work for you**—controlling your mouse, keyboard, and applications through natural conversation.

**Key Features:**

1. **Multi-Agent Architecture** — Google ADK orchestrator delegates to 4 specialist sub-agents:
   - **ScreenAgent**: Takes and describes screenshots using Gemini Vision
   - **InputAgent**: Controls mouse (click, double-click, right-click, scroll, drag) and keyboard (type, press keys)
   - **WindowAgent**: Manages windows and spaces (optional yabai integration)
   - **ShellAgent**: Runs shell commands, reads/writes files

2. **Cloud-Powered AI** — All reasoning runs on Google Cloud Run with Vertex AI:
   - **Orchestrator**: gemini-2.5-pro (deep reasoning)
   - **Sub-agents**: gemini-2.5-flash (fast, efficient)

3. **Session Memory** — Within a shell session, Zener remembers context from earlier tasks.

4. **Desktop Context** — Every task starts with a live snapshot: frontmost app, open windows, screenshot description.

5. **Real-time Streaming** — Watch the agent think and act step-by-step in your terminal.

**Technologies Used:**

| Layer | Technology |
|-------|-------------|
| Language | Python 3.11+ |
| CLI | Click + prompt_toolkit |
| AI Framework | Google ADK (Agent Development Kit) |
| Vision | Gemini 2.5 Flash (Vertex AI) |
| Orchestrator | Gemini 2.5 Pro (Vertex AI) |
| Auth | Google ADC Identity Tokens |
| Cloud | Google Cloud Run, Vertex AI |
| macOS Input | PyAutoGUI |
| Window Mgmt | yabai (optional) |
| Container | Docker + Cloud Build + Cloud Run |

**Findings & Learnings:**

1. **Model Availability** — gemini-2.0-flash was deprecated mid-development; migrated to gemini-2.5-flash-lite
2. **Cloud Deployment** — Cloud Run provides faster responses and higher quotas than client-side API calls
3. **Bidirectional Protocol** — WebSocket `action_request` events allow cloud agents to control the local Mac
4. **Graceful Degradation** — yabai is optional; window tasks return helpful install hints when unavailable

---

### 👨‍💻 URL to Public Code Repository


**Repository Structure:**
- `zener-cli/` — macOS CLI client (what users install)
- `zener-server/` — Cloud Run backend (ADK agents, Vertex AI)

#### Spin-Up Instructions

```bash
# 1. Clone the repo
git clone https://github.com/etharo/zener-cli.git
cd zener-cli

# 2. Create a Python virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install Python dependencies
pip install -e .

# 4. Set up API keys/configuration
# Copy .env.example to .env, then fill in your keys (see .env.example for details)
cp ../.env.example .env
# Edit .env and fill in your Firebase, Google Cloud, and Gemini API credentials

# 5. (Optional, for GCP usage) Authenticate with Google Cloud if deploying server/code:
#gcloud auth login

# 6. Run Zener CLI
zener setup    # enter API keys, perform initial config
zener shell    # interactive CLI session
```

---

### 🖥️ Proof of Google Cloud Deployment

**Live Backend**: https://zener-server-902816427420.us-central1.run.app

**Deployment Evidence:**
```
Service: zener-server
Region: us-central1
URL: https://zener-server-902816427420.us-central1.run.app
Revision: zener-server-00016-dcp
Image: gcr.io/zener-ai-hackathon/zener-server:latest
Memory: 2Gi | CPU: 2
Deployed via: Cloud Build → Cloud Run
```

**Infrastructure as Code** (see `zener-server/cloudbuild.yaml`):
```yaml
steps:
  - name: "gcr.io/cloud-builders/docker"
    args: ["build", "-t", "gcr.io/$PROJECT_ID/zener-server:latest", "."]
  - name: "gcr.io/google.com/cloudsdktool/cloud-sdk"
    entrypoint: gcloud
    args: ["run", "deploy", "zener-server", 
           "--image=gcr.io/$PROJECT_ID/zener-server:latest",
           "--region=us-central1", "--platform=managed"]
```

Deploy with one command:
```bash
cd zener-server
gcloud builds submit --project=zener-ai-hackathon --config=cloudbuild.yaml .
```

---

### 🏗️ Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER'S MAC (local)                           │
│                                                                      │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │  zener CLI   │    │  PyAutoGUI   │    │    Screenshot        │  │
│  │  (Python)   │───▶│  mouse/kb    │◀───│    (capture)        │  │
│  └──────┬───────┘    └──────────────┘    └──────────────────────┘  │
│         │                                                           │
│  takes screenshot                                                   │
│  executes actions                                                   │
│         │ WebSocket (wss://zener-server-...run.app/ws/agent/...)   │
└─────────┼───────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    GOOGLE CLOUD (Cloud Run)                         │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                    ADK Multi-Agent Loop                        │   │
│  │                                                                │   │
│  │   ┌────────────────┐                                           │   │
│  │   │ Orchestrator  │  gemini-2.5-pro                         │   │
│  │   │   (Zener)     │──▶ ScreenAgent (gemini-2.5-flash)       │   │
│  │   │                │──▶ InputAgent  (gemini-2.5-flash)       │   │
│  │   │                │──▶ WindowAgent (gemini-2.5-flash)      │   │
│  │   │                │──▶ ShellAgent  (gemini-2.5-flash)      │   │
│  │   └────────────────┘                                           │   │
│  │                                                                │   │
│  │   InMemorySessionService + InMemoryMemoryService              │   │
│  └──────────────────────────────────────────────────────────────┘   │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                   Vertex AI API                               │   │
│  │   gemini-2.5-pro, gemini-2.5-flash                         │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

**Data Flow:**
1. User types task → CLI takes screenshot, sends to Cloud Run
2. Orchestrator analyzes → Uses Gemini 2.5 Pro to reason
3. Delegates to specialist → Screen/Input/Window/Shell agents
4. Action request → Server sends `action_request` to CLI
5. CLI executes locally → PyAutoGUI clicks/types
6. Result sent back → Agent verifies, continues or completes

---

### 📹 Demonstration Video

*(Record separately showing Zener in action)*

**Script:**
1. Open terminal, run `zener shell`
2. Type: "open Safari and go to github.com"
3. Show agent thinking, tool calls, final result
4. Type: "take a screenshot" 
5. Show screenshot description
6. Type: "what apps do I have open?"
7. Show desktop context awareness

---

## 🚀 Quick Start

### Requirements

- macOS
- Python 3.11+
- Google Cloud SDK (`gcloud`)

### Install

```bash
git clone https://github.com/etharo/zener-web.git
cd zener-web/zener-cli
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### Setup

```bash
zener setup
# Follow prompts to authenticate with Google Firebase Auth
```

### Usage

**Interactive REPL:**
```bash
zener shell
❯ open Calculator
❯ go to google.com and search for "weather in NYC"
```

**Single Task:**
```bash
zener run "open Safari and go to github.com"
```

**Screenshot:**
```bash
zener screenshot
```

---

## 🛠️ Architecture Details

### Agent Tools

**ScreenAgent**
- `take_screenshot` — Capture current screen
- `describe_screenshot` — Gemini Vision analysis

**InputAgent**
- `mouse_click`, `mouse_double_click`, `mouse_right_click`
- `mouse_scroll`, `mouse_drag`
- `keyboard_type`, `keyboard_press_key`
- `open_application`, `open_url`

**WindowAgent** (yabai optional)
- `get_desktop_context` — Query windows/spaces/displays
- `yabai_focus_window`, `yabai_move_to_space`, etc.

**ShellAgent**
- `shell_run` — Execute commands in the cloud container
- `file_read`, `file_write`, `file_list_dir`

### Model Configuration

Override via environment variables:

```bash
export ZENER_ORCHESTRATOR_MODEL=gemini-2.5-pro
export ZENER_SCREEN_MODEL=gemini-2.5-flash
export ZENER_SERVER_URL=https://zener-server-902816427420.us-central1.run.app
```

---

## 🔒 Safety

- Dangerous shell commands (`rm -rf`, `dd`, `shutdown`, etc.) are blocked
- Shell commands require terminal confirmation when crossing safety thresholds
- All actions execute locally on your Mac—you control what Zener can do

---

## 📄 License

MIT License - See LICENSE file for details.

---

*Submitted for the Gemini Live Agent Challenge 2026*
