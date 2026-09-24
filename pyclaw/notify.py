from __future__ import annotations

import os

BACKENDS = ('auto', 'bell', 'iterm2', 'kitty', 'ghostty', 'disabled')

BACKEND_BY_PROGRAM = (
    ('iterm', 'iterm2'), ('kitty', 'kitty'), ('ghostty', 'ghostty'),
    ('wezterm', 'ghostty'))

BEL = '\007'

ST = '\033\\'

ENV_KEY = 'PYCLAW_NOTIFICATIONS'


def _clean(text) -> str:
    kept = ''.join(char for char in str(text or '')
                   if char not in BEL + ST and char.isprintable())
    return kept.strip()


def detect(env=None) -> str:
    source = env if env is not None else os.environ
    choice = str(source.get(ENV_KEY) or 'auto').strip().lower()
    if choice and choice != 'auto':
        if choice not in BACKENDS:
            raise ValueError(f'notifications must be one of '
                             f'{", ".join(BACKENDS)}, not {choice!r}')
        return choice
    program = str(source.get('TERM_PROGRAM') or '').lower()
    for needle, backend in BACKEND_BY_PROGRAM:
        if needle in program:
            return backend
    return 'bell'


def title_escape(title: str) -> str:
    return f'\033]0;{_clean(title)}{BEL}'


def escape_for(backend: str, title: str, body: str) -> str:
    head = title_escape(title)
    if backend not in BACKENDS:
        raise ValueError(f'unknown notification backend {backend!r}')
    if backend == 'disabled':
        return head
    if backend == 'bell':
        return head + BEL
    if backend == 'iterm2':
        return head + f'\033]9;{_clean(body)}{BEL}'
    if backend == 'kitty':
        return head + f'\033]99;d={_clean(body)};t={_clean(title)}{ST}'
    return (head + f'\033]777;notify;{_clean(title)};{_clean(body)}{ST}')


def message(reply: str, tool_calls: int = 0) -> str:
    if str(reply or '').strip():
        return 'PyClaw has an answer for you'
    return 'PyClaw is waiting for you'


def notify(write, *, title: str, body: str, backend: str = 'auto',
           env=None) -> str:
    chosen = detect(env) if backend == 'auto' else backend
    sequence = escape_for(chosen, title, body)
    write(sequence)
    return sequence


def clipboard(text: str) -> str:
    import base64

    payload = base64.b64encode(str(text).encode('utf-8')).decode('ascii')
    return f'\033]52;c;{payload}\007'


def set_title(write, title: str) -> str:
    sequence = title_escape(title)
    write(sequence)
    return sequence
