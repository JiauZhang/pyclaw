import time
from pathlib import Path

from textual.content import Content

from pyclaw import welcome
from pyclaw.session import store as session_store


def _markup_is_valid(lines):
    Content.from_markup("\n".join(lines))


def _dir_with(tmp_path, name, *files):
    path = tmp_path / name
    path.mkdir()
    for file in files:
        (path / file).write_text("hi", encoding="utf-8")
    return path


def _saved_sessions(monkeypatch, sessions, transcripts):
    monkeypatch.setattr(session_store, "list_sessions", lambda: sessions)
    monkeypatch.setattr(session_store, "load_entries",
                        lambda sid: transcripts.get(sid, []))


def test_home_path_shortens_the_home_directory():
    home = Path.home()
    for path, shown in ((str(home), "~"),
                        (str(home / "code" / "pyclaw"), "~/code/pyclaw"),
                        ("/tmp/elsewhere", "/tmp/elsewhere"),
                        ("", "")):
        assert welcome.home_path(path) == shown


def test_relative_time_reads_in_words():
    for seconds, words in ((0, "0 seconds ago"), (30, "30 seconds ago"),
                           (60, "1 minute ago"), (3_600, "1 hour ago"),
                           (86_400 * 3, "3 days ago"), (604_800, "1 week ago")):
        assert welcome.relative_time(seconds) == words


def test_welcome_message_falls_back_without_a_username():
    assert welcome.welcome_message() == "Good to see you."
    assert welcome.welcome_message("") == "Good to see you."
    assert welcome.welcome_message("x" * 21) == "Good to see you."
    assert welcome.welcome_message("Jiau") == "Good to see you, Jiau."


def test_onboarding_steps_follow_the_workspace_state(tmp_path):
    empty = _dir_with(tmp_path, 'empty')
    assert [step["enabled"] for step in welcome.onboarding_steps(str(empty))] \
        == [True, False]

    project = _dir_with(tmp_path, "project", "README.md")
    steps = welcome.onboarding_steps(str(project))
    assert [step["enabled"] for step in steps] == [False, True]
    assert steps[1]["complete"] is False
    assert welcome.MEMORY_FILE_NAME in steps[1]["text"]

    (project / welcome.MEMORY_FILE_NAME).write_text("notes", encoding="utf-8")
    steps = welcome.onboarding_steps(str(project))
    assert steps[1]["complete"] is True
    assert welcome.onboarding_pending(steps) is False


def test_home_warning_only_in_the_home_directory(tmp_path):
    assert welcome.home_warning(str(tmp_path)) is None
    note = welcome.home_warning(str(Path.home()))
    assert note and note.startswith("Note: You have launched PyClaw")


def test_tips_feed_marks_completed_steps_and_adds_the_home_note(tmp_path):
    project = _dir_with(tmp_path, "project", welcome.MEMORY_FILE_NAME)
    feed = welcome.tips_feed(welcome.onboarding_steps(str(project)),
                             str(project))
    assert feed["title"] == "Getting oriented"
    assert feed["lines"][0]["text"].startswith(welcome.COMPLETE_TICK + " ")
    assert "No sessions yet" not in feed["lines"][0]["text"]

    empty = _dir_with(tmp_path, "empty")
    feed = welcome.tips_feed(welcome.onboarding_steps(str(empty)), str(empty))
    assert feed["lines"][0]["text"].startswith("Ask PyClaw")


def test_activity_feed_reports_the_empty_state_and_the_resume_footer():
    feed = welcome.activity_feed([])
    assert feed["lines"] == []
    assert feed["footer"] is None
    assert feed["empty"] == "No sessions yet"

    feed = welcome.activity_feed([{"text": "do a thing",
                                   "timestamp": "2 hours ago"}])
    assert feed["footer"] == "/resume for more"


