from __future__ import annotations

import asyncio
import dataclasses
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static
from pyclaw.tools.coding.permission import BASH_TOOL, PermissionChoice

from pyclaw.tui.theme import (ACCEPT_FEEDBACK_HINT, BULLET, OPTION_PAGE_SIZE,
                              POINTER, REJECT_FEEDBACK_HINT, RULE_FEEDBACK_HINT)
from pyclaw.tui.toolcard import _display_name, _tool_use_args


@dataclasses.dataclass
class _PermOption:
    value: str
    label: str
    feedback: str = ''


@dataclasses.dataclass
class _Approval:
    tool_use_id: str
    tool_name: str
    prompt: '_PermissionPrompt'
    block: object
    future: asyncio.Future


class _PermissionPrompt(Vertical):

    can_focus = True

    BINDINGS = [
        ("up", "opt_prev", "Previous option"),
        ("down", "opt_next", "Next option"),
        ("k", "opt_prev", "Previous option"),
        ("j", "opt_next", "Next option"),
        ("ctrl+p", "opt_prev", "Previous option"),
        ("ctrl+n", "opt_next", "Next option"),
        ("pageup", "opt_prev_page", "Page up"),
        ("pagedown", "opt_next_page", "Page down"),
        ("enter", "accept_option", "Confirm"),
        ("escape", "cancel", "Cancel"),
        ("tab", "toggle_feedback", "Amend"),
    ] + [Binding(str(index), f"pick_option({index})", "Pick option")
         for index in range(1, 10)]

    def __init__(self, tool_name: str, tool_input, cwd: str = ".",
                 rememberable: bool = True, rule: str = "", agent: str = "",
                 **kw):
        super().__init__(classes="permission", **kw)
        self._tool = tool_name
        self._input = tool_input
        self._cwd = cwd
        self._rememberable = rememberable
        self._rule = rule
        self._agent = agent
        self._focused = 0
        self._open_accept = False
        self._open_reject = False
        self.on_choice = None

    def compose(self) -> ComposeResult:
        yield Static("", id="perm-body", markup=True)
        yield Input("", placeholder=ACCEPT_FEEDBACK_HINT, id="perm-accept",
                    select_on_focus=False)
        yield Input("", placeholder=REJECT_FEEDBACK_HINT, id="perm-reject",
                    select_on_focus=False)
        yield Input(self._rule, placeholder=RULE_FEEDBACK_HINT, id="perm-rule",
                    select_on_focus=False)

    def on_mount(self):
        self.app.register_overlay("select")
        self._draw()
        self._sync_row()

    def on_unmount(self):
        self.app.unregister_overlay("select")

    def _options(self) -> list[_PermOption]:
        options = [_PermOption("approved", "Yes", "accept")]
        if self._rememberable:
            if self._tool == BASH_TOOL and self._rule:
                options.append(_PermOption(
                    "dont_ask",
                    f"Yes, and stop asking about: {self._rule}", "rule"))
            else:
                options.append(_PermOption(
                    "dont_ask",
                    f"Yes, always allow {self._tool} in {self._cwd}", ""))
        options.append(_PermOption("denied", "No", "reject"))
        return options

    def _row_field(self, option: _PermOption) -> Input | None:
        if option.feedback == "rule":
            return self.query_one("#perm-rule", Input)
        if option.feedback == "accept" and self._open_accept:
            return self.query_one("#perm-accept", Input)
        if option.feedback == "reject" and self._open_reject:
            return self.query_one("#perm-reject", Input)
        return None

    def _fields(self) -> list[Input]:
        return [self.query_one(f"#perm-{name}", Input)
                for name in ("accept", "reject", "rule")]

    def _sync_row(self):
        active = self._row_field(self._options()[self._focused])
        for field in self._fields():
            field.display = field is active
        if active is not None:
            active.cursor_position = len(active.value)
            active.focus()
        else:
            self.focus()
        self._draw()

    def _draw(self):
        options = self._options()
        focused = options[self._focused]
        title = "[bold]Tool use[/bold]"
        if self._agent:
            title += f" [dim]\u00b7 @{escape(self._agent)}[/]"
        args = _tool_use_args(self._tool, self._input, self._cwd)
        lines = [f"[#B1B9F9]{BULLET}[/] {title}",
                 f"  {escape(_display_name(self._tool))}({escape(args)})"]
        intent = ''
        if isinstance(self._input, dict):
            intent = str(self._input.get('description') or '').strip()
        if intent:
            lines.append(f"  [dim]{escape(intent)}[/]")
        lines.append("  Allow this call?")
        for index, option in enumerate(options):
            marker = POINTER if index == self._focused else ' '
            row = escape(f"  {marker} {index + 1}. {option.label}")
            lines.append(f"[#B1B9F9]{row}[/]" if index == self._focused
                         else f"[dim]{row}[/]")
        hint = ((focused.feedback == "accept" and not self._open_accept)
                or (focused.feedback == "reject" and not self._open_reject))
        lines.append("[dim]  esc cancels"
                     + (" \u00b7 tab adds a note" if hint else "") + "[/]")
        self.query_one("#perm-body", Static).update("\n".join(lines))

    def _move(self, delta: int, wrap: bool):
        total = len(self._options())
        self._focused = ((self._focused + delta) % total if wrap
                         else min(max(0, self._focused + delta), total - 1))
        option = self._options()[self._focused].feedback
        if option != "accept" and self._open_accept:
            self._open_accept = bool(
                self.query_one("#perm-accept", Input).value.strip())
        if option != "reject" and self._open_reject:
            self._open_reject = bool(
                self.query_one("#perm-reject", Input).value.strip())
        self._sync_row()

    def action_opt_next(self):
        self._move(1, wrap=True)

    def action_opt_prev(self):
        self._move(-1, wrap=True)

    def action_opt_next_page(self):
        self._move(OPTION_PAGE_SIZE, wrap=False)

    def action_opt_prev_page(self):
        self._move(-OPTION_PAGE_SIZE, wrap=False)

    def action_toggle_feedback(self):
        option = self._options()[self._focused]
        if option.feedback == "accept":
            self._open_accept = not self._open_accept
        elif option.feedback == "reject":
            self._open_reject = not self._open_reject
        else:
            return
        self._sync_row()

    async def action_accept_option(self):
        await self._submit(self._options()[self._focused])

    async def action_pick_option(self, index: int):
        options = self._options()
        if index > len(options):
            return
        await self._submit(options[index - 1])

    async def action_cancel(self):
        await self._finish(PermissionChoice("denied"))

    def _choice(self, option: _PermOption, text: str) -> PermissionChoice:
        text = text.strip()
        if option.feedback == "rule":
            return (PermissionChoice("approved") if not text
                    else PermissionChoice("dont_ask", rule=text))
        if self._row_field(option) is not None:
            return PermissionChoice(option.value, feedback=text)
        return PermissionChoice(option.value)

    async def _submit(self, option: _PermOption):
        field = self._row_field(option)
        await self._finish(self._choice(option, field.value if field else ""))

    async def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        await self._submit(self._options()[self._focused])

    async def _finish(self, choice: PermissionChoice):
        if self.on_choice is not None:
            self.on_choice(choice)
