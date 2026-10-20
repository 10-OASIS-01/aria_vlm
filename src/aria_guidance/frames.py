"""Timestamped RGB buffering, independent of ROS and torch."""

import base64
import io
import threading
from collections import deque
from dataclasses import dataclass
from itertools import pairwise

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError


class WindowNotReady(ValueError):
    pass


@dataclass(frozen=True)
class BufferedFrame:
    timestamp_ms: float
    jpeg: bytes

    def payload(self):
        return {
            "timestamp_ms": self.timestamp_ms,
            "image_base64": base64.b64encode(self.jpeg).decode("ascii"),
        }


def encode_jpeg(image: Image.Image, max_edge: int = 640) -> bytes:
    image = ImageOps.exif_transpose(image).convert("RGB")
    image.thumbnail((max_edge, max_edge))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return buffer.getvalue()


def decode_image(value: str) -> Image.Image:
    if value.startswith("data:"):
        header, _, value = value.partition(",")
        if header not in {"data:image/jpeg;base64", "data:image/png;base64"}:
            raise ValueError("Only JPEG and PNG data URLs are accepted")
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > 1500 * 1024:
            raise ValueError("Each image must be smaller than 1.5 MiB")
        image = Image.open(io.BytesIO(raw))
        if image.format not in {"JPEG", "PNG"} or image.width * image.height > 4096 * 4096:
            raise ValueError("Image must be JPEG/PNG with at most 16 megapixels")
        image.load()
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((1280, 1280))
        return image
    except (
        UnidentifiedImageError,
        OSError,
        base64.binascii.Error,
        Image.DecompressionBombError,
    ) as e:
        raise ValueError("Invalid image data") from e


def ros_image_to_pil(message) -> Image.Image:
    """Respect row padding; cv_bridge is not needed in the model environment."""
    modes = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}
    channels = modes.get(message.encoding)
    if channels is None:
        raise ValueError(f"Unsupported ROS image encoding: {message.encoding}")
    if message.step < message.width * channels:
        raise ValueError("Image row stride is too small")
    raw = np.frombuffer(bytes(message.data), dtype=np.uint8)
    expected = message.height * message.step
    if raw.size < expected:
        raise ValueError("Truncated ROS image")
    pixels = raw[:expected].reshape(message.height, message.step)
    pixels = pixels[:, : message.width * channels].reshape(message.height, message.width, channels)
    if message.encoding in {"bgr8", "bgra8"}:
        pixels = pixels[:, :, [2, 1, 0]]
    elif channels == 4:
        pixels = pixels[:, :, :3]
    elif channels == 1:
        pixels = np.repeat(pixels, 3, axis=2)
    return Image.fromarray(pixels.copy())


class FrameBuffer:
    def __init__(self, window_ms: float = 10000, maxlen: int = 120):
        self.window_ms = window_ms
        self.frames = deque(maxlen=maxlen)
        self.lock = threading.Lock()

    def add(self, timestamp_ms: float, jpeg: bytes):
        with self.lock:
            if self.frames and timestamp_ms <= self.frames[-1].timestamp_ms:
                return False  # Reject reordering and duplicate capture stamps.
            self.frames.append(BufferedFrame(timestamp_ms, jpeg))
            while self.frames and self.frames[0].timestamp_ms < timestamp_ms - self.window_ms:
                self.frames.popleft()
        return True

    def sample(self, now_ms: float | None = None) -> list[BufferedFrame]:
        with self.lock:
            frames = list(self.frames)
        if len(frames) < 20 or frames[-1].timestamp_ms - frames[0].timestamp_ms < 9000:
            raise WindowNotReady("Collecting a 10-second camera window")
        end = frames[-1].timestamp_ms
        if now_ms is not None and now_ms - end > 1500:
            raise WindowNotReady("Camera stream is stale")
        # Nearest frame to each 2 Hz target. Never repeat a capture to pad the window.
        targets = np.linspace(end - 9500, end, 20)
        times = np.array([f.timestamp_ms for f in frames])
        indices = [int(np.argmin(abs(times - t))) for t in targets]
        if len(set(indices)) != 20:
            raise WindowNotReady("Camera has missing frames; waiting for a complete window")
        selected = [frames[i] for i in indices]
        if selected[-1].timestamp_ms - selected[0].timestamp_ms < 9000:
            raise WindowNotReady("Camera window is too short")
        if max(b.timestamp_ms - a.timestamp_ms for a, b in pairwise(selected)) > 1200:
            raise WindowNotReady("Camera has a gap longer than 1.2 seconds")
        return selected
