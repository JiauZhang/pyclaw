import asyncio

from pyclaw.agent_memory import (load_instruction_files,
                                 load_project_memory)
from pyclaw.agents import build_team


def _team(tmp_path):
    async def main():
        return build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path))
    return asyncio.run(main())


def _user_memory(tmp_path, monkeypatch):
    user_dir = tmp_path / 'user'
    user_dir.mkdir()
    (user_dir / 'AGENTS.md').write_text('user rules', encoding='utf-8')
    monkeypatch.setattr('pyclaw.agent_memory._user_memory_file',
                        lambda: user_dir / 'AGENTS.md')
    return user_dir


def test_load_user_then_project_nearest_first(tmp_path, monkeypatch):
    _user_memory(tmp_path, monkeypatch)
    project = tmp_path / 'proj'
    nested = project / 'a' / 'b'
    nested.mkdir(parents=True)
    (project / 'AGENTS.md').write_text('project root rules', encoding='utf-8')
    (nested / 'AGENTS.md').write_text('nested rules', encoding='utf-8')

    text = load_project_memory(str(nested))
    assert text.index('user rules') < text.index('nested rules')
    assert text.index('nested rules') < text.index('project root rules')


def test_load_missing_returns_empty(tmp_path):
    assert load_project_memory(str(tmp_path)) == ''


def test_build_team_appends_project_memory(tmp_path, monkeypatch):
    monkeypatch.setattr('pyclaw.agent_memory.load_project_memory',
                        lambda cwd: 'PROJECT MEMORY MARKER')
    instruction = _team(tmp_path).lead.instruction
    assert 'PROJECT MEMORY MARKER' in instruction
    assert 'capable AI assistant' in instruction


def test_load_instruction_files_reports_paths_and_scopes(tmp_path,
                                                         monkeypatch):
    user_dir = _user_memory(tmp_path, monkeypatch)
    project = tmp_path / 'proj'
    nested = project / 'a'
    nested.mkdir(parents=True)
    (project / 'AGENTS.md').write_text('root rules', encoding='utf-8')
    (nested / 'AGENTS.md').write_text('nested rules', encoding='utf-8')

    files = load_instruction_files(str(nested))
    assert [f['memory_type'] for f in files] == ['User', 'Project', 'Project']
    assert {f['load_reason'] for f in files} == {'session_start'}
    assert [f['content'] for f in files] == ['user rules', 'nested rules',
                                             'root rules']
    assert files[0]['path'] == str(user_dir / 'AGENTS.md')


def test_build_team_registers_instruction_files(tmp_path):
    (tmp_path / 'AGENTS.md').write_text('rules', encoding='utf-8')
    files = _team(tmp_path).instruction_files
    assert any(item['content'] == 'rules' for item in files)
