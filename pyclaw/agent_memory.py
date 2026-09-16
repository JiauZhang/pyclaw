from __future__ import annotations

from pathlib import Path


def _user_memory_file() -> Path:
    from pyclaw import __pyclaw_home__
    return Path(__pyclaw_home__) / 'AGENTS.md'


def load_instruction_files(cwd: str) -> list[dict]:
    files: list[dict] = []
    user = _user_memory_file()
    if user.exists():
        try:
            files.append({'path': str(user),
                          'content': user.read_text(encoding='utf-8'),
                          'load_reason': 'user'})
        except OSError:
            pass
    current = Path(cwd).resolve()
    for directory in [current, *current.parents]:
        candidate = directory / 'AGENTS.md'
        if not candidate.exists():
            continue
        try:
            content = candidate.read_text(encoding='utf-8')
        except OSError:
            continue
        files.append({'path': str(candidate), 'content': content,
                      'load_reason': 'project'})
    return files


def load_project_memory(cwd: str) -> str:
    return '\n\n'.join(item['content']
                       for item in load_instruction_files(cwd))
