import asyncio
import datetime
import itertools
import json
import logging
import os
import uuid
from pathlib import Path
from typing import AsyncIterator, Callable, Optional

from conippets import jsonl

from chatchat.team import Team
from chatchat.hooks.events import (
    AGENT_PROGRESS,
    AGENT_TEXT,
    register_runtime_handler,
)

from chatchat.tool import ToolContext

from .plugins import discover_skills, discover_tools
from .skills import skill_roots
from .tools import tools as base_tools
from .tools.coding import CODING_TOOLS, PermissionController, PermissionMode, \
    parse_mode

_name_counter = itertools.count()
_sessions_by_root: dict[str, 'Session'] = {}


def configured_context_window() -> int:
    from .config import load
    return int(load().get('contextWindow') or 0)


def session_for(team_name) -> Optional['Session']:
    return _sessions_by_root.get(team_name)


def _resolve_tools(tools):
    if tools is None:
        return list(base_tools) + discover_tools()
    return tools


def _resolve_skills(skills):
    if skills is None:
        return list(skill_roots) + discover_skills()
    return skills


def team_instruction(tool_names: list) -> str:
    names = ', '.join(tool_names)
    return f'''You are PyClaw, the leader of a task-executing team.

You have no direct tools. Your sub-agents are equipped with tools: {names}.

Whenever the user's request needs any tool, or benefits from parallel work, create a sub-agent with the `create_agent` tool and delegate the task, then relay its result and answer the user.

For simple requests that only need text, reply directly. Be helpful, accurate and concise.'''


def agent_instruction(tool_names: list) -> str:
    names = ', '.join(tool_names)
    return f'''You are PyClaw, a capable AI assistant with tools: {names}.

Use tools to complete the user's requests, then answer with the results. Be helpful, accurate and concise.'''


PLAN_NOTE = '''

You are in PLAN MODE (read-only). Investigate the workspace, explore and
propose a plan, but do NOT edit files or perform state-changing operations.
Read/Glob/Grep/LS and read-only shell commands are available; Write, Edit and
state-changing commands are blocked until the user approves a plan.'''


IM_EXTRA = '''You are PyClaw, an AI assistant on an instant messaging platform (QQ/WeChat).

Rules:
1. Keep responses very short and concise. One to three sentences is usually enough.
2. Only give detailed explanations or long output when the user explicitly asks for it.
3. Do not use markdown formatting — plain text only.
4. Be conversational and direct.
5. If you use tools, briefly summarize the result without technical details.'''


def _logs_dir() -> Path:
    home = os.environ.get("PYCLAW_HOME", str(Path.home() / ".pyclaw"))
    logs = Path(home) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def _session_dir(session_id) -> Path:
    session = _logs_dir() / str(session_id)
    session.mkdir(parents=True, exist_ok=True)
    return session


def _session_index_path() -> Path:
    return _logs_dir() / "session_index.json"


def resolve_session_id(logical_key, *, rotate: bool = False) -> str:
    path = _session_index_path()
    index = {}
    if path.exists():
        index = json.loads(path.read_text(encoding="utf-8"))
    key = json.dumps(logical_key, ensure_ascii=False, sort_keys=True)
    session_id = index.get(key)
    if session_id is None or rotate:
        session_id = uuid.uuid4().hex
        index[key] = session_id
        path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    return session_id


def transcript_path(session_id) -> Path:
    return _session_dir(session_id) / "transcript.jsonl"


_ENTRY_META = ("uuid", "parentUuid")


def load_entries(session_id) -> list:
    path = transcript_path(session_id)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            entries.append(record)
    return entries


def load_transcript(session_id) -> list:
    return [{key: value for key, value in entry.items()
             if key not in _ENTRY_META}
            for entry in load_entries(session_id)]


def list_sessions() -> list:
    root = _logs_dir()
    if not root.exists():
        return []
    sessions = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        path = entry / 'transcript.jsonl'
        if not path.exists():
            continue
        sessions.append({'id': entry.name,
                         'messages': len(load_entries(entry.name)),
                         'modified': path.stat().st_mtime})
    sessions.sort(key=lambda item: item['modified'], reverse=True)
    return sessions


def _content_of(entry) -> dict:
    return {key: value for key, value in entry.items()
            if key not in _ENTRY_META}


def _chained(payload, parent):
    records = []
    for message in payload:
        record = dict(message)
        record["uuid"] = uuid.uuid4().hex
        record["parentUuid"] = parent
        parent = record["uuid"]
        records.append(record)
    return records


