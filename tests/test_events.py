"""What happened in a run should be readable afterwards, on this machine only."""
import asyncio
import json
from datetime import datetime

from chatchat.hooks.events import (AGENT_TEXT, AGENT_TOOL_CALL,
                                   AGENT_TURN_FINISHED, RuntimeEvent)

from pyclaw import events as mod


def _home(monkeypatch, tmp_path):
    home = tmp_path / 'events'
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, '_directory', lambda: home)
    return home


def _rows(home):
    return [json.loads(line)
            for path in sorted(home.glob('*.jsonl'))
            for line in path.read_text(encoding='utf-8').splitlines()]


def test_an_opened_stream_writes_one_line_per_event(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1')
    sink(RuntimeEvent(AGENT_TEXT, agent='lead', data={'delta': 'hi'}))
    sink(RuntimeEvent(AGENT_TOOL_CALL, agent='lead',
                      data={'tool': 'Bash', 'input': {'command': 'ls'},
                            'tool_use_id': 't1'}))
    sink.close()
    rows = _rows(home)
    assert [row['at'] for row in rows] == ['2026-05-04T09:00:00',
                                           '2026-05-04T09:00:00']
    assert [row['kind'] for row in rows] == ['text', 'tool_call']
    assert rows[1]['tool'] == 'Bash' and rows[1]['command'] == 'ls'
    assert rows[0]['text'] == 'hi'


def test_the_file_is_named_for_the_day_the_event_happened(monkeypatch,
                                                          tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 23, 59), session='s1')
    sink(RuntimeEvent(AGENT_TEXT, agent='lead', data={'delta': 'x'}))
    assert [path.name for path in home.glob('*.jsonl')] == ['2026-05-04.jsonl']


def test_a_stream_stops_writing_once_it_is_closed(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1')
    sink(RuntimeEvent(AGENT_TURN_FINISHED, agent='lead', data={}))
    sink.close()
    sink(RuntimeEvent(AGENT_TURN_FINISHED, agent='lead', data={}))
    assert len(_rows(home)) == 1


def test_an_unfamiliar_event_is_dropped_rather_than_raising(monkeypatch,
                                                             tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1')
    sink('not-an-event')
    sink(RuntimeEvent('something.new', agent='lead', data={'delta': 'x'}))
    sink.close()
    assert [row['kind'] for row in _rows(home)] == ['something.new']


def test_the_facts_of_a_turn_are_kept_from_the_events(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1')
    for event in (RuntimeEvent(AGENT_TEXT, agent='lead', data={'delta': 'hi '}),
                  RuntimeEvent(AGENT_TEXT, agent='lead',
                               data={'delta': 'there'}),
                  RuntimeEvent(AGENT_TOOL_CALL, agent='lead',
                               data={'tool': 'Read',
                                     'input': {'file_path': 'a.py'},
                                     'tool_use_id': 't1'}),
                  RuntimeEvent(AGENT_TURN_FINISHED, agent='lead', data={})):
        sink(event)
    assert sink.turn_facts() == {'chars': 8, 'tool_calls': 1,
                                 'tools': ['Read'], 'turns': 1}
    sink.close()


def test_the_stream_records_an_error_as_its_own_kind(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1')
    sink({'kind': 'error', 'agent': 'lead', 'text': 'the provider went away'})
    sink.close()
    row = _rows(home)[0]
    assert row['kind'] == 'error' and row['text'] == 'the provider went away'


def test_the_recorded_errors_can_be_counted_back(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1')
    sink.note_error('the provider went away')
    sink.note_error('a tool blew up', agent='worker')
    sink.close()
    later = mod.open_stream(at=datetime(2026, 5, 6, 9, 0), session='s2')
    later.note_error('another one')
    later.close()
    assert mod.error_rows(home, until=datetime(2026, 5, 10).date(),
                          days=7) == [
        {'at': '2026-05-04T09:00:00', 'session': 's1', 'kind': 'error',
         'agent': '', 'text': 'the provider went away'},
        {'at': '2026-05-04T09:00:00', 'session': 's1', 'kind': 'error',
         'agent': 'worker', 'text': 'a tool blew up'},
        {'at': '2026-05-06T09:00:00', 'session': 's2', 'kind': 'error',
         'agent': '', 'text': 'another one'}]


def test_a_window_that_misses_the_files_finds_no_errors(monkeypatch, tmp_path):
    home = _home(monkeypatch, tmp_path)
    sink = mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1')
    sink.note_error('old')
    sink.close()
    assert mod.error_rows(home, until=datetime(2026, 5, 30).date(), days=3) == []


def test_opening_a_stream_throws_away_the_days_it_no_longer_needs(monkeypatch,
                                                                  tmp_path):
    home = _home(monkeypatch, tmp_path)
    stale = home / '2026-04-01.jsonl'
    stale.write_text(json.dumps({'kind': 'text'}) + '\n', encoding='utf-8')
    kept = home / '2026-05-01.jsonl'
    kept.write_text(json.dumps({'kind': 'text'}) + '\n', encoding='utf-8')
    mod.open_stream(at=datetime(2026, 5, 4, 9, 0), session='s1').close()
    assert kept.exists()
    assert not stale.exists()
