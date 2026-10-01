from pyclaw.gateway.im import _friendly_channel_error


def test_a_channel_failure_is_reworded_for_the_user():
    cases = (
        (TimeoutError("Request timed out"), "timed out"),
        (RuntimeError("InternalServerError: 500"), "unavailable"),
        (RuntimeError("got 500"), "unavailable"),
        (RuntimeError("rate limit exceeded"), "Too many requests"))
    for error, phrase in cases:
        assert phrase in _friendly_channel_error(error)


def test_an_unknown_failure_is_reported_briefly():
    out = _friendly_channel_error(RuntimeError("x" * 500))
    assert out.startswith("An error occurred:")
    assert len(out) <= len("An error occurred: ") + 200


def test_the_gateway_builds_a_team_when_told_to(monkeypatch):
    import pytest

    from pyclaw.gateway import server as gateway_server
    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    captured = {}

    class _Probed(Exception):
        pass

    def probe(**kw):
        captured.update(kw)
        raise _Probed()

    monkeypatch.setattr(gateway_server, 'build_team', probe)
    server = GatewayServer(GatewayConfig(use_team=True))
    with pytest.raises(_Probed):
        server._new_session('c1')
    assert captured['use_team'] is True


def test_conversation_ids_from_the_outside_are_sanitised():
    from pyclaw.gateway.server import _clean_conversation_id

    good = 'a' * 32
    assert _clean_conversation_id(good) == good
    assert _clean_conversation_id(good.upper()) == good
    assert _clean_conversation_id('../../etc') == ''
    assert _clean_conversation_id('') == ''
    assert _clean_conversation_id(None) == ''
    assert _clean_conversation_id('x' * 10) == ''
    assert _clean_conversation_id('a' * 32 + '; rm -rf') == ''


def test_a_returning_conversation_is_reused_and_restored(monkeypatch):
    import asyncio

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    server = GatewayServer(GatewayConfig())
    created = []

    class StubSession:
        name = 'agent1'
        restored = False

        def restore_transcript(self):
            self.restored = True

    def fake_new(key, provider=None, model=None):
        stub = StubSession()
        created.append(stub)
        return stub

    monkeypatch.setattr(server, '_new_session', fake_new)
    monkeypatch.setattr(server.runtime, 'get_or_create_session',
                        lambda *a, **k: None)

    first = asyncio.run(server._get_session('conv1'))
    second = asyncio.run(server._get_session('conv1'))

    assert first is second
    assert len(created) == 1
    assert created[0].restored


def test_serve_overrides_apply_to_the_run_without_touching_the_saved_config():
    import argparse

    from pyclaw.__main__ import _apply_overrides

    saved = {"provider": "agnes", "model": "m1",
             "enabled_channels": ["wechat"]}
    args = argparse.Namespace(provider="p", model="m", channels=["web"])

    settings = _apply_overrides(dict(saved), args)

    assert settings["provider"] == "p"
    assert settings["model"] == "m"
    assert settings["enabled_channels"] == ["web"]
    assert saved == {"provider": "agnes", "model": "m1",
                     "enabled_channels": ["wechat"]}


def test_commands_payload_survives_real_skill_objects():
    import asyncio

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    class Skill:
        name = 'mypriv'
        description = 'A private skill'
        argument_hint = '[key]'

    class StubSession:
        def user_skills(self):
            return [Skill()]

    server = GatewayServer(GatewayConfig())
    server._sessions['conv1'] = StubSession()

    payload = asyncio.run(server.commands_payload('my'))

    assert payload["commands"][0]["name"] == "mypriv"
    every = asyncio.run(server.commands_payload(''))
    assert any(row["name"] == "mypriv" for row in every["commands"])


def test_a_status_snapshot_carries_the_tui_hud_numbers(tmp_path, monkeypatch):
    import asyncio

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    monkeypatch.chdir(tmp_path)

    class _Usage:
        prompt_tokens = 51000
        completion_tokens = 3000
        total_tokens = 54000
        prompt_tokens_details = {'cached_tokens': 17000}

    class StubSession:
        model = 'agnes-3.0-flash'
        provider = 'agnes'
        usage = _Usage()
        context_window = 128000
        used_context = 54000
        compact_threshold = 90000
        auto_compact = True

        def __init__(self):
            self._gate = None

        @property
        def permission_mode(self):
            return 'default'

        def transcript(self):
            return [1, 2, 3]

        @property
        def elapsed_seconds(self):
            return 95

        @property
        def cwd(self):
            return str(tmp_path)

    server = GatewayServer(GatewayConfig())
    server._sessions['a' * 32] = StubSession()

    status = asyncio.run(server.session_status('a' * 32))

    assert status['model'] == 'agnes-3.0-flash'
    assert status['idle'] is False
    assert status['context']['percent'] == 42
    assert status['usage']['cached'] == 17000
    assert status['messages'] == 3
    assert status['elapsed'] == 95
    assert status['cwd'] == _display_home(str(tmp_path))


