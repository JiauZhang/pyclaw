from __future__ import annotations

import asyncio
from datetime import datetime

from chatchat.tasks.cron_schedule import DEFAULT_JITTER, expired, next_fire


DEFAULT_TICK_SECONDS = 1.0


def _now() -> datetime:
    return datetime.now().replace(second=0, microsecond=0)


def _deliver(deliver):
    if asyncio.iscoroutinefunction(deliver):
        return deliver

    async def send(task):
        deliver(task)

    return send


async def tick(store, lock, deliver, *, now: datetime | None = None,
               cfg=DEFAULT_JITTER, clock=_now) -> list:
    now = now or clock()
    send = _deliver(deliver)
    due = list(store.session())
    if lock.acquire():
        due += store.durable()
    fired = []
    for task in due:
        if expired(task, now, cfg):
            store.remove(task['id'])
            continue
        when = next_fire(task, now, cfg)
        if when is None or when > now:
            continue
        await send(task)
        fired.append(task)
        if task.get('recurring'):
            store.mark_fired(task['id'], now)
        else:
            store.remove(task['id'])
    return fired


async def run(store, lock, deliver, *, interval: float = DEFAULT_TICK_SECONDS,
              cfg=DEFAULT_JITTER, clock=_now):
    while True:
        await asyncio.sleep(interval)
        await tick(store, lock, deliver, cfg=cfg, clock=clock)
