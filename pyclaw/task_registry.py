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

PENDING = 'pending'
RUNNING = 'running'
COMPLETED = 'completed'
FAILED = 'failed'
KILLED = 'killed'

POLL_SECONDS = 1.0
STOPPED_DISPLAY_SECONDS = 3
PANEL_GRACE_SECONDS = 30

PREFIXES = {'shell': 'b', 'agent': 'a', 'teammate': 't'}

ALPHABET = string.ascii_lowercase + string.digits


def new_id(kind: str) -> str:
    prefix = PREFIXES.get(kind, 'x')
    return prefix + ''.join(secrets.choice(ALPHABET) for _ in range(8))


def is_terminal(status: str) -> bool:
    return status in (COMPLETED, FAILED, KILLED)


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
    payload: dict = dataclasses.field(default_factory=dict)

    def seconds(self, now: float) -> int:
        return int(max(0.0, (self.ended_at if self.ended_at is not None
                             else now) - self.started_at))

    def finish(self, status: str, now: float | None = None) -> 'Task':
        self.status = status
        self.ended_at = time.monotonic() if now is None else now
        return self

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


_registry = TaskRegistry()


def registry() -> TaskRegistry:
    return _registry
