from __future__ import annotations

import asyncio
import logging
import random
import time

from rich.markup import escape
from textual.containers import VerticalScroll
from textual.widgets import Static

from pyclaw.spinner_verbs import PAST_TENSE_VERBS, SPINNER_VERBS
from pyclaw.tui.agent_markup import agent_view_markup
from pyclaw.tui.plan import next_task_line
from pyclaw.tui.readout import _agent_tokens
from pyclaw.tui.roster import hide_row, leader_row, preview_rows, teammate_row
from pyclaw.tui.theme import (AGENT_COLORS, AGENT_TEAMMATES_HINT, ASTERISK,
                              IDLE_TEXT)
from pyclaw.tui.collapse import recent_rollup
from pyclaw.tui.components import _AgentPane

logger = logging.getLogger(__name__)


def _agent_alive(agent) -> bool:
    flag = getattr(agent, 'is_running', None)
    if flag is None:
        return True
    return bool(flag)


class RosterMixin:
    def _alive_teammates(self) -> list:
        team = self._team
        if team is None:
            return []
        lead = getattr(team, 'lead', None)
        found = []
        for agent in getattr(team, 'agents', {}).values():
            if agent is lead or getattr(agent, '_internal', False):
                continue
            if _agent_alive(agent):
                found.append(agent)
        return found
    def _teammates(self) -> list:
        found = self._alive_teammates()
        alive = {str(getattr(a, 'name', '')) for a in found}
        self._prune_lingering(time.monotonic())
        for name, (agent, _) in self._lingering.items():
            if name not in alive:
                found.append(agent)
        found.sort(key=lambda a: str(getattr(a, 'name', '')))
        return found
    def _agent_by_name(self, name):
        for agent in getattr(self._team, 'agents', {}).values():
            if getattr(agent, 'name', '') == name:
                return agent
        return None
    def _completion_verb(self) -> str:
        return random.choice(PAST_TENSE_VERBS)
    def _state(self, name: str) -> dict:
        state = self._agent_state.get(name)
        if state is None:
            state = {'tools': 0, 'think': False, 'busy': False,
                     'last_tool': '', 'error': '', 'recent': [],
                     'verb': random.choice(SPINNER_VERBS),
                     'past': self._completion_verb(),
                     'started_at': time.monotonic(), 'idle_since': None}
            self._agent_state[name] = state
        return state
    def _agent_color(self, name: str) -> str:
        color = self._agent_colors.get(name)
        if color is None:
            index = len(self._agent_colors) % len(AGENT_COLORS)
            color = AGENT_COLORS[index]
            self._agent_colors[name] = color
        return color
    def _agent_running(self, agent) -> bool:
        if agent is None:
            return False
        return bool(getattr(agent, 'busy', False))
    def _plan(self) -> list:
        return [] if self._team.tasks is None else self._team.tasks.all()
    def _plan_activity(self) -> dict:
        running = {}
        for agent in self._teammates():
            if not self._agent_running(agent):
                continue
            state = self._state(str(getattr(agent, 'name', '')))
            text = recent_rollup(state['recent']) or state['last_tool']
            if text:
                running[str(agent.name)] = text
        return running
    def _active_forms(self) -> dict:
        return {task.owner: task.active_form for task in self._plan()
                if task.status == 'in_progress' and task.active_form}
    def _tree_markup(self, rows: bool, idle_line: bool) -> str:
        teammates = self._teammates()
        if not teammates:
            return ''
        selecting = self._view_selection == 'selecting-agent'
        selected = self._selected_index if selecting else None
        all_idle = all(not self._agent_running(a) for a in teammates)
        now = time.monotonic()
        columns = self.screen.size.width or self.size.width
        awaiting = {approval.prompt._agent for approval in self._approvals
                    if approval.prompt._agent}
        work = self._active_forms()
        lines = []
        if idle_line:
            suffix = '' if all_idle else f" \u00b7 {AGENT_TEAMMATES_HINT}"
            lines.append(f"[dim]{ASTERISK} {IDLE_TEXT}{suffix}[/]")
        if rows:
            lines.append(leader_row(
                selected=selected, foreground=self._viewing is None,
                busy=self._turn_verb if self._processing is not None else None,
                tokens=_agent_tokens(self._team.lead), columns=columns))
            for index, agent in enumerate(teammates):
                name = str(getattr(agent, 'name', ''))
                chosen = selected == index
                last = index == len(teammates) - 1 and not selecting
                lines.append(teammate_row(
                    agent, self._state(name), running=self._agent_running(agent),
                    color='#B1B9F9' if chosen else self._agent_color(name),
                    chosen=chosen, last=last, all_idle=all_idle, now=now,
                    columns=columns, foregrounded=self._viewing == name,
                    stopping=bool(self._state(name).get('stopping')),
                    stopped=name in self._lingering,
                    awaiting=name in awaiting,
                    queued=len(agent.inbox.unread()),
                    work=work.get(name, '')))
                if self._preview:
                    lines += preview_rows(agent, last=last, cwd=self._cwd())
            if selecting:
                lines.append(hide_row(selected == len(teammates)))
        return '\n'.join(lines)
    async def _refresh_agents(self):
        self._note_lingering()
        self._schedule_linger()
        known = {str(getattr(a, 'name', ''))
                 for a in getattr(self._team, 'agents', {}).values()}
        for name in list(self._agent_state):
            if name not in known and name not in self._lingering:
                self._agent_state.pop(name, None)
        for name in list(self._spawns):
            if name not in known:
                self._spawns.pop(name, None)
        if self._agent_group is not None:
            # A teammate that left must close its row now, not on the next
            # spinner tick.
            self._agent_group.redraw()
        if self._viewing is not None:
            viewed = self._agent_by_name(self._viewing)
            if viewed is None or not _agent_alive(viewed):
                await self._exit_agent_view()
        teammates = self._teammates()
        rows = bool(teammates) and self._expanded_view == 'teammates'
        idle_line = bool(teammates) and self._processing is None
        text = self._tree_markup(rows, idle_line) or next_task_line(
            self._plan())
        if not text:
            if self._agents_pane is not None:
                self._agents_pane.remove()
                self._agents_pane = None
            return
        if self._agents_pane is None or self._agents_pane.parent is None:
            self._agents_pane = _AgentPane()
            await self.screen.mount(self._agents_pane,
                                    before=self.query_one("#prompt"))
        self._agents_pane.display = True
        self._agents_pane.update(text)
    def _step_selection(self, delta: int):
        teammates = self._teammates()
        if not teammates:
            return
        if self._expanded_view != 'teammates':
            self._selected_index = -1
        else:
            last = len(teammates)
            current = self._selected_index
            if delta == 1:
                self._selected_index = -1 if current >= last else current + 1
            else:
                self._selected_index = last if current <= -1 else current - 1
        self._view_selection = 'selecting-agent'
        self._expanded_view = 'teammates'
    async def action_agent_next(self):
        self._step_selection(1)
    async def action_agent_prev(self):
        self._step_selection(-1)
    async def action_agent_preview(self):
        self._preview = not self._preview
        await self._refresh_agents()
    async def _confirm_selection(self):
        teammates = self._teammates()
        index = self._selected_index
        if index == -1:
            await self._exit_agent_view()
        elif index >= len(teammates):
            self._expanded_view = 'none'
            self._view_selection = 'none'
            self._selected_index = -1
        else:
            await self._enter_agent_view(teammates[index])
        self._render_status()
        await self._refresh_agents()
    async def _view_teammate(self, name: str):
        agent = self._agent_by_name(name)
        if agent is None:
            return
        await self._enter_agent_view(agent)
    async def _enter_agent_view(self, agent):
        self._viewing = str(getattr(agent, 'name', ''))
        self._view_selection = 'viewing-agent'
        self.query_one("#conv").display = False
        view = self.query_one("#view", VerticalScroll)
        view.display = True
        await self._render_agent_view()
        view.scroll_end(animate=False)
        await self._render_queued()
        self._render_status()
    async def _exit_agent_view(self):
        if self._viewing is None:
            return
        self._viewing = None
        self._view_selection = 'none'
        self._selected_index = -1
        self.query_one("#view", VerticalScroll).display = False
        self.query_one("#conv").display = True
        self._follow_scroll()
        await self._render_queued()
        self._render_status()
    async def _render_agent_view(self):
        if self._viewing is None:
            return
        markup = agent_view_markup(self._agent_by_name(self._viewing),
                                   cwd=self._cwd(),
                                   color_for=self._agent_color)
        view = self.query_one("#view", VerticalScroll)
        if self._view_pane is None or self._view_pane.parent is None:
            self._view_pane = Static(markup, markup=True)
            await view.mount(self._view_pane)
        else:
            self._view_pane.update(markup)
    async def action_stop_agent(self):
        teammates = self._teammates()
        index = self._selected_index
        if index < 0 or index >= len(teammates):
            return
        agent = teammates[index]
        name = str(getattr(agent, 'name', ''))
        logger.info("stopping teammate %s by user request", name)
        if self._viewing == name:
            await self._exit_agent_view()
        self._state(name)['stopping'] = True
        await self._refresh_agents()
        stop = getattr(self._team, 'stop_agent', None)
        if stop is not None:
            await stop(agent)
        self._finish_agent(name, stopped=True)
        self._selected_index = -1
        await self._refresh_agents()
    async def _send_direct(self, agent, message: str):
        lead = getattr(self._team, 'lead', None)
        sender = str(getattr(lead, 'name', 'team-lead'))
        agent.inbox.write(sender, message)
        logger.info("direct message %s -> @%s", sender,
                    getattr(agent, 'name', ''))
        await self._append_block(
            f"[#B1B9F9]Sent to @{escape(str(agent.name))}[/]")
