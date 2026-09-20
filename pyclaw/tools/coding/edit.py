from __future__ import annotations

import difflib

from chatchat.tool import ToolResult, tool

from .paths import relative, resolve


def _read_text(path) -> tuple[str | None, str | None]:
    try:
        return path.read_text(encoding='utf-8'), None
    except UnicodeDecodeError:
        return None, f'Error: not a text file: {path}'
    except OSError as e:
        return None, f'Read of {path} failed: {e}'


def _diff_counts(before: str, after: str):
    added = sum(1 for ln in after.splitlines()
                if ln not in before.splitlines())
    removed = sum(1 for ln in before.splitlines()
                  if ln not in after.splitlines())
    return added, removed


def _diff(rel: str, before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f'a/{rel}', tofile=f'b/{rel}',
    )
    return ''.join(lines).rstrip('\n')


@tool(
    name='Write',
    description='Creates a file or rewrites an existing one, returning the '
                'path and, for an update, the unified diff. content becomes '
                'the whole file: anything left out of it is lost, so read the '
                'file first and write it back complete.',
    get_path=lambda args: args.get('file_path'),
    parameters={
        'type': 'object',
        'properties': {
            'file_path': {'type': 'string',
                          'description': 'Path relative to the workspace.'},
            'content': {'type': 'string',
                        'description': 'File content.'},
        },
        'required': ['file_path', 'content'],
    },
)
def Write(context, file_path: str, content: str) -> str:
    path = resolve(context.cwd, file_path)
    if path is None:
        return f'Error: path is outside the workspace: {file_path}'
    if path.exists() and path.is_dir():
        return f'Error: is a directory: {file_path}'
    rel = relative(context.cwd, path)
    old_text, err = _read_text(path) if path.exists() else (None, None)
    if path.exists() and old_text is None:
        return err
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        path.write_text(content, encoding='utf-8')
    except OSError as e:
        return f'Write to {file_path} failed: {e}'
    if existed:
        added, removed = _diff_counts(old_text or '', content)
        return ToolResult(
            text=(f'Saved {rel}.\n\n'
                  + _diff(rel, old_text or '', content)),
            meta={'path': rel, 'mode': 'updated',
                  'num_added': added, 'num_removed': removed})
    return ToolResult(text=f'Created {rel}.',
                      meta={'path': rel, 'mode': 'wrote'})


@tool(
    name='Edit',
    description='Replaces an exact string in one file and returns the unified '
                'diff of the change. old_string must match the file byte for '
                'byte, indentation included, and must occur exactly once.',
    get_path=lambda args: args.get('file_path'),
    parameters={
        'type': 'object',
        'properties': {
            'file_path': {'type': 'string',
                          'description': 'Path relative to the workspace.'},
            'old_string': {'type': 'string',
                           'description': 'Text to replace.'},
            'new_string': {'type': 'string',
                           'description': 'Replacement text.'},
        },
        'required': ['file_path', 'old_string', 'new_string'],
    },
)
def Edit(context, file_path: str, old_string: str, new_string: str) -> str:
    return _replace(context, file_path, [(old_string, new_string)])


@tool(
    name='MultiEdit',
    description='Applies exact-string replacements to one file in a single '
                'call and returns the unified diff of the combined change. '
                'Every old_string must match byte for byte and occur exactly '
                'once; if any one fails nothing is written. Later strings are '
                'matched against the file as earlier ones have already '
                'rewritten it.',
    get_path=lambda args: args.get('file_path'),
    parameters={
        'type': 'object',
        'properties': {
            'file_path': {'type': 'string',
                          'description': 'Path relative to the workspace.'},
            'edits': {
                'type': 'array',
                'minItems': 1,
                'description': 'Replacements, applied in order.',
                'items': {
                    'type': 'object',
                    'properties': {
                        'old_string': {'type': 'string',
                                       'description': 'Text to replace.'},
                        'new_string': {'type': 'string',
                                       'description': 'Replacement text.'},
                    },
                    'required': ['old_string', 'new_string'],
                },
            },
        },
        'required': ['file_path', 'edits'],
    },
)
def MultiEdit(context, file_path: str, edits: list) -> str:
    return _replace(context, file_path,
                    [(e.get('old_string', ''), e.get('new_string', ''))
                     for e in edits])


def _replace(context, file_path: str, pairs) -> str:
    path = resolve(context.cwd, file_path)
    if path is None:
        return f'Error: path is outside the workspace: {file_path}'
    text, err = _read_text(path)
    if text is None:
        return err
    numbered = len(pairs) > 1
    working = text
    for i, (old, new) in enumerate(pairs):
        tag = f'edit {i + 1} ' if numbered else ''
        count = working.count(old)
        if count == 0:
            return f'Error: {tag}old_string not found in {file_path}.'
        if count > 1:
            return (f'Error: {tag}old_string is not unique ({count} matches) in '
                    f'{file_path}. Provide more surrounding context.')
        working = working.replace(old, new, 1)
    try:
        path.write_text(working, encoding='utf-8')
    except OSError as e:
        return f'Write to {file_path} failed: {e}'
    rel = relative(context.cwd, path)
    added, removed = _diff_counts(text, working)
    return ToolResult(
        text=(f'Saved {rel}.\n\n'
              + _diff(rel, text, working)),
        meta={'path': rel, 'num_added': added, 'num_removed': removed})
