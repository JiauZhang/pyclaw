import asyncio
import datetime
import logging
import os
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from chatchat.team import Team
from chatchat.core.metrics import Metrics
from chatchat.core.thinking import Thinking
from chatchat.hooks.events import (
    AGENT_PROGRESS,
    AGENT_TEXT,
    register_runtime_handler,
)

from .session_store import (_session_dir, append_conv, close_session_logger,
                            load_transcript, save_transcript)
from .team_builder import _dispatch_event, configured_context_window
from .tools.coding import parse_mode

_sessions_by_root: dict[str, 'Session'] = {}






def session_for(team_name) -> Optional['Session']:
    return _sessions_by_root.get(team_name)










































_session_loggers: dict[str, logging.Logger] = {}


















class Session:
    def __init__(self, entity, session_id=None, resume_from=None):
        self._team: Team = entity
        self._provider = entity.provider
        self._model = entity.model
        self._tools = entity.provided_tools
        self.mode = getattr(entity, '_pyclaw_mode', 'agent')
        self.name = entity.name
        self.conv_session_id = session_id or entity.name
        self.resume_from = resume_from
        self._conv_reply = ""
        self._conv_thinking = ""
        self._seen = self._seen_zero()
        self._unreg = None
        self._bind_gen = 0
        self._gate = getattr(entity, '_pyclaw_gate', None)
        if session_id:
            self._team.sidechain_dir = _session_dir(session_id) / "subagents"
        _sessions_by_root[entity.name] = self

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    @property
    def thinking(self) -> Thinking:
        return self._team.thinking

    @property
    def available_tools(self) -> list:
        return [t['name'] for t in self._team.tool_schemas(self._team.tool_context)]

    @property
    def context_messages(self) -> int:
        return len(self._team.transcript())

    def transcript(self) -> list:
        return self._team.transcript()

    @property
    def lead_instruction(self) -> str:
        return str(self._team.lead.instruction or '')

    @property
    def instruction_files(self) -> list:
        return list(self._team.instruction_files)

    def tool_schemas(self) -> list:
        return self._team.tool_schemas(self._team.tool_context)

    def save_transcript(self):
        if self.conv_session_id:
            save_transcript(self.conv_session_id, self._team.transcript())

    def restore_transcript(self) -> int:
        messages = load_transcript(self.resume_from or self.conv_session_id)
        if messages:
            self._team.restore(messages)
        return len(messages)

    def turns(self) -> list:
        return self._team.turns()

    def rewind_stats(self, mark: int) -> dict | None:
        history = self._team.file_history
        return None if history is None else history.diff_stats(mark)

    async def note_config_change(self, source: str):
        await self._team.hooks.execute_config_change_hooks(self._team.lead,
                                                           source)

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

    def task_rows(self) -> list:
        from pyclaw.tools.coding import background
        rows = []
        lead = self._team.lead
        teammates = [agent for agent in self._team.agents.values()
                     if agent is not lead and not getattr(agent, '_internal',
                                                          False)
                     and getattr(agent, 'is_running', True)]
        for agent in sorted(teammates, key=lambda a: str(a.name)):
            rows.append({'kind': 'teammate', 'id': str(agent.name),
                         'label': f'@{agent.name}',
                         'detail': 'working' if agent.busy else 'idle',
                         'stoppable': True})
        for agent_id in self._team.background:
            agent = self._team.agents.get(agent_id)
            if agent is None:
                continue
            rows.append({'kind': 'sub-agent', 'id': str(agent.name),
                         'label': f'@{agent.name}',
                         'detail': 'in the background', 'stoppable': True})
        for shell in background.snapshot():
            rows.append({'kind': 'shell', 'id': shell['id'],
                         'label': shell['command'],
                         'detail': (f'running {shell["seconds"]}s'
                                    if shell['exit'] is None
                                    else f'exited {shell["exit"]}'),
                         'stoppable': shell['exit'] is None})
        return rows

    async def stop_task(self, row: dict) -> str:
        from pyclaw.tools.coding import background
        if row['kind'] == 'shell':
            background.stop(row['id'])
            return f'Stopped the shell {row["id"]}.'
        agent = next((candidate for candidate in self._team.agents.values()
                      if str(getattr(candidate, 'name', '')) == row['id']),
                     None)
        if agent is None:
            return f'{row["label"]} is already gone.'
        await self._team.stop_agent(agent)
        return f'Stopped {row["label"]}.'

    def diff(self) -> dict:
        import subprocess
        from pathlib import Path

        cwd = Path(self._team.tool_context.cwd)

        def git(*args):
            try:
                done = subprocess.run(('git', *args), cwd=str(cwd),
                                      capture_output=True, text=True)
            except OSError:
                return None
            return done if done.returncode == 0 else None

        status = git('status', '--porcelain')
        if status is not None:
            changed = sorted(line[3:].strip() for line in
                             status.stdout.splitlines() if line[1:2] != '?')
            untracked = sorted(line[3:].strip() for line in
                               status.stdout.splitlines()
                               if line.startswith('?? '))
            body = git('diff')
            text = (body.stdout if body else '')
            if untracked:
                text += ('' if not text else '\n') + '\n'.join(
                    f'?? {name} (not in git yet)' for name in untracked)
            return {'kind': 'git', 'files': changed + untracked,
                    'text': text or 'The working tree matches the last commit.'}
        history = self._team.file_history
        stats = history.session_stats() if history is not None else {}
        if not stats:
            return {'kind': 'session', 'files': [],
                    'text': 'Nothing has been changed in this session.'}
        return {'kind': 'session', 'files': sorted(stats),
                'text': '\n'.join(
                    f"{name} \u00b7 +{entry['insertions']} "
                    f"-{entry['deletions']}"
                    for name, entry in sorted(stats.items()))}

    def rewind(self, mark: int, *, code: bool = True,
               conversation: bool = True) -> dict:
        result = self._team.rewind(mark, code=code, conversation=conversation)
        if result['messages']:
            self.save_transcript()
        return result

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

    def set_thinking(self, thinking: Thinking):
        self._team.set_thinking(thinking)

    def set_model(self, model: str) -> str:
        self._model = model
        self._team.set_model(model)
        return self._model

    @property
    def permission_mode(self) -> str:
        return self._gate.mode.value if self._gate is not None else 'default'

    @property
    def bypass_available(self) -> bool:
        return bool(self._gate is not None and self._gate.bypass_available)

    @property
    def cwd(self) -> str:
        return str(self._gate.cwd) if self._gate is not None else os.getcwd()

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

    def set_permission_mode(self, mode: str) -> str:
        if self._gate is None:
            raise ValueError('Permission gate not available for this session.')
        parsed = parse_mode(mode)
        self._gate.mode = parsed
        self._team.hooks.permission_mode = parsed.value
        return parsed.value

    def attach_approval(self, coro):
        if self._gate is not None:
            self._gate.request = coro

    def attach_question(self, coro):
        self._team.ask_user = coro

    @property
    def snapshot_updates(self) -> list:
        return list(getattr(self._team, '_pyclaw_snapshot_updates', []))

    def apply_snapshot_update(self, agent_type: str, scope: str) -> str:
        return self._team.agent_memory.apply_snapshot(agent_type, scope)

    def permission_rule(self, tool_name: str, tool_input) -> str:
        if self._gate is None:
            return ''
        return self._gate.suggested_rule(tool_name, tool_input) or ''

    def submit(self, text: str, *, cancelable_tools: tuple = ()):
        self._team.lead.interrupt_and_submit(text, cancelable_tools=cancelable_tools)

    async def end_session(self, reason: str):
        await self._team.end_session(reason)

    def _seen_zero(self) -> dict:
        return {'input': 0, 'output': 0, 'cached': 0,
                **{name: 0 for name in Metrics().as_dict()}}

    def record_turn(self) -> dict:
        from .usage_history import record, row

        usage = self._team.usage()
        details = getattr(usage, 'prompt_tokens_details', None) or {}
        metrics = self._team.total_metrics().as_dict()
        seen = self._seen
        turn = datetime.datetime.now().astimezone()
        entry = row(turn, str(self.conv_session_id or self.name),
                    self._provider, self._model,
                    input_tokens=usage.prompt_tokens - seen['input'],
                    output_tokens=usage.completion_tokens - seen['output'],
                    cached=(int(details.get('cached_tokens') or 0)
                            - seen['cached']),
                    turns=sum(1 for message in self._team.transcript()
                              if message.get('role') == 'assistant'),
                    metrics={name: value - seen[name]
                             for name, value in metrics.items()})
        self._seen = {'input': usage.prompt_tokens,
                      'output': usage.completion_tokens,
                      'cached': int(details.get('cached_tokens') or 0),
                      **metrics}
        record(entry)
        return entry

    def reset(self):
        self._seen = self._seen_zero()
        self.conv_session_id = uuid.uuid4().hex
        self.resume_from = None
        self._team.lead.messages = []
        self._team.reset_usage()
        self._team.reset_rules()
        self._team.begin_new_session('clear')

    def resume_session(self, session_id: str) -> int:
        messages = load_transcript(session_id)
        if not messages:
            return 0
        self._team.restore(messages)
        self._team.reset_rules()
        self._team.begin_new_session('resume')
        self.conv_session_id = uuid.uuid4().hex
        self.resume_from = None
        return len(messages)

    @property
    def usage(self):
        return self._team.usage()

    def _member_names(self) -> set:
        return {agent.name for agent in self._team.agents.values()}

    def _in_scope(self, ev) -> bool:
        if ev.kind == AGENT_PROGRESS:
            return True
        return ev.agent == '' or ev.agent in self._member_names()

    def _bind(self, on_event: Callable):
        self._unbind()
        self._bind_gen += 1
        self._unreg = register_runtime_handler(on_event)

    def _unbind(self):
        if self._unreg is not None:
            self._unreg()
            self._unreg = None

    def _flush_conv(self):
        session_id = self.conv_session_id
        reply, thinking = self._conv_reply, self._conv_thinking
        self._conv_reply, self._conv_thinking = "", ""
        if reply or thinking:
            append_conv(session_id, "assistant", reply,
                        reasoning_content=thinking or None)

    async def chat(self, message: str, on_event: Optional[Callable] = None) -> str:
        if on_event is not None:
            out = await self._run(message, on_event)
        else:
            out = await self._team.query(message)
        self.save_transcript()
        return out

    def stream(self, message: str, on_event: Optional[Callable] = None) -> AsyncIterator[str]:
        async def gen():
            async for text in self._stream(message, on_event):
                yield text
        return gen()

    async def _run(self, message: str, on_event: Callable) -> str:
        def on_ev(ev):
            if not self._in_scope(ev):
                return
            if ev.kind == AGENT_TEXT:
                self._conv_reply += ev.data.get('delta', '')
            _dispatch_event(on_event, ev)

        self._bind(on_ev)
        gen = self._bind_gen
        try:
            out = await self._team.query(message)
            self._flush_conv()
            return out
        finally:
            if gen == self._bind_gen:
                self._unbind()

    async def _stream(self, message: str, on_event: Optional[Callable] = None):
        queue: asyncio.Queue = asyncio.Queue()

        def on_ev(ev):
            if not self._in_scope(ev):
                return
            if ev.kind == AGENT_TEXT:
                delta = ev.data.get('delta', '')
                if delta:
                    self._conv_reply += delta
                    queue.put_nowait(delta)
            if on_event is not None:
                _dispatch_event(on_event, ev)

        self._bind(on_ev)
        task = asyncio.create_task(self._team.query(message))
        try:
            while not task.done():
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
            while not queue.empty():
                yield queue.get_nowait()
        finally:
            self._unbind()
        await task
        self._flush_conv()
        self.save_transcript()

    async def close(self):
        self._unbind()
        _sessions_by_root.pop(self.name, None)

    @property
    def cwd(self):
        return str(self._gate.cwd) if self._gate is not None else '.'

    async def compact(self) -> str:
        before = len(self._team.lead.messages)
        result = await self._team.compact(list(self._team.lead.messages),
                                          force=True)
        self._team.lead.messages = list(result)
        return f'Compacted: {before} -> {len(result)} messages'

    def permission_rules(self):
        gate = self._gate
        return gate.rule_listing() if gate is not None else []

    def remove_rule(self, rule: str) -> bool:
        gate = self._gate
        return gate.remove_rule(rule) if gate is not None else False
        close_session_logger(self.conv_session_id)
