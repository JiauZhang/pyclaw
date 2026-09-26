"""Skills that ship with the program instead of a directory, and are
registered onto a team once that team exists."""
from __future__ import annotations

import logging
import os

from chatchat.knowledge.skills import Skill

from pyclaw import config
from pyclaw.home import pyclaw_home
from pyclaw.session import store as session_store
from pyclaw.tools.names import GLOB, GREP, READ

TAIL_BYTES = 64 * 1024
TAIL_LINES = 20
STEPS = (('GB', 1024 ** 3), ('MB', 1024 ** 2), ('KB', 1024))


def _size(value: int) -> str:
    for unit, divider in STEPS:
        if value >= divider:
            return f'{value // divider} {unit}'
    return f'{value} bytes'


def _tail(path) -> str:
    """The end of the log. Read from the end: a long session's log grows
    without bound and must not be loaded into memory whole."""
    try:
        size = path.stat().st_size
        with path.open('rb') as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            data = handle.read(TAIL_BYTES)
    except OSError as exc:
        return f'The end of the log could not be read: {exc}'
    lines = [line for line in
             data.decode('utf-8', errors='replace').split('\n')
             if line.strip()]
    if not lines:
        return 'Nothing has been written to it yet.'
    return (f'All of it is {_size(size)}. The last {TAIL_LINES} lines:\n\n'
            f'```\n' + '\n'.join(lines[-TAIL_LINES:]) + '\n```')


def _start_logging(conversation: str) -> bool:
    """Log every event from here on, and say whether that was already
    happening. Whatever went by before now was not recorded."""
    already = config.debug_on()
    os.environ['PYCLAW_DEBUG'] = '1'
    for handler in logging.getLogger().handlers:
        handler.setLevel(logging.DEBUG)
    if conversation:
        session_store.follow_conversation(conversation, level=logging.DEBUG)
    return already


def _debug_prompt(team, args: str = '') -> str:
    conversation = str(getattr(team, 'lead_session_id', '') or '')
    was_logging = _start_logging(conversation)
    path = session_store.conversation_log(conversation or 'default')
    gate = getattr(team, '_pyclaw_gate', None)
    parts = ['Find out what went wrong in this running session from what it '
             'recorded, not from what it is remembered.']
    if not was_logging:
        parts.append(
            '## Logging started here\n\n'
            'Event logging was off until now, so nothing before this moment was '
            'recorded. Say that logging is on from here, name the file, and ask '
            'for the problem to be repeated so there is something to read. To '
            'have it from the first line, set `PYCLAW_DEBUG=1` or `"debug": true` '
            'in the user settings.')
    parts.append('## What the session recorded\n\n'
                 f'The log of this conversation is at `{path}`.\n\n'
                 + _tail(path) +
                 f'\n\nFor anything outside this conversation, the whole '
                 f'process log is at `{pyclaw_home() / "logs" / "pyclaw.log"}`.')
    parts.append(
        '## The problem\n\n'
        + (args.strip() if args.strip() else
          'Nothing specific was said to be wrong. Read the log and report the '
          'errors, the warnings and anything else that stands out.'))
    if gate is not None:
        settings = gate.settings_files()
        parts.append('## Settings\n\n'
                     f'- for every project: `{settings["user"]}`\n'
                     f'- for this project: `{settings["project"]}`\n'
                     f'- only in this checkout: `{settings["local"]}`')
    parts.append(
        '## What to do\n\n'
        '1. Take the reported problem first.\n'
        '2. Search the whole file for ERROR and WARN, for tracebacks and for '
        'what failed just before it — the lines above are only the end of it.\n'
        '3. Say in plain words what went wrong, and what makes you think so.\n'
        '4. Give the concrete next step, and name the setting or file it '
        'belongs to.')
    return '\n\n'.join(parts)


def register_builtin_skills(registry, team) -> None:
    registry.register(Skill(
        name='debug',
        description='Find out what this session recorded about a problem and '
                    'say what to do about it',
        when_to_use='Only when the user asks for it by name',
        allowed_tools=(READ, GREP, GLOB),
        argument_hint='[what went wrong]',
        disable_model_invocation=True,
        source='builtin',
        builder=lambda args: _debug_prompt(team, args)))
