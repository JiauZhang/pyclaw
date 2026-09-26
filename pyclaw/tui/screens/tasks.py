from __future__ import annotations

from pyclaw.tui.formatting import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.binding import Binding
from pyclaw.tui import ui
from textual.screen import Screen
from pyclaw.tui.screens.task_detail import TaskDetailScreen
from pyclaw.tui import keys
from pyclaw.tui.theme import POINTER
from textual.widgets import Static


class TasksScreen(Screen):

    BINDINGS = [keys.binding('prev', 'Previous'),
                keys.binding('next', 'Next'),
                keys.binding('stop_selected', 'Stop'),
                keys.binding('stop_all', 'Stop all'),
                keys.binding('open', 'Open'),
                keys.binding('dismiss', 'Close'),
                Binding('q', 'dismiss', 'Close'),
                Binding('ctrl+c', 'dismiss', 'Close')]

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
        lines = ui.heading('Background tasks')
        if not rows:
            lines.append(ui.aside('nothing running'))
        labels = [row['label'] + ' · ' + row['detail']
                  + ('' if row['stoppable'] else '  (already finished)')
                  for row in rows]
        lines += ui.rows(labels, self._selected)
        lines += ui.footer(('stop_selected', 'stops the selected'),
                           ('stop_all', 'stops them all'), ('open', 'views'),
                           ('dismiss', 'closes'))
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
            self.app.push_screen(TaskDetailScreen(self._owner, row))
            return
        self.app.pop_screen()
        await self._owner._view_teammate(row['id'])

    def action_dismiss(self):
        self.app.pop_screen()
