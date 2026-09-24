"""A scheduled prompt has to fire once per match, in one session of the project."""
import asyncio
from datetime import datetime, timedelta

from chatchat.core import cron_schedule
from chatchat.core.cron_schedule import CronStore, SchedulerLock

from pyclaw.cron import run, tick


CREATED = datetime(2026, 5, 4, 8, 0)


def _setup(tmp_path, monkeypatch, created_at=CREATED):
    monkeypatch.setattr(cron_schedule, '_now', lambda: created_at)
    directory = tmp_path / 'project' / '.pyclaw'
    return CronStore(directory), SchedulerLock(directory, 'session-a')


def _fired(store, lock, deliver, now):
    return asyncio.run(tick(store, lock, deliver, now=now))


def test_a_recurring_prompt_fires_once_and_remembers_when(tmp_path,
                                                           monkeypatch):
    store, lock = _setup(tmp_path, monkeypatch)
    task = store.add('0 * * * *', 'hourly check', durable=True)
    seen = []
    at = datetime(2026, 5, 4, 10, 16)
    fired = _fired(store, lock, lambda task: seen.append(task['prompt']), at)
    assert [item['id'] for item in fired] == [task['id']]
    assert seen == ['hourly check']
    assert _fired(store, lock, lambda task: seen.append(task['prompt']), at + timedelta(minutes=4)) == []
    assert seen == ['hourly check']
    held = store.durable()[0]
    assert held['last_fired_at'] == '2026-05-04T10:16:00'
    following = next(iter(store.durable()))
    assert cron_schedule.next_fire(following,
                                   at + timedelta(minutes=5)) >= datetime(
        2026, 5, 4, 11, 0)


def test_a_one_shot_disappears_after_it_has_fired(tmp_path, monkeypatch):
    store, lock = _setup(tmp_path, monkeypatch)
    store.add('30 15 * * *', 'once only', recurring=False, durable=True)
    seen = []
    _fired(store, lock, lambda task: seen.append(task['prompt']), datetime(2026, 5, 4, 15, 31))
    assert seen == ['once only']
    assert store.durable() == []


def test_a_session_prompt_fires_without_the_project_lock(tmp_path, monkeypatch):
    store, _ = _setup(tmp_path, monkeypatch)
    assert SchedulerLock(store.directory, 'session-b').acquire() is True
    store.add('0 9 * * *', 'mine alone', durable=False)
    seen = []
    fired = _fired(store, SchedulerLock(store.directory, 'session-a'),
                   lambda task: seen.append(task['prompt']), datetime(2026, 5, 4, 9, 16))
    assert seen == ['mine alone']
    assert [item['id'] for item in fired] == [store.session()[0]['id']]


def test_a_durable_prompt_waits_while_another_session_owns_the_lock(tmp_path,
                                                                    monkeypatch):
    store, _ = _setup(tmp_path, monkeypatch)
    added = store.add('0 9 * * *', 'shared work', durable=True)
    assert SchedulerLock(store.directory, 'session-b').acquire() is True
    seen = []
    _fired(store, SchedulerLock(store.directory, 'session-a'), lambda task: seen.append(task['prompt']),
           datetime(2026, 5, 4, 9, 16))
    assert seen == []
    assert store.durable()[0]['id'] == added['id']
    assert 'last_fired_at' not in store.durable()[0]


def test_a_prompt_that_aged_out_is_dropped_rather_than_delivered(tmp_path,
                                                                  monkeypatch):
    store, lock = _setup(tmp_path, monkeypatch,
                        created_at=datetime(2026, 1, 1, 9, 0))
    store.add('0 * * * *', 'stale', durable=True)
    seen = []
    assert _fired(store, lock, lambda task: seen.append(task['prompt']),
                  datetime(2026, 5, 4, 10, 0)) == []
    assert seen == []
    assert store.durable() == []