def test_recent_activity_reads_saved_sessions_and_skips_the_current_one(
        monkeypatch):
    now = 1_800_000_000.0
    transcripts = {
        "mine": [{"role": "user", "content": "the current session"}],
        "older": [{"role": "user", "content": "fix the PDF outline jump"}],
        "oldest": [{"role": "user", "content": "add multi-agent alignment"}],
    }
    _saved_sessions(monkeypatch, [
        {"id": "mine", "modified": now - 10, "messages": 2},
        {"id": "older", "modified": now - 7_200, "messages": 4},
        {"id": "oldest", "modified": now - 90_000, "messages": 6},
    ], transcripts)
    found = welcome.recent_activity(exclude="mine", now=now)
    assert [(entry["text"], entry["timestamp"]) for entry in found] == [
        ("fix the PDF outline jump", "2 hours ago"),
        ("add multi-agent alignment", "1 day ago")]


def test_recent_activity_skips_sessions_without_a_prompt(monkeypatch):
    _saved_sessions(monkeypatch, [
        {"id": "a", "modified": time.time(), "messages": 1}], {})
    assert welcome.recent_activity() == []


def test_feeds_only_appear_while_onboarding_or_on_a_new_version(
        monkeypatch, tmp_path):
    project = _dir_with(tmp_path, "project", "README.md")
    monkeypatch.setattr(welcome, "recent_activity", lambda **kw: [])

    for settings, titles, shown in (
            ({"seen": 0, "lastVersion": "0.0.5"},
             ["Getting oriented", "Last sessions"], True),
            ({"seen": 4, "lastVersion": "0.0.5"}, [], False),
            ({"seen": 4, "lastVersion": "0.0.1"}, ["Last sessions"], False)):
        monkeypatch.setattr(welcome, "settings",
                            lambda s=settings: dict(s))
        feeds, got = welcome.feeds_for(cwd=str(project), version="0.0.5")
        assert [feed["title"] for feed in feeds] == titles
        assert got is shown


def test_remember_skips_the_write_when_nothing_changes(monkeypatch):
    from pyclaw import config
    saved = []
    state = {"welcome": {"seen": 2, "lastVersion": "0.0.5"}}
    monkeypatch.setattr(config, "load", lambda: dict(state))
    monkeypatch.setattr(config, "save", lambda value: saved.append(value))

    welcome.remember(seen_onboarding=False, version="0.0.5")
    assert saved == []

    welcome.remember(seen_onboarding=True, version="0.0.5")
    assert saved[0]["welcome"] == {"seen": 3, "lastVersion": "0.0.5"}

    welcome.remember(seen_onboarding=False, version="0.0.6")
    assert saved[1]["welcome"]["lastVersion"] == "0.0.6"


def test_block_reports_the_version_model_provider_and_cwd():
    text = welcome.block(version="0.0.5", model="hunyuan-lite",
                         provider="tencent", cwd=str(Path.home() / "code"),
                         feeds=[], brand="#FF6B80")
    assert "Good to see you." in text
    assert "PyClaw" in text and "v0.0.5" in text
    assert "hunyuan-lite" in text and "tencent" in text
    assert "~/code" in text
    _markup_is_valid(text.split("\n"))


def test_feed_markup_escapes_session_text():
    feed = welcome.activity_feed([
        {"text": "has [/bold] and [x] markers", "timestamp": "1 hour ago"}])
    lines = welcome.render_feeds([feed], "#FF6B80")
    _markup_is_valid(lines)
    assert "\\[/bold]" in "\n".join(lines)


def test_feed_divider_is_drawn_between_feeds_and_the_width_is_capped():
    wide = welcome.activity_feed([
        {"text": "x" * 400, "timestamp": "1 hour ago"}])
    assert welcome.tips_feed([], "/tmp") is None
    lines = welcome.render_feeds([wide, welcome.activity_feed([])], "#FF6B80")
    dividers = [line for line in lines if welcome.DIVIDER_CHAR in line]
    assert dividers
    assert all(line.count(welcome.DIVIDER_CHAR) == welcome.MAX_FEED_WIDTH
               for line in dividers)
    assert max(len(line) for line in lines) < 400
