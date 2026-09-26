"""Durable session transcripts, logs and metadata under the PyClaw home."""
from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

from conippets import jsonl

from pyclaw.home import pyclaw_home

DEFAULT_CLEANUP_DAYS = 30


def _logs_dir() -> Path:
    logs = pyclaw_home() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs


def _session_path(session_id) -> Path:
    return _logs_dir() / str(session_id)


def _session_dir(session_id) -> Path:
    session = _session_path(session_id)
    session.mkdir(parents=True, exist_ok=True)
    return session


def _session_index_path() -> Path:
    return _logs_dir() / "session_index.json"


def resolve_session_id(logical_key, *, rotate: bool = False) -> str:
    path = _session_index_path()
    index = {}
    if path.exists():
        index = json.loads(path.read_text(encoding="utf-8"))
    key = json.dumps(logical_key, ensure_ascii=False, sort_keys=True)
    session_id = index.get(key)
    if session_id is None or rotate:
        session_id = uuid.uuid4().hex
        index[key] = session_id
        path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    return session_id


def transcript_path(session_id) -> Path:
    return _session_path(session_id) / "transcript.jsonl"


_ENTRY_META = ("uuid", "parentUuid")


def load_entries(session_id) -> list:
    path = transcript_path(session_id)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            entries.append(record)
    return entries


def load_transcript(session_id) -> list:
    return [{key: value for key, value in entry.items()
             if key not in _ENTRY_META}
            for entry in load_entries(session_id)]


def session_meta(session_id) -> dict:
    path = _session_path(session_id) / "meta.json"
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    return meta if isinstance(meta, dict) else {}


def list_sessions() -> list:
    root = _logs_dir()
    if not root.exists():
        return []
    sessions = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        path = entry / 'transcript.jsonl'
        if not path.exists():
            continue
        sessions.append({'id': entry.name,
                         'title': str(session_meta(entry.name).get('title')
                                      or ''),
                         'messages': len(load_entries(entry.name)),
                         'modified': path.stat().st_mtime})
    sessions.sort(key=lambda item: item['modified'], reverse=True)
    return sessions


def match_sessions(query) -> list:
    """The saved conversations a person's word refers to: an exact id or name
    wins, otherwise everything whose id or name starts with it."""
    wanted = ' '.join(str(query or '').split()).lower()
    if not wanted:
        return []
    sessions = list_sessions()
    exact = [item for item in sessions
             if item['id'].lower() == wanted
             or item['title'].lower() == wanted]
    if exact:
        return exact
    return [item for item in sessions
            if item['id'].lower().startswith(wanted)
            or item['title'].lower().startswith(wanted)]


