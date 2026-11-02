"""Debug-only live meter for /aria/audio: level vs the backend's 0.002 silence cutoff, plus block rate.

The level uses guidance_node's own pcm32_to_mono, so the gain parameter previews its audio_gain.
"""

import time

import numpy as np
import rclpy
from aria_msgs.msg import Audio
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from aria_guidance.audio import pcm32_to_mono

CUTOFF = 0.002  # RMS below this makes SpeechRecognizer return an empty transcript.
FULL = 0.01  # Bar length maps 0..FULL.
WIDTH = 50


class MicLevel(Node):
    def __init__(self):
        super().__init__("mic_level")
        self.gain = float(self.declare_parameter("gain", 1.0).value)
        self.chunks, self.blocks, self.rate, self.channels = [], 0, 0, 0
        self.last_block = None
        self.create_subscription(Audio, "/aria/audio", self.on_audio, 100)
        self.create_timer(0.5, self.report)
        print(f"Listening on /aria/audio, gain x{self.gain:g}; '|' marks the {CUTOFF} cutoff")

    def on_audio(self, message):
        self.blocks += 1
        self.last_block = time.monotonic()
        if message.sample_rate:
            self.rate, self.channels = message.sample_rate, message.channels
            self.chunks.append(pcm32_to_mono(message.data, message.channels, self.gain))

    def report(self):
        blocks, chunks = self.blocks, self.chunks
        self.blocks, self.chunks = 0, []
        if not chunks:
            age = (
                "never"
                if self.last_block is None
                else f"{time.monotonic() - self.last_block:.1f} s ago"
            )
            print(f"NO AUDIO  (last block {age})", flush=True)
            return
        rms = float(np.sqrt(np.mean(np.concatenate(chunks) ** 2)))
        filled = min(WIDTH, round(rms / FULL * WIDTH))
        mark = round(CUTOFF / FULL * WIDTH)
        bar = "".join("#" if i < filled else ("|" if i == mark else " ") for i in range(WIDTH))
        verdict = "speech-level" if rms >= CUTOFF else "below cutoff"
        print(
            f"[{bar}] rms {rms:.4f} {verdict:<12} {blocks * 2:>3} blocks/s "
            f"{self.rate} Hz x{self.channels}ch",
            flush=True,
        )


def main():
    rclpy.init()
    node = MicLevel()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
