# Changelog

Newest first. Each entry names its commit. Add an entry with every change that affects behavior, configuration or deployment.

## 2026-10-29 — Task state in guidance_node, stateless backend (commit "Move task state into guidance_node, add stateless /v1/plan")

**Changed behavior**
- `guidance_node` owns the task: goal, the 24-turn dialogue history and the step state (`aria_guidance/task.py`). It calls the new stateless `POST /v1/plan` and no longer creates backend sessions or posts client events. A backend restart mid-task keeps the goal and history; the next reply plans again.
- The `/planner/*` interface is replaced, with no aliases: `/guidance/goal`, `/guidance/reply` (was `/planner/command`), `/guidance/note` (new), `/guidance/talk` (was `/planner/listen`), `/guidance/state` (was `/planner/status`, now transient-local with `task`, `goal` and `instruction` fields) and `/guidance/transcript`.
- Confirming a completion is local and immediate; it no longer waits for a backend round trip.
- A new plan waits for an older request still in flight instead of hitting 429.
- A result older than `max_result_age_seconds` now fails the step (state `error`) instead of being kept in history unspoken. Say `next` to observe again.
- `note: ...` (spoken or on `/guidance/reply`) and `/guidance/note` are accepted. They are only logged until trial recording lands.

**Added**
- Backend: `POST /v1/plan` (`{goal, history, frames, live}`) and `POST /v1/asr`. `/api/sessions*` stays for the workbench.
- `GuidanceClient.plan_v1`; `GuidanceClient.transcribe` now calls `/v1/asr`.
- Tests: `test_task.py`, `test_v1_plan.py`, `test_client.py`, `test_audio.py`, and ROS integration tests for confirm, cancel during inference, backend restart and a stopped camera.

**Upgrade notes**
- Rebuild is not needed with `--symlink-install`; restart the backend and `guidance_node`.
- Retarget scripts from `/planner/*` to `/guidance/*`. `tools/ptt.py` and the VS Code tasks are already retargeted.

## 2026-10-29 — Rename to aria_guidance_ros, test harness (commit "Rename bridge to aria_guidance_ros, add integration test harness")

No behavior change; the `/planner/*` topics are unchanged.

**Renamed**
- ROS package `aria_planner_bridge` is now `aria_guidance_ros`. Executable and node `planner_bridge` are now `guidance_node`. `bridge.launch.py` and `bridge.yaml` are now `guidance.launch.py` and `guidance.yaml`.
- Debug tools moved from the workspace `.vscode/` to `tools/`. The integration notes moved to `docs/architecture.md`.

**Added**
- `tests/unit/` (the existing tests) and `tests/integration/`: a real backend with a scripted `StubEngine` (`tests/stubs.py`), a 5 Hz camera and `guidance_node` on ROS domain 77, plus a goal-to-TTS smoke test.

**Upgrade notes**
- In `aria2_ros2/src`, replace the `aria_planner_bridge` symlink with `aria_guidance_ros`, delete `build/aria_planner_bridge` and `install/aria_planner_bridge`, then `colcon build --symlink-install --packages-select aria_guidance_ros`.
- Launch with `ros2 launch aria_guidance_ros guidance.launch.py`. A custom config file needs its top key renamed from `planner_bridge:` to `guidance_node:`.

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
