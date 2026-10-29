"""Replay saved observations through the local Qwen engine and score the answers.

Compares prompt, input-mode and pixel-budget variants offline (no HTTP, no ROS). Each case
reuses the 20 frames the backend saved for one plan (<data_dir>/runs/<plan id>/00-19.jpg).

Cases file (JSON list):
  {"name": "pens-step2", "goal": "...", "history": [{"role": "user", "text": "Next."}],
   "frames": "a087a067", "expect": ["instruction"], "mention_all": ["pen"], "mention_any": [],
   "min_words": 0}
Every case also fails when the answer repeats the last assistant turn word for word.

Example:
  python scripts/replay_eval.py --cases .runtime/eval_cases.json \
      --modes images video --pixels 131072 307200 --prompt-file old=old_prompt.txt
"""

import argparse
import json
import statistics
from pathlib import Path

from PIL import Image

from aria_guidance import engine as eng
from aria_guidance.config import Settings


def load_frames(runs: Path, prefix: str) -> list:
    matches = sorted(runs.glob(prefix + "*"))
    if len(matches) != 1:
        raise SystemExit(f"Frames '{prefix}' match {len(matches)} run folders in {runs}")
    paths = [matches[0] / f"{i:02d}.jpg" for i in range(eng.FRAME_COUNT)]
    if missing := [p.name for p in paths if not p.exists()]:
        raise SystemExit(f"{matches[0].name} is missing {missing}")
    return [Image.open(p).convert("RGB") for p in paths]


def score(case: dict, status: str, text: str) -> bool:
    lowered = text.lower()
    said = [t["text"] for t in case.get("history", []) if t["role"] == "assistant"]
    return (
        status in case["expect"]
        and not (said and eng._normalized(text) == eng._normalized(said[-1]))
        and len(text.split()) >= case.get("min_words", 0)
        and all(word.lower() in lowered for word in case.get("mention_all", []))
        and (not case.get("mention_any") or any(w.lower() in lowered for w in case["mention_any"]))
    )


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
    args = parser.parse_args()

    cases = json.loads(args.cases.read_text())
    prompts = {"current": eng.SYSTEM_PROMPT}
    for item in args.prompt_file:
        label, _, path = item.partition("=")
        prompts[label] = Path(path).read_text().strip()
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
                    row["pass"] = score(case, row["status"], row["text"])
                    results.append(row)
                    print(
                        f"{'PASS' if row['pass'] else 'FAIL'} {mode:6s} {pixels:>7} {label:8s} "
                        f"{case['name']:18s} {row['status']:13s} "
                        f"{row.get('model_ms', 0):>6.0f}ms {row['text'][:80]!r}",
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
            + f"  4-line format {sum(bool(r.get('proposal', {}).get('evidence')) for r in rows)}"
            + (f"  failed: {', '.join(failed)}" if failed else "")
        )
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
