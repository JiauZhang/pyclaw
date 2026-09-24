from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from pyclaw.tui.theme import POINTER
from textual.widgets import Static


class TasksScreen(Screen):

    BINDINGS = [("up", "prev", "Previous"),
                ("down", "next", "Next"),
                ("x", "stop_selected", "Stop"),
                ("a", "stop_all", "Stop all"),
                ("enter", "open", "Open"),
                ("escape", "dismiss", "Close"),
                ("q", "dismiss", "Close"),
                ("ctrl+c", "dismiss", "Close")]

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._selected = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="tasks-panel"):
            yield Static("", id="tk-body", markup=True)

    def on_mount(self):
        self.query_one("#tasks-panel", VerticalScroll).focus()
        self._draw()

    def _draw(self):
        rows = self._owner._task_rows()
        self._selected = max(0, min(self._selected, len(rows) - 1))
        lines = ['[bold]Background tasks[/bold]']
        if not rows:
            lines.append('[dim]nothing running[/]')
        for index, row in enumerate(rows):
            marker = POINTER if index == self._selected else ' '
            text = f'{marker} {row["label"]} · {row["detail"]}'
            if not row['stoppable']:
                text += '  (already finished)'
            escaped = escape(text)
            lines.append(f'[#B1B9F9]{escaped}[/]' if index == self._selected
                         else f'  [dim]{escaped}[/]')
        lines.append('[dim]x stops the selected · a stops them all · '
                     'enter views · esc closes[/]')
        self.query_one("#tk-body", Static).update('\n'.join(lines))

    def _move(self, delta: int):
        self._selected += delta
        self._draw()

    def action_next(self):
        self._move(1)

    def action_prev(self):
        self._move(-1)

    async def action_stop_selected(self):
        rows = self._owner._task_rows()
        if not rows:
            return
        row = rows[min(self._selected, len(rows) - 1)]
        if not row['stoppable']:
            return
        note = await self._owner._stop_task_row(row)
        self._selected = 0
        self._draw()
        if note:
            await self._owner._append_note(note)

    async def action_stop_all(self):
        rows = self._owner._task_rows()
        stopped = 0
        for row in rows:
            if not row['stoppable']:
                continue
            await self._owner._stop_task_row(row)
            stopped += 1
        self._selected = 0
        self._draw()
        await self._owner._append_note(
            f'Stopped {stopped} of {len(rows)} tasks.' if rows
            else 'Nothing was running.')

    async def action_open(self):
        rows = self._owner._task_rows()
        if not rows:
            return
        row = rows[min(self._selected, len(rows) - 1)]
        if row['kind'] != 'teammate':
            return
        self.app.pop_screen()
        await self._owner._view_teammate(row['id'])

    def action_dismiss(self):
        self.app.pop_screen()
