"""
server/gemini_loop.py
Gemini vision executor loop.
Analyzes screenshots with Gemini 2.5 Flash and outputs JSON actions to execute via xdotool.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types

from . import executor, safety, stream

logger = logging.getLogger(__name__)

VISION_MODEL = "gemini-2.0-flash-preview"
MAX_STEPS = 20


def _get_client() -> genai.Client:
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "zener-ai-hackathon")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    return genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
    )


@dataclass
class AgentTurn:
    """One turn of the Computer Use loop."""
    actions: list[dict[str, Any]]
    thought: str | None = None
    needs_confirmation: bool = False


async def capture_screenshot() -> bytes:
    """Capture current screen as JPEG bytes for Gemini vision."""
    return stream.capture_frame()


SYSTEM_PROMPT = """You are a desktop automation AI. Analyze the screenshot and determine what action to take.

You can output ONE action as JSON:
{"action": "click", "x": 100, "y": 200}
{"action": "type", "text": "hello"}
{"action": "open_url", "url": "https://youtube.com"}
{"action": "press", "key": "Enter"}
{"action": "done", "result": "Task completed"}

Available actions:
- click: move mouse to x,y and click
- type: type text
- open_url: open a URL in browser
- press: press a key (Enter, Escape, Tab, etc.)
- done: task is complete

Respond ONLY with valid JSON, no explanation."""


async def run_agent_turn(
    screenshot_bytes: bytes,
    task: str,
    step: int,
) -> AgentTurn:
    """
    Run one turn of the Gemini vision agent.
    - Sends the current screenshot + task to Gemini
    - Returns parsed actions from the model response as JSON
    """
    from .image_utils import compress_screenshot

    client = _get_client()

    compressed_bytes, mime_type = compress_screenshot(screenshot_bytes)

    prompt = f"{SYSTEM_PROMPT}\n\nTask: {task}"
    if step > 1:
        prompt = f"{SYSTEM_PROMPT}\n\nContinue with the task: {task}"

    response = await client.aio.models.generate_content(
        model=VISION_MODEL,
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part(text=prompt),
                    types.Part.from_bytes(data=compressed_bytes, mime_type=mime_type),
                ]
            )
        ],
        config=types.GenerateContentConfig(
            temperature=0.1,
        ),
    )

    text = response.text.strip()
    logger.info("Gemini response: %s", text[:200])

    actions = []
    thought = None

    try:
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        
        data = json.loads(text)
        
        action = data.get("action", "")
        if action == "done":
            pass
        elif action in ("click", "type", "open_url", "press"):
            actions.append({
                "name": action,
                "args": {k: v for k, v in data.items() if k != "action"},
                "requires_confirmation": False,
            })
    except json.JSONDecodeError:
        logger.warning("Failed to parse Gemini JSON response: %s", text[:100])

    return AgentTurn(actions=actions, thought=thought)


class EventEmitter:
    def __init__(self, session_id: str):
        self.session_id = session_id
        from .session import _session_events
        self._events = _session_events
    
    def emit(self, event: dict):
        if self.session_id in self._events:
            self._events[self.session_id].append(event)


def _emit(emitter: EventEmitter | None, event: dict):
    if emitter:
        emitter.emit(event)


async def run_task_loop(
    session_id: str,
    task: str,
    emitter: EventEmitter | None = None,
) -> str:
    """
    Main executor loop for a single task.
    - Captures screenshot → sends to Gemini Vision → executes actions → repeat
    - Respects loop detection, max steps, and confirmation gates
    - Returns a summary string when complete or max steps reached.
    """
    loop_detector = safety.LoopDetector()
    step_count = 0
    last_action_result = "started"

    from .hive import hive_write, hive_for_prompt

    _emit(emitter, {"type": "thinking", "text": "Starting task..."})

    while step_count < MAX_STEPS:
        step_count += 1

        screenshot = await capture_screenshot()
        if not screenshot:
            _emit(emitter, {"type": "error", "message": "Failed to capture screen"})
            break

        hive_context = hive_for_prompt(session_id)
        effective_task = f"{hive_context}\n\n{task}" if hive_context else task

        turn = await run_agent_turn(screenshot, effective_task, step_count)

        if turn.thought:
            _emit(emitter, {"type": "thinking", "text": turn.thought})

        if not turn.actions:
            if step_count > 1:
                break
            continue

        for action in turn.actions:
            action_name = action["name"]
            action_args = action["args"]

            if loop_detector.check(action_name, action_args):
                logger.warning("Loop detected, aborting: %s %s", action_name, action_args)
                _emit(emitter, {"type": "error", "message": "Agent stuck in a loop. Aborting."})
                return "Aborted: stuck in a loop"

            _emit(emitter, {
                "type": "action_start",
                "name": action_name,
                "args": action_args,
            })

            result = await executor.execute_action(action_name, action_args)
            last_action_result = result

            _emit(emitter, {
                "type": "action_done",
                "name": action_name,
                "result": result,
            })

            if action_name in ("type", "click") and "url" in action_args:
                hive_write(session_id, "last_url", action_args["url"])

    if step_count > 0:
        final_screenshot = await capture_screenshot()
        client = _get_client()
        verified = await safety.verify_task_complete(
            final_screenshot,
            task,
            client,
        )
        if verified:
            hive_write(session_id, "last_result", "success")
            return f"Task completed in {step_count} steps."

    return f"Stopped after {step_count} steps. Last result: {last_action_result}"
