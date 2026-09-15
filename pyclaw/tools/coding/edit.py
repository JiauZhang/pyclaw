from __future__ import annotations

import difflib
from pathlib import Path

from chatchat.tool import tool

from .paths import resolve


def _rel(base: Path, p: Path) -> str:
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)


def _read_text(path: Path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding='utf-8'), None
    except UnicodeDecodeError:
        return None, f'Error: not a text file: {path}'
    except OSError as e:
        return None, f'Error reading {path}: {e}'


def _diff(rel: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f'a/{rel}', tofile=f'b/{rel}',
    )
    return ''.join(lines).rstrip('\n')


def make_write(cwd: str):
    @tool(
        name='Write',
        description='Create a new file or overwrite an existing one entirely.',
        parameters={
            'type': 'object',
            'properties': {
                'file_path': {'type': 'string',
                              'description': 'Path relative to the workspace.'},
                'content': {'type': 'string',
                            'description': 'Full new file content.'},
            },
            'required': ['file_path', 'content'],
        },
    )
    def write(file_path: str, content: str) -> str:
        path = resolve(cwd, file_path)
        if path is None:
            return f'Error: path is outside the workspace: {file_path}'
        if path.exists() and path.is_dir():
            return f'Error: is a directory: {file_path}'
        rel = _rel(Path(cwd).resolve(), path)
        old_text, err = _read_text(path) if path.exists() else (None, None)
        if path.exists() and old_text is None:
            return err
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            path.write_text(content, encoding='utf-8')
        except OSError as e:
            return f'Error writing {file_path}: {e}'
        if existed:
            return (f'The file {rel} has been updated successfully.\n\n'
                    + _diff(rel, old_text or '', content))
        return f'File created successfully at: {rel}'
    return write


def make_edit(cwd: str):
    @tool(
        name='Edit',
        description='Replace an exact string in a file. old_string must occur '
                    'exactly once, otherwise the edit fails.',
        parameters={
            'type': 'object',
            'properties': {
                'file_path': {'type': 'string',
                              'description': 'Path relative to the workspace.'},
                'old_string': {'type': 'string',
                               'description': 'Unique text to replace.'},
                'new_string': {'type': 'string',
                               'description': 'Replacement text.'},
            },
            'required': ['file_path', 'old_string', 'new_string'],
        },
    )
    def edit(file_path: str, old_string: str, new_string: str) -> str:
        path = resolve(cwd, file_path)
        if path is None:
            return f'Error: path is outside the workspace: {file_path}'
        text, err = _read_text(path)
        if text is None:
            return err
        count = text.count(old_string)
        if count == 0:
            return f'Error: old_string not found in {file_path}.'
        if count > 1:
            return (f'Error: old_string is not unique ({count} matches) in '
                    f'{file_path}. Provide more surrounding context.')
        after = text.replace(old_string, new_string, 1)
        try:
            path.write_text(after, encoding='utf-8')
        except OSError as e:
            return f'Error writing {file_path}: {e}'
        rel = _rel(Path(cwd).resolve(), path)
        return (f'The file {rel} has been updated successfully.\n\n'
                + _diff(rel, text, after))
    return edit


def make_multi_edit(cwd: str):
    @tool(
        name='MultiEdit',
        description='Apply several exact-string replacements to one file in a '
                    'single call. Every old_string must occur exactly once, '
                    'otherwise nothing is written.',
        parameters={
            'type': 'object',
            'properties': {
                'file_path': {'type': 'string',
                              'description': 'Path relative to the workspace.'},
                'edits': {
                    'type': 'array',
                    'minItems': 1,
                    'items': {
                        'type': 'object',
                        'properties': {
                            'old_string': {'type': 'string'},
                            'new_string': {'type': 'string'},
                        },
                        'required': ['old_string', 'new_string'],
                    },
                },
            },
            'required': ['file_path', 'edits'],
        },
    )
    def multi_edit(file_path: str, edits: list) -> str:
        path = resolve(cwd, file_path)
        if path is None:
            return f'Error: path is outside the workspace: {file_path}'
        text, err = _read_text(path)
        if text is None:
            return err
        working = text
        for i, e in enumerate(edits):
            old = e.get('old_string', '')
            new = e.get('new_string', '')
            count = working.count(old)
            if count == 0:
                return (f'Error: edit {i + 1} old_string not found in '
                        f'{file_path}.')
            if count > 1:
                return (f'Error: edit {i + 1} old_string is not unique '
                        f'({count} matches). Provide more context.')
            working = working.replace(old, new, 1)
        try:
            path.write_text(working, encoding='utf-8')
        except OSError as e:
            return f'Error writing {file_path}: {e}'
        rel = _rel(Path(cwd).resolve(), path)
        return (f'The file {rel} has been updated successfully.\n\n'
                + _diff(rel, text, working))
    return multi_edit
