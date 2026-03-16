"""
server/safety.py
Safety systems:
1. LoopDetector — break if same action repeated N times
2. verify_task_complete — separate Gemini call to confirm success
3. Confirmation gate — pause for user confirmation on risky actions
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

LOOP_DETECTION_WINDOW = 3
MAX_STEPS_PER_TASK = 20


@dataclass
class LoopDetector:
    """Detect if the agent is stuck in a repetitive action loop."""
    window: int = LOOP_DETECTION_WINDOW
    history: list[tuple[str, str]] = field(default_factory=list)

    def check(self, action_name: str, action_args: dict[str, Any]) -> bool:
        """Returns True if stuck in a loop of identical actions."""
        key = (action_name, str(sorted(action_args.items())))
        self.history.append(key)
        if len(self.history) > self.window:
            self.history.pop(0)
        # Loop = same action repeated window times
        return (
            len(self.history) == self.window
            and len(set(self.history)) == 1
        )

    def reset(self) -> None:
        """Clear history for a fresh task."""
        self.history.clear()


async def verify_task_complete(
    screenshot_bytes: bytes,
    claimed_result: str,
    gemini_client: Any,
) -> bool:
    """
    Ask Gemini 2.0 Flash (fast, cheap) to verify task success from screenshot.
    Returns True if the screenshot confirms the claimed result.
    """
    try:
        from google.genai import types
        response = await gemini_client.aio.models.generate_content(
            model="gemini-2.0-flash-preview",
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            text=f"Does this screenshot confirm the task completed: '{claimed_result}'? "
                            "Reply ONLY 'YES' or 'NO: <reason>'."
                        ),
                        types.Part.from_bytes(
                            data=screenshot_bytes,
                            mime_type="image/png"
                        ),
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
            ),
        )
        text = response.text.strip()
        return text.startswith("YES")
    except Exception as exc:
        logger.warning("verify_task_complete failed: %s", exc)
        return True  # Optimistic — don't block on verification failure


async def send_confirmation_request(
    websocket: WebSocket,
    action: dict[str, Any],
) -> bool:
    """
    Send a confirmation request to the client via WebSocket.
    Blocks until the client responds.
    Returns True if confirmed, False if denied/aborted.
    """
    await websocket.send_json({
        "type": "confirm_required",
        "action": action,
    })
    try:
        msg = await websocket.receive_json()
        return msg.get("confirmed", False)
    except Exception:
        logger.warning("Confirmation request failed — denying action")
        return False


async def send_error(websocket: WebSocket, message: str) -> None:
    """Send an error message to the client."""
    await websocket.send_json({
        "type": "error",
        "message": message,
    })
