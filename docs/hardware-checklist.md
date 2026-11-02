# Aria hardware integration checklist

This checklist is for Hitesh and Matthew's lab test. The notebook's rendered trials do not complete these checks.

## Before starting

- Start the existing `aria2_ros2` node using Minh's tested device configuration.
- Confirm the three published topics (vision, gaze, audio) and the single subscribed topic (TTS) with `ros2 topic list -t` and `ros2 topic info`.
- Verify RGB frequency and a manual text publication to `/aria/tts`.
- Join the same ROS domain and synchronize the receiver/workstation host clocks.
- Confirm `/api/ready` from the ROS host and check available GPU memory.
- Keep the task area in view and verify that left/right and RGB colors look correct. The MVP uses the incoming RGB image directly; add calibrated rectification upstream if the lab feed is materially distorted.

## Task A: desk reset

Use a red mug, a round coaster, a blue book and an open tray. Place the mug and book on a white work mat. Goal: “Clear the white work mat by placing the red mug on the brown round coaster and the blue book inside the open tray.”

1. Speak the goal ("Aria, trial start, ...", or push-to-talk); save the recognized transcript.
2. Wait for a complete observation window and the first audible instruction.
3. Perform exactly that action, then say `done`.
4. Repeat until Qwen declares completion, or stop and record the reason for failure.
5. Verify the final physical state yourself; a model's completion claim is not the ground truth.

## Task B: color sorting

Place three colored cubes and three matching trays on a table. Goal: “Put each colored cube into the tray of the same color. Leave the trays in place.” Repeat the same loop. Change cube positions between runs so a fixed instruction sequence cannot pass by coincidence.

## Controls and failure handling

- Replace a goal while a request is running; verify the previous result is never newly published to TTS.
- Send `next` twice quickly; verify no inference queue or repeated utterance develops.
- Temporarily stop the camera stream; the bridge should report that observations are incomplete/stale.
- Attempt push-to-talk while planning and attempt planning while recording; both overlaps must be rejected or deferred.
- Check that microphone silence produces no new goal and that the TTS utterance is not treated as user speech.
- Test one natural-language correction, such as “I cannot find that object.”
- Retain the session ID and observed error for any blocked task.

## Evidence to retain

Record the task, participant/run identifier, device/ROS configuration, goal transcript, session ID, per-step instructions, user feedback, physical completion judgement, interventions and failure reasons. Preserve the Aria recording using the existing lab recording workflow and the backend session export.

Measure separately: observation collection, STT, model inference, HTTP round trip and time to audible TTS. Use a synchronized recording for the complete spoken-goal-to-audible-instruction metric. Backend timestamps alone cannot establish the speaker's playback latency.

After each trial, run `python scripts/trial_latency.py <trial folder>`. It prints every latency up to the TTS publish and saves them as `latency.json` in the trial folder. STT and speech times appear only for spoken goals and spoken replies. A goal typed in the console has none.

Time to audible TTS comes from a phone recording, not from the trial folder:

1. Film the whole trial with a phone close enough to hear both the speaker and the glasses. A single recording needs no clock sync.
2. In the recording, measure from the first word of the spoken goal to the first word of the first instruction. This is the spoken-goal-to-audible-instruction latency.
3. Subtract the report's `goal to TTS publish ... from speech start`. The rest is the time from publishing the TTS to hearing it. Repeat over several instructions, because one sample says little.

Do not read this from `audio.pcm`. In the first recorded trial (`20261030T202050Z_cec25f87/trials/01_142231`), the microphones did not pick up the 21-word instruction after it was published, and the only loud sound in that time was a person. A loudness search there reports that person as the speaker. Nothing confirms the instruction played in that trial, so if a later recording does show it, measure it against the phone before relying on it.

The October 26 integration deliverable is several examples of **human** multi-step completion and measured wearable latency. Count hardware integration as validated only after retaining that evidence.
