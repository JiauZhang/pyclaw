import asyncio

import pytest

from chatchat.core.agents import AgentDefinition

from test_tui import _FakeTeam, _flatten


@pytest.fixture(autouse=True)
def _no_status_line(monkeypatch):
    from pyclaw import statusline
    monkeypatch.setattr(statusline, 'configured_command', lambda: '')


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
    monkeypatch.setattr(mod, '_user_agents_dir', lambda: user)
    return user


def _project(tmp_path):
    root = tmp_path / '.pyclaw' / 'agents'
    root.mkdir(parents=True, exist_ok=True)
    return root


def _body(screen) -> str:
    from textual.widgets import Static
    return str(screen.query_one('#agents-body', Static).content)


def _plain(text: str) -> str:
    import re
    return re.sub(r"\[?/?[^\]\[\n]*\]", "", text)


def test_agents_command_opens_the_panel(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
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
    from pyclaw.tui import AgentsScreen, PyClawApp
    user = _home(monkeypatch, tmp_path)
    (user).mkdir(parents=True, exist_ok=True)
    (user / 'reviewer.md').write_text(REVIEWER, encoding='utf-8')
    project = _project(tmp_path)
    (project / 'planner.md').write_text(
        REVIEWER.replace('reviewer', 'planner'), encoding='utf-8')
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'Agents' in body
            assert '3 agents' in body
            assert 'Create new agent' in body
            assert f'User agents ({user})' in body
            assert f'Project agents ({project})' in body
            assert 'Built-in agents (always available)' in body
            lines = [line for line in body.splitlines() if 'planner' in line]
            assert lines and 'review-model' in lines[0]
    asyncio.run(scenario())


def test_navigation_wraps_and_skips_built_in_rows(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    user = _home(monkeypatch, tmp_path)
    user.mkdir(parents=True, exist_ok=True)
    (user / 'one.md').write_text(REVIEWER.replace('reviewer', 'one'),
                                 encoding='utf-8')
    (user / 'two.md').write_text(REVIEWER.replace('reviewer', 'two'),
                                 encoding='utf-8')
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press('up')
            assert screen._selected().agent_type == 'two'
            await pilot.press('down')
            body = _plain(_body(screen))
            assert any(line.startswith('›') and 'Create new agent' in line
                       for line in body.splitlines())
            assert screen._selected() is None
            built_in = _plain(_body(screen)).split('Built-in agents')[1]
            assert '›' not in built_in
    asyncio.run(scenario())


def test_a_built_in_agent_only_offers_view(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    _home(monkeypatch, tmp_path).mkdir(parents=True, exist_ok=True)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            screen._pos = 0
            entry = next(e for e in screen._entries
                         if e.agent_type == 'statusline-setup')
            screen._open(entry)
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'View agent' in body
            assert 'Edit agent' not in body
            assert 'Delete agent' not in body
    asyncio.run(scenario())


def test_view_shows_the_fields_in_the_reference_order(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    user = _home(monkeypatch, tmp_path)
    user.mkdir(parents=True, exist_ok=True)
    (user / 'reviewer.md').write_text(REVIEWER, encoding='utf-8')
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
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
            assert 'View agent' in _plain(_body(screen))
    asyncio.run(scenario())


def test_deleting_an_agent_removes_the_file_and_the_type(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    user = _home(monkeypatch, tmp_path)
    user.mkdir(parents=True, exist_ok=True)
    path = user / 'reviewer.md'
    path.write_text(REVIEWER, encoding='utf-8')
    team = _AgentsTeam(tmp_path, _tools())
    team.register_agent_definition(AgentDefinition('reviewer'))

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('down', 'down')
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'Are you sure you want to delete the agent reviewer?' in body
            assert 'Source: user' in body
            await pilot.press('enter')
            await pilot.pause()
            assert not path.exists()
            assert team.agent_defs.find('reviewer') is None
            assert 'Deleted agent: reviewer' in screen._changes
            await pilot.press('escape')
            for _ in range(4):
                await pilot.pause()
            assert 'Agent changes:' in _plain(_flatten(app))
            assert 'Deleted agent: reviewer' in _plain(_flatten(app))
    asyncio.run(scenario())


def test_the_create_wizard_writes_a_file_and_uses_it_live(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    user = _home(monkeypatch, tmp_path)
    user.mkdir(parents=True, exist_ok=True)
    project = _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            assert 'Agent type (identifier)' in _plain(_body(screen))
            await _type(pilot, screen, 'test-runner')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Choose location' in _plain(_body(screen))
            body = _plain(_body(screen))
            assert 'Project agents (.pyclaw/agents/)' in body
            assert 'User agents (~/.pyclaw/agents/)' in body
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
            assert 'Select tools' in _plain(_body(screen))
            body = _plain(_body(screen))
            assert 'Continue' in _body(screen) and 'All tools' in body
            assert 'Read-only tools' in body and 'Execution tools' in body
            await pilot.press('enter')
            await pilot.pause()
            assert 'Select model' in _plain(_body(screen))
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'Confirm and save' in body
            assert 'test-runner' in body
            assert 'Press s or Enter to save' in body
            await pilot.press('s')
            for _ in range(4):
                await pilot.pause()
            written = (project / 'test-runner.md').read_text(encoding='utf-8')
            assert 'name: test-runner' in written
            assert team.agent_defs.find('test-runner') is not None
            assert 'Created agent: test-runner' in screen._changes
            assert 'test-runner' in _plain(_body(screen))
    asyncio.run(scenario())


async def _type(pilot, screen, text):
    from textual.widgets import Input
    screen.query_one('#agents-input', Input).value = text
    await pilot.pause()


def test_the_wizard_blocks_an_invalid_name_until_it_is_fixed(monkeypatch,
                                                              tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    _home(monkeypatch, tmp_path).mkdir(parents=True, exist_ok=True)
    _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press('enter')
            await pilot.pause()
            await _type(pilot, screen, 'a_b')
            await pilot.press('enter')
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'must start and end with a letter or digit' in body
            assert 'Choose location' not in body
            await _type(pilot, screen, 'ok-name')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Choose location' in _plain(_body(screen))
    asyncio.run(scenario())


def test_escaping_the_panel_without_changes_only_dismisses(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    _home(monkeypatch, tmp_path).mkdir(parents=True, exist_ok=True)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            await pilot.press('escape')
            for _ in range(4):
                await pilot.pause()
            assert not isinstance(app.screen, AgentsScreen)
            assert 'Agents dialog dismissed' in _plain(_flatten(app))
    asyncio.run(scenario())


def test_the_tools_step_marks_buckets_by_full_selection(monkeypatch, tmp_path):
    from pyclaw import agent_defs
    from pyclaw.tui import (AgentsScreen, PyClawApp, AGENT_STEPS, _CHECKED,
                            _UNCHECKED)
    _home(monkeypatch, tmp_path).mkdir(parents=True, exist_ok=True)
    _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            screen._start_create()
            screen._step = AGENT_STEPS.index('tools')
            screen._refresh()
            body = _plain(_body(screen))
            assert f'{_CHECKED} All tools' in body
            assert 'All tools selected' in body
            assert '[ Continue ]' in _body(screen).replace('\\', '')
            # Collapsed by default: the individual list is behind the toggle.
            assert 'Show advanced options' in _body(screen)
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
            assert f'{len(names) - 1} of {len(names)} tools selected' in body
    asyncio.run(scenario())


def test_no_agents_yet_shows_the_create_hint(monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    _home(monkeypatch, tmp_path).mkdir(parents=True, exist_ok=True)
    _project(tmp_path)
    team = _AgentsTeam(tmp_path, _tools())

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            body = _plain(_body(screen))
            assert 'No agents found' in body
            assert 'Create new agent' in body
            assert 'Built-in agents' in body
    asyncio.run(scenario())


def _reviewer_home(monkeypatch, tmp_path):
    user = _home(monkeypatch, tmp_path)
    user.mkdir(parents=True, exist_ok=True)
    path = user / 'reviewer.md'
    path.write_text(REVIEWER, encoding='utf-8')
    team = _AgentsTeam(tmp_path, _tools())
    team.register_agent_definition(
        AgentDefinition('reviewer', description='Reviews code for real defects',
                        tools=[], model='review-model'))
    return user, path, team


def test_editing_tools_rewrites_the_file_and_the_live_definition(
        monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp, _UNCHECKED
    _path, path, team = _reviewer_home(monkeypatch, tmp_path)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Edit tools' in _plain(_body(screen))
            await pilot.press('enter')
            await pilot.pause()
            assert screen._mode == 'edit-tools'
            body = _plain(_body(screen))
            assert f'{_UNCHECKED} Read-only tools' in body
            assert f'{_UNCHECKED} Edit tools' in body
            assert '2 of' in body and 'tools selected' in body
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
                'Read', 'Grep', 'Edit', 'Write', 'MultiEdit'}
            live = team.agent_defs.find('reviewer')
            assert {t.name for t in live.tools} == {'Read', 'Grep', 'Edit',
                                                    'Write', 'MultiEdit'}
            assert screen._changes == ['Updated agent: reviewer']
            assert 'Updated agent: reviewer' in _plain(_body(screen))
    asyncio.run(scenario())


def test_editing_the_model_rewrites_just_the_model(
        monkeypatch, tmp_path):
    from pyclaw.tui import AgentsScreen, PyClawApp
    _path, path, team = _reviewer_home(monkeypatch, tmp_path)

    async def scenario():
        async with PyClawApp(builder=lambda: team).run_test() as pilot:
            app = pilot.app
            await pilot.pause()
            screen = AgentsScreen(app._session)
            app.push_screen(screen)
            await pilot.pause()
            screen._open(next(e for e in screen._entries
                              if e.agent_type == 'reviewer'))
            await pilot.press('down')
            await pilot.press('enter')
            await pilot.pause()
            assert 'Edit tools' in _plain(_body(screen))
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
            assert screen._changes == ['Updated agent: reviewer']
    asyncio.run(scenario())


def test_the_slash_command_lists_the_agents_without_the_panel():
    from types import SimpleNamespace
    from pyclaw.slash import handle_slash
    session = SimpleNamespace(agent_types=[('reviewer', 'Reviews code')])

    async def scenario():
        reply = await handle_slash('/agents', session)
        assert 'reviewer: Reviews code' in reply
    asyncio.run(scenario())


def test_the_slash_help_lists_the_command():
    from pyclaw.slash import HELP
    assert '/agents' in HELP
