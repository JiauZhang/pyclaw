import asyncio
import json

from chatchat.client import MockClient
from chatchat.team import Team

from pyclaw import pyclaw_home
from pyclaw.agents import Session
from pyclaw.session_store import _logs_dir


def test_pyclaw_home_expands_a_leading_tilde(tmp_path, monkeypatch):
    """PYCLAW_HOME is a user-typed setting, so `~` has to mean the real home
    before it is handed to chatchat or used to build the logs path."""
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('PYCLAW_HOME', '~/.pyclaw')
    assert pyclaw_home() == tmp_path / '.pyclaw'
    assert _logs_dir() == tmp_path / '.pyclaw' / 'logs'


def _spawn_subagent(team_name, session_id=None):
    async def sub_respond(messages, tools=None, *, stream_cb=None):
        return 'sub done'

    async def main():
        team = Team(team_name, client_factory=lambda inst, model=None:
                    MockClient(handler=sub_respond,
                               model=model))
        Session(team, session_id=session_id)
        return await team.spawn_subagent('do it', subagent_type='general-purpose')

    return asyncio.run(main())


def test_session_persists_subagent_sidechains_under_session_log(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCLAW_HOME", str(tmp_path))
    assert _spawn_subagent('sc', 'sid-1') == 'sub done'

    side_dir = tmp_path / 'logs' / 'sid-1' / 'subagents'
    files = list(side_dir.glob('agent-*.jsonl'))
    assert len(files) == 1
    records = [json.loads(line) for line in
               files[0].read_text(encoding='utf-8').splitlines() if line.strip()]
    assert records and all(r['isSidechain'] is True for r in records)
    meta = json.loads((side_dir / files[0].name.replace('.jsonl', '.meta.json'))
                      .read_text(encoding='utf-8'))
    assert meta['status'] == 'completed'


def test_sessions_without_persistence_do_not_write_sidechains(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCLAW_HOME", str(tmp_path))
    _spawn_subagent('np')
    assert not (tmp_path / 'logs').exists() or \
        list((tmp_path / 'logs').iterdir()) == []
