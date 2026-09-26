from __future__ import annotations

from chatchat.tool import ToolResult, tool

from pyclaw.tui.formatting import _plural
from pyclaw.tui.toolui import (build_tool_ui, is_memory_path,
                               path_args, register)

from .paths import relative, resolve
from .names import READ


_READ_LIMIT = 2000
_TEXT_ERROR = "Invalid UTF-8"

@tool(
    name=READ,
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
    path = resolve(context.cwd, file_path, context.extra_dirs)
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

register(READ, build_tool_ui(args=_read_args,
                               summary=_read_summary, kinds=_read_kinds))
