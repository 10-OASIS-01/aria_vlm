# Changelog

Newest first. Each entry names its commit. Add an entry with every change that affects behavior, configuration or deployment.

## 2026-10-29 — Run a session from the console, step buttons, walkthrough (commit "Run a whole session from the console, with a walkthrough")

From a second design critique, on the first-time workflow (24/40).

**Added**
- **System row in Live.** It starts and stops guidance_node, and switches the camera between Glasses, Test camera and Off.
  - The backend runs three fixed commands (`aria_guidance/system.py`), each in its own process group with aria2_ros2's environment, logging to `.runtime/logs/`.
  - Pid files let a restarted backend find the processes again.
  - Stop sends Ctrl-C to the group, then SIGKILL after 15 s.
  - Settings: `ARIA_ROS_WORKSPACE`, `ARIA_FASTDDS_PROFILE`, `ARIA_FAKE_IMAGE`.
- **Walkthrough** (top bar; offered on first visit). One practice trial on the test camera in 7 steps. Each step points at the real control and advances when the real state changes; it never presses anything itself.
- **Finish task.** `Task.finish()`, and "finish" as a typed or spoken reply, close a trial as completed without waiting for the model to propose it. A plan still running is dropped.
- The node reports `camera.window_s`, the seconds of camera buffered, so the band counts toward the 10 s the model needs.
- Trial detail carries `folder`.

**Changed behavior**
- Live buttons follow the workflow.
  - While acting: **Done with step** (D) and **Need help** (H). Need help sends "I need more help with this step."
  - When the model proposes completion: **Yes, finish** (F) and **Not yet** (N).
  - Next is gone.
  - The button the band asks for is the dark one.
- The band covers a model question ("Answer the question"), a missing camera ("No camera") and the warm-up countdown. It uses plain words: "Guidance stopped", not "Node offline".
- Keys act only when focus is not on a control, so Space presses a focused button instead of starting push-to-talk. The camera switch takes arrow keys.
- A failed process shows one line of cause, a log disclosure and a Dismiss button, instead of a traceback.
- Setup (was Connect) describes the one-time setup. Trials explains each outcome on hover and counts real trials to review.

## 2026-10-29 — Cleaner console, captions (commit "Make the console a set of views, add captions and camera strip")

From a dual-agent design critique (23/40); see `.impeccable/critique/`.

**Changed behavior**
- The workbench is a set of views (Live, Trials, Try a task, Connect) and opens on Live. The Overview hero, "This week" milestones and the Experiments notebook are removed.
- Live:
  - shows the goal;
  - shows a camera strip (Aria glasses or TEST CAMERA, with rate and frame age; the preview dims when frames are late);
  - the band maps the node's momentary state first, so it never shows two contradictory instructions.
- While a task runs, the goal form hides behind New goal. Replace goal and Cancel task each need a second press within 4 s.
- Keyboard shortcuts work only in the Live view and never reach Trials or the trial video.
- Trials:
  - the open-trial badge reads Running (links to Live);
  - test-camera trials are tagged and can be hidden;
  - the heading counts trials without a verdict;
  - an open trial whose files stopped changing for 30 s reads Interrupted.
- Contrast: `--muted` is #5f6b66, badges are 11px with darker ink, and key hints are no longer faded. Every measured text is at least 4.5:1, and secondary controls are 44px tall.

**Added**
- `GET /api/trials/{session}/{trial}/captions.vtt`, shown by default on the trial video and written as `video.vtt` next to `video.mp4`.
- `trial.json` is rewritten after each step, so open trials show their step count; list and detail responses carry `active`.

**Fixed**
- `scripts/service.sh start` refused to start right after a stop while connections sat in TIME_WAIT; its port check now uses `SO_REUSEADDR` like uvicorn.

## 2026-10-29 — Camera source in the console and trials (commit "Report which camera feeds guidance_node")

**Added**
- The console state carries `camera: {source, frame_id, hz, age_s}`: `glasses` for aria_node's `aria_rgb_optical_frame`, `fake` for `tools/fake_camera.py`, otherwise the raw frame_id. The 2 s heartbeat keeps rate and age current.
- `trial.json` records `camera`, so test-camera trials can be told apart from real ones.

**Changed behavior**
- `tools/fake_camera.py` stamps frames `fake_camera` instead of imitating aria_node's frame_id.

## 2026-10-29 — Live console in the workbench, Foxglove removed (commit "Replace the Foxglove console with a Live section in the workbench")

