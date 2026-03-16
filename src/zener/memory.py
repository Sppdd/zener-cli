"""
memory.py — ADK session and memory service singletons for Zener.

Provides:
  - InMemorySessionService: per-shell-session conversation history,
    automatically managed by ADK's Runner per task.
  - InMemoryMemoryService: cross-task semantic memory within a single
    shell session. Completed task sessions are added here so future
    tasks can query past context via the load_memory tool.

Both services are module-level singletons, shared across all agent Runner
instances created during one `zener shell` invocation. They reset when
the process exits.

Usage:
    from . import memory

    # In the runner setup:
    runner = Runner(
        agent=orchestrator,
        app_name=memory.APP_NAME,
        session_service=memory.session_service,
        memory_service=memory.memory_service,
    )

    # After a task completes, add the session to long-term memory:
    await memory.commit_session(session_id, user_id)
"""
import logging
import uuid
from typing import Optional

from google.adk.sessions import InMemorySessionService
from google.adk.memory import InMemoryMemoryService

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

APP_NAME = "zener"
# Single user ID for the local desktop agent (no multi-user needed)
USER_ID = "local"

# ── Singletons ─────────────────────────────────────────────────────────────────

# Stores per-task conversation turns (ADK manages this automatically via Runner)
session_service: InMemorySessionService = InMemorySessionService()

# Stores cross-task facts extracted from completed sessions
memory_service: InMemoryMemoryService = InMemoryMemoryService()


# ── Helpers ───────────────────────────────────────────────────────────────────

def new_session_id() -> str:
    """Generate a unique session ID for one agent task run."""
    return str(uuid.uuid4())


async def commit_session(session_id: str) -> None:
    """Add a completed session's turns to the long-term memory service.

    Call this after a task finishes so the agent can recall context in
    future tasks within the same shell session.
    """
    try:
        session = await session_service.get_session(
            app_name=APP_NAME,
            user_id=USER_ID,
            session_id=session_id,
        )
        if session:
            await memory_service.add_session_to_memory(session)
            logger.debug(f"Session {session_id} committed to memory")
    except Exception as e:
        # Memory commit is best-effort — never break the main loop
        logger.warning(f"Could not commit session to memory: {e}")


def reset() -> None:
    """Reset all session and memory state (useful for tests or fresh start)."""
    global session_service, memory_service
    session_service = InMemorySessionService()
    memory_service = InMemoryMemoryService()
    logger.debug("Session and memory services reset")
