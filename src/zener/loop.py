"""
loop.py — ADK Runner-based agent loop for Zener.

This replaces the old hand-rolled perception-action loop with Google ADK's
Runner, which drives the orchestrator ↔ sub-agent conversation automatically.

Key design choices (OpenCode-inspired):
  - Each task gets its own ADK Session (UUID)
  - Completed sessions are committed to InMemoryMemoryService for cross-task recall
  - LoopCallbacks interface preserved so cli.py doesn't need to change
  - Desktop context (windows/spaces/frontmost app) is prepended to every task
    so the orchestrator always knows what's on screen when planning
  - Spinner + live event streaming mirrors OpenCode's step-by-step UX

Event types surfaced to callbacks:
  - on_thought: orchestrator text reasoning blocks
  - on_tool_call: any tool or sub-agent call starting
  - on_tool_result: tool result received
  - on_done: final response text

ADK event model:
  event.author          → which agent or tool produced this event
  event.content         → Content with parts (text, function_call, function_response)
  event.is_final_response() → True for the last turn
"""
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from google.adk.runners import Runner
from google.genai import types as genai_types

from . import agent, config, macos, memory, yabai

logger = logging.getLogger(__name__)


# ── Callbacks interface (unchanged API — CLI wires in here) ───────────────────

class LoopCallbacks:
    """Hooks the CLI can attach to observe loop progress in real-time."""

    def on_thought(self, author: str, text: str) -> None:
        """Called when an agent emits reasoning text."""
        pass

    def on_tool_call(self, step: int, tool_name: str, tool_input: Dict[str, Any]) -> None:
        """Called just before a tool or sub-agent is invoked."""
        pass

    def on_tool_result(self, step: int, tool_name: str, ok: bool, summary: str) -> None:
        """Called after a tool or sub-agent returns."""
        pass

    def on_screenshot(self, description: str) -> None:
        """Called when a screenshot description is available."""
        pass

    def on_final(self, text: str) -> None:
        """Called with the orchestrator's final response text."""
        pass

    def on_done(self, success: bool) -> None:
        """Called when the loop finishes."""
        pass

    def confirm_dangerous(self, message: str) -> bool:
        """Ask the user to confirm a dangerous command. Default: block."""
        return False


# ── AgentLoop ────────────────────────────────────────────────────────────────

