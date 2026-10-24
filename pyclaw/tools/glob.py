from __future__ import annotations

from chatchat.tool import ToolResult, tool

from .paths import relative, resolve, skipped, workspace
from .names import GLOB


@tool(
    name=GLOB,
    description="""Returns the workspace-relative paths of matching files, one per line, sorted; directories are never listed.
A bare pattern matches one level only, so "*.py" finds the search root and "**/*.py" reaches nested files.
Vendored and VCS dirs are skipped.""",
    read_only=True,
    get_path=lambda args: args.get("path"),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": 'Glob pattern to match files against, e.g. "**/*.py".',
            },
            "path": {
                "type": "string",
                "description": "Directory to search in; defaults to the whole workspace.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
)
def Glob(context, pattern: str, path: str | None = None) -> str:
    base = resolve(context.cwd, path, context.extra_dirs) if path else workspace(context.cwd)
    if base is None:
        return f"Error: path is outside the workspace: {path}"
    if not base.is_dir():
        return f"Error: not a directory: {pattern}"
    matches = sorted(relative(context.cwd, p) for p in base.glob(pattern) if p.is_file() and not skipped(p))
    if not matches:
        return f'No files match "{pattern}".'
    return ToolResult(text="\n".join(matches), meta={"num_files": len(matches)})
