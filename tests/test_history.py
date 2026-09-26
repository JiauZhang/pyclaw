import asyncio

import pytest

from chatchat.client import MockClient
from chatchat.team.team import Team

from pyclaw import __main__, session as session_mod
from pyclaw.session import store as session_store
from pyclaw.session.store import load_entries, load_transcript, save_transcript, transcript_path


@pytest.fixture(autouse=True)
def _logs_under_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_logs_dir", lambda: tmp_path)


async def _answer(messages, tools=None, *, stream_cb=None):
    return "answer"


def _session(handler, session_id):
    team = Team("t1", client_factory=lambda inst, model=None:
                MockClient(handler=handler, model=model))
    return session_mod.Session(team, session_id=session_id)


def test_transcript_roundtrip():
    messages = [{"role": "user", "content": "hi"},
                {"role": "assistant", "content": "ok"}]
    session_store.save_transcript("s1", messages)
    assert session_store.load_transcript("s1") == messages
    assert session_store.load_transcript("missing") == []


def test_transcript_skips_broken_lines():
    path = session_store.transcript_path("s1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"role": "user", "content": "a"}\nnot json\n\n',
                    encoding="utf-8")
    assert session_store.load_transcript("s1") == [{"role": "user", "content": "a"}]


def test_transcript_appends_and_chains_uuids():
    first = [{"role": "user", "content": "a"},
             {"role": "assistant", "content": "b"}]
    session_store.save_transcript("s1", first)
    path = session_store.transcript_path("s1")
    size_after_first = path.stat().st_size

    session_store.save_transcript("s1", first + [{"role": "user", "content": "c"}])
    entries = session_store.load_entries("s1")
    assert [e["content"] for e in entries] == ["a", "b", "c"]
    assert entries[0]["parentUuid"] is None
    assert entries[1]["parentUuid"] == entries[0]["uuid"]
    assert entries[2]["parentUuid"] == entries[1]["uuid"]
    assert path.stat().st_size > size_after_first

    session_store.save_transcript("s1", first + [{"role": "user", "content": "c"}])
    assert len(session_store.load_entries("s1")) == 3


def test_transcript_rewrites_when_history_is_compacted():
    session_store.save_transcript("s1", [{"role": "user", "content": "a"},
                                  {"role": "assistant", "content": "b"}])
    session_store.save_transcript("s1", [{"role": "user", "content": "summary"}])
    entries = session_store.load_entries("s1")
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
        assert session_store.transcript_path("s1").exists()

        session.reset()
        assert session.conv_session_id != "s1"
        assert session_store.transcript_path("s1").exists()
        assert session.transcript() == []

    asyncio.run(main())


def test_slash_resume_lists_and_loads_a_saved_session():
    from pyclaw import slash
    session_store.save_transcript("old", [{"role": "user", "content": "hi"},
                                   {"role": "assistant", "content": "ok"}])

    async def main():
        session = _session(_answer, "fresh")

        session_store.rename_session("old", "the parser thread")
        listed = await slash.handle_slash("/resume", session)
        assert "old" in listed
        assert "the parser thread" in listed

        out = await slash.handle_slash("/resume old", session)
        assert "Resumed 2 messages" in out
        assert session.transcript() == [{"role": "user", "content": "hi"},
                                        {"role": "assistant", "content": "ok"}]
        assert session.conv_session_id != "old"

    asyncio.run(main())


def _saved(session_id, title):
    session_store.save_transcript(
        session_id, [{"role": "user", "content": "hi"},
                     {"role": "assistant", "content": "ok"}])
    if title:
        session_store.rename_session(session_id, title)


def test_resuming_by_name_loads_the_named_conversation():
    from pyclaw import slash
    _saved("deadbeef", "git-ssh-key")

    async def main():
        session = _session(_answer, "fresh")
        return await slash.handle_slash("/resume git-ssh-key", session)

    assert "Resumed 2 messages" in asyncio.run(main())


def test_a_name_that_belongs_to_two_conversations_asks_for_the_id():
    from pyclaw import slash
    _saved("aaaa1111", "same name")
    _saved("bbbb2222", "same name")

    async def main():
        session = _session(_answer, "fresh")
        return await slash.handle_slash("/resume same name", session), session

    out, session = asyncio.run(main())
    assert "aaaa1111" in out and "bbbb2222" in out
    assert session.transcript() == []


def test_a_name_matching_nothing_says_so():
    from pyclaw import slash
    _saved("deadbeef", "git-ssh-key")

    async def main():
        session = _session(_answer, "fresh")
        return await slash.handle_slash("/resume ssh-keys", session)

    assert "No saved conversation named" in asyncio.run(main())


def test_resume_on_the_command_line_takes_a_name_too():
    _saved("deadbeef", "git-ssh-key")

    class Explicit:
        resume = "git-ssh-key"
        continue_session = False

    assert __main__._cli_session(Explicit())[1] == "deadbeef"


def test_reading_a_conversation_that_is_not_there_creates_nothing(tmp_path):
    assert load_transcript("not-a-session") == []
    assert session_store.session_meta("not-a-session") == {}
    assert session_store.match_sessions("not-a-session") == []
    assert not (tmp_path / "not-a-session").exists()


async def _talked_session(session_id='talk'):
    """A session that has one exchange behind it, on disk."""
    session = _session(_answer, session_id)
    await session.chat('hi')
    return session


def test_renaming_names_the_conversation_for_the_resume_list():
    async def main():
        session = await _talked_session()
        session.rename('  parser   work  ')
        return session.title, session_store.list_sessions()

    title, sessions = asyncio.run(main())
    assert title == 'parser work'
    assert sessions[0]['title'] == 'parser work'


def test_a_conversation_cannot_be_named_nothing():
    async def main():
        session = await _talked_session()
        try:
            session.rename('   ')
        except ValueError as exc:
            return str(exc)

    assert 'name' in asyncio.run(main())


def test_branching_copies_the_conversation_and_moves_into_it():
    async def main():
        session = await _talked_session()
        original = session.conv_session_id
        fork = session.branch()
        return original, fork, session.conv_session_id, session_store.load_entries(fork['id'])

    original, fork, current, entries = asyncio.run(main())
    assert current == fork['id'] != original
    assert fork['messages'] == len(entries) > 0
    assert fork['title'] == 'hi (Branch)'
    assert all(entry['forkedFrom']['sessionId'] == original
               for entry in entries)
    assert session_store.load_entries(original)


def test_a_second_branch_of_the_same_conversation_is_numbered():
    async def main():
        session = await _talked_session()
        session.branch()
        return session.branch('parser work')

    fork = asyncio.run(main())
    assert fork['title'] == 'parser work (Branch)'


def test_branching_nothing_refuses():
    async def main():
        session = _session(_answer, 'empty')
        session._team.lead.messages = []
        try:
            session.branch()
        except ValueError as exc:
            return str(exc)

    assert 'no conversation' in asyncio.run(main())


def test_the_rename_and_branch_commands_reach_the_session():
    async def main():
        from pyclaw import slash
        session = await _talked_session()
        renamed = await slash.handle_slash('/rename parser work', session)
        branched = await slash.handle_slash('/branch', session)
        named = await slash.handle_slash('/branch the other way', session)
        return renamed, branched, named

    renamed, branched, named = asyncio.run(main())
    assert 'parser work' in renamed
    assert 'You are now in the branch' in branched
    assert '/resume talk' in branched
    assert '"the other way (Branch)"' in named
