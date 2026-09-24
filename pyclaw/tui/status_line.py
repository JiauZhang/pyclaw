from __future__ import annotations

import asyncio
import logging
import time

from rich.markup import escape
from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import Input, Static

from pyclaw import banner, statusline
from pyclaw.tools.coding import background
from pyclaw.tui.formatting import (_display_cwd, _fit, _format_count,
                                   _token_rate, duration)
from pyclaw.tui.readout import (_agent_tokens, context_meter, context_note,
                                elapsed_row, git_label, git_status,
                                message_count, meter_cells, mode_pill,
                                thinking_label, usage_hud, usage_meter)
from pyclaw.tui.theme import (AGENT_TEAMMATES_HINT, ASTERISK, IDLE_TEXT,
                              TEAMMATE_VIEW_HINT)
from pyclaw.tui.toolcard import _last_assistant_key

logger = logging.getLogger(__name__)

class StatusMixin:
    def _spinner_text(self, char: str) -> str:
        viewed = self._viewing
        if viewed is not None:
            if self._agent_running(self._agent_by_name(viewed)):
                verb = self._state(viewed)['verb']
                return (f"[{self.brand}]{char}[/] {escape(verb)}\u2026 "
                        f"[dim](esc stops the turn [/]"
                        f"[{self._agent_color(viewed)}]@{escape(viewed)}[/]"
                        f"[dim])[/]")
            return self._idle_row(self._state(viewed))
        elif self._processing is None and self._teammates_running():
            return self._idle_row()
        parts = []
        running = self._teammates_running()
        elapsed = self._elapsed_seconds()
        parts.append(duration(elapsed))
        tokens = self._turn_tokens(running=running)
        if tokens:
            arrow = '' if running else '\u2193 '
            parts.append(f"{arrow}{_format_count(tokens)} tokens")
            if elapsed:
                parts.append(_token_rate(tokens, elapsed))
        if self._leader_thinking():
            parts.append('thinking')
        head = f"[{self.brand}]{char}[/] {self._turn_verb}\u2026"
        if not parts:
            return head
        return (f"{head} [dim]([/]"
                + "[dim] \u00b7 [/]".join(parts) + "[dim])[/]")
    def _idle_row(self, state: dict | None = None) -> str:
        if state is None:
            return (f"[dim]{ASTERISK} {IDLE_TEXT} \u00b7 "
                    f"{AGENT_TEAMMATES_HINT}[/]")
        teammates = self._teammates()
        if teammates and all(not self._agent_running(a) for a in teammates):
            seconds = max(0, int(time.monotonic() - state['started_at']))
            return (f"[dim]{ASTERISK} {state['past']} for "
                    f"{duration(seconds)}[/]")
        return f"[dim]{ASTERISK} {IDLE_TEXT}[/]"
    def _elapsed_seconds(self) -> int:
        if not self._turn_started_at:
            return 0
        return max(0, int(time.monotonic() - self._turn_started_at))
    def _turn_tokens(self, *, running: bool) -> int:
        tokens = max(0, _agent_tokens(self._team.lead) - self._turn_usage)
        if running and self._expanded_view != 'teammates':
            for agent in self._teammates():
                if self._agent_running(agent):
                    tokens += _agent_tokens(agent)
        return tokens
    def _leader_thinking(self) -> bool:
        return bool(self._state(str(self._team.lead.name)).get('think'))
    @staticmethod
    def _duration(seconds: int) -> str:
        if seconds < 60:
            return f'{seconds}s'
        return f'{seconds // 60}m {seconds % 60}s'
    async def _render_queued(self):
        inp = self.query_one("#input", Input)
        queued = [] if self._viewing is not None else self._peek_queue()
        inp.placeholder = ("up edits what you queued" if queued
                           else "Message PyClaw\u2026")
        if not queued:
            if self._queued is not None:
                self._queued.remove()
                self._queued = None
            return
        text = "\n\n".join(escape(str(t)) for t in queued)
        if self._queued is None:
            self._queued = Static(text, markup=True, classes="user")
            await self.screen.mount(self._queued,
                                    before=self.query_one("#prompt"))
        else:
            self._queued.update(text)
    def _peek_queue(self) -> list:
        return list(self._pending_inputs._queue)
    def _note(self, name, tools=None, think=None, busy=None):
        st = self._state(name)
        if tools:
            st["tools"] += tools
        if think is not None:
            st["think"] = think
        if busy is not None:
            st["busy"] = busy
    def _render_status(self):
        s = self._session
        if s is None or not self.is_running:
            return
        self._schedule_statusline()
        viewing = self._viewing is not None
        viewing_busy = viewing and self._agent_running(
            self._agent_by_name(self._viewing))
        parts = []
        show_hint = not self._statusline_cmd and not self._prompt_has_text()
        if show_hint:
            if viewing and not viewing_busy:
                parts.append(TEAMMATE_VIEW_HINT)
            else:
                if self._processing is not None or viewing_busy:
                    parts.append("esc stops the turn")
                hint = self._tasks_hint()
                if hint:
                    parts.append(hint)
        if show_hint and not parts:
            parts.append("? lists the keys")
        self.query_one("#status", Static).update(" \u00b7 ".join(parts))
        self._paint_prompt()
        self._render_readouts()
    def _render_readouts(self):
        s = self._session
        if s is None or not self.is_running:
            return
        width = self.screen.size.width or self.size.width
        cells = meter_cells(width)
        background = (self._viewing is not None or self._teammates_running()
                      or self._subagents_running())
        self.query_one("#status-right", Static).update(context_note(s))
        self.query_one("#hud", Static).update(_fit((
            s.model, thinking_label(s),
            context_meter(self._triple, s.used_context, s.context_window,
                          cells),
            usage_meter(self._triple, s.usage, cells), usage_hud(s.usage),
            message_count(s), elapsed_row(self._hud_started)), width - 2))
        self.query_one("#hud2", Static).update(_fit((
            _display_cwd(s.cwd), self._git,
            mode_pill(s, background=background)), width - 2))
    async def _refresh_git(self):
        cwd = self._cwd()
        status = await asyncio.to_thread(git_status, cwd)
        if cwd == self._cwd():
            self._git = git_label(status)
    def _prompt_has_text(self) -> bool:
        return bool(self.query_one("#input", Input).value)
    def _statusline_state(self) -> tuple:
        s = self._session
        return (_last_assistant_key(s.transcript()), s.permission_mode,
                s.model, statusline.user_command())
    def _schedule_statusline(self):
        state = self._statusline_state()
        if state == self._statusline_seen:
            return
        self._statusline_seen = state
        if self._statusline_timer is not None:
            self._statusline_timer.stop()
        self._statusline_timer = self.set_timer(
            statusline.STATUS_LINE_DEBOUNCE_SECONDS,
            self._refresh_statusline)
    async def _refresh_statusline(self):
        self._statusline_timer = None
        self._statusline_cmd = statusline.user_command()
        if not self._statusline_cmd:
            self._statusline_text = ""
            self._paint_statusline()
            self._render_status()
            return
        if self._session is None:
            return
        previous = self._statusline_task
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(
            statusline.run(self._session, self._statusline_cmd))
        self._statusline_task = task
        try:
            text = await task
        except (asyncio.CancelledError, Exception):
            return
        if self._statusline_task is not task:
            return
        self._statusline_text = text
        self._paint_statusline()
        self._render_status()
    def _paint_statusline(self):
        if not self.is_running:
            return
        widget = self.query_one("#statusline", Static)
        widget.display = bool(self._statusline_text)
        widget.update(Text.from_ansi(self._statusline_text, style="dim",
                                     no_wrap=True))
    def _subagents_running(self) -> bool:
        return any(not st['done'] for st in self._subagents.values())
    def _paint_prompt(self):
        viewed = self._viewing
        accent = (self._agent_color(viewed) if viewed is not None
                  else banner.dimmed(self._triple, banner.RULE_LIGHTNESS))
        frame = self.query_one("#prompt", Horizontal)
        frame.styles.border_top = ("round", accent)
        frame.styles.border_bottom = ("round", accent)
        pointer = self.query_one("#prompt-pointer", Static)
        pointer.styles.color = accent if viewed is not None else self.brand
        pointer.styles.text_style = "dim" if self._processing else "none"
