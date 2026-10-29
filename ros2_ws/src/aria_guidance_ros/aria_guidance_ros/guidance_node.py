"""Guidance node: owns the task, calls the stateless backend. Network work stays off ROS callbacks.

The task (aria_guidance.task) is only touched from ROS callbacks, which run one at a time; pool
threads do HTTP and hand results back through `messages`, drained by `tick`.
"""

import json
import logging
import queue
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rclpy
from aria_msgs.msg import Audio
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image
from std_msgs.msg import String
from std_srvs.srv import SetBool

from aria_guidance.audio import mono_to_wav, pcm32_to_mono
from aria_guidance.client import GuidanceClient
from aria_guidance.frames import FrameBuffer, WindowNotReady, encode_jpeg, ros_image_to_pil
from aria_guidance.task import Task, parse_reply
from aria_guidance.trial import FALLBACK_ROOT, TrialRecorder

LOGGER = logging.getLogger(__name__)

# The model only proposes completion; the person closes the task by confirming.
CONFIRM_HINT = " Say done to confirm, or next to continue."
# Late joiners (Foxglove, ptt.py) get the current state at once.
LATCHED = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)


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
            # aria_node's record.output_dir, relative to the same working directory.
            "session_root": "data/aria",
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
        self.last_state = None
        self.planning = False  # A plan request is in flight, possibly for an older epoch.
        self.last_image_ms = -1.0
        self.speaking_until = 0.0
        self.audio_active = False
        self.audio_chunks = []
        self.audio_rate = 0
        self.audio_started = 0.0
        self.audio_busy = False
        self.last_wait_log = 0.0
        self.state_pub = self.create_publisher(String, "/guidance/state", LATCHED)
        # Plain strings for Foxglove panels, which cannot read fields inside the state JSON.
        self.task_pub = self.create_publisher(String, "/guidance/task", LATCHED)
        self.instruction_pub = self.create_publisher(String, "/guidance/instruction", LATCHED)
        self.preview_pub = self.create_publisher(
            CompressedImage, "/guidance/preview/compressed", qos_profile_sensor_data
        )
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
        self.status("idle", "Publish a goal to /guidance/goal or say one with /guidance/talk")

    def status(self, state, detail):
        last = self.task.last
        body = {
            "state": state,
            "detail": detail,
            "task": self.task.state,
            "goal": self.task.goal,
            "instruction": last["instruction"] if last else None,
        }
        self.state_pub.publish(String(data=json.dumps(body, ensure_ascii=False)))
        self.task_pub.publish(String(data=self.task.state))
        self.instruction_pub.publish(String(data=body["instruction"] or ""))
        self.get_logger().info(f"{state}: {detail}")
        if state != self.last_state:
            self.last_state = state
            self.record("state", state=state, detail=detail)

    def record(self, kind, **fields):
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
            # The 640 px JPEG already made for the model; Foxglove never pulls the raw frames.
            self.preview_pub.publish(
                CompressedImage(header=message.header, format="jpeg", data=jpeg)
            )
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
        try:
            self.trial.open(goal, list(self.frames.frames))
        except OSError as e:
            self.get_logger().error(f"Trial folder could not be written: {e}")
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
            self.record("reply", text="confirm")
            self.publish_tts("Task complete.")
            self.record("tts", text="Task complete.", repeat=False)
            self.status("completed", "Goal confirmed; set a new goal to continue")
            self.trial.close("completed")

    def plan_if_ready(self):
        """Starts a plan when the task wants one, nothing else is running and frames are fresh."""
        if self.task.state != "ready" or self.planning or self.audio_active or self.audio_busy:
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

    def publish_tts(self, text):
        self.tts_pub.publish(String(data=text))
        # Upstream provides no playback acknowledgement. This is only an estimate for PTT/echo.
        estimated = max(len(text.split()) / 2.3, len(text) / 12) + 1.0
        self.speaking_until = time.monotonic() + estimated

    def on_talk(self, request, response):
        if request.data:
            if (
                self.audio_active
                or self.audio_busy
                or self.planning
                or time.monotonic() < self.speaking_until
            ):
                response.success, response.message = (
                    False,
                    "Wait for planning, recording, transcription or speech to finish",
                )
                return response
            self.audio_active, self.audio_chunks, self.audio_rate = True, [], 0
            self.audio_started = time.monotonic()
            response.success, response.message = (
                True,
                "Recording; call with data: false when finished",
            )
        else:
            response.success = self.finish_audio()
            response.message = (
                "Transcribing" if response.success else "No valid microphone audio was captured"
            )
        return response

    def on_audio(self, message):
        if not self.audio_active or message.sample_rate == 0:
            return  # The first upstream block can have an unknown rate.
        if self.audio_rate and self.audio_rate != message.sample_rate:
            self.audio_active = False
            self.status("audio_error", "Sample rate changed during recording; record again")
            return
        try:
            self.audio_rate = message.sample_rate
            self.audio_chunks.append(
                pcm32_to_mono(message.data, message.channels, self.config["audio_gain"])
            )
            if (
                sum(len(a) for a in self.audio_chunks) / self.audio_rate
                >= self.config["max_audio_seconds"]
            ):
                self.finish_audio()
        except ValueError as e:
            self.audio_active = False
            self.status("audio_error", str(e))

    def finish_audio(self):
        self.audio_active = False
        if not self.audio_chunks or not self.audio_rate:
            return False
        samples = np.concatenate(self.audio_chunks)
        self.audio_chunks = []
        wav = mono_to_wav(samples, self.audio_rate)
        epoch = self.task.epoch
        self.audio_busy = True

        def transcribe():
            try:
                result = self.client.transcribe(wav)
                self.messages.put(("transcript", epoch, result))
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
            if kind in {"plan", "plan_error"}:
                self.planning = False
            if epoch != self.task.epoch:
                continue
            if kind == "plan":
                if payload["client_roundtrip_ms"] > self.config["max_result_age_seconds"] * 1000:
                    self.task.fail_plan(epoch)
                    self.record("tts_suppressed", text=payload["instruction"], reason="stale")
                    self.status("stale", "Result took too long; say next for a fresh observation")
                elif self.task.finish_plan(epoch, payload):
                    self.record("plan_result", **payload)
                    self.speak(payload)
            elif kind == "plan_error":
                self.task.fail_plan(epoch)
                self.record("error", text=payload)
                self.status("error", payload)
            elif kind == "error":
                self.record("error", text=payload)
                self.status("error", payload)
            elif kind == "transcript":
                text = payload["text"]
                self.transcript_pub.publish(String(data=text))
                self.get_logger().info(f"heard: {text}")
                self.record("transcript", text=text, duration_ms=payload.get("duration_ms"))
                if text:
                    self.reply(text)
                else:
                    self.status("silence", "No speech recognized; record again")
        self.plan_if_ready()
        if (
            self.audio_active
            and time.monotonic() - self.audio_started > self.config["max_audio_seconds"]
        ):
            self.finish_audio()

    def close(self):
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
