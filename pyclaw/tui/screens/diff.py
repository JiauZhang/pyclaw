from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from pyclaw.tui import keys
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Static


class DiffScreen(Screen):

    BINDINGS = [keys.binding('dismiss', 'Close'),
                Binding('q', 'dismiss', 'Close'),
                Binding('ctrl+c', 'dismiss', 'Close')]

    CSS = """
    #diff-panel { width: 1fr; height: 1fr; }
    #diff-body { width: 1fr; height: auto; padding: 0 1; }
    """

    def __init__(self, view, **kw):
        super().__init__(**kw)
        self._view = view

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="diff-panel"):
            yield Static("", id="diff-body", markup=True)

    def on_mount(self):
        self.query_one("#diff-panel", VerticalScroll).focus()
        files = self._view['files']
        lines = ['[bold]Changes[/bold]',
                 f'[dim]{len(files)} file(s) in '
                 + ('the working tree' if self._view['kind'] == 'git'
                    else 'this session') + '[/]', '']
        for line in str(self._view['text']).splitlines():
            if line.startswith('+++') or line.startswith('---'):
                lines.append(f'[bold]{escape(line)}[/bold]')
            elif line.startswith('+'):
                lines.append(f'[#4EBA65]{escape(line)}[/]')
            elif line.startswith('-'):
                lines.append(f'[#FF6B80]{escape(line)}[/]')
            elif line.startswith('@@'):
                lines.append(f'[dim]{escape(line)}[/]')
            else:
                lines.append(escape(line) or '')
        lines += ['', f'[dim]{keys.hint("dismiss", "closes")}[/]']
        self.query_one("#diff-body", Static).update('\n'.join(lines))

    def action_dismiss(self):
        self.app.pop_screen()
