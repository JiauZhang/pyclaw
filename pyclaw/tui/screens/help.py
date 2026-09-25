from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from pyclaw.slash import COMMANDS
from pyclaw.tui import keys, ui
from pyclaw.tui.components import _PagerScroll


class HelpScreen(Screen):

    BINDINGS = [keys.binding('dismiss', 'Close'),
                Binding('q', 'dismiss', 'Close'),
                Binding('ctrl+o', 'dismiss', 'Close'),
                Binding('?', 'dismiss', 'Close')]

    def _body(self) -> str:
        lines = ["[bold]Shortcuts[/bold]"]
        seen = set()
        for entry in keys.app_bindings():
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
        lines.append(f"[bold]Reading the transcript "
                     f"({keys.display('toggle_transcript')})[/bold]")
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
        lines += ui.footer(('dismiss', 'to close'))
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="help"):
            yield Static(self._body(), markup=True)

    def action_dismiss(self):
        self.app.pop_screen()
