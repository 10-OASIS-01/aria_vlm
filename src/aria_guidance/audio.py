"""Aria int32 microphone blocks to WAV; optional local Whisper ASR."""

import io
import threading
import time
import wave
from pathlib import Path

import numpy as np


def pcm32_to_mono(samples, channels: int, gain: float = 1.0) -> np.ndarray:
    if not 1 <= channels <= 16:
        raise ValueError("Audio channel count must be between 1 and 16")
    values = np.asarray(samples, dtype=np.int32)
    if values.size % channels:
        raise ValueError("Interleaved audio length must be a multiple of channel count")
    mono = values.reshape(-1, channels).astype(np.float64).mean(axis=1) / (2**31)
    return np.clip(mono * gain, -1, 1).astype(np.float32)


def mono_to_wav(samples: np.ndarray, rate: int) -> bytes:
    if not 8000 <= rate <= 96000:
        raise ValueError("Invalid audio sample rate")
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            rate, width, channels = wav.getframerate(), wav.getsampwidth(), wav.getnchannels()
            if width != 2 or not 1 <= channels <= 8 or not 8000 <= rate <= 96000:
                raise ValueError("Use 16-bit PCM WAV, 8–96 kHz, with 1–8 channels")
            if not 0.2 <= wav.getnframes() / rate <= 30:
                raise ValueError("Audio duration must be between 0.2 and 30 seconds")
            raw = wav.readframes(wav.getnframes())
            if len(raw) != wav.getnframes() * width * channels:
                raise ValueError("Truncated WAV audio")
            values = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).astype(np.float32)
            return values.mean(axis=1) / 32768.0, rate
    except (wave.Error, EOFError) as e:
        raise ValueError("A valid 16-bit PCM WAV file is required") from e


class SpeechRecognizer:
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = None
        self.lock = threading.Lock()

    def status(self):
        return {
            "configured": bool(self.model_path and Path(self.model_path).is_dir()),
            "loaded": self.model is not None,
            "backend": "faster-whisper",
            "device": "cpu",
        }

    def transcribe(self, data: bytes):
        if not self.status()["configured"]:
            raise RuntimeError("Set ARIA_ASR_MODEL_PATH to a downloaded faster-whisper model")
        samples, rate = read_wav(data)
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError("Speech recognition is already running")
        try:
            from faster_whisper import WhisperModel

            started = time.perf_counter()
            if self.model is None:
                self.model = WhisperModel(
                    self.model_path,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=4,
                    local_files_only=True,
                )
            if rate != 16000:
                # The output length and time positions preserve duration for any Aria rate.
                samples = np.interp(
                    np.arange(round(len(samples) * 16000 / rate)) * rate / 16000,
                    np.arange(len(samples)),
                    samples,
                ).astype(np.float32)
            if float(np.sqrt(np.mean(samples**2))) < 0.002:
                return {"text": "", "language": None, "duration_ms": 0, "silence": True}
            segments, info = self.model.transcribe(samples, beam_size=3, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            return {
                "text": text,
                "language": info.language,
                "silence": not bool(text),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        finally:
            self.lock.release()
