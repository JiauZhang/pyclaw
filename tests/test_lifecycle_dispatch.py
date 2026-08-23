import asyncio

from pyclaw.agents import _dispatch_event


class _Ev:
    def __init__(self, topic, source, data=None):
        self.topic = topic
        self.source = source
        self.data = data or {}


def test_dispatch_sync_callback_invoked_directly():
    calls = []

    def sync_cb(ev):
        calls.append(ev.topic)

    _dispatch_event(sync_cb, _Ev("lifecycle:tool:start", "agent"))
    assert calls == ["lifecycle:tool:start"]


def test_dispatch_async_callback_scheduled():
    async def run():
        scheduled = []

        async def async_cb(ev):
            scheduled.append(ev.topic)

        _dispatch_event(async_cb, _Ev("lifecycle:agent:start", "agent"))
        await asyncio.sleep(0)
        return scheduled

    assert asyncio.run(run()) == ["lifecycle:agent:start"]
