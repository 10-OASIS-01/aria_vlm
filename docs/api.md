# API and timing

All URLs below are relative to the backend base URL, including a proxy prefix when used. FastAPI's interactive schema is available at `/docs` and `/openapi.json`.

## Minimal Python example

```python
from pathlib import Path
from PIL import Image
import base64
from aria_guidance.client import GuidanceClient
from aria_guidance.frames import encode_jpeg

client = GuidanceClient("http://127.0.0.1:8785")
session = client.create_session("Place the red mug on the coaster.", source="replay")
paths = sorted(Path("example-frames").glob("*.jpg"))
assert len(paths) == 20
frames = []
for i, path in enumerate(paths):
    with Image.open(path) as image:
        value = base64.b64encode(encode_jpeg(image)).decode()
    frames.append({"timestamp_ms": i * 500, "image_base64": value})
result = client.plan(session["id"], frames)
print(result["instruction"], result["timings"])
# After the human has acted, collect a NEW observation, then:
client.feedback(session["id"], event="done")
client.close()
```

## Endpoints

| Method | Path | Meaning |
| --- | --- | --- |
| GET | `/api/health` | Process liveness, including model readiness boolean |
| GET | `/api/ready` | Model state; 503 until ready |
| GET | `/api/status` | Model, ASR, latest evaluation and recent sessions |
| POST | `/v1/plan` | `{goal, history, frames, live?}` → next instruction. Stateless: the caller owns the task |
| POST | `/v1/asr` | Same as `/api/transcribe` |
| POST | `/v1/live/state` | guidance_node's state JSON, on change and every 2 s |
| PUT | `/v1/live/preview` | guidance_node's newest preview JPEG |
| GET | `/v1/live/commands?wait=` | guidance_node long-polls for console commands (up to 25 s) |
| GET | `/api/live/state?after=&wait=` | The console long-polls the state; adds `version` and `node_online` |
| GET | `/api/live/preview.jpg` | Newest preview; 404 while the node is offline |
| POST | `/api/live/command` | `{kind: goal\|reply\|note\|talk, text}`; 503 while the node is offline |
| GET | `/api/system` | guidance_node, glasses and test camera: state, exit code, start time, log tail when failed |
| POST | `/api/system/{name}/{start\|stop}` | Starts or stops one of those three; nothing else can be run |
| GET | `/api/trials` | Trial summaries from `ARIA_SESSIONS_DIR` and `.runtime/guidance`, newest first |
| GET | `/api/trials/{session}/{trial}` | `trial.json`, events, review, frame count and the first frame's time |
| GET | `/api/trials/{session}/{trial}/video.mp4` | Frames as H.264, timed by capture stamps; cached once the trial closes |
| GET | `/api/trials/{session}/{trial}/captions.vtt` | WebVTT of goal, spoken instructions, replies, transcripts and notes, timed like the video |
| PUT | `/api/trials/{session}/{trial}/review` | `{verdict: success\|fail\|null, notes}` (notes ≤ 10,000 characters) |
| POST | `/api/sessions` | `{goal, source, task_id?}` → new session |
| GET | `/api/sessions/{id}` | Goal, state, history, plans and client events |
| POST | `/api/sessions/{id}/plan` | `{request_id, frames}` → next instruction |
| POST | `/api/sessions/{id}/feedback` | `{event, text?}`; event is `done`, `next`, `feedback`, `cancel` or `confirm` |
| POST | `/api/sessions/{id}/events` | Bridge timestamps, TTS publish/suppression and client timing |
| POST | `/api/transcribe` | Multipart `file`, 16-bit PCM WAV, 0.2–30 seconds, ≤6 MiB |
| GET | `/api/runs/{request_id}/frames/{index}` | Saved frame index 0–19 |
| GET | `/api/reports/latest` | Published evaluation JSON |

Write requests require `Authorization: Bearer TOKEN` only when `ARIA_API_TOKEN` is configured. The client and ROS bridge read that environment variable. Proxy environment variables are ignored by the internal client; the configured backend URL is contacted directly.

## Observation contract

- Exactly twenty chronological, distinct capture timestamps. At 2 Hz, they span 9.5 seconds of the last ten seconds.
- Accepted timestamp span is 9–10 seconds, with no individual gap greater than 1.2 seconds.
- Each frame contains `timestamp_ms` and base64 JPEG/PNG data. JPEG/PNG data URLs are also accepted.
- No HTTP image URLs or server-side file paths are accepted.
- Each encoded image is at most 2 MiB; decoded bytes are limited to 1.5 MiB and 16 megapixels. The complete HTTP request is limited to 24 MiB.
- For `source="aria"` or `"webcam"`, timestamps are Unix epoch milliseconds; the newest capture must be no more than `ARIA_MAX_FRAME_AGE_MS` old (default 5000) or three seconds into the future when received. The 422 response states the measured age. `ARIA_MAX_FRAME_AGE_MS=0` disables the check.
- For `source="replay"`, timestamps may be relative to the recording. Replay mode is identified in the session and must not be counted as live hardware evidence.

