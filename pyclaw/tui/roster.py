from __future__ import annotations

from rich.markup import escape

from pyclaw.tui.formatting import _format_count, _summarize, duration
from pyclaw.tui.readout import _agent_tokens
from pyclaw.tui.theme import (COLLAPSE_HINT, IDLE_TEXT, SELECT_HINT,
                              TREE_BRANCH, TREE_INDENT, TREE_LAST, TREE_LEAD,
                              TREE_POINTER, VIEW_HINT)
from pyclaw.tui.toolcard import _tool_uses


def status_text(state: dict, *, running: bool, all_idle: bool,
                highlighted: bool, now: float) -> str:
    if state.get('error'):
        return _summarize(state['error'], 60)
    if running:
        if highlighted:
            return ''
        if state['last_tool']:
            return f"{state['last_tool']}\u2026"
        return f"{state['verb']}\u2026"
    if all_idle:
        return f"{state['past']} for {duration(int(now - state['started_at']))}"
    if state.get('idle_since') is None:
        state['idle_since'] = now
    return f"{IDLE_TEXT} for {duration(int(now - state['idle_since']))}"


def leader_row(*, selected, foreground: bool, busy: str | None,
               tokens: int) -> str:
    highlighted = foreground or selected == -1
    glyph = TREE_LEAD[1] if selected == -1 else TREE_LEAD[0]
    pointer = TREE_POINTER if selected == -1 else ' '
    label = "[#48968C]team-lead[/]"
    if not foreground:
        label += (f": {escape(busy)}\u2026" if busy is not None
                  else f": {IDLE_TEXT}")
    stats = f" \u00b7 {_format_count(tokens)} tokens" if tokens else ''
    hint = f" \u00b7 {SELECT_HINT}" if highlighted else ''
    view = f" \u00b7 {VIEW_HINT}" if selected == -1 and not foreground else ''
    return f"{TREE_INDENT}{pointer} {glyph} {label}{stats}{hint}{view}"


def teammate_row(agent, state: dict, *, running: bool, color: str,
                 chosen: bool, last: bool, all_idle: bool,
                 now: float) -> str:
    name = str(getattr(agent, 'name', ''))
    glyph = (TREE_LAST if last else TREE_BRANCH)[1 if chosen else 0]
    pointer = TREE_POINTER if chosen else ' '
    status = status_text(state, running=running, all_idle=all_idle,
                         highlighted=chosen, now=now)
    label = f"[{color}][bold]@{escape(name)}[/][/]"
    body = f"{label}: {escape(status)}" if status else label
    stats = (f" \u00b7 {_tool_uses(state['tools'])}"
             f" \u00b7 {_format_count(_agent_tokens(agent))} tokens")
    hint = f" \u00b7 {SELECT_HINT}" if chosen else ''
    view = f" \u00b7 {VIEW_HINT}" if chosen else ''
    return f"{TREE_INDENT}{pointer} {glyph} {body}{stats}{hint}{view}"


def hide_row(chosen: bool) -> str:
    glyph = TREE_LAST[1] if chosen else TREE_LAST[0]
    pointer = TREE_POINTER if chosen else ' '
    hint = f" \u00b7 {COLLAPSE_HINT}" if chosen else ''
    return f"{TREE_INDENT}{pointer} {glyph} hide{hint}"
