from __future__ import annotations

import base64

from chatchat.tool import ToolResult, tool


from .paths import relative, resolve
from .names import READ


_READ_LIMIT = 2000
_TEXT_ERROR = "Invalid UTF-8"
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
_IMAGE_MAX_BYTES = 10 * 1024 * 1024


@tool(
    name=READ,
    description=f"""Reads a workspace file and returns its lines as text, each preceded by its 1-based line number and a tab.
That prefix is not file content: never carry it into an Edit old_string.
Files longer than {_READ_LIMIT} lines return the first page with a note; image files (png, jpeg, gif, webp) return the picture
itself; binary files return an encoding error.""",
    read_only=True,
    get_path=lambda args: args.get("file_path"),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path to the file, relative to the workspace.",
            },
            "offset": {
                "type": "integer",
                "minimum": 1,
                "description": "Line number to start reading from, 1-based. Only needed for a file too large to read at once.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "default": _READ_LIMIT,
                "description": "Max lines to return.",
            },
        },
        "required": ["file_path"],
        "additionalProperties": False,
    },
)
def Read(context, file_path: str, offset: int | None = None, limit: int | None = None) -> str:
    path = resolve(context.cwd, file_path, context.extra_dirs)
    if path is None:
        return f"Error: path is outside the workspace: {file_path}"
    if not path.is_file():
        return f"Error: file not found: {file_path}"
    media = _IMAGE_TYPES.get(path.suffix.lower())
    if media is not None:
        return _read_image(context, path, file_path, media)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return f"Error: {_TEXT_ERROR} (not a text file): {file_path}"
    except OSError as e:
        return f"Read of {file_path} failed: {e}"
    total = len(lines)
    start = max((offset or 1) - 1, 0)
    end = min(start + (limit or _READ_LIMIT), total)
    if start >= total and total:
        return f"Error: offset {offset} beyond file length {total}"
    body = lines[start:end]
    numbered = "\n".join(f"{i + start + 1}\t{ln}" for i, ln in enumerate(body))
    note = f" ({total} lines, showing {start + 1}-{end})" if total != len(body) else ""
    rel = relative(context.cwd, path)
    return ToolResult(text=f"{rel}{note}:\n{numbered}", meta={"num_lines": total, "path": rel})


def _read_image(context, path, file_path: str, media: str):
    try:
        data = path.read_bytes()
    except OSError as e:
        return f"Read of {file_path} failed: {e}"
    if len(data) > _IMAGE_MAX_BYTES:
        return f"Error: image larger than 10 MB: {file_path}"
    rel = relative(context.cwd, path)
    return ToolResult(
        text=f"{rel}: image ({media}, {len(data)} bytes)",
        blocks=[
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": base64.b64encode(data).decode("ascii")}}
        ],
        meta={"path": rel},
    )
