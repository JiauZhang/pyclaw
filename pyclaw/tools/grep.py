from __future__ import annotations

import re

from chatchat.tool import ToolResult, tool

from .paths import relative, resolve, skipped, workspace
from .names import GREP


_GREP_MAX = 200


@tool(
    name=GREP,
    description="""Searches file contents by regex, reporting each match as path:line: text.
Patterns are Python re applied one line at a time, so . never spans a newline.
A trailing "[exceeded N hits]" means the search stopped early, not that nothing else matched.
node_modules, dist and VCS dirs are never searched.""",
    read_only=True,
    get_path=lambda args: args.get("path"),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regex to match, one line at a time."},
            "path": {"type": "string", "description": "Directory to search in; defaults to the whole workspace."},
            "glob": {"type": "string", "description": "Only search files whose path matches this glob."},
            "max_hits": {
                "type": "integer",
                "minimum": 0,
                "default": _GREP_MAX,
                "description": "Stop after this many matches; 0 means no limit.",
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
)
async def Grep(context, pattern: str, path: str | None = None, glob: str | None = None, max_hits: int = _GREP_MAX) -> str:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"Error: invalid regex: {e}"
    base = resolve(context.cwd, path, context.extra_dirs) if path else workspace(context.cwd)
    if base is None:
        return f"Error: path is outside the workspace: {path}"
    if not base.is_dir():
        return f"Error: not a directory: {path or '.'}"
    files = (p for p in (base.glob(glob) if glob else base.rglob("*")) if p.is_file() and not skipped(p))
    hits = []
    files_seen = set()
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if not rx.search(line):
                continue
            rel = relative(context.cwd, p)
            hits.append(f"{rel}:{lineno}: {line}")
            files_seen.add(rel)
            if max_hits and len(hits) >= max_hits:
                return ToolResult(
                    text="\n".join(hits) + f"\n[exceeded {max_hits} hits]",
                    meta={"num_files": len(files_seen), "num_lines": len(hits)},
                )
    if not hits:
        return f'No matches for "{pattern}".'
    return ToolResult(text="\n".join(hits), meta={"num_files": len(files_seen), "num_lines": len(hits)})
