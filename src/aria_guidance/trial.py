"""Trial records: one folder per goal, next to the aria_node session that was streaming.

<session>/trials/<NN>_<HHMMSS>/
    trial.json      goal, start/end (host ms and ns), outcome, steps, latency summary
    guidance.jsonl  one event per line: goal, plan_request, plan_result, tts, reply, note, ...
    frames/<ms>.jpg the preview JPEGs, about 5 Hz
    video.mp4       frames/ encoded on first view by the backend, timed by the frame stamps
    review.json     verdict and notes; only the backend's Trials page writes it

TrialRecorder is the node half; the functions below it serve the workbench.
"""

import json
import os
import re
import threading
import time
from datetime import datetime
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

# shortcut: assumes the source layout (aria_vlm/src/aria_guidance); set fallback_root otherwise.
FALLBACK_ROOT = Path(__file__).resolve().parents[2] / ".runtime" / "guidance"
LIVE_SECONDS = 120  # aria_node appends to clock.csv while it streams.


def write_json(path, body):
    """Atomic, so a reader never sees half a file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1))
    os.replace(tmp, path)


class TrialRecorder:
    """Writes the trial of the current goal. Called from the node's ROS thread."""

    def __init__(self, session_root, fallback_root=FALLBACK_ROOT):
        self.session_root, self.fallback_root = Path(session_root), Path(fallback_root)
        self.fallback = None  # One fallback folder per node run, made on first use.
        self.folder, self.body, self.latencies = None, None, []
        self.last_ref = None  # {session, id} of the newest trial, open or closed, for the console.

    def session_folder(self):
        """The newest aria_node session still streaming, else this run's fallback folder."""
        # shortcut: two aria_nodes recording at once would share trials; upgrade by having
        # aria_node publish its folder.
        now = time.time()
        live = [
            clock.parent
            for clock in self.session_root.expanduser().glob("*/clock.csv")
            if now - clock.stat().st_mtime < LIVE_SECONDS
        ]
        if live:
            return max(live, key=lambda p: p.name)
        if self.fallback is None:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            self.fallback = self.fallback_root.expanduser() / stamp
        return self.fallback

    def open(self, goal, frames, camera=None):
        """Starts a trial; a trial still open is closed as replaced.

        Args:
            goal: the goal text.
            frames: BufferedFrames already buffered, saved so the video starts with what the
                model saw.
            camera: glasses, fake (tools/fake_camera.py), another frame_id, or None.
        """
        if self.folder:
            self.close("replaced")
        trials = self.session_folder() / "trials"
        trials.mkdir(parents=True, exist_ok=True)
        index = len([p for p in trials.iterdir() if p.is_dir()]) + 1
        self.folder = trials / f"{index:02d}_{datetime.now():%H%M%S}"
        (self.folder / "frames").mkdir(parents=True)
        self.last_ref = {"session": self.folder.parents[1].name, "id": self.folder.name}
        for frame in frames:
            self.frame_path(frame.timestamp_ms).write_bytes(frame.jpeg)
        ns = time.time_ns()
        self.body = {
            "id": self.folder.name,
            "session": self.folder.parents[1].name,
            "goal": goal,
            "camera": camera,
            "started_ms": ns / 1e6,
            "started_ns": ns,
            "ended_ms": None,
            "ended_ns": None,
            "outcome": None,
            "steps": 0,
            "latency_ms": None,
        }
        self.latencies = []
        write_json(self.folder / "trial.json", self.body)
        self.event("goal", text=goal)

    def frame_path(self, timestamp_ms):
        """Where a preview frame goes, or None with no open trial. The node writes it off-thread."""
        return self.folder / "frames" / f"{int(timestamp_ms)}.jpg" if self.folder else None

    def event(self, kind, **fields):
        if not self.folder:
            return
        if kind == "plan_result":
            self.body["steps"] += 1
            self.latencies.append(fields.get("client_roundtrip_ms", 0.0))
        line = json.dumps({"t_ms": time.time() * 1000, "kind": kind, **fields}, ensure_ascii=False)
        with open(self.folder / "guidance.jsonl", "a") as f:
            f.write(line + "\n")

    def close(self, outcome):
        """Ends the trial: completed, cancelled, replaced or aborted."""
        if not self.folder:
            return
        self.event("end", outcome=outcome)
        ns = time.time_ns()
        self.body.update(ended_ms=ns / 1e6, ended_ns=ns, outcome=outcome)
        if self.latencies:
            self.body["latency_ms"] = {
                "mean": round(sum(self.latencies) / len(self.latencies), 1),
                "max": round(max(self.latencies), 1),
            }
        write_json(self.folder / "trial.json", self.body)
        self.folder = None


