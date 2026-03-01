"""Built-in tools for pyclaw."""

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .base import Tool, ToolResult

logger = logging.getLogger(__name__)


class EchoTool(Tool):
    """Echo back the input text."""

    def __init__(self):
        super().__init__(
            name="echo",
            description="Echo back the provided text. Useful for testing and confirmation.",
            parameters={
                "text": {
                    "type": "string",
                    "description": "The text to echo back"
                }
            },
            required_params=["text"]
        )

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        text = arguments.get("text", "")
        return ToolResult(output=text)


class TimeTool(Tool):
    """Get current time."""

    def __init__(self):
        super().__init__(
            name="time",
            description="Get the current time in various formats.",
            parameters={
                "format": {
                    "type": "string",
                    "description": "Time format (iso, human, or custom strftime format)",
                    "enum": ["iso", "human", "time_only"],
                    "default": "human"
                },
                "timezone": {
                    "type": "string",
                    "description": "Timezone (default: local)",
                    "default": "local"
                }
            },
            required_params=[]
        )

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        format_type = arguments.get("format", "human")
        now = datetime.now()

        if format_type == "iso":
            output = now.isoformat()
        elif format_type == "time_only":
            output = now.strftime("%H:%M:%S")
        elif format_type == "human":
            output = now.strftime("%Y-%m-%d %H:%M:%S")
        else:
            # Custom format
            try:
                output = now.strftime(format_type)
            except Exception as e:
                return ToolResult(output="", error=f"Invalid format: {e}", exit_code=1)

        return ToolResult(output=output)


class DateTool(Tool):
    """Get current date."""

    def __init__(self):
        super().__init__(
            name="date",
            description="Get the current date in various formats.",
            parameters={
                "format": {
                    "type": "string",
                    "description": "Date format (iso, human, weekday, or custom strftime format)",
                    "enum": ["iso", "human", "weekday"],
                    "default": "human"
                }
            },
            required_params=[]
        )

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        format_type = arguments.get("format", "human")
        now = datetime.now()

        if format_type == "iso":
            output = now.date().isoformat()
        elif format_type == "human":
            output = now.strftime("%Y-%m-%d")
        elif format_type == "weekday":
            output = now.strftime("%A")
        else:
            try:
                output = now.strftime(format_type)
            except Exception as e:
                return ToolResult(output="", error=f"Invalid format: {e}", exit_code=1)

        return ToolResult(output=output)


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

        # Security check for allowed commands
        if self.allowed_commands:
            cmd_base = command.split()[0]
            if cmd_base not in self.allowed_commands:
                return ToolResult(
                    output="",
                    error=f"Command '{cmd_base}' is not allowed. Allowed: {', '.join(self.allowed_commands)}",
                    exit_code=1
                )

        try:
            # Run command
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


class ReadFileTool(Tool):
    """Read file contents."""

    def __init__(self, base_dir: Optional[str] = None, max_size: int = 1024 * 1024):
        super().__init__(
            name="read_file",
            description="Read the contents of a file.",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path to the file to read"
                },
                "offset": {
                    "type": "integer",
                    "description": "Line offset to start reading from (0-indexed)",
                    "default": 0
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of lines to read",
                    "default": 1000
                }
            },
            required_params=["path"]
        )
        self.base_dir = Path(base_dir) if base_dir else None
        self.max_size = max_size

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        path_str = arguments.get("path", "")
        offset = arguments.get("offset", 0)
        limit = arguments.get("limit", 1000)

        try:
            # Resolve path
            path = Path(path_str)
            if self.base_dir:
                # Security: prevent path traversal
                path = self.base_dir / path
                path = path.resolve()
                if not str(path).startswith(str(self.base_dir.resolve())):
                    return ToolResult(
                        output="",
                        error="Access denied: path outside allowed directory",
                        exit_code=1
                    )

            if not path.exists():
                return ToolResult(output="", error=f"File not found: {path}", exit_code=1)

            if not path.is_file():
                return ToolResult(output="", error=f"Not a file: {path}", exit_code=1)

            # Check file size
            if path.stat().st_size > self.max_size:
                return ToolResult(
                    output="",
                    error=f"File too large (max {self.max_size} bytes)",
                    exit_code=1
                )

            # Read file
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            # Apply offset and limit
            selected_lines = lines[offset:offset + limit]

            output = "".join(selected_lines)
            info = f"Lines {offset}-{offset + len(selected_lines)} of {len(lines)}"

            return ToolResult(output=f"# {info}\n{output}")

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)


