import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler

import pytest

from pyclaw import __main__


def _args(**kw):
    args = argparse.Namespace(
        provider=None, model=None, channels=None, port=None, host=None, log_level="INFO"
    )
    for k, v in kw.items():
        setattr(args, k, v)
    return args


def test_apply_overrides_only_set_values():
    config = {"provider": "p0", "model": "m0", "enabled_channels": ["wechat"]}
    modified = __main__._apply_overrides(config, _args(model="m1"))

    assert modified is True
    assert config["provider"] == "p0"
    assert config["model"] == "m1"
    assert config["enabled_channels"] == ["wechat"]


def test_apply_overrides_none_no_change():
    config = {"provider": "p0", "model": "m0", "enabled_channels": ["wechat"]}
    modified = __main__._apply_overrides(config, _args())

    assert modified is False
    assert config["provider"] == "p0"


def test_apply_overrides_all():
    config = {"provider": "p0", "model": "m0", "enabled_channels": ["wechat"]}
    modified = __main__._apply_overrides(
        config, _args(provider="p1", model="m1", channels=["web", "wechat"])
    )

    assert modified is True
    assert config["provider"] == "p1"
    assert config["model"] == "m1"
    assert config["enabled_channels"] == ["web", "wechat"]


def test_session_parsers_carry_the_flags_that_steer_a_run():
    for argv, team in ((["-p", "hi"], False), (["tui"], True)):
        args = __main__._build_parser().parse_args(
            argv + ["--allowed-tools", "Bash(git push:*)",
                    "--disallowed-tools", "Bash(curl:*)",
                    "--ask", "Bash(docker:*)"]
            + (["--use-team"] if team else []))
        assert args.allowed_tools == ["Bash(git push:*)"]
        assert args.disallowed_tools == ["Bash(curl:*)"]
        assert args.ask == ["Bash(docker:*)"]
        assert args.use_team is team
        assert __main__._build_parser().parse_args(argv).use_team is False
        assert args.agents is None
        given = __main__._build_parser().parse_args(
            argv + ['--agents', '{"reviewer": {"description": "d"}}'])
        assert given.agents == '{"reviewer": {"description": "d"}}'


def test_finalize_args_keeps_existing_log_level():
    ns = argparse.Namespace(command="serve", log_level="DEBUG")
    out = __main__._finalize_args(ns)
    assert out.log_level == "DEBUG"


def test_parse_args_without_subcommand_has_no_log_level():
    args = __main__._build_parser().parse_args([])
    assert not hasattr(args, "log_level")
    finalized = __main__._finalize_args(args)
    assert finalized.log_level == "INFO"


def test_finalize_args_fills_serve_fields_with_none():
    args = __main__._build_parser().parse_args([])
    finalized = __main__._finalize_args(args)
    assert (finalized.provider, finalized.model, finalized.channels, finalized.port, finalized.host) == (
        None, None, None, None, None,
    )


class FakeGateway:
    def __init__(self, *a, **k):
        pass

    async def start(self):
        return

    async def shutdown(self):
        return


def test_start_server_runs_with_default_args(monkeypatch):
    captured = {}

    def fake_gateway(config, app_config=None):
        captured["config"] = config
        return FakeGateway()

    monkeypatch.setattr(__main__, "GatewayServer", fake_gateway)
    monkeypatch.setattr(__main__, "load_config", lambda: {"provider": "p", "model": "m", "enabled_channels": ["wechat"]})
    monkeypatch.setattr(__main__, "save_config", lambda c: None)

    args = __main__._finalize_args(__main__._build_parser().parse_args([]))
    asyncio.run(__main__.start_server(args))
    assert captured["config"].provider == "p"


def _strip_root_handlers():
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers = [h for h in root.handlers
                     if not isinstance(h, RotatingFileHandler)]
    return saved


def test_tui_logging_never_writes_to_stdout():
    root = logging.getLogger()
    saved = _strip_root_handlers()
    try:
        before = {id(h) for h in root.handlers}
        __main__.setup_logging("INFO", console=False)
        added = [h for h in root.handlers if id(h) not in before]
        assert added
        assert all(isinstance(h, RotatingFileHandler) for h in added)
    finally:
        root.handlers = saved


