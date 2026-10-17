from __future__ import annotations

from enum import Enum


_MODE_NAMES = {
    "default": "default",
    "acceptEdits": "acceptEdits",
    "accept_edits": "acceptEdits",
    "plan": "plan",
    "allowAll": "allowAll",
    "allow_all": "allowAll",
}


class PermissionMode(str, Enum):
    default = "default"
    accept_edits = "acceptEdits"
    plan = "plan"
    allow_all = "allowAll"


def parse_mode(value) -> PermissionMode:
    key = str(value).strip()
    if key not in _MODE_NAMES:
        raise ValueError(f"Unknown permission mode: {value}. Use one of: default, acceptEdits, plan, allowAll.")
    return PermissionMode(_MODE_NAMES[key])


def next_mode(value, allow_all_available: bool = False) -> PermissionMode:
    cur = parse_mode(value)
    if cur is PermissionMode.default:
        return PermissionMode.accept_edits
    if cur is PermissionMode.accept_edits:
        return PermissionMode.plan
    if cur is PermissionMode.plan:
        return PermissionMode.allow_all if allow_all_available else PermissionMode.default
    return PermissionMode.default
