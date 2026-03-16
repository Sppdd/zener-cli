"""
_vision.py — Gemini multimodal image description for Zener.

Used by executor.describe_screenshot() to get a textual description
of a screenshot. Kept separate from agent.py to avoid circular imports
(executor → _vision, agent → executor).

Uses google-genai directly (ADK bundles it), initialised from config.
"""
import logging
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from . import config

logger = logging.getLogger(__name__)

_client: Optional[genai.Client] = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        cfg = config.get_config()
        if cfg.gemini_api_key:
            _client = genai.Client(api_key=cfg.gemini_api_key)
        else:
            _client = genai.Client(
                vertexai=True,
                project=cfg.gcp_project,
                location=cfg.gcp_location,
            )
    return _client


def describe_image(path: Path) -> str:
    """Return a concise natural-language description of a screenshot.

    Args:
        path: Path to a PNG screenshot file.

    Returns:
        Human-readable description of screen content.

    Raises:
        Exception: propagated to caller (executor wraps in error dict).
    """
    cfg = config.get_config()
    client = _get_client()

    with open(path, "rb") as f:
        image_data = f.read()

    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part(
                    text=(
                        "Describe what is currently visible on this macOS screen. "
                        "Include: visible windows and their titles, active application, "
                        "any text in focus, buttons or UI elements near the mouse, "
                        "and approximate coordinates of key interactive elements. "
                        "Be concise and factual — no preamble."
                    )
                ),
                types.Part(
                    inline_data=types.Blob(
                        data=image_data,
                        mime_type="image/png",
                    )
                ),
            ],
        )
    ]

    response = client.models.generate_content(
        model=cfg.screen_model,
        contents=contents,  # type: ignore[arg-type]
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=1024,
        ),
    )

    return (response.text or "").strip()
