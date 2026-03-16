# Zener Server

Backend for the Zener AI Remote Assistance Platform.

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set environment variables:
```bash
export FIREBASE_PROJECT_ID=your-project-id
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=your-project-id
export GOOGLE_CLOUD_LOCATION=us-central1
```

3. Run locally:
```bash
uvicorn main:app --reload --port 8080
```

## Docker

Build and run:
```bash
docker build -t zener-server .
docker run -p 8080:8080 zener-server
```

## API Endpoints

- `GET /health` - Health check
- `GET /stream/{session_id}` - MJPEG video stream
- `GET /stream/{session_id}/single` - Single screenshot
- `POST /api/session/start` - Create session
- `WS /ws/{session_id}` - WebSocket for session events
