import asyncio

import pytest

from pyclaw import agents
from pyclaw.gateway.server import run_im_interaction


class _Ev:
    def __init__(self, topic, data=None):
        self.topic = topic
        self.data = data or {}


class _Chunk:
    def __init__(self, content="", reasoning_content=""):
        self.choices = [type("C", (), {"delta": type("D", (), {
            "content": content, "reasoning_content": reasoning_content,
        })})()]


def _capture(monkeypatch):
    records = []

    def fake_info(msg, extra=None, **kwargs):
        records.append({"msg": msg, "extra": extra or {}})

    monkeypatch.setattr(agents._conv_logger, "info", fake_info)
    return records


def test_record_tool_start_and_end(monkeypatch):
    records = _capture(monkeypatch)
    agents._record_event("s1", _Ev("lifecycle:tool:start", {"name": "search", "input": "北京天气"}))
    agents._record_event("s1", _Ev("lifecycle:tool:end", {"name": "search", "output": "晴 25度"}))

    tool_records = [r for r in records if r["extra"].get("conv_role") == "tool"]
    assert len(tool_records) == 2
    assert tool_records[0]["extra"]["conv_topic"] == "lifecycle:tool:start"
    assert "search" in tool_records[0]["extra"]["conv_detail"]
    assert "北京天气" in tool_records[0]["extra"]["conv_detail"]
    assert "晴 25度" in tool_records[1]["extra"]["conv_detail"]


def test_record_tool_error(monkeypatch):
    records = _capture(monkeypatch)
    agents._record_event("s1", _Ev("lifecycle:tool:error", {"name": "search", "error": "timeout"}))

    rec = records[-1]
    assert rec["extra"]["conv_role"] == "tool"
    assert rec["extra"]["conv_topic"] == "lifecycle:tool:error"
    assert "timeout" in rec["extra"]["conv_detail"]


def test_accumulate_then_flush_merges_steps(monkeypatch):
    records = _capture(monkeypatch)
    session = agents.Session.__new__(agents.Session)
    session.conv_session_id = "s1"
    session._conv_thinking = ""
    session._conv_reply = ""

    session._accumulate(_Ev("lifecycle:client:step", _Chunk(
        content="你好", reasoning_content="用户在问好",
    )))
    session._accumulate(_Ev("lifecycle:client:step", _Chunk(
        content="世界", reasoning_content="接着想",
    )))
    session._flush_conv()

    topics = [(r["extra"]["conv_topic"], r["extra"]["conv_detail"]) for r in records]
    assert ("THINKING", "用户在问好接着想") in topics
    assert ("REPLY", "你好世界") in topics
    assert len([t for t in topics if t[0] == "REPLY"]) == 1
    assert len([t for t in topics if t[0] == "THINKING"]) == 1


def test_record_agent_start(monkeypatch):
    records = _capture(monkeypatch)
    agents._record_event("s1", _Ev("lifecycle:agent:start", {"name": "researcher"}))

    rec = records[-1]
    assert rec["extra"]["conv_role"] == "agent"
    assert rec["extra"]["conv_topic"] == "lifecycle:agent:start"
    assert "researcher" in rec["extra"]["conv_detail"]


def test_im_interaction_logs_user_and_assistant(monkeypatch):
    records = _capture(monkeypatch)

    class _Adapter:
        def __init__(self):
            self.sent = []

        async def send_message(self, to, msg):
            self.sent.append(msg.text)
            return True

    class _Session:
        def __init__(self):
            self.conv_session_id = None
            self._conv_thinking = ""
            self._conv_reply = ""

        def _flush_conv(self):
            agents.Session._flush_conv(self)

        async def chat(self, message, on_event=None):
            self._conv_reply = "final answer"
            self._flush_conv()
            return "final answer"

    adapter = _Adapter()
    session = _Session()
    response = asyncio.run(run_im_interaction(
        session, adapter, "u1", "hi there", "m1",
        im_extra="", progress_fn=lambda ev: "", status_interval=0.01, max_msg_len=1500,
    ))

    assert response == "final answer"
    topics = [r["extra"]["conv_topic"] for r in records]
    assert "USER" in topics
    assert "REPLY" in topics
    user_rec = next(r for r in records if r["extra"]["conv_topic"] == "USER")
    assert user_rec["extra"]["conv_detail"] == "hi there"
    assert user_rec["extra"]["conv_session"] == "im_u1"
    reply_rec = next(r for r in records if r["extra"]["conv_topic"] == "REPLY")
    assert reply_rec["extra"]["conv_detail"] == "final answer"