def test_an_idle_conversation_still_gets_a_default_snapshot():
    import asyncio

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    server = GatewayServer(GatewayConfig(model='m1'))

    status = asyncio.run(server.session_status('a' * 32))

    assert status['idle'] is True
    assert status['model'] == 'm1'
    assert status['usage'] == {'input': 0, 'output': 0, 'cached': 0,
                               'total': 0}


def test_deleting_a_conversation_removes_artifacts_plan_and_snapshots(
        tmp_path, monkeypatch):
    import asyncio

    from pyclaw.gateway.server import GatewayConfig, GatewayServer
    from pyclaw.session import store as session_store

    monkeypatch.setenv("PYCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)

    session_store.append_conv('a' * 32, 'user', 'hi')
    session_store.record_meta('a' * 32, {'title': 'mine'})
    session_store.plan_file('a' * 32).parent.mkdir(parents=True, exist_ok=True)
    session_store.plan_file('a' * 32).write_text('# plan\n')
    history = session_store.history_dir('a' * 32)
    history.mkdir(parents=True)
    (history / 'snap').write_text('x')

    server = GatewayServer(GatewayConfig())
    out = asyncio.run(server.delete_conversation('a' * 32))

    assert out['deleted'] is True
    assert not (tmp_path / ('a' * 32)).exists()
    assert not session_store.plan_file('a' * 32).exists()
    assert not history.exists()
    again = asyncio.run(server.delete_conversation('a' * 32))
    assert again['deleted'] is False


def _display_home(path):
    from pathlib import Path

    home = str(Path.home())
    if path == home:
        return '~'
    if path.startswith(home + '/'):
        return '~/' + path[len(home) + 1:]
    return path


def test_a_conversation_can_be_renamed_from_the_list(tmp_path, monkeypatch):
    import pytest

    from pyclaw.gateway.server import GatewayConfig, GatewayServer
    from pyclaw.session import store as session_store

    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)

    server = GatewayServer(GatewayConfig())
    server.rename_conversation('a' * 32, 'my talk')

    assert session_store.title_of('a' * 32) == 'my talk'
    with pytest.raises(Exception):
        server.rename_conversation('../../etc', 'nope')


def test_a_conversation_can_be_branched_from_the_list(tmp_path, monkeypatch):
    import pytest

    from pyclaw.gateway.server import GatewayConfig, GatewayServer
    from pyclaw.session import store as session_store

    monkeypatch.setenv("PYCLAW_HOME", str(tmp_path))
    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)

    session_store.append_conv('a' * 32, 'user', 'hi')
    session_store.save_transcript('a' * 32, [{'role': 'user',
                                              'content': 'hi'}])

    server = GatewayServer(GatewayConfig())
    out = server.branch_conversation('a' * 32)

    assert out['forked_from'] == 'a' * 32
    assert session_store.load_entries(out['id'])
    with pytest.raises(Exception):
        server.branch_conversation('b' * 32)


def test_im_progress_carries_the_tool_summary():
    from chatchat.hooks.events import AGENT_TOOL_CALL, AGENT_REASON_START, \
        RuntimeEvent

    from pyclaw.gateway.im import _im_progress_text

    lead = RuntimeEvent(AGENT_TOOL_CALL, agent='lead', team='t',
                        data={'tool': 'Bash', 'input': {'command': 'ls -la'}})
    teammate = RuntimeEvent(AGENT_TOOL_CALL, agent='researcher', team='t',
                            data={'tool': 'Read',
                                  'input': {'file_path': 'a/b.py'}})
    thinking = RuntimeEvent(AGENT_REASON_START, agent='lead', team='t', data={})

    assert _im_progress_text(lead) == '🔧 Bash ls -la'
    assert _im_progress_text(teammate) == '🔧 researcher Read a/b.py'
    assert _im_progress_text(thinking) == '🔄 思考中…'


