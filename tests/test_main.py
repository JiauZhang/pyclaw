import argparse

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


def test_finalize_args_adds_missing_log_level():
    ns = argparse.Namespace(command=None)
    out = __main__._finalize_args(ns)
    assert out.log_level == "INFO"


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


def test_start_server_runs_with_default_args(monkeypatch):
    import asyncio as _asyncio

    class FakeGateway:
        def __init__(self, *a, **k):
            pass

        async def start(self):
            return

        async def shutdown(self):
            return

    captured = {}

    def fake_gateway(config, app_config=None):
        captured["config"] = config
        return FakeGateway()

    monkeypatch.setattr(__main__, "GatewayServer", fake_gateway)
    monkeypatch.setattr(__main__, "load_config", lambda: {"provider": "p", "model": "m", "enabled_channels": ["wechat"]})
    monkeypatch.setattr(__main__, "save_config", lambda c: None)

    args = __main__._finalize_args(__main__._build_parser().parse_args([]))
    _asyncio.run(__main__.start_server(args))
    assert captured["config"].provider == "p"
