from __future__ import annotations

from chatchat.tool import ToolResult, tool
from pyclaw.tui.formatting import _plural
from pyclaw.tui.toolui import build_tool_ui, register, search_args

from .paths import relative, resolve, skipped, workspace


@tool(
    name='Glob',
    description='Returns the workspace-relative paths of matching files, one '
                'per line, sorted; directories are never listed. A bare '
                'pattern matches one level only, so "*.py" finds the search '
                'root and "**/*.py" reaches nested files. Vendored and VCS '
                'dirs are skipped.',
    read_only=True,
    get_path=lambda args: args.get('path'),
    parameters={
        'type': 'object',
        'properties': {
            'pattern': {
                'type': 'string',
                'description': 'Glob pattern.',
            },
            'path': {
                'type': 'string',
                'description': 'Subdirectory to search instead of the whole '
                               'workspace.',
            },
        },
        'required': ['pattern'],
    },
)
def Glob(context, pattern: str, path: str | None = None) -> str:
    base = resolve(context.cwd, path) if path else workspace(context.cwd)
    if base is None:
        return f'Error: path is outside the workspace: {path}'
    if not base.is_dir():
        return f'Error: not a directory: {pattern}'
    matches = sorted(relative(context.cwd, p) for p in base.glob(pattern)
                     if p.is_file() and not skipped(p))
    if not matches:
        return f'No files match "{pattern}".'
    return ToolResult(text='\n'.join(matches), meta={'num_files': len(matches)})

def _search_args(name, tool_input, cwd):
    return search_args(tool_input, cwd)


def _search_kinds(name, tool_input):
    return {'search'}


def _glob_summary(name, output, width):
    hits = [r for r in str(output if output is not None else "")
            .split("\n") if r.strip()]
    return f"Found {_plural(len(hits), 'file')}"

register('Glob', build_tool_ui(args=_search_args,
                               summary=_glob_summary,
                               kinds=_search_kinds))
