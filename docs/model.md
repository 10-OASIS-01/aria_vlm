# Model notes

## Inference settings

The model uses BF16, SDPA, `enable_thinking=False`, greedy decoding and at most 256 output tokens. Greedy decoding departs from the model card's sampling recommendation, to keep the guidance loop deterministic.

## Input modes

The 20 frames of each observation reach the model as one 2 fps video (`ARIA_INPUT_MODE=video`). Qwen's processor adds `<t seconds>` timestamps and packs two frames per temporal patch, so a request uses half the visual tokens of 20 separate images (`ARIA_INPUT_MODE=images`).

The default budget, `ARIA_MAX_PIXELS=307200`, keeps the full 640×480 frames from `guidance_node`. At 131,072 pixels the model misses small objects such as a pen on a mat.

| Input at 640×480 | Model time per request |
| --- | --- |
| One 2 fps video (default) | About 0.8 s |
| 20 separate images | About 1.3 s |

These were measured on an RTX 5090 with replayed observations.

## Replay saved observations

To compare prompts, input modes or pixel budgets, replay saved observations through the local model, with no server or ROS involved:

```bash
python scripts/replay_eval.py --cases .runtime/eval_cases.json \
    --modes images video --pixels 131072 307200 --prompt-file old=path/to/prompt.txt
```

Each case names a saved plan whose frames are replayed (`.runtime/runs/<plan id>/`), the goal and dialogue, the expected status and words that must appear. The docstring of [replay_eval.py](../scripts/replay_eval.py) gives the format. Stop the backend first, or make sure the GPU has room for a second copy of the model.
