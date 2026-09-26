from __future__ import annotations

import time

from pyclaw.tui.formatting import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from pyclaw.session import store as session_store
from pyclaw.tui import keys, ui
from pyclaw.tui.formatting import _plural


LISTED = 7


def _ago(seconds: float) -> str:
    if seconds < 90:
        return 'just now'
    if seconds < 5400:
        return f'{int(seconds // 60)}m ago'
    if seconds < 86400:
        return f'{int(seconds // 3600)}h ago'
    return f'{int(seconds // 86400)}d ago'


class SessionsScreen(Screen):

    BINDINGS = [keys.binding('prev', 'Previous', priority=True),
                keys.binding('next', 'Next', priority=True),
                keys.binding('accept', 'Continue'),
                keys.binding('dismiss', 'Close'),
                Binding('ctrl+c', 'dismiss', 'Close')]

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._sessions = session_store.list_sessions()
        self._selected = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="sessions"):
            yield Input(placeholder='Search conversations…', id="ss-input")
            yield Static('', id='ss-list', markup=True)

    def on_mount(self):
        self.query_one('#ss-input', Input).focus()
        self._refresh()

    def _label(self, item: dict) -> str:
        return (item['title'] or item['id'][:8])

    def _matches(self) -> list[dict]:
        query = self.query_one('#ss-input', Input).value.strip().lower()
        return [item for item in self._sessions
                if not query
                or query in self._label(item).lower()
                or query in item['id'].lower()]

    def _row(self, item: dict) -> str:
        meta = ' · '.join((_ago(max(0.0, time.time() - item['modified'])),
                           _plural(item['messages'], 'message')))
        return f'{escape(self._label(item))}  [dim]{meta}[/]'

    def _refresh(self):
        try:
            widget = self.query_one('#ss-list', Static)
        except Exception:
            return
        matches = self._matches()
        if not matches:
            widget.update('[dim]no saved conversation matches[/]')
            return
        self._selected = min(self._selected, len(matches) - 1)
        start = max(0, min(self._selected - 2, len(matches) - LISTED))
        widget.update('\n'.join(
            ui.rows([self._row(item) for item
                     in matches[start:start + LISTED]],
                    self._selected - start)))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._selected = 0
        self._refresh()

    def _move(self, delta: int):
        self._selected = max(0, min(self._selected + delta,
                                    len(self._matches()) - 1))
        self._refresh()

    def action_next(self):
        self._move(1)

    def action_prev(self):
        self._move(-1)

    async def action_accept(self):
        matches = self._matches()
        if not matches:
            return
        item = matches[min(self._selected, len(matches) - 1)]
        self.app.pop_screen()
        await self._owner._apply_resume(item['id'],
                                   self._label(item))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        await self.action_accept()

    def action_dismiss(self):
        self.app.pop_screen()
