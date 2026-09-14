import asyncio
import tempfile
from pathlib import Path

from pyclaw.agent_memory import init_project_memory, load_project_memory
from pyclaw.agents import build_team


def test_load_user_then_project_nearest_first(tmp_path, monkeypatch):
    user_dir = tmp_path / 'user'
    user_dir.mkdir()
    (user_dir / 'PYCLAW.md').write_text('user rules', encoding='utf-8')
    monkeypatch.setattr('pyclaw.agent_memory._user_memory_file',
                        lambda: user_dir / 'PYCLAW.md')

    project = tmp_path / 'proj'
    nested = project / 'a' / 'b'
    nested.mkdir(parents=True)
    (project / 'PYCLAW.md').write_text('project root rules', encoding='utf-8')
    (nested / 'PYCLAW.md').write_text('nested rules', encoding='utf-8')

    text = load_project_memory(str(nested))
    # user 在前；项目内由近及远（cwd 的最贴上下文，claude 同序）
    assert text.index('user rules') < text.index('nested rules')
    assert text.index('nested rules') < text.index('project root rules')


def test_load_missing_returns_empty(tmp_path):
    assert load_project_memory(str(tmp_path)) == ''


def test_init_creates_template_once(tmp_path):
    target = tmp_path / 'PYCLAW.md'
    first = init_project_memory(str(tmp_path))
    assert first.startswith('created')
    assert target.exists() and '# PYCLAW' in target.read_text(encoding='utf-8')
    second = init_project_memory(str(tmp_path))
    assert second.startswith('already')


def test_build_team_appends_project_memory(tmp_path, monkeypatch):
    (tmp_path / 'PYCLAW.md').write_text('PROJECT MEMORY MARKER',
                                        encoding='utf-8')
    monkeypatch.setattr('pyclaw.agent_memory.load_project_memory',
                        lambda cwd: 'PROJECT MEMORY MARKER')

    async def main():
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path))
        return team.lead.instruction

    assert 'PROJECT MEMORY MARKER' in asyncio.run(main())
    assert 'capable AI assistant' in asyncio.run(main())
