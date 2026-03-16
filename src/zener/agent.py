import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from enum import Enum

from google import genai
from google.genai import types

from . import config, macos

logger = logging.getLogger(__name__)


class ActionType(Enum):
    OPEN_APP = "open_app"
    CLICK = "click"
    TYPE = "type"
    PRESS_KEY = "press_key"
    OPEN_URL = "open_url"
    RUN_SHELL = "run_shell"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    LIST_DIR = "list_dir"
    SCREENSHOT = "screenshot"
    DONE = "done"


@dataclass
class Action:
    type: ActionType
    params: Dict[str, Any]
    description: str
    
    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "params": self.params,
            "description": self.description,
        }


_client: Optional[genai.Client] = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        cfg = config.get_config()
        if cfg.gemini_api_key:
            _client = genai.Client(api_key=cfg.gemini_api_key)
            logger.info(f"Gemini client initialized with API key")
        else:
            _client = genai.Client(
                vertexai=True,
                project=cfg.gcp_project,
                location=cfg.gcp_location,
            )
            logger.info(f"Gemini client initialized for project: {cfg.gcp_project}")
    return _client


SYSTEM_PROMPT = """You are Zener, an AI assistant with full control over the user's Mac. 

You can:
- Open and control applications (Safari, Chrome, Terminal, etc.)
- Click, type, and press keys on the screen
- Take screenshots to see what's on screen
- Run shell commands
- Read and write files
- Browse the web

IMPORTANT RULES:
- NEVER run commands that reference "zener", "activate", "venv", or that would cause recursion
- Don't try to run the same command the user just typed - execute actual tasks
- Focus on what the user wants to ACCOMPLISH, not replicating their terminal commands

IMPORTANT: You must output your response as a JSON object with your plan and actions.

Response format:
{
  "thought": "What you're thinking and why",
  "actions": [
    {
      "type": "action_type",
      "params": {...},
      "description": "Human readable description"
    }
  ]
}

Available action types:
- "open_app": {"name": "Safari"} - Open an application
- "click": {"x": 100, "y": 200} - Click at coordinates
- "type": {"text": "hello world"} - Type text
- "press_key": {"key": "return"} - Press a key
- "open_url": {"url": "https://github.com"} - Open URL in browser
- "run_shell": {"command": "ls -la", "timeout": 30} - Run shell command
- "screenshot": {} - Take a screenshot to see the screen
- "read_file": {"path": "/path/to/file"} - Read file contents
- "write_file": {"path": "/path/to/file", "content": "..."} - Write to file
- "list_dir": {"path": "/path/to/dir"} - List directory
- "done": {} - Task is complete

For multi-step tasks, output multiple actions in sequence.

Example: User says "Open Safari and go to github"
{
  "thought": "I need to open Safari first, then navigate to github.com",
  "actions": [
    {"type": "open_app", "params": {"name": "Safari"}, "description": "Opening Safari"},
    {"type": "open_url", "params": {"url": "https://github.com"}, "description": "Navigating to github.com"},
    {"type": "done", "params": {}, "description": "Task complete"}
  ]
}

Always respond with valid JSON. Start your response with { and end with }."""


def analyze_task(task: str, screenshot_path: Optional[Path] = None) -> List[Action]:
    """Analyze a task and return a list of actions to execute.
    
    Args:
        task: The user's task description
        screenshot_path: Optional path to screenshot for vision analysis
        
    Returns:
        List of Action objects to execute
    """
    client = get_client()
    cfg = config.get_config()
    
    contents = [task]
    
    if screenshot_path and screenshot_path.exists():
        with open(screenshot_path, "rb") as f:
            image_data = f.read()
        
        contents.append(
            types.Content(
                parts=[
                    types.Part(
                        inline_data=types.Blob(
                            data=image_data,
                            mime_type="image/png",
                        )
                    )
                ]
            )
        )
    
    response = client.models.generate_content(
        model=cfg.gemini_model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.7,
            max_output_tokens=2048,
        ),
    )
    
    response_text = response.text.strip()
    
    if response_text.startswith("```json"):
        response_text = response_text[7:]
    if response_text.startswith("```"):
        response_text = response_text[3:]
    if response_text.endswith("```"):
        response_text = response_text[:-3]
    response_text = response_text.strip()
    
    logger.info(f"Gemini response: {response_text[:500]}...")
    
    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini response: {e}")
        logger.error(f"Response was: {response_text}")
        return [
            Action(
                type=ActionType.DONE,
                params={},
                description=f"Error parsing response: {str(e)}"
            )
        ]
    
    actions = []
    for action_data in parsed.get("actions", []):
        action_type = action_data.get("type", "")
        
        try:
            action_enum = ActionType(action_type)
        except ValueError:
            logger.warning(f"Unknown action type: {action_type}")
            continue
        
        actions.append(Action(
            type=action_enum,
            params=action_data.get("params", {}),
            description=action_data.get("description", ""),
        ))
    
    return actions


def analyze_screenshot(screenshot_path: Path) -> str:
    """Analyze a screenshot and return a description of what's on screen.
    
    Args:
        screenshot_path: Path to the screenshot
        
    Returns:
        Description of the screen content
    """
    client = get_client()
    cfg = config.get_config()
    
    with open(screenshot_path, "rb") as f:
        image_data = f.read()
    
    response = client.models.generate_content(
        model=cfg.gemini_model,
        contents=[
            types.Content(
                parts=[
                    types.Part(
                        text="Describe what's on this screen in detail. Include any visible windows, buttons, text, and UI elements."
                    ),
                    types.Part(
                        inline_data=types.Blob(
                            data=image_data,
                            mime_type="image/png",
                        )
                    )
                ]
            )
        ],
        config=types.GenerateContentConfig(
            temperature=0.7,
            max_output_tokens=1024,
        ),
    )
    
    return response.text.strip()
