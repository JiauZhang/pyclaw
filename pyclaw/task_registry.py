"""One record for everything running in the background.

A shell command, a sub-agent and a teammate all describe themselves the same
way here, so the task panel, the detail view and the stop key read one shape
instead of three ad-hoc dicts. Finished work lingers briefly — long enough to
be seen — and is then dropped.
"""
from __future__ import annotations

import dataclasses
import secrets
import string
import time
from pathlib import Path

PENDING = 'pending'
RUNNING = 'running'
COMPLETED = 'completed'
FAILED = 'failed'
KILLED = 'killed'

POLL_SECONDS = 1.0
STOPPED_DISPLAY_SECONDS = 3
PANEL_GRACE_SECONDS = 30
REPORT_DELTA_CHARS = 800

PREFIXES = {'shell': 'b', 'agent': 'a', 'teammate': 't'}

ALPHABET = string.ascii_lowercase + string.digits


def new_id(kind: str) -> str:
    prefix = PREFIXES.get(kind, 'x')
    return prefix + ''.join(secrets.choice(ALPHABET) for _ in range(8))


def is_terminal(status: str) -> bool:
    return status in (COMPLETED, FAILED, KILLED)


STATUS_TEXT = {COMPLETED: 'completed successfully', FAILED: 'failed',
               KILLED: 'was stopped'}


@dataclasses.dataclass
class Task:
    id: str
    kind: str
    label: str
    status: str = PENDING
    started_at: float = dataclasses.field(default_factory=time.monotonic)
    ended_at: float | None = None
    output: str = ''
    detail: str = ''
    reported_chars: int = 0
    payload: dict = dataclasses.field(default_factory=dict)

    def seconds(self, now: float) -> int:
        return int(max(0.0, (self.ended_at if self.ended_at is not None
                             else now) - self.started_at))

    def finish(self, status: str, now: float | None = None) -> 'Task':
        self.status = status
        self.ended_at = time.monotonic() if now is None else now
        return self

    def output_delta(self) -> str:
        """What the task produced since it was last reported."""
        if not self.output:
            return ''
        try:
            text = Path(self.output).read_text(encoding='utf-8',
                                               errors='replace')
        except OSError:
            return ''
        fresh = text[self.reported_chars:]
        self.reported_chars = len(text)
        fresh = fresh.strip()
        if len(fresh) <= REPORT_DELTA_CHARS:
            return fresh
        return ('[... earlier output omitted ...]\n'
                + fresh[-REPORT_DELTA_CHARS:])

    def lingers_until(self) -> float:
        if self.ended_at is None:
            return float('inf')
        if self.status == COMPLETED:
            return self.ended_at + PANEL_GRACE_SECONDS
        return self.ended_at + STOPPED_DISPLAY_SECONDS


class TaskRegistry:

    def __init__(self):
        self._tasks: dict[str, Task] = {}

    def register(self, task: Task) -> Task:
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all(self) -> list[Task]:
        return sorted(self._tasks.values(), key=lambda task: task.started_at)

    def of_kind(self, kind: str) -> list[Task]:
        return [task for task in self.all() if task.kind == kind]

    def visible(self, now: float | None = None) -> list[Task]:
        moment = time.monotonic() if now is None else now
        return [task for task in self.all()
                if not is_terminal(task.status) or task.lingers_until() > moment]

    def forget(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    def clear(self) -> None:
        self._tasks.clear()

    def sweep(self, now: float | None = None) -> list[Task]:
        moment = time.monotonic() if now is None else now
        gone = [task for task in self.all()
                if is_terminal(task.status) and task.lingers_until() <= moment]
        for task in gone:
            self.forget(task.id)
        return gone


def notification(task: Task, delta: str = '', tool_use_id: str = '') -> str:
    """What the model is told when a task reaches a terminal state."""
    lines = [f'<task-notification>',
             f'<task-id>{task.id}</task-id>']
    if tool_use_id:
        lines.append(f'<tool-use-id>{tool_use_id}</tool-use-id>')
    lines += [f'<task-type>{task.kind}</task-type>',
              f'<output-file>{task.output}</output-file>',
              f'<status>{task.status}</status>',
              f'<summary>Task "{task.label}" '
              f'{STATUS_TEXT.get(task.status, task.status)}</summary>',
              '</task-notification>']
    if delta:
        lines += ['',
                  f'Task {task.id} (type: {task.kind}) '
                  f'(status: {task.status}) (description: {task.label})',
                  f'Delta: {delta}']
        if task.output:
            lines.append('Read the output file to retrieve the result: '
                         f'{task.output}')
    return '\n'.join(lines)


_registry = TaskRegistry()


def registry() -> TaskRegistry:
    return _registry
