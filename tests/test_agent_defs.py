import asyncio
import tempfile
from pathlib import Path

import pytest

from chatchat.core.agents import AgentDefinition
from pyclaw import agent_defs as mod
from pyclaw.agent_defs import (BUILT_IN, AgentEntry, agent_count, discover,
                               list_order, load_agent_defs, model_display,
                               relative_path, remove_agent, tool_buckets,
                               validate, validate_type, write_agent)
from pyclaw.team_builder import build_team
from pyclaw.tools import BUILTIN_TOOLS


@pytest.fixture(autouse=True)
def user_agents(tmp_path, monkeypatch):
    """Keeps every test's user-scope agents inside its own temporary home."""
    user = tmp_path / "user-agents"
    monkeypatch.setattr(mod, "_user_agents_dir", lambda: user)
    return user


ALL_TOOLS = list(BUILTIN_TOOLS)


def _write_agent(root: Path, filename: str, text: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_text(text, encoding='utf-8')


REVIEWER = '''---
name: reviewer
description: Reviews code changes for correctness and style
tools: Read, Glob, Grep, Bash
---

You are a strict code reviewer. Report issues only.
'''

WILDCARD = '''---
name: researcher
description: Deep research on any topic
tools: '*'
---

Research thoroughly and cite sources.
'''

NO_TOOLS = '''---
name: planner
description: Drafts implementation plans without touching files
permissionMode: plan
---

Plan first, edit never.
'''


def _project(tmp_path, filename, text):
    root = tmp_path / '.pyclaw' / 'agents'
    _write_agent(root, filename, text)
    return root


async def _agent_def(cwd, agent_type):
    team = build_team('agnes', 'agnes-2.5-flash', cwd=str(cwd))
    return team.agent_defs.get(agent_type)


def test_load_agent_defs_parses_and_resolves_tools():
    with tempfile.TemporaryDirectory() as d:
        agents_dir = Path(d) / '.pyclaw' / 'agents'
        _write_agent(agents_dir, 'reviewer.md', REVIEWER)
        _write_agent(agents_dir, 'researcher.md', WILDCARD)
        _write_agent(agents_dir, 'planner.md', NO_TOOLS)
        all_tools = list(BUILTIN_TOOLS)
        defs = {x.agent_type: x for x in load_agent_defs(d, all_tools=all_tools)}
        assert set(defs) >= {'reviewer', 'researcher', 'planner'}
        assert {t.name for t in defs['reviewer'].tools} <= {t.name for t in all_tools}
        assert 'Read' in {t.name for t in defs['reviewer'].tools}
        assert len(defs['researcher'].tools) == len(all_tools)
        assert len(defs['planner'].tools) == len(all_tools)
        assert 'strict code reviewer' in defs['reviewer'].system_prompt
        assert defs['reviewer'].description == \
            'Reviews code changes for correctness and style'
        assert defs['planner'].permission_mode == 'plan'
        assert defs['reviewer'].permission_mode is None


def test_load_agent_defs_skips_invalid_and_project_overrides_user(tmp_path,
                                                                  user_agents):
    _write_agent(user_agents, 'reviewer.md', REVIEWER)
    _write_agent(user_agents, 'no-name.md', 'just some docs, no frontmatter')
    _write_agent(user_agents, 'bad.md', '---\ndescription: no name here\n---\nbody')

    with tempfile.TemporaryDirectory() as d:
        project = Path(d) / '.pyclaw' / 'agents'
        _write_agent(project, 'reviewer.md',
                     REVIEWER.replace('strict code reviewer',
                                      'project-level reviewer'))
        defs = {x.agent_type: x for x in load_agent_defs(d, all_tools=[])}
        assert 'project-level reviewer' in defs['reviewer'].system_prompt
        assert 'reviewer' in defs and 'no-name' not in defs


def test_build_team_offers_every_agent_it_loaded(tmp_path):
    _project(tmp_path, 'reviewer.md', REVIEWER)

    async def main():
        team = build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path))
        schema = next(t for t in team.tool_schemas(team.tool_context)
                      if t['name'] == 'Agent')
        return team.agent_defs, schema['description']

    defs, description = asyncio.run(main())
    for agent_type in ('reviewer', 'statusline-setup'):
        assert defs.get(agent_type).agent_type == agent_type
        assert agent_type in description
    assert 'Reviews code changes' in description


