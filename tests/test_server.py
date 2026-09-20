from pyclaw.gateway.server import _friendly_channel_error


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
