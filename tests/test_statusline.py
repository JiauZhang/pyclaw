import asyncio
import itertools
import json
import logging
import os

import pytest

from fakes import Usage
from pyclaw import config as config_module
from pyclaw.statusline import (build_payload, clean_output, user_padding,
                               context_percentages, run, user_command)
from pyclaw.team.defs import STATUSLINE_SYSTEM_PROMPT


class _StatusSession:

    conv_session_id = 'session-1'
    permission_mode = 'plan'
    model = 'test-model'
    context_window = 200_000
    last_usage = Usage(40_000, 500, 40_500, 8_000)
    usage = Usage(1200, 300, 1500, 200)

    title = ''
    worktree = None
    elapsed_seconds = 90
    working_dirs = []

    def __init__(self, cwd=None):
        self.cwd = cwd or os.getcwd()

    def metrics(self) -> dict:
        return {'api_ms': 4200, 'requests': 3, 'lines_added': 12,
                'lines_removed': 3}


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
    """`current_usage` is what the last response reported and the percentage is
    its input side over the model's window; output does not occupy the window."""
    window = build_payload(_StatusSession())['context_window']
    assert window['context_window_size'] == 200_000
    assert window['current_usage'] == {'input_tokens': 40_000,
                                       'output_tokens': 500,
                                       'cached_tokens': 8_000}
    assert window['used_percentage'] == 20
    assert window['remaining_percentage'] == 80


def test_the_payload_reports_no_context_before_the_first_response():
    class _Fresh(_StatusSession):
        last_usage = None

    window = build_payload(_Fresh())['context_window']
    assert window['current_usage'] is None
    assert window['used_percentage'] is None
    assert window['remaining_percentage'] is None


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


def test_a_broken_status_line_command_leaves_a_trace_in_the_log(caplog):
    _use_command('echo oops >&2; exit 3')
    with caplog.at_level(logging.WARNING, logger='pyclaw.statusline'):
        assert asyncio.run(run(_StatusSession(), user_command())) == ''
    assert 'exited 3' in caplog.text and 'oops' in caplog.text


def test_a_slow_command_is_cut_off(monkeypatch):
    import pyclaw.statusline as statusline
    _use_command('sleep 5')
    monkeypatch.setattr(statusline, 'STATUS_LINE_TIMEOUT_SECONDS', 0.1)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run(_StatusSession(), user_command()))


def test_running_without_a_command_starts_nothing():
    assert asyncio.run(run(_StatusSession(), user_command())) == ''


def test_the_payload_carries_cost_and_the_two_context_fields():
    payload = build_payload(_StatusSession())
    assert payload['cost']['total_duration_ms'] == 90_000
    assert payload['cost']['total_api_duration_ms'] == 4200
    assert payload['cost']['total_lines_added'] == 12
    assert payload['cost']['total_lines_removed'] == 3
    assert payload['exceeds_200k_tokens'] is False
    assert 'session_name' not in payload
    assert 'worktree' not in payload

    class _Huge(_StatusSession):
        context_window = 400_000
        title = 'git ssh key'
        worktree = {'name': 'w1', 'path': '/tmp/w1', 'branch': 'worktree-w1',
                    'origin': '/repo', 'origin_branch': 'main'}

    big = build_payload(_Huge())
    assert big['exceeds_200k_tokens'] is True
    assert big['session_name'] == 'git ssh key'
    assert big['worktree'] == {'name': 'w1', 'path': '/tmp/w1',
                               'branch': 'worktree-w1', 'original_cwd': '/repo',
                               'original_branch': 'main'}


def test_the_setup_agent_documents_every_field_it_will_receive():
    class _Everything(_StatusSession):
        title = 'git ssh key'
        worktree = {'name': 'w1', 'path': '/tmp/w1', 'branch': 'worktree-w1',
                     'origin': '/repo', 'origin_branch': 'main'}

    def keys(value):
        if isinstance(value, dict):
            for key, inner in value.items():
                yield key
                yield from keys(inner)

    payload = build_payload(_Everything())
    missing = [key for key in sorted(set(keys(payload)))
               if f'"{key}"' not in STATUSLINE_SYSTEM_PROMPT]
    assert missing == []


def test_the_padding_comes_from_the_same_settings_entry():
    _write_config({'statusLine': {'type': 'command', 'command': 'echo x',
                                 'padding': 3}})
    assert user_padding() == 3
    _write_config({'statusLine': {'type': 'command', 'command': 'echo x'}})
    assert user_padding() == 0
    _write_config({'statusLine': {'type': 'command', 'command': 'echo x',
                                 'padding': 'wide'}})
    assert user_padding() == 0


def test_the_payload_lists_the_directories_the_session_reaches_into():
    class _Two(_StatusSession):
        working_dirs = ['/work/project', '/shared/lib']

    session = _Two('/work/project')
    payload = build_payload(session)
    assert payload['workspace']['project_dir'] == '/work/project'
    assert payload['workspace']['added_dirs'] == ['/shared/lib']
    assert build_payload(_StatusSession())['workspace']['added_dirs'] == []
