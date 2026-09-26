from __future__ import annotations

from pathlib import Path


def workspace(cwd) -> Path:
    return Path(cwd).resolve()


def roots(cwd, extra=()) -> list[Path]:
    """Every directory the session is allowed to work in: the one it started
    in, plus any added with /add-dir."""
    return [workspace(cwd)] + [Path(extra_one).resolve()
                               for extra_one in (extra or ())]


def resolve(cwd, p, extra=()) -> Path | None:
    path = Path(p)
    if not path.is_absolute():
        path = workspace(cwd) / path
    resolved = path.resolve()
    for base in roots(cwd, extra):
        if resolved.is_relative_to(base):
            return resolved
    return None


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