def test_setup_logging_is_idempotent_and_silences_noisy_loggers():
    root = logging.getLogger()
    saved = _strip_root_handlers()
    try:
        __main__.setup_logging("INFO")
        count = len([h for h in root.handlers])
        __main__.setup_logging("INFO")
        assert len(root.handlers) == count
        for name in ("asyncio", "markdown_it", "httpx"):
            assert logging.getLogger(name).level == logging.WARNING
    finally:
        root.handlers = saved
        for name in __main__._QUIET_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)


def test_setup_logging_returns_the_log_path():
    root = logging.getLogger()
    saved = _strip_root_handlers()
    try:
        path = __main__.setup_logging("INFO", console=False)
        assert str(path).endswith("pyclaw.log")
    finally:
        root.handlers = saved


def test_session_commands_start_logging_from_the_real_parser(monkeypatch):
    """run_tui / run_headless must not reach for flags the parser lacks.

    Regression: both called `setup_logging(args.log_level, console=False)`
    while neither `pyclaw tui` nor `pyclaw -p` defines --log-level (only the
    serve/rebind subparsers do), so the TUI died with AttributeError before
    it painted anything. Hand-built namespaces in other tests hid it.
    """
    from chatchat.hooks import events

    calls = []
    saved_handlers = _strip_root_handlers()
    saved_sinks = list(events._runtime_sinks)
    monkeypatch.setattr(__main__, "setup_logging",
                        lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(__main__, "load_config", lambda: {})
    try:
        headless = __main__._build_parser().parse_args(["-p", "hi"])
        assert not hasattr(headless, "log_level")
        with pytest.raises(SystemExit):
            asyncio.run(__main__.run_headless(headless))

        tui = __main__._build_parser().parse_args(["tui"])
        assert not hasattr(tui, "log_level")
        with pytest.raises(SystemExit):
            __main__.run_tui(tui)
    finally:
        logging.getLogger().handlers = saved_handlers
        events._runtime_sinks[:] = saved_sinks

    assert len(calls) == 2
    assert all(a == () and kw == {"console": False} for a, kw in calls)


def test_the_hook_event_flag_reaches_the_tui(monkeypatch):
    started = {}

    class _App:

        _exit_note = ''
        _startup_error = ''

        def __init__(self, **kw):
            started.update(kw)

        def run(self):
            return None

    monkeypatch.setattr(__main__, "load_config",
                        lambda: {"provider": "p", "model": "m"})
    monkeypatch.setattr(__main__, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr("pyclaw.tui.PyClawApp", _App)
    monkeypatch.setattr(__main__, "build_team", lambda *a, **kw: object())
    args = __main__._build_parser().parse_args(["tui", "--include-hook-events"])
    assert args.include_hook_events is True
    __main__.run_tui(args)
    assert started["hook_events"] is True

    args = __main__._build_parser().parse_args(["tui"])
    assert args.include_hook_events is False
    __main__.run_tui(args)
    assert started["hook_events"] is False


def test_what_became_of_the_worktree_is_said_once_the_screen_is_gone(
        monkeypatch, capsys):
    class _App:

        _exit_note = 'Back at /repo. The worktree was removed.'
        _startup_error = ''

        def __init__(self, **kw):
            pass

        def run(self):
            return None

    monkeypatch.setattr(__main__, "load_config",
                        lambda: {"provider": "p", "model": "m"})
    monkeypatch.setattr(__main__, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr("pyclaw.tui.PyClawApp", _App)
    monkeypatch.setattr(__main__, "build_team", lambda *a, **kw: object())
    __main__.run_tui(__main__._build_parser().parse_args(["tui"]))
    assert 'The worktree was removed.' in capsys.readouterr().out


def test_a_worktree_that_cannot_be_opened_stops_the_run(monkeypatch, capsys):
    class _App:

        _exit_note = ''
        _startup_error = 'Error: this worktree could not be made'

        def __init__(self, **kw):
            pass

        def run(self):
            return None

    monkeypatch.setattr(__main__, "load_config",
                        lambda: {"provider": "p", "model": "m"})
    monkeypatch.setattr("pyclaw.tui.PyClawApp", _App)
    monkeypatch.setattr(__main__, "build_team", lambda *a, **kw: object())
    args = __main__._build_parser().parse_args(["tui", "--worktree", "side"])
    with pytest.raises(SystemExit) as exit_info:
        __main__.run_tui(args)
    assert exit_info.value.code == 1
    assert 'could not be made' in capsys.readouterr().err
