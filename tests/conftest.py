import pytest
from chatchat.hooks import events
from pyclaw.task_registry import registry
from pyclaw import config
from pyclaw import statusline
from pyclaw.tui import PyClawApp
import json
import os
import tempfile
from pyclaw import home


@pytest.fixture(autouse=True)
def _isolated_runtime_sinks():

    saved = list(events._runtime_sinks)
    yield
    events._runtime_sinks[:] = saved


@pytest.fixture(autouse=True)
def _empty_task_registry():

    registry().clear()
    yield
    registry().clear()


@pytest.fixture(autouse=True)
def _no_config_writes(monkeypatch):

    monkeypatch.setattr(config, "save", lambda value: None)


@pytest.fixture(autouse=True)
def _no_status_line(monkeypatch):

    monkeypatch.setattr(statusline, "user_command", lambda: "")


@pytest.fixture(autouse=True)
def _one_completion_verb(monkeypatch):

    monkeypatch.setattr(PyClawApp, "_completion_verb", lambda self: "Handled")


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):

    monkeypatch.setattr(config, "__config_file__", tmp_path / "config.json")
    config.reload()
    yield config
    config.reload()


@pytest.fixture(autouse=True)
def _home_dir_is_a_temp_folder(tmp_path, monkeypatch):
    """Nothing in a test may write into the real ~/.pyclaw."""

    folder = tmp_path / "pyclaw-home"
    folder.mkdir()
    monkeypatch.setenv("PYCLAW_HOME", str(folder))
    fd, secret_path = tempfile.mkstemp(prefix="chatchat-secrets-")
    with os.fdopen(fd, "w") as f:
        f.write(
            json.dumps(
                {
                    name: {"type": "openai", "base_url": f"https://{name}.test/v1", "api_key": "test-key"}
                    for name in ("agnes", "deepseek", "openai", "anthropic")
                }
            )
        )
    monkeypatch.setenv("CHATCHAT_SECRET_FILE", secret_path)
    monkeypatch.setattr(home, "__pyclaw_home__", str(folder))
    yield folder
    try:
        os.unlink(secret_path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _isolated_prompt_history(tmp_path, monkeypatch):
    """The TUI's prompt history lives in ~/.pyclaw/history.jsonl; tests must
    never read the real one (a user's history would leak into assertions)."""
    monkeypatch.setattr("pyclaw.tui.history.path", lambda: tmp_path / "history.jsonl")
