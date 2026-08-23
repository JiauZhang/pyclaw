import asyncio
import inspect
import itertools
import logging
from typing import AsyncIterator, Callable, Optional

from chatchat.agent import Agent, AgentConfig, create_agent
from chatchat.runtime import get_runtime
from chatchat.team import Team, TeamConfig, create_team
from chatchat.types import Usage

from .plugins import discover_skills, discover_tools
from .skills import skill_roots
from .tools import tools as base_tools

_name_counter = itertools.count()
_sessions_by_root: dict[str, 'Session'] = {}


def session_of(actor) -> Optional['Session']:
    session = _sessions_by_root.get(getattr(actor, 'id', None))
    if session is not None:
        return session
    return _sessions_by_root.get(getattr(actor, '_parent', None))


def _resolve_tools(tools):
    if tools is None:
        return list(base_tools) + discover_tools()
    return tools


def _resolve_skills(skills):
    if skills is None:
        return list(skill_roots) + discover_skills()
    return skills


LIFECYCLE_PATTERNS = (
    'lifecycle:team:start', 'lifecycle:team:step', 'lifecycle:team:end', 'lifecycle:team:error',
    'lifecycle:agent:start', 'lifecycle:agent:step', 'lifecycle:agent:end', 'lifecycle:agent:error',
    'lifecycle:tool:start', 'lifecycle:tool:step', 'lifecycle:tool:end', 'lifecycle:tool:error',
)


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


IM_EXTRA = '''You are PyClaw, an AI assistant on an instant messaging platform (QQ/WeChat).

Rules:
1. Keep responses very short and concise. One to three sentences is usually enough.
2. Only give detailed explanations or long output when the user explicitly asks for it.
3. Do not use markdown formatting — plain text only.
4. Be conversational and direct.
5. If you use tools, briefly summarize the result without technical details.'''


def _delta_text(chunk) -> str:
    try:
        return chunk.choices[0].delta.content or ''
    except (AttributeError, IndexError, TypeError):
        return ''


_conv_logger = logging.getLogger("pyclaw.conversation")


def _summarize(value, limit: int = 300) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _delta_thinking(chunk) -> str:
    try:
        return chunk.choices[0].delta.reasoning_content or ''
    except (AttributeError, IndexError, TypeError):
        return ''


def _record_event(session_id: str, ev, logger=_conv_logger):
    topic = getattr(ev, "topic", "")
    data = getattr(ev, "data", None) or {}
    role = "system"
    detail = ""

    if "tool" in topic:
        role = "tool"
        name = data.get("name", "")
        if topic.endswith(":start"):
            detail = f"{name} | input={_summarize(data.get('input') or data.get('arguments'))}"
        elif topic.endswith(":end"):
            detail = f"{name} | output={_summarize(data.get('output') or data.get('result'))}"
        elif topic.endswith(":error"):
            detail = f"{name} | error={data.get('error')}"
    elif "agent" in topic or "team" in topic:
        role = "agent"
        name = data.get("name", "")
        if topic.endswith(":start"):
            detail = f"{name} started"
        elif topic.endswith(":end"):
            detail = f"{name} finished"
        elif topic.endswith(":error"):
            detail = f"{name} error: {data.get('error')}"

    if not detail:
        return
    logger.info(
        "lifecycle",
        extra={"conv_session": session_id, "conv_role": role,
               "conv_topic": topic, "conv_detail": detail},
    )


def _dispatch_event(on_event, ev):
    if inspect.iscoroutinefunction(on_event):
        asyncio.create_task(on_event(ev))
    else:
        on_event(ev)


def build_agent(
    provider: str,
    model: str,
    instruction: Optional[str] = None,
    tools: Optional[list] = None,
    skills: Optional[list] = None,
    thinking: bool = False,
    http_options: Optional[dict] = None,
    max_steps: int = 10,
) -> Agent:
    tools = _resolve_tools(tools)
    skills = _resolve_skills(skills)
    return create_agent(AgentConfig(
        name=f'pyclaw-{next(_name_counter)}',
        provider=provider,
        model=model,
        instruction=instruction or agent_instruction([t.name for t in tools]),
        thinking=thinking,
        tools=tools,
        skills=skills,
        http_options=http_options or {},
        max_steps=max_steps,
    ))


def build_team(
    provider: str,
    model: str,
    instruction: Optional[str] = None,
    tools: Optional[list] = None,
    skills: Optional[list] = None,
    thinking: bool = False,
    http_options: Optional[dict] = None,
    max_depth: int = 5,
    max_steps: int = 10,
) -> Team:
    tools = _resolve_tools(tools)
    skills = _resolve_skills(skills)
    return create_team(TeamConfig(
        name=f'pyclaw-{next(_name_counter)}',
        provider=provider,
        model=model,
        instruction=instruction or team_instruction([t.name for t in tools]),
        thinking=thinking,
        leader_tools=[],
        agent_tools=tools,
        skills=skills,
        http_options=http_options or {},
        max_depth=max_depth,
        max_steps=max_steps,
    ))


