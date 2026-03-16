"""
server/stream.py
Xvfb screen capture via scrot → MJPEG streaming endpoint.
scrot works reliably on headless Xvfb (unlike mss/PIL.ImageGrab).
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import subprocess
import time
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

DISPLAY = ":99"
SCREEN_RES = "1280x720"
FRAME_DELAY = 0.1  # ~10 FPS for streaming
_ongoing_capture: bool = False


def capture_frame() -> bytes:
    """Capture a single JPEG frame from Xvfb using scrot."""
    global _ongoing_capture
    _ongoing_capture = True
    try:
        env = os.environ.copy()
        env["DISPLAY"] = DISPLAY
        result = subprocess.run(
            [
                "scrot",
                "-",
                "-o",  # output to stdout
            ],
            capture_output=True,
            timeout=2,
            env=env,
        )
        if result.returncode != 0:
            logger.warning("scrot failed: %s", result.stderr.decode())
            return b""
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning("scrot timed out")
        return b""
    except Exception as exc:
        logger.error("capture_frame error: %s", exc)
        return b""
    finally:
        _ongoing_capture = False


async def mjpeg_generator(session_id: str) -> AsyncGenerator[bytes, None]:
    """
    Yield MJPEG frames for streaming to the browser.
    Uses multipart/x-mixed-replace for native browser support.
    """
    logger.info("Starting MJPEG stream for session: %s", session_id)
    while True:
        loop = asyncio.get_event_loop()
        frame = await loop.run_in_executor(None, capture_frame)
        if frame:
            yield (
                b"--frame\r\n"
                b"Content-Type: image/png\r\n\r\n" + frame + b"\r\n"
            )
        await asyncio.sleep(FRAME_DELAY)


def setup_stream_routes(app: FastAPI) -> None:
    """Register the MJPEG streaming route with FastAPI."""

    @app.get("/stream/{session_id}")
    async def stream_session(session_id: str):
        """MJPEG stream endpoint — browser displays natively."""
        return StreamingResponse(
            mjpeg_generator(session_id),
            media_type="multipart/x-mixed-replace; boundary=frame"
        )

    @app.get("/stream/{session_id}/single")
    async def single_frame(session_id: str):
        """One-shot screenshot endpoint for AI vision calls."""
        loop = asyncio.get_event_loop()
        frame = await loop.run_in_executor(None, capture_frame)
        from fastapi.responses import Response
        return Response(content=frame, media_type="image/png")
