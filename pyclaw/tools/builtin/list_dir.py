"""List directory tool."""

from pathlib import Path
from typing import Any, Dict, Optional

from ..base import Tool, ToolResult


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
