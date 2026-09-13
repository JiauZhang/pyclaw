import asyncio

import pytest

from pyclaw import slash


HELP_KEYWORDS = ("/help", "/agent", "/team", "/clear", "/status", "/tools",
                 "/thinking", "/model", "/cost")


class _Usage:
    def __init__(self, prompt=0, completion=0, total=0, cached=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = total
        self.prompt_tokens_details = {"cached_tokens": cached} if cached else None


def _fake_session(**kwargs):
    class FakeSession:
        name = "s1"
        mode = "agent"
        thinking = False
        provider = "p"
        model = "m"
        available_tools = ["a", "b"]
        context_messages = 0
        active_agents = 0
        usage = _Usage()

        def __init__(self, **kw):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def switch(self, mode):
            self._switched = mode
            return asyncio.sleep(0)

        def reset(self):
            self._reset = True

        def set_thinking(self, on):
            self.thinking = on

        def set_model(self, model):
            self.model = model

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


def test_status_includes_usage_and_cost():
    session = _fake_session(usage=_Usage(1200, 300, 1500))
    out = asyncio.run(_call("/status", session, session_key="k1"))
    assert "1200 in" in out
    assert "300 out" in out
    assert "unpriced" in out


def test_model_command_reports_current_model():
    out = asyncio.run(_call("/model", _fake_session()))
    assert out == "Model: m"


def test_model_command_switches_session_and_config(monkeypatch):
    from pyclaw import config as config_module
    saved = {}
    monkeypatch.setattr(config_module, "save", lambda c: saved.update(c))
    monkeypatch.setattr("pyclaw.load", lambda: {"model": "m"})

    session = _fake_session()
    out = asyncio.run(_call("/model newmodel", session))
    assert out == "Model: newmodel"
    assert session.model == "newmodel"
    assert saved["model"] == "newmodel"


def test_cost_command_unpriced(monkeypatch):
    from pyclaw import config as config_module
    monkeypatch.setattr(config_module, "load", lambda: {"pricing": {}})
    session = _fake_session(usage=_Usage(1200, 300, 1500))
    out = asyncio.run(_call("/cost", session))
    assert "unpriced" in out
    assert "pricing.m" in out
    assert "1200 in" in out


def test_cost_command_with_pricing(monkeypatch):
    from pyclaw import config as config_module
    monkeypatch.setattr(config_module, "load", lambda: {
        "pricing": {"m": {"input": 1, "output": 2}}})
    session = _fake_session(usage=_Usage(1000, 500, 1500))
    out = asyncio.run(_call("/cost", session))
    assert "$0.0020" in out


def test_command_aliases():
    assert "single-agent" in asyncio.run(_call("/agent", _fake_session()))
    assert "multi-agent" in asyncio.run(_call("/team", _fake_session()))
