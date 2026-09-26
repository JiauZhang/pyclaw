from __future__ import annotations

import dataclasses

from pyclaw.tui.formatting import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from pyclaw.tui import keys
from pyclaw.tui.theme import (ANSWER_HINT, BULLET, OPTION_PAGE_SIZE, POINTER)


NO_ANSWER = 'no answer'


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
        picker = (keys.hint('toggle_option', 'picks') + ', '
                  if ask.multi else '')
        lines.append(f"[dim]  {keys.hint('dismiss', 'skips')} \u00b7 "
                     f"{picker}{keys.hint('type_answer', 'types your own')}[/]")
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
