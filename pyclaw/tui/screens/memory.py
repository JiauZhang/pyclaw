from __future__ import annotations

import contextlib

from rich.markup import escape
from textual.app import ComposeResult, SuspendNotSupported
from textual.containers import VerticalScroll
from textual.binding import Binding
from pyclaw.tui import ui
from textual.screen import Screen
from pyclaw.tui import keys
from pyclaw.tui.theme import POINTER
from textual.widgets import Static
from pyclaw.agent_memory import memory_targets
from pyclaw import editor


class MemoryScreen(Screen):

    BINDINGS = [keys.binding('prev', 'Previous'),
                keys.binding('next', 'Next'),
                keys.binding('open', 'Edit'),
                keys.binding('dismiss', 'Close'),
                Binding('q', 'dismiss', 'Close'),
                Binding('ctrl+c', 'dismiss', 'Close')]

    def __init__(self, owner, cwd: str, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._cwd = cwd
        self._selected = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="memory-panel"):
            yield Static("", id="mm-body", markup=True)

    def on_mount(self):
        self.query_one("#memory-panel", VerticalScroll).focus()
        self._draw()

    def _targets(self):

        return memory_targets(self._cwd)

    def _draw(self):
        rows = self._targets()
        self._selected = max(0, min(self._selected, len(rows) - 1))
        lines = ui.heading('Memory files')
        labels = [f'{scope} · {path}'
                  + ('' if path.exists() else '  (not created yet)')
                  for scope, path in rows]
        lines += ui.rows(labels, self._selected)
        lines += ui.footer(('open', 'edits the selected file'),
                           ('dismiss', 'closes'))
        self.query_one("#mm-body", Static).update('\n'.join(lines))

    def _move(self, delta: int):
        self._selected += delta
        self._draw()

    def action_next(self):
        self._move(1)

    def action_prev(self):
        self._move(-1)

    async def action_open(self):

        rows = self._targets()
        if not rows:
            return
        _scope, path = rows[min(self._selected, len(rows) - 1)]
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text('', encoding='utf-8')
        with contextlib.ExitStack() as stack:
            try:
                stack.enter_context(self.app.suspend())
            except SuspendNotSupported:
                pass
            opened_with = editor.open_file(path)
        self.app.pop_screen()
        await self._owner._append_note(self._note(path, opened_with))

    def _note(self, path, opened_with: str) -> str:
        if not opened_with:
            return (f'No editor found, so {path} was not opened. Set $VISUAL '
                    f'or $EDITOR and try again.')
        return (f'Opened {path} in {opened_with}. To use a different editor, '
                f'set $VISUAL or $EDITOR.')

    def action_dismiss(self):
        self.app.pop_screen()
