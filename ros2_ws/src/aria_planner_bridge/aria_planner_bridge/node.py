"""User-paced Aria bridge. All network/model work stays off the ROS callback thread."""

import json
import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import rclpy
from aria_msgs.msg import Audio
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from std_msgs.msg import String
from std_srvs.srv import SetBool

from aria_guidance.audio import mono_to_wav, pcm32_to_mono
from aria_guidance.client import GuidanceClient
from aria_guidance.frames import FrameBuffer, WindowNotReady, encode_jpeg, ros_image_to_pil

LOGGER = logging.getLogger(__name__)


class PlannerBridge(Node):
    def __init__(self):
        super().__init__("planner_bridge")
        defaults = {
            "backend_url": "http://127.0.0.1:8785",
            "image_topic": "/aria/rgb/image_raw",
            "audio_topic": "/aria/audio",
            "tts_topic": "/aria/tts",
            "max_result_age_seconds": 60.0,
            "image_interval_ms": 200.0,
            "max_audio_seconds": 20.0,
            "audio_gain": 1.0,
        }
        for name, default in defaults.items():
            self.declare_parameter(name, default)
        self.config = {name: self.get_parameter(name).value for name in defaults}
        self.client = GuidanceClient(self.config["backend_url"])
        self.frames = FrameBuffer()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="aria-http")
        self.lock = threading.RLock()
        self.messages = queue.SimpleQueue()
        self.epoch = 0
        self.session = None
        self.busy = False
        self.need_initial_plan = False
        self.last_instruction = None
        self.published_ids = set()
        self.last_image_ms = -1.0
        self.speaking_until = 0.0
        self.audio_active = False
        self.audio_chunks = []
        self.audio_rate = 0
        self.audio_started = 0.0
        self.audio_busy = False
        self.last_wait_log = 0.0
        self.status_pub = self.create_publisher(String, "/planner/status", 10)
        self.transcript_pub = self.create_publisher(String, "/planner/transcript", 10)
        self.tts_pub = self.create_publisher(String, self.config["tts_topic"], 10)
        self.create_subscription(
            Image, self.config["image_topic"], self.on_image, qos_profile_sensor_data
        )
        self.create_subscription(Audio, self.config["audio_topic"], self.on_audio, 100)
        self.create_subscription(String, "/planner/goal", self.on_goal, 10)
        self.create_subscription(String, "/planner/command", self.on_command, 10)
        self.create_service(SetBool, "/planner/listen", self.on_listen)
        self.create_timer(0.2, self.tick)
        self.status("ready", "Publish a goal to /planner/goal or record one with /planner/listen")

    def status(self, state, detail):
        self.status_pub.publish(String(data=json.dumps({"state": state, "detail": detail})))
        self.get_logger().info(f"{state}: {detail}")

    def on_image(self, message):
        stamp = message.header.stamp
        ts = stamp.sec * 1000 + stamp.nanosec / 1e6
        if ts - self.last_image_ms < self.config["image_interval_ms"]:
            return
        try:
            self.frames.add(ts, encode_jpeg(ros_image_to_pil(message)))
            self.last_image_ms = ts
        except ValueError as e:
            self.status("image_error", str(e))

    def on_goal(self, message):
        goal = message.data.strip()
        if not 3 <= len(goal) <= 1500:
            self.status("invalid_goal", "Goal must contain 3–1500 characters")
            return
        with self.lock:
            old = self.session
            self.epoch += 1
            epoch = self.epoch
            self.session, self.last_instruction = None, None
            self.busy, self.need_initial_plan = True, False
            self.audio_active, self.audio_chunks = False, []
        self.status("creating_task", goal)
        self.pool.submit(self.begin_goal, epoch, goal, old)

    def begin_goal(self, epoch, goal, old):
        try:
            if old:
                self.client.feedback(old["id"], "cancel")
            session = self.client.create_session(goal)
            with self.lock:
                current = epoch == self.epoch
                if current:
                    self.session = session
                    self.need_initial_plan = True
            if not current:
                self.client.feedback(session["id"], "cancel")
        except Exception as e:
            LOGGER.exception("Could not create guidance session")
            self.messages.put(("error", epoch, str(e)))
        finally:
            with self.lock:
                if epoch == self.epoch:
                    self.busy = False

    def on_command(self, message):
        text = message.data.strip()
        command = text.lower().rstrip(".!?。！？")
        if command in {"cancel", "stop", "停止", "取消"}:
            with self.lock:
                old = self.session
                self.epoch += 1
                self.session, self.last_instruction = None, None
                self.busy, self.need_initial_plan = False, False
                self.audio_active, self.audio_chunks = False, []
            if old:
                self.pool.submit(self.cancel_remote, old["id"])
            self.status("cancelled", "Pending planner output is invalidated")
        elif command in {"repeat", "重复", "再说一遍"}:
            with self.lock:
                last, session = self.last_instruction, self.session
            if last and session:
                self.speak(session["id"], last, repeat=True)
            else:
                self.status("idle", "No instruction to repeat")
        elif command.startswith("goal:"):
            self.on_goal(String(data=text[5:].strip()))
        elif command in {"next", "done", "完成", "下一步", "好了"}:
            self.trigger(text)
        elif text:
            if not self.session:
                self.on_goal(String(data=text))
            else:
                self.trigger(text)

    def cancel_remote(self, session_id):
        try:
            self.client.feedback(session_id, "cancel")
        except Exception as e:
            LOGGER.exception("Remote cancellation failed")
            self.get_logger().warning(f"Remote cancellation failed: {e}")

    def trigger(self, feedback="", initial=False):
        with self.lock:
            if self.audio_active or self.audio_busy:
                if not initial:
                    self.status("recording", "Finish recording and transcription before planning")
                return
            if self.busy or not self.session:
                if not initial:
                    self.status(
                        "busy" if self.busy else "idle", "Wait for the request or set a goal first"
                    )
                return
            if self.session.get("state") == "completed":
                self.status("completed", "Goal is complete; set a new goal to continue")
                return
            try:
                frames = self.frames.sample(now_ms=time.time() * 1000)
            except WindowNotReady as e:
                if time.monotonic() - self.last_wait_log > 2:
                    self.status("collecting", str(e))
                    self.last_wait_log = time.monotonic()
                return
            self.busy, self.need_initial_plan = True, False
            epoch, session = self.epoch, self.session
        self.status("planning", "Sending 20 frames to Qwen")
        self.pool.submit(self.plan, epoch, session, frames, feedback, initial)

    def plan(self, epoch, session, frames, feedback, initial):
        started = time.monotonic()
        try:
            if not initial:
                self.client.feedback(session["id"], "feedback", feedback or "Next step, please.")
            with self.lock:
                if epoch != self.epoch:
                    return
            result = self.client.plan(session["id"], [f.payload() for f in frames])
            result["client_roundtrip_ms"] = (time.monotonic() - started) * 1000
            self.messages.put(("plan", epoch, (session, result)))
        except Exception as e:
            LOGGER.exception("Planning request failed")
            self.messages.put(("plan_error", epoch, str(e)))

    def speak(self, session_id, result, repeat=False):
        rid = result["request_id"]
        if not repeat and rid in self.published_ids:
            return
        if time.monotonic() < self.speaking_until:
            self.status("speech_guard", "Wait for the previous utterance, then send repeat")
            self.log_event(
                session_id, "tts_suppressed", rid, "Estimated previous speech is still active"
            )
            return
        text = result["instruction"]
        self.tts_pub.publish(String(data=text))
        self.published_ids.add(rid)
        # Upstream provides no playback acknowledgement. This is only an estimate for PTT/echo.
        estimated = max(len(text.split()) / 2.3, len(text) / 12) + 1.0
        self.speaking_until = time.monotonic() + estimated
        self.log_event(session_id, "tts_repeat" if repeat else "tts_published", rid, text)
        self.status(result["status"], text)

    def log_event(self, sid, event, rid, detail, duration_ms=None):
        payload = {
            "event": event,
            "request_id": rid,
            "timestamp_ms": time.time() * 1000,
            "detail": detail,
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms

        def save():
            try:
                self.client.event(sid, **payload)
            except Exception as e:
                LOGGER.exception("Client event could not be saved")
                self.get_logger().warning(f"Event log failed: {e}")

        self.pool.submit(save)

    def on_listen(self, request, response):
        if request.data:
            if (
                self.audio_active
                or self.audio_busy
                or self.busy
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
        epoch = self.epoch
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
            if epoch != self.epoch:
                continue
            if kind in {"error", "plan_error"}:
                if kind == "plan_error":
                    self.busy = False
                self.status("error", payload)
            elif kind == "transcript":
                text = payload["text"]
                self.transcript_pub.publish(String(data=text))
                if self.session:
                    self.log_event(
                        self.session["id"], "transcript", None, text, payload.get("duration_ms")
                    )
                if text:
                    self.on_command(String(data=text))
                else:
                    self.status("silence", "No speech recognized; record again")
            elif kind == "plan":
                self.busy = False
                session, result = payload
                if not self.session or self.session["id"] != session["id"]:
                    continue
                duration = result["client_roundtrip_ms"]
                self.log_event(
                    session["id"],
                    "client_timing",
                    result["request_id"],
                    "Request to model response",
                    duration,
                )
                if duration > self.config["max_result_age_seconds"] * 1000:
                    self.last_instruction = None
                    self.status("stale", "Result took too long; request a fresh observation")
                    self.log_event(
                        session["id"],
                        "tts_suppressed",
                        result["request_id"],
                        "Result exceeded maximum age",
                    )
                else:
                    self.last_instruction = result
                    self.session["state"] = result["status"]
                    self.speak(session["id"], result)
        if self.need_initial_plan:
            self.trigger(initial=True)
        if (
            self.audio_active
            and time.monotonic() - self.audio_started > self.config["max_audio_seconds"]
        ):
            self.finish_audio()

    def close(self):
        with self.lock:
            self.epoch += 1
            old = self.session
        if old:
            self.cancel_remote(old["id"])
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.client.close()


def main(args=None):
    rclpy.init(args=args)
    node = PlannerBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.close()
        node.destroy_node()
        rclpy.shutdown()
