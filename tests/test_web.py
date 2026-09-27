import asyncio


from pyclaw.channels.web import WebChannelAdapter


class _CollectingAdapter(WebChannelAdapter):
    def __init__(self):
        super().__init__({})
        self.sent = []

    async def send_response(self, client_id, text, message_type="response", extra_data=None):
        self.sent.append((message_type, text, extra_data))


def _make_session(name="agent1"):
    class Session:
        conv_session_id = "s1"
        model = "m1"
        mode = "default"

        def record_turn(self):
            return {"input": 10, "output": 5, "cached": 2, "seconds": 3,
                    "model": "m1"}

        def stream(self, message, on_event=None):
            async def gen():
                yield "Hello "
                yield "world"
            return gen()

        async def end_session(self, reason):
            pass

        def reset(self):
            pass

    Session.name = name
    return Session()


def _make_runtime():
    calls = {"get_or_create": 0, "activity": 0, "requests": 0, "errors": 0}

    class Runtime:
        def get_or_create_session(self, *a, **k):
            calls["get_or_create"] += 1

        def update_session_activity(self, *a, **k):
            calls["activity"] += 1

        def increment_requests(self, *a, **k):
            calls["requests"] += 1

        def increment_errors(self, *a, **k):
            calls["errors"] += 1

    return Runtime(), calls


def _run_message(text):
    adapter = _CollectingAdapter()
    runtime, calls = _make_runtime()
    asyncio.run(adapter._process_message("c1", {"type": "message", "text": text},
                                         _make_session(), runtime))
    return adapter, calls


def test_finish_stream_payload():
    adapter = _CollectingAdapter()

    asyncio.run(adapter._finish_stream("c1", "s1", _make_session(), "full text"))

    assert adapter.sent == [(
        "stream_complete",
        "",
        {"session_id": "s1", "agent_id": "agent1", "is_final": True,
         "full_response": "full text", "model": "m1", "mode": "default",
         "usage": {"input": 10, "output": 5, "cached": 2, "seconds": 3}},
    )]


def test_process_message_normal_streams_chunks_and_completes():
    adapter, calls = _run_message("hi")

    assert adapter.sent[0][0] == "stream_chunk"
    assert adapter.sent[0][1] == "Hello "
    assert adapter.sent[-1][0] == "stream_complete"
    assert adapter.sent[-1][2]["full_response"] == "Hello world"
    assert calls["requests"] == 1
    assert calls["errors"] == 0


def test_process_message_slash_command_streams_reply():
    adapter, calls = _run_message("/clear")

    assert adapter.sent[0][0] == "stream_chunk"
    assert adapter.sent[-1][0] == "stream_complete"
    assert adapter.sent[-1][2]["full_response"] == adapter.sent[0][1]
    assert calls["requests"] == 1


def test_a_failed_turn_reaches_the_page_as_an_error(tmp_path, monkeypatch):
    """The turn died on an API 503; the browser has to be told, not left
    staring at an empty reply."""
    from pyclaw.session import store as session_store

    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)

    from chatchat.hooks.events import AGENT_WARN, RuntimeEvent

    failure = "503, message='Service Unavailable'"

    class Session:
        name = 'agent1'
        conv_session_id = 'c1'

        def stream(self, message, on_event=None):
            async def gen():
                await on_event(RuntimeEvent(AGENT_WARN, agent='team-lead',
                                          team='t',
                                          data={'text': failure}))
                return
                yield

            return gen()

        async def end_session(self, reason):
            pass

        def reset(self):
            pass

    adapter = _CollectingAdapter()
    runtime, calls = _make_runtime()
    asyncio.run(adapter._process_message("c1", {"type": "message", "text": "hi"},
                                         Session(), runtime))
    assert (["error", failure] in
            [[kind, text] for kind, text, _ in adapter.sent])


def test_a_web_turn_stays_on_the_conversation_the_session_chose(tmp_path,
                                                                monkeypatch):
    from pyclaw.session import store as session_store

    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)

    class Session:
        name = 'agent1'
        conv_session_id = 'resumed-here'

        def stream(self, message, on_event=None):
            async def gen():
                yield "ok"
            return gen()

        async def end_session(self, reason):
            pass

        def reset(self):
            pass

    adapter = _CollectingAdapter()
    runtime, _ = _make_runtime()
    session = Session()
    asyncio.run(adapter._process_message("c1", {"type": "message", "text": "hi"},
                                         session, runtime))
    assert session.conv_session_id == 'resumed-here'
    assert (tmp_path / 'resumed-here' / 'messages.jsonl').exists()


