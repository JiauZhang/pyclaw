from __future__ import annotations

import asyncio
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from pyclaw.tui.approval import _PermissionPrompt
from pyclaw.tui.diff import _diff_block
from pyclaw.tui.formatting import _hang, _plural, _summarize, thinking_map
from pyclaw.tui.theme import (ASTERISK, BULLET_PREFIX, POINTER, RESULT_HANG,
                              RESULT_PREFIX)
from pyclaw.tui.widgets import (_AgentGroupBlock, _GroupBlock, _JumpToBottom,
                                _LogoBlock, _PagerScroll, _TextBlock,
                                _ToolBlock, _UserBlock)


class TranscriptScreen(Screen):

    BINDINGS = [("escape", "exit_transcript", "Back"),
                ("q", "exit_transcript", "Back"),
                ("ctrl+o", "exit_transcript", "Back"),
                ("ctrl+c", "exit_transcript", "Back")]

    def __init__(self, owner, **kw):
        super().__init__(**kw)
        self._owner = owner

    def compose(self) -> ComposeResult:
        with _PagerScroll(id="transcript"):
            for entry in self._entries():
                yield Static(entry, markup=True)

    def on_mount(self):
        self.query_one("#transcript", _PagerScroll).focus()

    @staticmethod
    def _tool_entry(block) -> str:
        head = block._head()
        if block._output is None:
            trail = block._trail_markup(full=True)
            return f"{head}\n{trail}" if trail else head
        diff = _diff_block(block._name, block._input, block._output,
                           block._cwd, block._width())
        if diff is not None:
            return head + "\n" + diff
        body = escape(str(block._output)) or "(no output)"
        return (head + "\n" + RESULT_PREFIX
                + body.replace("\n", "\n" + RESULT_HANG))

    @staticmethod
    def _thinking_entry(text: str) -> str:
        return (f"[#9A9A9A]{ASTERISK} Thinking\u2026[/]\n"
                + _hang(RESULT_HANG, escape(text)))

    def _entries(self) -> list[str]:
        app = self._owner
        entries: list[str] = []
        thoughts = thinking_map(app._team.transcript())
        for widget in app._conv().children:
            if isinstance(widget, (_PermissionPrompt, _JumpToBottom, _LogoBlock)):
                continue
            if isinstance(widget, _TextBlock):
                body = widget._body or ""
                shown = body.strip("\n")
                thought = thoughts.get(body) or thoughts.get(shown)
                if thought:
                    entries.append(self._thinking_entry(thought))
                entries.append(_hang(BULLET_PREFIX, escape(shown)))
            elif isinstance(widget, _UserBlock):
                entries.append(escape(str(widget.content)))
            elif isinstance(widget, _GroupBlock):
                for _kinds, _key, uid in widget.entries:
                    block = app._tools.get(uid)
                    if block is not None:
                        entries.append(self._tool_entry(block))
            elif isinstance(widget, _AgentGroupBlock):
                entries.extend(widget.verbose_entries())
            elif isinstance(widget, _ToolBlock):
                if widget.display:
                    entries.append(self._tool_entry(widget))
            else:
                entries.append(str(widget.content))
        return entries

    def action_exit_transcript(self):
        self.app.pop_screen()


class HelpScreen(Screen):

    BINDINGS = [("escape", "close", "Close"), ("q", "close", "Close"),
                ("ctrl+o", "close", "Close"), ("?", "close", "Close")]

    def _body(self) -> str:
        from pyclaw.slash import COMMANDS
        from pyclaw.tui.app import PyClawApp
        lines = ["[bold]Shortcuts[/bold]"]
        seen = set()
        for entry in PyClawApp.BINDINGS:
            if isinstance(entry, Binding):
                key, action, description = (entry.key, entry.action,
                                            entry.description)
            else:
                key, action, description = (entry[0], entry[1],
                                            entry[2] if len(entry) > 2 else '')
            if key in seen or not description:
                continue
            seen.add(key)
            lines.append(f"  {escape(key)}  "
                         f"[dim]{escape(description)}[/]")
        lines.append("")
        lines.append("[bold]Reading the transcript (ctrl+o)[/bold]")
        pager_seen = set()
        for entry in _PagerScroll.BINDINGS:
            if entry.key in pager_seen or not entry.description:
                continue
            pager_seen.add(entry.key)
            lines.append(f"  {escape(entry.key)}  "
                         f"[dim]{escape(entry.description)}[/]")
        lines.append("")
        lines.append("[bold]Slash commands[/bold]")
        for item in COMMANDS:
            lines.append(f"  /{escape(item['name'])}  "
                         f"[dim]{escape(item['desc'])}[/]")
        lines.append("")
        lines.append("[dim]esc to close[/]")
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Static(self._body(), markup=True)

    def action_close(self):
        self.app.pop_screen()


class HistorySearchScreen(Screen):

    BINDINGS = [Binding("up", "prev", "Previous", priority=True),
                Binding("down", "next", "Next", priority=True),
                ("escape", "close", "Close"),
                ("ctrl+c", "close", "Close"),
                ("tab", "accept", "Accept")]

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
        lines = []
        for i, text in enumerate(window):
            index = start + i
            row = escape(text)
            lines.append(f"[#B1B9F9]{row}[/]"
                         if index == self._selected else f"[dim]{row}[/]")
        widget.update("\n".join(lines))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._selected = 0
        self._refresh()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        await self._finish(True)

    def action_accept(self):
        asyncio.get_running_loop().create_task(self._finish(False))

    def action_close(self):
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
        inp = self._owner.query_one("#input", Input)
        inp.value = text
        inp.cursor_position = len(text)
        self.app.pop_screen()
        if execute:
            await inp.action_submit()


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

    def action_remove_rule(self):
        self._screen.action_remove_rule()

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

    def action_remove_rule(self):
        rules = self._rules()
        if not rules:
            return
        index = min(self._selected, len(rules) - 1)
        _behavior, rule, _source = rules[index]
        remover = getattr(self._session, 'remove_rule', None)
        if remover is None or not remover(rule):
            return
        self._selected = max(0, self._selected - 1)
        self._refresh_body()


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
