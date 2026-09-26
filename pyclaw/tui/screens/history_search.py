from __future__ import annotations

import asyncio
from pyclaw.tui.formatting import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from pyclaw.tui import keys
from pyclaw.tui import ui
from textual.screen import Screen
from textual.widgets import Input, Static
from pyclaw.tui.components import _PromptInput


class HistorySearchScreen(Screen):

    BINDINGS = [keys.binding('prev', 'Previous', priority=True),
                keys.binding('next', 'Next', priority=True),
                keys.binding('dismiss', 'Close'),
                Binding('ctrl+c', 'dismiss', 'Close'),
                keys.binding('accept', 'Accept')]

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._selected = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="history"):
            yield Input(placeholder="Search history\u2026", id="hs-input")
            yield Static("", id="hs-list", markup=True)

    def on_mount(self):
        self.query_one("#hs-input", Input).focus()
        self._refresh()

    def _matches(self) -> list[str]:
        query = self.query_one("#hs-input", Input).value
        return [t for t in reversed(self._owner._history)
                if not query or query in t]

    def _refresh(self):
        try:
            widget = self.query_one("#hs-list", Static)
        except Exception:
            return
        matches = self._matches()
        if not matches:
            widget.update("[dim]no matching history[/]")
            return
        self._selected = min(self._selected, len(matches) - 1)
        start = max(0, min(self._selected - 3, len(matches) - 7))
        window = matches[start:start + 7]
        widget.update("\n".join(
            ui.rows(window, self._selected - start)))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._selected = 0
        self._refresh()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        await self._finish(True)

    def action_accept(self):
        asyncio.get_running_loop().create_task(self._finish(False))

    def action_dismiss(self):
        self.app.pop_screen()

    def action_prev(self):
        matches = self._matches()
        if matches:
            self._selected = (self._selected - 1) % len(matches)
            self._refresh()

    def action_next(self):
        matches = self._matches()
        if matches:
            self._selected = (self._selected + 1) % len(matches)
            self._refresh()

    async def _finish(self, execute: bool):
        matches = self._matches()
        if not matches:
            self.app.pop_screen()
            return
        text = matches[self._selected]
        inp = self._owner.query_one("#input", _PromptInput)
        inp.value = text
        inp.cursor_position = len(text)
        self.app.pop_screen()
        if execute:
            inp.submit()
