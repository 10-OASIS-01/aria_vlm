# Aria Visual Guidance

A **Qwen3.5-9B** backend that looks at the last 10 seconds of camera observations and tells a person what to do next. It connects to [sparklabutah/aria2_ros2](https://github.com/sparklabutah/aria2_ros2) through a separate ROS 2 bridge. The service also hosts experiment records and a video-clip workbench.

**Model:** `Qwen/Qwen3.5-9B`. Use the post-trained multimodal checkpoint, not a text-only or Base checkpoint. A predownloaded local model directory is supported; no weights are included in this repository.

## System and interface boundary

```mermaid
flowchart LR
    G[Aria Gen2] -->|vision, audio| B[guidance_node on ROS host]
    B -->|20 frames + goal + dialogue| Q[Qwen3.5-9B on GPU workstation]
    Q -->|one next instruction| B
    B -->|text to /aria/tts| G
    H[Human] -->|spoken goal / done / next| G
    G -->|onboard speech| H
    W[Experiment workbench] --- Q
```

The existing Aria node **publishes three topics and subscribes to one**. Those are the only Aria-side dependencies:

| Existing Aria interface | Direction at the Aria node | ROS type | MVP use |
| --- | --- | --- | --- |
| `/aria/rgb/image_raw` | Publishes vision | `sensor_msgs/msg/Image` | RGB frames; sensor-data QoS |
| `/aria/gaze` | Publishes gaze | `geometry_msgs/msg/PointStamped` | Available; not used by the MVP planner |
| `/aria/audio` | Publishes microphone audio | `aria_msgs/msg/Audio` | Interleaved signed int32 samples |
| `/aria/tts` | Subscribes to text | `std_msgs/msg/String` | One utterance per instruction |

`guidance_node` requires no changes to the Aria node. It owns the task (goal, dialogue history, step state) and calls the backend's stateless `POST /v1/plan`, so a backend restart loses nothing. Its own interface, all standard message types:

| Interface | Type | Use |
| --- | --- | --- |
| `/guidance/goal` | `std_msgs/msg/String` | Sets a new goal |
| `/guidance/reply` | `std_msgs/msg/String` | `done`, `next`, `repeat`, `cancel` or free-text feedback |
| `/guidance/note` | `std_msgs/msg/String` | Operator note for the trial record |
| `/guidance/talk` | `std_srvs/srv/SetBool` | Push-to-talk: `true` starts recording, `false` transcribes |
| `/guidance/state` | `std_msgs/msg/String` | JSON `{state, detail, task, goal, instruction}`; transient-local |
| `/guidance/transcript` | `std_msgs/msg/String` | What the recognizer heard |
| `/guidance/task`, `/guidance/instruction` | `std_msgs/msg/String` | Task state and current instruction as plain text, for Foxglove; transient-local |
| `/guidance/preview/compressed` | `sensor_msgs/msg/CompressedImage` | The 640 px JPEG sent to the model, about 5 Hz |

Topic names for vision, audio and TTS are configurable.

The protocol is user-paced: set a goal → collect observations → receive one instruction → act → say or send `done` / `next` → observe again. Cancelling or replacing a goal invalidates pending model output.

## 1. Deploy the backend locally

Clone this repository with its pinned infrastructure reference:

```bash
git clone --recurse-submodules https://github.com/10-OASIS-01/aria_vlm.git
cd aria_vlm
# If already cloned without submodules:
git submodule update --init --recursive
```

`external/aria2_ros2` is a Git submodule pinned to
[`1af73bd`](https://github.com/sparklabutah/aria2_ros2/commit/1af73bdd179f5c31ce7dbbfa6d78bfc0727a99a6).
GitHub displays the directory as `aria2_ros2 @ 1af73bd`. It is a reference to upstream
infrastructure; no changes to that upstream code are required. The repository
contains the backend, bridge, experiment workbench and deployment documentation.
Local task-board data, verification tools, recordings and model weights are excluded.

Use Linux, Python 3.10 or newer, a CUDA-enabled PyTorch installation, and an NVIDIA GPU. A 32 GB GPU is recommended for BF16 weights and twenty images. The backend and the Aria ROS environment are separate environments; they may run on the same machine or different machines.

From this repository's root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
# Install PyTorch/torchvision wheels appropriate for your NVIDIA driver first.
# Example for a CUDA 13.0-capable driver:
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
python -m pip install -e '.[model,speech]'
cp .env.example .env
```

Edit `.env` for your machine:

```dotenv
ARIA_MODEL_PATH=./models/Qwen3.5-9B
ARIA_ASR_MODEL_PATH=./models/faster-whisper-small
HF_HOME=./models/hf-cache
CUDA_VISIBLE_DEVICES=0
ARIA_DEVICE=cuda:0
ARIA_HOST=127.0.0.1
ARIA_PORT=8785
ARIA_DATA_DIR=./.runtime
ARIA_ROOT_PATH=
```

`ARIA_MODEL_PATH` can point directly to an existing directory containing `config.json`, tokenizer/processor files and all four safetensors shards. Relative paths resolve from the project root when using `scripts/service.sh`. GPU numbering inside PyTorch is relative to `CUDA_VISIBLE_DEVICES`.

If weights are not already available, download them once:

```bash
export HF_HOME=./models/hf-cache
python scripts/download_model.py --repo Qwen/Qwen3.5-9B --output ./models/Qwen3.5-9B
python scripts/download_model.py --repo Systran/faster-whisper-small --output ./models/faster-whisper-small
```

Use your organization's permitted network route or Hugging Face mirror, if needed. The downloader respects `HF_ENDPOINT`; it does not start a VPN or install global proxy settings. Existing local checkpoints are loaded without contacting the model hub. ASR uses faster-whisper small on CPU in int8; leave `ARIA_ASR_MODEL_PATH` empty if you only need typed goals.

Start in the foreground:

```bash
bash scripts/service.sh run
```

Or keep it running after disconnecting, with `tmux` installed:

```bash
bash scripts/service.sh start
bash scripts/service.sh status
# Stop only this project's tmux session:
bash scripts/service.sh stop
```

Open **http://127.0.0.1:8785/**. `/api/health` checks the process; `/api/ready` returns HTTP 200 only after the model has loaded. Startup runs in the background so the workbench remains available while weights load. Use **one server worker**: every worker would load its own model.

The model uses BF16, SDPA, `enable_thinking=False`, greedy decoding and at most 96 output tokens. Greedy decoding deliberately departs from the model card's sampling recommendation, to keep the guidance loop deterministic. See [validation notes](docs/validation.md) for the measured environment and limits, and the [changelog](docs/CHANGELOG.md) for changes between versions.

The 20 frames of each observation reach the model as one 2 fps video (`ARIA_INPUT_MODE=video`). Qwen's processor adds `<t seconds>` timestamps and packs two frames per temporal patch, so a request uses half the visual tokens of 20 separate images (`ARIA_INPUT_MODE=images`). The default budget, `ARIA_MAX_PIXELS=307200`, keeps the bridge's full 640×480 frames; at the earlier 131,072 pixels the model missed small objects such as a pen on a mat. On an RTX 5090 this configuration measured about 0.8 s of model time per request on replayed observations, against about 1.3 s for 640×480 as separate images.

To compare prompts, input modes or pixel budgets, replay saved observations through the local model. No server or ROS is involved:

```bash
python scripts/replay_eval.py --cases .runtime/eval_cases.json \
    --modes images video --pixels 131072 307200 --prompt-file old=path/to/prompt.txt
```

Each case names a saved plan whose frames are replayed (`.runtime/runs/<plan id>/`), the goal and dialogue, the expected status and words that must appear. See the docstring of [replay_eval.py](scripts/replay_eval.py) for the format. Stop the backend first, or make sure the GPU has room for a second copy of the model.

## 2. Reach a backend on another workstation

An SSH tunnel keeps the backend listening on loopback. Run this on the machine that will run the ROS bridge:

```bash
ssh -N -L 8785:127.0.0.1:8785 USER@GPU_HOST
```

The bridge can then use `http://127.0.0.1:8785`. Verify it from that machine:

```bash
curl --noproxy '*' http://127.0.0.1:8785/api/ready
```

For an existing reverse proxy, see [deploy/nginx-location.conf](deploy/nginx-location.conf). It maps `/aria/` to `127.0.0.1:8785/` and strips the prefix. Set `ARIA_ROOT_PATH=/aria` in the backend environment. The frontend uses relative asset and API URLs, including images and downloads. Register the route in both the active proxy configuration and any configuration generator you maintain, then validate and reload Nginx. A service using HTTP on port 443 still needs an `http://` URL.

For authenticated write endpoints, set the same nonempty `ARIA_API_TOKEN` in the backend and bridge environments. The web workbench will offer a token field. The default is intended for a trusted lab network or SSH tunnel.

## 3. Install the bridge next to aria2_ros2

The Aria host should use **Ubuntu 22.04, ROS 2 Humble and its Python 3.10 environment**, as required by the current upstream SDK. The GPU backend can use a newer Python on another machine.

The source interface was inspected at upstream commit `1af73bdd179f5c31ce7dbbfa6d78bfc0727a99a6`. Follow the upstream README for installing ROS, `uv`, device certificates and the glasses' USB/Wi-Fi connection. Confirm that its camera/audio streams and TTS work before testing this bridge.

Starting from this guidance repository:

```bash
export GUIDANCE_REPO="$PWD"
cd "$GUIDANCE_REPO/external/aria2_ros2"
./setup_env.sh
source activate.sh

# Install only the lightweight guidance package into the Aria venv.
# Preserve the upstream numpy, setuptools, pytest and SDK pins.
uv pip install --python "${ARIA_VENV:-$PWD/.venv}/bin/python" -e "$GUIDANCE_REPO"

ln -s "$GUIDANCE_REPO/ros2_ws/src/aria_guidance_ros" src/aria_guidance_ros
python -m colcon build --symlink-install --packages-select aria_guidance_ros
source activate.sh
```

If you already have a working `aria2_ros2` checkout, use it instead of the submodule and do not rerun its setup. When using a source ZIP without Git metadata, clone upstream separately and check out commit `1af73bdd179f5c31ce7dbbfa6d78bfc0727a99a6`. Build the bridge after sourcing that checkout's `activate.sh`. Install the backend's model dependencies only in the GPU environment. The bridge imports no torch or transformers modules.

`activate.sh` currently sets `ROS_DOMAIN_ID=42`; all Aria and bridge terminals must join the same domain. Start the Aria node using your lab's known working launch configuration. In another terminal:

```bash
cd "$GUIDANCE_REPO/external/aria2_ros2"
source activate.sh
ros2 launch aria_guidance_ros guidance.launch.py backend_url:=http://127.0.0.1:8785
```

For a backend behind a prefix, pass the complete base URL, for example `backend_url:=http://GPU_HOST:443/aria`. A custom YAML file can be supplied with `config:=...`; see [guidance.yaml](ros2_ws/src/aria_guidance_ros/config/guidance.yaml). Use `ros2 topic list -t` to confirm the actual camera/audio/TTS names and change those three parameters if your lab's configuration differs.

## 4. Run a task through the glasses

### Operator console

`guidance.launch.py` also starts `foxglove_bridge` on port 8765. It forwards only `/guidance/*`, `/aria/tts` and `/rosout`, and accepts publishing only on `/guidance/*`. In Foxglove, open a connection to `ws://localhost:8765` (or the ROS host's address) and import [operator_layout.json](ros2_ws/src/aria_guidance_ros/foxglove/operator_layout.json). It shows:

- the task state as a full-width colour band (`/guidance/task`);
- the 640 px preview the model sees (`/guidance/preview/compressed`, about 5 Hz);
- the current instruction in large text (`/guidance/instruction`);
- Done and Next, Talk on and Talk off, then Repeat and Cancel;
- Goal and Note editors, and the node's log (states, transcripts, notes).

Foxglove has no hold-to-talk button. For hands-free speech, hold Space in `tools/ptt.py`. Rerun (`viewer.enable` in aria_node) stays the device-debug viewer; keep it off during trials.

### From a terminal

In a sourced ROS terminal, watch the node:

```bash
ros2 topic echo /guidance/state
```

In a second terminal, give a goal:

```bash
ros2 topic pub --once /guidance/goal std_msgs/msg/String "{data: 'Put each colored cube into the tray of the same color.'}"
```

Keep the work area visible. Once a complete camera window is available, the node sends twenty distinct frames and publishes the returned instruction to `/aria/tts`. Perform the action and request the next step:

```bash
ros2 topic pub --once /guidance/reply std_msgs/msg/String "{data: 'done'}"
# Other controls:
ros2 topic pub --once /guidance/reply std_msgs/msg/String "{data: 'repeat'}"
ros2 topic pub --once /guidance/reply std_msgs/msg/String "{data: 'cancel'}"
```

To supply a goal or feedback by microphone, use push-to-talk. `tools/ptt.py` gives a hold-Space window; the bare service calls are:

```bash
ros2 service call /guidance/talk std_srvs/srv/SetBool "{data: true}"
# Speak a goal, 'done', 'next', 'repeat', 'cancel', or a correction.
ros2 service call /guidance/talk std_srvs/srv/SetBool "{data: false}"
ros2 topic echo /guidance/transcript
```

The node buffers the existing `/aria/audio` blocks, downmixes channels, converts signed int32 PCM to WAV, and calls local ASR. With no active task, recognized speech becomes the goal. During a task, `done`/`next` requests a new step; other speech becomes feedback. Say `goal: ...` or publish a new `/guidance/goal` to replace a task explicitly. Recording is limited to 20 seconds. It is push-to-talk by design. Recording cannot begin while planning or estimated playback is active, and planning pauses while recording or transcription is active.

Upstream does not report playback completion or offer a TTS cancel acknowledgement. The node sends one instruction per user request and uses an estimated speaking interval to guard repeats and recording. Cancelling suppresses **unsent** planner output; it cannot retract speech already handed to the glasses.

### Trial records

Every goal is a trial, written to disk with nothing to save by hand. It goes into the newest aria_node session folder whose `clock.csv` is still being written (`session_root`, default `data/aria`, the same relative path aria_node uses), else into `.runtime/guidance/<start time>/`:

```
<session>/trials/03_142211/
  trial.json      goal, start/end (host ms and ns), outcome, steps, latency summary
  guidance.jsonl  goal, plan_request, plan_result, tts, reply, transcript, note, state, error
  frames/<ms>.jpg preview JPEGs at about 5 Hz, starting with the 10 s before the goal
```

The outcome is `completed`, `cancelled`, `replaced` (a new goal), `aborted` (node shutdown), or null while the trial is open. Notes come from `/guidance/note` or a spoken `note: ...`.

### Speaking to the node

Start recording, speak normally, then stop recording. No wake word or special phrasing is needed. What happens to the transcript depends on whether a task is active and on the exact words:

| Say | When | Effect |
| --- | --- | --- |
| A goal in plain words, e.g. "Pick up the black marker on the table" | No active task | Becomes the goal |
| `next` or `done` | Task active | Requests the next step |
| `done` or `yes` | After "The task looks complete. Say done to confirm, or next to continue." | Confirms and closes the task |
| `next`, or what is still missing | After that same prompt | The task continues with a new step |
| `repeat` | Task active | Speaks the last instruction again |
| `cancel` or `stop` | Any time | Ends the task |
| `goal: ...` | Task active | Replaces the goal |
| `note: ...` | Any time | Saved as a note in the trial record |
| Anything else, e.g. "I can't find it" | Task active | Sent as feedback, then a new step is planned |

- A command must be the whole recording. Case and trailing punctuation are ignored (`Next.` works), but "next step please" counts as feedback. It still triggers a new step, with the words attached.
- To change the goal by voice, say `cancel`, then the new goal as a separate recording. `goal: ...` only matches when the recognizer writes the colon; it may produce "Goal, ..." instead.
- Wait about ten seconds after the camera starts before giving the first goal, so a complete observation window exists.
- `/guidance/talk` refuses to start while planning, transcription or the estimated playback of the previous instruction is active. Retry once it finishes.
- `ros2 service call` takes a second or two to start. Begin speaking only after the start call returns, or the first words are lost.
- `/guidance/transcript` shows exactly what was recognized. Check it first when a command has no effect.

#### Microphone level

On the Aria Gen 2 glasses tested here, `/aria/audio` carries 8 channels at 16 kHz, with 16-bit samples in the upper half of each int32. After the node mixes the channels down, room noise measured about 0.0002 RMS of full scale and normal speech about 0.0006. The backend treats a clip below 0.002 RMS as silence and returns an empty transcript without running ASR. At `audio_gain: 1.0` every recording therefore comes back empty, so the node defaults to `audio_gain: 16.0`.

Adjust `audio_gain` in [guidance.yaml](ros2_ws/src/aria_guidance_ros/config/guidance.yaml), or in a copy passed with `config:=...`, if a different microphone or room needs it. The node reads the value only at startup, so restart it and confirm:

```bash
ros2 param get /guidance_node audio_gain   # Double value is: 16.0
```

## Tests

Unit tests need no ROS. Run them in the backend venv:

```bash
pip install -e '.[dev]'
python -m pytest
```

Integration tests start the real backend with a scripted model, a synthetic 5 Hz camera and `guidance_node`, on ROS domain 77. Run them in the Aria venv after building the package:

```bash
source "$GUIDANCE_REPO/external/aria2_ros2/activate.sh"
python -m pytest "$GUIDANCE_REPO/tests/integration"
```

`tools/` holds the debug helpers: `fake_camera.py` (5 Hz stand-in for the glasses), `ptt.py` (hold-Space push-to-talk window) and `mic_level.py` (microphone level meter).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| `/api/health` works but `/api/ready` is 503 | Read `.runtime/logs/service.log`; model load may still be running. Check CUDA, the model directory and dependency versions. There is no mock fallback. |
| `ModuleNotFoundError: Qwen3_5...` | Use this project's pinned `transformers==5.3.0` in the backend environment. |
| CUDA out of memory | Select a GPU with more free memory, use one server process, and keep the default frame pixel budget. Stop only processes you own. |
| Bridge stays on `collecting` | Verify topic names, sensor-data QoS, a continuous camera stream and about ten seconds of observations. Dropped frames are not padded by duplicating images. |
| Live timestamps rejected (422, "Newest frame is N s old") | Synchronize the ROS host and GPU workstation clocks. Use Aria's host-time capture stamps; replay requests use relative timestamps. Ages of several seconds on one machine mean the camera stream is backlogged (for example with the Aria viewer running). `ARIA_MAX_FRAME_AGE_MS` sets the limit (default 5000); `0` disables the check. |
| No speech recognized (empty transcript, `silence` status) | The clip was below the backend's 0.002 RMS silence cutoff. Aria Gen 2 speech needs about `audio_gain: 16.0`; see [Microphone level](#microphone-level). Also check the ASR model path and that `/aria/audio` is arriving. |
| No instruction heard | Verify a manual `/aria/tts` message first; inspect `/guidance/state` for stale results, speech guard or inference errors. |
| HTTP 409 | A previous instruction needs feedback, an ID is being reused with changed input, or the task was cancelled. |
| HTTP 429 | Another inference/ASR call is active. Wait and request a fresh observation. |
| Assets fail behind `/aria/` | Ensure the proxy strips `/aria/`, preserves the trailing-slash redirect, and the backend has `ARIA_ROOT_PATH=/aria`. |

All source paths and example configuration are portable. Keep machine-specific paths, tokens, tunnels and deployment records in `.env` or another excluded local configuration. `scripts/build_release.py` packages source and documentation using an explicit allowlist; it excludes model weights, recorded sessions and local machine configuration.
