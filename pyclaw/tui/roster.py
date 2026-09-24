from __future__ import annotations

from rich.markup import escape

from pyclaw.tui.agentview import agent_preview
from pyclaw.tui.formatting import (_format_count, _summarize, _visible_len,
                                   duration)
from pyclaw.tui.readout import _agent_tokens
from pyclaw.tui.theme import (COLLAPSE_HINT, IDLE_TEXT, ROW_ACTIVITY,
                              ROW_CONTINUATION, ROW_NARROW, ROW_PREFIX,
                              ROW_STATS_GAP, SELECT_HINT, STOPPED_TEXT,
                              STOPPING_TEXT, TREE_BRANCH, TREE_INDENT,
                              TREE_LAST, TREE_LEAD, TREE_POINTER, VIEW_HINT,
                              WAITING_PERMISSION_TEXT)
from pyclaw.tui.collapse import recent_rollup
from pyclaw.tui.toolcard import _tool_uses


def status_text(state: dict, *, running: bool, all_idle: bool,
                highlighted: bool, now: float, stopping: bool = False,
                awaiting: bool = False, work: str = '',
                stopped: bool = False) -> str:
    if stopping:
        return STOPPING_TEXT
    if stopped:
        return STOPPED_TEXT
    if awaiting:
        return WAITING_PERMISSION_TEXT
    if state.get('error'):
        return _summarize(state['error'], 60)
    if running:
        if highlighted:
            return ''
        activity = (recent_rollup(state['recent']) or state['last_tool']
                    or work or state['verb'])
        return activity if activity.endswith('…') else f"{activity}…"
    if all_idle:
        return f"{state['past']} for {duration(int(now - state['started_at']))}"
    if state.get('idle_since') is None:
        state['idle_since'] = now
    return f"{IDLE_TEXT} for {duration(int(now - state['idle_since']))}"


def _row(*, name: str, glyph: str, pointer: str, status: str, stats: str,
         view_hint: str, select_hint: str, columns: int) -> str:
    spare = columns - ROW_PREFIX
    name_cost = _visible_len(name) + 2
    show_name = columns >= ROW_NARROW and spare - name_cost >= ROW_ACTIVITY
    room = spare - (name_cost if show_name else 0)
    floor = ROW_ACTIVITY + ROW_STATS_GAP
    stats_cost = _visible_len(stats)
    view_cost = _visible_len(view_hint)
    select_cost = _visible_len(select_hint)
    show_view = bool(view_hint) and room - stats_cost - floor >= view_cost
    show_select = (bool(select_hint)
                   and room - stats_cost - view_cost * show_view - floor
                   >= select_cost)
    show_stats = bool(stats) and room - floor >= stats_cost
    extras = ((stats if show_stats else '')
              + (select_hint if show_select else '')
              + (view_hint if show_view else ''))
    activity = _summarize(status, max(ROW_ACTIVITY,
                                      room - _visible_len(extras) - 1))
    body = (f"{name}: {escape(activity)}" if show_name and activity
            else (name if show_name else escape(activity)))
    return f"{TREE_INDENT}{pointer} {glyph} {body}{extras}"


def leader_row(*, selected, foreground: bool, busy: str | None,
               tokens: int, columns: int) -> str:
    highlighted = foreground or selected == -1
    glyph = TREE_LEAD[1] if highlighted else TREE_LEAD[0]
    pointer = TREE_POINTER if selected == -1 else ' '
    status = '' if foreground else (
        f"{escape(busy)}…" if busy is not None else IDLE_TEXT)
    return _row(name='[#48968C]team-lead[/]', glyph=glyph, pointer=pointer,
                status=status,
                stats=f" \u00b7 {_format_count(tokens)} tokens" if tokens else '',
                view_hint=f" \u00b7 {VIEW_HINT}" if selected == -1
                and not foreground else '',
                select_hint=f" \u00b7 {SELECT_HINT}" if highlighted else '',
                columns=columns)


def teammate_row(agent, state: dict, *, running: bool, color: str,
                 chosen: bool, last: bool, all_idle: bool, now: float,
                 columns: int, foregrounded: bool = False,
                 stopping: bool = False, awaiting: bool = False,
                 queued: int = 0, work: str = '',
                 stopped: bool = False) -> str:
    name = str(getattr(agent, 'name', ''))
    highlighted = chosen or foregrounded
    glyph = (TREE_LAST if last else TREE_BRANCH)[1 if highlighted else 0]
    pointer = TREE_POINTER if chosen else ' '
    return _row(
        name=f"[{color}][bold]@{escape(name)}[/][/]", glyph=glyph,
        pointer=pointer,
        status=status_text(state, running=running, all_idle=all_idle,
                           highlighted=highlighted, now=now,
                           stopping=stopping, awaiting=awaiting, work=work,
                           stopped=stopped),
        stats=(f" \u00b7 {_tool_uses(state['tools'])}"
               f" \u00b7 {_format_count(_agent_tokens(agent))} tokens"
               + (f" \u00b7 {queued} queued" if queued else '')),
        view_hint=f" \u00b7 {VIEW_HINT}" if chosen else '',
        select_hint=f" \u00b7 {SELECT_HINT}" if highlighted else '',
        columns=columns)


def hide_row(chosen: bool) -> str:
    glyph = TREE_LAST[1] if chosen else TREE_LAST[0]
    pointer = TREE_POINTER if chosen else ' '
    hint = f" \u00b7 {COLLAPSE_HINT}" if chosen else ''
    return f"{TREE_INDENT}{pointer} {glyph} hide{hint}"


def preview_rows(agent, *, last: bool, cwd: str) -> list[str]:
    return [f"{TREE_INDENT} {ROW_CONTINUATION[1 if last else 0]} "
            f"{escape(line)}"
            for line in agent_preview(agent, cwd=cwd)]
