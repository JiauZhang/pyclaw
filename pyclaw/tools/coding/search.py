from __future__ import annotations

import re

from chatchat.tool import ToolResult, tool

from .paths import relative, resolve, workspace

_READ_LIMIT = 2000
_GREP_MAX = 200
_TEXT_ERROR = "Invalid UTF-8"

_SKIP_DIRS = {'.git', '.hg', '.svn', '__pycache__', 'node_modules', '.venv',
              'venv', 'dist', 'build', '.tox', '.idea', '.claude'}


def _skipped(path) -> bool:
    return any(part in _SKIP_DIRS for part in path.parts)


@tool(
    name='Read',
    description='Read a file from the workspace. Returns full content or '
                'a line range when offset/limit are given.',
    parameters={
        'type': 'object',
        'properties': {
            'file_path': {
                'type': 'string',
                'description': 'Path to the file, relative to the workspace.',
            },
            'offset': {
                'type': 'integer',
                'description': '0-based line to start from.',
            },
            'limit': {
                'type': 'integer',
                'description': 'Max number of lines to return.',
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
        return f'Error reading {file_path}: {e}'
    total = len(lines)
    start = offset or 0
    end = total if limit is None else min(start + limit, total)
    if start >= total and total:
        return f'Error: offset {start} beyond file length {total}'
    body = lines[start:end]
    numbered = '\n'.join(f'{i + start + 1}\t{ln}' for i, ln in enumerate(body))
    note = (f' ({total} lines, showing {start + 1}-{end})'
            if total != end - start else '')
    rel = relative(context.cwd, path)
    return ToolResult(text=f'{rel}{note}:\n{numbered}',
                      meta={'num_lines': total, 'path': rel})


@tool(
    name='Glob',
    description='Find files matching a shell-style glob pattern, '
                'relative to the workspace (supports ** for recursion).',
    parameters={
        'type': 'object',
        'properties': {
            'pattern': {
                'type': 'string',
                'description': 'Glob pattern, e.g. "**/*.py".',
            },
            'path': {
                'type': 'string',
                'description': 'Optional subdirectory to search within.',
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
    description='Search file contents with a regular expression. Each '
                'match is reported as path:line: text.',
    parameters={
        'type': 'object',
        'properties': {
            'pattern': {'type': 'string', 'description': 'Regex to match.'},
            'path': {'type': 'string',
                     'description': 'Optional subdirectory seed.'},
            'glob': {'type': 'string',
                     'description': 'Optional file glob to restrict to.'},
            'max_hits': {'type': 'integer',
                         'description': 'Max matches to return.',
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


@tool(
    name='LS',
    description='List a directory in the workspace (entries and sizes).',
    parameters={
        'type': 'object',
        'properties': {
            'path': {'type': 'string',
                     'description': 'Optional directory, relative to the '
                                    'workspace.'},
        },
        'required': [],
    },
)
def LS(context, path: str | None = None) -> str:
    base = resolve(context.cwd, path) if path else workspace(context.cwd)
    if base is None:
        return f'Error: path is outside the workspace: {path}'
    if not base.is_dir():
        return f'Error: not a directory: {path or "."}'
    entries = []
    for child in sorted(base.iterdir(), key=lambda c: (c.is_file(), c.name)):
        if child.is_dir():
            entries.append(f'{child.name}/')
        else:
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            entries.append(f'{child.name}  ({size} bytes)')
    label = relative(context.cwd, base) or '.'
    return ToolResult(text=f'{label}\n' + '\n'.join(entries),
                      meta={'num_entries': len(entries)})
