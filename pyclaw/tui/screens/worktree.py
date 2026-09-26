from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from pyclaw.tui import keys, ui
from pyclaw.tui.formatting import _plural, escape


CHOICES = (('keep', 'Keep it'),
           ('remove', 'Remove it'))


class WorktreeExitScreen(Screen):

    BINDINGS = [keys.binding('prev', 'Previous', priority=True),
                keys.binding('next', 'Next', priority=True),
                keys.binding('choose', 'Choose'),
                keys.binding('dismiss', 'Stay'),
                Binding('q', 'dismiss', 'Stay'),
                Binding('ctrl+c', 'dismiss', 'Stay')]

    def __init__(self, owner, changes: dict | None, **kw):
        super().__init__(**kw)
        self._owner = owner
        self._changes = changes
        self._choice = 0

    def compose(self) -> ComposeResult:
        with VerticalScroll(id='worktree-exit'):
            yield Static('', id='we-body', markup=True)

    def on_mount(self):
        self.query_one('#worktree-exit', VerticalScroll).focus()
        self._draw()

    def _notice(self) -> str:
        worktree = self._owner._session.worktree or {}
        at = str(worktree.get('path') or '')
        if self._changes is None:
            return (f'Working in {at}. PyClaw cannot ask git what is in it, so '
                    'removing it is not done without you saying so.')
        lost = []
        if self._changes['files']:
            lost.append(_plural(self._changes['files'], 'uncommitted file'))
        if self._changes['commits']:
            lost.append(_plural(self._changes['commits'], 'commit'))
        if not lost:
            return f'Working in {at}, with nothing in it that is not elsewhere.'
        return (f'Working in {at}. It holds ' + ' and '.join(lost) +
                '. Removing it loses them for good.')

    def _draw(self):
        lines = [ui.marked(escape('Leaving this worktree?'), selected=False),
                 ui.marked(escape(self._notice()), selected=False)]
        lines += ui.rows([label for _key, label in CHOICES], self._choice)
        lines += ui.footer(('choose', 'chooses'), ('dismiss', 'stays in it'))
        self.query_one('#we-body', Static).update('\n'.join(lines))

    def _move(self, delta: int):
        self._choice = max(0, min(self._choice + delta, len(CHOICES) - 1))
        self._draw()

    def action_next(self):
        self._move(1)

    def action_prev(self):
        self._move(-1)

    async def action_choose(self):
        keep = CHOICES[self._choice][0] == 'keep'
        self.app.pop_screen()
        self._owner._exit_note = await self._owner._session.leave_worktree(
            keep=keep)
        self.app.exit()

    def action_dismiss(self):
        self.app.pop_screen()
