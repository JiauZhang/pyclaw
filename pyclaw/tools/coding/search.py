from __future__ import annotations

import re
from pathlib import Path

from chatchat.tool import ToolResult, tool

from .paths import resolve

_READ_LIMIT = 2000
_GREP_MAX = 200
_TEXT_ERROR = "Invalid UTF-8"

_SKIP_DIRS = {'.git', '.hg', '.svn', '__pycache__', 'node_modules', '.venv',
              'venv', 'dist', 'build', '.tox', '.idea', '.claude'}


def _rel(base: Path, p: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def make_read(cwd: str):
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
    def read(file_path: str, offset: int | None = None,
             limit: int | None = None) -> str:
        path = resolve(cwd, file_path)
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
        note = f' ({total} lines, showing {start + 1}-{end})' if total != end - start else ''
        rel = _rel(Path(cwd).resolve(), path)
        return ToolResult(
            text=f'{rel}{note}:\n{numbered}',
            meta={'num_lines': total, 'path': rel})
    return read


def make_glob(cwd: str):
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
    def glob(pattern: str, path: str | None = None) -> str:
        base = resolve(cwd, path) if path else Path(cwd)
        if base is None:
            return f'Error: path is outside the workspace: {path}'
        if not base.is_dir():
            return f'Error: not a directory: {pattern}'
        matches = []
        for p in base.glob(pattern):
            if p.is_file() and not any(part in _SKIP_DIRS for part in p.parts):
                matches.append(_rel(Path(cwd).resolve(), p))
        if not matches:
            return f'No files match "{pattern}".'
        matches.sort()
        return ToolResult(text='\n'.join(matches),
                          meta={'num_files': len(matches)})
    return glob


def make_grep(cwd: str):
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
    async def grep(pattern: str, path: str | None = None, glob: str | None = None,
                   max_hits: int = _GREP_MAX) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f'Error: invalid regex: {e}'
        base = resolve(cwd, path) if path else Path(cwd)
        if base is None:
            return f'Error: path is outside the workspace: {path}'
        if not base.is_dir():
            return f'Error: not a directory: {path or "."}'
        if glob:
            files = (p for p in base.glob(glob) if p.is_file())
        else:
            files = (p for p in base.rglob('*') if p.is_file())
        hits = []
        files_seen = set()
        root = Path(cwd).resolve()
        for p in files:
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            try:
                text = p.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            if max_hits and len(hits) >= max_hits:
                return ToolResult(
                    text='\n'.join(hits) + f'\n[exceeded {max_hits} hits]',
                    meta={'num_files': len(files_seen),
                          'num_lines': len(hits)})
            for lineno, line in enumerate(text.splitlines(), 1):
                if max_hits and len(hits) >= max_hits:
                    break
                if rx.search(line):
                    hits.append(f'{_rel(root, p)}:{lineno}: {line}')
                    files_seen.add(_rel(root, p))
        if not hits:
            return f'No matches for "{pattern}".'
        return ToolResult(text='\n'.join(hits),
                          meta={'num_files': len(files_seen),
                                'num_lines': len(hits)})
    return grep


def make_ls(cwd: str):
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
    def ls(path: str | None = None) -> str:
        base = resolve(cwd, path) if path else Path(cwd)
        if base is None:
            return f'Error: path is outside the workspace: {path}'
        if not base.is_dir():
            return f'Error: not a directory: {path or "."}'
        base_root = Path(cwd).resolve()
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
        label = _rel(base_root, base) or '.'
        return ToolResult(text=f'{label}\n' + '\n'.join(entries),
                          meta={'num_entries': len(entries)})
    return ls
