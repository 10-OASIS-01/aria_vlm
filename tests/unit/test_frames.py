import pytest

from aria_guidance.frames import FrameBuffer, WindowNotReady


def filled(times):
    buffer = FrameBuffer()
    for t in times:
        buffer.add(t, b"jpeg")
    return buffer


def camera(seconds=10.0, hz=5.0, start=1_000_000.0):
    return [start + i * 1000 / hz for i in range(int(seconds * hz) + 1)]


def test_samples_twenty_frames_at_two_hz():
    times = camera()
    selected = filled(times).sample(now_ms=times[-1] + 300)
    assert len(selected) == 20
    assert selected[-1].timestamp_ms == times[-1]
    assert 9000 <= selected[-1].timestamp_ms - selected[0].timestamp_ms <= 10000


def test_short_window_is_not_ready():
    with pytest.raises(WindowNotReady, match="Collecting"):
        filled(camera(seconds=5)).sample()


def test_stale_stream_reports_age():
    times = camera()
    with pytest.raises(WindowNotReady, match="newest frame is 3.0 s old"):
        filled(times).sample(now_ms=times[-1] + 3000)


def test_gap_is_not_padded():
    times = [t for t in camera() if not 1_004_000 < t < 1_006_000]
    with pytest.raises(WindowNotReady, match="missing frames|gap"):
        filled(times).sample()


def test_out_of_order_and_duplicate_stamps_are_rejected():
    buffer = FrameBuffer()
    assert buffer.add(1000.0, b"a")
    assert not buffer.add(1000.0, b"b")
    assert not buffer.add(900.0, b"c")
    assert len(buffer.frames) == 1
