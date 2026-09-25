import asyncio

from chatchat.core.agents import AgentDefinition

from pyclaw.tui import PyClawApp
from pyclaw.tui.agent_form import AGENT_STEPS, _CHECKED, _UNCHECKED
from pyclaw.tui.theme import POINTER
from pyclaw.tui.agents_panel import AgentsScreen

from test_tui import _FakeTeam, _flatten
from markup import plain as _plain


REVIEWER = '''---
name: reviewer
description: Reviews code for real defects
tools: Read, Grep
model: review-model
---

Read the diff and report only real defects.
'''


class _AgentsTeam(_FakeTeam):

    def __init__(self, cwd, tools):
        super().__init__()
        from chatchat.core.agents import AgentRegistry
        from types import SimpleNamespace
        self.agent_defs = AgentRegistry()
        self.agent_defs.define('general-purpose', system_prompt='p',
                               default=True)
        self._pyclaw_gate = SimpleNamespace(
            cwd=cwd, mode=SimpleNamespace(value='default'),
            bypass_available=False)
        self.provided_tools = list(tools)
        self.cli_agent_defs = []

    def register_agent_definition(self, defn):
        return self.agent_defs.register(defn)

    def remove_agent_definition(self, agent_type):
        return self.agent_defs.remove(agent_type)


def _tools():
    from pyclaw.tools.coding import CODING_TOOLS
    return list(CODING_TOOLS)


def _home(monkeypatch, tmp_path):
    from pyclaw import agent_defs as mod
    user = tmp_path / 'home' / 'agents'
    user.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, '_user_agents_dir', lambda: user)
    return user


def _project(tmp_path):
    root = tmp_path / '.pyclaw' / 'agents'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_agents(root, *names):
    for name in names:
        (root / f'{name}.md').write_text(REVIEWER.replace('reviewer', name),
                                         encoding='utf-8')
    return root


def _body(screen) -> str:
    from textual.widgets import Static
    return str(screen.query_one('#agents-body', Static).content)


async def _open_panel(app, pilot):
    screen = AgentsScreen(app._session)
    app.push_screen(screen)
    await pilot.pause()
    return screen


def test_agents_command_opens_the_panel(tmp_path):
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            await pilot.press(*"/agents")
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(4):
                await pilot.pause()
            assert isinstance(app.screen, AgentsScreen)
            assert isinstance(app.screen._session._team, _AgentsTeam)
    asyncio.run(scenario())


def test_the_list_groups_scopes_and_offers_create(monkeypatch, tmp_path):
    user = _write_agents(_home(monkeypatch, tmp_path), 'reviewer')
    project = _write_agents(_project(tmp_path), 'planner')
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            body = _plain(_body(screen))
            assert 'Agents' in body
            assert '3 agents' in body
            assert 'New agent' in body
            assert f'Your agents ({user})' in body
            assert f'This project ({project})' in body
            assert 'Bundled with PyClaw' in body
            lines = [line for line in body.splitlines() if 'planner' in line]
            assert lines and 'review-model' in lines[0]
    asyncio.run(scenario())


def test_navigation_wraps_and_skips_built_in_rows(monkeypatch, tmp_path):
    _write_agents(_home(monkeypatch, tmp_path), 'one', 'two')
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            await pilot.press('up')
            assert screen._selected().agent_type == 'two'
            await pilot.press('down')
            body = _plain(_body(screen))
            assert any(line.startswith(POINTER) and 'New agent' in line
                       for line in body.splitlines())
            assert screen._selected() is None
            built_in = _plain(_body(screen)).split('Bundled with PyClaw')[1]
            assert POINTER not in built_in
    asyncio.run(scenario())