def _expired(path: Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def sweep(days: int = DEFAULT_CLEANUP_DAYS) -> int:
    """Drop conversations nobody has touched for `days`, the plan beside them
    and their snapshots, so a long-lived home does not grow forever."""
    cutoff = time.time() - max(1, int(days)) * 86400
    removed = 0
    logs = _logs_dir()
    for entry in logs.iterdir():
        if entry.is_dir() and _expired(entry, cutoff):
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        elif entry.name.startswith('pyclaw.log.') and _expired(entry, cutoff):
            entry.unlink(missing_ok=True)
    plans = plans_dir()
    for path in plans.glob('*.md'):
        if _expired(path, cutoff):
            path.unlink(missing_ok=True)
    history = pyclaw_home() / 'file-history'
    if history.exists():
        for entry in history.iterdir():
            if entry.is_dir() and _expired(entry, cutoff):
                shutil.rmtree(entry, ignore_errors=True)
    return removed


def _content_of(entry) -> dict:
    return {key: value for key, value in entry.items()
            if key not in _ENTRY_META}


def _chained(payload, parent):
    records = []
    for message in payload:
        record = dict(message)
        record["uuid"] = uuid.uuid4().hex
        record["parentUuid"] = parent
        parent = record["uuid"]
        records.append(record)
    return records


def _append_records(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_records(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def save_transcript(session_id, messages) -> None:
    path = transcript_path(session_id)
    payload = [m for m in messages if isinstance(m, dict)]
    entries = load_entries(session_id)
    written = 0
    while (written < len(entries) and written < len(payload)
           and _content_of(entries[written]) == payload[written]):
        written += 1
    if written == len(payload) == len(entries):
        return
    if written and written == len(entries):
        _append_records(path, _chained(payload[written:],
                                       entries[-1].get("uuid")))
        return
    _write_records(path, _chained(payload, None))


_session_loggers: dict[str, logging.Logger] = {}


def session_logger(session_id) -> logging.Logger:
    if session_id not in _session_loggers:
        name = f"session.{session_id}"
        log = logging.getLogger(name)
        log.setLevel(logging.DEBUG)
        log.propagate = False
        handler = logging.FileHandler(
            _session_dir(session_id) / "run.log", encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s"
        ))
        log.addHandler(handler)
        _session_loggers[session_id] = log
    return _session_loggers[session_id]


def close_session_logger(session_id) -> None:
    log = _session_loggers.pop(session_id, None)
    if log is not None:
        for handler in list(log.handlers):
            handler.close()
            log.removeHandler(handler)


def record_meta(session_id, meta: dict) -> None:
    path = _session_dir(session_id) / "meta.json"
    existing = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    existing.setdefault("session_id", str(session_id))
    existing.update(meta)
    path.write_text(
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def rename_session(session_id, title: str) -> str:
    title = ' '.join(str(title or '').split())
    if not title:
        raise ValueError('a conversation needs a name')
    record_meta(session_id, {"title": title})
    return title


def title_of(session_id) -> str:
    return str(session_meta(session_id).get('title') or '')


def plans_dir() -> Path:
    return pyclaw_home() / 'plans'


def plan_file(session_id) -> Path:
    """One plan per conversation, named after it: two conversations can never
    write over each other's plan."""
    return plans_dir() / f'{session_id}.md'


def history_dir(session_id) -> Path:
    return pyclaw_home() / 'file-history' / str(session_id)


def adopt_artifacts(source_id, target_id) -> None:
    """Hand the plan and the snapshots of an inherited conversation to the one
    that continues it: the copy keeps the original intact, and snapshots are
    shared by hard link rather than duplicated."""
    if not source_id or not target_id or source_id == target_id:
        return
    source_plan, target_plan = plan_file(source_id), plan_file(target_id)
    if source_plan.exists() and not target_plan.exists():
        target_plan.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_plan, target_plan)
    source_history, target_history = (history_dir(source_id),
                                       history_dir(target_id))
    if not source_history.exists():
        return
    target_history.mkdir(parents=True, exist_ok=True)
    for entry in source_history.iterdir():
        link = target_history / entry.name
        if link.exists():
            continue
        try:
            os.link(entry, link)
        except OSError:
            shutil.copyfile(entry, link)


def first_prompt(session_id) -> str:
    for entry in load_transcript(session_id):
        if entry.get('role') != 'user':
            continue
        content = entry.get('content')
        if isinstance(content, str) and content.strip():
            return ' '.join(content.split())[:100]
    return 'Branched conversation'


def branch_title(base: str, taken) -> str:
    if f'{base} (Branch)' not in taken:
        return f'{base} (Branch)'
    number = 2
    while f'{base} (Branch {number})' in taken:
        number += 1
    return f'{base} (Branch {number})'


def create_branch(session_id, title: str = '') -> dict:
    """Copy this conversation into a new session that carries on from here."""
    entries = load_entries(session_id)
    if not entries:
        raise ValueError('there is no conversation to branch')
    fork_id = uuid.uuid4().hex
    records = []
    parent = None
    for entry in entries:
        record = dict(entry)
        record['uuid'] = uuid.uuid4().hex
        record['parentUuid'] = parent
        record['forkedFrom'] = {'sessionId': str(session_id),
                                'uuid': entry.get('uuid')}
        parent = record['uuid']
        records.append(record)
    _write_records(transcript_path(fork_id), records)
    taken = {item['title'] for item in list_sessions() if item['title']}
    effective = branch_title(
        ' '.join(str(title or '').split()) or first_prompt(session_id), taken)
    record_meta(fork_id, {'title': effective, 'forked_from': str(session_id)})
    return {'id': fork_id, 'title': effective, 'messages': len(records),
            'forked_from': str(session_id)}


def append_conv(session_id, role, content, *, reasoning_content=None, topic=None,
                name=None, path=None):
    record = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "role": role,
        "content": content,
    }
    if reasoning_content:
        record["reasoning_content"] = reasoning_content
    if topic:
        record["topic"] = topic
    if name:
        record["name"] = name
    jsonl.append(path or _session_dir(session_id) / "messages.jsonl", [record])

