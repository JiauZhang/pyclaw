import asyncio
import json

import pytest

from pyclaw import __main__


def _fake_team():
    class T:
        provider = "p"
        model = "m"
        thinking = False
        name = "t"
        provided_tools = []

        def tool_schemas(self):
            return [{"name": "k", "description": "d", "input_schema": {}}]

        def transcript(self):
            return [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]

    return T()


class _U:
    def to_dict(self):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


class _FakeSession:
    name = "t"
    mode = "team"
    permission_mode = "default"
    provider = "p"
    model = "m"
    thinking = False
    usage = _U()

    def __init__(self, team):
        self.team = team

    async def chat(self, message, on_event=None):
        return "\n\nhello\n"

    async def close(self):
        pass


def test_prompt_once_returns_original_text(monkeypatch):
    monkeypatch.setattr(__main__, "build_team", lambda *a, **k: _fake_team())
    monkeypatch.setattr(__main__, "Session", _FakeSession)
    out = asyncio.run(__main__.prompt_once("p", "m", "hi"))
    assert out["text"] == "\n\nhello\n"
    assert out["mode"] == "team"
    assert out["messages"] == 2
    assert out["usage"]["total_tokens"] == 0


def test_render_output_text(capsys):
    __main__.render_output("text", {"text": "ok", "mode": "team", "messages": 2})
    assert capsys.readouterr().out == "ok\n"


def test_render_output_json(capsys):
    __main__.render_output("json", {"text": "ok", "mode": "team", "messages": 2})
    out = json.loads(capsys.readouterr().out)
    assert out == {"text": "ok", "mode": "team", "messages": 2}