def test_conversation_messages_reads_the_display_log(tmp_path, monkeypatch):
    from pyclaw.session import store as session_store

    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)

    session_store.append_conv('conv1', 'user', 'hi')
    session_store.append_conv('conv1', 'assistant', 'hello')

    messages = session_store.conversation_messages('conv1')

    assert [m['role'] for m in messages] == ['user', 'assistant']
    assert messages[0]['content'] == 'hi'
    assert session_store.conversation_messages('missing') == []


def test_an_untitled_conversation_previews_its_first_prompt(tmp_path,
                                                            monkeypatch):
    from pyclaw.session import store as session_store

    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)

    session_store.append_conv('conv1', 'assistant', 'earlier')
    session_store.append_conv('conv1', 'user', '  fix the  login bug\nplease  ')

    preview = session_store.conversation_preview('conv1')

    assert preview == 'fix the login bug please'
    assert session_store.conversation_preview('missing') == ''


def test_the_webchat_mode_endpoint_sets_the_permission_mode(monkeypatch):
    from fastapi.testclient import TestClient

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    class _FakeSession:
        def __init__(self):
            self.modes = []

        def set_permission_mode(self, mode):
            if mode == 'bogus':
                raise ValueError('Unknown permission mode: bogus.')
            self.modes.append(mode)
            return mode

    server = GatewayServer(GatewayConfig(enabled_channels=['web']))
    fake = _FakeSession()

    async def fake_get_session(session_key, *args, **kwargs):
        return fake

    monkeypatch.setattr(server, '_get_session', fake_get_session)
    client = TestClient(server.app)

    conversation = 'a' * 32

    accepted = client.post(f'/chat/api/session/{conversation}/mode',
                           json={'mode': 'plan'})
    assert accepted.status_code == 200
    assert accepted.json() == {'mode': 'plan'}
    assert fake.modes == ['plan']

    refused = client.post(f'/chat/api/session/{conversation}/mode',
                          json={'mode': 'bogus'})
    assert refused.status_code == 400
    assert 'bogus' in refused.json()['error']
    assert fake.modes == ['plan']


def test_the_headless_api_requires_a_valid_token(monkeypatch):
    from fastapi.testclient import TestClient

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    server = GatewayServer(GatewayConfig(enabled_channels=['web'],
                                         local_token='sekrit'))
    monkeypatch.setattr(server, '_get_session', fake_session_factory())
    client = TestClient(server.app)

    refused = client.get('/chat/api/conversations')
    assert refused.status_code == 401

    via_header = client.get('/chat/api/conversations',
                            headers={'X-PyClaw-Token': 'sekrit'})
    assert via_header.status_code == 200

    via_query = client.get('/chat/api/conversations?token=sekrit')
    assert via_query.status_code == 200


def test_the_chat_api_stays_open_without_a_token_configured():
    from fastapi.testclient import TestClient

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    server = GatewayServer(GatewayConfig(enabled_channels=['web']))
    client = TestClient(server.app)
    assert client.get('/chat/api/conversations').status_code == 200


def fake_session_factory():
    class _FakeSession:
        pass

    async def fake_get_session(session_key, *args, **kwargs):
        return _FakeSession()

    return fake_get_session


def test_a_cron_fire_runs_a_turn_and_pushes_the_answer_to_known_contacts(
        monkeypatch):
    import asyncio

    from pyclaw.gateway import server as gateway_server
    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    recorded = {'conv': [], 'meta': []}

    class _FakeAdapter:
        channel_id = 'wechat'
        connected = True

        def __init__(self):
            self.sent = []

        def known_contact(self):
            return 'owner-1'

        async def send_message(self, to, message):
            self.sent.append((to, message.text))
            return True

    class _FakeSession:
        name = 's'

        def __init__(self):
            self.chatted = []
            self.recorded = False
            self.conv_session_id = 'conv123'
            self.team = None

        async def chat(self, text, on_event=None):
            self.chatted.append(text)
            return 'the answer'

        def record_turn(self):
            self.recorded = True

    server = GatewayServer(GatewayConfig())
    adapter = _FakeAdapter()
    server.channels['wechat'] = adapter
    session = _FakeSession()

    async def fake_get_session(key, provider=None, model=None):
        return session

    monkeypatch.setattr(server, '_get_session', fake_get_session)
    monkeypatch.setattr(gateway_server, 'append_conv',
                        lambda conv, role, text, **kw:
                        recorded['conv'].append((conv, role, text)))
    monkeypatch.setattr(gateway_server, 'record_meta',
                        lambda conv, meta: recorded['meta'].append(meta))
    asyncio.run(server._deliver_cron(
        {'id': 't1', 'cron': '0 9 * * *', 'prompt': 'do the round'}))
    assert session.chatted == ['do the round']
    assert session.recorded is True
    assert recorded['conv'] == [('conv123', 'user', 'do the round')]
    assert adapter.sent and adapter.sent[0][0] == 'owner-1'
    assert 'the answer' in adapter.sent[0][1]


