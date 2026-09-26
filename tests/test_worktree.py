"""Worktrees: the session has to really move, and leaving must not throw work
away. Everything here runs against a real git repository."""
import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from pyclaw.session import Session
from pyclaw.session import store
from pyclaw.team.builder import build_team
from pyclaw.tui import PyClawApp

git = pytest.importorskip('shutil').which('git')


@pytest.fixture(autouse=True)
def _process_directory_is_put_back():
    start = Path(os.getcwd())
    yield
    os.chdir(start)


def _repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    for args in (['init', '-q'], ['config', 'user.email', 't@example.com'],
                 ['config', 'user.name', 'test']):
        subprocess.run([git, *args], cwd=root, check=True)
    (root / 'a.txt').write_text('committed\n', encoding='utf-8')
    subprocess.run([git, 'add', 'a.txt'], cwd=root, check=True)
    subprocess.run([git, 'commit', '-qm', 'first'], cwd=root, check=True)
    return root


def _team(root, conversation):
    return build_team('agnes', 'agnes-2.5-flash', cwd=str(root),
                      conversation_id=conversation)


def test_moving_into_a_worktree_moves_the_process_and_reads_it_from_there(
        tmp_path):
    root = _repo(tmp_path)

    async def main():
        team = _team(root, 'conv-roots')
        Session(team, session_id='conv-roots')
        await team.enter_worktree('aside')
        (team.tool_context.cwd / 'AGENTS.md').write_text('worktree rules\n',
                                                         encoding='utf-8')
        return await team.exit_worktree(keep=True)

    asyncio.run(main())
    inside = root / '.pyclaw' / 'worktrees' / 'aside'
    assert Path(os.getcwd()) == root

    async def again():
        team = _team(root, 'conv-roots-2')
        Session(team, session_id='conv-roots-2')
        await team.enter_worktree('aside')
        return team

    team = asyncio.run(again())
    assert Path(os.getcwd()) == inside.resolve() or Path(os.getcwd()) == inside
    loaded = [str(item.get('path') or '') for item in team.instruction_files]
    assert any(str(inside) in path for path in loaded)


def test_a_resumed_conversation_is_back_in_the_worktree_it_was_in(tmp_path):
    root = _repo(tmp_path)

    async def first():
        team = _team(root, 'conv-first')
        session = Session(team, session_id='conv-first')
        team.lead.messages.append({'role': 'user', 'content': 'start'})
        session.save_transcript()
        await team.enter_worktree('kept-alive')
        return team.worktree['path']

    path = asyncio.run(first())

    async def second():
        team = _team(root, 'conv-second')
        session = Session(team, session_id='conv-second',
                          resume_from='conv-first')
        session.restore_transcript()
        return session

    session = asyncio.run(second())
    assert session.worktree['name'] == 'kept-alive'
    assert Path(session.cwd) == path
    assert Path(os.getcwd()) == path


def test_a_saved_worktree_that_is_not_there_anymore_is_let_go(tmp_path):
    store.save_worktree('conv-x', {'name': 'gone', 'path': tmp_path / 'gone',
                                   'origin': tmp_path, 'root': tmp_path,
                                   'branch': 'worktree-gone'})
    assert store.load_worktree('conv-x') is None


def test_a_saved_worktree_comes_back_with_its_paths_as_paths(tmp_path):
    (tmp_path / 'here').mkdir()
    store.save_worktree('conv-y', {'name': 'here', 'path': tmp_path / 'here',
                                   'origin': tmp_path, 'root': tmp_path,
                                   'branch': 'worktree-here'})
    restored = store.load_worktree('conv-y')
    assert restored['path'] == tmp_path / 'here'
    assert restored['origin'] == tmp_path
    assert restored['branch'] == 'worktree-here'


class _WorktreeSession:

    worktree = {'name': 'aside', 'path': '/tmp/aside'}

    def __init__(self, changes):
        self._changes = changes
        self.left = None

    def worktree_changes(self):
        return self._changes

    async def leave_worktree(self, keep: bool) -> str:
        self.left = keep
        return 'kept it' if keep else 'removed it'

    async def close(self):
        pass


def _quitting(changes):
    """Run the app, ask it to quit, and report what the session was asked to
    do and what screen is on top."""

    async def scenario():
        from tests.test_tui import _GateTeam
        async with PyClawApp(builder=lambda: _GateTeam()).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            session = _WorktreeSession(changes)
            app._session = session
            await app.action_quit()
            await pilot.pause()
            return session, type(app.screen).__name__

    return asyncio.run(scenario())


def test_leaving_a_worktree_with_work_in_it_asks_first():
    session, screen = _quitting({'files': 2, 'commits': 1})
    assert screen == 'WorktreeExitScreen'
    assert session.left is None


def test_an_untouched_worktree_is_removed_without_asking():
    session, screen = _quitting({'files': 0, 'commits': 0})
    assert screen == 'Screen'
    assert session.left is False


def test_choosing_to_keep_the_worktree_leaves_it_on_disk():
    from pyclaw.tui.screens import WorktreeExitScreen

    async def scenario():
        from tests.test_tui import _GateTeam
        async with PyClawApp(builder=lambda: _GateTeam()).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            session = _WorktreeSession({'files': 1, 'commits': 0})
            app._session = session
            await app.action_quit()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, WorktreeExitScreen)
            await screen.action_choose()
            await pilot.pause()
            return session, app._exit_note, type(app.screen).__name__

    session, note, screen = asyncio.run(scenario())
    assert session.left is True and note == 'kept it'
    assert screen == 'Screen'


def test_backing_out_of_the_question_stays_in_the_worktree():
    async def scenario():
        from tests.test_tui import _GateTeam
        async with PyClawApp(builder=lambda: _GateTeam()).run_test(
                size=(120, 40)) as pilot:
            app = pilot.app
            session = _WorktreeSession({'files': 1, 'commits': 0})
            app._session = session
            await app.action_quit()
            await pilot.pause()
            screen = app.screen
            screen.action_dismiss()
            await pilot.pause()
            return session, type(app.screen).__name__

    session, screen = asyncio.run(scenario())
    assert session.left is None and screen == 'Screen'
