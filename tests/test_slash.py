import asyncio

import pytest

from pyclaw import slash


HELP_KEYWORDS = ("/help", "/agent", "/team", "/clear", "/status", "/tools", "/thinking")


def _fake_session(**kwargs):
    class FakeEntity:
        class Client:
            messages = []
        class Config:
            provider = "p"
            model = "m"
        client = Client()
        config = Config()
        sub_agents = {}

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 2
            total_tokens = 3
        total_usage = _Usage()

    class FakeSession:
        name = "s1"
        mode = "agent"
        thinking = False
        available_tools = ["a", "b"]

        def __init__(self, **kw):
            self.entity = FakeEntity()
            self.entity.config.provider = kw.get("provider", "p")
            self.entity.config.model = kw.get("model", "m")
            for k, v in kw.items():
                setattr(self, k, v)

        def switch(self, mode):
            self._switched = mode
            return asyncio.sleep(0)

        def reset(self):
            self._reset = True

        def set_thinking(self, on):
            self.thinking = on

    return FakeSession(**kwargs)


async def _call(*args, **kw):
    return await slash.handle_slash(*args, **kw)


def test_help_lists_commands():
    out = asyncio.run(_call("/help", _fake_session()))
    assert out is not None
    for kw in HELP_KEYWORDS:
        assert kw in out


def test_unknown_command_returns_help():
    out = asyncio.run(_call("/nope", _fake_session()))
    assert out.startswith("Unknown command")
    assert "/help" in out


def test_non_slash_returns_none():
    assert asyncio.run(_call("hello world", _fake_session())) is None


def test_clear_command():
    out = asyncio.run(_call("/clear", _fake_session()))
    assert "cleared" in out.lower()


def test_tools_command():
    session = _fake_session()
    out = asyncio.run(_call("/tools", session))
    assert "a" in out and "b" in out


def test_status_command():
    session = _fake_session()
    out = asyncio.run(_call("/status", session, session_key="k1"))
    assert "k1" in out
    assert "agent" in out


def test_thinking_toggle_on_off():
    session = _fake_session()
    assert "on" in asyncio.run(_call("/thinking on", session))
    assert session.thinking is True
    assert "off" in asyncio.run(_call("/thinking off", session))
    assert session.thinking is False


def test_thinking_without_arg_reports_state():
    session = _fake_session(thinking=True)
    out = asyncio.run(_call("/thinking", session))
    assert "on" in out


def test_thinking_invalid_value():
    session = _fake_session()
    out = asyncio.run(_call("/thinking maybe", session))
    assert "Invalid" in out


def test_command_aliases():
    assert "single-agent" in asyncio.run(_call("/agent", _fake_session()))
    assert "multi-agent" in asyncio.run(_call("/team", _fake_session()))
