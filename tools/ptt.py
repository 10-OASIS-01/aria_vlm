"""Debug-only push-to-talk window for the planner bridge.

Hold Space (window focused) or the mouse button to record; release to transcribe. It calls the
bridge's /planner/listen service from one long-lived node, so no words are lost to CLI start-up.
Also shows the live mic level, /planner/status and /planner/transcript.
"""

import json
import queue
import threading
import tkinter as tk

import numpy as np
import rclpy
from aria_msgs.msg import Audio
from rclpy.executors import ExternalShutdownException, SingleThreadedExecutor
from rclpy.node import Node
from std_msgs.msg import String
from std_srvs.srv import SetBool

from aria_guidance.audio import pcm32_to_mono

CUTOFF = 0.002  # Backend silence cutoff on the mono RMS.
FULL = 0.01  # Level bar maps 0..FULL.
RELEASE_DEBOUNCE_MS = 80  # X11 auto-repeat sends release/press pairs while a key is held.


class Ptt(Node):
    def __init__(self, events):
        super().__init__("ptt")
        self.events = events
        self.gain = float(self.declare_parameter("gain", 16.0).value)
        self.listen = self.create_client(SetBool, "/planner/listen")
        self.chunks = []
        self.lock = threading.Lock()
        self.create_subscription(Audio, "/aria/audio", self.on_audio, 100)
        self.create_subscription(String, "/planner/status", self.on_status, 10)
        self.create_subscription(String, "/planner/transcript", self.on_transcript, 10)

    def on_audio(self, message):
        if message.sample_rate:
            with self.lock:
                self.chunks.append(pcm32_to_mono(message.data, message.channels, self.gain))

    def take_level(self):
        with self.lock:
            chunks, self.chunks = self.chunks, []
        return float(np.sqrt(np.mean(np.concatenate(chunks) ** 2))) if chunks else None

    def on_status(self, message):
        try:
            data = json.loads(message.data)
            self.events.put(("status", f"{data['state']}: {data['detail']}"))
        except (ValueError, KeyError):
            self.events.put(("status", message.data))

    def on_transcript(self, message):
        self.events.put(("transcript", message.data or "(empty: nothing recognized)"))

    def call(self, start, done):
        if not self.listen.service_is_ready():
            self.events.put(("reply", "/planner/listen not available; is the bridge running?"))
            done(False)
            return
        future = self.listen.call_async(SetBool.Request(data=start))

        def finished(f):
            result = f.result()
            self.events.put(("reply", f"{'start' if start else 'stop'}: {result.message}"))
            done(result.success)

        future.add_done_callback(finished)


class Window:
    def __init__(self, node, events):
        self.node, self.events = node, events
        self.held = False
        self.recording = False  # Bridge accepted the start call.
        self.stop_wanted = False  # Released before the start reply arrived.
        self.release_job = None

        self.root = tk.Tk()
        self.root.title("Aria push-to-talk")
        self.root.geometry("560x300")
        self.button = tk.Label(
            self.root, text="Hold SPACE or mouse to talk", font=("Sans", 18, "bold"),
            bg="#2d3e50", fg="white", height=3,
        )
        self.button.pack(fill="x", padx=10, pady=(10, 6))
        self.level = tk.Canvas(self.root, height=18, bg="#1b1b1b", highlightthickness=0)
        self.level.pack(fill="x", padx=10)
        self.level_text = tk.Label(self.root, anchor="w", font=("Sans", 9))
        self.level_text.pack(fill="x", padx=10)
        self.lines = {}
        for key in ("reply", "transcript", "status"):
            label = tk.Label(self.root, anchor="w", justify="left", wraplength=530, font=("Sans", 11))
            label.pack(fill="x", padx=10, pady=2)
            self.lines[key] = label
        self.show("reply", "-")
        self.show("transcript", "-")
        self.show("status", "-")

        self.root.bind("<KeyPress-space>", lambda e: self.press())
        self.root.bind("<KeyRelease-space>", lambda e: self.release_soon())
        self.button.bind("<ButtonPress-1>", lambda e: self.press())
        self.button.bind("<ButtonRelease-1>", lambda e: self.release())
        self.root.after(50, self.poll)
        self.root.after(250, self.draw_level)

    def show(self, key, text):
        self.lines[key].config(text=f"{key}: {text}")

    def press(self):
        if self.release_job:  # Auto-repeat: the key never really went up.
            self.root.after_cancel(self.release_job)
            self.release_job = None
        if self.held:
            return
        self.held, self.stop_wanted = True, False
        self.button.config(text="Starting...", bg="#b8860b")
        self.node.call(True, self.started)

    def started(self, ok):
        self.events.put(("started", ok))

    def release_soon(self):
        self.release_job = self.root.after(RELEASE_DEBOUNCE_MS, self.release)

    def release(self):
        self.release_job = None
        if not self.held:
            return
        self.held = False
        if self.recording:
            self.stop()
        else:
            self.stop_wanted = True  # Start still pending; stop when it is accepted.

    def stop(self):
        self.recording = False
        self.button.config(text="Transcribing...", bg="#2d3e50")
        self.node.call(False, lambda ok: None)

    def poll(self):
        while not self.events.empty():
            kind, payload = self.events.get()
            if kind == "started":
                if payload:
                    self.recording = True
                    if self.stop_wanted or not self.held:
                        self.stop()
                    else:
                        self.button.config(text="RECORDING - release to send", bg="#b22222")
                else:
                    self.button.config(text="Hold SPACE or mouse to talk", bg="#2d3e50")
            else:
                self.show(kind, payload)
                if kind in ("transcript", "status") and not self.held:
                    self.button.config(text="Hold SPACE or mouse to talk", bg="#2d3e50")
        self.root.after(50, self.poll)

    def draw_level(self):
        rms = self.node.take_level()
        width = self.level.winfo_width()
        self.level.delete("all")
        if rms is None:
            self.level_text.config(text="mic: NO AUDIO from /aria/audio")
        else:
            fill = min(width, int(rms / FULL * width))
            colour = "#3cb371" if rms >= CUTOFF else "#808080"
            self.level.create_rectangle(0, 0, fill, 18, fill=colour, width=0)
            self.level_text.config(
                text=f"mic level {rms:.4f} (x{self.node.gain:g})  white line = {CUTOFF} cutoff"
            )
        x = int(CUTOFF / FULL * width)
        self.level.create_line(x, 0, x, 18, fill="white", width=2)
        self.root.after(250, self.draw_level)


def main():
    rclpy.init()
    events = queue.SimpleQueue()
    node = Ptt(events)
    executor = SingleThreadedExecutor()
    executor.add_node(node)

    def spin():
        try:
            executor.spin()
        except ExternalShutdownException:
            pass

    threading.Thread(target=spin, daemon=True).start()
    window = Window(node, events)
    try:
        window.root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if window.recording:  # Do not leave the bridge recording.
            node.listen.call_async(SetBool.Request(data=False))
        executor.shutdown(timeout_sec=1)
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
