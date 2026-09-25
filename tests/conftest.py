import pytest


@pytest.fixture(autouse=True)
def _isolated_runtime_sinks():
    from chatchat.hooks import events
    saved = list(events._runtime_sinks)
    yield
    events._runtime_sinks[:] = saved


@pytest.fixture(autouse=True)
def _empty_task_registry():
    from pyclaw.task_registry import registry
    registry().clear()
    yield
    registry().clear()


@pytest.fixture(autouse=True)
def _no_config_writes(monkeypatch):
    from pyclaw import config
    monkeypatch.setattr(config, 'save', lambda value: None)


@pytest.fixture(autouse=True)
def _no_status_line(monkeypatch):
    from pyclaw import statusline
    monkeypatch.setattr(statusline, 'user_command', lambda: '')


@pytest.fixture(autouse=True)
def _one_completion_verb(monkeypatch):
    from pyclaw.tui import PyClawApp
    monkeypatch.setattr(PyClawApp, '_completion_verb', lambda self: 'Handled')


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    from pyclaw import config
    monkeypatch.setattr(config, '__config_file__', tmp_path / 'config.json')
    config.reload()
    yield config
    config.reload()


@pytest.fixture(autouse=True)
def _home_dir_is_a_temp_folder(tmp_path, monkeypatch):
    """Nothing in a test may write into the real ~/.pyclaw."""
    from pyclaw import home

    folder = tmp_path / 'pyclaw-home'
    folder.mkdir()
    monkeypatch.setenv('PYCLAW_HOME', str(folder))
    monkeypatch.setenv('CHATCHAT_SECRET_FILE', str(folder / 'secrets.json'))
    monkeypatch.setattr(home, '__pyclaw_home__', str(folder))
    return folder
