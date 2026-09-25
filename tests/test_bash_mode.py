import asyncio

from chatchat.core.thinking import Thinking
from chatchat.tool import ToolContext

from pyclaw.agents import Session


class _Lead:
    def __init__(self):
        self.messages = []


class _Team:
    provider = 'p'
    model = 'm'
    name = 'bash-session'
    thinking = Thinking('off')
    provided_tools = []
    _pyclaw_mode = 'agent'

    def __init__(self, cwd):
        self.lead = _Lead()
        self._pyclaw_gate = ToolContext(cwd=cwd)


def _session(tmp_path):
    return Session(_Team(tmp_path), session_id='bash-mode')


def test_a_bash_command_is_recorded_as_the_pair_the_model_reads(tmp_path):
    session = _session(tmp_path)
    result = asyncio.run(session.run_bash('echo hello'))

    assert result['stdout'].strip() == 'hello'
    assert result['exit_code'] == 0
    messages = session._team.lead.messages
    assert messages[-2]['content'] == '<bash-input>echo hello</bash-input>'
    assert 'hello' in messages[-1]['content']
    assert messages[-1]['content'].startswith('<bash-stdout>')
    assert '<bash-stderr></bash-stderr>' in messages[-1]['content']


def test_stderr_comes_back_separately(tmp_path):
    session = _session(tmp_path)
    result = asyncio.run(session.run_bash('echo oops >&2; exit 3'))

    assert result['stderr'].strip() == 'oops'
    assert result['stdout'] == ''
    assert result['exit_code'] == 3


def test_the_command_runs_in_the_session_directory(tmp_path):
    session = _session(tmp_path)
    result = asyncio.run(session.run_bash('pwd'))

    assert result['stdout'].strip() == str(tmp_path)


def test_a_command_that_cannot_run_reports_why(tmp_path, monkeypatch):
    import subprocess

    session = _session(tmp_path)

    def boom(*args, **kw):
        raise OSError('no shell here')

    monkeypatch.setattr(subprocess, 'run', boom)
    result = asyncio.run(session.run_bash('echo hi'))

    assert result['exit_code'] is None
    assert 'no shell here' in result['stderr']
