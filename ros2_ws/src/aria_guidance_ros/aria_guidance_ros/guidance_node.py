"""Guidance node: owns the task, calls the stateless backend. Network work stays off ROS callbacks.

The task (aria_guidance.task) is only touched from ROS callbacks, which run one at a time; pool
threads do HTTP and hand results back through `messages`, drained by `tick`. Two more threads link
the node to the web console through the backend: one pushes state and preview, one pulls commands.
"""

import json
import logging
import queue
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np
import rclpy
from aria_msgs.msg import Audio
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from std_srvs.srv import SetBool

from aria_guidance.audio import Endpointer, mono_to_wav, pcm32_to_mono
from aria_guidance.client import GuidanceClient
from aria_guidance.frames import FrameBuffer, WindowNotReady, encode_jpeg, ros_image_to_pil
from aria_guidance.intent import interpret, is_echo, route
from aria_guidance.task import Task, parse_reply
from aria_guidance.trial import FALLBACK_ROOT, TrialRecorder

LOGGER = logging.getLogger(__name__)

# The model only proposes completion; the person closes the task by confirming.
CONFIRM_HINT = " Say done to confirm, or next to continue."
# Late joiners (ptt.py, ros2 topic echo) get the current state at once.
# Frame IDs of known cameras: aria_node's RGB camera and tools/fake_camera.py.
CAMERA_SOURCES = {"aria_rgb_optical_frame": "glasses", "fake_camera": "fake"}
LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
ECHO_GUARD_SECONDS = 0.5  # The estimated end of speech is rough; keep the mic deaf a little longer.
SPEECH_END_MARGIN_SECONDS = 1.0  # Added to the estimate, so speaking_until errs late.
AUDIO_BUFFER_MS = 10_500  # About the frames a new trial's video opens with.
MAX_QUEUED_UTTERANCES = 3
HEARD_FEED_SIZE = 20  # Utterances the console's microphone card shows.
MIC_SILENT_SECONDS = 1.0  # No audio block for this long: the glasses are not streaming sound.
# Seconds speech stays answerable after the system invites it, from the end of its own speech.
GOAL_WINDOW_SECONDS = 15.0
REPLY_WINDOW_SECONDS = 10.0
ARIA_WINDOW_SECONDS = 8.0
# Do not talk over someone, but ongoing chatter must not starve a finished instruction.
HOLD_FOR_SPEECH_SECONDS = 8.0
SAY_ASK_GOAL = "What is the goal?"
SAY_OKAY = "Okay."
SAY_SORRY = "Sorry, I didn't catch that."


