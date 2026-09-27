from pyclaw.gateway.im import _friendly_channel_error


def test_a_channel_failure_is_reworded_for_the_user():
    cases = (
        (TimeoutError("Request timed out"), "timed out"),
        (RuntimeError("InternalServerError: 500"), "unavailable"),
        (RuntimeError("got 500"), "unavailable"),
        (RuntimeError("rate limit exceeded"), "Too many requests"))
    for error, phrase in cases:
        assert phrase in _friendly_channel_error(error)


def test_an_unknown_failure_is_reported_briefly():
    out = _friendly_channel_error(RuntimeError("x" * 500))
    assert out.startswith("An error occurred:")
    assert len(out) <= len("An error occurred: ") + 200


def test_the_gateway_builds_a_team_when_told_to(monkeypatch):
    import pytest

    from pyclaw.gateway import server as gateway_server
    from pyclaw.gateway.server import GatewayConfig, GatewayServer

    captured = {}

    class _Probed(Exception):
        pass

    def probe(**kw):
        captured.update(kw)
        raise _Probed()

    monkeypatch.setattr(gateway_server, 'build_team', probe)
    server = GatewayServer(GatewayConfig(use_team=True))
    with pytest.raises(_Probed):
        server._new_session('c1')
    assert captured['use_team'] is True
