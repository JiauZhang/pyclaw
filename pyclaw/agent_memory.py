from __future__ import annotations

from pathlib import Path

TEMPLATE = '''# PYCLAW

Project instructions for PyClaw. This file is loaded into the agent's
context at the start of every session (claude: CLAUDE.md).

- Describe build/test commands, conventions and gotchas.
- Keep it short; anything the agent must always know goes here.
'''


def _user_memory_file() -> Path:
    from pyclaw import __pyclaw_home__
    return Path(__pyclaw_home__) / 'PYCLAW.md'


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
        candidate = directory / 'PYCLAW.md'
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
    """claude CLAUDE.md 的等价物：用户级 ~/.pyclaw/PYCLAW.md 在前，
    项目级由 cwd 向上收集、由近及远拼接（越近越贴上下文）。"""
    return '\n\n'.join(item['content']
                       for item in load_instruction_files(cwd))


def init_project_memory(cwd: str) -> str:
    """claude /init：生成项目级 PYCLAW.md 模板，已存在则不覆盖。"""
    target = Path(cwd) / 'PYCLAW.md'
    if target.exists():
        return f'already exists: {target}'
    target.write_text(TEMPLATE, encoding='utf-8')
    return f'created {target}'
