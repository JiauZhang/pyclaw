import asyncio

import pytest

from chatchat.client import MockClient
from chatchat.team import Team

from pyclaw import __main__, agents


@pytest.fixture(autouse=True)
def _logs_under_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)


async def _answer(messages, tools=None, *, stream_cb=None):
    return "answer"


def _session(handler, session_id):
    team = Team("t1", client_factory=lambda inst, model=None:
                MockClient(handler=handler, model=model))
    return agents.Session(team, session_id=session_id)


def test_transcript_roundtrip():
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ok"}]
    agents.save_transcript("s1", messages)
    assert agents.load_transcript("s1") == messages
    assert agents.load_transcript("missing") == []


def test_transcript_skips_broken_lines():
    path = agents.transcript_path("s1")
    path.write_text('{"role": "user", "content": "a"}\nnot json\n\n',
                    encoding="utf-8")
    assert agents.load_transcript("s1") == [{"role": "user", "content": "a"}]


def test_transcript_appends_and_chains_uuids():
    first = [{"role": "user", "content": "a"},
             {"role": "assistant", "content": "b"}]
    agents.save_transcript("s1", first)
    path = agents.transcript_path("s1")
    size_after_first = path.stat().st_size

    agents.save_transcript("s1", first + [{"role": "user", "content": "c"}])
    entries = agents.load_entries("s1")
    assert [e["content"] for e in entries] == ["a", "b", "c"]
    assert entries[0]["parentUuid"] is None
    assert entries[1]["parentUuid"] == entries[0]["uuid"]
    assert entries[2]["parentUuid"] == entries[1]["uuid"]
    assert path.stat().st_size > size_after_first

    agents.save_transcript("s1", first + [{"role": "user", "content": "c"}])
    assert len(agents.load_entries("s1")) == 3


def test_transcript_rewrites_when_history_is_compacted():
    agents.save_transcript("s1", [{"role": "user", "content": "a"},
                                  {"role": "assistant", "content": "b"}])
    agents.save_transcript("s1", [{"role": "user", "content": "summary"}])
    entries = agents.load_entries("s1")
    assert [e["content"] for e in entries] == ["summary"]
    assert entries[0]["parentUuid"] is None


def test_session_persists_and_restores_history():
    seen = []

    async def handler(messages, tools=None, *, stream_cb=None):
        seen.append([dict(m) for m in messages])
        return "answer"

    async def main():
        first = _session(handler, "s1")
        await first.chat("first question")
        await first.close()
        second = _session(handler, "s1")
        restored = second.restore_transcript()
        await second.chat("second question")
        await second.close()
        return restored

    restored = asyncio.run(main())
    assert restored == 2
    assert [m["content"] for m in seen[1]] == [
        "first question", "answer", "second question"]


def test_session_id_rotates_unless_continuing():
    class Fresh:
        resume = None
        continue_session = False

    class Continue:
        resume = None
        continue_session = True

    first, source = __main__._cli_session(Fresh())
    second, source2 = __main__._cli_session(Fresh())
    assert first != second
    assert source is None and source2 is None
    third, previous = __main__._cli_session(Continue())
    assert previous == second
    assert third != second


def test_explicit_resume_forks_a_new_session():
    class Explicit:
        resume = "abc123"
        continue_session = False

    session_id, source = __main__._cli_session(Explicit())
    assert session_id != "abc123"
    assert source == "abc123"
    assert __main__._cli_resume(Explicit()) is True


def test_parser_exposes_resume_flags():
    parser = __main__._build_parser()
    headless = parser.parse_args(["-p", "hi", "--continue"])
    assert headless.continue_session is True
    assert headless.resume is None
    assert __main__._cli_resume(headless) is True

    tui = parser.parse_args(["tui", "--resume", "s1"])
    assert tui.resume == "s1"
    assert __main__._cli_resume(tui) is True


def test_clear_rotates_session_id_and_keeps_the_old_transcript():
    async def main():
        session = _session(_answer, "s1")
        await session.chat("hi")
        session.save_transcript()
        assert agents.transcript_path("s1").exists()

        session.reset()
        assert session.conv_session_id != "s1"
        assert agents.transcript_path("s1").exists()
        assert session.transcript() == []

    asyncio.run(main())


def test_slash_resume_lists_and_loads_a_saved_session():
    from pyclaw import slash
    agents.save_transcript("old", [{"role": "user", "content": "hi"},
                                   {"role": "assistant", "content": "ok"}])

    async def main():
        session = _session(_answer, "fresh")

        listed = await slash.handle_slash("/resume", session)
        assert "old" in listed

        out = await slash.handle_slash("/resume old", session)
        assert "Resumed 2 messages" in out
        assert session.transcript() == [{"role": "user", "content": "hi"},
                                        {"role": "assistant", "content": "ok"}]
        assert session.conv_session_id != "old"

    asyncio.run(main())