class GuidanceNode(Node):
    def __init__(self, **kwargs):
        super().__init__("guidance_node", **kwargs)
        defaults = {
            "backend_url": "http://127.0.0.1:8785",
            "image_topic": "/aria/rgb/image_raw",
            "audio_topic": "/aria/audio",
            "tts_topic": "/aria/tts",
            "max_result_age_seconds": 60.0,
            "request_timeout_seconds": 120.0,
            "image_interval_ms": 200.0,
            "max_audio_seconds": 20.0,
            "audio_gain": 16.0,
            # Always listen for "Aria"; console Hold to talk and /guidance/talk still work.
            "open_mic": False,
            "open_mic_threshold": 0.006,
            "open_mic_silence_seconds": 1.2,
            # In the first glasses trial their voice never reached the threshold, and this cut off
            # replies. Turn it on if the microphone starts picking the voice up.
            "open_mic_deaf_while_speaking": False,
            # aria_node's record.output_dir, relative to the same working directory.
            "session_root": "data/sessions",
            "fallback_root": str(FALLBACK_ROOT),
        }
        for name, default in defaults.items():
            self.declare_parameter(name, default)
        self.config = {name: self.get_parameter(name).value for name in defaults}
        self.client = GuidanceClient(
            self.config["backend_url"], timeout=self.config["request_timeout_seconds"]
        )
        self.frames = FrameBuffer()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aria-http")
        self.messages = queue.SimpleQueue()
        self.task = Task()
        self.trial = TrialRecorder(self.config["session_root"], self.config["fallback_root"])
        self.last_state, self.last_detail = None, ""
        self.planning = False  # A plan request is in flight, possibly for an older epoch.
        self.last_image_ms = -1.0
        self.speaking_until = 0.0
        self.audio_active = False
        self.audio_chunks = []
        self.audio_rate = 0
        self.audio_started = 0.0
        self.audio_busy = False
        self.last_wait_log = 0.0
        self.last_heard = None
        self.last_tts = None
        self.last_audio = 0.0
        self.last_language = None
        self.mic_state = None
        self.heard_feed = deque(maxlen=HEARD_FEED_SIZE)
        self.voice_turn = False  # This reply came from speech, so its transcript already says it.
        self.endpointer = Endpointer(
            self.config["open_mic_threshold"], silence_s=self.config["open_mic_silence_seconds"]
        )
        # (samples, rate, started, stamps)
        self.utterances = deque(maxlen=MAX_QUEUED_UTTERANCES)
        self.speech_started_ms, self.speech_last_ms = None, None  # Host stamps of the mic blocks.
        self.heard_timing = {}  # Of the one utterance in flight; audio_busy holds back the next.
        self.speech_started = 0.0
        self.window = None  # (kind, deadline): speech needing no "Aria"; see intent.interpret.
        self.held_plan, self.held_since = None, 0.0  # (epoch, result) waiting for speech to end.
        self.audio_buffer = deque()  # (stamp_ms, samples, rate) for the start of the next trial.
        self.camera_frame = None  # frame_id of the newest image, which names its camera.
        self.closing = threading.Event()
        self.console_wake = threading.Event()
        self.console_state, self.console_preview = None, None
        self.state_pub = self.create_publisher(String, "/guidance/state", LATCHED)
        self.transcript_pub = self.create_publisher(String, "/guidance/transcript", 10)
        self.tts_pub = self.create_publisher(String, self.config["tts_topic"], 10)
        self.create_subscription(
            Image, self.config["image_topic"], self.on_image, qos_profile_sensor_data
        )
        self.create_subscription(Audio, self.config["audio_topic"], self.on_audio, 100)
        self.create_subscription(String, "/guidance/goal", lambda m: self.start(m.data), 10)
        self.create_subscription(String, "/guidance/reply", lambda m: self.reply(m.data), 10)
        self.create_subscription(String, "/guidance/note", lambda m: self.note(m.data), 10)
        self.create_service(SetBool, "/guidance/talk", self.on_talk)
        self.create_timer(0.2, self.tick)
        self.status("idle", "Set a goal in the console, on /guidance/goal, or by talking")
        for target in (self.console_push, self.console_pull):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()

    def status(self, state, detail):
        last = self.task.last
        body = {
            "state": state,
            "detail": detail,
            "task": self.task.state,
            "goal": self.task.goal,
            "instruction": last["instruction"] if last else None,
            "recording": self.audio_active,
            "heard": self.last_heard,
            "trial": self.trial.last_ref,
        }
        self.state_pub.publish(String(data=json.dumps(body, ensure_ascii=False)))
        # The microphone card only goes to the console: not to /guidance/state, not to the trial.
        self.console_state = {**body, "mic": self.mic_state, "feed": list(self.heard_feed)}
        self.console_wake.set()
        self.get_logger().info(f"{state}: {detail}")
        self.last_detail = detail
        if state != self.last_state:
            self.last_state = state
            self.record("state", state=state, detail=detail)

    def console_update(self, **fields):
        """Replaces the console's state with these fields changed, so console_push sends it."""
        self.console_state = {**self.console_state, **fields}
        self.console_wake.set()

    def log_heard(self, text, outcome, label=None):
        """Adds one utterance to the console's feed; the text stays in memory.

        Args:
            text: what the recognizer heard, "" for nothing.
            outcome: no_words, no_aria, echo, failed, hold, or the command acted on.
            label: the classifier's label, when it decided.
        """
        self.heard_feed.append(
            {
                "t_ms": time.time() * 1000,
                "text": text,
                "outcome": outcome,
                "label": label,
                "language": self.last_language,
            }
        )
        self.console_update(feed=list(self.heard_feed))

    def update_mic(self):
        """What the microphone is doing, for the console: speech while deaf never gets a transcript."""
        now = time.monotonic()
        if now - self.last_audio > MIC_SILENT_SECONDS:
            state = "no_audio"
        elif self.audio_active:
            state = "talk"
        elif not self.config["open_mic"]:
            state = "off"
        elif self.deaf(now):
            state = "deaf"
        elif self.endpointer.speaking:
            state = "speech"
        elif self.audio_busy or self.utterances:
            state = "transcribing"
        else:
            state = "listening"
        if state != self.mic_state:
            self.mic_state = state
            self.console_update(mic=state)

    def deaf(self, now):
        """True while the configured echo guard keeps the open mic off."""
        return (
            self.config["open_mic_deaf_while_speaking"]
            and now < self.speaking_until + ECHO_GUARD_SECONDS
        )

    def record(self, kind, **fields):
        if kind == "reply" and self.voice_turn:
            fields["voice"] = True
        try:
            self.trial.event(kind, **fields)
        except OSError as e:  # A full disk must not stop guidance.
            self.get_logger().error(f"Trial event could not be written: {e}")

    def on_image(self, message):
        stamp = message.header.stamp
        ts = stamp.sec * 1000 + stamp.nanosec / 1e6
        # 10% slack: a 5 Hz camera jitters around 200 ms and would otherwise be thinned to ~3 Hz.
        if ts - self.last_image_ms < 0.9 * self.config["image_interval_ms"]:
            return
        try:
            jpeg = encode_jpeg(ros_image_to_pil(message))
        except ValueError as e:
            self.status("image_error", str(e))
            return
        if self.frames.add(ts, jpeg):
            self.last_image_ms = ts
            self.camera_frame = message.header.frame_id
            # The 640 px JPEG already made for the model; the console never sees raw frames.
            self.console_preview = jpeg
            self.console_wake.set()
            path = self.trial.frame_path(ts)
            if path:
                self.pool.submit(path.write_bytes, jpeg)

    def start(self, goal):
        goal = goal.strip()
        if not 3 <= len(goal) <= 1500:
            self.status("invalid_goal", "Goal must contain 3–1500 characters")
            return
        if self.task.active:
            self.record("reply", text="goal: " + goal)
        self.task.start(goal)
        self.audio_active, self.audio_chunks = False, []
        self.window, self.held_plan = None, None
        try:
            self.trial.open(
                goal,
                list(self.frames.frames),
                self.camera_info()["source"],
                list(self.audio_buffer),
            )
        except OSError as e:
            self.get_logger().error(f"Trial folder could not be written: {e}")
        if self.voice_turn:  # The transcript was recorded before this trial existed.
            self.record("transcript", text=self.last_heard, voice=True, **self.heard_timing)
        self.status("ready", goal)

    def reply(self, text):
        command, text = parse_reply(text, self.task)
        if command == "goal":
            self.start(text)
        elif command == "note":
            self.note(text)
        elif command == "cancel":
            self.cancel()
        elif command == "repeat":
            self.repeat()
        elif command == "confirm":
            self.confirm()
        elif command == "finish":
            self.finish()
        elif command == "feedback":
            if self.task.feedback(text):
                self.record("reply", text=text)
                self.status("ready", text)
            else:
                self.status("busy", "Wait for the current plan to finish")
        else:
            self.status("idle", "Set a goal first")

    def note(self, text):
        if not text.strip():
            return
        if self.trial.folder:
            self.record("note", text=text.strip())
            self.status("note", text.strip())
        else:
            self.status("idle", "Notes need an open trial; set a goal first")

    def cancel(self):
        if not self.task.active:
            self.status("idle", "No task to cancel")
            return
        self.task.cancel()
        self.audio_active, self.audio_chunks = False, []
        self.window, self.held_plan = None, None
        self.record("reply", text="cancel")
        self.status("cancelled", "Pending planner output is invalidated")
        self.trial.close("cancelled")

    def repeat(self):
        if self.task.last and self.task.active:
            self.speak(self.task.last, repeat=True)
        else:
            self.status("idle", "No instruction to repeat")

    def confirm(self):
        if self.task.confirm():
            self.complete("confirm")

    def finish(self):
        if self.task.finish():
            self.complete("finish")

    def complete(self, reply):
        self.window, self.held_plan = None, None
        self.record("reply", text=reply)
        self.publish_tts("Task complete.")
        self.record("tts", text="Task complete.", repeat=False)
        self.status("completed", "Goal reached; set a new goal to continue")
        self.trial.close("completed")

    def plan_if_ready(self):
        """Starts a plan when the task wants one, nothing else is running and frames are fresh."""
        if self.task.state != "ready" or self.planning or self.audio_active or self.audio_busy:
            return
        if self.utterances:
            return
        try:
            frames = self.frames.sample(now_ms=time.time() * 1000)
        except WindowNotReady as e:
            if time.monotonic() - self.last_wait_log > 2:
                self.status("collecting", str(e))
                self.last_wait_log = time.monotonic()
            return
        epoch, goal, history = self.task.begin_plan()
        self.planning = True
        self.record("plan_request", frames=[f.timestamp_ms for f in frames])
        self.status("planning", "Sending 20 frames to Qwen")
        self.pool.submit(self.plan, epoch, goal, history, frames)

    def plan(self, epoch, goal, history, frames):
        started = time.monotonic()
        try:
            result = self.client.plan_v1(goal, history, [f.payload() for f in frames])
            result["client_roundtrip_ms"] = (time.monotonic() - started) * 1000
            self.messages.put(("plan", epoch, result))
        except Exception as e:
            LOGGER.exception("Planning request failed")
            self.messages.put(("plan_error", epoch, str(e)))

    def speak(self, result, repeat=False):
        if time.monotonic() < self.speaking_until:
            self.status("speech_guard", "Wait for the previous utterance, then send repeat")
            self.record("tts_suppressed", text=result["instruction"])
            return
        text = result["instruction"]
        if result["status"] == "done_proposed":
            text += CONFIRM_HINT
        self.publish_tts(text)
        self.record("tts", text=text, repeat=repeat)
        self.status(result["status"], text)
        if result["status"] in {"done_proposed", "clarification"}:
            self.open_window("reply", REPLY_WINDOW_SECONDS)

    def publish_tts(self, text):
        self.tts_pub.publish(String(data=text))
        self.last_tts = text
        # Upstream provides no playback acknowledgement. This is only an estimate for PTT/echo.
        estimated = max(len(text.split()) / 2.3, len(text) / 12) + SPEECH_END_MARGIN_SECONDS
        self.speaking_until = time.monotonic() + estimated

    def on_talk(self, request, response):
        response.success, response.message = self.talk(request.data)
        return response

    def talk(self, on):
        """Push-to-talk on the glasses' microphone; returns (accepted, message)."""
        if on:
            if (
                self.audio_active
                or self.audio_busy
                or self.planning
                or self.held_plan
                or time.monotonic() < self.speaking_until
            ):
                message = "Wait for planning, recording, transcription or speech to finish"
                self.status("talk_refused", message)
                return False, message
            self.audio_active, self.audio_chunks, self.audio_rate = True, [], 0
            self.audio_started = time.monotonic()
            self.endpointer.reset()
            self.utterances.clear()
            self.status("recording", "Recording; release to send")
            return True, "Recording; call with data: false when finished"
        if not self.audio_active:
            return False, "Not recording"  # E.g. the release after a refused start.
        if self.finish_audio():
            self.status("transcribing", "Recognizing speech")
            return True, "Transcribing"
        self.status("silence", "No microphone audio was captured")
        return False, "No valid microphone audio was captured"

    def camera_info(self):
        """Which camera feeds the node, its rate over the last ~3 s, and its newest frame's age."""
        window = list(self.frames.frames)
        frames = window[-16:]
        if not frames:
            return {"source": None, "frame_id": None, "hz": 0.0, "age_s": None, "window_s": 0.0}
        span_ms = frames[-1].timestamp_ms - frames[0].timestamp_ms
        return {
            # Seconds of camera buffered; planning needs about 10 s.
            "window_s": round(
                min(window[-1].timestamp_ms - window[0].timestamp_ms, 10_000) / 1000, 1
            ),
            "source": CAMERA_SOURCES.get(self.camera_frame, self.camera_frame),
            "frame_id": self.camera_frame,
            "hz": round((len(frames) - 1) * 1000 / span_ms, 1) if span_ms else 0.0,
            "age_s": round(time.time() - frames[-1].timestamp_ms / 1000, 1),
        }

    def console_command(self, command):
        kind, text = command["kind"], command["text"]
        if kind == "goal":
            self.start(text)
        elif kind == "reply":
            self.reply(text)
        elif kind == "note":
            self.note(text)
        elif kind == "talk":
            self.talk(text == "on")

    def console_push(self):
        """Sends state on change (and every 2 s, as a heartbeat) and each new preview frame."""
        sent, sent_at, failing = None, 0.0, False
        while not self.closing.is_set():
            self.console_wake.wait(2.0)
            self.console_wake.clear()
            state, preview = self.console_state, self.console_preview
            self.console_preview = None
            try:
                if state is not None and (state is not sent or time.monotonic() - sent_at > 2):
                    # Camera health rides on every push, so the 2 s heartbeat keeps it current.
                    self.client.live_state({**state, "camera": self.camera_info()})
                    sent, sent_at = state, time.monotonic()
                if preview:
                    self.client.live_preview(preview)
                failing = False
            except (httpx.HTTPError, RuntimeError) as e:  # RuntimeError: client closed.
                if not failing:
                    self.get_logger().warning(f"Web console unreachable: {e}")
                failing = True
                self.closing.wait(2.0)

    def console_pull(self):
        """Long-polls the backend for console commands; tick runs them on the ROS thread."""
        while not self.closing.is_set():
            try:
                for command in self.client.live_commands(wait=10.0):
                    self.messages.put(("console", None, command))
            except (httpx.HTTPError, RuntimeError):
                self.closing.wait(2.0)  # Backend down; console_push logs it.

    def on_audio(self, message):
        if message.sample_rate == 0:
            return  # The first upstream block can have an unknown rate.
        self.last_audio = time.monotonic()
        try:
            samples = pcm32_to_mono(message.data, message.channels, self.config["audio_gain"])
        except ValueError as e:
            self.audio_active = False
            if self.last_state != "audio_error":  # Every block fails the same way.
                self.status("audio_error", str(e))
            return
        stamp = message.header.stamp
        stamp_ms = stamp.sec * 1000 + stamp.nanosec / 1e6
        self.keep_audio(stamp_ms, samples, message.sample_rate)
        if self.audio_active:
            self.collect_talk(samples, message.sample_rate, stamp_ms)
        elif self.config["open_mic"]:
            self.listen(samples, message.sample_rate, stamp_ms)

    def keep_audio(self, stamp_ms, samples, rate):
        """Buffers the block for the next trial's opening, and saves it to the open trial."""
        self.audio_buffer.append((stamp_ms, samples, rate))
        while stamp_ms - self.audio_buffer[0][0] > AUDIO_BUFFER_MS:
            self.audio_buffer.popleft()
        try:
            self.trial.audio(stamp_ms, samples, rate)
        except OSError as e:  # A full disk must not stop guidance.
            self.get_logger().error(f"Trial audio could not be written: {e}")

    def collect_talk(self, samples, rate, stamp_ms):
        if self.audio_rate and self.audio_rate != rate:
            self.audio_active = False
            self.status("audio_error", "Sample rate changed during recording; record again")
            return
        self.audio_rate = rate
        if not self.audio_chunks:
            self.speech_started_ms = stamp_ms
        self.speech_last_ms = stamp_ms + len(samples) * 1000 / rate
        self.audio_chunks.append(samples)
        if sum(len(a) for a in self.audio_chunks) / rate >= self.config["max_audio_seconds"]:
            self.finish_audio()

    def listen(self, samples, rate, stamp_ms):
        """Feeds the open mic to the endpointer."""
        if self.deaf(time.monotonic()):
            self.endpointer.reset()
            return
        was_speaking = self.endpointer.speaking
        utterance = self.endpointer.feed(samples, rate)
        if self.endpointer.speaking and not was_speaking:
            self.speech_started = time.monotonic()
            self.speech_started_ms = stamp_ms
        if utterance is not None:
            # The endpoint comes after silence_s of trailing quiet, unless max_s cut the speech.
            stamps = {
                "speech_started_ms": self.speech_started_ms,
                "speech_endpointed_ms": stamp_ms + len(samples) * 1000 / rate,
            }
            self.utterances.append((utterance, rate, self.speech_started, stamps))

    def hear_next(self):
        """Starts one queued utterance; ASR takes one at a time, and audio_busy holds back plans."""
        if self.audio_busy or self.audio_active or not self.utterances:
            return
        samples, rate, started, stamps = self.utterances.popleft()
        self.audio_busy = True
        self.pool.submit(self.recognize, mono_to_wav(samples, rate), started, stamps)

    def timed_transcribe(self, wav, stamps):
        """Transcribes, adding the `timing` the trial keeps with the transcript."""
        started = time.monotonic()
        result = self.client.transcribe(wav)
        result["timing"] = {
            **stamps,
            "duration_ms": result.get("duration_ms"),
            "client_roundtrip_ms": (time.monotonic() - started) * 1000,
        }
        return result

    def recognize(self, wav, started, stamps):
        try:
            self.messages.put(("heard", started, self.timed_transcribe(wav, stamps)))
        except Exception as e:
            LOGGER.exception("Transcription failed")
            self.messages.put(("heard_error", started, str(e)))

    def open_window(self, kind, seconds):
        self.window = (kind, max(time.monotonic(), self.speaking_until) + seconds)

    def on_heard(self, started, result):
        """Decides what one open-mic transcript asks for; only a discard leaves no trace."""
        self.heard_timing = result.get("timing", {})
        text = result["text"]
        self.last_language = result.get("language")
        window = self.window[0] if self.window and started <= self.window[1] else None
        command, rest = interpret(text, self.task, window)
        # Without the margin: someone repeating a hint just after it must not count as its echo.
        during_speech = started < self.speaking_until - SPEECH_END_MARGIN_SECONDS
        if command == "discard" or is_echo(text, self.last_tts, during_speech):
            self.audio_busy = False
            self.get_logger().debug(f"Discarded: {text!r}")
            if not text.strip():
                self.log_heard("", "no_words")
            else:
                self.log_heard(text, "no_aria" if command == "discard" else "echo")
            return
        self.window = None
        if command == "classify":
            last = self.task.last
            self.pool.submit(
                self.classify,
                self.task.epoch,
                text,
                rest,
                self.task.goal or "",
                self.task.state,
                last["instruction"] if last else "",
            )
            return  # audio_busy stays set, so no plan takes the model first.
        self.audio_busy = False
        if command == "listen":
            self.open_window("goal" if window == "goal" else "aria", ARIA_WINDOW_SECONDS)
        self.act(command, rest, text, command)

    def classify(self, epoch, text, rest, goal, state, instruction):
        try:
            result = self.client.intent(rest, goal, state, instruction)
            self.messages.put(("intent", epoch, (text, rest, result["label"])))
        except Exception as e:
            LOGGER.exception("Intent classification failed")
            self.messages.put(("intent_error", epoch, (text, str(e))))

    def on_intent(self, epoch, text, rest, label):
        if epoch != self.task.epoch or not self.task.active:
            return  # The task moved on while the model ran.
        if label is None:
            self.act("say", SAY_SORRY, text, "unclear")
            return
        command, reply = route(label, rest, self.task)
        self.act(command, reply, text, label)

    def act(self, command, text, heard, label):
        """Carries out a command from speech and records what was heard.

        Args:
            command: from intent.interpret or intent.route.
            text: the command's words: a goal, a reply, or the line to say.
            heard: the whole transcript.
            label: the command or the classifier's label, kept with the transcript.
        """
        self.last_heard = heard
        self.transcript_pub.publish(String(data=heard))
        self.record("transcript", text=heard, intent=label, **self.heard_timing)
        self.log_heard(heard, command, label if label != command else None)
        self.voice_turn = True
        try:
            if command == "start":
                self.start(text)
            elif command == "ask_goal":
                self.say(SAY_ASK_GOAL)
                self.open_window("goal", GOAL_WINDOW_SECONDS)
            elif command == "say":
                self.say(text)
            elif command == "feedback":
                self.task.abandon_plan(self.task.epoch)
                self.held_plan = None
                self.say(SAY_OKAY)
                self.reply(text)
            elif command == "repeat":
                if not self.held_plan:  # The instruction about to be spoken is the newer one.
                    self.repeat()
            elif command == "cancel":
                self.cancel()
            elif command == "finish":
                self.finish()
            elif command == "confirm":
                self.confirm()
            elif command == "note":
                self.note(text)
        finally:
            self.voice_turn = False
        if command in {"none", "listen", "say", "ask_goal"} and self.last_state is not None:
            self.status(self.last_state, self.last_detail)  # Pushes `heard` to the console.

    def say(self, text):
        self.publish_tts(text)
        self.record("tts", text=text, repeat=False)

    def speech_pending(self):
        """True while speech may change what a finished plan should say."""
        if self.utterances or self.audio_busy:
            return True
        return (
            self.endpointer.speaking
            and time.monotonic() - self.held_since < HOLD_FOR_SPEECH_SECONDS
        )

    def accept_plan(self, epoch, payload):
        if epoch != self.task.epoch:
            return
        if payload["client_roundtrip_ms"] > self.config["max_result_age_seconds"] * 1000:
            self.task.fail_plan(epoch)
            self.record(
                "tts_suppressed",
                text=payload["instruction"],
                reason="stale",
                client_roundtrip_ms=payload["client_roundtrip_ms"],
                timings=payload.get("timings"),
            )
            self.status("stale", "Result took too long; say next for a fresh observation")
        elif self.task.finish_plan(epoch, payload):
            self.record("plan_result", **payload)
            self.speak(payload)

    def release_held_plan(self):
        if self.held_plan and not self.speech_pending() and time.monotonic() >= self.speaking_until:
            epoch, payload = self.held_plan
            self.held_plan = None
            self.accept_plan(epoch, payload)

    def finish_audio(self):
        self.audio_active = False
        if not self.audio_chunks or not self.audio_rate:
            return False
        samples = np.concatenate(self.audio_chunks)
        self.audio_chunks = []
        wav = mono_to_wav(samples, self.audio_rate)
        epoch = self.task.epoch
        self.audio_busy = True
        stamps = {
            "speech_started_ms": self.speech_started_ms,
            "speech_endpointed_ms": self.speech_last_ms,
        }

        def transcribe():
            try:
                self.messages.put(("transcript", epoch, self.timed_transcribe(wav, stamps)))
            except Exception as e:
                LOGGER.exception("Transcription failed")
                self.messages.put(("error", epoch, str(e)))
            finally:
                self.audio_busy = False

        self.pool.submit(transcribe)
        return True

    def tick(self):
        while not self.messages.empty():
            kind, epoch, payload = self.messages.get()
            if kind == "console":
                self.console_command(payload)
                continue
            if kind == "heard":
                self.on_heard(epoch, payload)  # `epoch` carries when the speech started.
                continue
            if kind == "heard_error":
                self.audio_busy = False
                self.record("error", text=payload)
                self.log_heard("", "failed")
                continue
            if kind == "intent":
                self.audio_busy = False
                self.on_intent(epoch, *payload)
                continue
            if kind == "intent_error":
                self.audio_busy = False
                if epoch == self.task.epoch and self.task.active:
                    self.record("error", text=payload[1])
                    self.act("say", SAY_SORRY, payload[0], "unclear")
                continue
            if kind in {"plan", "plan_error"}:
                self.planning = False
            if epoch != self.task.epoch:
                continue
            if kind == "plan":
                # The open mic can overlap a plan: speech may replace it, and speech is not cut off.
                self.held_since = time.monotonic()
                wait = self.speech_pending() or time.monotonic() < self.speaking_until
                if self.config["open_mic"] and wait:
                    self.held_plan = (epoch, payload)
                else:
                    self.accept_plan(epoch, payload)
            elif kind == "plan_error":
                self.task.fail_plan(epoch)
                self.record("error", text=payload)
                self.status("error", payload)
            elif kind == "error":
                self.record("error", text=payload)
                self.log_heard("", "failed")
                self.status("error", payload)
            elif kind == "transcript":
                text = payload["text"]
                self.transcript_pub.publish(String(data=text))
                self.last_heard = text
                self.heard_timing = payload.get("timing", {})
                self.record("transcript", text=text, **self.heard_timing)
                self.last_language = payload.get("language")
                self.log_heard(text, "hold" if text else "no_words")
                if text:
                    self.voice_turn = True
                    try:
                        self.reply(text)
                    finally:
                        self.voice_turn = False
                else:
                    self.status("silence", "No speech recognized; record again")
        self.hear_next()
        self.release_held_plan()
        self.plan_if_ready()
        self.update_mic()
        if (
            self.audio_active
            and time.monotonic() - self.audio_started > self.config["max_audio_seconds"]
        ):
            self.finish_audio()

    def close(self):
        self.closing.set()
        self.task.cancel()
        self.trial.close("aborted")
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.client.close()


def main(args=None):
    rclpy.init(args=args)
    node = GuidanceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        rclpy.shutdown()
