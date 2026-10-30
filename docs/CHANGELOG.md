# Changelog

Newest first. Each entry names its commit. Add an entry with every change that affects behavior, configuration or deployment.

## 2026-10-30 — Live microphone feed in the console (commit "Show what the microphone hears in the console")

In the first trial on the glasses most speech was not acted on, and the console could not show why.

**Added**
- Live view card **What the microphone hears**: the last 20 utterances, each with its time, text and outcome, including ignored speech, empty transcripts and recognizer failures. It also shows the classifier's label and any language other than English.
- A microphone state on the same card: listening, hearing speech, transcribing, deaf while the glasses speak, Hold to talk, open mic off, or no audio.
- `mic` and `feed` in the console state from guidance_node (`/v1/live/state`, `/api/live/state`).
- README section "Run a task by voice": the path of one command, a worked example, and what to do for each thing the card shows.

**Privacy**
- Ignored speech's text now reaches the console, which the open-mic change kept to a debug log. It stays in guidance_node's and the backend's memory. It is not on `/guidance/state` and not in the trial folder, and it is readable from `/api/live/state` by anyone who can reach the backend.

**Found in that trial** (`audio.pcm` replayed offline; not changed here)
- The deaf window clips replies: "I want to place the mug" became the goal "Yes, the mug", because the speech began before the estimated end of "What is the goal?" plus the 0.5 s guard.
- A pause of 0.8 s ends an utterance, so the rest of that goal was ignored as speech without "Aria".
- "Next, Aria next" is not addressed, because "Aria" must come first.
- "Aria done" came back as "done." from the cut clip, at every pre-roll tried (0.3, 0.6 and 1.0 s), though the whole file transcribes it. One sample.
- Auto-detect read a short English clip as German.

**Upgrade notes**
- Restart Guidance from the console and reload the page. The backend needs no restart.

## 2026-10-30 — "Aria" open-mic commands with an intent classifier (commit "Add Aria open-mic commands with an intent classifier")

The wearer had to hold push-to-talk, and anything that wasn't an exact word was feedback or, with no task, a new goal. An open mic would have turned any chatter into a trial. Now the mic stays on and "Aria" marks speech meant for the system. [ROS control](ros-control.md) has the full tables and limits.

**Changed behavior**
- With `open_mic: true`, speech without "Aria" is dropped, unless a reply window is open. Its text is not saved.
- "Aria, trial start, ..." opens a trial, and "Aria, trial start" alone asks for the goal. In a running trial, it closes that trial as `replaced`.
- Exact words ("Aria, stop", "Aria, finish", "Aria, trial end", "Aria, done", "Aria, next", "Aria, repeat", "Aria, note: ...") act without a model call. `trial end`, `end trial` and `goal end` also finish a task on Hold to talk and `/guidance/reply`.
- Anything else addressed in a running trial is classified: `done`, `help`, `correction`, `repeat`, `cancel`, `finish` or `ignore`. **The model can never close a trial.** `cancel` and `finish` only say how to.
- A finished plan's instruction is held while an utterance is pending or someone is mid-sentence (at most 8 s). If the speech is an action, the instruction is dropped and a new plan runs.
- `/guidance/talk` is also refused while an instruction is held. Otherwise the recording's reply would be refused as busy.
- Captions and the Trials timeline say `Human:` and `System:`, not `Goal`, `Guide`, `Operator`, `Heard` or `Spoken`. A spoken reply appears once: its `reply` event carries `voice: true`.
- `video.mp4` gains an AAC track from the glasses' microphones.

**Added**
- `POST /v1/intent`: a text-only label from the Qwen model already loaded, with no frames. It waits up to 15 s for a running plan, where `/v1/plan` answers 429 at once.
- `audio.pcm` in each trial folder (mono int16), and `audio: {rate, start_ms}` in `trial.json`. It starts about 10 s before the goal, like the frames.
- `transcript` events carry `intent`.
- guidance_node parameters `open_mic` (false in code, true in `guidance.yaml`) and `open_mic_threshold` (0.006 RMS after gain).
- `aria_guidance/intent.py`, `audio.Endpointer`, `Task.abandon_plan`, `scripts/intent_eval.py` and `scripts/intent_cases.json`.
- `tests/integration/test_open_mic.py`.

**Measured** (RTX 5090, no glasses and no real voices yet)
- Classifier: 45/46 on hand-labelled cases, and 29/30 on the cases not copied from the prompt's examples. The one miss is "uh, okay so", labelled `done` instead of `ignore`.
- Classifier round trip: median 284 ms, max 518 ms, first call 1.7 s.
- Whisper: about 1.5 s per utterance on CPU, including 1 s clips. That is above the 1 s hoped for.
- End of speech to the first spoken response, on a full rehearsal with `espeak-ng` speech and the test camera: 2.9 s for "Okay", and 4.5 s for the first instruction of a new trial.
- Wake word, same speech at 0.02 RMS with room noise, through `Endpointer`: 14/14 addressed or not as intended. At 0.0096 RMS, the measured level of real speech: 13/14 with 100 ms blocks and 11/14 with 20 ms blocks. Whisper garbled more words there too, such as "Aria Star" for "Aria, stop", which does nothing rather than close the trial. Real voices may differ.
- The hotword added "Aria" to no chatter clip and no room-noise clip (0/9). Without about 0.3 s of lead-in, Whisper drops "Aria" from the start of speech, so the endpointer's pre-roll is required.