**Changed behavior**
- The operator console is the workbench's **Live** section (`/#live`), next to Trials. It shows the task state band, the camera preview and the instruction. Its controls: Done, Next, a hold-to-talk button, Repeat, Cancel, Goal and Note. Keys: D, N and R, and hold Space to talk.
- guidance_node links to the console through the backend it already uses: it pushes its state (on change and every 2 s) and each preview JPEG, and long-polls for commands. The relay (`aria_guidance/live.py`) holds no task state.
- Console commands are refused (503) while the node is not connected, instead of being queued.
- `/guidance/talk` reports recording, transcribing and refusals on `/guidance/state`; the state JSON gains `recording`, `heard` and `trial`.

**Removed**
- `foxglove_bridge` from `guidance.launch.py`, `foxglove/operator_layout.json`, and the Foxglove-only topics `/guidance/task`, `/guidance/instruction` and `/guidance/preview/compressed`.

**Upgrade notes**
- Rebuild (`colcon build --symlink-install --packages-select aria_guidance_ros`) and restart the backend and guidance_node. Delete `install/aria_guidance_ros/share/aria_guidance_ros/foxglove` if it remains.
- A backend restart now waits up to the node's 10 s command poll before the port is free.

## 2026-10-29 — Trials review page (commit "Add Trials review page to the workbench")

**Added**
- Workbench **Trials** section (`/#trials`, deep links `#trials/<session>/<trial>`): trial list with outcome and verdict, the trial video, a timeline of goal, spoken instructions, replies, transcripts and notes (select one to seek), and Success/Fail plus review notes saved on change. Open trials show as Live and refresh every 15 s.
- Routes `GET /api/trials`, `GET /api/trials/{session}/{trial}`, `GET .../video.mp4` and `PUT .../review` (token-guarded like every write). Session and trial IDs must be single safe path segments and resolve inside a trials root; anything else is 404.
- The video is encoded with PyAV from `frames/`, with each frame at its capture time and no B-frames, so seeking matches the timeline. It is cached in the trial folder once the trial closes.
- `ARIA_SESSIONS_DIR` (default `../aria2_ros2/data/aria`) names aria_node's session folders; `.runtime/guidance` is always searched too.

**Fixed**
- Two frame-age tests compared the age to a tenth of a second and failed under load.

**Upgrade notes**
- Set `ARIA_SESSIONS_DIR` in `.env` if the backend does not run from `aria_vlm/` next to `aria2_ros2/`. PyAV comes with the `speech` extra; without it the video route fails.

## 2026-10-29 — Foxglove operator console (commit "Add Foxglove operator console")

**Added**
- `guidance.launch.py` starts `foxglove_bridge` on port 8765. It forwards `/guidance/.*`, `/aria/tts` and `/rosout`, and lets clients publish only on `/guidance/.*`.
- `foxglove/operator_layout.json`: state band, preview, large instruction text, Done/Next, Talk on/off, Repeat/Cancel, Goal and Note editors, and the node log.
- Topics `/guidance/preview/compressed` (the 640 px JPEG already made for the model), `/guidance/task` and `/guidance/instruction` (plain strings, transient-local; Foxglove panels cannot read fields inside the `/guidance/state` JSON).
- Transcripts are logged as `heard: ...`, so they show in the Foxglove log panel.

**Upgrade notes**
- Rebuild once (`colcon build --symlink-install --packages-select aria_guidance_ros`) to install the layout and launch changes. `foxglove_bridge` must be installed (`ros-humble-foxglove-bridge`).
- The bridge listens on all interfaces. Anyone who can reach port 8765 can publish goals and replies.

## 2026-10-29 — Trial recording (commit "Record each goal as a trial next to the aria_node session")

**Added**
- `TrialRecorder` (`aria_guidance/trial.py`). Each goal opens `<session>/trials/<NN>_<HHMMSS>/` with `trial.json`, `guidance.jsonl` and `frames/`. The 10 s frame window already buffered is saved at the start, and every later preview frame is written from the node's thread pool.
- The session is the newest folder under `session_root` (default `data/aria`) whose `clock.csv` changed in the last 120 s. Otherwise trials go to `fallback_root` (default `aria_vlm/.runtime/guidance/<start time>/`).
- Outcomes: `completed`, `cancelled`, `replaced` (new goal), `aborted` (shutdown).
- Notes from `/guidance/note` and spoken `note: ...` are written to the trial as `note` events.

**Changed behavior**
- `guidance_node` writes its event log to the trial instead of the backend.

**Upgrade notes**
- Run `guidance_node` from `aria2_ros2/` (as the VS Code configs do), or set `session_root` to an absolute path, so it finds aria_node's sessions.

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
