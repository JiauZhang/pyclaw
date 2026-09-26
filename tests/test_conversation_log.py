import asyncio
import json
import logging
import os
import time
from pathlib import Path

import pytest

from conippets import jsonl

from pyclaw.session import store as session_store
from pyclaw.session import Session as RealSession
from pyclaw.session.store import (_session_dir, append_conv, conversation_log,
                                  follow_conversation, record_meta,
                                  resolve_session_id)
from pyclaw.gateway.im import run_im_interaction


@pytest.fixture(autouse=True)
def _logs_under_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_logs_dir", lambda: tmp_path)


def _read(path: Path):
    return jsonl.read(path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_session_dir_under_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_logs_dir", lambda: tmp_path / "logs")
    assert _session_dir("s1") == tmp_path / "logs" / "s1"


def test_append_conv_writes_to_session_messages(tmp_path):
    session_store.append_conv("s1", "user", "hi")
    session_store.append_conv("s1", "tool", "ran", topic="tool:end", name="search")
    data = _read(tmp_path / "s1" / "messages.jsonl")
    assert "session" not in data[0]
    assert data[0]["role"] == "user"
    assert data[0]["content"] == "hi"
    assert data[1]["role"] == "tool" and data[1]["name"] == "search"


def test_append_conv_keeps_sessions_separate(tmp_path):
    session_store.append_conv("s1", "user", "first")
    session_store.append_conv("s2", "user", "second")
    assert len(_read(tmp_path / "s1" / "messages.jsonl")) == 1
    assert len(_read(tmp_path / "s2" / "messages.jsonl")) == 1


def test_record_meta_creates_and_updates(tmp_path):
    session_store.record_meta("s1", {"provider": "tencent", "model": "hunyuan-lite"})
    session_store.record_meta("s1", {"message_count": 3})
    meta = _read_json(tmp_path / "s1" / "meta.json")
    assert meta["session_id"] == "s1"
    assert meta["provider"] == "tencent"
    assert meta["model"] == "hunyuan-lite"
    assert meta["message_count"] == 3
    assert (tmp_path / "s1" / "meta.json").read_text(encoding="utf-8").endswith("\n")


def test_the_conversation_log_holds_what_this_conversation_did(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(logging.getLogger(), 'level', logging.DEBUG)
    path = session_store.follow_conversation('s1')
    logging.getLogger('pyclaw.test').info('agent started')
    logging.getLogger('pyclaw.test').error('boom')
    text = path.read_text(encoding='utf-8')
    assert 'agent started' in text
    assert 'boom' in text


def test_following_a_conversation_moves_the_log_and_closes_the_old_one(
        tmp_path, monkeypatch):
    monkeypatch.setattr(logging.getLogger(), 'level', logging.DEBUG)
    first = session_store.follow_conversation('s1')
    logging.getLogger('pyclaw.test').info('while on the first')
    second = session_store.follow_conversation('s2')
    assert first != second
    logging.getLogger('pyclaw.test').info('only in the second')
    assert 'only in the second' in second.read_text(encoding='utf-8')
    assert 'only in the second' not in first.read_text(encoding='utf-8')
    assert 'while on the first' in first.read_text(encoding='utf-8')


def test_a_quiet_conversation_leaves_no_log_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(logging.getLogger(), 'level', logging.DEBUG)
    path = session_store.follow_conversation('quiet')
    assert not path.exists()


def test_a_conversation_log_is_a_file_of_its_own(tmp_path):
    path = session_store.conversation_log('s9')
    assert path == tmp_path / 's9' / 'run.log'


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
            RealSession._flush_conv(self)

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
        session_store.resolve_session_id(["wechat", "u1"]),
        session_store.resolve_session_id(["qq", "u1"]),
        session_store.resolve_session_id(["wechat", "u2"]),
    }
    assert len(ids) == 3


def test_resolve_session_id_persists(tmp_path):
    first = session_store.resolve_session_id(["wechat", "u1"])
    index = json.loads((tmp_path / "session_index.json").read_text(encoding="utf-8"))
    assert list(index.values()) == [first]
    again = session_store.resolve_session_id(["wechat", "u1"])
    assert first == again


def _age(path, days):
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_sweep_drops_only_the_stale_conversation(tmp_path):
    fresh, stale = tmp_path / 'fresh', tmp_path / 'stale'
    for folder in (fresh, stale):
        folder.mkdir()
        (folder / 'transcript.jsonl').write_text('', encoding='utf-8')
    plans = session_store.plans_dir()
    plans.mkdir(parents=True, exist_ok=True)
    old_plan, new_plan = plans / 'old-plan.md', plans / 'new-plan.md'
    old_plan.write_text('x', encoding='utf-8')
    new_plan.write_text('x', encoding='utf-8')
    _age(stale, 40)
    _age(old_plan, 40)

    assert session_store.sweep(30) == 1
    assert fresh.exists() and not stale.exists()
    assert new_plan.exists() and not old_plan.exists()
