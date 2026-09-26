"""Yesterday's work has to be readable tomorrow, in the numbers the API gave."""
import json
from datetime import date, datetime, timedelta

from chatchat.team.team import Team

from pyclaw import usage_history as mod


def _home(monkeypatch, tmp_path):
    home = tmp_path / 'usage'
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, '_directory', lambda: home)
    return home


ROW = {'at': '2026-05-04T09:00:00', 'day': '2026-05-04', 'session': 's1',
       'provider': 'agnes', 'model': 'agnes-2.5-flash',
       'input': 1000, 'output': 200, 'cached': 600, 'cache_write': 0,
       'turns': 1, 'metrics': {'tool_calls': 3, 'tool_ms': 400,
                               'lines_added': 12, 'lines_removed': 4,
                               'api_ms': 2500, 'requests': 1, 'hooks': 2,
                               'hook_ms': 60, 'denials': 1}}


def test_a_record_lands_in_the_file_for_its_own_day(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    first = mod.record({**ROW, 'at': '2026-05-04T09:00:00'})
    second = mod.record({**ROW, 'at': '2026-05-05T23:59:00'})
    assert first.name == '2026-05-04.jsonl'
    assert second.name == '2026-05-05.jsonl'
    assert len(first.read_text(encoding='utf-8').splitlines()) == 1


def test_a_window_reads_back_only_the_days_it_covers(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    for index in range(10):
        day = date(2026, 5, 1) + timedelta(days=index)
        (home / f'{day}.jsonl').write_text(
            json.dumps({**ROW, 'day': str(day),
                        'at': f'{day.isoformat()}T09:00:00'}) + '\n',
            encoding='utf-8')
    rows = mod.read_days(3, until=date(2026, 5, 10))
    assert sorted(row['day'] for row in rows) == ['2026-05-08', '2026-05-09',
                                                 '2026-05-10']


def test_a_half_written_line_is_skipped_rather_than_raising(monkeypatch,
                                                            tmp_path):
    home = _home(monkeypatch, tmp_path)
    (home / '2026-05-04.jsonl').write_text(
        json.dumps(ROW) + '\n' + '{"at": "2026-05', encoding='utf-8')
    assert mod.read_days(1, until=date(2026, 5, 4)) == [ROW]


def test_a_window_adds_up_to_one_line_per_number(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    mod.record(ROW)
    mod.record({**ROW, 'at': '2026-05-04T10:00:00',
                'input': 500, 'output': 50,
                'metrics': {**ROW['metrics'], 'tool_calls': 2,
                            'lines_added': 1}})
    totals = mod.totals(mod.read_days(1, until=date(2026, 5, 4)))
    assert totals['input'] == 1500
    assert totals['output'] == 250
    assert totals['records'] == 2
    assert totals['tool_calls'] == 5
    assert totals['lines_added'] == 13
    assert totals['api_ms'] == 5000
    assert totals['denials'] == 2


def test_the_days_are_listed_newest_first(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    for day in ('2026-05-03', '2026-05-04'):
        (home / f'{day}.jsonl').write_text(
            json.dumps({**ROW, 'day': day}) + '\n', encoding='utf-8')
    rows = mod.read_days(7, until=date(2026, 5, 4))
    days = [day for day, _ in mod.by_day(rows)]
    assert days == ['2026-05-04', '2026-05-03']


def test_cost_of_a_recorded_row_uses_the_configured_rates():
    from pyclaw.cost import usage_cost

    pricing = {'agnes-2.5-flash': {'input': 0.01, 'output': 0.02}}
    cost = usage_cost(ROW['model'], ROW, pricing)

    assert cost == (400 * 0.01 + 200 * 0.02) / 1_000_000


def _history(tmp_path, monkeypatch):
    from chatchat.client import MockClient

    from pyclaw.session import Session

    usage = {'prompt_tokens': 1000, 'completion_tokens': 100,
             'prompt_tokens_details': {'cached_tokens': 400}}

    async def respond(messages, tools=None, *, stream_cb=None):
        return 'ok'

    home = tmp_path / 'usage'
    home.mkdir(exist_ok=True)
    monkeypatch.setattr(mod, '_directory', lambda: home)

    async def build():
        team = Team('hist', provider='agnes', model='agnes-2.5-flash',
                    client_factory=lambda inst, model=None: MockClient(
                        handler=respond, usage=usage))
        return Session(team, session_id='hist'), home


    return build


def test_a_recorded_turn_carries_only_what_this_turn_used(tmp_path,
                                                          monkeypatch):
    import asyncio

    async def main():
        session, home = await _history(tmp_path, monkeypatch)()
        before = session.record_turn()
        assert (before['input'], before['output']) == (0, 0)
        assert before['provider'] == 'agnes' and before['session'] == 'hist'
        await session.chat('go on')
        second = session.record_turn()
        assert (second['input'], second['output'], second['cached']) == (
            1000, 100, 400)
        lines = (home / f"{second['day']}.jsonl").read_text().splitlines()
        assert len(lines) == 2
        await session.close()

    asyncio.run(main())


def test_resetting_the_session_starts_the_history_at_zero_again(tmp_path,
                                                                monkeypatch):
    import asyncio

    async def main():
        session, home = await _history(tmp_path, monkeypatch)()
        await session.chat('go on')
        assert session.record_turn()['input'] == 1000
        session.reset()
        assert session.record_turn()['input'] == 0
        lines = sum(len(path.read_text(encoding='utf-8').splitlines())
                    for path in home.glob('*.jsonl'))
        assert lines == 2
        await session.close()

    asyncio.run(main())


def test_the_totals_of_one_conversation_are_read_back(monkeypatch, tmp_path):
    _home(monkeypatch, tmp_path)
    today = date.today().isoformat()
    mod.record({**ROW, 'at': f'{today}T09:00:00', 'day': today,
                'session': 'mine'})
    mod.record({**ROW, 'at': f'{today}T09:05:00', 'day': today,
                'session': 'mine'})
    mod.record({**ROW, 'at': f'{today}T09:10:00', 'day': today,
                'session': 'someone-else'})

    totals = mod.conversation_totals('mine', today=date.fromisoformat(today))
    assert totals['input'] == 2000 and totals['output'] == 400
    assert totals['cached'] == 1200 and totals['turns'] == 2
    assert totals['metrics']['tool_calls'] == 6
    assert mod.conversation_totals(
        'nobody', today=date.fromisoformat(today))['input'] == 0


def test_a_resumed_conversation_keeps_the_numbers_it_had(tmp_path, monkeypatch):
    import asyncio

    from chatchat.client import MockClient
    from pyclaw import slash, usage_history, session as session_mod
    from pyclaw.session import store as session_store

    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)
    _home(monkeypatch, tmp_path)
    today = date.today().isoformat()

    async def answer(messages, tools=None, *, stream_cb=None):
        return 'ok'

    def build(session_id):
        return session_mod.Session(
            Team('t1', client_factory=lambda inst, model=None:
                 MockClient(handler=answer, model=model)),
            session_id=session_id)

    async def main():
        first = build('conv-one')
        session_store.save_transcript('conv-one', [
            {'role': 'user', 'content': 'do it'},
            {'role': 'assistant', 'content': [{'type': 'text', 'text': 'done'}],
             'usage': {'prompt_tokens': 1800, 'completion_tokens': 200,
                       'total_tokens': 2000,
                       'prompt_tokens_details': {'cached_tokens': 600}}}])
        usage_history.record({
            **ROW, 'at': f'{today}T09:00:00', 'day': today,
            'session': 'conv-one', 'input': 1800, 'output': 200,
            'total': 2000, 'cached': 600, 'turns': 1})
        second = build('conv-two')
        assert second.resume_session('conv-one') == 2
        cost = await slash.handle_slash('/cost', second)
        status = await slash.handle_slash('/status', second)
        return second, cost, status

    session, cost, status = asyncio.run(main())
    assert session.usage.prompt_tokens == 1800
    assert 'Tokens: 1800 in / 200 out' in cost
    assert 'Usage: 1800 in / 200 out / 2000 total' in status


def test_a_resumed_conversation_picks_up_the_clock_where_it_stopped(
        tmp_path, monkeypatch):
    """Wall seconds are stored per turn, so what the status row shows after a
    resume is the time really spent on the conversation, not a guess."""
    import asyncio

    from chatchat.client import MockClient
    from pyclaw import usage_history
    from pyclaw.session import Session, store as session_store

    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)
    _home(monkeypatch, tmp_path)
    today = date.today().isoformat()

    async def answer(messages, tools=None, *, stream_cb=None):
        return 'ok'

    def build(session_id):
        return Session(Team('t1', client_factory=lambda inst, model=None:
                           MockClient(handler=answer, model=model)),
                       session_id=session_id)

    async def main():
        session = build('timed')
        session._started -= 120
        session_store.save_transcript('timed', [{'role': 'user',
                                                 'content': 'go'}])
        session.record_turn()
        again = build('later')
        again.resume_session('timed')
        return again

    resumed = asyncio.run(main())
    assert resumed.carried_seconds == 120
