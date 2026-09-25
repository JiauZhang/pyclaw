from __future__ import annotations

import re

from chatchat.tool import ToolResult, tool
from pyclaw.tui.formatting import _plural
from pyclaw.tui.toolui import build_tool_ui, register, search_args

from .paths import relative, resolve, skipped, workspace
from .names import GREP


_GREP_MAX = 200

@tool(
    name=GREP,
    description='Searches file contents by regex, reporting each match as '
                'path:line: text. Patterns are Python re applied one line at a '
                'time, so . never spans a newline. A trailing "[exceeded N '
                'hits]" means the search stopped early, not that nothing else '
                'matched. node_modules, dist and VCS dirs are never searched.',
    read_only=True,
    get_path=lambda args: args.get('path'),
    parameters={
        'type': 'object',
        'properties': {
            'pattern': {'type': 'string', 'description': 'Regex to match.'},
            'path': {'type': 'string',
                     'description': 'Subdirectory to search instead of the '
                                    'whole workspace.'},
            'glob': {'type': 'string',
                     'description': 'Only search files whose path matches '
                                    'this glob.'},
            'max_hits': {'type': 'integer',
                         'description': 'Stop after this many matches.',
                         'default': _GREP_MAX},
        },
        'required': ['pattern'],
    },
)
async def Grep(context, pattern: str, path: str | None = None,
               glob: str | None = None,
               max_hits: int = _GREP_MAX) -> str:
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f'Error: invalid regex: {e}'
    base = resolve(context.cwd, path) if path else workspace(context.cwd)
    if base is None:
        return f'Error: path is outside the workspace: {path}'
    if not base.is_dir():
        return f'Error: not a directory: {path or "."}'
    files = (p for p in (base.glob(glob) if glob else base.rglob('*'))
             if p.is_file() and not skipped(p))
    hits = []
    files_seen = set()
    for p in files:
        try:
            text = p.read_text(encoding='utf-8')
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if not rx.search(line):
                continue
            rel = relative(context.cwd, p)
            hits.append(f'{rel}:{lineno}: {line}')
            files_seen.add(rel)
            if max_hits and len(hits) >= max_hits:
                return ToolResult(
                    text='\n'.join(hits) + f'\n[exceeded {max_hits} hits]',
                    meta={'num_files': len(files_seen),
                          'num_lines': len(hits)})
    if not hits:
        return f'No matches for "{pattern}".'
    return ToolResult(text='\n'.join(hits),
                      meta={'num_files': len(files_seen),
                            'num_lines': len(hits)})

def _search_args(name, tool_input, cwd):
    return search_args(tool_input, cwd)


def _search_kinds(name, tool_input):
    return {'search'}


def _grep_summary(name, output, width):
    hits = [r for r in str(output if output is not None else "")
            .split("\n") if r.strip()]
    if hits and ":" in hits[0]:
        return f"Found {_plural(len(hits), 'line')}"
    return f"Found {_plural(len(hits), 'file')}"

register(GREP, build_tool_ui(args=_search_args,
                               summary=_grep_summary,
                               kinds=_search_kinds))
