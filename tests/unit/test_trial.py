import json
import os
import time

import numpy as np
import pytest
from PIL import Image

from aria_guidance.frames import BufferedFrame
from aria_guidance.trial import TrialRecorder, trial_video


def read(folder):
    trial = json.loads((folder / "trial.json").read_text())
    events = [json.loads(line) for line in (folder / "guidance.jsonl").read_text().splitlines()]
    return trial, events


@pytest.fixture
def sessions(tmp_path):
    root = tmp_path / "aria"
    for name, age in [("20261029T100000Z_old", 3600), ("20261029T120000Z_live", 1)]:
        clock = root / name / "clock.csv"
        clock.parent.mkdir(parents=True)
        clock.write_text("stream,capture_ns,host_ns,latency_ms\n")
        os.utime(clock, (time.time() - age, time.time() - age))
    return root


def test_trial_goes_into_the_live_session(sessions, tmp_path):
    recorder = TrialRecorder(sessions, tmp_path / "fallback")
    recorder.open("Clear the mat", [])
    assert recorder.folder.parents[1].name == "20261029T120000Z_live"
    assert recorder.folder.name.startswith("01_")


def test_fallback_without_a_live_session(tmp_path):
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    recorder.open("Clear the mat", [])
    first = recorder.folder
    recorder.open("Stack the cups", [])
    assert first.parents[2] == tmp_path / "fallback"
    assert recorder.folder.parent == first.parent  # One fallback folder per run.
    assert recorder.folder.name.startswith("02_")


def test_open_saves_the_buffered_window(tmp_path):
    frames = [BufferedFrame(1000.0 + i * 200, b"jpeg%d" % i) for i in range(3)]
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    recorder.open("Clear the mat", frames, camera="glasses")
    assert read(recorder.folder)[0]["camera"] == "glasses"
    assert sorted(p.name for p in (recorder.folder / "frames").iterdir()) == [
        "1000.jpg",
        "1200.jpg",
        "1400.jpg",
    ]
    assert (recorder.folder / "frames" / "1200.jpg").read_bytes() == b"jpeg1"
    recorder.frame_path(1600.4).write_bytes(b"live")
    assert (recorder.folder / "frames" / "1600.jpg").exists()


def test_events_steps_and_latency(tmp_path):
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    recorder.open("Clear the mat", [])
    folder = recorder.folder
    recorder.event("plan_result", instruction="Pick up the pen.", client_roundtrip_ms=2000.0)
    assert read(folder)[0]["steps"] == 1  # Written while the trial is still open.
    recorder.event("note", text="the cup slipped")
    recorder.event("plan_result", instruction="Put it down.", client_roundtrip_ms=4000.0)
    recorder.close("completed")
    trial, events = read(folder)
    assert [e["kind"] for e in events] == ["goal", "plan_result", "note", "plan_result", "end"]
    assert trial["steps"] == 2 and trial["latency_ms"] == {"mean": 3000.0, "max": 4000.0}
    assert trial["outcome"] == "completed" and trial["ended_ns"] >= trial["started_ns"]


@pytest.mark.parametrize("outcome", ["completed", "cancelled", "aborted"])
def test_every_outcome_reaches_trial_json(tmp_path, outcome):
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    recorder.open("Clear the mat", [])
    folder = recorder.folder
    assert read(folder)[0]["outcome"] is None  # Open trials read as live.
    recorder.close(outcome)
    assert read(folder)[0]["outcome"] == outcome
    assert recorder.frame_path(0) is None
    recorder.event("note", text="after close")  # Ignored, not an error.


def test_new_goal_closes_the_previous_trial_as_replaced(tmp_path):
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    recorder.open("Clear the mat", [])
    first = recorder.folder
    recorder.open("Stack the cups", [])
    assert read(first)[0]["outcome"] == "replaced"
    assert read(recorder.folder)[0]["goal"] == "Stack the cups"


def test_audio_is_placed_by_its_stamp(tmp_path):
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    block = np.full(1600, 0.5, dtype=np.float32)  # 100 ms at 16 kHz.
    buffered = [(1000.0, block, 16000), (1100.0, block, 16000)]
    recorder.open("Clear the mat", [], audio_blocks=buffered)
    folder = recorder.folder
    recorder.audio(1210.0, block, 16000)  # 10 ms of jitter: not a gap.
    recorder.audio(2300.0, block, 16000)  # 1 s of lost blocks: padded with silence.
    recorder.audio(2300.0, block, 16000)  # Overlapping by a block: the overlap is cut.
    recorder.audio(2500.0, -block, 16000)
    pcm = np.fromfile(folder / "audio.pcm", dtype="<i2")
    assert read(folder)[0]["audio"] == {"rate": 16000, "start_ms": 1000.0}
    assert len(pcm) == 1600 * 3 + 16000 + 1600 + 3200
    assert (pcm[:4800] == 16383).all()
    assert (pcm[4800:20800] == 0).all()
    assert pcm[-1] == -16383  # Later blocks keep their place.
    assert (pcm[-1600:] < 0).all()


def test_audio_stops_when_the_rate_changes_and_needs_an_open_trial(tmp_path):
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    block = np.zeros(160, dtype=np.float32)
    recorder.audio(0.0, block, 16000)  # No trial yet: ignored, not an error.
    recorder.open("Clear the mat", [])
    recorder.audio(1000.0, block, 16000)
    recorder.audio(1010.0, block, 48000)
    recorder.audio(1020.0, block, 16000)
    assert (recorder.folder / "audio.pcm").stat().st_size == 320


def test_video_carries_the_sound_at_its_offset(tmp_path):
    av = pytest.importorskip("av")
    rate = 16000
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    recorder.open("Clear the mat", [])
    folder = recorder.folder
    for ms in range(1000, 4000, 200):
        Image.new("RGB", (64, 48), "white").save(folder / "frames" / f"{ms}.jpg")
    # Sound starts 0.5 s into the video, silent for its first second there, then a tone.
    tone = 0.5 * np.sin(2 * np.pi * 440 * np.arange(rate) / rate).astype(np.float32)
    recorder.audio(1500.0, np.zeros(rate, dtype=np.float32), rate)
    recorder.audio(2500.0, tone, rate)
    recorder.close("completed")
    with av.open(str(trial_video(folder))) as container:
        stream = container.streams.audio[0]
        assert stream.codec_context.name == "aac" and stream.rate == rate
        assert stream.channels == 1
        decoded = np.concatenate([f.to_ndarray().ravel() for f in container.decode(stream)])
    assert len(decoded) / rate == pytest.approx(3.0, abs=0.15)  # As long as the video.
    onset = np.argmax(np.abs(decoded) > 0.1) / rate
    assert onset == pytest.approx(1.5, abs=0.1)
    assert np.abs(decoded).max() == pytest.approx(0.5, abs=0.1)


def test_video_without_audio_stays_silent(tmp_path):
    av = pytest.importorskip("av")
    recorder = TrialRecorder(tmp_path / "aria", tmp_path / "fallback")
    recorder.open("Clear the mat", [])
    Image.new("RGB", (64, 48), "white").save(recorder.folder / "frames" / "1000.jpg")
    folder = recorder.folder
    recorder.close("completed")
    with av.open(str(trial_video(folder))) as container:
        assert len(container.streams.audio) == 0