class WriteFileTool(Tool):
    """Write content to a file."""

    def __init__(self, base_dir: Optional[str] = None):
        super().__init__(
            name="write_file",
            description="Write content to a file. Creates the file if it doesn't exist.",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path to the file to write"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                },
                "append": {
                    "type": "boolean",
                    "description": "Append to file instead of overwriting",
                    "default": False
                }
            },
            required_params=["path", "content"]
        )
        self.base_dir = Path(base_dir) if base_dir else None

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        path_str = arguments.get("path", "")
        content = arguments.get("content", "")
        append = arguments.get("append", False)

        try:
            # Resolve path
            path = Path(path_str)
            if self.base_dir:
                path = self.base_dir / path
                path = path.resolve()
                if not str(path).startswith(str(self.base_dir.resolve())):
                    return ToolResult(
                        output="",
                        error="Access denied: path outside allowed directory",
                        exit_code=1
                    )

            # Create parent directories
            path.parent.mkdir(parents=True, exist_ok=True)

            # Write file
            mode = "a" if append else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)

            action = "appended to" if append else "wrote"
            return ToolResult(output=f"Successfully {action} {path}")

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)


class ListDirTool(Tool):
    """List directory contents."""

    def __init__(self, base_dir: Optional[str] = None):
        super().__init__(
            name="list_dir",
            description="List the contents of a directory.",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path to the directory to list",
                    "default": "."
                },
                "show_hidden": {
                    "type": "boolean",
                    "description": "Show hidden files (starting with .)",
                    "default": False
                }
            },
            required_params=[]
        )
        self.base_dir = Path(base_dir) if base_dir else None

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        path_str = arguments.get("path", ".")
        show_hidden = arguments.get("show_hidden", False)

        try:
            # Resolve path
            path = Path(path_str)
            if self.base_dir:
                path = self.base_dir / path
                path = path.resolve()
                if not str(path).startswith(str(self.base_dir.resolve())):
                    return ToolResult(
                        output="",
                        error="Access denied: path outside allowed directory",
                        exit_code=1
                    )

            if not path.exists():
                return ToolResult(output="", error=f"Directory not found: {path}", exit_code=1)

            if not path.is_dir():
                return ToolResult(output="", error=f"Not a directory: {path}", exit_code=1)

            # List contents
            entries = []
            for entry in path.iterdir():
                if not show_hidden and entry.name.startswith("."):
                    continue

                entry_type = "📁" if entry.is_dir() else "📄"
                size = ""
                if entry.is_file():
                    try:
                        size_bytes = entry.stat().st_size
                        if size_bytes < 1024:
                            size = f" {size_bytes}B"
                        elif size_bytes < 1024 * 1024:
                            size = f" {size_bytes // 1024}KB"
                        else:
                            size = f" {size_bytes // (1024 * 1024)}MB"
                    except:
                        pass

                entries.append(f"{entry_type} {entry.name}{size}")

            if not entries:
                return ToolResult(output="(empty directory)")

            return ToolResult(output="\n".join(sorted(entries)))

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)


class PythonTool(Tool):
    """Execute Python code."""

    def __init__(self, timeout: int = 30):
        super().__init__(
            name="python",
            description="Execute Python code and return the result.",
            parameters={
                "code": {
                    "type": "string",
                    "description": "Python code to execute"
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "default": timeout
                }
            },
            required_params=["code"]
        )
        self.default_timeout = timeout

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        code = arguments.get("code", "")
        timeout = arguments.get("timeout", self.default_timeout)

        if not code:
            return ToolResult(output="", error="Code is required", exit_code=1)

        try:
            # Create a temporary file for the code
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                temp_file = f.name

            try:
                # Run the Python code
                process = await asyncio.create_subprocess_exec(
                    "python3",
                    temp_file,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
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
                        error=f"Code execution timed out after {timeout} seconds",
                        exit_code=124
                    )

                output = stdout.decode("utf-8", errors="replace")
                error = stderr.decode("utf-8", errors="replace") if stderr else None

                return ToolResult(
                    output=output,
                    error=error if error else None,
                    exit_code=process.returncode or 0
                )

            finally:
                # Clean up temp file
                try:
                    os.unlink(temp_file)
                except:
                    pass

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)


class BashTool(Tool):
    """Execute bash commands."""

    def __init__(
        self,
        allowed_commands: Optional[list] = None,
        timeout: int = 60,
        workdir: Optional[str] = None
    ):
        super().__init__(
            name="bash",
            description="Execute bash commands. Supports pipes and shell features.",
            parameters={
                "command": {
                    "type": "string",
                    "description": "The bash command to execute"
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

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        command = arguments.get("command", "")
        timeout = arguments.get("timeout", self.default_timeout)
        workdir = arguments.get("workdir", self.default_workdir) or self.default_workdir

        if not command:
            return ToolResult(output="", error="Command is required", exit_code=1)

        # Security check
        if self.allowed_commands:
            # Simple check - extract first command
            first_cmd = command.split("|")[0].strip().split()[0]
            if first_cmd not in self.allowed_commands:
                return ToolResult(
                    output="",
                    error=f"Command '{first_cmd}' not allowed",
                    exit_code=1
                )

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir,
                executable="/bin/bash"
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
            return ToolResult(output="", error=str(e), exit_code=1)
