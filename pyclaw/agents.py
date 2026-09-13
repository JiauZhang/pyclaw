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

from .plugins import discover_skills, discover_tools
from .skills import skill_roots
from .tools import tools as base_tools
from .tools.coding import (PermissionController, PermissionMode,
                           build_coding_tools, parse_mode)

_name_counter = itertools.count()
_sessions_by_root: dict[str, 'Session'] = {}


def session_of(actor) -> Optional['Session']:
    team = getattr(actor, 'team', None)
    name = getattr(team, 'name', None) or getattr(actor, 'id', None)
    return _sessions_by_root.get(name)


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


def resolve_session_id(logical_key) -> str:
    path = _session_index_path()
    index = {}
    if path.exists():
        index = json.loads(path.read_text(encoding="utf-8"))
    key = json.dumps(logical_key, ensure_ascii=False, sort_keys=True)
    session_id = index.get(key)
    if session_id is None:
        session_id = uuid.uuid4().hex
        index[key] = session_id
        path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    return session_id


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
    max_depth: int = 5,
    max_steps: int = 10,
    cwd: Optional[str] = None,
    permission_mode: str = 'default',
    allow: Optional[list] = None,
    ask: Optional[list] = None,
    deny: Optional[list] = None,
) -> Team:
    cwd = cwd or os.getcwd()
    gate = PermissionController(mode=permission_mode, cwd=cwd, allow=allow or (),
                                ask=ask or (), deny=deny or ())
    coding_tools = build_coding_tools(cwd)
    coding_names = {t.name for t in coding_tools}
    _resolve_skills(skills)
    resolved = coding_tools + [t for t in _resolve_tools(tools)
                               if t.name not in coding_names]
    resolved = [t for t in resolved if gate.allowed_tool(t.name)]
    names = [t.name for t in resolved]
    model_timeout = (http_options or {}).get('timeout', 120)
    inst = instruction or team_instruction(names)
    if gate.mode is PermissionMode.plan:
        inst = inst + PLAN_NOTE
    team = Team(
        f'pyclaw-{next(_name_counter)}',
        provider=provider,
        model=model,
        tools=resolved,
        lead_instruction=inst,
        thinking=bool(thinking),
        model_timeout=model_timeout,
        http_options=http_options or {},
    )
    team._pyclaw_gate = gate

    async def _permission_gate(hook_input):
        return await gate.authorize(hook_input.get('tool_name', ''),
                                    hook_input.get('tool_input') or {})

    team.hooks.register('PreToolUse', fn=_permission_gate, timeout=3600)
    return team


class Session:
    def __init__(self, entity):
        self._team: Team = entity
        self._provider = entity.provider
        self._model = entity.model
        self._thinking = entity.thinking
        self._tools = entity.provided_tools
        self.mode = 'team'
        self.name = entity.name
        self.deliver = None
        self.conv_session_id = entity.name
        self._conv_reply = ""
        self._conv_thinking = ""
        self._unreg = None
        self._bind_gen = 0
        self._gate = getattr(entity, '_pyclaw_gate', None)
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
        return [t['name'] for t in self._team.tool_schemas()]

    @property
    def context_messages(self) -> int:
        return len(self._team.transcript())

    def transcript(self) -> list:
        return self._team.transcript()

    @property
    def active_agents(self) -> int:
        return len(self._team.agents) - 1

    @property
    def total_usage(self) -> dict:
        return {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}

    def set_thinking(self, on: bool):
        self._thinking = bool(on)
        self._team.set_thinking(self._thinking)

    @property
    def permission_mode(self) -> str:
        return self._gate.mode.value if self._gate is not None else 'default'

    def set_permission_mode(self, mode: str) -> str:
        if self._gate is None:
            raise ValueError('Permission gate not available for this session.')
        parsed = parse_mode(mode)
        self._gate.mode = parsed
        return parsed.value

    def attach_approval(self, coro):
        if self._gate is not None:
            self._gate.request = coro

    def submit(self, text: str, *, cancelable_tools: tuple = ()):
        self._team.lead.interrupt_and_submit(text, cancelable_tools=cancelable_tools)

    def reset(self):
        self._team.lead.messages = []
        self._team.reset_usage()

    @property
    def usage(self):
        return self._team.usage()

    async def switch(self, mode: str):
        if mode not in ('agent', 'team'):
            raise ValueError(f"Unknown mode: {mode}")
        names = [t.name for t in self._tools]
        inst = (agent_instruction(names) if mode == 'agent'
                else team_instruction(names))
        self._team.set_lead_instruction(inst)
        self.mode = mode

    def schedule_delivery(self, text: str, when: str):
        if self.deliver is None:
            return 'Delivery is not available for this session.'
        from .task import schedule_delivery
        job = schedule_delivery(text, when, self.deliver)
        at = job['next'].strftime('%Y-%m-%d %H:%M:%S') if job.get('next') else 'later'
        return f"Scheduled delivery {job['id']} at {at}: {text}"

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
            return await self._run(message, on_event)
        return await self._team.query(message)

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

    async def close(self):
        self._unbind()
        _sessions_by_root.pop(self.name, None)
        close_session_logger(self.conv_session_id)
