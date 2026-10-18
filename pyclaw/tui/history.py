from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pyclaw.home import pyclaw_home

HISTORY_LOAD_LIMIT = 500


def path() -> Path:
    return pyclaw_home() / "history.jsonl"


def load(project: str, limit: int = HISTORY_LOAD_LIMIT) -> list[str]:
    """Submitted prompts for this project, oldest first. A prompt re-submitted
    later moves to the end, matching how the up-arrow walks recency."""
    file = path()
    if not file.exists():
        return []
    seen: dict[str, None] = {}
    try:
        for line in file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("project") != project:
                continue
            display = str(entry.get("display") or "")
            if display:
                seen[display] = None
    except OSError:
        return []
    return list(seen)[-limit:]


def append(project: str, text: str) -> None:
    """Record a submitted prompt for this project, dropping the earlier copy
    of the same text so the newest position wins."""
    file = path()
    entries: list[dict] = []
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    entries = [entry for entry in entries if not (entry.get("project") == project and entry.get("display") == text)]
    entries.append({"display": text, "project": project, "at": datetime.now(timezone.utc).isoformat()})
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    except OSError:
        pass
