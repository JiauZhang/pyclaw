import asyncio
import itertools
import json
import os

import pytest

from pyclaw import config as config_module
from pyclaw.statusline import (build_payload, clean_output, configured_command,
                               context_percentages, run)


class _Usage:
    prompt_tokens = 1200
    completion_tokens = 300
    total_tokens = 1500
    prompt_tokens_details = {'cached_tokens': 200}


class _StatusSession:

    conv_session_id = 'session-1'
    permission_mode = 'plan'
    model = 'test-model'
    compact_threshold = 200_000
    context_tokens = 40_000
    usage = _Usage()

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


def test_the_gate_only_accepts_a_command_status_line():
    _write_config({'statusLine': {'type': 'http', 'command': 'echo hi'}})
    assert configured_command() == ''
    _use_command('echo hi')
    assert configured_command() == 'echo hi'


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


def test_editing_the_config_file_takes_effect_without_a_restart():
    _write_config({'statusLine': {'type': 'off'}})
    assert configured_command() == ''
    _use_command('echo hi')
    assert configured_command() == 'echo hi'


def test_an_explicitly_disabled_status_line_takes_no_row():
    _write_config({'statusLine': {'type': 'off'}})
    assert configured_command() == ''


def test_an_unset_status_line_falls_back_to_the_default_command():
    from pyclaw.statusline import DEFAULT_COMMAND
    _write_config({})
    assert configured_command() == DEFAULT_COMMAND


def test_an_unset_status_line_is_not_the_users_command():
    """The built-in default is only for whoever runs the command; the app's own
    readout rows take its place when nobody configured a line."""
    from pyclaw.statusline import user_command
    _write_config({})
    assert user_command() == ''
    _write_config({'statusLine': {'type': 'off'}})
    assert user_command() == ''
    _use_command('echo hi')
    assert user_command() == 'echo hi'


def test_the_default_line_names_the_model_and_directory(tmp_path):
    """Room left belongs to the footer's meter, so the default line stays short."""
    _write_config({})
    line = asyncio.run(run(_StatusSession(str(tmp_path))))
    assert 'test-model' in line
    assert str(tmp_path) in line
    assert 'context left' not in line


def test_the_command_reads_its_payload_from_stdin():
    _use_command('cat')
    session = _StatusSession()
    assert json.loads(asyncio.run(run(session))) == build_payload(session)


def test_a_failing_command_blanks_the_line():
    _use_command('echo shown; exit 1')
    assert asyncio.run(run(_StatusSession())) == ''


def test_a_silent_command_blanks_the_line():
    _use_command('true')
    assert asyncio.run(run(_StatusSession())) == ''


def test_a_slow_command_is_cut_off(monkeypatch):
    import pyclaw.statusline as statusline
    _use_command('sleep 5')
    monkeypatch.setattr(statusline, 'STATUS_LINE_TIMEOUT_SECONDS', 0.1)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run(_StatusSession()))


def test_running_without_a_configured_command_starts_nothing():
    _write_config({'statusLine': {'type': 'off'}})
    assert asyncio.run(run(_StatusSession())) == ''
