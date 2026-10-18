from pyclaw.tui import history


def test_append_and_load_are_scoped_by_project(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "path", lambda: tmp_path / "history.jsonl")
    history.append("/repo", "git push")
    history.append("/repo", "git status")
    history.append("/other", "git push")
    assert history.load("/repo") == ["git push", "git status"]
    assert history.load("/other") == ["git push"]


def test_resubmitting_moves_the_prompt_to_the_end(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "path", lambda: tmp_path / "history.jsonl")
    history.append("/repo", "first")
    history.append("/repo", "second")
    history.append("/repo", "first")
    assert history.load("/repo") == ["second", "first"]


def test_a_broken_line_does_not_kill_the_file(tmp_path, monkeypatch):
    file = tmp_path / "history.jsonl"
    file.write_text('{"display": "kept", "project": "/repo"}\nnot json\n', encoding="utf-8")
    monkeypatch.setattr(history, "path", lambda: file)
    assert history.load("/repo") == ["kept"]
    history.append("/repo", "new")
    assert history.load("/repo") == ["kept", "new"]
