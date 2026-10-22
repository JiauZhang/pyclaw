import argparse
import asyncio
import logging
from logging.handlers import RotatingFileHandler

import pytest

from pyclaw import __main__
import pyclaw.config as config_module
from chatchat.hooks import events
import inspect
from pyclaw.team.builder import build_team as the_real_builder


def _args(**kw):
    args = argparse.Namespace(model=None, channels=None, port=None, host=None, log_level="INFO")
    for k, v in kw.items():
        setattr(args, k, v)
    return args


def test_apply_overrides_only_set_values():
    saved = {"model": "p0/m0", "enabled_channels": ["wechat"]}
    settings = __main__._apply_overrides(dict(saved), _args(model="p1/m1"))

    assert settings["model"] == "p1/m1"
    assert settings["enabled_channels"] == ["wechat"]
    assert saved["model"] == "p0/m0"


def test_apply_overrides_none_no_change():
    saved = {"model": "p0/m0", "enabled_channels": ["wechat"]}
    settings = __main__._apply_overrides(dict(saved), _args())

    assert settings == saved


def test_apply_overrides_all():
    saved = {"model": "p0/m0", "enabled_channels": ["wechat"]}
    settings = __main__._apply_overrides(dict(saved), _args(model="p1/m1", channels=["web", "wechat"]))

    assert settings["model"] == "p1/m1"
    assert settings["enabled_channels"] == ["web", "wechat"]
    assert saved["model"] == "p0/m0"


def test_start_server_does_not_persist_cli_overrides(monkeypatch):
    """serve's flags steer this run only: the saved config is what the TUI and
    every other surface read, and a serve invocation must not rewrite it."""
    saved_calls = []

    monkeypatch.setattr(__main__, "GatewayServer", lambda *a, **k: FakeGateway())
    monkeypatch.setattr(__main__, "load_config", lambda: {"model": "p/m", "enabled_channels": ["wechat"]})

    monkeypatch.setattr(config_module, "save", lambda c: saved_calls.append(c))

    args = __main__._finalize_args(__main__._build_parser().parse_args(["serve", "--model", "p/other", "--channels", "qq"]))
    asyncio.run(__main__.start_server(args))
    assert saved_calls == []


