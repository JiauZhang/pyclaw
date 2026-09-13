from __future__ import annotations

from pathlib import Path


def workspace(cwd) -> Path:
    return Path(cwd).resolve()


def resolve(cwd, p) -> Path | None:
    base = workspace(cwd)
    path = Path(p)
    if not path.is_absolute():
        path = base / path
    resolved = path.resolve()
    if not resolved.is_relative_to(base):
        return None
    return resolved
