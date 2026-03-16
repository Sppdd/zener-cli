"""
main.py
Zener Server — FastAPI entry point.
Hosts:
- MJPEG stream at /stream/{session_id}
- WebSocket at /ws/{session_id}
- REST API at /api/session/start
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server import session, stream

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: start Xvfb/Chrome (done by CMD in Dockerfile), yield, cleanup."""
    logger.info("Zener server starting on port %s", os.environ.get("PORT", 8080))
    yield
    logger.info("Zener server shutting down")


app = FastAPI(
    title="Zener AI Assistant",
    description="AI Remote Assistance Platform — Cloud Desktop with Gemini Computer Use",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO: restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

stream.setup_stream_routes(app)
app.include_router(session.router)


@app.get("/health")
async def health():
    """Liveness probe for Cloud Run."""
    return {"status": "healthy"}


@app.get("/")
async def root():
    """Redirect to docs or return basic info."""
    return {
        "service": "Zener AI Assistant",
        "version": "0.1.0",
        "docs": "/docs",
    }
