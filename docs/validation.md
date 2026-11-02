# Validation record

The backend has been deployed and tested on a notebook GPU, and the bridge has run against live Aria topics (October 23–24). The [hardware checklist](hardware-checklist.md) has not been completed. Runtime results are available at `api/reports/latest` and in the downloadable `validation.json`; verification code, fixtures and raw records remain local and are excluded from this repository.

## Evidence on October 20, 2026

Both rounds used real `Qwen/Qwen3.5-9B` inference, twenty chronological images per request, a goal and recent dialogue. The notebook used an NVIDIA L20D (32 GB), BF16, SDPA and a 131,072-pixel budget per frame, with thinking disabled. No model stub was used in the visual trials.

| Round | Desk reset | Color sorting | Requests | Median model | Median HTTP | First model call |
| --- | --- | --- | --- | --- | --- | --- |
| Initial deployment | 2 moves + DONE | 3 moves + DONE | 7 | 2.56 s | 3.18 s | 32.66 s |
| After handoff, 21:59 China time | 2 moves + DONE | 3 moves + DONE | 7 | 1.27 s | 1.97 s | 4.91 s |

Both objectives succeeded in each round. The latest report is `20261020T135856Z.json` under the deployment's `.runtime/reports/`; `latest.json` points to the latest evaluation. Timings vary with initialization and shared GPU load. These few deterministic scenes do not establish a general success rate or a latency guarantee.

Rendered trials use MuJoCo with a limited kinematic action executor: the model receives pixels, and supported object moves update the next observation. Completion requires the objective to be reached and the model to return `DONE`. The trials do not test contact physics or actual human task completion.

## Current configuration

Since the October 20 trials the defaults changed; those trials do not measure the current setup.

- The 20 frames reach the model as one 2 fps video (`ARIA_INPUT_MODE=video`), at up to 307,200 pixels per frame (`ARIA_MAX_PIXELS`), which keeps the bridge's full 640×480 frames.
- A model `DONE` only proposes completion (`done_proposed`). The person confirms (`confirm`) before a session is `completed`; any other feedback rejects the proposal.
- Live frames older than `ARIA_MAX_FRAME_AGE_MS` (default 5000) on arrival are rejected with the measured age.
- The bridge defaults to `audio_gain: 16.0` for the Aria Gen 2 microphones.

## Live Aria sessions, October 23–24, 2026

The bridge ran with the glasses' live `/aria` topics and the resident model. These figures come from the backend's session store. They are not a checklist run: goals were ad hoc (pens, a mug, an iPad, a water bottle) and nobody recorded whether the tasks were physically completed.

| Measure | Value |
| --- | --- |
| Live sessions | 29: 17 cancelled, 6 awaiting feedback, 3 awaiting confirmation, 2 ready, 1 completed |
| Sessions with two or more plans | 8 (up to 7 plans in one session) |
| Plan requests | 36: 34 answered, 2 failed with a rejected model output (the error message was not recorded at the time) |
| Answers | 25 instructions, 3 clarifications, 4 completion proposals, 2 completions under the earlier flow without confirmation |
| Model time | median 1.17 s (0.71–2.43 s); median server time 1.22 s |
| Frame age on arrival | median 0.36 s; one request at 11.4 s, which the freshness check now rejects |

The single `completed` session predates the confirmation step. Audible TTS latency and human task completion were not measured.

## Functional coverage

| Capability | Verified evidence | Remaining validation |
| --- | --- | --- |
| Model loading and next-step instructions | Resident real model; two multi-step visual tasks | Varied real scenes and task layouts |
| Twenty-frame window and image conversion | Buffer jitter, gaps, stale frames, row padding and color conversion checked | Aria camera transport, orientation and distortion |
| Session control | Feedback, idempotent retry, cancellation, restart recovery and completed-goal guards checked | User-paced loop on the wearable |
| Audio and ASR | int32/WAV conversion checked; prerecorded English speech transcribed | Actual Aria microphone, noise and spoken task commands |
| ROS bridge and TTS | Topic/message definitions inspected against the pinned upstream; state transitions checked with ROS substitutes; live `/aria` topics used on October 23–24 (33 TTS publications logged) | Audible onboard speech and its latency, recorded under the checklist |
| Concurrency | Recording/planning overlap guards and superseded-goal cancellation lock checked | Real timing and echo behavior on the glasses |
| Latency and experiment records | Model, HTTP and session records retained; transcription timing logged for active tasks | Full spoken-goal-to-audible-TTS latency |

The October 20 local regression suite passed 20 tests; it was not part of this repository. The repository now includes `tests/` (`python -m pytest`, install with `.[dev]`), covering output parsing, the confirmation state machine, frame sampling and the frame-freshness check. The October 20 round also passed Ruff checks and formatting checks, Python sources parsed with Python 3.10 syntax rules, JavaScript syntax checked, and the deployed experiment page was inspected in a browser. The notebook does not have `rclpy` or a ROS 2 Humble runtime; the bridge tests use substitutes and do not prove native ROS transport.

The current implementation uses incoming RGB directly with image resizing, without Aria-specific fisheye rectification. TTS playback guards estimate speaking duration because the pinned infrastructure provides no playback completion/cancel acknowledgement. Follow the [hardware checklist](hardware-checklist.md) before counting the wearable MVP as validated.