class Session:
    def __init__(self, entity):
        self.entity = entity
        self.name = entity.name
        self.mode = entity.kind
        self.deliver = None
        self.conv_session_id = entity.id
        self._conv_thinking = ""
        self._conv_reply = ""
        self._runtime = get_runtime()
        self._hooks: list[tuple[str, Callable]] = []
        _sessions_by_root[entity.id] = self

    @property
    def provider(self) -> str:
        return self.entity.config.provider

    @property
    def model(self) -> str:
        return self.entity.config.model

    @property
    def available_tools(self) -> list[str]:
        cfg = self.entity.config
        tools = getattr(cfg, 'agent_tools', None) or getattr(cfg, 'tools', []) or []
        return [t.name for t in tools]

    @property
    def running(self) -> bool:
        return self.entity.is_running

    @property
    def thinking(self) -> bool:
        return bool(self.entity._loop.thinking)

    def set_thinking(self, on: bool):
        self.entity._loop.thinking = on
        self.entity.config.thinking = on

    def reset(self):
        self.entity.clear()
        self.entity._usage = Usage()

    async def switch(self, mode: str):
        self._swap_entity(build_agent if mode == 'agent' else build_team)

    def _swap_entity(self, builder):
        cfg = self.entity.config
        old_id = self.entity.id
        tools = getattr(cfg, 'agent_tools', None) or getattr(cfg, 'tools', None)
        entity = builder(
            provider=cfg.provider,
            model=cfg.model,
            tools=tools,
            skills=getattr(cfg, 'skills', None),
            thinking=self.thinking,
            http_options=getattr(cfg, 'http_options', {}),
        )
        entity._parent = self.entity._parent
        _sessions_by_root[entity.id] = self
        _sessions_by_root.pop(old_id, None)
        self.entity = entity
        self.name = entity.name
        self.mode = entity.kind
        self._runtime.unregister_entity(old_id)

    def schedule_delivery(self, text: str, when: str):
        if self.deliver is None:
            return 'Delivery is not available for this session.'
        from .task import schedule_delivery
        job = schedule_delivery(text, when, self.deliver)
        at = job['next'].strftime('%Y-%m-%d %H:%M:%S') if job.get('next') else 'later'
        return f"Scheduled delivery {job['id']} at {at}: {text}"

    def _entity_names(self) -> set:
        names = {self.entity.name}
        stack = list(self.entity.sub_agents.values())
        while stack:
            actor = stack.pop()
            names.add(actor.name)
            stack.extend(actor.sub_agents.values())
        return names

    def _scoped(self, ev) -> bool:
        return ev.source in self._entity_names()

    def _bind(self, pattern: str, handler: Callable):
        self._runtime.subscribe(pattern, handler)
        self._hooks.append((pattern, handler))

    def _unbind(self):
        self._flush_conv()
        for pattern, handler in self._hooks:
            self._runtime.unsubscribe(pattern, handler)
        self._hooks.clear()

    def _accumulate(self, ev):
        thinking = _delta_thinking(getattr(ev, "data", None))
        content = _delta_text(getattr(ev, "data", None))
        if thinking:
            self._conv_thinking += thinking
        if content:
            self._conv_reply += content

    def _flush_conv(self):
        session_id = self.conv_session_id
        if self._conv_thinking:
            _conv_logger.info(
                "thinking",
                extra={"conv_session": session_id, "conv_role": "thinking",
                       "conv_topic": "THINKING", "conv_detail": self._conv_thinking},
            )
            self._conv_thinking = ""
        if self._conv_reply:
            _conv_logger.info(
                "reply",
                extra={"conv_session": session_id, "conv_role": "assistant",
                       "conv_topic": "REPLY", "conv_detail": self._conv_reply},
            )
            self._conv_reply = ""

    def _bind_lifecycle(self, on_event: Optional[Callable]):
        def record(ev):
            if not self._scoped(ev):
                return
            if ev.topic == "lifecycle:client:step":
                self._accumulate(ev)
            else:
                _record_event(self.conv_session_id, ev)

        for pattern in LIFECYCLE_PATTERNS + ("lifecycle:client:step",):
            self._bind(pattern, record)

        if on_event:

            def dispatch(ev):
                if not self._scoped(ev):
                    return
                _dispatch_event(on_event, ev)

            for pattern in LIFECYCLE_PATTERNS:
                self._bind(pattern, dispatch)

    async def chat(self, message: str, on_event: Optional[Callable] = None) -> str:
        self._bind_lifecycle(on_event)
        try:
            return await self.entity.chat(message)
        finally:
            self._unbind()

    def stream(self, message: str, on_event: Optional[Callable] = None) -> AsyncIterator[str]:
        async def gen():
            queue: asyncio.Queue = asyncio.Queue()

            def on_chunk(ev):
                if ev.source != self.entity.name:
                    return
                text = _delta_text(ev.data)
                if text:
                    queue.put_nowait(text)

            self._bind('lifecycle:client:step', on_chunk)
            self._bind_lifecycle(on_event)
            task = asyncio.create_task(self.entity.chat(message))
            try:
                while not task.done():
                    try:
                        yield await asyncio.wait_for(queue.get(), timeout=0.1)
                    except asyncio.TimeoutError:
                        continue
                while not queue.empty():
                    yield queue.get_nowait()
            finally:
                self._unbind()
            await task

        return gen()

    async def close(self):
        await self.entity.stop()
        self._unbind()
        self._runtime.unregister_entity(self.entity.id)
        _sessions_by_root.pop(self.entity.id, None)