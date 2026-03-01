"""Exec tool."""

import asyncio
import logging
from typing import Any, Dict, Optional

from ..base import Tool, ToolResult

logger = logging.getLogger(__name__)


class ExecTool(Tool):
    """Execute a system command."""

    def __init__(
        self,
        allowed_commands: Optional[list] = None,
        timeout: int = 60,
        workdir: Optional[str] = None
    ):
        super().__init__(
            name="exec",
            description="Execute a system command. Use with caution.",
            parameters={
                "command": {
                    "type": "string",
                    "description": "The command to execute"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 60)",
                    "default": timeout
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory for the command",
                    "default": workdir or "."
                }
            },
            required_params=["command"]
        )
        self.allowed_commands = set(allowed_commands or [])
        self.default_timeout = timeout
        self.default_workdir = workdir

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        command = arguments.get("command", "")
        timeout = arguments.get("timeout", self.default_timeout)
        workdir = arguments.get("workdir", self.default_workdir) or self.default_workdir

        if not command:
            return ToolResult(output="", error="Command is required", exit_code=1)

        if self.allowed_commands:
            cmd_base = command.split()[0]
            if cmd_base not in self.allowed_commands:
                return ToolResult(
                    output="",
                    error=f"Command '{cmd_base}' is not allowed. Allowed: {', '.join(self.allowed_commands)}",
                    exit_code=1
                )

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
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
            logger.error(f"Exec error: {e}")
            return ToolResult(output="", error=str(e), exit_code=1)
