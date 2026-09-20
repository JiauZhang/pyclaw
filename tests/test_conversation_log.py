import asyncio
import json
from pathlib import Path

import pytest

from conippets import jsonl

from pyclaw import agents
from pyclaw.gateway.server import run_im_interaction


@pytest.fixture(autouse=True)
def _logs_under_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)


def _read(path: Path):
    return jsonl.read(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_session_dir_under_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path / "logs")
    assert agents._session_dir("s1") == tmp_path / "logs" / "s1"


def test_append_conv_writes_to_session_messages(tmp_path):
    agents.append_conv("s1", "user", "hi")
    agents.append_conv("s1", "tool", "ran", topic="tool:end", name="search")
    data = _read(tmp_path / "s1" / "messages.jsonl")
    assert "session" not in data[0]
    assert data[0]["role"] == "user"
    assert data[0]["content"] == "hi"
    assert data[1]["role"] == "tool" and data[1]["name"] == "search"


def test_append_conv_keeps_sessions_separate(tmp_path):
    agents.append_conv("s1", "user", "first")
    agents.append_conv("s2", "user", "second")
    assert len(_read(tmp_path / "s1" / "messages.jsonl")) == 1
    assert len(_read(tmp_path / "s2" / "messages.jsonl")) == 1


def test_record_meta_creates_and_updates(tmp_path):
    agents.record_meta("s1", {"provider": "tencent", "model": "hunyuan-lite"})
    agents.record_meta("s1", {"message_count": 3})
    meta = _read_json(tmp_path / "s1" / "meta.json")
    assert meta["session_id"] == "s1"
    assert meta["provider"] == "tencent"
    assert meta["model"] == "hunyuan-lite"
    assert meta["message_count"] == 3
    assert (tmp_path / "s1" / "meta.json").read_text(encoding="utf-8").endswith("\n")


def test_session_logger_writes_run_log(tmp_path):
    log = agents.session_logger("s1")
    log.info("agent started")
    log.error("boom")
    text = (tmp_path / "s1" / "run.log").read_text(encoding="utf-8")
    assert "agent started" in text
    assert "boom" in text


def test_session_logger_is_stable():
    a = agents.session_logger("s1")
    b = agents.session_logger("s1")
    assert a is b


def test_close_session_logger_removes_and_closes(tmp_path):
    log = agents.session_logger("s1")
    assert log.handlers
    agents.close_session_logger("s1")
    assert log.handlers == []
    rebuilt = agents.session_logger("s1")
    assert rebuilt.handlers
    assert rebuilt is log


def test_im_interaction_logs_user_and_assistant(tmp_path):
    class Adapter:
        def __init__(self):
            self.sent = []

        async def send_message(self, to, msg):
            self.sent.append(msg.text)
            return True

    class Session:
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

    adapter = Adapter()
    session = Session()
    asyncio.run(run_im_interaction(
        session, adapter, "abc123", "u1", "hi there", "m1",
        im_extra="", progress_fn=lambda ev: "", status_interval=0.01, max_msg_len=1500,
    ))

    data = _read(tmp_path / "abc123" / "messages.jsonl")
    roles = [d["role"] for d in data]
    assert "user" in roles
    assert "assistant" in roles
    user = next(d for d in data if d["role"] == "user")
    assert user["content"] == "hi there"
    assert "session" not in user
    assistant = next(d for d in data if d["role"] == "assistant")
    assert assistant["content"] == "final answer"


def test_resolve_session_id_distinct_keys_distinct_ids():
    ids = {
        agents.resolve_session_id(["wechat", "u1"]),
        agents.resolve_session_id(["qq", "u1"]),
        agents.resolve_session_id(["wechat", "u2"]),
    }
    assert len(ids) == 3


def test_resolve_session_id_persists(tmp_path):
    first = agents.resolve_session_id(["wechat", "u1"])
    index = json.loads((tmp_path / "session_index.json").read_text(encoding="utf-8"))
    assert list(index.values()) == [first]
    again = agents.resolve_session_id(["wechat", "u1"])
    assert first == again
