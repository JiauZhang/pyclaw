import asyncio
import json

from chatchat.client import MockClient
from chatchat.team import Team

from pyclaw.agents import Session


def test_session_persists_subagent_sidechains_under_session_log(tmp_path, monkeypatch):
    monkeypatch.setenv("PYCLAW_HOME", str(tmp_path))

    async def sub_respond(messages, tools=None, *, stream_cb=None):
        return 'sub done'

    async def main():
        team = Team('sc', client_factory=lambda inst, model=None: MockClient(handler=sub_respond))
        Session(team, session_id='sid-1')
        return await team.spawn_subagent('do it', subagent_type='general-purpose')

    out = asyncio.run(main())
    assert out == 'sub done'

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

    async def sub_respond(messages, tools=None, *, stream_cb=None):
        return 'sub done'

    async def main():
        team = Team('np', client_factory=lambda inst, model=None: MockClient(handler=sub_respond))
        Session(team)
        await team.spawn_subagent('do it', subagent_type='general-purpose')

    asyncio.run(main())
    assert not (tmp_path / 'logs').exists() or \
        list((tmp_path / 'logs').iterdir()) == []
