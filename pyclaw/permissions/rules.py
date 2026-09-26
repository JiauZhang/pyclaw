from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from conippets import json

from pyclaw.permissions.bash_rules import (
    bash_allowed_by, bash_covered_by, rule_content, _rule_name)

from pyclaw.home import pyclaw_home
from pyclaw.tools.names import BASH


def _command_of(tool_input) -> str:
    if isinstance(tool_input, dict):
        return str(tool_input.get('command') or '').strip()
    return ''


def split_rules(text: str) -> list[str]:
    """One command-line value can carry several rules; commas inside the
    parentheses of a rule argument belong to that rule."""
    out, depth, current = [], 0, ''
    for char in str(text):
        if char == '(':
            depth += 1
        elif char == ')':
            depth = max(0, depth - 1)
        if char == ',' and depth == 0:
            out.append(current)
            current = ''
        else:
            current += char
    out.append(current)
    return [item.strip() for item in out if item.strip()]
def _rule_matches(rules, tool_name: str, tool_input=None, cwd=None,
                  target=None, every_part: bool = False) -> bool:
    """`every_part` is the allow question — is each part of this command
    covered — while the default is the deny question: does any part fall under
    a rule. The two differ so that one unsafe part cannot slip through inside
    a command the user already allowed."""
    if not rules:
        return False
    command = _command_of(tool_input) if tool_name == BASH else ''
    if tool_name == BASH:
        mine = [rule for rule in rules if _rule_name(rule) == BASH]
        if not command or not mine:
            return any(not rule_content(rule) for rule in rules)
        return (bash_allowed_by(mine, command) if every_part
                else bash_covered_by(mine, command))
    for rule in rules:
        if _rule_name(rule) != tool_name:
            continue
        content = rule_content(rule)
        if not content:
            return True
        if target and cwd is not None \
                and _matches_pattern(content, target, cwd):
            return True
    return False


def matched_rule(rules, tool_name: str, tool_input=None, cwd=None,
                 target=None, every_part: bool = False) -> str:
    """The rule that decided this, for the log line that has to explain a
    permission answer without the user having to guess."""
    for rule in rules:
        if _rule_matches([rule], tool_name, tool_input, cwd, target,
                         every_part):
            return str(rule).strip()
    return ''


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
