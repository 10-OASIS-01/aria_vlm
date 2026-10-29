import json
import os

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from stubs import StubEngine

from aria_guidance.config import Settings
from aria_guidance.service import create_app


def make_trial(root, session, trial, outcome="completed", stamps=(1000, 1200, 1450, 2000)):
    folder = root / session / "trials" / trial
    (folder / "frames").mkdir(parents=True)
    body = {"id": trial, "goal": f"Goal {trial}", "started_ms": stamps[0], "outcome": outcome}
    (folder / "trial.json").write_text(json.dumps(body))
    events = [{"t_ms": 1000, "kind": "goal", "text": "Goal"}, {"t_ms": 1450, "kind": "note"}]
    (folder / "guidance.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n{cut")
    for ms in stamps:
        Image.new("RGB", (64, 48), "white").save(folder / "frames" / f"{ms}.jpg")
    return folder


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_API_TOKEN", "secret")
    settings = Settings(data_dir=tmp_path / "runtime", sessions_dir=tmp_path / "aria")
    folders = {
        "live": make_trial(tmp_path / "aria", "20261029T120000Z_ab", "01_120001"),
        "fallback": make_trial(
            tmp_path / "runtime" / "guidance", "20261029T130000", "01_130001", stamps=(5000,)
        ),
    }
    client = TestClient(create_app(settings, engine=StubEngine(), load_model=False))
    client.headers["Authorization"] = "Bearer secret"
    return client, folders, tmp_path


def test_list_spans_both_roots_newest_first(app):
    client, _, _ = app
    trials = client.get("/api/trials").json()
    assert [t["id"] for t in trials] == ["01_130001", "01_120001"]
    assert trials[1]["session"] == "20261029T120000Z_ab" and trials[1]["verdict"] is None


def test_open_trial_without_recent_writes_is_not_active(app):
    client, _, tmp_path = app
    folder = make_trial(tmp_path / "aria", "20261029T120000Z_ab", "06_open", outcome=None)
    assert client.get("/api/trials/20261029T120000Z_ab/06_open").json()["trial"]["active"]
    old = 1_700_000_000
    for path in [folder / "frames", folder / "guidance.jsonl"]:
        os.utime(path, (old, old))
    trials = {t["id"]: t for t in client.get("/api/trials").json()}
    assert trials["06_open"]["active"] is False  # Killed node: shown as interrupted.


def test_detail_skips_a_cut_line(app):
    client, _, _ = app
    detail = client.get("/api/trials/20261029T120000Z_ab/01_120001").json()
    assert [e["kind"] for e in detail["events"]] == ["goal", "note"]
    assert detail["frames"] == 4 and detail["video_start_ms"] == 1000
    assert detail["review"] == {"verdict": None, "notes": ""}


def test_review_round_trips(app):
    client, folders, _ = app
    url = "/api/trials/20261029T120000Z_ab/01_120001"
    body = {"verdict": "fail", "notes": "Grasped the wrong pen."}
    assert client.put(url + "/review", json=body).status_code == 200
    assert client.get(url).json()["review"]["notes"] == "Grasped the wrong pen."
    assert client.get("/api/trials").json()[1]["verdict"] == "fail"
    assert json.loads((folders["live"] / "trial.json").read_text())["outcome"] == "completed"


def test_review_needs_the_token_and_a_valid_body(app):
    client, _, _ = app
    url = "/api/trials/20261029T120000Z_ab/01_120001/review"
    assert client.put(url, json={"verdict": "maybe"}).status_code == 422
    assert client.put(url, json={"notes": "x" * 10_001}).status_code == 422
    del client.headers["Authorization"]
    assert client.put(url, json={"verdict": "success"}).status_code == 401


def test_missing_trial_is_404(app):
    client, _, _ = app
    assert client.get("/api/trials/20261029T120000Z_ab/09_000000").status_code == 404


@pytest.mark.parametrize("session, trial", [("..", "x"), ("%2Fetc", "passwd"), ("a b", "x")])
def test_bad_ids_are_rejected(app, session, trial):
    client, _, _ = app
    assert client.get(f"/api/trials/{session}/{trial}").status_code == 404


def test_symlink_out_of_the_root_is_rejected(app):
    client, _, tmp_path = app
    outside = make_trial(tmp_path / "outside", "s", "01_000000")
    (tmp_path / "aria" / "20261029T120000Z_ab" / "trials" / "02_link").symlink_to(outside)
    assert client.get("/api/trials/20261029T120000Z_ab/02_link").status_code == 404


