"""Python tool."""

import asyncio
import logging
import os
import tempfile
from typing import Any, Dict

from ..base import Tool, ToolResult

logger = logging.getLogger(__name__)


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
            with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
                f.write(code)
                temp_file = f.name

            try:
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
                try:
                    os.unlink(temp_file)
                except:
                    pass

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
