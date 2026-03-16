"""
server/session.py
Session lifecycle management.

Provides:
  POST /api/session/start          — legacy REST session (original flow)
  GET  /api/session/{id}/events    — poll events (legacy)
  WS   /ws/agent/{session_id}      — NEW: full-duplex ADK agent loop
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import agent, auth, hive, adk_loop

logger = logging.getLogger(__name__)

router = APIRouter()

SESSION_TTL_SECONDS = 600


class SessionState(str, Enum):
    STARTING = "starting"
    ACTIVE = "active"
    COMPLETE = "complete"
    ERROR = "error"
    ABORTED = "aborted"


@dataclass
class Session:
    """In-memory session state."""
    session_id: str
    uid: str
    task: str
    state: SessionState = SessionState.STARTING
    action_count: int = 0
    created_at: float = field(default_factory=lambda: asyncio.get_event_loop().time())


_active_sessions: dict[str, Session] = {}
_session_tasks: dict[str, asyncio.Task] = {}
_session_events: dict[str, list[dict]] = {}


# ── NEW: WebSocket ADK agent endpoint ─────────────────────────────────────────

@router.websocket("/ws/agent/{session_id}")
async def ws_agent(websocket: WebSocket, session_id: str) -> None:
    """
    Full-duplex WebSocket endpoint for the Zener ADK agent loop.

    Auth: Bearer token in the Sec-WebSocket-Protocol header (gcloud ADC identity token).
    The CLI sends the token as the WebSocket subprotocol:
        websockets.connect(url, subprotocols=["bearer.<token>"])

    Message protocol: see adk_loop.py docstring.
    """
    # ── Auth: extract token from Authorization header or subprotocol ─────────
    token_str: str = ""

    # Primary: standard Authorization: Bearer <token> header
    auth_header = websocket.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token_str = auth_header[7:].strip()

    # Fallback: token embedded in Sec-WebSocket-Protocol header
    if not token_str:
        subprotocols = websocket.headers.get("sec-websocket-protocol", "")
        for proto in subprotocols.split(","):
            proto = proto.strip()
            if proto.startswith("bearer."):
                token_str = proto[len("bearer."):]
                break

    if not token_str:
        await websocket.close(code=4001, reason="Missing auth token")
        return

    # Verify the Google identity token
    uid = await _verify_google_token(token_str)
    if not uid:
        await websocket.close(code=4003, reason="Invalid auth token")
        return

    # Accept — no subprotocol needed (auth is via header)
    await websocket.accept()

    logger.info("WS /ws/agent/%s connected (uid=%s)", session_id, uid)

    try:
        # Wait for the first message: { type: "task", task: str, screenshot_b64: str }
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
        message = json.loads(raw)

        if message.get("type") != "task":
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Expected first message type='task', got '{message.get('type')}'",
            }))
            await websocket.close()
            return

        # Hand off to the ADK loop (blocks until done)
        await adk_loop.run_agent_loop(websocket, session_id, message)

    except asyncio.TimeoutError:
        await websocket.send_text(json.dumps({
            "type": "error", "message": "Timed out waiting for task message",
        }))
        await websocket.close()
    except WebSocketDisconnect:
        logger.info("WS /ws/agent/%s disconnected", session_id)
    except Exception as e:
        logger.exception("WS /ws/agent/%s error: %s", session_id, e)
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
            await websocket.close()
        except Exception:
            pass


async def _verify_google_token(token: str) -> str | None:
    """
    Verify a Google identity token (from gcloud ADC) by calling Google's
    tokeninfo endpoint.  Returns the `sub` (user ID) on success, None on failure.

    We intentionally use Google's tokeninfo instead of Firebase verify_token
    because the CLI uses gcloud ADC, not Firebase.
    """
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://oauth2.googleapis.com/tokeninfo",
                params={"id_token": token},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Accept tokens for our project audience OR generic Google accounts
                return data.get("sub") or data.get("email")
            else:
                logger.warning("tokeninfo rejected: %s %s", resp.status_code, resp.text[:200])
                return None
    except Exception as e:
        logger.warning("Token verification error: %s", e)
        return None


# ── Legacy REST session endpoints (unchanged) ─────────────────────────────────

async def start_session_task(session_id: str, task: str) -> None:
    """Background task that runs the agent loop for a session."""
    session = _active_sessions.get(session_id)
    if not session:
        return

    session.state = SessionState.ACTIVE

    _session_events[session_id].append({
        "type": "session_ready",
        "sessionId": session_id,
        "streamUrl": f"/stream/{session_id}",
    })

    try:
        result = await agent.delegate_to_executor(
            session_id=session_id,
            task=task,
        )
        session.state = SessionState.COMPLETE
        _session_events[session_id].append({
            "type": "task_complete",
            "summary": result,
        })
    except asyncio.CancelledError:
        session.state = SessionState.ABORTED
        _session_events[session_id].append({
            "type": "error",
            "message": "Session aborted by user.",
        })
    except Exception as exc:
        logger.exception("Session %s error: %s", session_id, exc)
        session.state = SessionState.ERROR
        _session_events[session_id].append({
            "type": "error",
            "message": str(exc),
        })
    finally:
        hive.hive_clear(session_id)
        _active_sessions.pop(session_id, None)
        _session_tasks.pop(session_id, None)


class StartSessionRequest(BaseModel):
    id_token: str
    task: str


@router.post("/api/session/start")
async def start_session(body: StartSessionRequest):
    """Create a new session and return session_id + stream_url."""
    if not body.id_token or not body.task:
        return {"error": "Missing id_token or task"}, 400

    user = await auth.verify_token(body.id_token)

    session_id = str(uuid.uuid4())[:8]

    session = Session(
        session_id=session_id,
        uid=user["uid"],
        task=body.task,
    )
    _active_sessions[session_id] = session
    _session_events[session_id] = []

    task_obj = asyncio.create_task(
        start_session_task(session_id, body.task),
        name=f"session-{session_id}",
    )
    _session_tasks[session_id] = task_obj

    return {
        "sessionId": session_id,
        "streamUrl": f"/stream/{session_id}",
    }


@router.get("/api/session/{session_id}/events")
async def get_session_events(session_id: str):
    """Poll for session events."""
    if session_id not in _active_sessions:
        return {"error": "Session not found"}, 404

    events = _session_events.get(session_id, [])
    _session_events[session_id] = []
    return {"events": events}


class ConfirmSessionRequest(BaseModel):
    confirmed: bool


@router.post("/api/session/{session_id}/confirm")
async def confirm_session(session_id: str, body: ConfirmSessionRequest):
    """Handle user confirmation for risky actions."""
    if session_id not in _active_sessions:
        return {"error": "Session not found"}, 404

    _session_events[session_id].append({
        "type": "confirmation_result",
        "confirmed": body.confirmed,
    })
    return {"status": "confirmation received"}


@router.post("/api/session/{session_id}/abort")
async def abort_session(session_id: str):
    """Abort a session."""
    if session_id in _session_tasks:
        _session_tasks[session_id].cancel()
    if session_id in _active_sessions:
        _active_sessions[session_id].state = SessionState.ABORTED
    return {"status": "aborted"}
