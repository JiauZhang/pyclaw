import asyncio

from chatchat.hooks.events import (
    AGENT_REASON_START,
    AGENT_TEXT,
    AGENT_TOOL_CALL,
    AGENT_WARN,
    RuntimeEvent,
)

from pyclaw import agents
from pyclaw.gateway.server import _im_progress_text, run_im_interaction


class _Adapter:
    def __init__(self):
        self.sent = []

    async def send_message(self, to, msg):
        self.sent.append(msg.text)
        return True


class _Session:
    def __init__(self, response, events=None):
        self._response = response
        self._events = events or []

    async def chat(self, message, on_event=None):
        for ev in self._events:
            if on_event:
                on_event(ev)
        return self._response


def test_im_progress_text_maps_real_event_kinds():
    assert _im_progress_text(RuntimeEvent(
        AGENT_REASON_START, agent="lead")) == "🔄 PyClaw 思考中…"
    assert _im_progress_text(RuntimeEvent(
        AGENT_TOOL_CALL, agent="lead",
        data={"tool": "Read", "input": {}})) == "🔧 调用工具 Read"
    assert _im_progress_text(RuntimeEvent(
        AGENT_WARN, agent="lead", data={"text": "careful"})) == "⚠️ careful"
    assert _im_progress_text(RuntimeEvent(
        AGENT_TEXT, agent="lead", data={"delta": "hi"})) == ""


def test_interaction_splits_long_response(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    adapter = _Adapter()
    session = _Session("x" * 5000)
    response = asyncio.run(run_im_interaction(
        session, adapter, "sid1", "u1", "hi", "m1",
        im_extra="", progress_fn=_im_progress_text, status_interval=4.0,
        max_msg_len=1500, clock=lambda: 0.0,
    ))
    assert response == "x" * 5000
    assert all(len(p) <= 1500 for p in adapter.sent)
    assert "".join(adapter.sent) == "x" * 5000


def test_interaction_collapses_status_bursts_to_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    events = [
        RuntimeEvent(AGENT_REASON_START, agent="lead"),
        RuntimeEvent(AGENT_TOOL_CALL, agent="lead",
                     data={"tool": "search", "input": {}}),
    ]
    session = _Session("done", events)
    adapter = _Adapter()

    clock_vals = [0.0, 1.0, 1.0, 1.0, 5.0, 5.0]
    idx = {"i": 0}

    def clock():
        v = clock_vals[min(idx["i"], len(clock_vals) - 1)]
        idx["i"] += 1
        return v

    asyncio.run(run_im_interaction(
        session, adapter, "sid1", "u1", "hi", "m1",
        im_extra="", progress_fn=_im_progress_text, status_interval=4.0,
        max_msg_len=1500, clock=clock,
    ))

    assert adapter.sent == ["🔧 调用工具 search", "done"]


def test_interaction_no_status_when_progress_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    session = _Session("short answer")
    adapter = _Adapter()
    asyncio.run(run_im_interaction(
        session, adapter, "sid1", "u1", "hi", "m1",
        im_extra="", progress_fn=_im_progress_text, status_interval=4.0,
        max_msg_len=1500, clock=lambda: 0.0,
    ))
    assert adapter.sent == ["short answer"]
