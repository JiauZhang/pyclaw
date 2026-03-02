"""Bash tool - Safe shell command execution with pipe support."""

import asyncio
import logging
import shlex
from typing import Any, Dict, Optional

from ..base import Tool, ToolResult

logger = logging.getLogger(__name__)


class BashTool(Tool):
    """Execute bash commands with security restrictions.
    
    This tool supports shell features like pipes (|), but uses
    strict security measures to prevent command injection.
    """

    def __init__(
        self,
        allowed_commands: Optional[list] = None,
        timeout: int = 60,
        workdir: Optional[str] = None
    ):
        super().__init__(
            name="bash",
            description="Execute bash commands. Supports pipes and shell features like: ls -la | grep pattern",
            parameters={
                "command": {
                    "type": "string",
                    "description": "The bash command to execute. Supports pipes (|) but NOT other shell operators like ; && || < > $() ``"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": timeout
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory",
                    "default": workdir or "."
                }
            },
            required_params=["command"]
        )
        self.allowed_commands = set(allowed_commands or [])
        self.default_timeout = timeout
        self.default_workdir = workdir

    def _validate_command(self, command: str) -> tuple[bool, str]:
        """Validate command for security issues.
        
        Returns:
            (is_valid, error_message)
        """
        if not command or not command.strip():
            return False, "Command is required"
        
        command = command.strip()
        
        # Block dangerous shell operators
        dangerous_patterns = [
            ';',           # Command separator
            '&&',          # AND operator  
            '||',          # OR operator
            '`',           # Command substitution (backticks)
            '$(',          # Command substitution
            '${',          # Parameter expansion
            '<',           # Input redirection
            '>',           # Output redirection
            '>>',          # Append redirection
            '&',           # Background (at end)
        ]
        
        for pattern in dangerous_patterns:
            if pattern in command:
                return False, f"Shell operator '{pattern}' is not allowed for security reasons"
        
        # Only allow pipe operator |
        # Split by pipes to check each command segment
        segments = command.split('|')
        
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
                
            # Get the base command (first word)
            try:
                parts = shlex.split(segment)
                if not parts:
                    continue
                base_cmd = parts[0]
            except ValueError as e:
                return False, f"Invalid command syntax: {e}"
            
            # Check if command is in allowed list
            if self.allowed_commands and base_cmd not in self.allowed_commands:
                return False, f"Command '{base_cmd}' is not allowed. Allowed: {', '.join(sorted(self.allowed_commands))}"
        
        return True, ""

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        command = arguments.get("command", "")
        timeout = arguments.get("timeout", self.default_timeout)
        workdir = arguments.get("workdir", self.default_workdir) or self.default_workdir

        # Validate command
        is_valid, error_msg = self._validate_command(command)
        if not is_valid:
            return ToolResult(output="", error=error_msg, exit_code=1)

        try:
            # Use bash with restricted options
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                executable="/bin/bash",
                # Limit environment to prevent info leakage
                env={
                    'PATH': '/usr/local/bin:/usr/bin:/bin',
                    'HOME': '/tmp',
                    'LANG': 'C.UTF-8',
                }
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                try:
                    await asyncio.wait_for(process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    process.terminate()
                return ToolResult(
                    output="",
                    error=f"Command timed out after {timeout} seconds",
                    exit_code=124
                )

            output = stdout.decode("utf-8", errors="replace")
            error = stderr.decode("utf-8", errors="replace") if stderr else None

            return ToolResult(
                output=output,
                error=error if error else None,
                exit_code=process.returncode or 0
            )

        except Exception as e:
            logger.error(f"Bash execution error: {e}")
            return ToolResult(output="", error=str(e), exit_code=1)
