from __future__ import annotations

import asyncio
from datetime import datetime

from pyclaw.tui.formatting import duration
from pyclaw.tui.queued import QueuedPrompt
from pyclaw.tui.theme import ASTERISK
from chatchat.tasks.cron_schedule import (find_missed, missed_notification,
                                          SchedulerLock)
from pyclaw.cron import run


class DeliveryMixin:
    async def _finish_work(self):
        self._discard_think()
        elapsed = self._elapsed_seconds()
        text = (f"[dim]{ASTERISK} {self._turn_past} for "
                f"{duration(elapsed)}[/]")
        if self._work_block is None or self._work_block.parent is None:
            self._work_block = await self._append_block(text)
        else:
            self._work_block.update(text)
        self._set_title(False)
        self._note_finished()
        await self._refresh_git()
        self._render_readouts()

    def _missed_prompts(self, now=None) -> list:

        store = getattr(self._team, 'cron', None)
        if store is None:
            return []
        overdue = find_missed(store.durable(), now or datetime.now())
        return [task for task in overdue if not task.get('recurring')]

    async def _note_missed_prompts(self):
        missed = self._missed_prompts()
        store = getattr(self._team, 'cron', None)
        if not missed or store is None:
            return
        for task in missed:
            store.remove(task['id'])
        await self._pending_inputs.put(
            QueuedPrompt(missed_notification(missed), meta=True))

    def _start_cron(self):

        store = getattr(self._team, 'cron', None)
        if store is None:
            return
        self._cron_lock = SchedulerLock(store.directory,
                                        str(self._session.conv_session_id))
        self._cron_lock.acquire()
        self._cron_task = asyncio.create_task(
            run(store, self._cron_lock, self._deliver_cron))

    async def _deliver_cron(self, task: dict):
        prompt = str(task.get('prompt') or '')
        if not prompt:
            return
        name = task.get('agent')
        if name:
            agent = self._team.get_by_name(str(name))
            if agent is not None:
                agent.submit(prompt)
                return
            store = getattr(self._team, 'cron', None)
            if store is not None:
                store.remove(task.get('id'))
            return
        await self._pending_inputs.put(QueuedPrompt(prompt, meta=True))