def test_session_parsers_carry_the_flags_that_steer_a_run():
    for argv, team in ((["-p", "hi"], False), ([], True)):
        args = __main__._build_parser().parse_args(
            argv
            + ["--allowed-tools", "Bash(git push:*)", "--disallowed-tools", "Bash(curl:*)", "--ask", "Bash(docker:*)"]
            + (["--use-team"] if team else [])
        )
        assert args.allowed_tools == ["Bash(git push:*)"]
        assert args.disallowed_tools == ["Bash(curl:*)"]
        assert args.ask == ["Bash(docker:*)"]
        assert args.use_team is team
        assert __main__._build_parser().parse_args(argv).use_team is False
        assert args.agents is None
        given = __main__._build_parser().parse_args(argv + ["--agents", '{"reviewer": {"description": "d"}}'])
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
    assert (finalized.model, finalized.channels, finalized.port, finalized.host) == (
        None,
        None,
        None,
        None,
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
    monkeypatch.setattr(__main__, "load_config", lambda: {"model": "p/m", "enabled_channels": ["wechat"]})

    args = __main__._finalize_args(__main__._build_parser().parse_args([]))
    asyncio.run(__main__.start_server(args))
    assert captured["config"].model == "p/m"


def _strip_root_handlers():
    root = logging.getLogger()
    saved = list(root.handlers)
    root.handlers = [h for h in root.handlers if not isinstance(h, RotatingFileHandler)]
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
    while neither bare `pyclaw` nor `pyclaw -p` defines --log-level (only the
    serve/rebind subparsers do), so the TUI died with AttributeError before
    it painted anything. Hand-built namespaces in other tests hid it.
    """

    calls = []
    saved_handlers = _strip_root_handlers()
    saved_sinks = list(events._runtime_sinks)
    monkeypatch.setattr(__main__, "setup_logging", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(__main__, "load_config", lambda: {})
    try:
        headless = __main__._build_parser().parse_args(["-p", "hi"])
        assert not hasattr(headless, "log_level")
        with pytest.raises(SystemExit):
            asyncio.run(__main__.run_headless(headless))

        tui = __main__._build_parser().parse_args([])
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
        _exit_note = ""
        _startup_error = ""

        def __init__(self, **kw):
            started.update(kw)

        def run(self):
            return None

    monkeypatch.setattr(__main__, "load_config", lambda: {"model": "p/m"})
    monkeypatch.setattr(__main__, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr("pyclaw.tui.PyClawApp", _App)
    monkeypatch.setattr(__main__, "build_team", lambda *a, **kw: object())
    args = __main__._build_parser().parse_args(["--include-hook-events"])
    assert args.include_hook_events is True
    __main__.run_tui(args)
    assert started["hook_events"] is True

    args = __main__._build_parser().parse_args([])
    assert args.include_hook_events is False
    __main__.run_tui(args)
    assert started["hook_events"] is False


def test_what_became_of_the_worktree_is_said_once_the_screen_is_gone(monkeypatch, capsys):
    class _App:
        _exit_note = "Back at /repo. The worktree was removed."
        _startup_error = ""

        def __init__(self, **kw):
            pass

        def run(self):
            return None

    monkeypatch.setattr(__main__, "load_config", lambda: {"model": "p/m"})
    monkeypatch.setattr(__main__, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr("pyclaw.tui.PyClawApp", _App)
    monkeypatch.setattr(__main__, "build_team", lambda *a, **kw: object())
    __main__.run_tui(__main__._build_parser().parse_args([]))
    assert "The worktree was removed." in capsys.readouterr().out


def test_a_worktree_that_cannot_be_opened_stops_the_run(monkeypatch, capsys):
    class _App:
        _exit_note = ""
        _startup_error = "Error: this worktree could not be made"

        def __init__(self, **kw):
            pass

        def run(self):
            return None

    monkeypatch.setattr(__main__, "load_config", lambda: {"model": "p/m"})
    monkeypatch.setattr("pyclaw.tui.PyClawApp", _App)
    monkeypatch.setattr(__main__, "build_team", lambda *a, **kw: object())
    args = __main__._build_parser().parse_args(["--worktree", "side"])
    with pytest.raises(SystemExit) as exit_info:
        __main__.run_tui(args)
    assert exit_info.value.code == 1
    assert "could not be made" in capsys.readouterr().err


def test_the_terminal_builds_the_team_with_what_the_run_asked_for(tmp_path, monkeypatch):
    """The builder is only called once the terminal mounts, so a keyword left
    behind in it crashes the real run rather than a test."""
    built = []
    holders = {}

    class _App:
        _exit_note = ""
        _startup_error = ""

        def __init__(self, *, builder, **kw):
            holders["builder"] = builder
            built.append(dict(kw, builder=None))

        def run(self):
            return None

    monkeypatch.setattr(__main__, "load_config", lambda: {"model": "p/m"})
    monkeypatch.setattr(__main__, "setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr("pyclaw.tui.PyClawApp", _App)

    def build_team(model, **kw):
        built.append(kw)
        return object()

    monkeypatch.setattr(__main__, "build_team", build_team)
    args = __main__._build_parser().parse_args(["--worktree", "side"])
    __main__.run_tui(args)
    holders["builder"]()

    app_kw, build_kw = built
    missing = set(build_kw) - set(inspect.signature(the_real_builder).parameters)
    assert missing == set()
    assert "worktree" not in build_kw
    assert build_kw["conversation_id"]
    assert app_kw["worktree"] is None or app_kw["worktree"] == "side"


class TestConfigCommand:
    def _secret(self, tmp_path, monkeypatch, data):
        path = tmp_path / "secrets.json"
        path.write_text(__import__("json").dumps(data))
        monkeypatch.setenv("CHATCHAT_SECRET_FILE", str(path))
        return path

    def _run(self, monkeypatch, argv):
        monkeypatch.setattr("sys.argv", ["pyclaw"] + argv)
        with pytest.raises(SystemExit) as exc:
            __main__.main()
        return exc.value.code

    def test_the_bare_command_shows_profiles_then_pyclaw_settings(self, monkeypatch, capsys, tmp_path):
        self._secret(
            tmp_path,
            monkeypatch,
            {"agnes": {"type": "openai", "base_url": "https://x.example/v1", "api_key": "sk-1234567890abcd", "rpm": 60}},
        )
        monkeypatch.setattr(config_module, "__config_file__", tmp_path / "config.json")
        monkeypatch.setattr(
            __main__,
            "load_config",
            lambda: {
                "model": "agnes/agnes-3.0-flash",
                "contextWindow": 200000,
                "gateway": {"http": {"host": "127.0.0.1", "port": 12321}},
                "enabled_channels": ["web", "wechat"],
                "permissions": {"defaultMode": "default"},
            },
        )
        assert self._run(monkeypatch, ["config"]) == 0
        out = capsys.readouterr().out
        assert "agnes" in out and "sk-123****abcd" in out and "rpm=60" in out
        assert "pyclaw config:" in out and "agnes/agnes-3.0-flash" in out and "127.0.0.1:12321" in out

    def test_a_write_keeps_the_pyclaw_section_out(self, monkeypatch, capsys, tmp_path):
        secret = self._secret(tmp_path, monkeypatch, {})
        assert self._run(monkeypatch, ["config", "--provider", "agnes", "--rpm", "30"]) == 0
        out = capsys.readouterr().out
        assert "pyclaw config:" not in out
        assert "rpm" in secret.read_text()

    def test_the_rpm_limit_comes_from_the_chatchat_profiles(self, monkeypatch):
        configured = {}
        monkeypatch.setattr(__main__.ratelimit, "configure", lambda limits: configured.update(limits))
        monkeypatch.setattr(__main__, "rate_limits", lambda: {"agnes": 60})
        monkeypatch.setattr(__main__, "load_config", lambda: {"model": "p/m"})
        monkeypatch.setattr(__main__, "parse_config", lambda args: 0)
        monkeypatch.setattr(__main__, "is_write_request", lambda args: False)
        monkeypatch.setattr(__main__, "_show_pyclaw_config", lambda: None)
        monkeypatch.setattr("sys.argv", ["pyclaw", "config"])
        with pytest.raises(SystemExit):
            __main__.main()
        assert configured == {"agnes": 60}
