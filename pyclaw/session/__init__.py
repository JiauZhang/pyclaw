import asyncio
import datetime
import logging
import subprocess
import os
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from chatchat.team.team import Team
from chatchat.runtime.metrics import Metrics
from chatchat.runtime.thinking import Thinking
from chatchat.hooks.events import (
    AGENT_PROGRESS,
    AGENT_TEXT,
    register_runtime_handler,
)

from pyclaw import config
from pyclaw.tools.bash import get_default_timeout_ms
from pyclaw.usage_history import record, row
from .history import HistoryMixin
from .readout import ReadoutMixin
from .store import (_session_path, append_conv, follow_conversation,
                    adopt_artifacts, history_dir, load_transcript, plan_file,
                    save_transcript)
from pyclaw.team.builder import (_dispatch_event, checkpoints_enabled,
                                 configured_context_window)
from pyclaw.permissions import parse_mode

_sessions_by_root: dict[str, 'Session'] = {}






def session_for(team_name) -> Optional['Session']:
    return _sessions_by_root.get(team_name)


























































class Session(HistoryMixin, ReadoutMixin):
    def __init__(self, entity, session_id=None, resume_from=None):
        self._team: Team = entity
        self._provider = entity.provider
        self._model = entity.model
        self._tools = entity.provided_tools
        self.mode = getattr(entity, '_pyclaw_mode', 'agent')
        self.name = entity.name
        self.resume_from = resume_from
        self._conv_reply = ""
        self._conv_thinking = ""
        self._seen = self._seen_zero()
        self._unreg = None
        self._bind_gen = 0
        self._gate = getattr(entity, '_pyclaw_gate', None)
        self.conv_session_id = session_id or uuid.uuid4().hex
        _sessions_by_root[entity.name] = self

    @property
    def conv_session_id(self) -> str:
        return self._conv_session_id

    @conv_session_id.setter
    def conv_session_id(self, value: str):
        """Moving to another conversation re-points every artifact store with
        it, so a plan or a snapshot can never land on another conversation."""
        self._conv_session_id = value
        team = self._team
        team.lead_session_id = value
        team.sidechain_dir = _session_path(value) / "subagents"
        team.plan_path = plan_file(value)
        if self._gate is not None:
            self._gate.plan_file = team.plan_path
        follow_conversation(value, level=logging.DEBUG if config.debug_on()
                            else logging.INFO)

    def _adopt_history(self):
        """Start the snapshot store of this conversation, which after a clear,
        a resume or a branch is a different one from before."""
        if checkpoints_enabled():
            self._team.use_file_history(history_dir(self.conv_session_id))

    @property
    def plan_path(self) -> Path:
        return self._team.plan_path

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
    def lead_instruction(self) -> str:
        return str(self._team.lead.instruction or '')


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



    async def note_config_change(self, source: str):
        await self._team.hooks.execute_config_change_hooks(self._team.lead,
                                                           source)













    def set_thinking(self, thinking: Thinking):
        self._team.set_thinking(thinking)

    def remember_thinking(self):
        saved = config.load()
        saved['thinking'] = {'mode': self.thinking.mode,
                             'budget': self.thinking.budget,
                             'effort': self.thinking.effort}
        config.save(saved)

    def toggle_thinking(self) -> str:
        current = self.thinking
        mode = 'on' if not current.enabled else 'off'
        self.set_thinking(Thinking(mode=mode, budget=current.budget,
                                   effort=current.effort))
        self.remember_thinking()
        return self.thinking.label()

    def set_model(self, model: str) -> str:
        self._model = model
        self._team.set_model(model)
        return self._model



    @property
    def cwd(self) -> str:
        return str(self._gate.cwd) if self._gate is not None else os.getcwd()






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


    def apply_snapshot_update(self, agent_type: str, scope: str) -> str:
        return self._team.agent_memory.apply_snapshot(agent_type, scope)

    def suggested_rules(self, tool_name: str, tool_input) -> list[str]:
        if self._gate is None:
            return []
        return self._gate.suggested_rules(tool_name, tool_input)

    def submit(self, text: str, *, cancelable_tools: tuple = ()):
        self._team.lead.interrupt_and_submit(text, cancelable_tools=cancelable_tools)

    async def end_session(self, reason: str):
        await self._team.end_session(reason)

    def _seen_zero(self) -> dict:
        return {'input': 0, 'output': 0, 'cached': 0,
                **{name: 0 for name in Metrics().as_dict()}}

    def record_turn(self) -> dict:
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
        self._adopt_history()

    def resume_session(self, session_id: str) -> int:
        messages = load_transcript(session_id)
        if not messages:
            return 0
        self._team.restore(messages)
        self._team.reset_rules()
        self._team.begin_new_session('resume')
        resumed = uuid.uuid4().hex
        adopt_artifacts(session_id, resumed)
        self.conv_session_id = resumed
        self._adopt_history()
        self.resume_from = None
        return len(messages)


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

    async def run_bash(self, command: str) -> dict:
        cwd = self.cwd
        limit = get_default_timeout_ms() / 1000
        try:
            done = subprocess.run(command, shell=True, cwd=cwd,
                                  capture_output=True, text=True,
                                  errors='replace', timeout=limit)
            out, err, code = done.stdout, done.stderr, done.returncode
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ''
            err = (exc.stderr or '') + f'\nThe command ran past {int(limit)}s.'
            code = None
        except OSError as exc:
            out, err, code = '', str(exc), None
        self._team.lead.messages.append(
            {'role': 'user', 'content': f'<bash-input>{command}</bash-input>'})
        self._team.lead.messages.append(
            {'role': 'user',
             'content': (f'<bash-stdout>{out}</bash-stdout>'
                         f'<bash-stderr>{err}</bash-stderr>')})
        if self.conv_session_id:
            append_conv(self.conv_session_id, 'user', f'!{command}')
            body = (out + err).rstrip('\n')
            if body:
                append_conv(self.conv_session_id, 'user', body)
        return {'command': command, 'stdout': out, 'stderr': err,
                'exit_code': code}

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


    def remove_rule(self, rule: str) -> bool:
        gate = self._gate
        return gate.remove_rule(rule) if gate is not None else False
