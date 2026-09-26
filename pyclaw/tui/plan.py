from __future__ import annotations

from pyclaw.tui.formatting import escape

from pyclaw.tui.formatting import _summarize
from pyclaw.tui.theme import (BLOCKED_PREFIX, DONE_COLOR, NEXT_PREFIX,
                              PLAN_HIDDEN, PLAN_ICONS, PLAN_MAX_LINES,
                              PLAN_MIN_ROWS, PLAN_RECENT_SECONDS)


def _number(task_id: str) -> int:
    try:
        return int(task_id)
    except (TypeError, ValueError):
        return 0


def open_ids(tasks) -> set[str]:
    return {t.id for t in tasks if t.open()}


def next_pending(tasks):
    waiting = [t for t in tasks if t.status == 'pending']
    if not waiting:
        return None
    unresolved = open_ids(tasks)
    for task in waiting:
        if not any(b in unresolved for b in task.blocked_by):
            return task
    return waiting[0]


def next_task_line(tasks) -> str:
    task = next_pending(tasks)
    return '' if task is None else (
        f"[dim]{NEXT_PREFIX}{escape(_summarize(task.subject, 80))}[/]")


def _waiting_order(tasks):
    unresolved = open_ids(tasks)
    return sorted([t for t in tasks if t.status == 'pending'],
                  key=lambda t: (any(b in unresolved for b in t.blocked_by),
                                 _number(t.id)))


def _prioritised(tasks, recent: set[str]) -> list:
    closed = [t for t in tasks if not t.open()]
    return ([*sorted([t for t in closed if t.id in recent], key=_number),
             *sorted([t for t in tasks if t.status == 'in_progress'],
                     key=_number),
             *_waiting_order(tasks),
             *sorted([t for t in closed if t.id not in recent],
                     key=_number)])


def _task_row(task, *, columns: int, unresolved: set[str], brand: str,
              owner_color: str, activity: str, owner_alive: bool) -> str:
    icon = PLAN_ICONS[task.status]
    color = {'completed': DONE_COLOR, 'in_progress': brand,
             'pending': 'white'}[task.status]
    blockers = sorted((b for b in task.blocked_by if b in unresolved),
                      key=_number)
    blocked = bool(blockers)
    finished = task.status == 'completed'
    label = escape(_summarize(activity or task.subject, max(15, columns - 15)))
    styles = (['strike'] if finished else []) + \
        (['dim'] if finished or blocked else []) + \
        (['bold'] if task.status == 'in_progress' else [])
    if styles:
        label = f'[{" ".join(styles)}]{label}[/]'
    row = f'[{color}]{icon}[/] {label}'
    if task.owner and owner_alive and columns >= 60:
        owner = escape(_summarize(task.owner, 24))
        owner = (f'[{owner_color}]@{owner}[/]' if owner_color
                 else f'@{owner}')
        row += f'[dim] ({owner})[/]'
    if blocked:
        row += (f'[dim]{BLOCKED_PREFIX}'
                + ', '.join(f'#{b}' for b in blockers) + '[/]')
    return row


def plan_lines(tasks, *, columns: int, rows: int, brand: str,
               colors=None, activity=None, alive=(), recent=None) -> list[str]:
    """The task list as the spinner sees it: full list when there is room for
    it, otherwise the priority order the reference uses plus a count."""
    if not tasks:
        return []
    limit = (0 if rows <= PLAN_MIN_ROWS
             else min(PLAN_MAX_LINES, max(3, rows - 14)))
    ordered = (sorted(tasks, key=_number) if not limit
               else _prioritised(tasks, recent or set()))
    shown, hidden = ((ordered, []) if not limit
                     else (ordered[:limit], ordered[limit:]))
    unresolved = open_ids(tasks)
    colors = colors or {}
    activity = activity or {}
    alive = set(alive)
    lines = [_task_row(task, columns=columns, unresolved=unresolved,
                       brand=brand,
                       owner_color=colors.get(task.owner, ''),
                       activity=(activity.get(task.owner, '')
                                 if task.status == 'in_progress'
                                 and not any(b in unresolved
                                             for b in task.blocked_by)
                                 else ''),
                       owner_alive=task.owner in alive)
               for task in shown]
    if limit and hidden:
        parts = [f'{sum(1 for t in hidden if t.status == status)} {noun}'
                 for status, noun in (('in_progress', 'in progress'),
                                      ('pending', 'pending'),
                                      ('completed', 'completed'))
                 if any(t.status == status for t in hidden)]
        if parts:
            lines.append(f'[dim]{PLAN_HIDDEN}{", ".join(parts)}[/]')
    return lines


def recent_completions(tasks, seen: dict[str, float],
                       now: float) -> set[str]:
    """Ids that moved to completed within the last grace window, so a task
    that just closed does not vanish before you read it."""
    closed = {t.id for t in tasks if not t.open()}
    for task_id in closed:
        seen.setdefault(task_id, now)
    for task_id in list(seen):
        if task_id not in closed:
            seen.pop(task_id)
        elif now - seen[task_id] > PLAN_RECENT_SECONDS:
            seen.pop(task_id)
    return {t for t in closed if t in seen}