class AgentLoop:
    """Drives the ADK Runner for one `zener shell` session.

    Maintains:
      - The ADK Runner (wraps orchestrator + session + memory services)
      - A step counter for the callbacks
    """

    def __init__(self, callbacks: Optional[LoopCallbacks] = None):
        self.callbacks = callbacks or LoopCallbacks()
        self._step = 0
        self._runner: Optional[Runner] = None

    def _get_runner(self) -> Runner:
        """Lazily create the ADK Runner (builds agents on first call)."""
        if self._runner is None:
            self._runner = Runner(
                agent=agent.get_orchestrator(),
                app_name=memory.APP_NAME,
                session_service=memory.session_service,
                memory_service=memory.memory_service,
            )
        return self._runner

    def run(self, task: str) -> bool:
        """Synchronous wrapper — runs the async loop in a new event loop.

        Returns True if the agent completed successfully, False otherwise.
        """
        return asyncio.run(self.run_async(task))

    async def run_async(self, task: str) -> bool:
        """Run the agent loop for the given task, streaming events to callbacks.

        Steps:
          1. Gather desktop context (windows, spaces, frontmost app)
          2. Take an initial screenshot and describe it
          3. Build enriched task message
          4. Create a new ADK session
          5. Stream runner events → callbacks
          6. Commit session to memory for future tasks
        """
        runner = self._get_runner()
        session_id = memory.new_session_id()

        # Create ADK session
        await memory.session_service.create_session(
            app_name=memory.APP_NAME,
            user_id=memory.USER_ID,
            session_id=session_id,
        )

        # ── Step 1: gather desktop context ────────────────────────────────────
        ctx = yabai.get_desktop_context()
        ctx_lines = [f"Frontmost app: {ctx.get('frontmost_app', 'unknown')}"]
        screen_w, screen_h = ctx.get("screen_size", [1920, 1080])
        ctx_lines.append(f"Screen size: {screen_w}x{screen_h}")

        spaces = ctx.get("spaces", [])
        if spaces:
            focused_space = next((s for s in spaces if s.get("has-focus")), None)
            if focused_space:
                ctx_lines.append(f"Current space: {focused_space.get('index')} ({focused_space.get('label') or 'unlabeled'})")

        windows = ctx.get("windows", [])
        if windows:
            open_apps = sorted({w["app"] for w in windows if w.get("app")})
            ctx_lines.append(f"Open apps: {', '.join(open_apps[:12])}")

        # ── Step 2: initial screenshot ────────────────────────────────────────
        try:
            ss_path = macos.take_screenshot()
            from . import _vision
            screen_desc = _vision.describe_image(ss_path)
            ctx_lines.append(f"Current screen: {screen_desc}")
            self.callbacks.on_screenshot(screen_desc)
        except Exception as e:
            logger.warning(f"Initial screenshot/description failed: {e}")

        # ── Step 3: build enriched task message ───────────────────────────────
        context_block = "\n".join(ctx_lines)
        enriched_task = (
            f"[Desktop context]\n{context_block}\n\n"
            f"[Task]\n{task}"
        )

        user_message = genai_types.Content(
            role="user",
            parts=[genai_types.Part(text=enriched_task)],
        )

        # ── Step 4: stream runner events ──────────────────────────────────────
        success = False
        final_text = ""

        try:
            async for event in runner.run_async(
                user_id=memory.USER_ID,
                session_id=session_id,
                new_message=user_message,
            ):
                self._handle_event(event)

                # Capture final response
                if event.is_final_response():
                    if event.content and event.content.parts:
                        final_text = "".join(
                            p.text for p in event.content.parts if hasattr(p, "text") and p.text
                        ).strip()
                    success = True

        except KeyboardInterrupt:
            logger.info("Agent loop interrupted by user")
            success = False
        except Exception as e:
            logger.error(f"Runner error: {e}", exc_info=True)
            success = False

        if final_text:
            self.callbacks.on_final(final_text)

        # ── Step 5: commit session to memory ──────────────────────────────────
        await memory.commit_session(session_id)

        self.callbacks.on_done(success)
        return success

    def _handle_event(self, event: Any) -> None:
        """Route an ADK event to the appropriate callback."""
        author = getattr(event, "author", "Zener")
        content = getattr(event, "content", None)

        if not content:
            return

        for part in content.parts or []:
            # ── Text / thought block ──────────────────────────────────────────
            if hasattr(part, "text") and part.text:
                text = part.text.strip()
                if text and not event.is_final_response():
                    self.callbacks.on_thought(author, text)

            # ── Tool / sub-agent call ─────────────────────────────────────────
            if hasattr(part, "function_call") and part.function_call:
                fc = part.function_call
                self._step += 1
                tool_input: Dict[str, Any] = {}
                if hasattr(fc, "args") and fc.args:
                    try:
                        tool_input = dict(fc.args)
                    except Exception:
                        tool_input = {}
                self.callbacks.on_tool_call(self._step, fc.name, tool_input)

            # ── Tool result ───────────────────────────────────────────────────
            if hasattr(part, "function_response") and part.function_response:
                fr = part.function_response
                result_data: Any = {}
                if hasattr(fr, "response") and fr.response:
                    result_data = fr.response

                ok = True
                summary = ""
                if isinstance(result_data, dict):
                    ok = result_data.get("ok", True)
                    # Build a short summary from common fields
                    if "message" in result_data:
                        summary = str(result_data["message"])
                    elif "description" in result_data:
                        summary = str(result_data["description"])[:120]
                        self.callbacks.on_screenshot(summary)
                    elif "error" in result_data:
                        summary = str(result_data["error"])
                        ok = False
                    elif "stdout" in result_data:
                        summary = str(result_data["stdout"])[:120].strip() or "(no output)"
                    else:
                        try:
                            summary = json.dumps(result_data)[:120]
                        except Exception:
                            summary = str(result_data)[:120]

                self.callbacks.on_tool_result(self._step, fr.name, ok, summary)
