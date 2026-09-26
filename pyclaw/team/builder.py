from __future__ import annotations

import asyncio
import itertools
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

from chatchat.team.team import Team
from chatchat.team.worktrees import repository_root, sweep_worktrees
from chatchat.tasks.cron_schedule import CronStore
from chatchat.runtime.thinking import Thinking
from chatchat.tool import ToolContext

from pyclaw import task_registry
from pyclaw import config
from pyclaw.team import defs as agent_defs
from pyclaw.team import memory as agent_memory
from pyclaw.home import pyclaw_home
from pyclaw.tools import background
from pyclaw.plugins import discover_tools
from pyclaw.skills import discover_registry
from pyclaw.skills.builtin import register_builtin_skills
from pyclaw.tools import BUILTIN_TOOLS
from pyclaw.permissions import PermissionController, PermissionMode, parse_mode
from chatchat.knowledge.agent_memory import AgentMemory
from chatchat.knowledge.skills import SkillRegistry

_name_counter = itertools.count()

logger = logging.getLogger(__name__)


def configured_context_window() -> int:
    return int(config.load().get('contextWindow') or 0)


def checkpoints_enabled() -> bool:
    return bool(config.load().get('checkpoints', True))

def _resolve_tools(tools):
    if tools is None:
        return list(BUILTIN_TOOLS) + discover_tools()
    return tools


def _resolve_skills(skills, cwd):
    if skills is None:
        return discover_registry(cwd)
    return SkillRegistry.load([(Path(root), 'plugin') for root in skills])


def team_instruction(tool_names: list) -> str:
    names = ', '.join(tool_names)
    return f'''You are PyClaw, the leader of a task-executing team.

You have no direct tools. Your sub-agents are equipped with tools: {names}.

Whenever the user's request needs any tool, or benefits from parallel work, create a sub-agent with the `Agent` tool and delegate the task, then relay its result and answer the user.

Break work that has more than one step into tasks with `TaskCreate` and keep
`TaskUpdate` current as each one starts and finishes; teammates pick up tasks
that are free.

For simple requests that only need text, reply directly. Be helpful, accurate and concise.'''


def agent_instruction(tool_names: list) -> str:
    names = ', '.join(tool_names)
    return f'''You are PyClaw, a capable AI assistant with tools: {names}.

Use tools to complete the user's requests, then answer with the results. When a
request needs more than one step, track it with `TaskCreate` and `TaskUpdate`
so the progress is visible. Be helpful, accurate and concise.'''


PLAN_NOTE = '''

You are in PLAN MODE (read-only). Investigate the workspace, explore and
propose a plan, but do NOT edit files or perform state-changing operations.
Read/Glob/Grep and read-only shell commands are available; Write, Edit and
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
    setting = config.load().get('thinking') or {}
    return Thinking(mode=str(setting.get('mode') or 'on'),
                    budget=int(setting.get('budget') or 0),
                    effort=str(setting.get('effort') or ''))


def _agent_memory(cwd: str):
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
    conversation_id: Optional[str] = None,
) -> Team:
    conversation_id = conversation_id or uuid.uuid4().hex
    cwd = cwd or os.getcwd()
    registry = _resolve_skills(skills, cwd)
    coding_tools = list(BUILTIN_TOOLS)
    coding_names = {t.name for t in coding_tools}
    candidates = coding_tools + [t for t in _resolve_tools(tools)
                                 if t.name not in coding_names]
    refused = list(disallowed_tools or [])
    if base_tools is not None:
        refused += [t.name for t in candidates if t.name not in base_tools]
    gate = PermissionController(mode=permission_mode, cwd=cwd,
                                allow=allowed_tools or (),
                                ask=ask or (), deny=refused,
                                tools=candidates,
                                extra_dirs=config.load().get(
                                    'additionalDirectories') or ())
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
        tool_context=ToolContext(cwd=Path(cwd).resolve(),
                                 extra_dirs=tuple(gate.extra_dirs)),
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
        rules=agent_memory.rule_set(cwd),
        file_history_dir=(str(pyclaw_home() / 'file-history' / conversation_id)
                          if checkpoints_enabled() else None),
        multi_agent=use_team,
        context_window=configured_context_window(),
    )
    gate.agent_memory = team.agent_memory
    team._pyclaw_gate = gate

    def _roots_of(cwd):
        """What the session reads about the directory it works in: the
        instruction files, the project memory and the rules beside them."""
        team.set_instruction_files(agent_memory.load_instruction_files(cwd))
        memory = agent_memory.load_project_memory(cwd)
        team.set_lead_instruction(inst + (f'\n\n{memory}' if memory else ''))
        team.rules = agent_memory.rule_set(cwd)

    def _follow_worktree(cwd):
        gate.move_to(cwd)
        os.chdir(cwd)
        _roots_of(cwd)

    team._cwd_changed = _follow_worktree
    team._skills_granted = gate.grant_rules

    def _plan_mode(mode):
        gate.mode = parse_mode(mode)
        team.hooks.permission_mode = gate.mode.value

    team._plan_mode_changed = _plan_mode
    team.lead_session_id = conversation_id
    team._pyclaw_mode = 'team' if use_team else 'agent'

    _roots_of(cwd)
    register_builtin_skills(team.skills, team)

    # Only a worktree directory that someone left behind makes this cost
    # anything: the check before it is one stat.
    stale = sweep_worktrees(str(repository_root(cwd) or cwd))
    if stale:
        logger.info('swept %d stale worktree(s): %s', len(stale),
                    ', '.join(path.name for path in stale))

    for defn in agent_defs.builtin_agent_defs(all_tools=resolved):
        team.register_agent_definition(defn)
    cli_defs = (agent_defs.parse_agents_json(agents_json, resolved)
                if agents_json else [])
    definitions = agent_defs.load_agent_defs(cwd, all_tools=resolved,
                                             cli=cli_defs)
    for defn in definitions:
        team.register_agent_definition(defn)
    team.cli_agent_defs = cli_defs
    team._pyclaw_snapshot_updates = [
        defn.agent_type for defn in definitions
        if defn.memory
        and team.agent_memory.sync_snapshot(defn.agent_type, defn.memory)
        != 'none']


    def _notify_task_finished(task_id, command, code, killed):
        record = background.task(task_id)
        if record is None:
            return
        team.lead.enqueue_attachment(task_registry.notification(
            record, record.output_delta()))

    background.set_notifier(_notify_task_finished)

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
