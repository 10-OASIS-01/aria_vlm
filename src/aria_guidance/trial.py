"""Trial records: one folder per goal, next to the aria_node session that was streaming.

<session>/trials/<NN>_<HHMMSS>/
    trial.json      goal, start/end (host ms and ns), outcome, steps, latency summary
    guidance.jsonl  one event per line: goal, plan_request, plan_result, tts, reply, note, ...
    frames/<ms>.jpg the preview JPEGs, about 5 Hz
    audio.pcm       the glasses' microphones, mono int16; trial.json's audio gives rate and start
    video.mp4       frames/ and audio.pcm encoded on first view by the backend, timed by the stamps
    video.vtt       captions for video.mp4 from guidance.jsonl (VLC loads it with the video)
    review.json     verdict and notes; only the backend's Trials page writes it
    latency.json    per-step timing; only scripts/trial_latency.py writes it

TrialRecorder is the node half; the functions below it serve the workbench.
"""

import json
import os
import re
import statistics
import threading
import time
from datetime import datetime
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import numpy as np

# shortcut: assumes the source layout (aria_vlm/src/aria_guidance); set fallback_root otherwise.
FALLBACK_ROOT = Path(__file__).resolve().parents[2] / ".runtime" / "guidance"
LIVE_SECONDS = 120  # aria_node appends to clock.csv while it streams.
AUDIO_RESYNC_SECONDS = 0.25  # Stamps jitter; only a larger difference is a real gap or overlap.


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
        self.audio_samples, self.audio_stopped = 0, False
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

    def open(self, goal, frames, camera=None, audio_blocks=()):
        """Starts a trial; a trial still open is closed as replaced.

        Args:
            goal: the goal text.
            frames: BufferedFrames already buffered, saved so the video starts with what the
                model saw.
            camera: glasses, fake (tools/fake_camera.py), another frame_id, or None.
            audio_blocks: (stamp_ms, samples, rate) already buffered, saved for the same reason.
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
            "audio": None,
        }
        self.latencies = []
        self.audio_samples, self.audio_stopped = 0, False
        write_json(self.folder / "trial.json", self.body)
        self.event("goal", text=goal)
        for block in audio_blocks:
            self.audio(*block)

    def frame_path(self, timestamp_ms):
        """Where a preview frame goes, or None with no open trial. The node writes it off-thread."""
        return self.folder / "frames" / f"{int(timestamp_ms)}.jpg" if self.folder else None

    def audio(self, stamp_ms, samples, rate):
        """Appends one microphone block where its stamp says, so lost blocks do not shift the sound.

        Args:
            stamp_ms: host time of the first sample.
            samples (N,): mono floats in -1 to 1, with the same gain the recognizer hears.
            rate: samples per second; a change stops the audio of this trial.
        """
        if not self.folder or self.audio_stopped:
            return
        if self.body["audio"] is None:
            self.body["audio"] = {"rate": rate, "start_ms": stamp_ms}
            write_json(self.folder / "trial.json", self.body)
        elif self.body["audio"]["rate"] != rate:
            self.audio_stopped = True
            return
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        offset = round((stamp_ms - self.body["audio"]["start_ms"]) * rate / 1000)
        difference = offset - self.audio_samples
        if abs(difference) > AUDIO_RESYNC_SECONDS * rate:
            if difference > 0:
                pcm = np.concatenate([np.zeros(difference, dtype="<i2"), pcm])
            else:
                pcm = pcm[-difference:]
        with open(self.folder / "audio.pcm", "ab") as f:
            f.write(pcm.tobytes())
        self.audio_samples += len(pcm)

    def event(self, kind, **fields):
        if not self.folder:
            return
        line = json.dumps({"t_ms": time.time() * 1000, "kind": kind, **fields}, ensure_ascii=False)
        with open(self.folder / "guidance.jsonl", "a") as f:
            f.write(line + "\n")
        if kind == "plan_result":
            self.body["steps"] += 1
            self.latencies.append(fields.get("client_roundtrip_ms", 0.0))
            write_json(self.folder / "trial.json", self.body)  # Open trials show their steps.
        elif kind == "tts_suppressed" and "client_roundtrip_ms" in fields:
            self.latencies.append(fields["client_roundtrip_ms"])  # A stale plan's wait counts too.

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


def is_active(folder):
    """An open trial is still recording if its frames or events changed in the last 30 s.

    A node killed without closing leaves its trial open forever; this tells that apart.
    """
    paths = [folder / "frames", folder / "guidance.jsonl"]
    newest = max((p.stat().st_mtime for p in paths if p.exists()), default=0)
    return time.time() - newest < 30


def list_trials(roots):
    """Summaries of every trial, newest first."""
    found = []
    for root in roots:
        for path in Path(root).glob("*/trials/*/trial.json"):
            trial = read_json(path)
            if trial is None:
                continue
            review = read_json(path.parent / "review.json", {})
            active = trial.get("outcome") is None and is_active(path.parent)
            trial.update(session=path.parents[2].name, verdict=review.get("verdict"), active=active)
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
        "trial": {
            **read_json(folder / "trial.json", {}),
            "session": folder.parents[1].name,
            "active": is_active(folder),
            "folder": str(folder),
        },
        "events": events,
        "review": read_json(folder / "review.json", {"verdict": None, "notes": ""}),
        "frames": len(stamps),
        "video_start_ms": stamps[0] if stamps else None,
    }


def write_review(folder, verdict, notes):
    review = {"verdict": verdict, "notes": notes, "updated_at": time.time()}
    write_json(folder / "review.json", review)
    return review


def rounded(value):
    return None if value is None else round(value, 1)


def difference(later, earlier):
    return None if later is None or earlier is None else round(later - earlier, 1)


def trial_latency(folder):
    """Each latency of one trial up to publishing TTS; what it did not record is None.

    Not audible TTS: the glasses give no playback signal, and in the first recorded trial their
    microphones did not pick up the speaker, so audio.pcm cannot show it either.

    Returns:
        {id, goal, steps, speech, goal_to_tts_from_start_ms, goal_to_tts_from_endpoint_ms,
        summary}. A step is one plan_request and its result. summary gives the mean, median and
        max of each step field.
    """
    detail = read_trial(folder)
    steps, speech, goal_speech = [], [], None
    step, instruction_due = None, False
    for event in detail["events"]:
        kind = event["kind"]
        if kind == "transcript":
            if event.get("voice"):
                goal_speech = event
            speech.append(
                {
                    "text": event.get("text"),
                    "stt_ms": rounded(event.get("duration_ms")),
                    "stt_roundtrip_ms": rounded(event.get("client_roundtrip_ms")),
                    "speech_started_ms": event.get("speech_started_ms"),
                    "speech_endpointed_ms": event.get("speech_endpointed_ms"),
                }
            )
        elif kind == "plan_request":
            frames = event.get("frames") or [None]
            step = {
                "t_ms": event["t_ms"],
                "stale": False,
                "observation_span_ms": difference(frames[-1], frames[0]),
                "frame_age_ms": difference(event["t_ms"], frames[-1]),
                "http_ms": None,
                "server_total_ms": None,
                "model_ms": None,
                "first_token_ms": None,
                "publish_ms": None,
                "tts_ms": None,
            }
            steps.append(step)
            instruction_due = False
        elif kind in {"plan_result", "tts_suppressed"} and step:
            if "client_roundtrip_ms" in event:
                timings = event.get("timings") or {}
                step.update(
                    http_ms=rounded(event["client_roundtrip_ms"]),
                    server_total_ms=rounded(timings.get("server_total_ms")),
                    model_ms=rounded(timings.get("model_ms")),
                    first_token_ms=rounded(timings.get("time_to_first_token_ms")),
                    stale=kind == "tts_suppressed",
                )
            instruction_due = kind == "plan_result"
        elif kind == "tts" and instruction_due:
            step.update(publish_ms=difference(event["t_ms"], step["t_ms"]), tts_ms=event["t_ms"])
            instruction_due = False

    first_tts = next((s["tts_ms"] for s in steps if s["tts_ms"] is not None), None)
    goal_speech = goal_speech or {}
    fields = [k for k in (steps[0] if steps else {}) if k not in {"t_ms", "stale", "tts_ms"}]
    summary = {}
    for name in fields:
        values = [s[name] for s in steps if s[name] is not None]
        if values:
            summary[name] = {
                "mean": round(statistics.mean(values), 1),
                "median": round(statistics.median(values), 1),
                "max": max(values),
            }
    return {
        "id": detail["trial"].get("id"),
        "goal": detail["trial"].get("goal"),
        "steps": steps,
        "speech": speech,
        "goal_to_tts_from_start_ms": difference(first_tts, goal_speech.get("speech_started_ms")),
        "goal_to_tts_from_endpoint_ms": difference(
            first_tts, goal_speech.get("speech_endpointed_ms")
        ),
        "summary": summary,
    }


CAPTIONS = {
    "goal": "Human",
    "tts": "System",
    "reply": "Human",
    "transcript": "Human",
    "note": "Note",
}


def vtt_time(ms):
    seconds = max(ms, 0) / 1000
    return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}:{seconds % 60:06.3f}"


def trial_captions(folder):
    """WebVTT of what was said and noted, timed like the video; cached once the trial closes.

    Returns:
        The video.vtt path, or None when the trial has no frames (and so no video).
    """
    target = folder / "video.vtt"
    detail = read_trial(folder)
    if target.exists() and detail["trial"].get("outcome") is not None:
        return target
    start = detail["video_start_ms"]
    if start is None:
        return None
    cues = []
    for event in detail["events"]:
        if event["kind"] == "end":
            cues.append((event["t_ms"] - start, f"Trial ended: {event.get('outcome')}"))
        # A spoken reply is already captioned as its transcript.
        elif event["kind"] in CAPTIONS and event.get("text") and not event.get("voice"):
            cues.append((event["t_ms"] - start, f"{CAPTIONS[event['kind']]}: {event['text']}"))
    cues.sort(key=lambda cue: cue[0])
    lines = ["WEBVTT", ""]
    for i, (at, text) in enumerate(cues):
        gap = cues[i + 1][0] - at if i + 1 < len(cues) else 6000
        # Until the next cue, but readable (>= 1.5 s) and not lingering (<= 6 s).
        end = at + min(6000, max(1500, gap))
        text = text.replace("&", "&amp;").replace("<", "&lt;")
        lines += [f"{vtt_time(at)} --> {vtt_time(end)}", text, ""]
    tmp = folder / "video.vtt.tmp"
    tmp.write_text("\n".join(lines))
    os.replace(tmp, target)
    return target


def video_audio(path, sound, first_frame_ms, length_ms):
    """audio.pcm lined up with the video: padded or cut at both ends.

    Args:
        path: the audio.pcm file.
        sound: trial.json's audio, {rate, start_ms}.
        first_frame_ms: stamp of the video's first frame.
        length_ms: the video's length.

    Returns:
        pcm (N,): int16 samples, exactly as long as the video.
    """
    rate = sound["rate"]
    pcm = np.fromfile(path, dtype="<i2")
    offset = round((sound["start_ms"] - first_frame_ms) * rate / 1000)
    pcm = np.concatenate([np.zeros(offset, dtype="<i2"), pcm]) if offset > 0 else pcm[-offset:]
    length = round(length_ms * rate / 1000)
    return np.concatenate([pcm, np.zeros(max(length - len(pcm), 0), dtype="<i2")])[:length]


def trial_video(folder):
    """frames/ as an H.264 mp4 timed by the capture times, with audio.pcm, cached once closed.

    Returns:
        The mp4 path, or None when the trial has no frames.
    """
    import av  # Installed with faster-whisper; only this route needs it.
    from PIL import Image

    target = folder / "video.mp4"
    trial = read_json(folder / "trial.json", {})
    live = trial.get("outcome") is None
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
            # Before the first frame: the header is written with the first packet.
            sound = trial.get("audio") if (folder / "audio.pcm").is_file() else None
            sound_stream = None
            if sound:
                sound_stream = container.add_stream("aac", rate=sound["rate"], layout="mono")
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
            if sound_stream:
                length_ms = offsets[-1] + 200
                pcm = video_audio(folder / "audio.pcm", sound, stamps[0], length_ms)
                frame = av.AudioFrame.from_ndarray(pcm[None], format="s16", layout="mono")
                frame.sample_rate, frame.pts = sound["rate"], 0
                frame.time_base = Fraction(1, sound["rate"])
                container.mux(sound_stream.encode(frame))
                container.mux(sound_stream.encode())
        os.replace(tmp, target)
        return target
