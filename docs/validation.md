# Validation record

The backend has been deployed and tested on a notebook GPU. Aria hardware integration remains pending. Runtime results are available at `api/reports/latest` and in the downloadable `validation.json`; verification code, fixtures and raw records remain local and are excluded from this repository.

## Evidence on October 20, 2026

Both rounds used real `Qwen/Qwen3.5-9B` inference, twenty chronological images per request, a goal and recent dialogue. The notebook used an NVIDIA L20D (32 GB), BF16, SDPA and a 131,072-pixel budget per frame, with thinking disabled. No model stub was used in the visual trials.

| Round | Desk reset | Color sorting | Requests | Median model | Median HTTP | First model call |
| --- | --- | --- | --- | --- | --- | --- |
| Initial deployment | 2 moves + DONE | 3 moves + DONE | 7 | 2.56 s | 3.18 s | 32.66 s |
| After handoff, 21:59 China time | 2 moves + DONE | 3 moves + DONE | 7 | 1.27 s | 1.97 s | 4.91 s |

Both objectives succeeded in each round. The latest report is `20261020T135856Z.json` under the deployment's `.runtime/reports/`; `latest.json` points to the latest evaluation. Timings vary with initialization and shared GPU load. These few deterministic scenes do not establish a general success rate or a latency guarantee.

Rendered trials use MuJoCo with a limited kinematic action executor: the model receives pixels, and supported object moves update the next observation. Completion requires the objective to be reached and the model to return `DONE`. The trials do not test contact physics or actual human task completion.

## Functional coverage

| Capability | Verified evidence | Remaining validation |
| --- | --- | --- |
| Model loading and next-step instructions | Resident real model; two multi-step visual tasks | Varied real scenes and task layouts |
| Twenty-frame window and image conversion | Buffer jitter, gaps, stale frames, row padding and color conversion checked | Aria camera transport, orientation and distortion |
| Session control | Feedback, idempotent retry, cancellation, restart recovery and completed-goal guards checked | User-paced loop on the wearable |
| Audio and ASR | int32/WAV conversion checked; prerecorded English speech transcribed | Actual Aria microphone, noise and spoken task commands |
| ROS bridge and TTS | Topic/message definitions inspected against the pinned upstream; state transitions checked with ROS substitutes | Build with ROS 2 Humble, live topics and audible onboard speech |
| Concurrency | Recording/planning overlap guards and superseded-goal cancellation lock checked | Real timing and echo behavior on the glasses |
| Latency and experiment records | Model, HTTP and session records retained; transcription timing logged for active tasks | Full spoken-goal-to-audible-TTS latency |

The latest local regression suite passed **20 tests**. Ruff checks and formatting checks passed, Python sources parsed with Python 3.10 syntax rules, JavaScript syntax checked, and the deployed experiment page was inspected in a browser. The notebook does not have `rclpy` or a ROS 2 Humble runtime; the bridge tests use substitutes and do not prove native ROS transport.

The current implementation uses incoming RGB directly with image resizing, without Aria-specific fisheye rectification. TTS playback guards estimate speaking duration because the pinned infrastructure provides no playback completion/cancel acknowledgement. Follow the [hardware checklist](hardware-checklist.md) before counting the wearable MVP as validated.
