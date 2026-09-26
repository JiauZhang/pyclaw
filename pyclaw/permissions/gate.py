from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from pyclaw.permissions.bash_rules import (is_dangerous_removal,
                                          is_dangerous_rule,
                                          is_read_only,
                                          is_workspace_edit_command,
                                          suggested_rules)
from pyclaw.permissions.modes import PermissionMode, parse_mode
from pyclaw.permissions.rules import (BASH, _command_of,
                                     _local_settings_file,
                                     _path_rule,
                                     _read_rule_file, _rule_matches,
                                     _user_settings_file, matched_rule)
from conippets import json

from pyclaw.tools.paths import resolve
from chatchat.runtime.structured import STRUCTURED_OUTPUT_TOOL
from pyclaw.tools.names import (AGENT, ASK_USER_QUESTION, CRON_CREATE,
                               CRON_DELETE, CRON_LIST, ENTER_PLAN_MODE,
                               EXIT_PLAN_MODE, SEND_MESSAGE, SKILL,
                               TASK_STOP, TEAM_CREATE, TEAM_DELETE)

logger = logging.getLogger(__name__)

AUTO_TOOLS = frozenset({AGENT, SEND_MESSAGE, TASK_STOP,
                        SKILL, TEAM_CREATE, TEAM_DELETE,
                        STRUCTURED_OUTPUT_TOOL, ASK_USER_QUESTION,
                        ENTER_PLAN_MODE, EXIT_PLAN_MODE,
                        CRON_CREATE, CRON_LIST, CRON_DELETE})

REJECT_MESSAGE = (
    "The user refused this tool call, so nothing ran; a refused edit left the "
    "file exactly as it was. Stop here and wait for them to say what next.")
REJECT_MESSAGE_WITH_REASON_PREFIX = (
    "The user refused this tool call, so nothing ran; a refused edit left the "
    "file exactly as it was. What they said instead:\n")
SUBAGENT_REJECT_MESSAGE = (
    "This tool call was refused, so nothing ran; a refused edit left the file "
    "exactly as it was. Route around it: try another way, or report the "
    "blocker in your result and finish the task.")
SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX = (
    "This tool call was refused, so nothing ran; a refused edit left the file "
    "exactly as it was. What they added:\n")


def reject_message(feedback: str = '', teammate: bool = False) -> str:
    if teammate:
        return (SUBAGENT_REJECT_MESSAGE_WITH_REASON_PREFIX + feedback
                if feedback else SUBAGENT_REJECT_MESSAGE)
    return (REJECT_MESSAGE_WITH_REASON_PREFIX + feedback
            if feedback else REJECT_MESSAGE)


def _pre_tool_decision(decision: str, reason: str = '',
                       context: str = '', updated_input: dict | None = None) -> dict:
    specific = {'hookEventName': 'PreToolUse', 'permissionDecision': decision}
    if reason:
        specific['permissionDecisionReason'] = reason
    if context:
        specific['additionalContext'] = context
    if updated_input:
        specific['updatedInput'] = updated_input
    return {'hookSpecificOutput': specific}


@dataclass
class PermissionChoice:
    value: str
    rule: str | None = None
    feedback: str = ''
