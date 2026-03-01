"""File operations tool."""

from pathlib import Path
from typing import Any, Dict, Optional

from ..base import Tool, ToolResult


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
                return ToolResult(output="", error=f"File not found: {path}", exit_code=1)

            if not path.is_file():
                return ToolResult(output="", error=f"Not a file: {path}", exit_code=1)

            if path.stat().st_size > self.max_size:
                return ToolResult(
                    output="",
                    error=f"File too large (max {self.max_size} bytes)",
                    exit_code=1
                )

            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

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

            path.parent.mkdir(parents=True, exist_ok=True)

            mode = "a" if append else "w"
            with open(path, mode, encoding="utf-8") as f:
                f.write(content)

            action = "appended to" if append else "wrote"
            return ToolResult(output=f"Successfully {action} {path}")

        except Exception as e:
            return ToolResult(output="", error=str(e), exit_code=1)