def test_video_is_timed_by_frame_stamps_and_cached(app):
    av = pytest.importorskip("av")
    client, folders, _ = app
    url = "/api/trials/20261029T120000Z_ab/01_120001/video.mp4"
    response = client.get(url)
    assert response.status_code == 200 and response.headers["content-type"] == "video/mp4"
    path = folders["live"] / "video.mp4"
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        times = [float(f.pts * f.time_base) for f in container.decode(stream)]
        assert times == [0.0, 0.2, 0.45, 1.0]
        assert float(stream.duration * stream.time_base) == pytest.approx(1.2)
    mtime = path.stat().st_mtime_ns
    assert client.get(url).content == response.content
    assert path.stat().st_mtime_ns == mtime  # Served from the cache, not re-encoded.


def test_long_video_keeps_frame_times(app):
    # Long enough that x264 emits packets (and the mp4 header is written) mid-encode.
    av = pytest.importorskip("av")
    client, _, tmp_path = app
    stamps = [10_000 + i * 200 for i in range(120)]
    folder = make_trial(tmp_path / "aria", "20261029T120000Z_ab", "04_long", stamps=stamps)
    assert client.get("/api/trials/20261029T120000Z_ab/04_long/video.mp4").status_code == 200
    with av.open(str(folder / "video.mp4")) as container:
        stream = container.streams.video[0]
        times = [round(float(f.pts * f.time_base), 3) for f in container.decode(stream)]
        assert times == [round(i * 0.2, 3) for i in range(120)]
        assert float(stream.duration * stream.time_base) == pytest.approx(24.0)


def test_open_trial_video_is_re_encoded(app):
    pytest.importorskip("av")
    client, _, tmp_path = app
    folder = make_trial(tmp_path / "aria", "20261029T120000Z_ab", "02_live", outcome=None)
    url = "/api/trials/20261029T120000Z_ab/02_live/video.mp4"
    first = client.get(url).content
    Image.new("RGB", (64, 48), "black").save(folder / "frames" / "3000.jpg")
    assert client.get(url).content != first


def test_captions_follow_the_timeline(app):
    client, folders, _ = app
    folder = folders["live"]  # Frames start at 1000 ms; outcome completed.
    events = [
        {"t_ms": 1000, "kind": "goal", "text": "Clear the mat"},
        {"t_ms": 1500, "kind": "tts", "text": "Pick up the <red> cup & go."},
        {"t_ms": 2000, "kind": "plan_request", "frames": []},  # Not captioned.
        {"t_ms": 12_000, "kind": "reply", "text": "done"},
        {"t_ms": 13_000, "kind": "end", "outcome": "completed"},
    ]
    (folder / "guidance.jsonl").write_text("\n".join(map(json.dumps, events)) + "\n")
    response = client.get("/api/trials/20261029T120000Z_ab/01_120001/captions.vtt")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/vtt")
    assert response.text.strip().split("\n\n")[1:] == [
        "00:00:00.000 --> 00:00:01.500\nGoal: Clear the mat",  # 0.5 s gap: held 1.5 s.
        "00:00:00.500 --> 00:00:06.500\nGuide: Pick up the &lt;red> cup &amp; go.",  # Capped.
        "00:00:11.000 --> 00:00:12.500\nOperator: done",  # 1 s gap: held 1.5 s.
        "00:00:12.000 --> 00:00:18.000\nTrial ended: completed",
    ]
    # Closed trial: cached in the folder next to video.mp4, for VLC.
    assert (folder / "video.vtt").read_text() == response.text
    (folder / "guidance.jsonl").write_text("")
    assert (
        client.get("/api/trials/20261029T120000Z_ab/01_120001/captions.vtt").text == response.text
    )


def test_trial_without_frames_has_no_captions(app):
    client, _, tmp_path = app
    folder = make_trial(tmp_path / "aria", "20261029T120000Z_ab", "05_empty", stamps=(1,))
    (folder / "frames" / "1.jpg").unlink()
    assert client.get("/api/trials/20261029T120000Z_ab/05_empty/captions.vtt").status_code == 404


def test_trial_without_frames_has_no_video(app):
    client, _, tmp_path = app
    folder = make_trial(tmp_path / "aria", "20261029T120000Z_ab", "03_empty", stamps=(1,))
    (folder / "frames" / "1.jpg").unlink()
    assert client.get("/api/trials/20261029T120000Z_ab/03_empty/video.mp4").status_code == 404
