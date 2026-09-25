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


def relative(cwd, p) -> str:
    try:
        return str(Path(p).resolve().relative_to(workspace(cwd)))
    except ValueError:
        return str(p)


SKIP_DIRS = frozenset({'.git', '.hg', '.svn', '__pycache__', 'node_modules',
                       '.venv', 'venv', 'dist', 'build', '.tox', '.idea',
                       '.pyclaw'})


def skipped(path) -> bool:
    return any(part in SKIP_DIRS for part in Path(path).parts)