def test_the_status_line_setup_agent_is_a_builtin(tmp_path):
    defn = asyncio.run(_agent_def(tmp_path, 'statusline-setup'))
    assert {t.name for t in defn.tools} == {'Read', 'Edit'}
    assert 'statusLine' in defn.system_prompt
    assert 'context_window' in defn.system_prompt
    assert 'PS1' in defn.system_prompt
    assert 'status line' in defn.description


def test_a_user_definition_replaces_the_builtin(tmp_path, user_agents):
    _write_agent(user_agents, 'statusline-setup.md', '''---
name: statusline-setup
description: Local status line setup
tools: Read
---

Local override.
''')
    defn = asyncio.run(_agent_def(tmp_path, 'statusline-setup'))
    assert {t.name for t in defn.tools} == {'Read'}
    assert defn.system_prompt == 'Local override.'


def test_discovery_lists_builtins_then_user_then_project(tmp_path, user_agents):
    _write_agent(user_agents, 'reviewer.md', REVIEWER)
    _project(tmp_path, 'planner.md', NO_TOOLS)

    rows = discover(str(tmp_path), all_tools=ALL_TOOLS)
    assert [(r.agent_type, r.scope) for r in rows] == [
        ('statusline-setup', 'built-in'), ('reviewer', 'user'),
        ('planner', 'project')]
    assert rows[0].path is None
    assert rows[2].path.name == 'planner.md'


def test_discovery_marks_the_scope_that_wins(tmp_path, user_agents):
    _write_agent(user_agents, 'reviewer.md', REVIEWER)
    _project(tmp_path, 'reviewer.md', REVIEWER)
    rows = {r.scope: r for r in discover(str(tmp_path), all_tools=ALL_TOOLS)
            if r.agent_type == 'reviewer'}
    assert rows['user'].shadowed_by == 'project'
    assert rows['project'].shadowed_by is None


def test_a_builtin_can_be_shadowed_by_a_user_file(tmp_path, user_agents):
    _write_agent(user_agents, 'statusline-setup.md', REVIEWER
                 .replace('name: reviewer', 'name: statusline-setup'))
    rows = discover(str(tmp_path), all_tools=ALL_TOOLS)
    assert rows[0].shadowed_by == 'user'


def test_writing_an_agent_produces_a_file_the_loader_reads_back(tmp_path):
    defn = AgentDefinition('worker',
                           system_prompt='Do the work carefully.\nMore detail.',
                           tools=[t for t in ALL_TOOLS if t.name in ('Read',)],
                           description='He said "hi" and\nkept going',
                           model='some-model')
    path = write_agent(defn, 'project', str(tmp_path), all_tools=ALL_TOOLS)
    assert path == tmp_path / '.pyclaw' / 'agents' / 'worker.md'
    loaded = {d.agent_type: d for d in load_agent_defs(str(tmp_path),
                                                       all_tools=ALL_TOOLS)}
    assert loaded['worker'].description == 'He said "hi" and\nkept going'
    assert [t.name for t in loaded['worker'].tools] == ['Read']
    assert loaded['worker'].model == 'some-model'
    assert 'Do the work carefully.' in loaded['worker'].system_prompt


def test_an_all_tools_agent_omits_the_tools_line(tmp_path):
    text = write_agent(AgentDefinition('wide', system_prompt='prompt body',
                                       tools=list(ALL_TOOLS)),
                       'user', str(tmp_path), all_tools=ALL_TOOLS
                       ).read_text(encoding='utf-8')
    assert 'tools:' not in text


def test_writing_over_an_existing_file_in_the_same_scope_refuses(tmp_path):
    _project(tmp_path, 'worker.md', REVIEWER)
    with pytest.raises(FileExistsError) as caught:
        write_agent(AgentDefinition('worker', system_prompt='x'),
                    'project', str(tmp_path), all_tools=ALL_TOOLS)
    assert 'already defined' in str(caught.value)


def test_removing_an_agent_deletes_only_that_scope(tmp_path, user_agents):
    _write_agent(user_agents, 'reviewer.md', REVIEWER)
    _project(tmp_path, 'reviewer.md', REVIEWER)
    project = [r for r in discover(str(tmp_path), all_tools=ALL_TOOLS)
               if r.agent_type == 'reviewer' and r.scope == 'project'][0]
    remove_agent(project)
    assert not project.path.exists()
    assert (user_agents / 'reviewer.md').exists()


