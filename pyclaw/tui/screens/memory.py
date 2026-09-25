from __future__ import annotations

import contextlib

from rich.markup import escape
from textual.app import ComposeResult, SuspendNotSupported
from textual.containers import VerticalScroll
from textual.screen import Screen
from pyclaw.tui.theme import POINTER
from textual.widgets import Static


class MemoryScreen(Screen):

    BINDINGS = [("up", "prev", "Previous"),
                ("down", "next", "Next"),
                ("enter", "open", "Edit"),
                ("escape", "dismiss", "Close"),
                ("q", "dismiss", "Close"),
                ("ctrl+c", "dismiss", "Close")]

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
        from pyclaw.agent_memory import memory_targets

        return memory_targets(self._cwd)

    def _draw(self):
        rows = self._targets()
        self._selected = max(0, min(self._selected, len(rows) - 1))
        lines = ['[bold]Memory files[/bold]']
        for index, (scope, path) in enumerate(rows):
            marker = POINTER if index == self._selected else ' '
            state = '' if path.exists() else '  (not created yet)'
            text = escape(f'{marker} {scope} · {path}{state}')
            lines.append(f'[#B1B9F9]{text}[/]' if index == self._selected
                         else f'  [dim]{text}[/]')
        lines.append('[dim]enter edits the selected file · esc closes[/]')
        self.query_one("#mm-body", Static).update('\n'.join(lines))

    def _move(self, delta: int):
        self._selected += delta
        self._draw()

    def action_next(self):
        self._move(1)

    def action_prev(self):
        self._move(-1)

    async def action_open(self):
        from pyclaw.editor import open_file

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
            editor = open_file(path)
        self.app.pop_screen()
        await self._owner._append_note(self._note(path, editor))

    def _note(self, path, editor: str) -> str:
        if not editor:
            return (f'No editor found, so {path} was not opened. Set $VISUAL '
                    f'or $EDITOR and try again.')
        return (f'Opened {path} in {editor}. To use a different editor, set '
                f'$VISUAL or $EDITOR.')

    def action_dismiss(self):
        self.app.pop_screen()
