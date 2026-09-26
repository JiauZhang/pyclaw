from __future__ import annotations

from pyclaw.tui.formatting import escape
from textual import events
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Input, Markdown, Static, TextArea

from pyclaw import banner

from pyclaw.tui.diff import _diff_block
from pyclaw.tui.components.blocks import teammate_name
from pyclaw.tui.components.conv import _content_width
from pyclaw.tui.formatting import _edit_summary, _plural, _user_markup
from pyclaw.tui.theme import (AGENT_TRAIL_LIMIT, BULLET, BULLET_PREFIX,
                              DONE_COLOR, EXPAND_HINT, INITIALIZING_TEXT,
                              INTERRUPTED_TEXT, RESULT_HANG,
                              RESULT_PREFIX, TREE_INDENT,
                              WAITING_PERMISSION_TEXT)
from pyclaw.tui.collapse import Group, group_text
from pyclaw.tui.toolcard import (_more_tool_uses, agent_group_header,
                                 agent_group_row)
from pyclaw.tui.toolui import result_summary, tool_args, tool_label
from pyclaw.tools.names import AGENT, GREP, READ, WRITE


class _LogoBlock(Static):

    def __init__(self, triple: tuple, greeting: str = "", **kw):
        lines = [banner.directional(*triple)]
        if greeting:
            lines.append("\n" + greeting)
        super().__init__("\n".join(lines), markup=True, classes="logo", **kw)


MAX_PROMPT_LINES = 8


class PromptSubmitted(Message):
    """Enter was pressed without asking for another line."""

    def __init__(self, prompt: "_PromptInput") -> None:
        super().__init__()
        self.value = prompt.value


class _PromptInput(TextArea):
    """The prompt takes as many lines as you write. Enter sends, unless the
    line asks to continue: a trailing backslash, or shift/alt held down."""

    # Page keys belong to the conversation, not to the caret.
    BINDINGS = [Binding("pageup", "scroll_page_up"),
                Binding("pagedown", "scroll_page_down")]

    def __init__(self, *, placeholder: str = "",
                 id: str | None = None) -> None:
        super().__init__(placeholder=placeholder, id=id,
                         show_line_numbers=False, soft_wrap=True,
                         tab_behavior="focus")

    @property
    def value(self) -> str:
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.load_text(text)
        self.cursor_position = len(text)
        self.post_message(TextArea.Changed(self))

    def submit(self) -> None:
        """Send what is in the prompt, as pressing enter would."""
        self.post_message(PromptSubmitted(self))

    @property
    def cursor_position(self) -> int:
        row, column = self.cursor_location
        lines = self.text.split("\n")
        return sum(len(line) + 1 for line in lines[:row]) + column

    @cursor_position.setter
    def cursor_position(self, position: int) -> None:
        remaining = max(0, position)
        row = 0
        for row, line in enumerate(self.text.split("\n")):
            if remaining <= len(line):
                break
            remaining -= len(line) + 1
        self.move_cursor((row, remaining))

    async def action_scroll_page_up(self) -> None:
        await self.app.action_conv_page_up()

    async def action_scroll_page_down(self) -> None:
        await self.app.action_conv_page_down()

    def check_consume_key(self, key: str, character: str | None) -> bool:
        app = self.app
        if key == 'k' and getattr(app, '_view_selection', '') \
                == 'selecting-agent':
            return False
        return super().check_consume_key(key, character)

    def _continues(self) -> bool:
        row, column = self.cursor_location
        return column > 0 and self.document.get_line(row)[column - 1] == '\\'

    def _break_line(self) -> None:
        row, column = self.cursor_location
        if self._continues():
            self.delete((row, column - 1), (row, column))
        self.insert('\n')

    async def _on_key(self, event: events.Key) -> None:
        if not event.key.endswith('enter'):
            await super()._on_key(event)
            return
        event.stop()
        event.prevent_default()
        if event.key == 'enter' and not self._continues():
            self.post_message(PromptSubmitted(self))
            return
        self._break_line()


class _AgentPane(Static):

    def __init__(self, **kw):
        super().__init__("", markup=True, classes="agents", **kw)


class _UserBlock(Static):

    def __init__(self, text: str, color_for=None, **kw):
        super().__init__(_user_markup(text, color_for), markup=True,
                         classes="user", **kw)


