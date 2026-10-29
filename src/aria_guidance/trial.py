"""Trial records: one folder per goal, next to the aria_node session that was streaming.

<session>/trials/<NN>_<HHMMSS>/
    trial.json      goal, start/end (host ms and ns), outcome, steps, latency summary
    guidance.jsonl  one event per line: goal, plan_request, plan_result, tts, reply, note, ...
    frames/<ms>.jpg the preview JPEGs, about 5 Hz
"""

import json
import os
import time
from datetime import datetime
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

    def open(self, goal, frames):
        """Starts a trial; a trial still open is closed as replaced.

        Args:
            goal: the goal text.
            frames: BufferedFrames already buffered, saved so the video starts with what the
                model saw.
        """
        if self.folder:
            self.close("replaced")
        trials = self.session_folder() / "trials"
        trials.mkdir(parents=True, exist_ok=True)
        index = len([p for p in trials.iterdir() if p.is_dir()]) + 1
        self.folder = trials / f"{index:02d}_{datetime.now():%H%M%S}"
        (self.folder / "frames").mkdir(parents=True)
        for frame in frames:
            self.frame_path(frame.timestamp_ms).write_bytes(frame.jpeg)
        ns = time.time_ns()
        self.body = {
            "id": self.folder.name,
            "session": self.folder.parents[1].name,
            "goal": goal,
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
