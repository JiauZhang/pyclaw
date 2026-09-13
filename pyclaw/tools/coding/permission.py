from __future__ import annotations

from enum import Enum
from pathlib import Path

from .paths import resolve

READ_TOOLS = frozenset({'Read', 'Glob', 'Grep', 'LS'})
WRITE_TOOLS = frozenset({'Write', 'Edit', 'MultiEdit'})

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


def _rule_matches(rules, tool_name: str) -> bool:
    if not rules:
        return False
    return any(r == tool_name or str(r).split('(')[0] == tool_name
               for r in rules)


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

    def remember_allow(self, tool_name: str):
        if tool_name not in self._allow:
            self._allow.append(tool_name)

    @staticmethod
    def _in_workspace(cwd: Path, input) -> bool:
        fields = _path_fields(input)
        if not fields:
            return True
        return all(resolve(cwd, f) is not None for f in fields)

    def decide(self, tool_name: str, tool_input) -> str:
        if _rule_matches(self._deny, tool_name):
            return 'deny'
        if _rule_matches(self._ask, tool_name):
            return 'ask'
        if _rule_matches(self._allow, tool_name):
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
        if choice == 'dont_ask':
            self.remember_allow(tool_name)
        if choice in ('approved', 'dont_ask'):
            return True
        return {'decision': 'block', 'reason': f'{tool_name} was not approved.'}
