from __future__ import annotations

import asyncio
import fnmatch
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from conippets import json

from .paths import resolve
from .shell_rules import (bash_rule_matches, is_dangerous_removal,
                          is_read_only, is_workspace_edit_command,
                          suggested_rule)

BASH_TOOL = 'Bash'

AUTO_TOOLS = frozenset({'create_agent', 'send_message', 'task_stop'})

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

_MODE_NAMES = {
    'default': 'default',
    'acceptEdits': 'acceptEdits',
    'accept_edits': 'acceptEdits',
    'plan': 'plan',
    'bypassPermissions': 'bypassPermissions',
    'bypass_permissions': 'bypassPermissions',
}


class PermissionMode(str, Enum):
    default = 'default'
    accept_edits = 'acceptEdits'
    plan = 'plan'
    bypass_permissions = 'bypassPermissions'


def parse_mode(value) -> PermissionMode:
    key = str(value).strip()
    if key not in _MODE_NAMES:
        raise ValueError(f'Unknown permission mode: {value}. Use one of: '
                         f'default, acceptEdits, plan, bypassPermissions.')
    return PermissionMode(_MODE_NAMES[key])


def next_mode(value, bypass_available: bool = False) -> PermissionMode:
    cur = parse_mode(value)
    if cur is PermissionMode.default:
        return PermissionMode.accept_edits
    if cur is PermissionMode.accept_edits:
        return PermissionMode.plan
    if cur is PermissionMode.plan:
        return (PermissionMode.bypass_permissions if bypass_available
                else PermissionMode.default)
    return PermissionMode.default


def _command_of(tool_input) -> str:
    if isinstance(tool_input, dict):
        return str(tool_input.get('command') or '').strip()
    return ''


def _rule_matches(rules, tool_name: str, tool_input=None,
                  env_all: bool = False, cwd=None, target=None) -> bool:
    if not rules:
        return False
    command = _command_of(tool_input) if tool_name == BASH_TOOL else ''
    for rule in rules:
        name, _, arg = str(rule).partition('(')
        if name.strip() != tool_name:
            continue
        if not arg:
            return True
        if tool_name == BASH_TOOL:
            if command and bash_rule_matches(arg.rstrip(')'), command,
                                            env_all):
                return True
            continue
        if target and cwd is not None \
                and _matches_pattern(arg.rstrip(')'), target, cwd):
            return True
    return False


def _matches_pattern(pattern: str, target: str, cwd) -> bool:
    if pattern.startswith('./'):
        pattern = pattern[2:]
    if pattern.endswith('/**'):
        pattern = pattern[:-3]
    pattern = pattern.rstrip('/')
    if pattern in ('', '.'):
        return True
    target = str(target).strip()
    if target.startswith('./'):
        target = target[2:]
    try:
        absolute = Path(target)
        if absolute.is_absolute() and cwd is not None:
            target = os.path.relpath(absolute, cwd)
    except (OSError, ValueError):
        pass
    target = target.lstrip('/')
    if target == pattern or target.startswith(pattern + '/'):
        return True
    if '*' in pattern or '?' in pattern:
        return (fnmatch.fnmatch(target, pattern)
                or fnmatch.fnmatch(target, pattern + '/*'))
    return False


def _relative_target(target: str, cwd) -> str | None:
    if not target:
        return None
    raw = str(target).strip()
    if raw.startswith(('~', '/')):
        try:
            raw = os.path.relpath(os.path.expanduser(raw), str(cwd))
        except (OSError, ValueError):
            return None
    if raw.startswith('./'):
        raw = raw[2:]
    return raw or None


def _path_rule(tool_name: str, target, cwd) -> str | None:
    rel = _relative_target(target, cwd)
    if not rel or rel == '.':
        return None
    return f'{tool_name}(./{rel})'


def _user_settings_file() -> Path:
    from pyclaw import pyclaw_home
    return pyclaw_home() / 'settings.json'


def _local_settings_file(cwd) -> Path:
    return Path(cwd) / '.pyclaw' / 'settings.local.json'


def _read_rule_file(path) -> dict:
    try:
        if not Path(path).exists():
            return {}
        perms = (json.read(path) or {}).get('permissions') or {}
    except Exception:
        return {}
    return {k: [str(r) for r in perms.get(k, []) if str(r).strip()]
            for k in ('allow', 'ask', 'deny')}


class PermissionController:

    def __init__(self, *, mode: str = 'default', cwd, allow=(),
                 ask=(), deny=(), request=None, tools=None):
        self.mode: PermissionMode = parse_mode(mode)
        self.cwd = Path(cwd).resolve()
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

    def allowed_tool(self, name: str) -> bool:
        return not _rule_matches(self._deny, name)

    def rule_listing(self) -> list[tuple[str, str, str]]:
        return list(self._layers)

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

    def suggested_rule(self, tool_name: str, tool_input) -> str | None:
        if tool_name == BASH_TOOL:
            return suggested_rule(_command_of(tool_input))
        return _path_rule(tool_name, self.path_of(tool_name, tool_input),
                          self.cwd)

    def remember_allow(self, tool_name: str, tool_input=None, rule=None):
        if rule is None:
            rule = self.suggested_rule(tool_name, tool_input)
            if tool_name == BASH_TOOL:
                command = _command_of(tool_input)
                if not command:
                    return
                rule = rule or f'Bash({" ".join(command.split())})'
            rule = rule or tool_name
        if rule not in self._allow:
            self._allow.append(rule)
            self._layers.append(('allow', rule, 'session'))
        self._save_local_rule(rule)

    def _in_workspace(self, tool_name: str, input) -> bool:
        target = self.path_of(tool_name, input)
        return target is None or resolve(self.cwd, target) is not None

    def _rememberable(self, tool_name: str, tool_input) -> bool:
        if tool_name == BASH_TOOL:
            return suggested_rule(_command_of(tool_input)) is not None
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
        if _rule_matches(self._allow, BASH_TOOL, tool_input):
            return 'allow'
        if mode is PermissionMode.accept_edits \
                and is_workspace_edit_command(command):
            return 'allow'
        if is_read_only(command):
            return 'allow'
        return 'ask'

    def decide(self, tool_name: str, tool_input, mode=None) -> str:
        mode = self._effective_mode(mode)
        env_all = tool_name == BASH_TOOL
        target = self.path_of(tool_name, tool_input)
        if _rule_matches(self._deny, tool_name, tool_input, env_all, self.cwd,
                         target):
            return 'deny'
        if _rule_matches(self._ask, tool_name, tool_input, env_all, self.cwd,
                         target):
            return 'ask'
        if mode is PermissionMode.bypass_permissions:
            return 'allow'
        if tool_name in AUTO_TOOLS:
            return 'allow'
        if tool_name == BASH_TOOL:
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
            if choice.rule:
                self.remember_allow(tool_name, tool_input, rule=choice.rule)
            elif self._rememberable(tool_name, tool_input):
                self.remember_allow(tool_name, tool_input)
        if choice.value in ('approved', 'dont_ask'):
            if not choice.feedback:
                return True
            return _pre_tool_decision('allow', context=choice.feedback)
        return _pre_tool_decision(
            'deny', reason=reject_message(choice.feedback, bool(agent)))
