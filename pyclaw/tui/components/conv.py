from __future__ import annotations

from rich.markup import escape
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Markdown, Static

from pyclaw import banner

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


class _Conv(VerticalScroll):

    def watch_scroll_y(self, value):
        try:
            self.app._set_follow(bool(self.is_vertical_scroll_end))
        except Exception:
            pass


def _content_width(widget, indent: int) -> int:
    width = widget.size.width or 0
    if width <= 0:
        try:
            width = widget.app.size.width
        except Exception:
            width = 0
    return max(20, (width or 80) - indent - 2)


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
