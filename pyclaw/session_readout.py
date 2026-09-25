from __future__ import annotations

from pyclaw import agent_defs, task_registry
from pyclaw.team_builder import configured_context_window
from pyclaw.tools import background


class ReadoutMixin:

    @property
    def available_tools(self) -> list:
        return [t['name'] for t in self._team.tool_schemas(self._team.tool_context)]
    @property
    def context_messages(self) -> int:
        return len(self._team.transcript())
    def transcript(self) -> list:
        return self._team.transcript()
    @property
    def instruction_files(self) -> list:
        return list(self._team.instruction_files)
    def skill_rows(self) -> list:
        return [{'name': skill.name, 'description': skill.description,
                 'source': skill.source, 'allowed_tools': skill.allowed_tools}
                for skill in self._team.skills.all()]
    def skill_problems(self) -> list:
        return list(self._team.skills.problems)
    @property
    def worktree(self) -> dict | None:
        worktree = self._team.worktree
        return None if worktree is None else dict(worktree)
    @property
    def team_context(self) -> dict | None:
        context = self._team.team_context
        return None if context is None else dict(context)
    def hook_rows(self) -> list:
        return self._team.hooks.configured()
    def _sync_agent_tasks(self) -> dict:
        from pyclaw import task_registry as tasks

        registry = tasks.registry()
        lead = self._team.lead
        seen = {}
        for agent in self._team.agents.values():
            if agent is lead:
                continue
            if agent.agent_id in self._team.background:
                kind, detail = 'sub-agent', 'in the background'
            else:
                if getattr(agent, '_internal', False):
                    continue
                if not getattr(agent, 'is_running', True):
                    continue
                kind = 'teammate'
                detail = ('working' if getattr(agent, 'busy', False)
                          else 'idle')
            name = str(agent.name)
            seen[name] = kind
            record = registry.get(name)
            if record is None:
                registry.register(tasks.Task(
                    id=name, kind=kind, label=f'@{name}',
                    status=tasks.RUNNING, detail=detail))
            elif not tasks.is_terminal(record.status):
                record.kind = kind
                record.detail = detail
        for record in (registry.of_kind('teammate')
                       + registry.of_kind('sub-agent')):
            if record.id not in seen and not tasks.is_terminal(record.status):
                record.finish(tasks.COMPLETED)
        return seen

    def task_rows(self) -> list:
        from pyclaw import task_registry as tasks
        from pyclaw.tools import background

        registry = tasks.registry()
        registry.sweep()
        seen = self._sync_agent_tasks()
        rows = []
        order = {'teammate': 0, 'sub-agent': 1, 'shell': 2}
        listed = sorted(registry.visible(),
                        key=lambda record: (order.get(record.kind, 9),
                                            record.started_at))
        for record in listed:
            if record.kind == 'shell':
                rows.append(background.row(record))
                continue
            rows.append({'kind': record.kind, 'id': record.id,
                         'label': record.label, 'detail': record.detail,
                         'stoppable': record.id in seen})
        return rows

    async def stop_task(self, row: dict) -> str:
        from pyclaw import task_registry as tasks
        from pyclaw.tools import background

        if row['kind'] == 'shell':
            background.stop(row['id'])
            return f'Stopped the shell {row["id"]}.'
        record = tasks.registry().get(row['id'])
        if record is not None and not tasks.is_terminal(record.status):
            record.detail = 'stopped'
            record.finish(tasks.KILLED)
        agent = next((candidate for candidate in self._team.agents.values()
                      if str(getattr(candidate, 'name', '')) == row['id']),
                     None)
        if agent is None:
            return f'{row["label"]} is already gone.'
        await self._team.stop_agent(agent)
        return f'Stopped {row["label"]}.'
    @property
    def active_agents(self) -> int:
        return len(self._team.agents) - 1
    @property
    def cli_agent_defs(self) -> list:
        return list(getattr(self._team, 'cli_agent_defs', []))
    @property
    def agent_types(self) -> list:
        return self._team.agent_defs.describe()
    def agent_usage(self) -> list:
        return sorted(
            ((str(agent.name), str(getattr(agent.client, 'model', '') or ''),
              agent.total_usage)
             for agent in self._team.agents.values() if not agent._internal),
            key=lambda row: str(row[0]))
    @property
    def permission_mode(self) -> str:
        return self._gate.mode.value if self._gate is not None else 'default'
    @property
    def bypass_available(self) -> bool:
        return bool(self._gate is not None and self._gate.bypass_available)
    @property
    def compact_threshold(self) -> int:
        return int(self._team.compact_threshold)
    @property
    def context_window(self) -> int:
        return configured_context_window()
    @property
    def used_context(self) -> int:
        last = self._team.last_usage()
        return 0 if last is None else int(last.prompt_tokens
                                          + last.completion_tokens)
    @property
    def last_usage(self):
        return self._team.last_usage()
    @property
    def auto_compact(self) -> bool:
        return bool(self._team.auto_compact)
    @property
    def snapshot_updates(self) -> list:
        return list(getattr(self._team, '_pyclaw_snapshot_updates', []))
    @property
    def usage(self):
        return self._team.usage()
    def permission_rules(self):
        gate = self._gate
        return gate.rule_listing() if gate is not None else []
