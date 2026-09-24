from __future__ import annotations

import logging
import time

from rich.markup import escape
from textual.containers import VerticalScroll

from pyclaw.tools.coding import background
from pyclaw.tui.formatting import _format_count, _summarize, duration
from pyclaw.tui.plan import plan_lines, recent_completions
from pyclaw.tui.theme import RESULT_GLYPH
from pyclaw.tui.toolcard import _tool_uses, recent_rollup

logger = logging.getLogger(__name__)

LISTED_TOOLS = 40
BRANCH_LAST = '└─'
BRANCH_MIDDLE = '├─'
PIPE_LAST = '   '
PIPE_MIDDLE = '│  '


def agent_tree(team, states: dict) -> list[str]:
    lines = ['[bold]Agents[/bold]']

    def walk(agent_id, prefix, last):
        agent = team.agents.get(agent_id)
        if agent is None:
            return
        state = states.get(agent.name, {})
        status = ('working…' if state.get('think')
                  else ('busy' if state.get('busy') else 'idle'))
        lines.append(f"{prefix}{BRANCH_LAST if last else BRANCH_MIDDLE} "
                     f"{escape(agent.name)} · "
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

    def _tasks_hint(self) -> str:
        if not self._teammates():
            return ''
        if self._expanded_view == 'none':
            action = 'shows the task list'
        elif self._expanded_view == 'tasks':
            action = 'shows the teammate tree'
        else:
            action = 'hides them'
        return f"[dim]ctrl+t {action}[/]"

    def _render_tasks(self):
        if self._team is None:
            return
        names = [str(getattr(agent, 'name', ''))
                 for agent in self._teammates()]
        plan = plan_lines(self._plan(),
                          columns=self.screen.size.width or self.size.width,
                          rows=self.screen.size.height or self.size.height,
                          brand=self.brand,
                          colors={name: self._agent_color(name)
                                  for name in names},
                          activity=self._plan_activity(),
                          alive=set(names),
                          recent=recent_completions(self._plan(),
                                                    self._task_seen,
                                                    time.monotonic()))
        self._tasks_pane.update(panel_text([
            plan,
            agent_tree(self._team, self._agent_state),
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
