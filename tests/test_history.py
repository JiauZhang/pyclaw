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

    first = __main__._cli_session_id(Fresh())
    second = __main__._cli_session_id(Fresh())
    assert first != second
    assert __main__._cli_session_id(Continue()) == second


def test_session_id_uses_explicit_resume():
    class Explicit:
        resume = "abc123"
        continue_session = False

    assert __main__._cli_session_id(Explicit()) == "abc123"
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
