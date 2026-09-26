from __future__ import annotations

from pyclaw.tui.formatting import escape
from textual.app import ComposeResult
from textual.binding import Binding
from pyclaw.tui import keys
from textual.screen import Screen
from textual.widgets import Static
from pyclaw.tui.components import (
    _AgentGroupBlock,
    _GroupBlock,
    _JumpToBottom,
    _LogoBlock,
    _PagerScroll,
    _SummaryBlock,
    _TextBlock,
    _ToolBlock,
    _UserBlock,
)

from pyclaw.tui.theme import (
    ASTERISK,
    BULLET_PREFIX,
    RESULT_HANG,
    RESULT_PREFIX,
)
from pyclaw.tui.formatting import _hang, model_map, thinking_map
from pyclaw.tui.permission_card import _PermissionPrompt
from pyclaw.tui.question_card import _QuestionPrompt
from pyclaw.tui.diff import _diff_block


class TranscriptScreen(Screen):

    BINDINGS = [keys.binding('dismiss', 'Back'),
                Binding('q', 'dismiss', 'Back'),
                Binding('ctrl+o', 'dismiss', 'Back'),
                Binding('ctrl+c', 'dismiss', 'Back')]

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
        return (f"[dim]{ASTERISK} Thinking\u2026[/]\n"
                + _hang(RESULT_HANG, escape(text)))

    def _entries(self) -> list[str]:
        app = self._owner
        entries: list[str] = []
        thoughts = thinking_map(app._team.transcript())
        models = model_map(app._team.transcript())
        for widget in app._conv().children:
            if isinstance(widget, (_PermissionPrompt, _QuestionPrompt,
                                   _JumpToBottom, _LogoBlock)):
                continue
            if isinstance(widget, _TextBlock):
                body = widget._body or ""
                shown = body.strip("\n")
                model = models.get(body) or models.get(shown)
                if model:
                    entries.append(f"[dim]{escape(model)}[/]")
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
            elif isinstance(widget, _SummaryBlock):
                entries.append(widget.verbose())
            elif isinstance(widget, _ToolBlock):
                if widget.display:
                    entries.append(self._tool_entry(widget))
            else:
                entries.append(str(widget.content))
        return entries

    def action_dismiss(self):
        self.app.pop_screen()
