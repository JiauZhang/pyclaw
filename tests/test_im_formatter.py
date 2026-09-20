from pyclaw.channels.im_formatter import split_long_message, IMStatusTracker


def test_split_keeps_every_part_under_the_limit_and_lossless():
    paragraphs = "\n\n".join(["para one " * 50, "para two " * 50])
    for text, limit, stays_whole in (
            ("hello world", 1500, True),
            ("a" * 5000, 1500, False),
            (paragraphs, 200, False),
            ("句子内容。" * 100, 200, False)):
        parts = split_long_message(text, limit)
        assert all(len(p) <= limit for p in parts)
        assert "".join(parts) == text
        if stays_whole:
            assert parts == [text]


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
