from __future__ import annotations

import asyncio

from textual.widgets import Input

from pyclaw.tui.screens import (HistorySearchScreen, HelpScreen,
                                TranscriptScreen, WorktreeExitScreen)
from pyclaw.tui.theme import INTERRUPTED_TEXT


class ActionMixin:

    async def action_escape(self):
        if self._suggest_items:
            self.action_suggest_dismiss()
            return
        if self._viewing is not None:
            agent = self._agent_by_name(self._viewing)
            if agent is not None and self._agent_running(agent):
                agent.abort_work()
                await self._render_agent_view()
                return
            await self._exit_agent_view()
            await self._refresh_agents()
            return
        if self._view_selection == 'selecting-agent':
            self._view_selection = 'none'
            self._selected_index = -1
            await self._refresh_agents()
            return
        if self._processing is None:
            return
        await self._interrupt()
    async def action_interrupt(self):
        now = asyncio.get_running_loop().time()
        if self._processing is None and now - self._last_interrupt < 2.0:
            self.exit()
            return
        self._last_interrupt = now
        await self._interrupt()
    async def _interrupt(self):
        rejected = await self._deny_pending_permission()
        if self._team is not None:
            self._team.lead.abort_work()
        if self._processing and not self._wrote_body:
            if self._typed_by_user:
                self.query_one("#input", Input).value = self._processing
            self._processing = None
        if not rejected:
            await self._append_block(
                f"[dim]{INTERRUPTED_TEXT}[/]")
    def action_redraw(self):
        self.refresh()
    def action_toggle_transcript(self):
        self.push_screen(TranscriptScreen(self))
    def action_history_search(self):
        if self._history:
            self.push_screen(HistorySearchScreen(self))
    def action_stash(self):
        inp = self.query_one("#input", Input)
        text = inp.value
        if text.strip():
            self._stashed = text
            inp.value = ""
        elif self._stashed is not None:
            inp.value = self._stashed
            inp.cursor_position = len(self._stashed)
            self._stashed = None
    async def action_toggle_thinking(self):
        if self._session is None:
            return
        self._session.toggle_thinking()
        await self._session.note_config_change('settings')
        self._render_status()
    def action_toggle_help(self):
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return
        self.push_screen(HelpScreen())
    async def action_quit(self):
        if self._spin_timer is not None:
            self._spin_timer.stop()
        session = self._session
        worktree = session.worktree if session is not None else None
        if worktree is not None:
            changes = session.worktree_changes()
            if changes is None or changes['files'] or changes['commits']:
                self.push_screen(WorktreeExitScreen(self, changes))
                return
            self._exit_note = await session.leave_worktree(keep=False)
        self.exit()
