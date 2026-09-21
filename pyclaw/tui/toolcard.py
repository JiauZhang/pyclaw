from __future__ import annotations

from pathlib import Path
from rich.markup import escape

from pyclaw.tui.formatting import (_clip_lines, _display_path, _plural,
                                   _preview, _single_line, _summarize)
from pyclaw.tui.theme import (BASH_LIST_COMMANDS, BASH_NEUTRAL_COMMANDS,
                              BASH_READ_COMMANDS, BASH_SEARCH_COMMANDS,
                              DISPLAY_NAMES, EXPAND_HINT, MAX_COMMAND_CHARS,
                              MAX_COMMAND_LINES, MAX_USE_ARG_CHARS,
                              MEMORY_FILE_NAME, PATH_TOOLS, SEARCH_TOOLS)


def _is_memory_path(value) -> bool:
    return Path(str(value or '')).name == MEMORY_FILE_NAME


def _bash_kinds(command) -> set:
    from pyclaw.tools.coding.shell_rules import base_command, split_commands
    try:
        parts = [p for p in split_commands(str(command or ''))
                 if base_command(p) not in BASH_NEUTRAL_COMMANDS]
    except Exception:
        return set()
    if not parts:
        return set()
    kinds = set()
    for part in parts:
        base = base_command(part)
        if base in BASH_SEARCH_COMMANDS:
            kinds.add('search')
        elif base in BASH_READ_COMMANDS:
            kinds.add('read')
        elif base in BASH_LIST_COMMANDS:
            kinds.add('list')
        else:
            return {'bash'}
    return kinds


def _collapsible_kinds(name, tool_input) -> set:
    data = tool_input if isinstance(tool_input, dict) else {}
    raw_path = data.get('file_path') or data.get('path') or ''
    if name == 'Read':
        return {'memory_read' if _is_memory_path(raw_path) else 'read'}
    if name in ('Grep', 'Glob'):
        return {'search'}
    if name == 'LS':
        return {'list'}
    if name in ('Write', 'Edit', 'MultiEdit'):
        return {'memory_write'} if _is_memory_path(raw_path) else set()
    if name == 'Bash':
        return _bash_kinds(data.get('command'))
    return set()


def _read_key(name, tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    return str(data.get('file_path') or data.get('path') or name)


def _display_name(name: str) -> str:
    return DISPLAY_NAMES.get(name, name)


def _agent_tool_name(tool_input) -> str:
    data = tool_input if isinstance(tool_input, dict) else {}
    subagent = str(data.get('subagent_type') or '')
    if subagent and subagent != 'general-purpose':
        return 'Agent' if subagent == 'worker' else subagent
    return 'Agent'


def _tool_label(name: str, tool_input) -> str:
    if name == 'create_agent':
        return _agent_tool_name(tool_input)
    return _display_name(name)


def _hidden_card(name: str, tool_input) -> bool:
    if name != 'send_message':
        return False
    data = tool_input if isinstance(tool_input, dict) else {}
    return isinstance(data.get('message'), str)


def _tool_use_args(name: str, tool_input, cwd) -> str:
    if not isinstance(tool_input, dict):
        return _clip_lines(tool_input, MAX_COMMAND_LINES, MAX_COMMAND_CHARS)
    data = tool_input
    if name == "Bash":
        return _clip_lines(data.get("command", ""), MAX_COMMAND_LINES,
                           MAX_COMMAND_CHARS)
    if name in PATH_TOOLS:
        return _display_path(cwd, data.get("file_path") or data.get("path"))
    if name in SEARCH_TOOLS:
        parts = [f'pattern: "{data.get("pattern", "")}"']
        target = data.get("path")
        if target:
            parts.append(f'path: "{_display_path(cwd, target)}"')
        return ", ".join(parts)
    if name == "create_agent":
        return _summarize(data.get("prompt") or data.get("name") or "",
                          MAX_USE_ARG_CHARS)
    if name == "send_message":
        return _summarize(f'{data.get("to", "")}: {data.get("message", "")}',
                          MAX_USE_ARG_CHARS)
    pairs = ", ".join(f"{k}: {v}" for k, v in data.items())
    return _clip_lines(pairs, MAX_COMMAND_LINES, MAX_COMMAND_CHARS)


def _last_assistant_key(messages) -> tuple:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get('role') == 'assistant':
            return (i, len(str(messages[i].get('content') or '')))
    return (0, 0)


def _result_summary(name: str, output, width: int) -> str:
    text = str(output if output is not None else "")
    if not text:
        return "Done"
    if text.startswith("Error"):
        return text.split("\n")[0]
    if name == "Read":
        rows = text.split("\n")
        body = rows[1:] if rows and rows[0].endswith(":") else rows
        return f"Read {_plural(len([r for r in body if r.strip()]), 'line')}"
    if name in SEARCH_TOOLS:
        hits = [r for r in text.split("\n") if r.strip()]
        if name == "Glob" or hits and ":" not in hits[0]:
            return f"Found {_plural(len(hits), 'file')}"
        return f"Found {_plural(len(hits), 'line')}"
    if name == "LS":
        rows = [r for r in text.split("\n")[1:] if r.strip()]
        noun = "entry" if len(rows) == 1 else "entries"
        return f"Listed {len(rows)} {noun}"
    if name == "Bash":
        return _preview(text, width)
    if name == "create_agent":
        return "Done"
    return _preview(text, width)


def _tool_uses(count: int) -> str:
    return f"{count} tool call" if count == 1 else f"{count} tool calls"


def _more_tool_uses(count: int) -> str:
    return f"+{_tool_uses(count)} ({EXPAND_HINT})"


def _agent_progress_rows(message, cwd, width: int) -> tuple[list[str], int]:
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        return [], 0
    content = message.get('content')
    if isinstance(content, str):
        text = _single_line(content, width)
        return ([escape(text)] if text else []), 0
    if not isinstance(content, list):
        return [], 0
    rows: list[str] = []
    uses = 0
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get('type')
        if kind == 'text':
            text = _single_line(block.get('text', ''), width)
            if text:
                rows.append(escape(text))
        elif kind == 'tool_use':
            uses += 1
            target = str(block.get('name') or 'tool')
            label = escape(_tool_label(target, block.get('input')))
            args = _single_line(_tool_use_args(target, block.get('input'), cwd),
                                max(width * 2, 80))
            rows.append(f"{label}({escape(args)})" if args else label)
    return rows, uses
