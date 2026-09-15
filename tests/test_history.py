import asyncio

from chatchat.client import MockClient
from chatchat.team import Team

from pyclaw import __main__, agents


def test_transcript_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ok"}]
    agents.save_transcript("s1", messages)
    assert agents.load_transcript("s1") == messages
    assert agents.load_transcript("missing") == []


def test_transcript_skips_broken_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    path = agents.transcript_path("s1")
    path.write_text('{"role": "user", "content": "a"}\nnot json\n\n',
                    encoding="utf-8")
    assert agents.load_transcript("s1") == [{"role": "user", "content": "a"}]


def test_transcript_appends_and_chains_uuids(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
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


def test_transcript_rewrites_when_history_is_compacted(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    agents.save_transcript("s1", [{"role": "user", "content": "a"},
                                  {"role": "assistant", "content": "b"}])
    agents.save_transcript("s1", [{"role": "user", "content": "summary"}])
    entries = agents.load_entries("s1")
    assert [e["content"] for e in entries] == ["summary"]
    assert entries[0]["parentUuid"] is None


def test_session_persists_and_restores_history(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    seen = []

    async def handler(messages, tools=None, *, stream_cb=None):
        seen.append([dict(m) for m in messages])
        return "answer"

    def make_session():
        team = Team("t1", client_factory=lambda inst: MockClient(handler=handler))
        return agents.Session(team, session_id="s1")

    async def main():
        first = make_session()
        await first.chat("first question")
        await first.close()
        second = make_session()
        restored = second.restore_transcript()
        await second.chat("second question")
        await second.close()
        return restored

    restored = asyncio.run(main())
    assert restored == 2
    assert [m["content"] for m in seen[1]] == [
        "first question", "answer", "second question"]


def test_session_id_rotates_unless_continuing(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)

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


def test_explicit_resume_forks_a_new_session(tmp_path, monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)

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


def test_clear_rotates_session_id_and_keeps_the_old_transcript(tmp_path,
                                                              monkeypatch):
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)

    async def handler(messages, tools=None, *, stream_cb=None):
        return "answer"

    async def main():
        team = Team("t1",
                    client_factory=lambda inst: MockClient(handler=handler))
        session = agents.Session(team, session_id="s1")
        await session.chat("hi")
        session.save_transcript()
        assert agents.transcript_path("s1").exists()

        session.reset()
        assert session.conv_session_id != "s1"
        assert agents.transcript_path("s1").exists()
        assert session.transcript() == []

    asyncio.run(main())


def test_slash_resume_lists_and_loads_a_saved_session(tmp_path, monkeypatch):
    from pyclaw import slash
    monkeypatch.setattr(agents, "_logs_dir", lambda: tmp_path)
    agents.save_transcript("old", [{"role": "user", "content": "hi"},
                                   {"role": "assistant", "content": "ok"}])

    async def handler(messages, tools=None, *, stream_cb=None):
        return "answer"

    async def main():
        team = Team("t1",
                    client_factory=lambda inst: MockClient(handler=handler))
        session = agents.Session(team, session_id="fresh")

        listed = await slash.handle_slash("/resume", session)
        assert "old" in listed

        out = await slash.handle_slash("/resume old", session)
        assert "Resumed 2 messages" in out
        assert session.transcript() == [{"role": "user", "content": "hi"},
                                        {"role": "assistant", "content": "ok"}]
        assert session.conv_session_id != "old"

    asyncio.run(main())
