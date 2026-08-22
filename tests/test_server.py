import pytest

from pyclaw.gateway.server import _friendly_channel_error


def test_timeout_error():
    assert "timed out" in _friendly_channel_error(TimeoutError("Request timed out"))


def test_internal_server_error():
    assert "unavailable" in _friendly_channel_error(RuntimeError("InternalServerError: 500"))


def test_rate_limit_error():
    assert "Too many requests" in _friendly_channel_error(RuntimeError("rate limit exceeded"))


def test_generic_error_truncated():
    long_msg = "x" * 500
    out = _friendly_channel_error(RuntimeError(long_msg))
    assert out.startswith("An error occurred:")
    assert len(out) <= len("An error occurred: ") + 200


def test_500_in_message():
    assert "unavailable" in _friendly_channel_error(RuntimeError("got 500"))
