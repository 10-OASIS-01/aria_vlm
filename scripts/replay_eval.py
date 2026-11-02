"""Replay saved observations through the local Qwen engine and score the answers.

Compares prompt, input-mode and pixel-budget variants offline (no HTTP, no ROS). Each case
reuses the 20 frames the backend saved for one plan (<data_dir>/runs/<plan id>/00-19.jpg).

Cases file (JSON list):
  {"name": "pens-step2", "goal": "...", "history": [{"role": "user", "text": "Next."}],
   "frames": "a087a067", "expect": ["instruction"], "mention_all": ["pen"], "mention_any": [],
   "min_words": 0, "expect_mode": ["ACT"], "expect_check": ["yes"], "plan_excludes": ["ipad"]}
Every case also fails when the answer repeats the SAY of the last assistant turn word for word.

Example:
  python scripts/replay_eval.py --cases .runtime/eval_cases.json \
      --modes images video --pixels 131072 307200 --prompt-file old=old_prompt.txt

Trials mode replays every decision point of recorded aria2_ros2 trials (guidance.jsonl and frames/)
with the history guidance_node would have sent, using the .env input mode and pixel budget, and
scores it against labels per decision point (scripts/trial_labels.json documents them):
  python scripts/replay_eval.py --trials scripts/trial_labels.json --split heldout
"""

import argparse
import json
import statistics
from pathlib import Path

from PIL import Image

from aria_guidance import engine as eng
from aria_guidance.config import Settings
from aria_guidance.task import REJECTION

# Replies the node handles as commands; they never reach the history.
COMMANDS = {"cancel", "finish", "confirm"}


def load_frames(runs: Path, prefix: str) -> list:
    matches = sorted(runs.glob(prefix + "*"))
    if len(matches) != 1:
        raise SystemExit(f"Frames '{prefix}' match {len(matches)} run folders in {runs}")
    paths = [matches[0] / f"{i:02d}.jpg" for i in range(eng.FRAME_COUNT)]
    if missing := [p.name for p in paths if not p.exists()]:
        raise SystemExit(f"{matches[0].name} is missing {missing}")
    return [Image.open(p).convert("RGB") for p in paths]


def score(case: dict, status: str, text: str, proposal: dict) -> bool:
    lowered = text.lower()
    said = [t["text"] for t in case.get("history", []) if t["role"] == "assistant"]
    last_say = eng.parse_output(said[-1])[0] if said else ""
    check = eng._normalized(proposal.get("check", "")).split(" ")[0]
    plan = proposal.get("plan", "").lower()
    return (
        status in case["expect"]
        and eng._normalized(text) != eng._normalized(last_say)
        and proposal.get("mode") in case.get("expect_mode", [proposal.get("mode")])
        and check in case.get("expect_check", [check])
        and not any(w.lower() in plan for w in case.get("plan_excludes", []))
        and len(text.split()) >= case.get("min_words", 0)
        and all(word.lower() in lowered for word in case.get("mention_all", []))
        and (not case.get("mention_any") or any(w.lower() in lowered for w in case["mention_any"]))
    )


def trial_points(folder: Path):
    """Yields (index, goal, history, images, timestamps) per plan request, as the node sent them."""
    events = [json.loads(line) for line in open(folder / "guidance.jsonl") if line.strip()]
    goal, history, last_status, index = None, [], None, 0
    for e in events:
        if e["kind"] == "goal":
            goal = e["text"]
        elif e["kind"] == "plan_request":
            frames = [folder / "frames" / f"{int(t)}.jpg" for t in e["frames"]]
            images = [Image.open(path).convert("RGB") for path in frames]
            yield index, goal, list(history), images, e["frames"]
            index += 1
        elif e["kind"] == "plan_result":
            history.append({"role": "assistant", "text": e["instruction"]})
            last_status = e["status"]
        elif e["kind"] == "reply" and e["text"].lower().strip(".") not in COMMANDS:
            text = REJECTION + e["text"] if last_status == "done_proposed" else e["text"]
            history.append({"role": "user", "text": text})


def trial_failures(label: dict, status: str, repeated: bool, check: str) -> dict:
    """Which metric this answer counts toward (None: not applicable) and whether it failed."""
    done = status == "done_proposed"
    check = eng._normalized(check).split(" ")[0]
    return {
        "repeated error": repeated if label["prev_done"] or label["rejected"] else None,
        "DONE too early": done if label["goal_met"] is False else None,
        "missed DONE": not done if label["goal_met"] is True else None,
        "CHECK wrong": (check == "yes") != label["prev_done"]
        if label["prev_done"] is not None and check in {"yes", "no"}
        else None,
    }


