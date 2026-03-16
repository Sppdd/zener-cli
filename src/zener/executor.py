import logging
from pathlib import Path
from typing import Optional, Callable
from dataclasses import dataclass
from enum import Enum

from . import macos, agent

logger = logging.getLogger(__name__)


class ExecutionStatus(Enum):
    SUCCESS = "success"
    FAILED = "failed"
    CONFIRMATION_REQUIRED = "confirmation_required"


DANGEROUS_COMMANDS = [
    "rm -rf",
    "rm -r /",
    "dd if=",
    "mkfs.",
    "shutdown",
    "reboot",
    "chmod -x",
    "chown -R",
    "> /dev/",
    "curl | sh",
    "wget | sh",
    "chmod 777",
]


@dataclass
class ExecutionResult:
    status: ExecutionStatus
    message: str
    data: Optional[dict] = None


class ActionExecutor:
    def __init__(self, confirm_callback: Optional[Callable[[str], bool]] = None):
        self.confirm_callback = confirm_callback
    
    def execute(self, action: agent.Action) -> ExecutionResult:
        """Execute a single action and return the result."""
        
        try:
            if action.type == agent.ActionType.OPEN_APP:
                return self._execute_open_app(action)
            elif action.type == agent.ActionType.CLICK:
                return self._execute_click(action)
            elif action.type == agent.ActionType.TYPE:
                return self._execute_type(action)
            elif action.type == agent.ActionType.PRESS_KEY:
                return self._execute_press_key(action)
            elif action.type == agent.ActionType.OPEN_URL:
                return self._execute_open_url(action)
            elif action.type == agent.ActionType.RUN_SHELL:
                return self._execute_run_shell(action)
            elif action.type == agent.ActionType.READ_FILE:
                return self._execute_read_file(action)
            elif action.type == agent.ActionType.WRITE_FILE:
                return self._execute_write_file(action)
            elif action.type == agent.ActionType.LIST_DIR:
                return self._execute_list_dir(action)
            elif action.type == agent.ActionType.SCREENSHOT:
                return self._execute_screenshot(action)
            elif action.type == agent.ActionType.DONE:
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    message="Task completed",
                    data={},
                )
            else:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    message=f"Unknown action type: {action.type}",
                )
        except Exception as e:
            logger.exception(f"Error executing action: {action}")
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Error: {str(e)}",
            )
    
    def _execute_open_app(self, action: agent.Action) -> ExecutionResult:
        app_name = action.params.get("name", "")
        success = macos.open_application(app_name)
        
        if success:
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Opened {app_name}",
                data={"app": app_name},
            )
        else:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Failed to open {app_name}",
            )
    
    def _execute_click(self, action: agent.Action) -> ExecutionResult:
        x = action.params.get("x", 0)
        y = action.params.get("y", 0)
        
        success = macos.click_at(x, y)
        
        if success:
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Clicked at ({x}, {y})",
                data={"x": x, "y": y},
            )
        else:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Failed to click at ({x}, {y})",
            )
    
    def _execute_type(self, action: agent.Action) -> ExecutionResult:
        text = action.params.get("text", "")
        
        success = macos.type_text(text)
        
        if success:
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}",
                data={"text": text},
            )
        else:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message="Failed to type text",
            )
    
    def _execute_press_key(self, action: agent.Action) -> ExecutionResult:
        key = action.params.get("key", "")
        
        success = macos.press_key(key)
        
        if success:
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Pressed key: {key}",
                data={"key": key},
            )
        else:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Failed to press key: {key}",
            )
    
    def _execute_open_url(self, action: agent.Action) -> ExecutionResult:
        url = action.params.get("url", "")
        
        success = macos.open_url(url)
        
        if success:
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Opened {url}",
                data={"url": url},
            )
        else:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Failed to open {url}",
            )
    
    def _execute_run_shell(self, action: agent.Action) -> ExecutionResult:
        command = action.params.get("command", "")
        timeout = action.params.get("timeout", 30)
        
        if not command:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message="No command provided",
            )
        
        is_dangerous = any(dangerous in command for dangerous in DANGEROUS_COMMANDS)
        
        if is_dangerous:
            if self.confirm_callback:
                confirm_msg = f"⚠️  This command may be dangerous: {command[:100]}..."
                if not self.confirm_callback(confirm_msg):
                    return ExecutionResult(
                        status=ExecutionStatus.FAILED,
                        message="Command cancelled by user",
                    )
            else:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    message=f"Command blocked (dangerous): {command[:50]}...",
                )
        
        returncode, stdout, stderr = macos.run_shell_command(command, timeout)
        
        if returncode == 0:
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Command executed successfully",
                data={"returncode": returncode, "stdout": stdout, "stderr": stderr},
            )
        else:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Command failed with code {returncode}",
                data={"returncode": returncode, "stdout": stdout, "stderr": stderr},
            )
    
    def _execute_read_file(self, action: agent.Action) -> ExecutionResult:
        file_path = Path(action.params.get("path", ""))
        
        if not file_path.exists():
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"File not found: {file_path}",
            )
        
        try:
            content = macos.read_file(file_path)
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Read {len(content)} characters from {file_path.name}",
                data={"path": str(file_path), "content": content},
            )
        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Error reading file: {str(e)}",
            )
    
    def _execute_write_file(self, action: agent.Action) -> ExecutionResult:
        file_path = Path(action.params.get("path", ""))
        content = action.params.get("content", "")
        
        try:
            macos.write_file(file_path, content)
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Wrote {len(content)} characters to {file_path.name}",
                data={"path": str(file_path), "size": len(content)},
            )
        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Error writing file: {str(e)}",
            )
    
    def _execute_list_dir(self, action: agent.Action) -> ExecutionResult:
        dir_path = Path(action.params.get("path", "."))
        
        try:
            contents = macos.list_directory(dir_path)
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Listed {len(contents)} items in {dir_path}",
                data={"path": str(dir_path), "contents": contents},
            )
        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Error listing directory: {str(e)}",
            )
    
    def _execute_screenshot(self, action: agent.Action) -> ExecutionResult:
        try:
            screenshot_path = macos.take_screenshot()
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                message=f"Screenshot captured",
                data={"path": str(screenshot_path)},
            )
        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                message=f"Error taking screenshot: {str(e)}",
            )


def execute_action(action: agent.Action, confirm_callback: Optional[Callable[[str], bool]] = None) -> ExecutionResult:
    """Convenience function to execute a single action."""
    executor = ActionExecutor(confirm_callback)
    return executor.execute(action)
