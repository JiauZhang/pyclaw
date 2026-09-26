
from __future__ import annotations

from pyclaw.tui import keys

import asyncio
import logging

from pyclaw.tui.formatting import escape
from textual.widgets import Static, TextArea

from pyclaw.slash import (handle_slash, skill_rows,
                                suggest as slash_suggest)
from pyclaw.tui.agents_panel import AgentsScreen
from pyclaw.tui.components import PromptSubmitted, _PromptInput
from pyclaw.tui.formatting import _direct_message
from pyclaw.tui.queued import QueuedPrompt
from pyclaw.tui.screens import (DiffScreen, MemoryScreen, PermissionsScreen,
                                RewindScreen, SessionsScreen, TasksScreen)
from pyclaw.tui.suggest import (_apply_at, _at_token, _file_suggest,
                                _suggest_label)
from pyclaw.tui.theme import RESULT_HANG, RESULT_PREFIX

logger = logging.getLogger(__name__)


class PromptMixin:
    async def _run_bash(self, command: str):
        if not command or self._session is None:
            return
        await self._append_user(f'!{command}')
        result = await self._session.run_bash(command)
        body = (result['stdout'] + result['stderr']).rstrip('\n')
        if not body:
            body = '(no output)'
        await self._append_block(
            RESULT_PREFIX + escape(body).replace('\n', '\n' + RESULT_HANG))

    async def on_prompt_submitted(self, event: PromptSubmitted):
        text = event.value.strip()
        self._history_index = None
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        if self._view_selection == 'selecting-agent':
            await self._confirm_selection()
            return
        if self._viewing is not None and text and not text.startswith('/'):
            agent = self._agent_by_name(self._viewing)
            if agent is not None:
                self.query_one("#input", _PromptInput).value = ""
                agent.submit(text)
                await self._render_agent_view()
                self._render_status()
                return
        direct = None if self._viewing is not None else _direct_message(text)
        if direct is not None:
            target = self._agent_by_name(direct[0])
            if target is not None:
                self.query_one("#input", _PromptInput).value = ""
                await self._send_direct(target, direct[1])
                return
        if text.startswith('!') and len(text) > 1:
            self.query_one("#input", _PromptInput).value = ""
            await self._run_bash(text[1:].strip())
            return
        if text == '/permissions':
            self.query_one("#input", _PromptInput).value = ""
            await self._append_user(text)
            self.push_screen(PermissionsScreen(self._session))
            return
        if text == '/rewind':
            self.query_one("#input", _PromptInput).value = ""
            await self._append_user(text)
            if self._processing:
                await self._append_block(escape(
                    f'PyClaw is still working. Press {keys.display("escape")} '
                    'to stop it first, '
                    'then rewind.'))
                return
            if not self._session.turns():
                await self._append_block(escape(
                    'PyClaw has not recorded any turn to go back to.'))
                return
            self.push_screen(RewindScreen(self))
            return
        if text == '/resume':
            self.query_one("#input", _PromptInput).value = ""
            if self._processing:
                await self._append_block(escape(
                    f'PyClaw is still working. Press {keys.display("escape")} '
                    'to stop it first, then change conversation.'))
                return
            await self._append_user(text)
            self.push_screen(SessionsScreen(self))
            return
        if text == '/diff':
            self.query_one("#input", _PromptInput).value = ""
            await self._append_user(text)
            view = self._session.diff()
            if not view['files']:
                await self._append_block(escape(view['text']))
                return
            self.push_screen(DiffScreen(view))
            return
        if text == '/memory':
            self.query_one("#input", _PromptInput).value = ""
            await self._append_user(text)
            cwd = getattr(self._session, 'cwd', None) or '.'
            self.push_screen(MemoryScreen(self, cwd))
            return
        if text in ('/tasks', '/bashes'):
            self.query_one("#input", _PromptInput).value = ""
            await self._append_user(text)
            if not self._task_rows():
                await self._append_block(escape(
                    'No background tasks are running.'))
                return
            self.push_screen(TasksScreen(self))
            return
        if text == '/agents':
            self.query_one("#input", _PromptInput).value = ""
            await self._append_user(text)
            self.push_screen(AgentsScreen(self._session))
            return
        if self._suggest_items:
            item = self._suggest_items[min(self._suggest_selected,
                                           len(self._suggest_items) - 1)]
            if self._typeahead == 'at':
                inp = self.query_one("#input", _PromptInput)
                inp.value = _apply_at(inp.value, item['name'],
                                      item.get('dir', False))
                inp.cursor_position = len(inp.value)
                return
            if item.get('hint'):
                inp = self.query_one("#input", _PromptInput)
                inp.value = f"/{item['name']} "
                inp.cursor_position = len(inp.value)
                self._suggest_items = []
                self._show_suggest_widget(False)
                return
            if item['name'].startswith(text[1:].strip().lower()):
                text = f"/{item['name']}"
        self.query_one("#input", _PromptInput).value = ""
        if not text:
            return
        if text.startswith("/"):

            reply = await handle_slash(text, self._session,
                                       terminal=self._terminal)
            await self._append_user(text)
            follow = None
            if isinstance(reply, tuple):
                reply, follow = reply
            if reply:
                await self._append_block(escape(reply))
            self._render_status()
            await self._render_queued()
            if follow:
                self._pending_inputs.put_nowait(
                    QueuedPrompt(follow, meta=True))
            return
        self._pending_inputs.put_nowait(QueuedPrompt(text))
        self._render_status()
        await self._render_queued()
    def _peek_queue(self) -> list:
        return list(self._pending_inputs._queue)
    def _queued_is_editable(self) -> bool:
        return any(item.editable for item in self._peek_queue())
    def _pop_queued(self) -> bool:
        """Pull the messages a person typed into the input for editing, taking
        them off the queue so they cannot also be sent as they were."""
        items = self._peek_queue()
        editable = [item for item in items if item.editable]
        if not editable:
            return False
        while True:
            try:
                self._pending_inputs.get_nowait()
            except asyncio.QueueEmpty:
                break
        for item in items:
            if not item.editable:
                self._pending_inputs.put_nowait(item)
        inp = self.query_one("#input", _PromptInput)
        inp.value = "\n".join([item.text for item in editable]
                              + ([inp.value] if inp.value else []))
        inp.cursor_position = len(inp.value)
        return True
    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        value = event.text_area.text
        self._render_status()
        if value == '?':
            self.query_one("#input", _PromptInput).value = ""
            self.action_toggle_help()
            return
        items = []
        kind = None
        if value.startswith('/'):
            items = slash_suggest(
                value, skill_rows(self._session) if self._session else ())
            kind = 'slash'
        else:
            token = _at_token(value[:event.text_area.cursor_position])
            if token is not None:
                items = _file_suggest(self._cwd(), token)
                kind = 'at'
        if not items or self._suggest_dismissed == value:
            self._suggest_items = []
            self._typeahead = None
            self._show_suggest_widget(False)
            return
        self._suggest_dismissed = None
        self._suggest_items = items
        self._typeahead = kind
        self._suggest_selected = 0
        self._show_suggest_widget(True)
    def _show_suggest_widget(self, show: bool):
        if show:
            self.register_overlay('autocomplete')
        else:
            self.unregister_overlay('autocomplete')
        try:
            self.query_one('#suggest', Static).display = show
        except Exception:
            pass
        if show:
            self._render_suggestions()
    def _render_suggestions(self):
        try:
            widget = self.query_one('#suggest', Static)
        except Exception:
            return
        items = self._suggest_items
        start = max(0, min(self._suggest_selected - 2, len(items) - 6))
        window = items[start:start + 6]
        labels = [_suggest_label(i) for i in window]
        width = max((len(l) for l in labels), default=0)
        lines = []
        for i, item in enumerate(window):
            index = start + i
            desc = item.get('desc', '')
            row = escape(labels[i].ljust(width) + (f"  {desc}" if desc else ''))
            lines.append(f"[#B1B9F9]{row}[/]" if index == self._suggest_selected
                         else f"[dim]{row}[/]")
        widget.update('\n'.join(lines))
    def action_prompt_prev(self):
        if len(self._suggest_items) > 1:
            self.action_suggest_prev()
            return
        if self._pop_queued():
            self._render_status()
            return
        self._history_step(-1)
    def action_prompt_next(self):
        if self._suggest_items:
            self.action_suggest_next()
            return
        self._history_step(1)
    def _history_step(self, delta: int):
        if not self._history:
            return
        inp = self.query_one("#input", _PromptInput)
        if self._history_index is None:
            if delta > 0:
                return
            self._draft = inp.value
            self._history_index = len(self._history)
        index = min(max(0, self._history_index + delta), len(self._history))
        self._history_index = index
        inp.value = (self._draft if index == len(self._history)
                     else self._history[index])
        inp.cursor_position = len(inp.value)
    def action_suggest_next(self):
        if self._suggest_items:
            self._suggest_selected = ((self._suggest_selected + 1)
                                      % len(self._suggest_items))
            self._render_suggestions()
    def action_suggest_prev(self):
        if self._suggest_items:
            self._suggest_selected = ((self._suggest_selected - 1)
                                      % len(self._suggest_items))
            self._render_suggestions()
    def action_suggest_tab(self):
        if not self._suggest_items:
            return
        item = self._suggest_items[self._suggest_selected]
        inp = self.query_one("#input", _PromptInput)
        if self._typeahead == 'at':
            inp.value = _apply_at(inp.value, item['name'],
                                  item.get('dir', False))
            inp.cursor_position = len(inp.value)
            return
        inp.value = f"/{item['name']} "
        inp.cursor_position = len(inp.value)
    def action_suggest_dismiss(self):
        self._suggest_dismissed = self.query_one("#input", _PromptInput).value
        self._suggest_items = []
        self._typeahead = None
        self._show_suggest_widget(False)
