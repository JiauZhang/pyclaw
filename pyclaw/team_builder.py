from __future__ import annotations

import asyncio
import itertools
import os
from pathlib import Path
from typing import Optional

from chatchat.team import Team
from chatchat.core.cron_schedule import CronStore
from chatchat.core.thinking import Thinking
from chatchat.tool import ToolContext

from . import pyclaw_home
from .plugins import discover_tools
from .skills import discover_registry
from .tools import tools as base_tools
from .tools.coding import CODING_TOOLS, PermissionController, PermissionMode

_name_counter = itertools.count()


def configured_context_window() -> int:
    from .config import load
    return int(load().get('contextWindow') or 0)


def checkpoints_enabled() -> bool:
    from .config import load
    return bool(load().get('checkpoints', True))

def _resolve_tools(tools):
    if tools is None:
        return list(base_tools) + discover_tools()
    return tools


def _resolve_skills(skills, cwd):
    if skills is None:
        return discover_registry(cwd)
    from chatchat.core.skills import SkillRegistry
    return SkillRegistry.load([(Path(root), 'plugin') for root in skills])


def team_instruction(tool_names: list) -> str:
    names = ', '.join(tool_names)
    return f'''You are PyClaw, the leader of a task-executing team.

You have no direct tools. Your sub-agents are equipped with tools: {names}.

Whenever the user's request needs any tool, or benefits from parallel work, create a sub-agent with the `create_agent` tool and delegate the task, then relay its result and answer the user.

Break work that has more than one step into tasks with `task_create` and keep
`task_update` current as each one starts and finishes; teammates pick up tasks
that are free.

For simple requests that only need text, reply directly. Be helpful, accurate and concise.'''


def agent_instruction(tool_names: list) -> str:
    names = ', '.join(tool_names)
    return f'''You are PyClaw, a capable AI assistant with tools: {names}.

Use tools to complete the user's requests, then answer with the results. When a
request needs more than one step, track it with `task_create` and `task_update`
so the progress is visible. Be helpful, accurate and concise.'''


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

def _dispatch_event(on_event, ev):
    if asyncio.iscoroutinefunction(on_event):
        asyncio.create_task(on_event(ev))
    else:
        on_event(ev)


def thinking_from_config() -> Thinking:
    from . import config
    setting = config.load().get('thinking') or {}
    return Thinking(mode=str(setting.get('mode') or 'on'),
                    budget=int(setting.get('budget') or 0),
                    effort=str(setting.get('effort') or ''))


def _agent_memory(cwd: str):
    from chatchat.core.agent_memory import AgentMemory
    workspace = Path(cwd) / '.pyclaw'
    return AgentMemory(
        roots={'user': pyclaw_home() / 'agent-memory',
               'project': workspace / 'agent-memory',
               'local': workspace / 'agent-memory-local'},
        snapshots=workspace / 'agent-memory-snapshots')
def build_team(
    provider: str,
    model: str,
    instruction: Optional[str] = None,
    tools: Optional[list] = None,
    skills: Optional[list] = None,
    thinking: Optional[Thinking] = None,
    http_options: Optional[dict] = None,
    cwd: Optional[str] = None,
    permission_mode: str = 'default',
    allowed_tools: Optional[list] = None,
    ask: Optional[list] = None,
    disallowed_tools: Optional[list] = None,
    base_tools: Optional[list] = None,
    agents_json: Optional[str] = None,
    use_team: bool = False,
) -> Team:
    cwd = cwd or os.getcwd()
    from .agent_memory import (load_instruction_files, load_project_memory,
                               rule_set)
    registry = _resolve_skills(skills, cwd)
    coding_tools = list(CODING_TOOLS)
    coding_names = {t.name for t in coding_tools}
    candidates = coding_tools + [t for t in _resolve_tools(tools)
                                 if t.name not in coding_names]
    refused = list(disallowed_tools or [])
    if base_tools is not None:
        refused += [t.name for t in candidates if t.name not in base_tools]
    gate = PermissionController(mode=permission_mode, cwd=cwd,
                                allow=allowed_tools or (),
                                ask=ask or (), deny=refused,
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
        thinking=thinking or thinking_from_config(),
        model_timeout=model_timeout,
        http_options=http_options or {},
        mailbox_dir=os.path.join(cwd, '.pyclaw', 'teams'),
        tasks_dir=str(pyclaw_home() / 'tasks'),
        skills=registry,
        team_store=str(pyclaw_home() / 'teams'),
        agent_memory=_agent_memory(cwd),
        cron=CronStore(Path(cwd) / '.pyclaw'),
        rules=rule_set(cwd),
        file_history_dir=(str(pyclaw_home() / 'file-history')
                          if checkpoints_enabled() else None),
        multi_agent=use_team,
        context_window=configured_context_window(),
    )
    gate.agent_memory = team.agent_memory
    team._pyclaw_gate = gate

    def _follow_worktree(cwd):
        gate.move_to(cwd)

    team._cwd_changed = _follow_worktree
    team._pyclaw_mode = 'team' if use_team else 'agent'

    team.set_instruction_files(load_instruction_files(cwd))
    memory = load_project_memory(cwd)
    if memory:
        team.set_lead_instruction(team.lead.instruction + '\n\n' + memory)

    from .agent_defs import (builtin_agent_defs, load_agent_defs,
                          parse_agents_json)
    for defn in builtin_agent_defs(all_tools=resolved):
        team.register_agent_definition(defn)
    cli_defs = parse_agents_json(agents_json, resolved) if agents_json else []
    definitions = load_agent_defs(cwd, all_tools=resolved, cli=cli_defs)
    for defn in definitions:
        team.register_agent_definition(defn)
    team.cli_agent_defs = cli_defs
    team._pyclaw_snapshot_updates = [
        defn.agent_type for defn in definitions
        if defn.memory
        and team.agent_memory.sync_snapshot(defn.agent_type, defn.memory)
        != 'none']

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
        who = (team.get_by_name(asker) if asker else None) or team.lead
        tool_name = hook_input.get('tool_name', '')
        tool_input = hook_input.get('tool_input') or {}
        outcome = await gate.authorize(
            tool_name, tool_input, mode=mode,
            tool_use_id=str(hook_input.get('tool_use_id') or ''),
            agent='' if asker == team.lead.name else asker)
        specific = (outcome.get('hookSpecificOutput') or {}
                    if isinstance(outcome, dict) else {})
        if specific.get('permissionDecision') == 'deny':
            await team.hooks.execute_permission_denied_hooks(who, tool_name,
                                                             tool_input)
        return outcome

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

    async def _config_changed(source: str):
        await team.hooks.execute_config_change_hooks(team.lead, source)

    gate.permission_hooks = _permission_request
    gate.config_changed = _config_changed
    return team
