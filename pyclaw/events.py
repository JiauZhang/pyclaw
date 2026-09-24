from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

KEEP_DAYS = 7

from chatchat.hooks.events import (AGENT_PROGRESS, AGENT_REASON_START,
                                   AGENT_STATE, AGENT_TEXT, AGENT_TOOL_CALL,
                                   AGENT_TOOL_RESULT, AGENT_TURN_FINISHED,
                                   AGENT_WARN)

KINDS = {AGENT_TEXT: 'text', AGENT_REASON_START: 'reason',
         AGENT_TOOL_CALL: 'tool_call', AGENT_TOOL_RESULT: 'tool_result',
         AGENT_TURN_FINISHED: 'turn_finished', AGENT_PROGRESS: 'progress',
         AGENT_WARN: 'warn', AGENT_STATE: 'state'}

TOOLS = {'Bash': 'command', 'Read': 'file_path', 'Write': 'file_path',
         'Edit': 'file_path', 'Glob': 'pattern', 'Grep': 'pattern'}


def _directory() -> Path:
    from pyclaw import pyclaw_home
    return pyclaw_home() / 'events'


def _text(data) -> str:
    for key in ('text', 'delta', 'output', 'message', 'prompt', 'summary'):
        value = data.get(key)
        if isinstance(value, str) and value:
            return value
    return ''


def _read(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def describe(event) -> dict | None:
    kind = getattr(event, 'kind', None) or (event.get('kind')
                                             if isinstance(event, dict) else '')
    if not kind:
        return None
    agent = getattr(event, 'agent', None)
    data = getattr(event, 'data', None)
    if data is None:
        data = {key: value for key, value in event.items()
                if key not in ('kind', 'agent')}
    data = data if isinstance(data, dict) else {}
    row = {'kind': KINDS.get(kind, str(kind)), 'agent': agent or '',
           'text': _text(data)}
    if row['kind'] == 'tool_call':
        row['tool'] = str(data.get('tool') or '')
        tool_input = data.get('input')
        if isinstance(tool_input, dict):
            key = TOOLS.get(row['tool'])
            if key and isinstance(tool_input.get(key), str):
                row[key] = tool_input[key]
    if row['kind'] == 'progress':
        row['subagent_type'] = str(data.get('subagent_type') or '')
        if data.get('done'):
            row['done'] = True
    return row


class Stream:

    def __init__(self, directory: Path, at: datetime, session: str):
        self._directory = directory
        self._at = at
        self._session = session
        self._closed = False
        self.chars = 0
        self.tools: list[str] = []
        self.turns = 0

    def __call__(self, event) -> None:
        if self._closed:
            return
        row = describe(event)
        if row is None:
            return
        if row['kind'] == 'text':
            self.chars += len(row['text'])
        elif row['kind'] == 'tool_call':
            self.tools.append(row.get('tool', ''))
        elif row['kind'] == 'turn_finished':
            self.turns += 1
        self._write(row)

    def _write(self, row: dict) -> None:
        stamp = self._at.replace(microsecond=0).isoformat()
        body = {'at': stamp, 'session': self._session, **row}
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            with (self._directory
                  / f'{self._at.date().isoformat()}.jsonl').open(
                      'a', encoding='utf-8') as handle:
                handle.write(json.dumps(body, ensure_ascii=False,
                                        default=str) + '\n')
        except OSError:
            return

    def turn_facts(self) -> dict:
        return {'chars': self.chars, 'tool_calls': len(self.tools),
                'tools': list(dict.fromkeys(self.tools)), 'turns': self.turns}

    def note_error(self, text: str, agent: str = '') -> None:
        self._write({'kind': 'error', 'agent': agent, 'text': str(text)})

    def close(self) -> None:
        self._closed = True


def error_rows(directory: Path, *, until=None, days: int = 7) -> list[dict]:
    from datetime import date, timedelta

    last = until or date.today()
    first = last - timedelta(days=max(1, int(days)) - 1)
    out = []
    try:
        files = sorted(Path(directory).glob('*.jsonl'))
    except OSError:
        return []
    for path in files:
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if not first <= day <= last:
            continue
        for row in _read(path):
            if row.get('kind') == 'error':
                out.append(row)
    return out


def read_errors(*, until=None, days: int = 7) -> list[dict]:
    return error_rows(_directory(), until=until, days=days)


def _forget(directory: Path, before) -> None:
    try:
        files = list(directory.glob('*.jsonl'))
    except OSError:
        return
    for path in files:
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day < before:
            path.unlink(missing_ok=True)


def open_stream(*, at: datetime | None = None,
                session: str = '') -> Stream:
    moment = at or datetime.now()
    directory = _directory()
    _forget(directory, moment.date() - timedelta(days=KEEP_DAYS))
    return Stream(directory, moment, session)
