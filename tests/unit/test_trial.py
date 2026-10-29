import json
import os
import time

import pytest

from aria_guidance.frames import BufferedFrame
from aria_guidance.trial import TrialRecorder


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
    recorder.open("Clear the mat", frames)
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
