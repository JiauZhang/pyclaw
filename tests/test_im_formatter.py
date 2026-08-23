import pytest

from pyclaw.channels.im_formatter import split_long_message, IMStatusTracker


def test_split_short_message_unchanged():
    text = "hello world"
    assert split_long_message(text, 1500) == [text]


def test_split_respects_limit():
    text = "a" * 5000
    parts = split_long_message(text, 1500)
    assert all(len(p) <= 1500 for p in parts)
    assert "".join(parts) == text


def test_split_on_paragraph_boundary():
    parts = ["para one " * 50, "para two " * 50]
    text = "\n\n".join(parts)
    result = split_long_message(text, 200)
    assert all(len(p) <= 200 for p in result)
    assert "".join(result) == text


def test_split_on_sentence_when_paragraph_too_long():
    long_para = ("句子内容。" * 100)
    result = split_long_message(long_para, 200)
    assert all(len(p) <= 200 for p in result)
    assert "".join(result) == long_para


def test_tracker_no_duplicate_same_status():
    t = IMStatusTracker(refresh_interval=4.0)
    assert t.update("thinking", now=0.0) is True
    assert t.update("thinking", now=1.0) is False


def test_tracker_drains_after_interval():
    t = IMStatusTracker(refresh_interval=4.0)
    t.update("step 1", now=0.0)
    assert t.drain(now=1.0) == ["step 1"]
    assert t.drain(now=2.0) == []
    assert t.drain(now=5.0) == []


def test_tracker_keeps_only_latest_pending():
    t = IMStatusTracker(refresh_interval=4.0)
    t.update("step 1", now=0.0)
    t.update("step 2", now=1.0)
    t.update("step 3", now=2.0)
    assert t.drain(now=5.0) == ["step 3"]


def test_tracker_empty_drain():
    t = IMStatusTracker(refresh_interval=4.0)
    assert t.drain(now=10.0) == []


def test_tracker_status_text_emitted_on_change():
    t = IMStatusTracker(refresh_interval=4.0)
    t.update("using tool", now=0.0)
    assert t.drain(now=5.0) == ["using tool"]
    t.update("thinking", now=6.0)
    assert t.drain(now=10.0) == ["thinking"]
