from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

SNAPSHOT_META = "snapshot.json"
SYNCED_META = ".snapshot-synced.json"


def _stamp(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


class SnapshotStore:
    """Published copies of an agent's notes, and how they land in a project.

    A team may keep a shared snapshot of an agent type's notes; each working
    copy is only replaced when the person asks for it.
    """

    def __init__(self, root):
        self.root = Path(root)

    def _directory(self, agent_type: str) -> Path:
        return self.root / agent_type.replace(":", "-")

    def _published(self, directory: Path) -> str | None:
        try:
            meta = json.loads((directory / SNAPSHOT_META).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return str(meta.get("updatedAt") or "") or None

    def _synced(self, directory: Path) -> str | None:
        try:
            meta = json.loads((directory / SYNCED_META).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return str(meta.get("syncedFrom") or "") or None

    def _copy(self, source: Path, target: Path) -> None:
        target.mkdir(parents=True, exist_ok=True)
        for entry in source.iterdir():
            if entry.is_file() and entry.name != SNAPSHOT_META:
                shutil.copyfile(entry, target / entry.name)

    def _note_synced(self, target: Path, stamp: str) -> None:
        (target / SYNCED_META).write_text(json.dumps({"syncedFrom": stamp}), encoding="utf-8")

    def _has_notes(self, directory: Path) -> bool:
        try:
            return any(entry.name.endswith(".md") and entry.is_file() for entry in directory.iterdir())
        except OSError:
            return False

    def sync(self, agent_type: str, local: Path) -> str:
        published = self._directory(agent_type)
        stamp = self._published(published)
        if stamp is None:
            return "none"
        if not self._has_notes(local):
            self._copy(published, local)
            self._note_synced(local, stamp)
            return "initialized"
        synced = _stamp(self._synced(local) or "")
        published_stamp = _stamp(stamp)
        if synced is None or published_stamp is None or published_stamp > synced:
            return "update"
        return "none"

    def apply(self, agent_type: str, local: Path) -> str:
        published = self._directory(agent_type)
        stamp = self._published(published)
        if stamp is None:
            return "none"
        local.mkdir(parents=True, exist_ok=True)
        for entry in local.iterdir():
            if entry.is_file() and entry.name.endswith(".md"):
                entry.unlink()
        self._copy(published, local)
        self._note_synced(local, stamp)
        return "applied"
