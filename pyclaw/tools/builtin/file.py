"""File operations tool with path traversal protection."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from ..base import Tool, ToolResult


def _resolve_path(path_str: str, base_dir: Optional[Path]) -> tuple[Optional[Path], Optional[str]]:
    """Resolve and validate a file path.
    
    Args:
        path_str: The path string provided by user
        base_dir: The base directory for sandboxing (None = no sandbox)
        
    Returns:
        (resolved_path, error_message) - if error_message is not None, path is invalid
    """
    try:
        # Normalize the path string first
        path_str = os.path.normpath(path_str)
        
        # Reject paths with null bytes
        if '\x00' in path_str:
            return None, "Invalid path: contains null bytes"
        
        path = Path(path_str)
        
        # If base_dir is set, enforce sandbox
        if base_dir:
            # Resolve base_dir to absolute path
            base_dir_resolved = base_dir.resolve().absolute()
            
            # Handle relative paths - join with base_dir first
            if not path.is_absolute():
                path = base_dir / path
            
            # Resolve the final path
            try:
                path_resolved = path.resolve().absolute()
            except (OSError, ValueError) as e:
                return None, f"Invalid path: {e}"
            
            # Security check: ensure resolved path is within base_dir
            # Use os.path.commonpath for reliable comparison
            try:
                common = os.path.commonpath([str(path_resolved), str(base_dir_resolved)])
                if common != str(base_dir_resolved):
                    return None, f"Access denied: path outside allowed directory"
            except ValueError:
                # Different drives on Windows
                return None, f"Access denied: path outside allowed directory"
            
            return path_resolved, None
        else:
            # No sandbox - just resolve to absolute
            return path.resolve().absolute(), None
            
    except Exception as e:
        return None, f"Path resolution error: {e}"


class ReadFileTool(Tool):
    """Read file contents with path traversal protection."""

    def __init__(self, base_dir: Optional[str] = None, max_size: int = 1024 * 1024):
        super().__init__(
            name="read_file",
            description="Read the contents of a file.",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path to the file to read (relative to workspace or absolute)"
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

        # Validate offset and limit
        if not isinstance(offset, int) or offset < 0:
            return ToolResult(output="", error="offset must be a non-negative integer", exit_code=1)
        if not isinstance(limit, int) or limit < 1 or limit > 10000:
            return ToolResult(output="", error="limit must be between 1 and 10000", exit_code=1)

        # Resolve and validate path
        path, error = _resolve_path(path_str, self.base_dir)
        if error:
            return ToolResult(output="", error=error, exit_code=1)

        try:
            if not path.exists():
                return ToolResult(output="", error=f"File not found: {path_str}", exit_code=1)

            if not path.is_file():
                return ToolResult(output="", error=f"Not a file: {path_str}", exit_code=1)

            # Check file size
            try:
                file_size = path.stat().st_size
                if file_size > self.max_size:
                    return ToolResult(
                        output="",
                        error=f"File too large ({file_size} bytes, max {self.max_size} bytes)",
                        exit_code=1
                    )
            except (OSError, IOError) as e:
                return ToolResult(output="", error=f"Cannot access file: {e}", exit_code=1)

            # Read file with proper error handling
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except (OSError, IOError, PermissionError) as e:
                return ToolResult(output="", error=f"Cannot read file: {e}", exit_code=1)

            selected_lines = lines[offset:offset + limit]

            output = "".join(selected_lines)
            info = f"Lines {offset}-{offset + len(selected_lines)} of {len(lines)}"

            return ToolResult(output=f"# {info}\n{output}")

        except Exception as e:
            return ToolResult(output="", error=f"Unexpected error: {e}", exit_code=1)


class WriteFileTool(Tool):
    """Write content to a file with path traversal protection."""

    def __init__(self, base_dir: Optional[str] = None, max_size: int = 10 * 1024 * 1024):
        super().__init__(
            name="write_file",
            description="Write content to a file. Creates the file if it doesn't exist.",
            parameters={
                "path": {
                    "type": "string",
                    "description": "Path to the file to write (relative to workspace or absolute)"
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
        self.max_size = max_size

    async def execute(self, arguments: Dict[str, Any], context: Any) -> ToolResult:
        path_str = arguments.get("path", "")
        content = arguments.get("content", "")
        append = arguments.get("append", False)

        # Check content size
        content_bytes = content.encode('utf-8')
        if len(content_bytes) > self.max_size:
            return ToolResult(
                output="",
                error=f"Content too large ({len(content_bytes)} bytes, max {self.max_size} bytes)",
                exit_code=1
            )

        # Resolve and validate path
        path, error = _resolve_path(path_str, self.base_dir)
        if error:
            return ToolResult(output="", error=error, exit_code=1)

        try:
            # Create parent directories if needed
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except (OSError, PermissionError) as e:
                return ToolResult(output="", error=f"Cannot create directory: {e}", exit_code=1)

            # Check if parent is a directory and writable
            if not path.parent.is_dir():
                return ToolResult(output="", error=f"Parent path is not a directory: {path.parent}", exit_code=1)

            # Write file
            mode = "a" if append else "w"
            try:
                with open(path, mode, encoding="utf-8") as f:
                    f.write(content)
            except (OSError, IOError, PermissionError) as e:
                return ToolResult(output="", error=f"Cannot write file: {e}", exit_code=1)

            action = "appended to" if append else "wrote"
            return ToolResult(output=f"Successfully {action} {path_str}")

        except Exception as e:
            return ToolResult(output="", error=f"Unexpected error: {e}", exit_code=1)