class PermissionController:

    def __init__(self, *, mode: str = 'default', cwd, allow=(),
                 ask=(), deny=(), request=None, tools=None):
        self.mode: PermissionMode = parse_mode(mode)
        self.cwd = Path(cwd).resolve()
        self.plan_file = None
        self._by_name = {t.name: t for t in (tools or ())}
        self._layers: list[tuple[str, str, str]] = []
        self._layer_files = {
            'user': _user_settings_file(),
            'project': Path(self.cwd) / '.pyclaw' / 'settings.json',
            'local': _local_settings_file(self.cwd),
        }
        merged = {b: [] for b in ('allow', 'ask', 'deny')}
        layers = (('user', self._layer_files['user']),
                  ('project', self._layer_files['project']),
                  ('local', self._layer_files['local']),
                  ('cli', {'allow': list(allow), 'ask': list(ask),
                           'deny': list(deny)}))
        for source, rules in layers:
            if source != 'cli':
                rules = _read_rule_file(rules)
            for behavior in ('allow', 'ask', 'deny'):
                for rule in rules.get(behavior, []):
                    bucket = merged[behavior]
                    if rule not in bucket:
                        bucket.append(rule)
                        self._layers.append((behavior, rule, source))
        self._allow = merged['allow']
        self._ask = merged['ask']
        self._deny = merged['deny']
        self.bypass_available = self.mode is PermissionMode.bypass_permissions
        self.request = request
        self.permission_hooks = None
        self.config_changed = None
        self.agent_memory = None

    def move_to(self, cwd):
        self.cwd = Path(cwd).resolve()
        self._layer_files['project'] = (
            self.cwd / '.pyclaw' / 'settings.json')
        self._layer_files['local'] = _local_settings_file(self.cwd)

    def allowed_tool(self, name: str) -> bool:
        return not _rule_matches(self._deny, name)

    def rule_listing(self) -> list[tuple[str, str, str]]:
        return list(self._layers)

    def dangerous_rules(self) -> list[str]:
        """Allow rules that would let the model run anything it wants, so the
        listing can say so instead of leaving them looking like any other."""
        return [rule for behavior, rule, _source in self._layers
                if behavior == 'allow' and is_dangerous_rule(rule)]

    def remove_rule(self, rule: str) -> bool:
        entry = next((e for e in self._layers
                      if e[1] == rule and e[2] in self._layer_files), None)
        if entry is None:
            return False
        behavior, _rule, source = entry
        path = self._layer_files[source]
        try:
            data = json.read(path) if path.exists() else {}
            perms = data.get('permissions') or {}
            perms[behavior] = [str(r) for r in perms.get(behavior, [])
                               if str(r) != rule]
            data['permissions'] = perms
            json.write(path, data)
        except Exception:
            return False
        self._layers.remove(entry)
        bucket = getattr(self, f'_{behavior}')
        if rule in bucket:
            bucket.remove(rule)
        return True

    def _save_local_rule(self, rule: str):
        path = _local_settings_file(self.cwd)
        try:
            data = json.read(path) if path.exists() else {}
            perms = data.get('permissions') or {}
            rules = [str(r) for r in perms.get('allow', [])]
            if rule not in rules:
                rules.append(rule)
            perms['allow'] = rules
            data['permissions'] = perms
            path.parent.mkdir(parents=True, exist_ok=True)
            json.write(path, data)
            gitignore = self.cwd / '.gitignore'
            if gitignore.exists():
                text = gitignore.read_text(encoding='utf-8')
                if '.pyclaw/' not in text:
                    gitignore.write_text(
                        text.rstrip('\n') + '\n.pyclaw/\n', encoding='utf-8')
        except OSError:
            pass

    def path_of(self, tool_name: str, tool_input) -> str | None:
        tool = self._by_name.get(tool_name)
        if tool is None or tool.get_path is None \
                or not isinstance(tool_input, dict):
            return None
        value = tool.get_path(tool_input)
        return str(value) if value else None

    def suggested_rules(self, tool_name: str, tool_input) -> list[str]:
        if tool_name == BASH:
            return suggested_rules(_command_of(tool_input))
        rule = _path_rule(tool_name, self.path_of(tool_name, tool_input),
                          self.cwd)
        return [rule] if rule else []

    def suggested_rule(self, tool_name: str, tool_input) -> str | None:
        rules = self.suggested_rules(tool_name, tool_input)
        return rules[0] if rules else None

    def remember_allow(self, tool_name: str, tool_input=None, rule=None):
        rules = [rule] if rule else self.suggested_rules(tool_name, tool_input)
        if tool_name != BASH and not rules:
            rules = [tool_name]
        stored = []
        for one in rules:
            if one not in self._allow:
                self._allow.append(one)
                self._layers.append(('allow', one, 'session'))
            self._save_local_rule(one)
            stored.append(one)
        return stored

    def _in_workspace(self, tool_name: str, input) -> bool:
        target = self.path_of(tool_name, input)
        return target is None or resolve(self.cwd, target) is not None

    def _rememberable(self, tool_name: str, tool_input) -> bool:
        if tool_name == BASH:
            return bool(suggested_rules(_command_of(tool_input)))
        return True

    def _effective_mode(self, mode) -> PermissionMode:
        if mode is None:
            return self.mode
        if isinstance(mode, PermissionMode):
            return mode
        return parse_mode(mode)

    def _decide_bash(self, tool_input, mode: PermissionMode) -> str:
        command = _command_of(tool_input)
        if is_dangerous_removal(command):
            return 'ask'
        if _rule_matches(self._allow, BASH, tool_input, every_part=True):
            return 'allow'
        if mode is PermissionMode.accept_edits \
                and is_workspace_edit_command(command):
            return 'allow'
        if is_read_only(command):
            return 'allow'
        return 'ask'

    def decide(self, tool_name: str, tool_input, mode=None) -> str:
        mode = self._effective_mode(mode)
        target = self.path_of(tool_name, tool_input)
        if _rule_matches(self._deny, tool_name, tool_input, self.cwd, target):
            return 'deny'
        if _rule_matches(self._ask, tool_name, tool_input, self.cwd, target):
            return 'ask'
        if mode is PermissionMode.bypass_permissions:
            return 'allow'
        if tool_name in AUTO_TOOLS:
            return 'allow'
        if (self.agent_memory is not None and target is not None
                and not tool_name == BASH
                and self.agent_memory.contains(Path(target))):
            return 'allow'
        if tool_name == BASH:
            return self._decide_bash(tool_input, mode)
        if _rule_matches(self._allow, tool_name, tool_input, cwd=self.cwd,
                         target=target):
            return 'allow'
        tool = self._by_name.get(tool_name)
        if tool is None:
            return 'ask'
        inside = self._in_workspace(tool_name, tool_input)
        if tool.read_only:
            if inside:
                return 'allow'
            return 'allow' if mode is PermissionMode.plan else 'ask'
        if target is None:
            return 'ask'
        if mode is PermissionMode.plan:
            if self.plan_file is not None \
                    and Path(target) == Path(self.plan_file).resolve():
                return 'allow'
            return 'deny'
        if inside and mode is PermissionMode.accept_edits:
            return 'allow'
        return 'ask'

    async def _hook_decision(self, tool_name: str, tool_input,
                             tool_use_id: str, agent: str):
        if self.permission_hooks is None:
            return None
        decision = await self.permission_hooks(tool_name, tool_input,
                                               tool_use_id, agent)
        if decision is None:
            return None
        if decision['behavior'] == 'allow':
            return _pre_tool_decision('allow',
                                      updated_input=decision['updated_input'])
        return _pre_tool_decision(
            'deny',
            reason=decision['message'] or 'Permission denied by hook')

    async def _ask_with_hooks(self, tool_name: str, tool_input,
                              tool_use_id: str, agent: str):
        prompt = asyncio.create_task(
            self.request(tool_name, tool_input, tool_use_id=tool_use_id,
                         agent=agent))
        if self.permission_hooks is None:
            return await prompt
        hooks = asyncio.create_task(self._hook_decision(tool_name, tool_input,
                                                        tool_use_id, agent))
        pending = {prompt, hooks}
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED)
                if prompt in done:
                    return prompt.result()
                decision = hooks.result()
                if decision is not None:
                    return decision
        finally:
            for task in (prompt, hooks):
                if not task.done():
                    task.cancel()

    async def authorize(self, tool_name: str, tool_input,
                        mode=None, tool_use_id: str = '',
                        agent: str = '') -> bool | dict:
        mode = self._effective_mode(mode)
        decision = self.decide(tool_name, tool_input, mode)
        logger.info('permission %s %s mode=%s rule=%s', tool_name, decision,
                    mode.value,
                    matched_rule(self._deny if decision == 'deny' else
                                 self._ask if decision == 'ask' else
                                 self._allow,
                                 tool_name, tool_input, self.cwd,
                                 self.path_of(tool_name, tool_input),
                                 every_part=decision == 'allow') or '-')
        if decision == 'allow':
            return True
        if decision == 'deny':
            return _pre_tool_decision(
                'deny', reason=f'{tool_name} is not allowed in {mode.value} '
                               f'mode / by your permission rules.')
        if self.request is None:
            decided = await self._hook_decision(tool_name, tool_input,
                                                tool_use_id, agent)
            if decided is not None:
                return decided
            return _pre_tool_decision(
                'deny', reason=f'{tool_name} needs approval but no app is '
                               f'present to ask.')
        answer = await self._ask_with_hooks(tool_name, tool_input, tool_use_id,
                                            agent)
        if isinstance(answer, dict):
            return answer
        choice = answer
        if choice.value == 'dont_ask':
            stored = None
            if choice.rule:
                stored = self.remember_allow(tool_name, tool_input,
                                             rule=choice.rule)
            elif self._rememberable(tool_name, tool_input):
                stored = self.remember_allow(tool_name, tool_input)
            if stored and self.config_changed is not None:
                await self.config_changed('permissions')
        if choice.value in ('approved', 'dont_ask'):
            if not choice.feedback:
                return True
            return _pre_tool_decision('allow', context=choice.feedback)
        return _pre_tool_decision(
            'deny', reason=reject_message(choice.feedback, bool(agent)))
