from __future__ import annotations

from enum import Enum


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
