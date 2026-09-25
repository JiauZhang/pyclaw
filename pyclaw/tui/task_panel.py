from __future__ import annotations

from pyclaw.tui import keys

import logging
import time

from rich.markup import escape
from textual.containers import VerticalScroll

from pyclaw.tools import background
from pyclaw.tui.formatting import _format_count, _summarize, duration
from pyclaw.tui.plan import plan_lines, recent_completions
from pyclaw.tui.theme import RESULT_GLYPH
from pyclaw.tui.collapse import recent_rollup
from pyclaw.tui.toolcard import _tool_uses

logger = logging.getLogger(__name__)

LISTED_TOOLS = 40
BRANCH_LAST = '└─'
BRANCH_MIDDLE = '├─'
PIPE_LAST = '   '
PIPE_MIDDLE = '│  '


def agent_tree(team, states: dict, colors: dict) -> list[str]:
    lines = ['[bold]Agents[/bold]']

    def walk(agent_id, prefix, last):
        agent = team.agents.get(agent_id)
        if agent is None:
            return
        state = states.get(agent.name, {})
        status = ('working…' if state.get('think')
                  else ('busy' if state.get('busy') else 'idle'))
        name = escape(agent.name)
        color = colors.get(agent.name)
        if color:
            name = f'[{color}][bold]{name}[/][/]'
        lines.append(f"{prefix}{BRANCH_LAST if last else BRANCH_MIDDLE} "
                     f"{name} · "
                     f"{_tool_uses(state.get('tools', 0))} ({status})")
        kids = sorted(team.children.get(agent_id, ()))
        branch = PIPE_LAST if last else PIPE_MIDDLE
        for index, kid in enumerate(kids):
            walk(kid, prefix + branch, index == len(kids) - 1)

    walk(team.lead.agent_id, '', True)
    return lines


def subagent_rows(subagents: dict) -> list[str]:
    if not subagents:
        return []
    rows = list(subagents.values())
    lines = ['[bold]Sub-agents[/bold]']
    for index, state in enumerate(rows):
        last = index == len(rows) - 1
        branch = PIPE_LAST if last else PIPE_MIDDLE
        tokens = (f" · {_format_count(state['tokens'])} tokens"
                  if state['tokens'] is not None else '')
        status = ('Done' if state['done']
                  else (recent_rollup(state['recent']) or state['last_tool']
                        or 'Initializing…'))
        label = escape(str(state['type'])) or 'Agent'
        lines.append(f"{BRANCH_LAST if last else BRANCH_MIDDLE} "
                     f"[bold]{label}[/] · {_tool_uses(state['tools'])}{tokens}")
        lines.append(f"{branch}{RESULT_GLYPH}  {escape(status)}")
    return lines


def shell_rows(shells: list) -> list[str]:
    if not shells:
        return []
    lines = [f'[bold]Background shells[/bold] {len(shells)}']
    for row in shells:
        state = (f"exited {row['exit']}" if row['exit'] is not None
                 else f"running {duration(row['seconds'])}")
        lines.append(f"  {escape(str(row['id']))} · "
                     f"{escape(_summarize(row['command'], 60))} · {state}")
    return lines


def task_detail_lines(row, *, output: str = '', state=None, subagent=None,
                      now=None) -> list[str]:
    if row.get('kind') == 'shell':
        return _shell_detail_lines(row, output)
    return _agent_detail_lines(row, state or {}, subagent or {}, now)


def _shell_detail_lines(row, output: str) -> list[str]:
    status = ('running' if row.get('exit') is None
              else f"exited {row['exit']}")
    if row.get('killed'):
        status += ' (stopped)'
    lines = [f"[bold]Background shell[/bold] {escape(str(row.get('id', '')))}",
             f"  Status:   {status}",
             f"  Runtime:  {duration(int(row.get('seconds') or 0))}",
             f"  Command:  "
             f"{escape(str(row.get('command') or row.get('label') or ''))}",
             '',
             '[bold]Output[/bold]']
    body = (output or '').rstrip('\n')
    if not body:
        lines.append('[dim]nothing yet[/]')
        return lines
    lines += [escape(line) for line in body.splitlines()]
    return lines


