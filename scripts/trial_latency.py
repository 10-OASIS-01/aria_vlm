"""Print a trial's latencies up to the TTS publish and save them as latency.json in its folder.

Time to audible TTS needs the phone recording in docs/hardware-checklist.md.

Example:
  python scripts/trial_latency.py ../aria2_ros2/data/sessions/<session>/trials/01_142231
"""

import argparse
from pathlib import Path

from aria_guidance.trial import trial_latency, write_json

COLUMNS = [
    "observation_span_ms",
    "frame_age_ms",
    "http_ms",
    "server_total_ms",
    "model_ms",
    "first_token_ms",
    "publish_ms",
]


def row(label, values):
    return f"{label:>7} " + " ".join(f"{'-' if v is None else v:>{len(c)}}" for c, v in values)


def report(folder: Path) -> None:
    if not (folder / "trial.json").is_file():
        raise SystemExit(f"{folder} is not a trial folder (no trial.json)")
    latency = trial_latency(folder)
    write_json(folder / "latency.json", latency)
    print(f"{latency['id']}: {latency['goal']}")
    print(" " * 8 + " ".join(COLUMNS))
    for i, step in enumerate(latency["steps"], 1):
        label = f"{i} stale" if step["stale"] else str(i)
        print(row(label, [(c, step[c]) for c in COLUMNS]))
    for name in ("mean", "median", "max"):
        print(row(name, [(c, latency["summary"].get(c, {}).get(name)) for c in COLUMNS]))
    for said in latency["speech"]:
        print(f"stt {said['stt_ms']} ms, round trip {said['stt_roundtrip_ms']} ms: {said['text']}")
    print(
        "goal to TTS publish: "
        f"{latency['goal_to_tts_from_start_ms']} ms from speech start, "
        f"{latency['goal_to_tts_from_endpoint_ms']} ms from its endpoint"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("trials", nargs="+", type=Path, help="trial folders")
    for folder in parser.parse_args().trials:
        report(folder)


if __name__ == "__main__":
    main()
