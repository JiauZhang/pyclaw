from __future__ import annotations

import difflib

from chatchat.tool import ToolResult, tool

from pyclaw.tui.toolui import build_tool_ui, is_memory_path, path_args, register

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
        context.track_edit(path)
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
                'byte, indentation included, and must occur exactly once '
                'unless replace_all is set.',
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
            'replace_all': {'type': 'boolean', 'default': False,
                            'description': 'Replace every occurrence of '
                                           'old_string.'},
        },
        'required': ['file_path', 'old_string', 'new_string'],
    },
)
def Edit(context, file_path: str, old_string: str, new_string: str,
         replace_all: bool = False) -> str:
    return _replace(context, file_path, old_string, new_string,
                    bool(replace_all))


def _replace(context, file_path: str, old_string: str, new_string: str,
             replace_all: bool = False) -> str:
    path = resolve(context.cwd, file_path)
    if path is None:
        return f'Error: path is outside the workspace: {file_path}'
    text, err = _read_text(path)
    if text is None:
        return err
    count = text.count(old_string)
    if count == 0:
        return f'Error: old_string not found in {file_path}.'
    if count > 1 and not replace_all:
        return (f'Error: old_string is not unique ({count} matches) in '
                f'{file_path}. Provide more surrounding context, or set '
                f'replace_all to change every one of them.')
    working = (text.replace(old_string, new_string) if replace_all
               else text.replace(old_string, new_string, 1))
    try:
        context.track_edit(path)
        path.write_text(working, encoding='utf-8')
    except OSError as e:
        return f'Write to {file_path} failed: {e}'
    rel = relative(context.cwd, path)
    added, removed = _diff_counts(text, working)
    return ToolResult(
        text=(f'Saved {rel}.\n\n'
              + _diff(rel, text, working)),
        meta={'path': rel, 'num_added': added, 'num_removed': removed})


def _write_args(name, tool_input, cwd):
    return path_args(tool_input, cwd)


def _write_kinds(name, tool_input):
    data = tool_input if isinstance(tool_input, dict) else {}
    raw = data.get('file_path') or data.get('path') or ''
    return {'memory_write'} if is_memory_path(raw) else set()


register('Write', build_tool_ui(args=_write_args, kinds=_write_kinds))
register('Edit', build_tool_ui(args=_write_args, kinds=_write_kinds))