def test_the_loop_keeps_ticking_until_it_is_cancelled(tmp_path, monkeypatch):
    store, lock = _setup(tmp_path, monkeypatch)
    store.add('*/2 * * * *', 'often', durable=True)
    seen = []

    async def main():
        steps = iter(datetime(2026, 5, 4, 9, minute)
                     for minute in range(2, 20))
        task = asyncio.create_task(run(store, lock, lambda task: seen.append(task['prompt']),
                                       interval=0.005,
                                       clock=lambda: next(steps)))
        await asyncio.sleep(0.25)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(main())
    assert len(seen) >= 2


def test_a_built_team_schedules_into_its_own_project(tmp_path):
    from pyclaw.team_builder import build_team

    async def main():
        return build_team('agnes', 'agnes-2.5-flash', cwd=str(tmp_path))

    team = asyncio.run(main())
    assert team.cron.directory == tmp_path / '.pyclaw'


def test_a_fired_prompt_becomes_a_turn_of_the_conversation(tmp_path):
    from pyclaw.tui import PyClawApp

    from test_tui import _FakeTeam

    async def scenario():
        team = _FakeTeam()
        team.cron = CronStore(tmp_path / '.pyclaw')
        async with PyClawApp(builder=lambda: team).run_test(
                size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            await app._deliver_cron({'prompt': 'the scheduled thing'})
            for _ in range(6):
                await pilot.pause()
            return app._team.transcript()

    transcript = asyncio.run(scenario())
    assert any('the scheduled thing' in str(turn.get('content'))
               for turn in transcript)


def test_the_owning_session_runs_the_durable_prompts(tmp_path):
    from pyclaw.tui import PyClawApp

    from test_tui import _FakeTeam

    async def scenario():
        team = _FakeTeam()
        team.cron = CronStore(tmp_path / '.pyclaw')
        async with PyClawApp(builder=lambda: team).run_test(
                size=(90, 40)) as pilot:
            app = pilot.app
            await pilot.pause()
            store = CronStore(tmp_path / '.pyclaw')
            other = SchedulerLock(tmp_path / '.pyclaw', 'another-session')
            started = app._cron_task
            held = app._cron_lock.held_by()
            other_took_it = other.acquire()
            await app._deliver_cron(store.add('0 9 * * *', 'shared',
                                              durable=True))
            return (started, held['session_id'] == app._cron_lock.session_id,
                    other_took_it, app._cron_lock.acquire())

    started, owned, stolen, reacquired = asyncio.run(scenario())
    assert started is not None
    assert owned
    assert stolen is False
    assert reacquired is True


def test_work_missed_while_the_process_was_down_is_listed(tmp_path):
    import json

    from pyclaw.tui import PyClawApp

    from test_tui import _FakeTeam

    async def scenario():
        team = _FakeTeam()
        store = CronStore(tmp_path / '.pyclaw')
        store.add('0 9 * * *', 'the thing that was missed', durable=True)
        body = json.loads(store.path.read_text(encoding='utf-8'))
        body['tasks'][0]['created_at'] = '2026-05-01T08:00:00'
        store.path.write_text(json.dumps(body), encoding='utf-8')
        team.cron = store
        async with PyClawApp(builder=lambda: team).run_test(
                size=(90, 40)) as pilot:
            app = pilot.app
            for _ in range(4):
                await pilot.pause()
            return app._missed_prompts(datetime(2026, 5, 4, 10, 0))

    missed = asyncio.run(scenario())
    assert [task['prompt'] for task in missed] == [
        'the thing that was missed']


def test_a_missed_prompt_is_said_out_loud_when_the_app_opens(tmp_path):
    import json

    from pyclaw.tui import PyClawApp

    from test_tui import _FakeTeam, _plain

    async def scenario():
        team = _FakeTeam()
        store = CronStore(tmp_path / '.pyclaw')
        store.add('0 9 * * *', 'water the plant', durable=True)
        body = json.loads(store.path.read_text(encoding='utf-8'))
        body['tasks'][0]['created_at'] = '2026-05-01T08:00:00'
        store.path.write_text(json.dumps(body), encoding='utf-8')
        team.cron = store
        async with PyClawApp(builder=lambda: team).run_test(
                size=(90, 40)) as pilot:
            app = pilot.app
            for _ in range(6):
                await pilot.pause()
            return _plain(app)

    text = asyncio.run(scenario())
    assert 'water the plant' in text
    assert 'came due' in text
