import asyncio

import pytest

from chatchat.hooks.events import (AGENT_REASON_START, AGENT_TEXT,
                                   AGENT_TOOL_CALL, AGENT_WARN, RuntimeEvent)

from pyclaw import agents
from pyclaw import session_store
from pyclaw.gateway.im import _im_progress_text, run_im_interaction


@pytest.fixture(autouse=True)
def _logs(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, '_logs_dir', lambda: tmp_path)


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


def _run(session, adapter, clock=lambda: 0.0, **kw):
    return asyncio.run(run_im_interaction(
        session, adapter, 'sid1', 'u1', 'hi', 'm1',
        im_extra='', progress_fn=_im_progress_text, status_interval=4.0,
        max_msg_len=1500, clock=clock, **kw))


def test_im_progress_text_maps_real_event_kinds():
    cases = (
        (RuntimeEvent(AGENT_REASON_START, agent='lead'), '🔄 PyClaw 思考中…'),
        (RuntimeEvent(AGENT_TOOL_CALL, agent='lead',
                      data={'tool': 'Read', 'input': {}}), '🔧 调用工具 Read'),
        (RuntimeEvent(AGENT_WARN, agent='lead',
                      data={'text': 'careful'}), '⚠️ careful'),
        (RuntimeEvent(AGENT_TEXT, agent='lead', data={'delta': 'hi'}), ''))
    for event, expected in cases:
        assert _im_progress_text(event) == expected


def test_interaction_splits_long_response():
    adapter = _Adapter()
    assert _run(_Session('x' * 5000), adapter) == 'x' * 5000
    assert all(len(part) <= 1500 for part in adapter.sent)
    assert ''.join(adapter.sent) == 'x' * 5000


def test_interaction_collapses_status_bursts_to_latest():
    adapter = _Adapter()
    clock_vals = [0.0, 1.0, 1.0, 1.0, 5.0, 5.0]
    count = {'n': 0}

    def clock():
        value = clock_vals[min(count['n'], len(clock_vals) - 1)]
        count['n'] += 1
        return value

    session = _Session('done', [
        RuntimeEvent(AGENT_REASON_START, agent='lead'),
        RuntimeEvent(AGENT_TOOL_CALL, agent='lead',
                     data={'tool': 'search', 'input': {}})])
    _run(session, adapter, clock=clock)
    assert adapter.sent == ['🔧 调用工具 search', 'done']


def test_interaction_sends_only_the_answer_without_progress():
    adapter = _Adapter()
    _run(_Session('short answer'), adapter)
    assert adapter.sent == ['short answer']
