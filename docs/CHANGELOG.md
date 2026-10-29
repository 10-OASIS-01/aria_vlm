# Changelog

Newest first. Each entry names its commit. Add an entry with every change that affects behavior, configuration or deployment.

## 2026-10-29 — Freshness check, bridge defaults, tests (`603a76f`)

**Changed behavior**
- Live frames are checked for age again. The check had been disabled with `pass` in `service.py`. `ARIA_MAX_FRAME_AGE_MS` sets the limit (default 5000; `0` disables). A rejected request returns 422 with the measured age, for example "Newest frame is 11.4 s old".
- The bridge defaults to `audio_gain: 16.0` (was 1.0). At 1.0, Aria Gen 2 speech fell below the 0.002 RMS silence cutoff and every recording returned an empty transcript.
- Failed plans store the error message, not only its type (e.g. `ValueError: Model echoed the goal instead of answering`). The 503 response carries the same text.

**Fixed**
- Bridge: after a completion proposal, saying `next` and then having the plan fail left the bridge in `done_proposed` while the backend was `ready`. A later "done" sent `confirm` and got 409. The bridge now marks the session ready once the backend accepts the feedback.

**Added**
- `tests/` with 31 pytest tests: output parsing, the confirmation state machine, frame sampling and the freshness check. Install with `pip install -e '.[dev]'`; run `python -m pytest`.
- `docs/validation.md`: current configuration and the October 23–24 live Aria sessions.

**Upgrade notes**
- Add `ARIA_MAX_FRAME_AGE_MS=5000` to `.env`, or leave it out to use the default.
- Restart the backend and the bridge. If the bridge was built without `--symlink-install`, rebuild it with `colcon build`.

## 2026-10-29 — Completion confirmation, video input, replay evaluation (`e520325`)

Work from about October 21–24, committed together. It was used in the live Aria sessions on October 23–24.

**Changed behavior**
- The model only proposes completion. `DONE` returns status `done_proposed` (was `completed`) and moves the session to `awaiting_confirmation`. A new `confirm` feedback event closes it as `completed`. Any other feedback rejects the proposal and is recorded as "No, the goal is not complete yet. …". The bridge says "Say done to confirm, or next to continue." The workbench has a **Confirm complete** button.
- The 20 frames go to Qwen as one 2 fps video (`ARIA_INPUT_MODE=video`, new; `images` keeps the old behavior). This uses half the visual tokens.
- Default `ARIA_MAX_PIXELS` is 307,200 (was 131,072), keeping full 640×480 frames. At the old budget the model missed small objects such as a pen.
- Rewritten system prompt. Question-style goals get a direct answer, and the goal is restated after the video.
- Rejected model outputs: a question that repeats the goal, and instructions over 45 words.

**Bridge**
- `request_timeout_seconds` parameter (default 120).
- 10% slack on `image_interval_ms`, so a jittery 5 Hz camera is not thinned to about 3 Hz.
- HTTP errors include the server's reason instead of a bare status code.
- The stale-camera message states the age of the newest frame.

**Added**
- `scripts/replay_eval.py`: replays saved observations through the local model to compare prompts, input modes and pixel budgets, without the server or ROS.

**Known issue in this commit:** the live-frame freshness check was commented out. It was restored in `603a76f`.

## 2026-10-20 — Initial release (`ff5b76f`, `d6d643b`, `b9e973c`)

- Qwen3.5-9B guidance backend (FastAPI, SQLite sessions, web workbench, faster-whisper ASR).
- `aria_planner_bridge` ROS 2 node for `aria2_ros2` (camera, audio and TTS topics; goal, command and push-to-talk controls).
- Deployment documentation, the hardware checklist and validation notes from rendered task trials.