def _agent_detail_lines(row, state: dict, subagent: dict,
                        now) -> list[str]:
    kind = 'Teammate' if row.get('kind') == 'teammate' else 'Sub-agent'
    lines = [f"[bold]{kind}[/bold] {escape(str(row.get('label') or ''))}"]
    if subagent.get('type'):
        lines.append(f"  Type:     {escape(str(subagent['type']))}")
    lines.append(f"  Status:   {escape(str(row.get('detail') or 'idle'))}")
    started = state.get('started_at')
    if started and now:
        lines.append(f"  Runtime:  {duration(int(now - started))}")
    tools = int(subagent.get('tools') or state.get('tools') or 0)
    lines.append(f"  Tools:    {_tool_uses(tools)}")
    tokens = subagent.get('tokens')
    if tokens is not None:
        lines.append(f"  Tokens:   {_format_count(int(tokens))}")
    if state.get('error'):
        lines += ['', f"[bold]Error[/bold] {escape(str(state['error']))}"]
    activity = subagent.get('last_tool') or state.get('last_tool')
    recent = recent_rollup(list(subagent.get('recent')
                                or state.get('recent') or []))
    if activity or recent:
        lines += ['', '[bold]Activity[/bold]']
        if recent:
            lines.append(f"  {escape(str(recent))}")
        if activity:
            lines.append(f"  {escape(str(activity))}")
    return lines


def tool_rows(schemas: list) -> list[str]:
    lines = [f'[bold]Tools[/bold] {len(schemas)}']
    lines += [f"  {escape(str(tool['name']))}"
              for tool in schemas[:LISTED_TOOLS]]
    return lines


def panel_text(sections: list[list[str]]) -> str:
    lines: list[str] = []
    for section in sections:
        if not section:
            continue
        if lines:
            lines.append('')
        lines += section
    return '\n'.join(lines)


class TaskPanelMixin:

    def _task_rows(self) -> list:
        return self._session.task_rows()

    async def _stop_task_row(self, row: dict) -> str:
        note = await self._session.stop_task(row)
        logger.info("stopped background task %s: %s", row.get('id'), note)
        await self._refresh_agents()
        return note

    def _task_detail(self, row) -> dict:
        current = next((candidate for candidate in self._task_rows()
                        if candidate['kind'] == row['kind']
                        and candidate['id'] == row['id']), None)
        row = current or row
        shell = row['kind'] == 'shell'
        return {'row': row,
                'output': background.output_of(row['id']) if shell else '',
                'state': {} if shell else self._state(row['id']),
                'subagent': {} if shell else dict(
                    self._subagents.get(row['id'], {})),
                'now': time.monotonic()}

    def _tasks_hint(self) -> str:
        if not self._teammates():
            return ''
        if self._expanded_view == 'none':
            action = 'shows the task list'
        elif self._expanded_view == 'tasks':
            action = 'shows the teammate tree'
        else:
            action = 'hides them'
        return f"[dim]{keys.display('toggle_tasks')} {action}[/]"

    def _render_tasks(self):
        if self._team is None:
            return
        names = [str(getattr(agent, 'name', ''))
                 for agent in self._teammates()]
        colors = {name: self._agent_color(name) for name in names}
        plan = plan_lines(self._plan(),
                          columns=self.screen.size.width or self.size.width,
                          rows=self.screen.size.height or self.size.height,
                          brand=self.brand,
                          colors=colors,
                          activity=self._plan_activity(),
                          alive=set(names),
                          recent=recent_completions(self._plan(),
                                                    self._task_seen,
                                                    time.monotonic()))
        self._tasks_pane.update(panel_text([
            plan,
            agent_tree(self._team, self._agent_state, colors),
            subagent_rows(self._subagents),
            shell_rows(background.snapshot()),
            tool_rows(self._team.tool_schemas(self._team.tool_context))]))

    async def action_toggle_tasks(self):
        teammates = bool(self._teammates())
        view = self._expanded_view
        if teammates:
            order = {'none': 'tasks', 'tasks': 'teammates'}
            nxt = order.get(view, 'none')
        else:
            nxt = 'none' if view == 'tasks' else 'tasks'
        self._expanded_view = nxt
        if nxt != 'teammates':
            self._view_selection = 'none'
            self._selected_index = -1
        pane = self.query_one("#tasks", VerticalScroll)
        pane.display = nxt == 'tasks'
        if nxt == 'tasks':
            self._render_tasks()
        await self._refresh_agents()
