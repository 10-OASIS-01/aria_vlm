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
| `/guidance/talk` | `std_srvs/srv/SetBool` | Push-to-talk: `true` starts recording, `false` transcribes; no "Aria" needed |
| `/guidance/state` | `std_msgs/msg/String` | JSON `{state, detail, task, goal, instruction}`, transient-local |
| `/guidance/transcript` | `std_msgs/msg/String` | What the recognizer heard, for push-to-talk and addressed open-mic speech |

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

With `open_mic: true`, the default in [guidance.yaml](../ros2_ws/src/aria_guidance_ros/config/guidance.yaml), the glasses' microphone is always on, including while they speak, and "Aria" marks speech meant for the system. A pause of about 1.2 s ends an utterance (`open_mic_silence_seconds`). Every utterance is transcribed. Speech without "Aria" is dropped, unless it is a bare step word or a reply window is open.

| Say | When | Effect | Model call |
| --- | --- | --- | --- |
| "Aria, trial start, pick up the marker" | Any time | Opens a trial with that goal. A running trial closes as `replaced` | No |
| "Aria, trial start" | Any time | Asks "What is the goal?", and the next words are the goal | No |
| "Aria, stop" or "Aria, cancel" | Task active | Closes the trial as cancelled, at once | No |
| "Aria, finish" or "Aria, trial end" | Task active | Closes the trial as completed, at once | No |
| "Aria, done" or "Aria, next" | Task active | Requests the next step | No |
| "Aria, repeat" | Task active | Speaks the last instruction again | No |
| "Aria, note: ..." | Task active | Saved as a note in the trial record | No |
| "Aria" alone, then a pause | Any time | The next words count as addressed | No |
| "Aria, I can't find it" (anything else) | Task active | Classified, as below | Yes |
| Bare `next`, `done` or `repeat`, with no "Aria" | Task active | Requests the next step, or speaks the last instruction. The whole utterance must be that word. `done` does not confirm completion here | No |
| "Well, Aria, next" ("Aria" as the second or third word) | Task active | Exact `next`, `done`, `repeat` and `note:` act; anything else is classified. Can't close, replace or start a trial | Only if classified |
| "Aria, what's next" (anything else) | No task | Says "No trial running. Say Aria, trial start." | No |

To close, replace or start a trial, the wake word must be the first word, or follow `okay`, `ok`, `hey`, `hi`, `um` or `uh`. Whisper's spellings `arya`, `area` and `maria` also count there, so "Okay Aria, what's next" is addressed and "in that area" is not. The transcribed words must otherwise be the whole command: case and trailing punctuation are ignored. As the second or third word, only `aria` and `arya` count. With no task, that speech only gets the "No trial running" line, and "I said Aria, stop" reaches the classifier and at most gets a hint.

**Classifier labels.** Text only, on the Qwen model already loaded, with no frames. It picks the action, and a new step still looks at the camera through `/v1/plan`.

| Label | Example | Effect |
| --- | --- | --- |
| `done`, `help`, `correction` | "okay got it", "how do I hold it", "no, the blue one" | Says "Okay", then plans again with the raw words as feedback |
| `repeat` | "say that again" | Speaks the last instruction again |
| `cancel`, `finish` | "forget it", "that's everything" | **Does not close the trial.** Says "Say Aria, stop to cancel." or "Say Aria, finish to finish." |
| `ignore` | "hold on" | Nothing |
| No label, or the call failed | | Says "Sorry, I didn't catch that." |

**What can close or replace a trial:** only the exact words above after a leading "Aria", plus exact `done` or `yes` after "The task looks complete", with a leading "Aria" or in its 10 s reply window. The model never can, and "stop" without "Aria" never does, even in a reply window.

**Reply windows** need no "Aria". The timer starts at the estimated end of the system's speech.

| After | Lasts | The next words |
| --- | --- | --- |
| "What is the goal?" | 15 s | Are the goal. "Stop" drops the question |
| A question from the model, or "The task looks complete" | 10 s | Answer it. Exact `done` or `yes` confirms completion |
| "Aria" alone | 8 s | Are addressed |

**Speech while the glasses talk.** The microphone stays on, so "Aria, stop" interrupts an instruction. In the first trial on the glasses, a 10 s instruction never reached the speech threshold and Whisper heard nothing in it, while the old mic-off window cut off replies. As a backstop, speech that begins before the estimated end of the line is dropped if two or more of its words appear in order in that line, with or without "Aria". So "Say Aria, stop to cancel." can't close the trial if the microphone hears it, and the card shows "Ignored: the glasses' own words". If the card shows their voice at your room and volume, set `open_mic_deaf_while_speaking: true` to turn the microphone off during that speech. That restores the old limits: "Aria, stop" is lost while they speak, and the start of a quick reply is cut off.

