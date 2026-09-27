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

        def record_turn(self):
            pass

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
        {"session_id": "s1", "agent_id": "agent1", "is_final": True, "full_response": "full text"},
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