def test_a_built_in_agent_only_offers_view(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._pos = 0
            entry = next(e for e in screen._entries
                         if e.agent_type == 'statusline-setup')
            screen._open(entry)
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'Open' in body
            assert 'Edit agent' not in body
            assert 'Delete agent' not in body
    asyncio.run(scenario())


def test_view_shows_the_agent_fields_in_a_fixed_order(monkeypatch, tmp_path):
    _write_agents(_home(monkeypatch, tmp_path), 'reviewer')
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            order = [body.index(part) for part in (
                '~/.pyclaw/agents/reviewer.md', 'Description', 'Tools',
                'Model', 'System prompt')]
            assert order == sorted(order)
            assert 'Read, Grep' in body
            assert 'review-model' in body
            assert 'report only real defects' in body.lower()
            await pilot.press('escape')
            await pilot.pause()
            assert 'Open' in _plain(_body(screen))
    asyncio.run(scenario())


def test_deleting_an_agent_removes_the_file_and_the_type(monkeypatch, tmp_path):
    path, team = _reviewer_home(monkeypatch, tmp_path)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('down', 'down')
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'Remove reviewer for good?' in body
            assert 'Source: user' in body
            await pilot.press('enter')
            await pilot.pause()
            assert not path.exists()
            assert team.agent_defs.find('reviewer') is None
            assert 'Removed reviewer' in screen._changes
            await pilot.press('escape')
            for _ in range(4):
                await pilot.pause()
            assert 'What changed:' in _plain(_flatten(app))
            assert 'Removed reviewer' in _plain(_flatten(app))
    asyncio.run(scenario())


def test_the_create_wizard_writes_a_file_and_uses_it_live(monkeypatch, tmp_path):
    user = _home(monkeypatch, tmp_path)
    project = _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            await pilot.press('enter')
            await pilot.pause()
            assert 'Name (identifier)' in _plain(_body(screen))
            await _type(pilot, screen, 'test-runner')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Where to save' in _plain(_body(screen))
            body = _plain(_body(screen))
            assert 'This project (.pyclaw/agents/)' in body
            assert 'Your agents (~/.pyclaw/agents/)' in body
            await pilot.press('enter')
            await pilot.pause()
            assert 'System prompt' in _plain(_body(screen))
            await _type(pilot, screen,
                        'Run the test suite and report which tests fail.')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Description' in _plain(_body(screen))
            await _type(pilot, screen, 'Use this agent to run the tests.')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Pick tools' in _plain(_body(screen))
            body = _plain(_body(screen))
            assert 'Continue' in _body(screen) and 'All tools' in body
            assert 'Reading and search' in body and 'Running commands' in body
            await pilot.press('enter')
            await pilot.pause()
            assert 'Pick a model' in _plain(_body(screen))
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'Review and save' in body
            assert 'test-runner' in body
            assert 's or enter saves' in body
            await pilot.press('s')
            for _ in range(4):
                await pilot.pause()
            written = (project / 'test-runner.md').read_text(encoding='utf-8')
            assert 'name: test-runner' in written
            assert team.agent_defs.find('test-runner') is not None
            assert 'Added test-runner' in screen._changes
            assert 'test-runner' in _plain(_body(screen))
    asyncio.run(scenario())


async def _type(pilot, screen, text):
    from textual.widgets import Input
    screen.query_one('#agents-input', Input).value = text
    await pilot.pause()


def test_the_wizard_blocks_an_invalid_name_until_it_is_fixed(monkeypatch,
                                                              tmp_path):
    _home(monkeypatch, tmp_path)
    _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            await pilot.press('enter')
            await pilot.pause()
            await _type(pilot, screen, 'a_b')
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'letters, digits and hyphens only' in body
            assert 'Where to save' not in body
            await _type(pilot, screen, 'ok-name')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Where to save' in _plain(_body(screen))
    asyncio.run(scenario())


def test_escaping_the_panel_without_changes_only_dismisses(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            await pilot.press('escape')
            for _ in range(4):
                await pilot.pause()
            assert not isinstance(app.screen, AgentsScreen)
            assert 'Closed the agents list' in _plain(_flatten(app))
    asyncio.run(scenario())


def test_the_tools_step_marks_buckets_by_full_selection(monkeypatch, tmp_path):
    from pyclaw import agent_defs
    _home(monkeypatch, tmp_path)
    _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._start_create()
            screen._step = AGENT_STEPS.index('tools')
            screen._refresh()
            body = _plain(_body(screen))
            assert f'{_CHECKED} All tools' in body
            assert 'Every tool picked' in body
            assert '[ Continue ]' in _body(screen).replace('\\', '')
            # Collapsed by default: the individual list is behind the toggle.
            assert 'Show the tool list' in _body(screen)
            for _ in range(len(screen._tool_items()) - 1):
                await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            assert screen._tools_individual
            names = screen._names
            first = names[0]
            body = _plain(_body(screen))
            assert f'{_CHECKED} {first}' in body
            # One tool off makes its bucket partially selected, so the bucket
            # row is no longer checked and the counter names the remainder.
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            assert f'{_UNCHECKED} {first}' in body
            assert f'{_UNCHECKED} {agent_defs.bucket_of(first)}' in body
            assert f'{len(names) - 1} of {len(names)} picked' in body
    asyncio.run(scenario())


def test_no_agents_yet_shows_the_create_hint(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            body = _plain(_body(screen))
            assert 'Nothing defined yet' in body
            assert 'New agent' in body
            assert 'Bundled with PyClaw' in body
    asyncio.run(scenario())


def _reviewer_home(monkeypatch, tmp_path):
    user = _write_agents(_home(monkeypatch, tmp_path), 'reviewer')
    team = _AgentsTeam(tmp_path, _tools())
    team.register_agent_definition(
        AgentDefinition('reviewer', description='Reviews code for real defects',
                        tools=[], model='review-model'))
    return user / 'reviewer.md', team


def test_editing_tools_rewrites_the_file_and_the_live_definition(
        monkeypatch, tmp_path):
    path, team = _reviewer_home(monkeypatch, tmp_path)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Change tools' in _plain(_body(screen))
            await pilot.press('enter')
            await pilot.pause()
            assert screen._mode == 'edit-tools'
            body = _plain(_body(screen))
            assert f'{_UNCHECKED} Reading and search' in body
            assert f'{_UNCHECKED} Editing files' in body
            assert '2 of' in body and 'picked' in body
            for _ in range(3):
                await pilot.press('down')
            await pilot.press('enter')
            for _ in range(3):
                await pilot.press('up')
            await pilot.press('enter')
            await pilot.pause()
            written = path.read_text(encoding='utf-8')
            tools_line = next(line for line in written.splitlines()
                              if line.startswith('tools:'))
            assert set(tools_line.split(': ', 1)[1].split(', ')) == {
                'Read', 'Grep', 'Edit', 'Write'}
            live = team.agent_defs.find('reviewer')
            assert {t.name for t in live.tools} == {'Read', 'Grep', 'Edit',
                                                    'Write'}
            assert screen._changes == ['Saved changes to reviewer']
            assert 'Saved changes to reviewer' in _plain(_body(screen))
    asyncio.run(scenario())


def test_editing_the_model_rewrites_just_the_model(
        monkeypatch, tmp_path):
    path, team = _reviewer_home(monkeypatch, tmp_path)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Change tools' in _plain(_body(screen))
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            assert screen._mode == 'edit-model'
            assert screen.query_one('#agents-input').value == 'review-model'
            await _type(pilot, screen, 'other-model')
            await pilot.press('enter')
            await pilot.pause()
            written = path.read_text(encoding='utf-8')
            assert 'model: other-model' in written
            assert 'tools: Read, Grep' in written
            assert team.agent_defs.find('reviewer').model == 'other-model'
            assert screen._changes == ['Saved changes to reviewer']
    asyncio.run(scenario())


def test_an_agent_from_the_command_line_is_listed_and_read_only(tmp_path):
    team = _AgentsTeam(tmp_path, _tools())
    team.cli_agent_defs = [AgentDefinition('auditor', description='from --agents',
                                           system_prompt='check every claim')]

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            return screen, _plain(_body(screen))

    screen, body = asyncio.run(scenario())
    assert 'Given with --agents' in body
    assert 'auditor' in body
    assert [e.agent_type for e in screen._selectable] == []


def _noted(monkeypatch, tmp_path, pending=False):
    from chatchat.core.agent_memory import AgentMemory

    user = _write_agents(_home(monkeypatch, tmp_path), 'reviewer')
    (user / 'reviewer.md').write_text(
        REVIEWER.replace('model: review-model\n',
                         'model: review-model\nmemory: project\n'),
        encoding='utf-8')
    team = _AgentsTeam(tmp_path, _tools())
    workspace = tmp_path / '.pyclaw'
    team.agent_memory = AgentMemory(
        roots={'user': tmp_path / 'home' / 'agent-memory',
               'project': workspace / 'agent-memory',
               'local': workspace / 'agent-memory-local'},
        snapshots=workspace / 'agent-memory-snapshots')
    team._pyclaw_snapshot_updates = ['reviewer'] if pending else []
    if pending:
        snapshot = team.agent_memory.directory('reviewer', 'project')
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / 'snapshot.json').write_text(
            '{"updatedAt": "2026-01-01T00:00:00Z"}', encoding='utf-8')
        (snapshot / 'MEMORY.md').write_text('- from the project copy',
                                            encoding='utf-8')
    return team


def test_an_agent_with_notes_says_where_they_are_kept(monkeypatch, tmp_path):
    team = _noted(monkeypatch, tmp_path)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('enter')
            await pilot.pause()
            return _plain(_body(screen))

    body = asyncio.run(scenario())
    assert 'Notes' in body and 'project' in body


def test_the_project_copy_of_the_notes_is_offered_and_applied(monkeypatch,
                                                               tmp_path):
    team = _noted(monkeypatch, tmp_path, pending=True)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.pause()
            menu = _plain(_body(screen))
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            held = (team.agent_memory.directory('reviewer', 'project')
                    / 'MEMORY.md')
            return menu, held.read_text(encoding='utf-8'), screen._changes

    menu, held, changes = asyncio.run(scenario())
    assert 'project copy' in menu
    assert 'from the project copy' in held
    assert changes and 'notes' in changes[-1]


def test_the_list_says_which_agents_have_newer_saved_notes(monkeypatch,
                                                            tmp_path):
    team = _noted(monkeypatch, tmp_path, pending=True)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = await _open_panel(app, pilot)
            return _plain(_body(screen))

    assert 'Newer notes are saved in this project for: reviewer' in (
        asyncio.run(scenario()))
