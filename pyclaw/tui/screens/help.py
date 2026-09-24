from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from pyclaw.tui.components import _PagerScroll
from textual.widgets import Static


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
