from __future__ import annotations

import asyncio
import logging
import time

from pyclaw.tui.formatting import escape

from chatchat.hooks.events import (AGENT_COMPACT, AGENT_PROGRESS,
                                   AGENT_REASON_START, AGENT_STATE, AGENT_TEXT,
                                   AGENT_TOOL_CALL, AGENT_TOOL_RESULT,
                                   AGENT_TURN_FINISHED, AGENT_WARN)
from pyclaw.session.store import append_conv
from pyclaw.tui.components import _SummaryBlock
from pyclaw.tui.formatting import _content_text, _log_data, _summarize
from pyclaw.tui.theme import RECENT_ACTIVITIES, RESULT_PREFIX
from pyclaw.tui.toolcard import _agent_progress_rows
from pyclaw.tui.toolui import collapse_kinds
from pyclaw.tools.names import AGENT

logger = logging.getLogger(__name__)


class EventRouterMixin:
    async def _pump(self):
        while True:
            ev = await self._queue.get()
            try:
                await self._handle(ev)
                self._render_status()
                self._render_tasks()
                if self._viewing is not None:
                    await self._render_agent_view()
                await self._refresh_agents()
            except Exception as exc:
                logger.exception(
                    "render failed for %s agent=%r data=%s",
                    ev.kind, ev.agent, _log_data(ev.data))
                try:
                    await self._append_error(
                        f"render error: {exc}")
                except Exception:
                    logger.exception("failed to report a render error")
            finally:
                self._queue.task_done()
    def _on_event(self, ev):
        if ev.agent and ev.agent not in self._member_names():
            if ev.kind != AGENT_PROGRESS:
                return
        self._queue.put_nowait(ev)
    def _member_names(self) -> set:
        return {a.name for a in self._team.agents.values()}
    async def _handle(self, ev):
        logger.debug("event %s agent=%r data=%s", ev.kind, ev.agent,
                     _log_data(ev.data))
        name = ev.agent or ""
        lead = str(getattr(self._team.lead, 'name', ''))
        mine = not name or name == lead
        if ev.kind == AGENT_REASON_START:
            self._note(name, think=True)
            if mine:
                await self._add_think(ev)
        elif ev.kind == AGENT_TEXT:
            self._note(name, think=False, busy=True)
            delta = ev.data.get("delta", "")
            if not delta:
                return
            if not mine:
                return
            if self._live is None:
                if not delta.strip():
                    return
                await self._start_live()
                self._discard_think()
            self._live_text += delta
            self._update_live()
            self._wrote_body = True
            self._reset_tool_group()
        elif ev.kind == AGENT_TOOL_CALL:
            self._note(name, tools=1, think=False)
            if not mine:
                self._note_tool(name, ev.data)
                return
            await self._frozen()
            await self._add_tool(ev)
        elif ev.kind == AGENT_PROGRESS:
            self._note_progress(ev)
        elif ev.kind == AGENT_WARN:
            if not mine:
                self._state(name)['error'] = str(ev.data.get('text', ''))
                return
            await self._frozen()
            await self._append_error(ev.data.get('text', ''))
        elif ev.kind == AGENT_COMPACT:
            await self._frozen()
            block = _SummaryBlock(int(ev.data.get('summarized') or 0),
                                  str(ev.data.get('summary') or ''))
            await self._conv().mount(block)
            await self._after_mount()
        elif ev.kind == AGENT_TOOL_RESULT:
            uid = ev.data.get('tool_use_id') or ev.data.get('tool', '')
            self._tool_meta[uid] = dict(ev.data)
        elif ev.kind == AGENT_TURN_FINISHED:
            self._note(name, think=False, busy=False)
            self._finish_agent(name)
            if not mine:
                return
            if not self._teammates_running():
                self._discard_think()
            self._reset_tool_group()
            await self._frozen()
            self._sync_tool_states()
            self._log_turn()
            asyncio.create_task(self._finish_work_when_settled())
        elif ev.kind == AGENT_STATE:
            self._note(name, busy=bool(ev.data.get("busy", False)))
    def _on_hook_event(self, event) -> None:
        if getattr(event, 'type', '') != 'response':
            return
        outcome = str(getattr(event, 'outcome', '') or 'done')
        line = (f"[dim]{RESULT_PREFIX}hook {escape(str(event.hook_event))}"
                f" \u00b7 {escape(str(event.hook_name))} \u00b7 "
                f"{escape(outcome)}[/]")
        asyncio.create_task(self._append_block(line))
    def _log_turn(self):
        if self._session is None:
            return
        for m in reversed(self._team.transcript()[self._turn_start:]):
            if m.get('role') != 'assistant':
                continue
            text = _content_text(m.get('content'))
            if text:
                append_conv(self._session.conv_session_id, "assistant", text,
                            reasoning_content=m.get('thinking') or None)
            return
    def _note_progress(self, ev):
        name = ev.agent or ""
        data = ev.data
        if data.get('tool_use_id'):
            self._spawns[name] = str(data['tool_use_id'])
            state = self._state(name)
            state['busy'] = True
            state['started_at'] = time.monotonic()
            state['idle_since'] = None
            logger.info("sub-agent %s started (tool_use_id=%s, type=%s)",
                        name, data['tool_use_id'],
                        data.get('subagent_type') or '')
        st = self._subagents.get(name)
        if st is None:
            st = {'type': data.get('subagent_type') or AGENT, 'tools': 0,
                  'tokens': None, 'last_tool': None, 'done': False,
                  'recent': [], 'tool_names': {}}
            self._subagents[name] = st
        if data.get('done'):
            st['done'] = True
            self._finish_agent(name)
            return
        msg = data.get('message')
        if msg is None:
            return
        if msg.get('role') == 'assistant':
            usage = data.get('usage')
            if usage:
                st['tokens'] = int(usage.get('prompt_tokens') or 0) + \
                    int(usage.get('completion_tokens') or 0)
            content = msg.get('content')
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get('type') == 'tool_use':
                        tool = str(b.get('name', 'tool'))
                        st['tool_names'][b.get('id', '')] = tool
                        recent = st['recent'] + [collapse_kinds(
                            tool, b.get('input'))]
                        st['recent'] = recent[-RECENT_ACTIVITIES:]
            block = self._spawn_block(name)
            if block is not None:
                rows, uses = _agent_progress_rows(msg, self._cwd(),
                                                  block._width())
                if rows or uses:
                    block.add_progress(rows, uses)
        elif msg.get('role') == 'user':
            content = msg.get('content')
            if not isinstance(content, list):
                return
            counted = False
            for b in content:
                if isinstance(b, dict) and b.get('type') == 'tool_result':
                    st['tools'] += 1
                    uid = b.get('tool_use_id', '')
                    tname = st['tool_names'].get(uid, 'tool')
                    st['last_tool'] = (f"{tname}: "
                                       f"{_summarize(b.get('content', ''))}")
                    counted = True
            block = self._spawn_block(name) if counted else None
            if block is not None:
                block.redraw()
    async def _drive(self):
        if self._driving:
            return
        self._driving = True
        try:
            while True:
                item = await self._pending_inputs.get()
                self._processing = item.text
                self._render_status()
                await self._render_queued()
                await self._refresh_agents()
                if not item.meta:
                    await self._append_user(item.text)
                self._begin_turn()
                await self._mount_spinner()
                await self._converse(item.text)
                self._session.record_turn()
                await self._settle_paint()
                self._processing = None
                self._render_status()
                await self._render_queued()
                await self._refresh_agents()
        finally:
            self._driving = False
