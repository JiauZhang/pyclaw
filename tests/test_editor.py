import pytest

import pyclaw.editor as editor


def test_visual_wins_over_editor(monkeypatch):
    monkeypatch.setenv('VISUAL', 'nano')
    monkeypatch.setenv('EDITOR', 'vi')
    assert editor.external_editor() == 'nano'


def test_editor_is_used_when_visual_is_unset(monkeypatch):
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.setenv('EDITOR', 'vi')
    assert editor.external_editor() == 'vi'


def test_a_known_editor_is_picked_from_the_path(monkeypatch):
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.delenv('EDITOR', raising=False)
    monkeypatch.setattr(editor.shutil, 'which',
                        lambda name: None if name == 'code' else f'/bin/{name}')
    assert editor.external_editor() == 'vi'


def test_no_editor_anywhere_reports_nothing(monkeypatch):
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.delenv('EDITOR', raising=False)
    monkeypatch.setattr(editor.shutil, 'which', lambda name: None)
    assert editor.external_editor() == ''


def test_gui_editors_are_told_to_wait(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv('VISUAL', 'code')
    monkeypatch.setattr(editor.subprocess, 'run',
                        lambda argv, check: calls.append(argv))
    target = tmp_path / 'AGENTS.md'
    assert editor.open_file(target) == 'code'
    assert calls == [['code', '-w', str(target)]]


def test_a_plain_editor_is_run_as_is(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setenv('VISUAL', 'vi')
    monkeypatch.setattr(editor.subprocess, 'run',
                        lambda argv, check: calls.append(argv))
    editor.open_file(tmp_path / 'AGENTS.md')
    assert calls == [['vi', str(tmp_path / 'AGENTS.md')]]


def test_nothing_runs_without_an_editor(monkeypatch, tmp_path):
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.delenv('EDITOR', raising=False)
    monkeypatch.setattr(editor.shutil, 'which', lambda name: None)
    monkeypatch.setattr(editor.subprocess, 'run',
                        lambda *a, **k: pytest.fail('no editor should run'))
    assert editor.open_file(tmp_path / 'AGENTS.md') == ''
