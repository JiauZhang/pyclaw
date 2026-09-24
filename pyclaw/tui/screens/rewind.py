from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from pyclaw.tui.formatting import _plural, _summarize
from pyclaw.tui.theme import POINTER
from textual.widgets import Static


class RewindScreen(Screen):

    BINDINGS = [("up", "prev", "Previous"),
                ("down", "next", "Next"),
                ("enter", "choose", "Choose"),
                ("escape", "dismiss", "Back"),
                ("q", "dismiss", "Back"),
                ("ctrl+c", "dismiss", "Back")]

    MODES = (('both', 'Restore code and conversation'),
             ('conversation', 'Restore conversation'),
             ('code', 'Restore code'),
             ('never', 'Never mind'))

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._turns = list(owner._session.turns())
        self._turn = max(0, len(self._turns) - 1)
        self._mode = 0
        self._choosing = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="rewind"):
            yield Static("", id="rw-body", markup=True)

    def on_mount(self):
        self.query_one("#rewind", VerticalScroll).focus()
        self._draw()

    @staticmethod
    def _stats(stats) -> str:
        files = len((stats or {}).get('files') or [])
        if not files:
            return ''
        return (f' · {_plural(files, "file")}'
                f' · +{(stats or {}).get("insertions", 0)}'
                f' -{(stats or {}).get("deletions", 0)}')

    def _rows(self) -> tuple[list, int]:
        if not self._choosing:
            rows = ['Go back to which turn?']
            for index, (mark, prompt) in enumerate(self._turns):
                row = f'{index + 1}. {_summarize(prompt, 60)}'
                if index == self._turn:
                    row += self._stats(self._owner._session.rewind_stats(mark))
                rows.append(row)
            return rows, self._turn + 1
        _mark, prompt = self._turns[self._turn]
        rows = [f'Back to: {self._turn + 1}. {_summarize(prompt, 60)}']
        rows.extend(label for _key, label in self.MODES)
        return rows, self._mode + 1

    def _draw(self):
        rows, selected = self._rows()
        lines = []
        for index, row in enumerate(rows):
            text = escape(row)
            if index == selected:
                lines.append(f'[#B1B9F9]{POINTER} {text}[/]')
            elif index == 0:
                lines.append(f'[bold]{text}[/bold]')
            else:
                lines.append(f'  [dim]{text}[/]')
        lines.append('[dim]enter chooses · esc backs out[/]')
        self.query_one("#rw-body", Static).update('\n'.join(lines))

    def _move(self, delta: int):
        if self._choosing:
            self._mode = max(0, min(self._mode + delta, len(self.MODES) - 1))
        else:
            self._turn = max(0, min(self._turn + delta, len(self._turns) - 1))
        self._draw()

    def action_next(self):
        self._move(1)

    def action_prev(self):
        self._move(-1)

    async def action_choose(self):
        if not self._choosing:
            self._choosing = True
            self._mode = 0
            self._draw()
            return
        key = self.MODES[self._mode][0]
        mark = self._turns[self._turn][0]
        self.app.pop_screen()
        if key != 'never':
            await self._owner._apply_rewind(
                mark, code=key != 'conversation',
                conversation=key != 'code')

    def action_dismiss(self):
        self.app.pop_screen()