class _Closed(Exception):
    pass


class _FakeWS:
    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.out = []

    async def send_json(self, payload):
        self.out.append(payload)

    async def receive_json(self):
        if not self.incoming:
            raise _Closed()
        item = self.incoming.pop(0)
        await asyncio.sleep(0)
        return item


class _Lead:
    def __init__(self):
        self.aborts = 0

    def abort_work(self):
        self.aborts += 1


class _Team:
    lead = _Lead()


class _WSSession:
    _team = _Team()
    name = "agent1"
    conv_session_id = "conv1"

    def record_turn(self):
        pass

    def stream(self, message, on_event=None):
        async def gen():
            yield "reply"
        return gen()


def _run_socket(adapter, incoming, get_session, runtime=None,
                monkeypatch=None, tmp_path=None):
    import pytest

    from pyclaw.session import store as session_store

    if tmp_path is not None:
        monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)
    runtime = runtime or _make_runtime()[0]
    ws = _FakeWS(incoming)
    with pytest.raises(_Closed):
        asyncio.run(adapter.handle_websocket(ws, "conv1", get_session, runtime))
    return ws


def test_the_socket_announces_the_conversation_and_creates_the_session_upfront(monkeypatch, tmp_path):
    """会话在连接时创建：环境/磁盘类错误在握手阶段就推给客户端，
    而不是等第一条消息发出去才发现。"""
    from pyclaw.channels.web import WebChannelAdapter

    created = []

    async def get_session():
        created.append(1)
        return _WSSession()

    adapter = WebChannelAdapter({})
    ws = _run_socket(adapter, [{"type": "ping"}], get_session,
                     monkeypatch=monkeypatch, tmp_path=tmp_path)

    assert created == [1]
    assert ws.out[0]["type"] == "connected"
    assert ws.out[0]["session_id"] == "conv1"
    assert any(out.get("type") == "pong" for out in ws.out)


def test_an_abort_reaches_the_team_lead_and_is_acknowledged(monkeypatch, tmp_path):
    from pyclaw.channels.web import WebChannelAdapter

    session = _WSSession()

    async def get_session():
        return session

    adapter = WebChannelAdapter({})
    ws = _run_socket(adapter,
                     [{"type": "message", "text": "hi"}, {"type": "abort"}],
                     get_session, monkeypatch=monkeypatch,
                     tmp_path=tmp_path)

    assert session._team.lead.aborts == 1
    assert {"type": "aborted"} in ws.out


def test_a_second_message_while_a_turn_runs_is_refused(monkeypatch, tmp_path):
    from pyclaw.channels.web import WebChannelAdapter

    class BusySession(_WSSession):
        def stream(self, message, on_event=None):
            async def gen():
                yield "start"
                await asyncio.Event().wait()
            return gen()

    async def get_session():
        return BusySession()

    adapter = WebChannelAdapter({})
    ws = _run_socket(adapter,
                     [{"type": "message", "text": "first"},
                      {"type": "message", "text": "second"}],
                     get_session, monkeypatch=monkeypatch,
                     tmp_path=tmp_path)

    errors = [out for out in ws.out if out.get("type") == "error"]
    assert any("already running" in out.get("text", "") for out in errors)


def test_tool_activity_reaches_the_page_as_structured_progress(tmp_path):
    from chatchat.hooks.events import AGENT_TOOL_CALL, RuntimeEvent

    from pyclaw.channels.web import WebChannelAdapter

    class EventSession(_WSSession):
        def stream(self, message, on_event=None):
            async def gen():
                await on_event(RuntimeEvent(AGENT_TOOL_CALL, agent='lead',
                                            team='t',
                                            data={'tool': 'Bash',
                                                  'input': {'command': 'ls'}}))
                yield "done"
            return gen()

    async def get_session():
        return EventSession()

    adapter = _CollectingAdapter()
    runtime, _ = _make_runtime()
    asyncio.run(adapter._process_message("c1", {"type": "message", "text": "hi"},
                                         EventSession(), runtime))
    progress = [s for s in adapter.sent if s[0] == "progress"]
    assert progress and progress[0][2]["kind"] == "agent.tool_call"
    assert progress[0][2]["tool"] == "Bash"
