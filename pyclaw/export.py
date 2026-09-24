from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

MAX_SLUG = 40

CODE_BLOCK = re.compile(r'^```(\w*)[ \t]*\n(.*?)\n```', re.S | re.M)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, dict) and block.get('type') == 'text':
            parts.append(str(block.get('text') or ''))
    return '\n\n'.join(part for part in parts if part)


def replies(transcript: list) -> list[str]:
    out = []
    for message in transcript or []:
        if message.get('role') != 'assistant':
            continue
        body = _text_of(message.get('content')).strip()
        if body:
            out.append(body)
    return out


def copy_targets(transcript: list, *, which: int = 1,
                 block: int = 0) -> list[tuple[str, str]]:
    found = replies(transcript)
    if not found or which < 1 or which > len(found):
        return []
    reply = found[-which]
    targets = [('whole reply', reply)]
    targets += [(language or 'code', code.strip())
                for language, code in CODE_BLOCK.findall(reply)]
    if block:
        if block > len(targets):
            return []
        return [targets[block]]
    return targets


def slug(transcript: list) -> str:
    for message in transcript or []:
        if message.get('role') != 'user':
            continue
        first = _text_of(message.get('content')).strip().split('\n')[0]
        kept = re.sub(r'[^A-Za-z0-9]+', '-', first).strip('-').lower()
        if kept:
            return kept[:MAX_SLUG].rstrip('-')
    return 'session'


def filename_for(transcript: list, when: datetime | None = None) -> str:
    moment = (when or datetime.now()).strftime('%Y-%m-%d-%H%M%S')
    return f'{moment}-{slug(transcript)}.md'


def export_text(transcript: list, *, session_id: str = '') -> str:
    lines = [f'# PyClaw session {session_id}' if session_id
             else '# PyClaw session', '']
    for message in transcript or []:
        role = message.get('role')
        body = _text_of(message.get('content')).strip()
        if role == 'assistant' and body:
            lines += ['**pyclaw**', '', body, '']
        elif role == 'user' and body:
            lines += ['**you**', '', body, '']
        elif role == 'user':
            tools = [block for block in (message.get('content') or [])
                     if isinstance(block, dict)
                     and block.get('type') == 'tool_result']
            if tools:
                lines += ['> (tool results were returned)', '']
    return '\n'.join(lines).rstrip() + '\n'


def write(transcript: list, *, session_id: str = '', directory=None,
          name: str = '') -> Path:
    target = Path(name) if name else (
        Path(directory or Path.cwd())
        / filename_for(transcript))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(export_text(transcript, session_id=session_id),
                      encoding='utf-8')
    return target
