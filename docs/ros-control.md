# ROS control

Everything the console does is also available from a sourced ROS terminal, and the wearer can do most of it by voice.

## Topics and services

`aria_node` publishes three topics and subscribes to one. Those are the only Aria-side dependencies, and the names for vision, audio and TTS are configurable.

| Interface | Direction at `aria_node` | Type | Use |
| --- | --- | --- | --- |
| `/aria/rgb/image_raw` | Publishes | `sensor_msgs/msg/Image` | RGB frames, sensor-data QoS |
| `/aria/gaze` | Publishes | `geometry_msgs/msg/PointStamped` | Available, not used by the planner |
| `/aria/audio` | Publishes | `aria_msgs/msg/Audio` | Interleaved signed int32 samples |
| `/aria/tts` | Subscribes | `std_msgs/msg/String` | One utterance per instruction |

`guidance_node` adds its own interface, all standard message types:

| Interface | Type | Use |
| --- | --- | --- |
| `/guidance/goal` | `std_msgs/msg/String` | Sets a new goal |
| `/guidance/reply` | `std_msgs/msg/String` | `done`, `next`, `repeat`, `cancel` or free-text feedback |
| `/guidance/note` | `std_msgs/msg/String` | Operator note for the trial record |
| `/guidance/talk` | `std_srvs/srv/SetBool` | Push-to-talk: `true` starts recording, `false` transcribes |
| `/guidance/state` | `std_msgs/msg/String` | JSON `{state, detail, task, goal, instruction}`, transient-local |
| `/guidance/transcript` | `std_msgs/msg/String` | What the recognizer heard |

## Run a task from a terminal

Watch the node in one terminal:

```bash
ros2 topic echo /guidance/state
```

Give a goal in another:

```bash
ros2 topic pub --once /guidance/goal std_msgs/msg/String "{data: 'Put each colored cube into the tray of the same color.'}"
```

Keep the work area visible. Once a complete camera window is available, the node sends twenty distinct frames and publishes the returned instruction to `/aria/tts`. Perform the action and request the next step:

```bash
ros2 topic pub --once /guidance/reply std_msgs/msg/String "{data: 'done'}"
ros2 topic pub --once /guidance/reply std_msgs/msg/String "{data: 'repeat'}"
ros2 topic pub --once /guidance/reply std_msgs/msg/String "{data: 'cancel'}"
```

To give a goal or feedback by microphone, use push-to-talk. `tools/ptt.py` gives a hold-Space window, and the bare service calls are:

```bash
ros2 service call /guidance/talk std_srvs/srv/SetBool "{data: true}"
# Speak a goal, 'done', 'next', 'repeat', 'cancel', or a correction.
ros2 service call /guidance/talk std_srvs/srv/SetBool "{data: false}"
ros2 topic echo /guidance/transcript
```

The node buffers the `/aria/audio` blocks, downmixes channels, converts signed int32 PCM to WAV, and calls the backend's speech recognition. Recording is limited to 20 seconds. It cannot begin while planning or estimated playback is active, and planning pauses while recording or transcription is active.

The glasses do not report when playback ends, and they offer no TTS cancel acknowledgement. The node sends one instruction per request and uses an estimated speaking time to guard repeats and recording. Cancelling suppresses unsent planner output, but it cannot retract speech already handed to the glasses.

## Spoken commands

Start recording, speak normally, then stop recording. No wake word or special phrasing is needed. What happens to the transcript depends on whether a task is active and on the exact words:

| Say | When | Effect |
| --- | --- | --- |
| A goal in plain words, such as "Pick up the black marker on the table" | No active task | Becomes the goal |
| `done` or `next` | Task active | Requests the next step |
| `help`, or what is wrong | Task active | Sent as feedback; the model rephrases or breaks the step down |
| `done` or `yes` | After "The task looks complete. Say done to confirm, or next to continue." | Confirms and closes the task |
| `next`, or what is still missing | After that same prompt | The task continues with a new step |
| `repeat` | Task active | Speaks the last instruction again |
| `finish` | Task active | Closes the trial as completed |
| `cancel` or `stop` | Any time | Ends the task |
| `goal: ...` | Task active | Replaces the goal |
| `note: ...` | Any time | Saved as a note in the trial record |
| Anything else, such as "I can't find it" | Task active | Sent as feedback, then a new step is planned |

- A command must be the whole recording. Case and trailing punctuation are ignored (`Next.` works), but "next step please" counts as feedback. It still triggers a new step, with the words attached.
- To change the goal by voice, say `cancel`, then the new goal as a separate recording. `goal: ...` only matches when the recognizer writes the colon, and it may produce "Goal, ..." instead.
- Wait about ten seconds after the camera starts before giving the first goal, so a complete observation window exists.
- `/guidance/talk` refuses to start while planning, transcription or the estimated playback of the previous instruction is active. Retry once it finishes.
- `ros2 service call` takes a second or two to start. Begin speaking only after the start call returns, or the first words are lost.
- `/guidance/transcript` shows exactly what was recognized. Check it first when a command has no effect.

## Microphone level

On Aria Gen 2 glasses, `/aria/audio` carries 8 channels at 16 kHz, with 16-bit samples in the upper half of each int32. After the node mixes the channels down, room noise measured about 0.0002 RMS of full scale and normal speech about 0.0006. The backend treats a clip below 0.002 RMS as silence and returns an empty transcript without running speech recognition. At `audio_gain: 1.0` every recording therefore comes back empty, so the node defaults to `audio_gain: 16.0`.

Adjust `audio_gain` in [guidance.yaml](../ros2_ws/src/aria_guidance_ros/config/guidance.yaml), or in a copy passed with `config:=...`, if a different microphone or room needs it. The node reads the value only at startup, so restart it and confirm:

```bash
ros2 param get /guidance_node audio_gain   # Double value is: 16.0
```
