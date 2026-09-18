import time
from pathlib import Path

import pytest

from pyclaw import welcome


def _markup_lines_are_valid(lines) -> bool:
    from textual.content import Content
    Content.from_markup("\n".join(lines))
    return True


def test_home_path_shortens_the_home_directory():
    home = Path.home()
    assert welcome.home_path(str(home)) == "~"
    assert welcome.home_path(str(home / "code" / "pyclaw")) == "~/code/pyclaw"
    assert welcome.home_path("/tmp/elsewhere") == "/tmp/elsewhere"
    assert welcome.home_path("") == ""


def test_relative_time_reads_in_words():
    assert welcome.relative_time(0) == "0 seconds ago"
    assert welcome.relative_time(30) == "30 seconds ago"
    assert welcome.relative_time(60) == "1 minute ago"
    assert welcome.relative_time(3_600) == "1 hour ago"
    assert welcome.relative_time(86_400 * 3) == "3 days ago"
    assert welcome.relative_time(604_800) == "1 week ago"


def test_welcome_message_falls_back_without_a_username():
    assert welcome.welcome_message() == "Welcome back!"
    assert welcome.welcome_message("") == "Welcome back!"
    assert welcome.welcome_message("x" * 21) == "Welcome back!"
    assert welcome.welcome_message("Jiau") == "Welcome back Jiau!"


def test_onboarding_steps_follow_the_workspace_state(tmp_path):
    steps = welcome.onboarding_steps(str(tmp_path))
    assert [step["enabled"] for step in steps] == [True, False]

    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("hi", encoding="utf-8")
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
    project = tmp_path / "project"
    project.mkdir()
    (project / welcome.MEMORY_FILE_NAME).write_text("notes", encoding="utf-8")
    feed = welcome.tips_feed(welcome.onboarding_steps(str(project)),
                             str(project))
    assert feed["title"] == "Tips for getting started"
    assert feed["lines"][0]["text"].startswith(welcome.COMPLETE_TICK + " ")
    assert "No recent activity" not in feed["lines"][0]["text"]

    empty = tmp_path / "empty"
    empty.mkdir()
    feed = welcome.tips_feed(welcome.onboarding_steps(str(empty)), str(empty))
    assert feed["lines"][0]["text"].startswith("Ask PyClaw")


def test_activity_feed_reports_the_empty_state_and_the_resume_footer():
    feed = welcome.activity_feed([])
    assert feed["lines"] == []
    assert feed["footer"] is None
    assert feed["empty"] == "No recent activity"

    feed = welcome.activity_feed([{"text": "do a thing",
                                   "timestamp": "2 hours ago"}])
    assert feed["footer"] == "/resume for more"


def test_recent_activity_reads_saved_sessions_and_skips_the_current_one(
        monkeypatch):
    from pyclaw import agents
    now = 1_800_000_000.0
    transcripts = {
        "mine": [{"role": "user", "content": "the current session"}],
        "older": [{"role": "user", "content": "fix the PDF outline jump"}],
        "oldest": [{"role": "user", "content": "add multi-agent alignment"}],
    }
    monkeypatch.setattr(agents, "list_sessions", lambda: [
        {"id": "mine", "modified": now - 10, "messages": 2},
        {"id": "older", "modified": now - 7_200, "messages": 4},
        {"id": "oldest", "modified": now - 90_000, "messages": 6},
    ])
    monkeypatch.setattr(agents, "load_entries",
                        lambda sid: transcripts.get(sid, []))
    found = welcome.recent_activity(exclude="mine", now=now)
    assert [(entry["text"], entry["timestamp"]) for entry in found] == [
        ("fix the PDF outline jump", "2 hours ago"),
        ("add multi-agent alignment", "1 day ago")]


def test_recent_activity_skips_sessions_without_a_prompt(monkeypatch):
    from pyclaw import agents
    monkeypatch.setattr(agents, "list_sessions", lambda: [
        {"id": "a", "modified": time.time(), "messages": 1}])
    monkeypatch.setattr(agents, "load_entries", lambda sid: [])
    assert welcome.recent_activity() == []


def test_feeds_only_appear_while_onboarding_or_on_a_new_version(
        monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(welcome, "recent_activity", lambda **kw: [])

    monkeypatch.setattr(welcome, "settings",
                        lambda: {"seen": 0, "lastVersion": "0.0.5"})
    feeds, shown = welcome.feeds_for(cwd=str(project), version="0.0.5")
    assert shown is True
    assert [feed["title"] for feed in feeds] == ["Tips for getting started",
                                                 "Recent activity"]

    monkeypatch.setattr(welcome, "settings",
                        lambda: {"seen": 4, "lastVersion": "0.0.5"})
    feeds, shown = welcome.feeds_for(cwd=str(project), version="0.0.5")
    assert feeds == [] and shown is False

    monkeypatch.setattr(welcome, "settings",
                        lambda: {"seen": 4, "lastVersion": "0.0.1"})
    feeds, shown = welcome.feeds_for(cwd=str(project), version="0.0.5")
    assert shown is False
    assert [feed["title"] for feed in feeds] == ["Recent activity"]


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
    assert "Welcome back!" in text
    assert "PyClaw" in text and "v0.0.5" in text
    assert "hunyuan-lite" in text and "tencent" in text
    assert "~/code" in text
    assert _markup_lines_are_valid(text.split("\n"))


def test_feed_markup_escapes_session_text():
    feed = welcome.activity_feed([
        {"text": "has [/bold] and [x] markers", "timestamp": "1 hour ago"}])
    lines = welcome.render_feeds([feed], "#FF6B80")
    assert _markup_lines_are_valid(lines)
    assert "\\[/bold]" in "\n".join(lines)


def test_feed_divider_is_drawn_between_feeds_and_the_width_is_capped():
    wide = welcome.activity_feed([
        {"text": "x" * 400, "timestamp": "1 hour ago"}])
    narrow = welcome.tips_feed([], "/tmp")
    assert narrow is None
    lines = welcome.render_feeds([wide, welcome.activity_feed([])], "#FF6B80")
    dividers = [line for line in lines if welcome.DIVIDER_CHAR in line]
    assert dividers
    assert all(line.count(welcome.DIVIDER_CHAR) == welcome.MAX_FEED_WIDTH
               for line in dividers)
    assert max(len(line) for line in lines) < 400
