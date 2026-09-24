from __future__ import annotations

from rich.markup import escape

from pyclaw.tui.formatting import (_format_count, _single_line, _summarize)
from pyclaw.tui.theme import (EXPAND_HINT, GROUP_PARTS, INITIALIZING_TEXT,
                              MAX_USE_ARG_CHARS, ROLLUP_KINDS, TREE_BRANCH,
                              TREE_INDENT, TREE_LAST)
from pyclaw.tui.toolui import tool_args, tool_label


def group_text(counts: dict, *, active: bool, markup: bool = True) -> str:
    chunks = []
    for kind, active_verb, done_verb, noun, plural in GROUP_PARTS:
        count = counts.get(kind, 0)
        if not count:
            continue
        verb = active_verb if active else done_verb
        verb = verb[0].upper() + verb[1:] if not chunks else \
            verb[0].lower() + verb[1:]
        shown = f"[bold]{count}[/]" if markup else str(count)
        chunks.append(f"{verb} {shown} {noun if count == 1 else plural}")
    return ", ".join(chunks)


def recent_rollup(recent: list) -> str:
    counts = {}
    for kinds in reversed(recent):
        if not kinds or not kinds <= ROLLUP_KINDS:
            break
        for kind in kinds:
            counts[kind] = counts.get(kind, 0) + 1
    if sum(counts.values()) < 2:
        return ''
    return group_text(counts, active=True, markup=False)


def _last_assistant_key(messages) -> tuple:
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get('role') == 'assistant':
            return (i, len(str(messages[i].get('content') or '')))
    return (0, 0)


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
            label = escape(tool_label(target, block.get('input')))
            args = _single_line(tool_args(target, block.get('input'), cwd),
                                max(width * 2, 80))
            rows.append(f"{label}({escape(args)})" if args else label)
    return rows, uses



def agent_group_label(name: str, tool_input) -> tuple[str, str]:
    """The grouped card labels a teammate spawn by its name and a one-off by
    its agent type, and keeps the other half as the parenthesised detail."""
    data = tool_input if isinstance(tool_input, dict) else {}
    teammate = str(data.get('name') or '')
    if teammate:
        label = f'@{teammate}'
        detail = str(data.get('subagent_type') or '')
    else:
        label = tool_label(name, tool_input) if name == 'create_agent' else name
        detail = str(data.get('prompt') or data.get('description') or '')
    return label, _summarize(detail, MAX_USE_ARG_CHARS)


def agent_group_header(count: int, *, kind: str = '', done: bool = False,
                       background: bool = False) -> str:
    group = f'{kind} agents' if kind else 'agents'
    if done:
        return (f'[bold]{count}[/] background {group} launched' if background
                else f'[bold]{count}[/] {group} finished')
    return f'Running [bold]{count}[/] {group}…'


def agent_group_row(*, label: str, detail: str = '', tools: int = 0,
                    tokens: int | None = None, status: str = '',
                    last: bool = False, error: bool = False) -> str:
    glyph = TREE_LAST[0] if last else TREE_BRANCH[0]
    row = f'{TREE_INDENT}{glyph} [bold]{escape(label)}[/]'
    if detail:
        row += f'[dim]({escape(detail)})[/]'
    row += f' · {_tool_uses(tools)}'
    if tokens:
        row += f' · {_format_count(tokens)} tokens'
    state = status or INITIALIZING_TEXT
    return row + (f' · [red]{escape(state)}[/]' if error
                  else f' · {escape(state)}')
