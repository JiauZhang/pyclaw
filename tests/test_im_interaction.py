import asyncio

import pytest

from pyclaw.gateway.server import run_im_interaction


class _Ev:
    def __init__(self, topic, data=None):
        self.topic = topic
        self.data = data or {}


def _progress(ev):
    topic = ev.topic
    data = ev.data or {}
    if topic == "lifecycle:tool:start":
        return f"[Using: {data.get('name')}]"
    if topic == "lifecycle:agent:start":
        return "[thinking]"
    return ""


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


def test_interaction_splits_long_response():
    adapter = _Adapter()
    session = _Session("x" * 5000)
    response = asyncio.run(run_im_interaction(
        session, adapter, "u1", "hi", "m1",
        im_extra="", progress_fn=_progress, status_interval=4.0, max_msg_len=1500,
        clock=lambda: 0.0,
    ))
    assert response == "x" * 5000
    assert all(len(p) <= 1500 for p in adapter.sent)
    assert "".join(adapter.sent) == "x" * 5000


def test_interaction_throttles_status_messages():
    # clock advances slowly so only the first drain emits; later drains within
    # the interval are suppressed. We let chat emit two distinct statuses.
    events = [_Ev("lifecycle:agent:start"), _Ev("lifecycle:tool:start", {"name": "search"})]
    session = _Session("done", events)
    adapter = _Adapter()

    clock_vals = [0.0, 1.0, 1.0, 1.0, 5.0, 5.0]
    idx = {"i": 0}

    def clock():
        v = clock_vals[min(idx["i"], len(clock_vals) - 1)]
        idx["i"] += 1
        return v

    asyncio.run(run_im_interaction(
        session, adapter, "u1", "hi", "m1",
        im_extra="", progress_fn=_progress, status_interval=4.0, max_msg_len=1500,
        clock=clock,
    ))

    # statuses emitted: first drain (clock 0) keeps only the latest pending
    # status -> [Using: search]; earlier [thinking] is coalesced away.
    statuses = [s for s in adapter.sent if s.startswith("[")]
    assert statuses == ["[Using: search]"]
    assert adapter.sent[-1] == "done"


def test_interaction_no_status_when_progress_empty():
    session = _Session("short answer")
    adapter = _Adapter()
    asyncio.run(run_im_interaction(
        session, adapter, "u1", "hi", "m1",
        im_extra="", progress_fn=lambda ev: "", status_interval=4.0, max_msg_len=1500,
        clock=lambda: 0.0,
    ))
    assert adapter.sent == ["short answer"]