def run_trials(args, prompts):
    labels = json.loads(args.trials.read_text())
    engine = eng.QwenEngine(Settings())
    engine.load()
    if engine.state != "ready":
        raise SystemExit(f"Model failed to load: {engine.error}")
    results = []
    for label_name, prompt in prompts.items():
        eng.SYSTEM_PROMPT = prompt
        for split in args.split:
            for trial, points in labels[split].items():
                for index, goal, history, images, stamps in trial_points(args.sessions / trial):
                    row = {"prompt": label_name, "split": split, "trial": trial, "point": index}
                    try:
                        g = engine.generate(goal, history, images, stamps)
                        seconds = (g.timings["model_ms"] + g.timings.get("guard_ms", 0)) / 1000
                        row.update(status=g.status, text=g.instruction, proposal=g.proposal,
                                   seconds=seconds)
                    except ValueError as e:
                        row.update(status="rejected", text=str(e), proposal={}, seconds=0.0)
                    said = [t["text"] for t in history if t["role"] == "assistant"]
                    with engine.lock:  # _same_step expects the lock held, as inside generate.
                        repeated = bool(said) and engine._same_step(row["text"], said[-1])
                    row["fails"] = trial_failures(points[index], row["status"], repeated,
                                                  row["proposal"].get("check", ""))
                    results.append(row)
                    bad = [m for m, f in row["fails"].items() if f]
                    print(f"{split:7s} {label_name:8s} {trial.split('/')[-1]} p{index} "
                          f"{row['status']:13s} {'FAIL ' + ', '.join(bad) if bad else 'ok':28s} "
                          f"{row['text'][:90]!r}", flush=True)

    print("\nSummary (failures / applicable points, mean seconds per plan):")
    for label_name in prompts:
        for split in args.split:
            rows = [r for r in results if (r["prompt"], r["split"]) == (label_name, split)]
            counts = []
            for metric in rows[0]["fails"]:
                cases = [r["fails"][metric] for r in rows if r["fails"][metric] is not None]
                counts.append(f"{metric} {sum(cases)}/{len(cases)}")
            print(f"  {label_name:8s} {split:7s} " + "  ".join(counts)
                  + f"  {statistics.mean(r['seconds'] for r in rows):.1f} s"
                  + f"  guard used {sum('guard' in r['proposal'] for r in rows)}")
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cases", type=Path, default=Path(".runtime/eval_cases.json"))
    parser.add_argument("--modes", nargs="+", default=["images", "video"])
    parser.add_argument("--pixels", nargs="+", type=int, default=[131072, 307200])
    parser.add_argument(
        "--prompt-file",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="Extra system prompt to compare; the engine's current prompt is always 'current'",
    )
    parser.add_argument("--out", type=Path, help="Write every result as JSON")
    parser.add_argument("--trials", type=Path, help="Labels file: replay recorded trials instead")
    parser.add_argument("--split", nargs="+", default=["dev"], choices=["dev", "heldout"])
    parser.add_argument("--sessions", type=Path, default=Path("../aria2_ros2/data/sessions"))
    args = parser.parse_args()

    prompts = {"current": eng.SYSTEM_PROMPT}
    for item in args.prompt_file:
        label, _, path = item.partition("=")
        prompts[label] = Path(path).read_text().strip()
    if args.trials:
        run_trials(args, prompts)
        return
    cases = json.loads(args.cases.read_text())
    base = Settings()
    frames = {c["frames"]: load_frames(base.data_dir / "runs", c["frames"]) for c in cases}
    timestamps = [i * 1000 / eng.FRAME_RATE_HZ for i in range(eng.FRAME_COUNT)]

    # Keep the raw answer even when parse_output rejects it (e.g. leaked reasoning).
    raw_seen = {}
    parse = eng.parse_output

    def capture(raw, goal=""):
        raw_seen["raw"] = raw
        return parse(raw, goal)

    eng.parse_output = capture

    results = []
    for mode in args.modes:
        for pixels in args.pixels:
            settings = Settings()
            settings.input_mode, settings.max_pixels = mode, pixels
            engine = eng.QwenEngine(settings)
            engine.load()
            if engine.state != "ready":
                raise SystemExit(f"Model failed to load: {engine.error}")
            for label, prompt in prompts.items():
                eng.SYSTEM_PROMPT = prompt
                for case in cases:
                    raw_seen.clear()
                    row = {"mode": mode, "pixels": pixels, "prompt": label, "case": case["name"]}
                    try:
                        history, images = case.get("history", []), frames[case["frames"]]
                        g = engine.generate(case["goal"], history, images, timestamps)
                        row.update(
                            status=g.status,
                            text=g.instruction,
                            model_ms=g.timings["model_ms"],
                            input_tokens=g.input_tokens,
                            proposal=g.proposal,
                        )
                    except ValueError as e:
                        row.update(status="rejected", text=f"{e}: {raw_seen.get('raw', '')}")
                    row["pass"] = score(case, row["status"], row["text"], row.get("proposal", {}))
                    results.append(row)
                    answer = row.get("proposal", {})
                    print(
                        f"{'PASS' if row['pass'] else 'FAIL'} {mode:6s} {pixels:>7} {label:8s} "
                        f"{case['name']:18s} {row['status']:13s} "
                        f"{row.get('model_ms', 0):>6.0f}ms {answer.get('mode', ''):7s} "
                        f"check={answer.get('check', '')[:8]!r} {row['text'][:80]!r}",
                        flush=True,
                    )
            del engine
            try:
                import torch

                torch.cuda.empty_cache()
            except ImportError:
                pass

    print("\nSummary (passes / cases, median model time, median input tokens):")
    configs = sorted({(r["mode"], r["pixels"], r["prompt"]) for r in results})
    for mode, pixels, label in configs:
        rows = [
            r for r in results if (r["mode"], r["pixels"], r["prompt"]) == (mode, pixels, label)
        ]
        timed = [r for r in rows if "model_ms" in r]
        failed = [r["case"] for r in rows if not r["pass"]]
        print(
            f"  {mode:6s} {pixels:>7} {label:8s} {len(rows) - len(failed)}/{len(rows)}  "
            f"{statistics.median(r['model_ms'] for r in timed):>6.0f} ms  "
            f"{statistics.median(r['input_tokens'] for r in timed):>6.0f} tok"
            + f"  with PLAN {sum(bool(r.get('proposal', {}).get('plan')) for r in rows)}"
            + (f"  failed: {', '.join(failed)}" if failed else "")
        )
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
