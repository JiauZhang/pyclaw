from __future__ import annotations

from enum import Enum
from pathlib import Path

from .paths import resolve
from .shell_rules import (bash_rule_matches, is_dangerous_removal,
                          is_read_only, is_workspace_edit_command)

READ_TOOLS = frozenset({'Read', 'Glob', 'Grep', 'LS'})
WRITE_TOOLS = frozenset({'Write', 'Edit', 'MultiEdit'})
BASH_TOOL = 'Bash'

_MODE_NAMES = {
    'default': 'default',
    'acceptEdits': 'acceptEdits',
    'accept_edits': 'acceptEdits',
    'plan': 'plan',
}


class PermissionMode(str, Enum):
    default = 'default'
    accept_edits = 'acceptEdits'
    plan = 'plan'


def parse_mode(value) -> PermissionMode:
    key = str(value).strip()
    if key not in _MODE_NAMES:
        raise ValueError(f'Unknown permission mode: {value}. '
                         f'Use one of: default, acceptEdits, plan.')
    return PermissionMode(_MODE_NAMES[key])


_MODE_CYCLE = ['default', 'acceptEdits', 'plan']


def next_mode(value) -> PermissionMode:
    cur = parse_mode(value)
    i = _MODE_CYCLE.index(cur.value)
    nxt = _MODE_CYCLE[(i + 1) % len(_MODE_CYCLE)]
    return PermissionMode(nxt)


def _command_of(tool_input) -> str:
    if isinstance(tool_input, dict):
        return str(tool_input.get('command') or '').strip()
    return ''


def _rule_matches(rules, tool_name: str, tool_input=None,
                  env_all: bool = False) -> bool:
    if not rules:
        return False
    command = _command_of(tool_input) if tool_name == BASH_TOOL else ''
    for rule in rules:
        name, _, arg = str(rule).partition('(')
        if name.strip() != tool_name:
            continue
        if not arg:
            return True
        if tool_name != BASH_TOOL:
            return True
        if command and bash_rule_matches(arg.rstrip(')'), command, env_all):
            return True
    return False


def _path_fields(input) -> list[str]:
    fields = []
    for key in ('file_path', 'path', 'pattern'):
        val = input.get(key) if isinstance(input, dict) else None
        if val:
            fields.append(str(val))
    return fields


class PermissionController:

    def __init__(self, *, mode: str = 'default', cwd, allow=(),
                 ask=(), deny=(), request=None):
        self.mode: PermissionMode = parse_mode(mode)
        self.cwd = Path(cwd).resolve()
        self._allow = list(allow)
        self._ask = list(ask)
        self._deny = list(deny)
        self.request = request

    def allowed_tool(self, name: str) -> bool:
        return not _rule_matches(self._deny, name)

    def remember_allow(self, tool_name: str, tool_input=None):
        rule = tool_name
        if tool_name == BASH_TOOL:
            command = _command_of(tool_input)
            if not command:
                return
            rule = f'Bash({command})'
        if rule not in self._allow:
            self._allow.append(rule)

    @staticmethod
    def _in_workspace(cwd: Path, input) -> bool:
        fields = _path_fields(input)
        if not fields:
            return True
        return all(resolve(cwd, f) is not None for f in fields)

    def _rememberable(self, tool_name: str, tool_input) -> bool:
        if tool_name == BASH_TOOL:
            return not is_dangerous_removal(_command_of(tool_input))
        return True

    def _decide_bash(self, tool_input) -> str:
        command = _command_of(tool_input)
        if _rule_matches(self._deny, BASH_TOOL, tool_input, env_all=True):
            return 'deny'
        if _rule_matches(self._ask, BASH_TOOL, tool_input, env_all=True):
            return 'ask'
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
        if tool_name == BASH_TOOL:
            return self._decide_bash(tool_input)
        if _rule_matches(self._deny, tool_name, tool_input):
            return 'deny'
        if _rule_matches(self._ask, tool_name, tool_input):
            return 'ask'
        if _rule_matches(self._allow, tool_name, tool_input):
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
        return 'allow'

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
