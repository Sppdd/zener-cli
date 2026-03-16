"""
server/agent.py
Conductor module — routes tasks to the Gemini Computer Use executor.
This is Brain 1 of the Two-Brain AI system.
"""
import logging

from . import gemini_loop, hive, router

logger = logging.getLogger(__name__)


def _emit_event(session_id: str, event: dict) -> None:
    """Emit an event to the session's event queue."""
    from .session import _session_events
    if session_id in _session_events:
        _session_events[session_id].append(event)


async def delegate_to_executor(
    session_id: str,
    task: str,
) -> str:
    """
    Master routing + executor entry point.
    1. Classify intent via Master Router (Gemini Flash)
    2. Run the Computer Use executor loop
    3. Return summary to the caller.
    """
    classification = await router.classify_intent(task)

    logger.info(
        "Task classified: category=%s, task=%s, url=%s",
        classification.category,
        classification.task,
        classification.target_url,
    )

    hive.hive_write(session_id, "task_category", classification.category)
    hive.hive_write(session_id, "task_description", classification.task)
    if classification.target_url:
        hive.hive_write(session_id, "target_url", classification.target_url)

    if classification.target_url:
        from . import executor
        await executor.execute_action("open_url", {"url": classification.target_url})

    result = await gemini_loop.run_task_loop(
        session_id=session_id,
        task=classification.task,
    )

    return result


conductor_instruction = """You are Zener's Conductor — Brain 1 of the Two-Brain AI system.

Your job:
1. Receive the user's task description
2. Route it to the Master Router for classification (the system does this automatically)
3. Report the executor's progress back to the user
4. Keep responses SHORT (1-2 sentences)

Rules:
- NEVER execute visual actions yourself — the Executor (Brain 2) handles that
- If the user asks to do something dangerous (delete files, send emails), ask for confirmation first
- If the task is unclear, ask the user to clarify
- When the executor finishes, summarize the result clearly
"""


def create_conductor_agent():
    """
    Create the ADK Agent for the Conductor.
    Note: For simplicity, we use direct function calls instead of ADK Runner.
    Returns the delegate function that can be called directly.
    """
    return delegate_to_executor
