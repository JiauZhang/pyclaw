from __future__ import annotations

import asyncio
import dataclasses
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static
from pyclaw.tools.coding.permission import BASH_TOOL, PermissionChoice

from pyclaw.tui.theme import (ACCEPT_FEEDBACK_HINT, ANSWER_HINT, BULLET,
                              OPTION_PAGE_SIZE, POINTER,
                              REJECT_FEEDBACK_HINT, RULE_FEEDBACK_HINT)
from pyclaw.tui.toolui import tool_args, tool_label


NO_ANSWER = 'no answer'


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
        self._open_rule = False
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
        if option.feedback == "rule" and self._open_rule:
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
        args = tool_args(self._tool, self._input, self._cwd)
        lines = [f"[#B1B9F9]{BULLET}[/] {title}",
                 f"  {escape(tool_label(self._tool, {}))}({escape(args)})"]
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
        closed = {"accept": not self._open_accept,
                  "reject": not self._open_reject,
                  "rule": not self._open_rule}.get(focused.feedback, False)
        note = ''
        if closed:
            note = (" \u00b7 tab edits the rule" if focused.feedback == "rule"
                    else " \u00b7 tab adds a note")
        lines.append(f"[dim]  esc cancels{note}[/]")
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
        if option != "rule" and self._open_rule:
            self._open_rule = bool(
                self.query_one("#perm-rule", Input).value.strip()
                != self._rule.strip())
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
        elif option.feedback == "rule":
            self._open_rule = not self._open_rule
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

    def _text_for(self, option: _PermOption) -> str:
        if option.feedback == "rule":
            return self.query_one("#perm-rule", Input).value
        field = self._row_field(option)
        return '' if field is None else field.value

    async def _submit(self, option: _PermOption):
        await self._finish(self._choice(option, self._text_for(option)))

    async def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        await self._submit(self._options()[self._focused])

    async def _finish(self, choice: PermissionChoice):
        if self.on_choice is not None:
            self.on_choice(choice)


@dataclasses.dataclass
class _Ask:
    text: str
    header: str
    options: list[dict]
    multi: bool


def _ask(question) -> _Ask:
    question = question if isinstance(question, dict) else {}
    options = question.get("options") or []
    return _Ask(str(question.get("question") or ""),
                str(question.get("header") or ""),
                [option if isinstance(option, dict) else {"label": option}
                 for option in options],
                bool(question.get("multiSelect")))