def test_a_builtin_cannot_be_removed(tmp_path):
    builtin = [r for r in discover(str(tmp_path), all_tools=ALL_TOOLS)
               if r.scope == 'built-in'][0]
    with pytest.raises(ValueError):
        remove_agent(builtin)


def _draft(name='code-reviewer', prompt=None, tools=('Read', 'Grep'),
           description='Reviews changes for defects'):
    chosen = [t for t in ALL_TOOLS if t.name in tools]
    return AgentDefinition(
        name,
        system_prompt=prompt or ('Report only real defects, with the file and '
                                 'line number for each one.'),
        tools=chosen, description=description)


def test_validation_reports_name_prompt_and_tool_problems():
    errors, warnings = validate(_draft(name='ab', prompt='too short',
                                       description='hi'),
                                known_tools=['Read', 'Edit'], taken=[])
    assert any('at least 3 characters' in e for e in errors)
    assert any('not enough' in e for e in errors)
    assert any('easier to read' in w for w in warnings)


def test_validation_accepts_a_well_formed_draft():
    errors, warnings = validate(_draft(), known_tools=['Read', 'Grep'],
                                taken=[])
    assert errors == []
    assert warnings == []


def test_validation_flags_an_empty_tool_pick_and_a_cross_scope_twin():
    errors, warnings = validate(_draft(tools=()), known_tools=['Read'],
                                taken=[('code-reviewer', 'user')])
    assert any('tool' in e for e in errors)
    assert any('already taken by your agents' in e for e in errors)
    assert warnings == []


def test_the_identifier_step_rejects_a_name_that_cannot_be_a_file():
    assert validate_type('') == 'An agent needs a name'
    assert validate_type('my agent') is not None
    assert 'hyphens' in validate_type('my agent')
    assert validate_type('code-reviewer') is None


def test_the_detail_view_shows_a_display_path_per_scope(tmp_path):
    built_in = AgentEntry('a', BUILT_IN, None, AgentDefinition('a'))
    project = AgentEntry('a', 'project', tmp_path / 'a.md', AgentDefinition('a'))
    user = AgentEntry('a', 'user', tmp_path / 'a.md', AgentDefinition('a'))
    assert relative_path(built_in) == 'Bundled agents'
    assert relative_path(project) == '.pyclaw/agents/a.md'
    assert relative_path(user) == '~/.pyclaw/agents/a.md'


def test_tools_are_grouped_by_their_kind():
    names = [t.name for t in ALL_TOOLS]
    assert tool_buckets(names) == [
        ('Reading and search', ['Read', 'Glob', 'Grep', 'TaskOutput', 'TaskStop']),
        ('Editing files', ['Write', 'Edit']),
        ('Running commands', ['Bash'])]
    assert tool_buckets([]) == []


def test_the_panel_groups_by_scope_and_sorts_each_group(tmp_path, user_agents):
    _write_agent(user_agents, 'zeta.md', REVIEWER)
    _write_agent(user_agents, 'beta.md', REVIEWER.replace('reviewer', 'beta'))
    _project(tmp_path, 'alpha.md', REVIEWER.replace('reviewer', 'alpha'))
    rows = discover(str(tmp_path), all_tools=ALL_TOOLS)
    assert [(r.agent_type, r.scope) for r in list_order(rows)] == [
        ('beta', 'user'), ('reviewer', 'user'),
        ('alpha', 'project'), ('statusline-setup', 'built-in')]


def test_the_header_count_excludes_shadowed_scopes(tmp_path, user_agents):
    _write_agent(user_agents, 'reviewer.md', REVIEWER)
    _project(tmp_path, 'reviewer.md', REVIEWER)
    assert agent_count(discover(str(tmp_path), all_tools=ALL_TOOLS)) == 2


def test_an_agent_without_a_model_shows_the_team_default():
    assert model_display(AgentDefinition('a'), 'team-model') == 'team-model'
    assert model_display(AgentDefinition('a', model='other'), 'team-model') \
        == 'other'


def test_editing_an_agent_rewrites_its_own_file(tmp_path):
    _project(tmp_path, 'worker.md', REVIEWER)
    write_agent(AgentDefinition('worker', system_prompt='New body.',
                                tools=[t for t in ALL_TOOLS
                                       if t.name == 'Read'],
                                description='Reviews code for real defects'),
                'project', str(tmp_path), all_tools=ALL_TOOLS,
                overwrite=True)
    loaded = {d.agent_type: d for d in
              load_agent_defs(str(tmp_path), all_tools=ALL_TOOLS)}
    assert loaded['worker'].system_prompt == 'New body.'
    assert [t.name for t in loaded['worker'].tools] == ['Read']