def _append_records(path, records) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_records(path, records) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def save_transcript(session_id, messages) -> None:
    path = transcript_path(session_id)
    payload = [m for m in messages if isinstance(m, dict)]
    entries = load_entries(session_id)
    written = 0
    while (written < len(entries) and written < len(payload)
           and _content_of(entries[written]) == payload[written]):
        written += 1
    if written == len(payload) == len(entries):
        return
    if written and written == len(entries):
        _append_records(path, _chained(payload[written:],
                                       entries[-1].get("uuid")))
        return
    _write_records(path, _chained(payload, None))


_session_loggers: dict[str, logging.Logger] = {}


def session_logger(session_id) -> logging.Logger:
    if session_id not in _session_loggers:
        name = f"session.{session_id}"
        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)
        log.propagate = False
        handler = logging.FileHandler(
            _session_dir(session_id) / "run.log", encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s"
        ))
        log.addHandler(handler)
        _session_loggers[session_id] = log
    return _session_loggers[session_id]


def close_session_logger(session_id) -> None:
    log = _session_loggers.pop(session_id, None)
    if log is not None:
        for handler in list(log.handlers):
            handler.close()
            log.removeHandler(handler)


def record_meta(session_id, meta: dict) -> None:
    path = _session_dir(session_id) / "meta.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing.setdefault("session_id", str(session_id))
    existing.update(meta)
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def append_conv(session_id, role, content, *, reasoning_content=None, topic=None,
                name=None, path=None):
    record = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "role": role,
        "content": content,
    }
    if reasoning_content:
        record["reasoning_content"] = reasoning_content
    if topic:
        record["topic"] = topic
    if name:
        record["name"] = name
    jsonl.append(path or _session_dir(session_id) / "messages.jsonl", [record])


def _dispatch_event(on_event, ev):
    if asyncio.iscoroutinefunction(on_event):
        asyncio.create_task(on_event(ev))
    else:
        on_event(ev)


def build_team(
    provider: str,
    model: str,
    instruction: Optional[str] = None,
    tools: Optional[list] = None,
    skills: Optional[list] = None,
    thinking: bool = True,
    http_options: Optional[dict] = None,
    cwd: Optional[str] = None,
    permission_mode: str = 'default',
    allow: Optional[list] = None,
    ask: Optional[list] = None,
    deny: Optional[list] = None,
    use_team: bool = False,
) -> Team:
    cwd = cwd or os.getcwd()
    _resolve_skills(skills)
    coding_tools = list(CODING_TOOLS)
    coding_names = {t.name for t in coding_tools}
    candidates = coding_tools + [t for t in _resolve_tools(tools)
                                 if t.name not in coding_names]
    gate = PermissionController(mode=permission_mode, cwd=cwd, allow=allow or (),
                                ask=ask or (), deny=deny or (),
                                tools=candidates)
    resolved = [t for t in candidates if gate.allowed_tool(t.name)]
    names = [t.name for t in resolved]
    model_timeout = (http_options or {}).get('timeout', 120)
    inst = instruction or (team_instruction(names) if use_team
                           else agent_instruction(names))
    if gate.mode is PermissionMode.plan:
        inst = inst + PLAN_NOTE
    team = Team(
        f'pyclaw-{next(_name_counter)}',
        provider=provider,
        model=model,
        tools=resolved,
        tool_context=ToolContext(cwd=Path(cwd).resolve()),
        lead_instruction=inst,
        thinking=bool(thinking),
        model_timeout=model_timeout,
        http_options=http_options or {},
        mailbox_dir=os.path.join(cwd, '.pyclaw', 'teams'),
        multi_agent=use_team,
        context_window=configured_context_window(),
    )
    team._pyclaw_gate = gate
    team._pyclaw_mode = 'team' if use_team else 'agent'

    from .agent_memory import load_instruction_files, load_project_memory
    team.set_instruction_files(load_instruction_files(cwd))
    memory = load_project_memory(cwd)
    if memory:
        team.set_lead_instruction(team.lead.instruction + '\n\n' + memory)

    from .agent_defs import builtin_agent_defs, load_agent_defs
    for defn in builtin_agent_defs(all_tools=resolved):
        team.register_agent_definition(defn)
    for defn in load_agent_defs(cwd, all_tools=resolved):
        team.register_agent_definition(defn)

    from .tools.coding import background as _background

    def _notify_task_finished(task_id, command, code, killed):
        status = ('killed' if killed
                  else ('completed' if code == 0 else 'failed'))
        team.lead.enqueue_attachment(
            f'<background_done>\n<task_ref>{task_id}</task_ref>\n'
            f'<command>{command}</command>\n'
            f'<log_file>{_background._output_path(task_id)}</log_file>\n'
            f'<result>{status}</result>\n'
            f'<note>The background command returned {code}.</note>\n'
            f'</background_done>')

    _background.set_notifier(_notify_task_finished)

    async def _permission_gate(hook_input):
        agent_type = hook_input.get('agent_type') or ''
        mode = None
        if agent_type and gate.mode not in (PermissionMode.bypass_permissions,
                                            PermissionMode.accept_edits):
            defn = team.agent_defs.find(agent_type)
            mode = (defn.permission_mode if defn is not None else None) \
                or 'acceptEdits'
        asker = str(hook_input.get('agent_id') or '')
        return await gate.authorize(hook_input.get('tool_name', ''),
                                    hook_input.get('tool_input') or {},
                                    mode=mode,
                                    tool_use_id=str(
                                        hook_input.get('tool_use_id') or ''),
                                    agent='' if asker == team.lead.name
                                    else asker)

    team.hooks.permission_mode = gate.mode.value
    team.hooks.register('PreToolUse', fn=_permission_gate, timeout=3600)

    async def _permission_request(tool_name, tool_input, tool_use_id, agent):
        asker = (team.get_by_name(agent) if agent else None) or team.lead
        agg = await team.hooks.execute_permission_request_hooks(
            asker, tool_name, tool_input or {}, tool_use_id=tool_use_id)
        if agg.decision == 'allow':
            return {'behavior': 'allow', 'updated_input': agg.updated_input,
                    'message': ''}
        if agg.decision == 'deny' or agg.blocking_error is not None:
            return {'behavior': 'deny', 'updated_input': None,
                    'message': (agg.blocking_error.blocking_error
                                if agg.blocking_error else '')}
        return None

    gate.permission_hooks = _permission_request
    return team


