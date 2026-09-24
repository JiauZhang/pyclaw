import asyncio

from pyclaw.agent_memory import (load_instruction_files,
                                 load_project_memory)
from pyclaw.team_builder import build_team


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


def _rule(directory, name, frontmatter, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f'{name}.md').write_text(
        f'---\n{frontmatter}\n---\n{body}', encoding='utf-8')


def test_a_rule_that_names_no_paths_joins_the_standing_instructions(tmp_path):
    _rule(tmp_path / '.pyclaw' / 'rules', 'style', 'paths: ',
          'Always two spaces.\n')
    files = load_instruction_files(str(tmp_path))
    assert [item['load_reason'] for item in files] == ['rules_dir']
    assert files[0]['content'] == 'Always two spaces.'
    assert 'Always two spaces.' in load_project_memory(str(tmp_path))


def test_a_rule_that_names_paths_stays_out_of_the_standing_ones(tmp_path):
    _rule(tmp_path / '.pyclaw' / 'rules', 'python', 'paths: src/*.py',
          'Annotate every argument.\n')
    assert 'Annotate' not in load_project_memory(str(tmp_path))


def test_reading_a_covered_file_brings_its_rule_into_the_turn(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src' / 'a.py').write_text('x = 1\n', encoding='utf-8')
    _rule(tmp_path / '.pyclaw' / 'rules', 'python', 'paths: src/*.py',
          'Annotate every argument.\n')

    async def main():
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path))
        out = await team.execute_tool('Read', {'file_path': 'src/a.py'},
                                      team.lead, 't1')
        await team.end_session('done')
        return out

    out = asyncio.run(main())
    assert 'Annotate every argument.' in out.additional_context
