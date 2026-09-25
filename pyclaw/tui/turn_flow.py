from __future__ import annotations

import asyncio
import logging
import random
import time

from rich.markup import escape

from pyclaw.spinner_verbs import SPINNER_VERBS
from pyclaw.tui.readout import _agent_tokens
from pyclaw.tui.theme import FINISHED_LINGER_SECONDS

logger = logging.getLogger(__name__)


class TurnFlowMixin:

    def _note_lingering(self):
        alive = {str(getattr(agent, 'name', '')): agent
                 for agent in self._alive_teammates()}
        now = time.monotonic()
        for name, agent in self._known_teammates.items():
            if name not in alive and name not in self._lingering:
                self._lingering[name] = (agent, now + FINISHED_LINGER_SECONDS)
        self._known_teammates = alive
        for name in [name for name, (_, deadline) in self._lingering.items()
                     if now >= deadline]:
            self._lingering.pop(name, None)
            self._agent_state.pop(name, None)
    def _schedule_linger(self):
        if not self._lingering or self._linger_task is not None:
            return
        self._linger_task = asyncio.create_task(self._expire_lingering())
    async def _expire_lingering(self):
        try:
            while self._lingering:
                deadline = min(end for _, end in self._lingering.values())
                await asyncio.sleep(max(0.1, deadline - time.monotonic()))
                await self._refresh_agents()
        finally:
            self._linger_task = None
    def _begin_turn(self):
        self._set_title(True)
        self._live = None
        self._live_text = ""
        self._turn_usage = _agent_tokens(self._team.lead)
        self._wrote_body = False
        self._interrupted_call = False
        self._turn_start = len(self._team.transcript())
        self._turn_verb = random.choice(SPINNER_VERBS)
        self._turn_past = self._completion_verb()
        self._turn_started_at = time.monotonic()
    async def _settle_paint(self):
        done = asyncio.Event()
        self.call_after_refresh(done.set)
        await done.wait()
    async def _wait_session_idle(self):
        lead = self._team.lead
        while True:
            if not getattr(lead, 'busy', False) and await lead.idle():
                return
            await asyncio.sleep(0.05)
    def _teammates_running(self) -> bool:
        return any(a is not self._team.lead and getattr(a, 'busy', False)
                   for a in self._team.agents.values())
    async def _finish_work_when_settled(self):
        while self._teammates_running():
            await asyncio.sleep(0.1)
        await self._finish_work()
    async def _converse(self, text: str):
        try:
            out = await self._session.chat(text)
        except Exception as exc:
            logger.exception("chat failed for prompt %r", _log_data(text))
            if self._events is not None:
                self._events.note_error(str(exc))
            await self._append_error(str(exc))
            return
        await self._wait_session_idle()
        await self._queue.join()
        if out.strip() and not self._wrote_body and not self._interrupted_call \
                and len(self._team.transcript()) > self._turn_start:
            await self._append_block(escape(out))
        self._render_status()