class Session:
    def __init__(self, entity, session_id=None, resume_from=None):
        self._team: Team = entity
        self._provider = entity.provider
        self._model = entity.model
        self._thinking = entity.thinking
        self._tools = entity.provided_tools
        self.mode = getattr(entity, '_pyclaw_mode', 'agent')
        self.name = entity.name
        self.deliver = None
        self.conv_session_id = session_id or entity.name
        self.resume_from = resume_from
        self._conv_reply = ""
        self._conv_thinking = ""
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
    def thinking(self) -> bool:
        return self._thinking

    @property
    def available_tools(self) -> list:
        return [t['name'] for t in self._team.tool_schemas(self._team.tool_context)]

    @property
    def context_messages(self) -> int:
        return len(self._team.transcript())

    def transcript(self) -> list:
        return self._team.transcript()

    def save_transcript(self):
        if self.conv_session_id:
            save_transcript(self.conv_session_id, self._team.transcript())

    def restore_transcript(self) -> int:
        messages = load_transcript(self.resume_from or self.conv_session_id)
        if messages:
            self._team.restore(messages)
        return len(messages)

    @property
    def active_agents(self) -> int:
        return len(self._team.agents) - 1

    @property
    def agent_types(self) -> list:
        return self._team.agent_defs.describe()

    @property
    def total_usage(self) -> dict:
        return {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}

    def set_thinking(self, on: bool):
        self._thinking = bool(on)
        self._team.set_thinking(self._thinking)

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
    def context_tokens(self) -> int:
        return int(self._team.context_tokens)

    @property
    def context_window(self) -> int:
        return configured_context_window()

    @property
    def used_context(self) -> int:
        last = self._team.last_usage()
        return int(last.prompt_tokens + last.completion_tokens)

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

    def permission_rule(self, tool_name: str, tool_input) -> str:
        if self._gate is None:
            return ''
        return self._gate.suggested_rule(tool_name, tool_input) or ''

    def submit(self, text: str, *, cancelable_tools: tuple = ()):
        self._team.lead.interrupt_and_submit(text, cancelable_tools=cancelable_tools)

    async def end_session(self, reason: str):
        await self._team.end_session(reason)

    def reset(self):
        self.conv_session_id = uuid.uuid4().hex
        self.resume_from = None
        self._team.lead.messages = []
        self._team.reset_usage()
        self._team.begin_new_session('clear')

    def resume_session(self, session_id: str) -> int:
        messages = load_transcript(session_id)
        if not messages:
            return 0
        self._team.restore(messages)
        self._team.begin_new_session('resume')
        self.conv_session_id = uuid.uuid4().hex
        self.resume_from = None
        return len(messages)

    @property
    def usage(self):
        return self._team.usage()

    def schedule_delivery(self, text: str, when: str):
        if self.deliver is None:
            return 'Delivery is not available for this session.'
        from .task import schedule_delivery
        job = schedule_delivery(text, when, self.deliver)
        at = job['next'].strftime('%Y-%m-%d %H:%M:%S') if job.get('next') else 'later'
        return f"Delivery {job['id']} queued for {at}: {text}"

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
