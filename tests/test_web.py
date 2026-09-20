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
