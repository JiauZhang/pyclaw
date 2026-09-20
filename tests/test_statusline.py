import asyncio
import itertools
import json
import os

import pytest

from fakes import Usage
from pyclaw import config as config_module
from pyclaw.statusline import (build_payload, clean_output,
                               context_percentages, run, user_command)


class _StatusSession:

    conv_session_id = 'session-1'
    permission_mode = 'plan'
    model = 'test-model'
    compact_threshold = 200_000
    context_tokens = 40_000
    usage = Usage(1200, 300, 1500, 200)

    def __init__(self, cwd=None):
        self.cwd = cwd or os.getcwd()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    from pyclaw import statusline
    monkeypatch.setenv('PYCLAW_HOME', str(tmp_path))
    monkeypatch.setattr(config_module, '__config_file__',
                        tmp_path / 'config.json')
    monkeypatch.setattr(statusline, '_CONFIG_MTIME', None)


_TICK = itertools.count(1)


def _write_config(data: dict):
    path = config_module.__config_file__
    path.write_text(json.dumps(data), encoding='utf-8')
    stamp = path.stat().st_mtime + next(_TICK)
    os.utime(path, (stamp, stamp))


def _use_command(command):
    _write_config({'statusLine': {'type': 'command', 'command': command}})


def test_only_a_command_status_line_is_accepted():
    for cfg in ({}, {'statusLine': {'type': 'off'}},
                {'statusLine': {'type': 'http', 'command': 'echo hi'}}):
        _write_config(cfg)
        assert user_command() == ''
    _use_command('echo hi')
    assert user_command() == 'echo hi'


def test_editing_the_config_file_takes_effect_without_a_restart():
    assert user_command() == ''
    _use_command('echo hi')
    assert user_command() == 'echo hi'


def test_the_payload_names_the_session_model_and_workspace():
    payload = build_payload(_StatusSession('/work/project'))
    assert payload['session_id'] == 'session-1'
    assert payload['permission_mode'] == 'plan'
    assert payload['model'] == {'id': 'test-model',
                                'display_name': 'test-model'}
    assert payload['workspace']['project_dir'] == '/work/project'
    assert payload['workspace']['current_dir'] == os.getcwd()
    assert payload['transcript_path'].endswith(
        os.path.join('session-1', 'transcript.jsonl'))
    assert payload['usage'] == {'prompt_tokens': 1200,
                                'completion_tokens': 300,
                                'total_tokens': 1500,
                                'cached_tokens': 200}


def test_the_payload_carries_pre_calculated_context_percentages():
    window = build_payload(_StatusSession())['context_window']
    assert window['context_window_size'] == 200_000
    assert window['current_usage'] == {'input_tokens': 40_000}
    assert window['used_percentage'] == 20
    assert window['remaining_percentage'] == 80


def test_context_percentages_stay_within_a_hundred():
    assert context_percentages(500, 100) == (100, 0)
    assert context_percentages(0, 100) == (0, 100)


def test_context_percentages_without_a_context_budget():
    assert context_percentages(1000, 0) == (0, 100)


def test_blank_lines_are_dropped_and_edges_trimmed():
    assert clean_output('  one\n\n  two  \n\n') == 'one\ntwo'


def test_the_command_reads_its_payload_from_stdin():
    _use_command('cat')
    session = _StatusSession()
    assert json.loads(asyncio.run(run(session, user_command()))) == \
        build_payload(session)


def test_a_command_that_prints_nothing_usable_blanks_the_line():
    for command in ('echo shown; exit 1', 'true'):
        _use_command(command)
        assert asyncio.run(run(_StatusSession(), user_command())) == ''


def test_a_slow_command_is_cut_off(monkeypatch):
    import pyclaw.statusline as statusline
    _use_command('sleep 5')
    monkeypatch.setattr(statusline, 'STATUS_LINE_TIMEOUT_SECONDS', 0.1)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run(_StatusSession(), user_command()))


def test_running_without_a_command_starts_nothing():
    assert asyncio.run(run(_StatusSession(), user_command())) == ''
