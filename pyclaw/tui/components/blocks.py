from __future__ import annotations

from pyclaw.tui.formatting import escape
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, Markdown, Static

from pyclaw import banner

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
from pyclaw.tools.names import AGENT


def teammate_name(tool_input) -> str:
    return (str(tool_input.get('name') or '')
            if isinstance(tool_input, dict) else '')


class _SummaryBlock(Static):

    def __init__(self, summarized: int, summary: str = '', **kw):
        super().__init__("", **kw)
        self.summarized = summarized
        self.summary = summary
        self.update(self.short())

    def short(self) -> str:
        return (f'{BULLET_PREFIX}Summarized conversation\n'
                f'  Summarized {_plural(self.summarized, "message")} up to '
                f'this point\n'
                f'  {EXPAND_HINT}')

    def verbose(self) -> str:
        lines = [f'{BULLET_PREFIX}Summarized conversation']
        if self.summary:
            lines += [''] + self.summary.splitlines()
        return '\n'.join(lines)


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
        self._group = Group()
        self.active = True
        self._frame = BULLET
        self._draw()

    def add(self, kinds, key, uid):
        self._group.add(kinds, key, uid)
        self._draw()

    @property
    def counts(self):
        return self._group.counts

    @property
    def entries(self):
        return self._group.entries

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


class _AgentGroupBlock(Static):

    def _width(self) -> int:
        return _content_width(self, len(TREE_INDENT) + 2)


    def __init__(self, stats_for, **kw):
        super().__init__(markup=True, **kw)
        self._stats_for = stats_for
        self.members: list[dict] = []
        self.active = True
        self._frame = BULLET
        self._draw()

    def add(self, uid: str, label: str, detail: str, agent: str = ''):
        self.members.append({'uid': uid, 'name': agent, 'label': label,
                             'detail': detail, 'resolved': False,
                             'settled': False, 'seen': False,
                             'error': False, 'progress': []})
        self._draw()

    def state(self, uid: str) -> dict:
        return next(m for m in self.members if m['uid'] == uid)

    def member(self, uid: str) -> '_AgentMember':
        return _AgentMember(self, uid)

    def resolve(self, uid: str, output) -> None:
        member = self.state(uid)
        member['resolved'] = True
        member['error'] = str(output).startswith('Error')
        self._draw()

    def tick(self, char: str):
        if self.active:
            self._frame = char
            self._draw()

    def redraw(self):
        self._draw()

    def verbose_entries(self) -> list[str]:
        entries = []
        for member in self.members:
            head = f'{BULLET_PREFIX}[bold]{escape(member["label"])}[/]'
            if member['detail']:
                head += f'[dim]({escape(member["detail"])})[/]'
            steps = [row for rows, _uses in member['progress'] for row in rows]
            entries.append('\n'.join([head, *steps]) if steps else head)
        return entries

    def finish(self):
        if not self.active:
            return
        self.active = False
        self._frame = BULLET
        self._draw()

    def _draw(self):
        stats = [self._stats_for(member) for member in self.members]
        kinds = {member['label'] for member in self.members}
        kind = kinds.pop() if len(kinds) == 1 and AGENT not in kinds else ''
        closed = [bool(member['resolved'] or member['settled']
                       or (member['seen'] and not stat['running']))
                  for member, stat in zip(self.members, stats)]
        done = bool(stats) and all(closed)
        header = agent_group_header(len(stats), kind=kind, done=done)
        color = (self.app.brand if self.active and not done else DONE_COLOR)
        marker = self._frame if self.active and not done else BULLET
        lines = [f'[{color}]{marker}[/] {header} [dim]({EXPAND_HINT})[/]']
        for index, (member, stat, is_closed) in enumerate(
                zip(self.members, stats, closed)):
            lines.append(agent_group_row(
                label=member['label'], detail=member['detail'],
                tools=stat['tools'], tokens=stat['tokens'],
                status=stat['done_text'] if is_closed else stat['status'],
                error=member['error'],
                last=index == len(self.members) - 1))
        self.update('\n'.join(lines))


class _AgentMember:

    def __init__(self, group: _AgentGroupBlock, uid: str):
        self._group = group
        self.uid = uid

    @property
    def _done(self) -> bool:
        state = self._group.state(self.uid)
        return state['resolved'] or state['settled']

    def set_result(self, output, meta=None):
        self._group.resolve(self.uid, output)

    def add_progress(self, rows, tool_uses: int):
        self._group.state(self.uid)['progress'].append((list(rows),
                                                        int(tool_uses)))
        self._group.redraw()

    def redraw(self):
        self._group.redraw()

    def end_progress(self):
        self._group.state(self.uid)['settled'] = True
        self._group.redraw()

    def _width(self) -> int:
        return self._group._width()