**Upgrade notes**
- Restart the backend and guidance_node. With `--symlink-install`, no rebuild is needed.
- **Bystanders' voices are saved.** Every new trial keeps all microphone audio, so tell people nearby. Set `open_mic: false` to go back to Hold to talk only; trials still save audio.
- Old trials keep their silent video and old caption labels, because closed trials' `video.mp4` and `video.vtt` are cached. Deleting an old `video.vtt` regenerates it with the new labels.
- `open_mic_threshold` and the block size are not yet checked on the glasses. Run `tools/mic_level.py` and the live steps in the plan first.

## 2026-10-30 — The model checks its last step and keeps a plan (commit "Have Qwen check its last step and keep a plan")

The model only saw what it had said aloud, not what it had predicted, so it could not verify a step. With the old prompt, a "done" on an unchanged scene moved on to the next step.

**Changed behavior**
- The model answers in seven lines. Only `SAY` is spoken.
  - New `CHECK`: is the last `EXPECT` true now (`yes`, `no`, `unknown`, `none`)? "done" is treated as a claim.
  - New `UNKNOWN`: a fact the step depends on that the scene does not show. ACT is only for when it is `none`; INSPECT and ASK resolve it.
  - New `PLAN`: the steps left, separated by `;`.
- Assistant turns in the history now hold the model's whole answer, not only the spoken line, in guidance_node and in workbench sessions. The next plan can read its own `EXPECT` and `PLAN`.
- `max_new_tokens` is 256 (was 192).
- Older four-line and one-line answers still parse; the missing fields are empty.

**Added**
- `proposal` gains `check`, `unknown` and `plan`. guidance_node writes them into `plan_result`.
- `scripts/replay_eval.py`: optional `expect_mode`, `expect_check` and `plan_excludes`. The repeat check compares against the last turn's `SAY`, since turns now hold every line.

**Measured** (replay on the RTX 5090, 17 cases from a few desk scenes, also used to tune the prompt)
- 14/17 against 9/17 for the four-line prompt. One of the old prompt's failures (`verify-passed`) only reflects that it writes no `CHECK` line; its step was right.
- Median model time 2.2 s against 1.9 s.
- Still failing: `pens-help` (help only rephrases the step), `correction-replan` (`SAY` repeats the correction and `PLAN` keeps the old steps), `estop-visible` (calls a visible object's location unknown).
- Restating the last reply after the video fixed `correction-replan` but broke `verify-passed`, so it is not included. Small wording changes flipped several cases.

**Upgrade notes**
- Restart the backend; guidance_node needs no rebuild.

## 2026-10-29 — Descriptive instructions with a logged proposal (commit "Make Qwen's instructions descriptive and log its proposal")

In today's trials, instructions were about 10 words ("Reach for the black marker on the grey table."). 4 of 7 replans repeated the last instruction word for word, including after "I need more help with this step."

**Changed behavior**
- The model answers in four lines: `EVIDENCE`, `MODE` (ACT, INSPECT, ASK, REPLAN or DONE), `SAY` and `EXPECT`. Only `SAY` is spoken.
- `SAY` runs 10 to 40 words. It names the object and where it is from the wearer's view (left, right, ahead, next to a landmark), and says how only when that isn't obvious.
- The prompt says what replies mean.
  - "done" or "next": check the scene, then advance or say what is still visible.
  - A request for help: same step, in smaller motions.
  - A correction: follow it, with REPLAN if the plan changes.
  - The model must never repeat its last SAY word for word.
- DONE speaks the model's SAY line (for example "The orange pen is in the green mug."), followed by the confirm hint. Without a SAY line it still speaks "The task looks complete."
- The spoken-word limit is 60 (was 45). `max_new_tokens` is 192 (was 96); expect about +1 s per plan.
- The old one-line answer still parses (mode ACT, ASK or DONE, empty evidence). An unknown mode or a missing SAY line fails the plan.

**Added**
- `proposal: {mode, evidence, expect}` in `/v1/plan` and session plan responses. guidance_node writes it into `plan_result` in `guidance.jsonl`.
- `scripts/replay_eval.py`:
  - every case fails if it repeats the last assistant turn;
  - optional `min_words`;
  - the summary counts four-line answers.
- New replay cases `pens-help` and `pens-done-unchanged`. `.runtime/prompts/one_line.txt` holds the previous prompt for comparison.

**Upgrade notes**
- Restart the backend; guidance_node needs no rebuild.

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
- Setup (was Connect) describes the one-time setup; its text, the sidebar and the footer now reach at least 5:1 contrast at 11px or larger. Trials explains each outcome on hover and counts real trials to review.

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