def test_agents_given_as_json_become_definitions():
    defs = mod.parse_agents_json(
        '{"reviewer": {"description": "reads diffs", "prompt": "be harsh",'
        ' "tools": ["Read", "Grep"], "model": "cheap-m",'
        ' "permissionMode": "plan"}}', ALL_TOOLS)

    assert [d.agent_type for d in defs] == ['reviewer']
    assert defs[0].description == 'reads diffs'
    assert defs[0].system_prompt == 'be harsh'
    assert [t.name for t in defs[0].tools] == ['Read', 'Grep']
    assert defs[0].model == 'cheap-m'
    assert defs[0].permission_mode == 'plan'


def test_an_agent_given_without_tools_sees_all_of_them():
    defs = mod.parse_agents_json('{"scout": {"description": "d"}}', ALL_TOOLS)

    assert {t.name for t in defs[0].tools} == {t.name for t in ALL_TOOLS}


def test_bad_agent_json_is_reported_instead_of_swallowed():
    for broken in ('{"a": ', '[]', '{"a": "not an object"}'):
        with pytest.raises(ValueError) as err:
            mod.parse_agents_json(broken, ALL_TOOLS)
        assert '--agents' in str(err.value)


async def _cli_agent(cwd, agents_json):
    team = build_team('agnes', 'agnes-2.5-flash', cwd=str(cwd),
                      agents_json=agents_json)
    return team.agent_defs.get('reviewer')


def test_agents_from_the_command_line_win_over_a_file(tmp_path, user_agents):
    write_agent(AgentDefinition('reviewer', system_prompt='from file',
                               description='from file'),
                'user', str(tmp_path), ALL_TOOLS)
    defn = asyncio.run(_cli_agent(
        tmp_path, '{"reviewer": {"description": "from flag",'
                  ' "prompt": "from flag"}}'))

    assert defn.description == 'from flag'
    assert defn.system_prompt == 'from flag'


def test_agents_given_on_the_command_line_shadow_a_file_of_the_same_name(tmp_path):
    _project(tmp_path, 'reviewer.md', REVIEWER)
    given = [AgentDefinition('reviewer', description='from the flag',
                             system_prompt='from the flag')]

    entries = mod.discover(str(tmp_path), ALL_TOOLS, cli=given)
    by_scope = {e.scope: e for e in entries if e.agent_type == 'reviewer'}

    assert set(by_scope) == {mod.PROJECT, mod.CLI}
    assert by_scope[mod.PROJECT].shadowed_by == mod.CLI
    assert by_scope[mod.CLI].shadowed_by is None
    assert mod.list_order(entries)[0].scope == mod.CLI
    assert [d.description for d in
            mod.load_agent_defs(str(tmp_path), ALL_TOOLS, cli=given)
            if d.agent_type == 'reviewer'] == ['from the flag']




def test_an_agent_can_be_given_a_memory_scope():
    defs = mod.parse_agents_json(
        '{"reviewer": {"description": "reads diffs", "prompt": "be harsh",'
        ' "memory": "project"}}', ALL_TOOLS)
    assert defs[0].memory == 'project'


def test_a_memory_scope_that_is_not_one_of_the_three_is_dropped():
    defs = mod.parse_agents_json(
        '{"reviewer": {"description": "d", "prompt": "p", '
        '"memory": "galaxy"}}', ALL_TOOLS)
    assert defs[0].memory is None


def test_the_frontmatter_of_an_agent_file_carries_its_scope():
    defn = mod._definition_from_md(
        '---\nname: reviewer\ndescription: reads diffs\nmemory: user\n---\n\n'
        + 'be harsh ' * 10, ALL_TOOLS)
    assert defn.memory == 'user'


def test_a_written_agent_keeps_its_scope_on_the_way_out():
    defn = AgentDefinition('reviewer', system_prompt='p' * 30,
                           tools=list(ALL_TOOLS), description='d' * 20,
                           memory='local')
    text = mod.render_agent_md(defn, ALL_TOOLS)
    assert 'memory: local' in text
    assert mod._definition_from_md(text, ALL_TOOLS).memory == 'local'