def test_a_cron_fire_without_contacts_still_runs_the_turn(monkeypatch):
    import asyncio

    from pyclaw.gateway import server as gateway_server
    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    class _FakeAdapter:
        channel_id = 'wechat'
        connected = True

        def known_contact(self):
            return ''

        async def send_message(self, to, message):
            raise AssertionError('should not send')

    class _FakeSession:
        conv_session_id = 'conv'

        async def chat(self, text, on_event=None):
            return 'ok'

        def record_turn(self):
            pass

    server = GatewayServer(GatewayConfig())
    server.channels['wechat'] = _FakeAdapter()
    session = _FakeSession()

    async def fake_get_session(key, provider=None, model=None):
        return session

    monkeypatch.setattr(server, '_get_session', fake_get_session)
    monkeypatch.setattr(gateway_server, 'append_conv',
                        lambda *a, **kw: None)
    monkeypatch.setattr(gateway_server, 'record_meta',
                        lambda *a, **kw: None)
    asyncio.run(server._deliver_cron({'id': 't1', 'prompt': 'round'}))


def test_a_cron_for_a_gone_agent_is_removed_not_delivered(monkeypatch):
    import asyncio

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    removed = []

    class _Store:
        def remove(self, task_id):
            removed.append(task_id)

    class _FakeTeam:
        cron = _Store()

        def get_by_name(self, name):
            return None

    class _FakeSession:
        conv_session_id = 'conv'
        team = _FakeTeam()

        async def chat(self, text, on_event=None):
            raise AssertionError('should not chat')

        def record_turn(self):
            pass

    server = GatewayServer(GatewayConfig())
    session = _FakeSession()

    async def fake_get_session(key, provider=None, model=None):
        return session

    monkeypatch.setattr(server, '_get_session', fake_get_session)
    asyncio.run(server._deliver_cron(
        {'id': 't2', 'prompt': 'ghost work', 'agent': 'ghost'}))
    assert removed == ['t2']


def test_a_cron_for_a_live_teammate_is_submitted_not_chatted(monkeypatch):
    import asyncio

    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    class _Agent:
        def __init__(self):
            self.submitted = []

        def submit(self, prompt):
            self.submitted.append(prompt)

    class _FakeTeam:
        def __init__(self):
            self.roster = {'helper': _Agent()}

        def get_by_name(self, name):
            return self.roster.get(name)

    class _FakeSession:
        conv_session_id = 'conv'

        def __init__(self):
            self.team = _FakeTeam()
            self.chatted = []

        async def chat(self, text, on_event=None):
            self.chatted.append(text)

        def record_turn(self):
            pass

    server = GatewayServer(GatewayConfig())
    session = _FakeSession()

    async def fake_get_session(key, provider=None, model=None):
        return session

    monkeypatch.setattr(server, '_get_session', fake_get_session)
    asyncio.run(server._deliver_cron(
        {'id': 't3', 'prompt': 'help out', 'agent': 'helper'}))
    assert session.team.roster['helper'].submitted == ['help out']
    assert session.chatted == []


def test_the_gateway_starts_and_stops_the_cron_loop(monkeypatch):
    import asyncio

    from pyclaw.gateway import server as gateway_server
    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    started = []

    class _Store:
        directory = 'unused'

    class _FakeSession:
        conv_session_id = 'cronconv'
        team = type('T', (), {'cron': _Store()})()

        def restore_transcript(self):
            pass

    server = GatewayServer(GatewayConfig())

    async def fake_get_session(key, provider=None, model=None):
        return _FakeSession()

    def fake_run(store, lock, deliver, **kw):
        started.append(store)
        done = asyncio.Event()

        async def wait():
            await done.wait()

        return wait()

    monkeypatch.setattr(server, '_get_session', fake_get_session)
    monkeypatch.setattr(gateway_server, 'run_cron_loop', fake_run)
    asyncio.run(server._start_cron())
    assert len(started) == 1
    asyncio.run(server._stop_cron())
