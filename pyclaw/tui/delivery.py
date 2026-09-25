from __future__ import annotations

import asyncio
from datetime import datetime

from pyclaw.tui.formatting import duration
from pyclaw.tui.theme import ASTERISK
from chatchat.tasks.cron_schedule import find_missed, SchedulerLock
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
        return find_missed(store.durable(), now or datetime.now())

    async def _note_missed_prompts(self):
        missed = self._missed_prompts()
        if not missed:
            return
        await self._append_note(
            f'{len(missed)} scheduled prompt(s) came due while PyClaw was '
            'not running: '
            + ', '.join(str(task['prompt'])[:40] for task in missed))

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
        agent = (self._team.get_by_name(task['agent'])
                 if task.get('agent') else None)
        if agent is not None:
            agent.submit(prompt)
            return
        await self._pending_inputs.put(prompt)
