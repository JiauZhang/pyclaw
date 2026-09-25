from __future__ import annotations

import re

from chatchat.tool import ToolResult, tool

from pyclaw.tui.formatting import _plural
from pyclaw.tui.toolui import (build_tool_ui, is_memory_path, path_args,
                               register, search_args)

from .paths import relative, resolve, workspace

_READ_LIMIT = 2000
_GREP_MAX = 200
_TEXT_ERROR = "Invalid UTF-8"

_SKIP_DIRS = {'.git', '.hg', '.svn', '__pycache__', 'node_modules', '.venv',
              'venv', 'dist', 'build', '.tox', '.idea', '.pyclaw'}


def _skipped(path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


@tool(
    name='Read',
    description='Reads a workspace file and returns its lines as text, each '
                'preceded by its 1-based line number and a tab. That prefix is '
                'not file content: never carry it into an Edit old_string. '
                f'Files longer than {_READ_LIMIT} lines return the first page '
                'with a note; binary files return an encoding error.',
    read_only=True,
    get_path=lambda args: args.get('file_path'),
    parameters={
        'type': 'object',
        'properties': {
            'file_path': {
                'type': 'string',
                'description': 'Path to the file, relative to the workspace.',
            },
            'offset': {
                'type': 'integer',
                'description': 'Line number to start from, counting the '
                               'numbers the output prints.',
            },
            'limit': {
                'type': 'integer',
                'description': 'Max lines to return.',
                'default': _READ_LIMIT,
            },
        },
        'required': ['file_path'],
    },
)
def Read(context, file_path: str, offset: int | None = None,
         limit: int | None = None) -> str:
    path = resolve(context.cwd, file_path)
    if path is None:
        return f'Error: path is outside the workspace: {file_path}'
    if not path.is_file():
        return f'Error: file not found: {file_path}'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except UnicodeDecodeError:
        return f'Error: {_TEXT_ERROR} (not a text file): {file_path}'
    except OSError as e:
        return f'Read of {file_path} failed: {e}'
    total = len(lines)
    start = max((offset or 1) - 1, 0)
    end = min(start + (limit or _READ_LIMIT), total)
    if start >= total and total:
        return f'Error: offset {offset} beyond file length {total}'
    body = lines[start:end]
    numbered = '\n'.join(f'{i + start + 1}\t{ln}' for i, ln in enumerate(body))
    note = (f' ({total} lines, showing {start + 1}-{end})'
            if total != len(body) else '')
    rel = relative(context.cwd, path)
    return ToolResult(text=f'{rel}{note}:\n{numbered}',
                      meta={'num_lines': total, 'path': rel})


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
                     if p.is_file() and not _skipped(p))
    if not matches:
        return f'No files match "{pattern}".'
    return ToolResult(text='\n'.join(matches), meta={'num_files': len(matches)})


@tool(
    name='Grep',
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
             if p.is_file() and not _skipped(p))
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


def _input_path(tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    return data.get('file_path') or data.get('path') or ''


def _read_args(name, tool_input, cwd):
    return path_args(tool_input, cwd)


def _read_summary(name, output, width):
    rows = str(output if output is not None else "").split("\n")
    body = rows[1:] if rows and rows[0].endswith(":") else rows
    return f"Read {_plural(len([r for r in body if r.strip()]), 'line')}"


def _read_kinds(name, tool_input):
    return {'memory_read' if is_memory_path(_input_path(tool_input))
            else 'read'}


register('Read', build_tool_ui(args=_read_args,
                               summary=_read_summary, kinds=_read_kinds))


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


def _glob_summary(name, output, width):
    hits = [r for r in str(output if output is not None else "")
            .split("\n") if r.strip()]
    return f"Found {_plural(len(hits), 'file')}"


register('Grep', build_tool_ui(args=_search_args, summary=_grep_summary,
                               kinds=_search_kinds))
register('Glob', build_tool_ui(args=_search_args, summary=_glob_summary,
                               kinds=_search_kinds))