The ROS bridge samples a bounded buffer. It requires a sufficiently complete window and rejects stale, duplicated or badly gapped captures. Images are JPEG encoded at a maximum edge of 640 pixels before transmission; Qwen's processor applies the configured pixel budget. With `ARIA_INPUT_MODE=video` (the default), the backend passes the 20 frames to the model as one 2 fps video. The `<t seconds>` timestamps the processor inserts come from that nominal rate, not from the client's capture times; the gap limits above keep the actual spacing close to it.

## Stateless planning

`POST /v1/plan` is what `guidance_node` uses. The body carries the goal, the dialogue `history` (at most 24 turns of `{role: user|assistant, text}`) and the 20 frames. `live` (default true) applies the frame age check; set it false for recorded frames. The response has `status`, `instruction`, `raw_output`, token counts, `timings`, `received_at_ms` and `responded_at_ms`. Nothing is stored. Busy is 429, a loading model 503, bad or stale frames 422.

## Session lifecycle

The `/api/sessions` routes keep state on the server and serve the workbench.


`ready → planning → awaiting_feedback → ready`, or `planning → awaiting_confirmation → completed`.

The model only proposes completion. A `DONE` answer moves the session to `awaiting_confirmation`, and the `confirm` feedback event closes it as `completed`. Any other feedback event in that state (for example `next`, or `feedback` saying something is still missing) returns the session to `ready` for another plan, with the exchange kept in the dialogue history. `confirm` in any other state is rejected with 409.

`cancel` moves the session to `cancelled` and invalidates a generation in flight. A new goal creates a new session. Restarting the service marks interrupted plans as failed and returns affected sessions to `ready`.

Only one model inference runs at a time. Another inference is rejected with 429, instead of accumulating an instruction queue. The model thread does not block health or cancellation requests.

Each plan uses a UUID `request_id`. Retrying the same ID with identical input returns the cached result (`cached=true`) and does not generate again. Reusing an ID with different input is rejected. Cancelled or discarded requests cannot be replayed as valid instructions. Failed requests need a fresh ID after the cause is fixed. Clients must deduplicate TTS using `request_id`.

Output includes:

- `instruction`: text intended for the person.
- `status`: `instruction`, `clarification` or `done_proposed`. Only the model's `DONE` mode proposes completion; sentences mentioning completion do not. A `done_proposed` result speaks the model's SAY line (or "The task looks complete." without one) and needs a `confirm` feedback event before the session is `completed`.
- `proposal`: `{mode, check, evidence, unknown, expect, plan}`, never spoken. `mode` is `ACT`, `INSPECT`, `ASK`, `REPLAN` or `DONE`. `check` says whether the last answer's `expect` holds now (`yes`, `no`, `unknown` or `none`); `evidence` is what the model saw that decided the step; `unknown` is a fact the step depends on that the scene does not show, or `none`; `expect` is what should be true after the step; `plan` is the steps left after this one, separated by `;`. Fields the model did not write are empty, as in the old one-line format.
- `raw_output`: the non-thinking model answer, retained for evaluation.
- `input_tokens`, `output_tokens`, observation stamps and timing fields.
- `preview`: a relative URL for the newest saved frame, or null when frame saving is disabled.

## Latency definitions

| Field | Measurement |
| --- | --- |
| `decode_ms` | HTTP handler start through image validation/decoding |
| `preprocess_ms` | Prompt construction, image processor and input transfer |
| `time_to_first_token_ms` | Generate call start until the first generated token reaches the streamer; includes prefill |
| `generation_ms` | Generate call through synchronized completion |
| `model_ms` | Preprocessing plus generation |
| `server_total_ms` | Handler work through inference and optional image saving, measured before the final SQLite commit/HTTP serialization |
| `client_roundtrip_ms` | Caller-observed request-to-response duration, recorded by the replay evaluator/bridge |
| `received_at_ms`, `responded_at_ms` | Backend wall clock stamps |
| `tts_published` event | ROS host timestamp when text is published to the TTS topic |

The ten-second observation window, model load time, STT latency, and speaker playback duration are separate quantities. TTS publish time is **not** the first audible sample or playback completion. Aria provides no playback acknowledgement through the current four-topic interface. Full spoken-goal-to-audible-instruction latency requires a hardware recording and synchronized clocks. Do not label a replay's HTTP timing as wearable end-to-end latency.

## Storage

`.runtime/guidance.sqlite3` contains sessions, plans and client events. Saved input frames live in `.runtime/runs/REQUEST_ID/`. Evaluation summaries are stored in `.runtime/reports/`. Set `ARIA_DATA_DIR` to relocate this state. Back up the state directory when preserving experiments; no automatic deletion of observations is performed. Set `ARIA_SAVE_FRAMES=0` if images should not be retained.
