"""
server/router.py
Master Router — Gemini 2.5 Flash JSON classification.
Routes user tasks into deterministic categories for the executor.
Zero-hallucination: Gemini outputs JSON, Python dispatches.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

ROUTER_MODEL = "gemini-2.5-flash"
_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "zener-ai-hackathon")
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        _client = genai.Client(
            vertexai=True,
            project=project_id,
            location=location,
        )
    return _client


@dataclass
class TaskClassification:
    """Structured output from the Master Router."""
    category: str  # BROWSER | DESKTOP | FORM | RESEARCH | CREATIVE | UNKNOWN
    task: str  # Clear task description
    target_url: str | None  # URL if applicable
    confidence: float = 1.0


SYSTEM_PROMPT = """You are Zener's Master Router. Classify the user's task into a JSON object.

Return ONLY valid JSON (no markdown, no explanation):

{
    "category": "BROWSER" | "DESKTOP" | "FORM" | "RESEARCH" | "CREATIVE" | "UNKNOWN",
    "task": "<clear task description>",
    "target_url": "<url if applicable or null>"
}

Rules:
- BROWSER: open websites, navigate, click links, fill forms in a browser
- DESKTOP: OS-level actions (file manager, settings, apps outside browser)
- FORM: fill out and submit a specific web form (e.g., "fill out this Google Form")
- RESEARCH: search + extract information from the web
- CREATIVE: draw, create, write long-form content
- UNKNOWN: unclear task — ask for clarification

Examples:
- "Open youtube and search for cat videos" → {"category":"BROWSER","task":"Search for cat videos on YouTube","target_url":"https://youtube.com"}
- "What's the weather in Tokyo?" → {"category":"RESEARCH","task":"Check weather in Tokyo","target_url":null}
- "Create a PowerPoint about my vacation" → {"category":"CREATIVE","task":"Create presentation about vacation","target_url":null}
"""


async def classify_intent(task: str) -> TaskClassification:
    """
    Classify a user task using Gemini 2.5 Flash.
    Returns a structured TaskClassification object.
    """
    client = _get_client()
    response = None
    try:
        response = await client.aio.models.generate_content(
            model=ROUTER_MODEL,
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part(text=f"{SYSTEM_PROMPT}\n\nUSER TASK: {task}")]
                )
            ],
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
        text = response.text.strip()
        # Strip any markdown code blocks if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        data = json.loads(text)
        return TaskClassification(
            category=data.get("category", "UNKNOWN"),
            task=data.get("task", task),
            target_url=data.get("target_url"),
        )
    except json.JSONDecodeError:
        logger.warning("Router JSON parse failed, returning UNKNOWN")
        return TaskClassification(category="UNKNOWN", task=task, target_url=None)
    except Exception as exc:
        logger.error("Router failed: %s", exc)
        return TaskClassification(category="UNKNOWN", task=task, target_url=None)
