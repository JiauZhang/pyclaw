from __future__ import annotations

from rich.markup import escape
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Markdown, Static
from pyclaw import banner

from pyclaw.tui.diff import _diff_block
from pyclaw.tui.formatting import _edit_summary, _plural, _user_markup
from pyclaw.tui.theme import (AGENT_TRAIL_LIMIT, BULLET, BULLET_PREFIX,
                              GROUP_PARTS, INITIALIZING_TEXT, INTERRUPTED_TEXT,
                              RESULT_HANG, RESULT_PREFIX,
                              WAITING_PERMISSION_TEXT)
from pyclaw.tui.toolcard import (_more_tool_uses, _result_summary, _tool_label,
                                 _tool_use_args, group_text)


class _Conv(VerticalScroll):

    def watch_scroll_y(self, value):
        try:
            self.app._set_follow(bool(self.is_vertical_scroll_end))
        except Exception:
            pass


def _half_page(view) -> int:
    return max(1, view.scrollable_content_region.height // 2)


def _full_page(view) -> int:
    return max(1, view.scrollable_content_region.height)


class _PagerScroll(VerticalScroll):

    BINDINGS = [
        Binding("up", "line_up", "Scroll up", show=False),
        Binding("k", "line_up", "Scroll up", show=False),
        Binding("down", "line_down", "Scroll down", show=False),
        Binding("j", "line_down", "Scroll down", show=False),
        Binding("pageup", "half_page_up", "Half page up", show=False),
        Binding("ctrl+u", "half_page_up", "Half page up", show=False),
        Binding("pagedown", "half_page_down", "Half page down", show=False),
        Binding("ctrl+d", "half_page_down", "Half page down", show=False),
        Binding("ctrl+b", "full_page_up", "Page up", show=False),
        Binding("b", "full_page_up", "Page up", show=False),
        Binding("ctrl+f", "full_page_down", "Page down", show=False),
        Binding("space", "full_page_down", "Page down", show=False),
        Binding("home", "scroll_home", "Top", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("end", "scroll_end", "Bottom", show=False),
        Binding("G", "scroll_end", "Bottom", show=False),
    ]

    def _jump(self, rows: int):
        self.scroll_relative(y=rows, animate=False)

    def action_line_up(self):
        self._jump(-1)

    def action_line_down(self):
        self._jump(1)

    def action_half_page_up(self):
        self._jump(-_half_page(self))

    def action_half_page_down(self):
        self._jump(_half_page(self))

    def action_full_page_up(self):
        self._jump(-_full_page(self))

    def action_full_page_down(self):
        self._jump(_full_page(self))


class _JumpToBottom(Static):

    def __init__(self, text: str = "", **kw):
        super().__init__(text, markup=True, **kw)

    async def on_click(self):
        await self.app.action_jump_to_bottom()


class _TextBlock(Vertical):

    def __init__(self, bullet: str = BULLET_PREFIX, **kw):
        super().__init__(classes="text-block", **kw)
        self._body = ""
        self._bullet = bullet
        self._md: Markdown | None = None
        self._flush_scheduled = False

    def _display(self) -> str:
        return self._body.strip("\n")

    def set_body(self, text: str):
        self._body = text
        if self._md is None:
            if self.is_mounted:
                self._mount_children()
            else:
                return
        if not self._flush_scheduled:
            self._flush_scheduled = True
            self.call_later(self._flush)

    async def _flush(self):
        self._flush_scheduled = False
        if self._md is not None:
            self._md.update(self._display())

    def on_mount(self):
        if self._md is None:
            self._mount_children()

    def _mount_children(self):
        bullet = Static(self._bullet, markup=True, classes="text-bullet")
        self._md = Markdown(self._display(), classes="text-body")
        self.mount(Horizontal(bullet, self._md, classes="text-row"))


class _GroupBlock(Static):

    def __init__(self, **kw):
        super().__init__(markup=True, **kw)
        self.counts = {kind: 0 for kind, *_ in GROUP_PARTS}
        self.entries = []
        self.active = True
        self._frame = BULLET
        self._read_keys = set()
        self._draw()

    def add(self, kinds, key, uid):
        self.entries.append((kinds, key, uid))
        for kind in kinds:
            if kind == 'read':
                if key in self._read_keys:
                    continue
                self._read_keys.add(key)
            self.counts[kind] = self.counts.get(kind, 0) + 1
        self._draw()

    def tick(self, char: str):
        self._frame = char
        if self.active:
            self._draw()

    def finish(self):
        if not self.active:
            return
        self.active = False
        self._draw()

    def _parts(self) -> str:
        return group_text(self.counts, active=self.active)

    def _draw(self):
        body = self._parts()
        if not body:
            self.update("")
            return
        color = self.app.brand if self.active else '#4EBA65'
        marker = self._frame if self.active else BULLET
        hint = "" if self.active else " [dim](ctrl+o for the list)[/]"
        self.update(f"[{color}]{marker}[/] {body}{hint}")


class _LogoBlock(Static):

    def __init__(self, triple: tuple, greeting: str = "", **kw):
        lines = [banner.directional(*triple)]
        if greeting:
            lines.append("\n" + greeting)
        super().__init__("\n".join(lines), markup=True, classes="logo", **kw)


class _PromptInput(Input):

    def check_consume_key(self, key: str, character: str | None) -> bool:
        app = self.app
        if key == 'k' and getattr(app, '_view_selection', '') \
                == 'selecting-agent':
            return False
        return super().check_consume_key(key, character)


class _AgentPane(Static):

    def __init__(self, **kw):
        super().__init__("", markup=True, classes="agents", **kw)


class _UserBlock(Static):

    def __init__(self, text: str, **kw):
        super().__init__(_user_markup(text), markup=True, classes="user", **kw)


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
        if self._name != 'create_agent':
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
        if m.get('num_lines') and self._name == 'Read':
            path = m.get('path', '')
            return (f"Read {_plural(m['num_lines'], 'line')}"
                    + (f" {escape(path)}" if path else ""))
        if m.get('num_files') is not None or m.get('num_lines') is not None:
            if self._name == 'Grep':
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
        if self._name == 'Write' and m.get('mode'):
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
        width = self.size.width or 0
        if width <= 0:
            try:
                width = self.app.size.width
            except Exception:
                width = 0
        return max(20, (width or 80) - len(RESULT_PREFIX) - 2)

    def _is_teammate_spawn(self) -> bool:
        return (self._name == 'create_agent'
                and isinstance(self._input, dict)
                and bool(self._input.get('name')))

    def _agent_summary(self) -> str | None:
        return (self._meta or {}).get('agent_summary')

    def _head(self) -> str:
        name = escape(_tool_label(self._name, self._input))
        args = escape(_tool_use_args(self._name, self._input, self._cwd))
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
            summary = _result_summary(self._name, self._output,
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
