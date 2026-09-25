from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from pyclaw.tui import keys
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static

from pyclaw.tui.task_panel import task_detail_lines


class TaskDetailScreen(Screen):

    BINDINGS = [keys.binding('dismiss', 'Close'),
                Binding('q', 'dismiss', 'Close'),
                Binding('ctrl+c', 'dismiss', 'Close'),
                keys.binding('stop_selected', 'Stop')]

    def __init__(self, owner, row, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._row = dict(row)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="task-detail"):
            yield Static("", id="td-body", markup=True)

    def on_mount(self):
        self.query_one("#task-detail", VerticalScroll).focus()
        self._draw()
        if self._row.get('stoppable'):
            self.set_interval(1.0, self._refresh)

    def _draw(self):
        data = self._owner._task_detail(self._row)
        lines = task_detail_lines(data['row'], output=data['output'],
                                  state=data['state'],
                                  subagent=data['subagent'], now=data['now'])
        lines.append('')
        closing = keys.hint('dismiss', 'closes')
        lines.append('[dim]' + keys.hints(
            ('stop_selected', 'stops it'), ('dismiss', 'closes')) + '[/]'
            if data['row'].get('stoppable')
            else f'[dim]{closing}[/]')
        self.query_one("#td-body", Static).update('\n'.join(lines))

    def _refresh(self):
        self._draw()

    async def action_stop_selected(self):
        if not self._row.get('stoppable'):
            return
        note = await self._owner._stop_task_row(self._row)
        self._draw()
        if note:
            await self._owner._append_note(note)

    def action_dismiss(self):
        self.app.pop_screen()
