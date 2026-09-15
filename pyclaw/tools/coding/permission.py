from __future__ import annotations

import fnmatch
import os
from enum import Enum
from pathlib import Path

from conippets import json

from .paths import resolve
from .shell_rules import (bash_rule_matches, is_dangerous_removal,
                          is_read_only, is_workspace_edit_command,
                          suggested_rule)

READ_TOOLS = frozenset({'Read', 'Glob', 'Grep', 'LS'})
WRITE_TOOLS = frozenset({'Write', 'Edit', 'MultiEdit'})
BASH_TOOL = 'Bash'

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
                  env_all: bool = False, cwd=None) -> bool:
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
        if _path_rule_matches(arg.rstrip(')'), tool_input, cwd):
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


def _path_rule_matches(pattern: str, tool_input, cwd) -> bool:
    if not isinstance(tool_input, dict) or cwd is None:
        return False
    fields = _path_fields(tool_input)
    if not fields:
        return False
    return any(_matches_pattern(pattern, f, cwd) for f in fields)


def _path_fields(input) -> list[str]:
    fields = []
    for key in ('file_path', 'path', 'pattern'):
        val = input.get(key) if isinstance(input, dict) else None
        if val:
            fields.append(str(val))
    return fields


def _user_settings_file() -> Path:
    from pyclaw import __pyclaw_home__
    return Path(__pyclaw_home__) / 'settings.json'


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
                 ask=(), deny=(), request=None):
        self.mode: PermissionMode = parse_mode(mode)
        self.cwd = Path(cwd).resolve()
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

    def remember_allow(self, tool_name: str, tool_input=None):
        rule = tool_name
        if tool_name == BASH_TOOL:
            command = _command_of(tool_input)
            if not command:
                return
            rule = suggested_rule(command) or f'Bash({" ".join(command.split())})'
        if rule not in self._allow:
            self._allow.append(rule)
            self._layers.append(('allow', rule, 'session'))
        self._save_local_rule(rule)

    @staticmethod
    def _in_workspace(cwd: Path, input) -> bool:
        fields = _path_fields(input)
        if not fields:
            return True
        return all(resolve(cwd, f) is not None for f in fields)

    def _rememberable(self, tool_name: str, tool_input) -> bool:
        if tool_name == BASH_TOOL:
            return suggested_rule(_command_of(tool_input)) is not None
        return True

    def _decide_bash(self, tool_input) -> str:
        command = _command_of(tool_input)
        if is_dangerous_removal(command):
            return 'ask'
        if _rule_matches(self._allow, BASH_TOOL, tool_input):
            return 'allow'
        if self.mode is PermissionMode.accept_edits \
                and is_workspace_edit_command(command):
            return 'allow'
        if is_read_only(command):
            return 'allow'
        return 'ask'

    def decide(self, tool_name: str, tool_input) -> str:
        env_all = tool_name == BASH_TOOL
        if _rule_matches(self._deny, tool_name, tool_input, env_all, self.cwd):
            return 'deny'
        if _rule_matches(self._ask, tool_name, tool_input, env_all, self.cwd):
            return 'ask'
        if self.mode is PermissionMode.bypass_permissions:
            return 'allow'
        if tool_name == BASH_TOOL:
            return self._decide_bash(tool_input)
        if _rule_matches(self._allow, tool_name, tool_input, cwd=self.cwd):
            return 'allow'
        if tool_name in WRITE_TOOLS:
            if self.mode is PermissionMode.plan:
                return 'deny'
            if self._in_workspace(self.cwd, tool_input):
                if self.mode is PermissionMode.accept_edits:
                    return 'allow'
                return 'ask'
            return 'ask'
        if tool_name in READ_TOOLS:
            if self._in_workspace(self.cwd, tool_input):
                return 'allow'
            return 'allow' if self.mode is PermissionMode.plan else 'ask'
        return 'ask'

    async def authorize(self, tool_name: str, tool_input) -> bool | dict:
        decision = self.decide(tool_name, tool_input)
        if decision == 'allow':
            return True
        if decision == 'deny':
            return {'decision': 'block',
                    'reason': f'{tool_name} is not allowed in {self.mode.value} '
                              f'mode / by your permission rules.'}
        if self.request is None:
            return {'decision': 'block',
                    'reason': f'{tool_name} needs approval but no app is '
                              f'present to ask.'}
        choice = await self.request(tool_name, tool_input)
        if choice == 'dont_ask' and self._rememberable(tool_name, tool_input):
            self.remember_allow(tool_name, tool_input)
        if choice in ('approved', 'dont_ask'):
            return True
        return {'decision': 'block', 'reason': f'{tool_name} was not approved.'}