# One path segment of word characters, dots and dashes, and not "." or "..".
TRIAL_ID = re.compile(r"^(?!\.+$)[\w.-]+$")
ENCODE_LOCK = threading.Lock()


def find_trial(roots, session, trial):
    """The trial folder under one of the roots; KeyError for bad IDs and anything outside."""
    if not (TRIAL_ID.match(session) and TRIAL_ID.match(trial)):
        raise KeyError(trial)
    for root in roots:
        root = Path(root).resolve()
        folder = (root / session / "trials" / trial).resolve()
        if folder.is_relative_to(root) and (folder / "trial.json").is_file():
            return folder
    raise KeyError(trial)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def list_trials(roots):
    """Summaries of every trial, newest first."""
    found = []
    for root in roots:
        for path in Path(root).glob("*/trials/*/trial.json"):
            trial = read_json(path)
            if trial is None:
                continue
            review = read_json(path.parent / "review.json", {})
            trial.update(session=path.parents[2].name, verdict=review.get("verdict"))
            found.append(trial)
    return sorted(found, key=lambda t: t.get("started_ms") or 0, reverse=True)


def frame_stamps(folder):
    """Frame timestamps in ms, ascending."""
    return sorted(int(p.stem) for p in (folder / "frames").glob("*.jpg") if p.stem.isdigit())


def read_trial(folder):
    events = []
    jsonl = folder / "guidance.jsonl"
    for line in jsonl.read_text().splitlines() if jsonl.exists() else []:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue  # A line cut short by a crash.
    stamps = frame_stamps(folder)
    return {
        "trial": {**read_json(folder / "trial.json", {}), "session": folder.parents[1].name},
        "events": events,
        "review": read_json(folder / "review.json", {"verdict": None, "notes": ""}),
        "frames": len(stamps),
        "video_start_ms": stamps[0] if stamps else None,
    }


def write_review(folder, verdict, notes):
    review = {"verdict": verdict, "notes": notes, "updated_at": time.time()}
    write_json(folder / "review.json", review)
    return review


def trial_video(folder):
    """frames/ as an H.264 mp4 whose timestamps are the capture times, cached once closed.

    Returns:
        The mp4 path, or None when the trial has no frames.
    """
    import av  # Installed with faster-whisper; only this route needs it.
    from PIL import Image

    target = folder / "video.mp4"
    live = read_json(folder / "trial.json", {}).get("outcome") is None
    with ENCODE_LOCK:
        if target.exists() and not live:
            return target
        stamps = frame_stamps(folder)
        if not stamps:
            return None
        tmp = folder / "video.tmp.mp4"
        # Each frame lasts until the next; the last gets 200 ms (5 Hz). The mp4 duration is the
        # sum of packet durations, and x264 would otherwise derive them from a fixed rate.
        offsets = [ms - stamps[0] for ms in stamps]
        durations = dict(zip(offsets, [b - a for a, b in pairwise(offsets)] + [200]))

        def mux(packets):
            for packet in packets:
                if packet.pts is not None:
                    packet.duration = durations[packet.pts]
                container.mux(packet)

        with av.open(str(tmp), "w", options={"movflags": "faststart"}) as container:
            # No B-frames: decode order equals capture order, so the duration sums exactly.
            stream = container.add_stream(
                "libx264", rate=5, options={"preset": "veryfast", "x264-params": "bframes=0"}
            )
            # Milliseconds throughout. Not stream.time_base: writing the header changes it.
            ms = Fraction(1, 1000)
            stream.time_base = stream.codec_context.time_base = ms
            stream.pix_fmt = "yuv420p"
            for stamp, offset in zip(stamps, offsets):
                with Image.open(folder / "frames" / f"{stamp}.jpg") as image:
                    image = image.convert("RGB")
                    if not stream.width:  # The first frame sets the size; x264 needs it even.
                        stream.width, stream.height = image.width // 2 * 2, image.height // 2 * 2
                    image = image.resize((stream.width, stream.height))
                frame = av.VideoFrame.from_image(image)
                frame.pts, frame.time_base = offset, ms
                mux(stream.encode(frame))
            mux(stream.encode())
        os.replace(tmp, target)
        return target