class _ToolBlock(Static):

    def __init__(self, name: str, tool_input, cwd: str = ".", **kw):
        super().__init__(markup=True, **kw)
        self._name = name
        self._input = tool_input
        self._cwd = cwd
        self._output = None
        self._done = False
        self._expanded = False
        self._failed = False
        self._frame = BULLET
        self._meta: dict | None = None
        self._progress: list[tuple[list[str], int]] = []
        self._progress_done = False
        self._waiting_permission = False
        self._rejected: str | None = None
        self._draw()

    def tick(self, char: str):
        if not self._done:
            self._frame = char
            self._draw()

    def redraw(self):
        self._draw()

    def set_waiting_permission(self, waiting: bool):
        if self._waiting_permission == waiting:
            return
        self._waiting_permission = waiting
        self._draw()

    def reject(self, text: str = INTERRUPTED_TEXT):
        self._done = True
        self._progress = []
        self._progress_done = True
        self._waiting_permission = False
        self._rejected = text
        self._failed = True
        self._draw()

    def add_progress(self, rows: list[str], tool_uses: int):
        if self._output is not None:
            return
        self._progress.append((list(rows), int(tool_uses)))
        self._draw()

    def end_progress(self):
        if not self._progress and self._progress_done:
            return
        self._progress = []
        self._progress_done = True
        self._draw()

    def _trail_rows(self, full: bool = False) -> list[str]:
        if self._name != AGENT:
            return []
        if full:
            shown, hidden = self._progress, 0
        else:
            shown = self._progress[-AGENT_TRAIL_LIMIT:]
            hidden = sum(uses for _rows, uses
                         in self._progress[:-AGENT_TRAIL_LIMIT])
        rows = [row for entry, _uses in shown for row in entry]
        if hidden:
            rows.append(_more_tool_uses(hidden))
        return rows

    def _trail_markup(self, full: bool = False) -> str | None:
        if self._output is not None or self._progress_done:
            return None
        if self._waiting_permission:
            return f"[dim]{RESULT_PREFIX}{WAITING_PERMISSION_TEXT}[/]"
        rows = self._trail_rows(full) or [INITIALIZING_TEXT]
        body = ("\n" + RESULT_HANG).join(rows)
        return f"[dim]{RESULT_PREFIX}{body}[/]"

    def set_result(self, output, meta=None):
        self._done = True
        self._progress = []
        self._progress_done = True
        self._output = output
        self._meta = meta
        self._failed = str(output or "").startswith("Error")
        self._draw()

    def _meta_summary(self) -> str | None:
        m = self._meta or {}
        if m.get('num_lines') and self._name == READ:
            path = m.get('path', '')
            return (f"Read {_plural(m['num_lines'], 'line')}"
                    + (f" {escape(path)}" if path else ""))
        if m.get('num_files') is not None or m.get('num_lines') is not None:
            if self._name == GREP:
                hits = m.get('num_lines', 0)
                files = m.get('num_files', 0)
                return (f"Found {_plural(hits, 'line')} in "
                        f"{_plural(files, 'file')}")
            if m.get('num_files') is not None:
                return f"Found {_plural(m['num_files'], 'file')}"
        if m.get('num_entries') is not None:
            return f"Listed {_plural(m['num_entries'], 'entry')}"
        if m.get('num_added', 0) or m.get('num_removed', 0):
            return _edit_summary(m.get('num_added', 0),
                                 m.get('num_removed', 0))
        if self._name == WRITE and m.get('mode'):
            verb = 'Wrote' if m['mode'] == 'wrote' else 'Updated'
            if m.get('path'):
                return f"{verb} {escape(m['path'])}"
        return None

    def on_click(self):
        self._expanded = not self._expanded
        self._draw()

    def on_resize(self):
        self._draw()

    def _width(self) -> int:
        return _content_width(self, len(RESULT_PREFIX))

    def _is_teammate_spawn(self) -> bool:
        return self._name == AGENT and bool(teammate_name(self._input))

    def _agent_summary(self) -> str | None:
        return (self._meta or {}).get('agent_summary')

    def _head(self) -> str:
        name = escape(tool_label(self._name, self._input))
        args = escape(tool_args(self._name, self._input, self._cwd))
        color = "#FF6B80" if self._failed else (
            "#4EBA65" if self._done else self.app.brand)
        marker = BULLET if (self._done or self._failed) else self._frame
        return f"[{color}]{marker}[/] [bold]{name}[/]({args})"

    def _draw(self):
        head = self._head()
        if self._rejected is not None:
            self.update(f"{head}\n[dim]{RESULT_PREFIX}"
                        f"{escape(self._rejected)}[/]")
            return
        if self._is_teammate_spawn():
            summary = self._agent_summary()
            if summary is not None:
                rows = escape(summary).replace("\n", "\n" + RESULT_HANG)
                self.update(f"{head}\n[dim]{RESULT_PREFIX}{rows}[/]")
                return
            trail = self._trail_markup()
            self.update(f"{head}\n{trail}" if trail else head)
            return
        summary = self._agent_summary()
        if summary is not None and self._output is not None:
            rows = escape(summary).replace("\n", "\n" + RESULT_HANG)
            self.update(f"{head}\n[dim]{RESULT_PREFIX}{rows}[/]")
            return
        if self._output is None:
            trail = self._trail_markup()
            self.update(f"{head}\n{trail}" if trail else head)
            return
        if self._expanded:
            body = str(self._output)
            if not body.strip():
                self.update(f"{head}\n[dim]{RESULT_PREFIX}(no output)[/]")
                return
            rows = escape(body).replace("\n", "\n" + RESULT_HANG)
            self.update(f"{head}\n{RESULT_PREFIX}{rows}")
            return
        summary = self._meta_summary()
        if summary is None:
            summary = result_summary(self._name, self._output,
                                      self._width())
        if self._meta_summary() is not None:
            self.remove_class("diff")
        else:
            diff = _diff_block(self._name, self._input, self._output,
                               self._cwd, self._width())
            if diff is not None:
                self.add_class("diff")
                self.update(f"{head}\n{diff}")
                return
            self.remove_class("diff")
        rows = escape(summary).replace("\n", "\n" + RESULT_HANG)
        self.update(f"{head}\n[dim]{RESULT_PREFIX}{rows}[/]")
