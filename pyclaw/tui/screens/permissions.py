from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static


class _RuleList(VerticalScroll):

    BINDINGS = [("up", "move_up", "Up"), ("down", "move_down", "Down"),
                ("d", "remove_rule", "Remove rule"),
                ("escape", "close", "Close"), ("q", "close", "Close")]

    def __init__(self, screen, **kw):
        super().__init__(**kw)
        self._screen = screen

    def action_move_up(self):
        self._screen.action_move_up()

    def action_move_down(self):
        self._screen.action_move_down()

    async def action_remove_rule(self):
        await self._screen.action_remove_rule()

    def action_close(self):
        self._screen.action_close()

class PermissionsScreen(Screen):

    BINDINGS = [("escape", "close", "Close"),
                ("q", "close", "Close"),
                ("d", "remove_rule", "Remove rule"),
                ("up", "move_up", "Up"),
                ("down", "move_down", "Down")]

    def __init__(self, session, **kw):
        super().__init__(**kw)
        self._session = session
        self._selected = 0

    def compose(self) -> ComposeResult:
        with _RuleList(self, id="permissions"):
            yield Static('...', id="permissions-body")

    def on_mount(self):
        self.query_one("#permissions", _RuleList).focus()
        self._refresh_body()

    def _rules(self) -> list:
        getter = getattr(self._session, 'permission_rules', None)
        return list(getter()) if getter else []

    def _refresh_body(self):
        rules = self._rules()
        mode = getattr(self._session, 'permission_mode', 'default')
        bypass = bool(getattr(self._session, 'bypass_available', False))
        lines = [f'[bold]Permission mode[/bold] [{self.app.brand}]{mode}[/]',
                 f'[#9A9A9A]bypass available: {bypass} · '
                 f'shift+tab cycles · /permissions <mode> switches[/]', '']
        if not rules:
            lines.append('[#9A9A9A]No permission rules.[/]')
        else:
            lines.append('[bold]Rules[/bold] '
                         '[#9A9A9A](up/down to move, d to remove)[/]')
            for index, (behavior, rule, source) in enumerate(rules):
                marker = '\u203a' if index == self._selected else ' '
                body = escape(f'  {marker} [{behavior}] {rule}  ({source})')
                lines.append(f'[reverse]{body}[/]' if index == self._selected
                             else body)
        self.query_one('#permissions-body', Static).update('\n'.join(lines))

    def action_close(self):
        self.app.pop_screen()

    def action_move_up(self):
        self._selected = max(0, self._selected - 1)
        self._refresh_body()

    def action_move_down(self):
        rules = self._rules()
        self._selected = min(max(0, len(rules) - 1), self._selected + 1)
        self._refresh_body()

    async def action_remove_rule(self):
        rules = self._rules()
        if not rules:
            return
        index = min(self._selected, len(rules) - 1)
        _behavior, rule, _source = rules[index]
        remover = getattr(self._session, 'remove_rule', None)
        if remover is None or not remover(rule):
            return
        await self._session.note_config_change('permissions')
        self._selected = max(0, self._selected - 1)
        self._refresh_body()