class _QuestionPrompt(Vertical):

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
        ("space", "toggle_option", "Pick option"),
        ("enter", "accept_option", "Answer"),
        ("escape", "cancel", "Skip"),
        ("tab", "type_answer", "Own answer"),
    ] + [Binding(str(index), f"pick_option({index})", "Pick option")
         for index in range(1, 5)]

    def __init__(self, questions: list, agent: str = "", **kw):
        super().__init__(classes="question", **kw)
        self._asks = [_ask(question) for question in questions]
        self._agent = agent
        self._index = 0
        self._focused = 0
        self._picked: set[int] = set()
        self._typing = False
        self._answers: list[str] = []
        self.on_answer = None

    def compose(self) -> ComposeResult:
        yield Static("", id="q-body", markup=True)
        yield Input("", placeholder=ANSWER_HINT, id="q-text",
                    select_on_focus=False)

    def on_mount(self):
        self.app.register_overlay("select")
        self.focus()
        self._draw()

    def on_unmount(self):
        self.app.unregister_overlay("select")

    @property
    def _ask(self) -> _Ask:
        return self._asks[self._index]

    def _draw(self):
        ask = self._ask
        title = "[bold]Question[/bold]"
        if len(self._asks) > 1:
            title += f" [dim]{self._index + 1}/{len(self._asks)}[/]"
        if self._agent:
            title += f" [dim]\u00b7 @{escape(self._agent)}[/]"
        lines = [f"[#B1B9F9]{BULLET}[/] {title}"]
        head = f"  {escape(ask.header)}" if ask.header else "  question"
        lines.append(f"[dim]{head}"
                     + (" \u00b7 pick any[/]" if ask.multi else "[/]"))
        lines.append(f"  {escape(ask.text)}")
        for index, option in enumerate(ask.options):
            marker = POINTER if index == self._focused else ' '
            if ask.multi:
                marker = "\u25c9" if index in self._picked else marker
            label = str(option.get("label") or "")
            detail = str(option.get("description") or "").strip()
            row = f"  {marker} {index + 1}. {label}"
            if detail:
                row += f" \u2014 {detail}"
            row = escape(row)
            lines.append(f"[#B1B9F9]{row}[/]" if index == self._focused
                         else f"[dim]{row}[/]")
        if not ask.options:
            lines.append("  [dim]no options, type an answer[/]")
        preview = self._focused_preview(ask)
        if preview:
            lines.append("")
            for line in preview.splitlines():
                lines.append(f"[dim]  \u2502 {escape(line)}[/]")
        keys = "space picks, " if ask.multi else ""
        lines.append(f"[dim]  esc skips \u00b7 {keys}tab types your own[/]")
        self.query_one("#q-body", Static).update("\n".join(lines))
        self._sync_field()

    def _focused_preview(self, ask: _Ask) -> str:
        if ask.multi or not ask.options:
            return ""
        index = min(self._focused, len(ask.options) - 1)
        return str(ask.options[index].get("preview") or "").strip()

    def _sync_field(self):
        field = self.query_one("#q-text", Input)
        field.display = self._typing
        if self._typing:
            field.focus()
        else:
            self.focus()

    def _move(self, delta: int, wrap: bool):
        total = max(1, len(self._ask.options))
        self._focused = ((self._focused + delta) % total if wrap
                         else min(max(0, self._focused + delta), total - 1))
        self._draw()

    def action_opt_next(self):
        self._move(1, wrap=True)

    def action_opt_prev(self):
        self._move(-1, wrap=True)

    def action_opt_next_page(self):
        self._move(OPTION_PAGE_SIZE, wrap=False)

    def action_opt_prev_page(self):
        self._move(-OPTION_PAGE_SIZE, wrap=False)

    def action_type_answer(self):
        self._typing = not self._typing
        self._draw()

    def action_toggle_option(self):
        if not self._ask.multi:
            return
        if self._focused in self._picked:
            self._picked.discard(self._focused)
        else:
            self._picked.add(self._focused)
        self._draw()

    def action_pick_option(self, index: int):
        ask = self._ask
        if index > len(ask.options):
            return
        if ask.multi:
            self._focused = index - 1
            self.action_toggle_option()
            return
        self._focused = index - 1
        self._accept()

    async def action_accept_option(self):
        self._accept()

    async def action_cancel(self):
        self._answers.extend(NO_ANSWER
                             for _ in range(self._index, len(self._asks)))
        self._finish()

    def on_input_submitted(self, event: Input.Submitted):
        event.stop()
        self._record(event.value.strip())

    def _accept(self):
        ask = self._ask
        if ask.multi:
            labels = [str(ask.options[index].get("label") or "")
                      for index in sorted(self._picked)]
            self._record(", ".join(label for label in labels if label))
            return
        if not ask.options:
            self._record(NO_ANSWER)
            return
        option = ask.options[self._focused]
        self._record(self._with_preview(
            str(option.get("label") or ""), option))

    @staticmethod
    def _with_preview(answer: str, option: dict) -> str:
        preview = str(option.get("preview") or "").strip()
        if not preview:
            return answer
        return f'{answer}\nselected preview:\n{preview}'

    def _record(self, answer: str):
        self._answers.append(answer or NO_ANSWER)
        self._index += 1
        self._focused = 0
        self._picked.clear()
        self._typing = False
        field = self.query_one("#q-text", Input)
        field.value = ""
        if self._index < len(self._asks):
            self._draw()
            return
        self._finish()

    def _finish(self):
        if self.on_answer is not None:
            self.on_answer(list(self._answers))
