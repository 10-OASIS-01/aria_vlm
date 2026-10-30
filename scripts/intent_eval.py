"""Score the running backend's intent classifier on hand-labelled utterances, and time it.

Cases file (JSON list, words after "Aria"; status done_proposed means the guide just asked whether
the task is complete):
  {"text": "I can't find it", "label": "help", "status": "instruction"}

Example:
  python scripts/intent_eval.py --out .runtime/intent_eval.json
"""

import argparse
import json
import statistics
import time
from pathlib import Path

from aria_guidance.client import GuidanceClient

GOAL = "Put the black marker in the green mug."
INSTRUCTION = "Pick up the black marker on the table, just to your right."
STATES = {"instruction": "awaiting_feedback", "done_proposed": "awaiting_confirmation"}
CLOSING = {"cancel", "finish"}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("intent_cases.json"))
    parser.add_argument("--url", default="http://127.0.0.1:8785")
    parser.add_argument("--out", type=Path, help="Write every result as JSON")
    args = parser.parse_args()

    client = GuidanceClient(args.url)
    results = []
    for case in json.loads(args.cases.read_text()):
        state = STATES[case.get("status", "instruction")]
        started = time.perf_counter()
        answer = client.intent(case["text"], GOAL, state, INSTRUCTION)
        row = {
            **case,
            "got": answer["label"],
            "raw": answer["raw_output"],
            "model_ms": answer["timings"]["model_ms"],
            "roundtrip_ms": (time.perf_counter() - started) * 1000,
            "input_tokens": answer["input_tokens"],
        }
        row["pass"] = row["got"] == row["label"]
        results.append(row)
        print(
            f"{'PASS' if row['pass'] else 'FAIL'} {row['label']:10s} {row['got']!s:10s} "
            f"{row['model_ms']:>6.0f}ms {case['text']!r}",
            flush=True,
        )

    print("\nPer label (right / cases):")
    for label in sorted({r["label"] for r in results}):
        rows = [r for r in results if r["label"] == label]
        print(f"  {label:10s} {sum(r['pass'] for r in rows)}/{len(rows)}")
    right = sum(r["pass"] for r in results)
    print(f"  {'all':10s} {right}/{len(results)}")
    # Harmless: a closing label only speaks a hint. Still worth seeing, as is the reverse.
    hinted = [r["text"] for r in results if r["got"] in CLOSING and r["label"] not in CLOSING]
    print(f"Wrongly hinted to close: {hinted or 'none'}")
    # The first call also loads whatever the model has not touched yet.
    ms = sorted(r["roundtrip_ms"] for r in results[1:])
    print(
        f"Round trip without the first call: median {statistics.median(ms):.0f} ms, "
        f"max {ms[-1]:.0f} ms; first {results[0]['roundtrip_ms']:.0f} ms; "
        f"median {statistics.median(r['input_tokens'] for r in results):.0f} input tokens"
    )
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