**Speech during planning.** A finished plan's instruction is held while an utterance is pending. If the utterance is an action, the instruction is dropped and a new plan runs. If it is not (no wake word, `ignore`), the instruction is spoken.

**What is saved.** The trial record keeps only addressed speech as `transcript` events, with the label in `intent`. Other speech's text goes to the console's microphone card and a debug-level log line, off by default (`--ros-args --log-level guidance_node:=debug`). The card holds the last 20 utterances in memory and readable from `/api/live/state`, and it is not written to disk. The raw audio is different, as below.

**Limits**

- **Bystanders' voices are saved.** Every trial's `audio.pcm` keeps all microphone audio from about 10 s before the goal, and the session VRS already keeps what the glasses stream. Tell people nearby.
- **Mistakes close trials.** There is no spoken confirmation, so a misheard or overheard "Aria, stop" ends the trial for good.
- **Anyone can answer.** Any voice saying "Aria" counts, and any voice can answer in a reply window, including setting the goal.
- **Forgetting the word** gets no response, because that speech is dropped silently, apart from the bare step words.
- **A bare `next` or `done` from anyone** replans during a trial, with "Okay" spoken. It can't end one, and the model checks the scene.
- **Whisper failures are logged, not spoken.** A failed transcription can't be told from noise. Only classifier failures say "Sorry".
- **Whisper is set to English** (`ARIA_ASR_LANGUAGE=en`) for all speech, including Hold to talk, so the Chinese command words in `task.py` won't be recognized. Set it empty to auto-detect, which misread short English clips in testing, is slower, and may spell "Aria" in ways the list misses.
- A spoken line can collide with a finishing plan, which suppresses the instruction. "Aria, repeat" recovers it.

### Hold to talk

Console Hold to talk and `/guidance/talk` stay as a manual fallback. They suspend the open mic while held, need no "Aria", and treat the whole recording as a reply:

| Say | When | Effect |
| --- | --- | --- |
| A goal in plain words, such as "Pick up the black marker on the table" | No active task | Becomes the goal |
| `done` or `next` | Task active | Requests the next step |
| `done` or `yes` | After "The task looks complete. Say done to confirm, or next to continue." | Confirms and closes the task |
| `next`, or what is still missing | After that same prompt | The task continues with a new step |
| `repeat` | Task active | Speaks the last instruction again |
| `finish` | Task active | Closes the trial as completed |
| `cancel` or `stop` | Any time | Ends the task |
| `goal: ...` | Task active | Replaces the goal |
| `note: ...` | Any time | Saved as a note in the trial record |
| Anything else, such as "I can't find it" | Task active | Sent as feedback, then a new step is planned |

- A command must be the whole recording. Case and trailing punctuation are ignored (`Next.` works), but "next step please" counts as feedback. It still triggers a new step, with the words attached.
- `/guidance/talk` refuses to start while planning, transcription or the estimated playback of the previous instruction is active. Retry once it finishes.
- `ros2 service call` takes a second or two to start. Begin speaking only after the start call returns, or the first words are lost.

The Live view's **What the microphone hears** card is the first place to look when a command has no effect. It lists every utterance the node cut from the microphone with its outcome, and its label says what the microphone is doing right now.

For both, wait about ten seconds after the camera starts before giving the first goal, so a complete observation window exists. `/guidance/transcript` shows what was recognized and acted on, and open-mic speech that was dropped does not appear there.

## Microphone level

On Aria Gen 2 glasses, `/aria/audio` carries 8 channels at 16 kHz, with 16-bit samples in the upper half of each int32. After the node mixes the channels down, room noise measured about 0.0002 RMS of full scale and normal speech about 0.0006. The backend treats a clip below 0.002 RMS as silence and returns an empty transcript without running speech recognition. At `audio_gain: 1.0` every recording therefore comes back empty, so the node defaults to `audio_gain: 16.0`.

The open mic counts a block as speech at `open_mic_threshold: 0.006` RMS, after gain. That sits between the room (about 0.0032 at x16) and normal speech (about 0.0096). Going over it only costs a Whisper call, because the recognizer's voice filter returns empty text for non-speech. `tools/mic_level.py` shows the live level. `open_mic_silence_seconds` and `open_mic_deaf_while_speaking` are in the same file.

Adjust `audio_gain` in [guidance.yaml](../ros2_ws/src/aria_guidance_ros/config/guidance.yaml), or in a copy passed with `config:=...`, if a different microphone or room needs it. The node reads the value only at startup, so restart it and confirm:

```bash
ros2 param get /guidance_node audio_gain   # Double value is: 16.0
```
