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


# Keeps "Aria" at the start of short clips, where hotwords lost it. No closing word here: if Whisper
# ever repeats its prompt into noise, that must not end a trial.
WAKE_PROMPT = "Aria, next. Aria, done."


class Endpointer:
    """Cuts a continuous microphone stream into utterances by loudness."""

    def __init__(
        self, threshold=0.006, pre_roll_s=0.3, silence_s=1.2, max_s=20.0, min_voiced_s=0.15
    ):
        """Args:
        threshold: block RMS that counts as speech, after gain.
            shortcut: fixed value, move to a noise-tracking floor if rooms vary.
        pre_roll_s: audio kept from before the speech, so the first word is not clipped.
        silence_s: trailing quiet that ends an utterance. A goal's mid-sentence pause runs about
            1 s, and 0.8 and 1.0 both split one.
        max_s: longest utterance; speech beyond it starts the next one.
        min_voiced_s: loud time below this is a click, not speech, and is dropped.
        """
        self.threshold, self.pre_roll_s, self.silence_s = threshold, pre_roll_s, silence_s
        self.max_s, self.min_voiced_s = max_s, min_voiced_s
        self.rate = 0
        self.reset()

    def reset(self):
        self.speaking = False
        self.pre_roll, self.chunks = [], []
        self.pre_samples = self.samples = self.voiced = self.quiet = 0

    def finish(self):
        utterance = np.concatenate(self.chunks)
        voiced = self.voiced
        self.reset()
        return utterance if voiced >= self.min_voiced_s * self.rate else None

    def feed(self, samples, rate):
        """Takes one mono block; returns a finished utterance (N,) at this rate, or None."""
        if rate != self.rate:
            self.reset()
            self.rate = rate
        loud = len(samples) > 0 and float(np.sqrt(np.mean(samples**2))) >= self.threshold
        if not self.speaking:
            self.pre_roll.append(samples)
            self.pre_samples += len(samples)
            while self.pre_samples - len(self.pre_roll[0]) >= self.pre_roll_s * rate:
                self.pre_samples -= len(self.pre_roll.pop(0))
            if not loud:
                return None
            self.speaking = True
            self.chunks, self.samples = self.pre_roll, self.pre_samples
            self.pre_roll, self.pre_samples = [], 0
            self.voiced = len(samples)
            return None
        self.chunks.append(samples)
        self.samples += len(samples)
        if loud:
            self.voiced += len(samples)
            self.quiet = 0
        else:
            self.quiet += len(samples)
        if self.quiet >= self.silence_s * rate or self.samples >= self.max_s * rate:
            return self.finish()
        return None


class SpeechRecognizer:
    def __init__(self, model_path: str, language: str = "en"):
        """Args:
        model_path: a downloaded faster-whisper model.
        language: speech language, or "" to auto-detect.
        """
        self.model_path = model_path
        self.language = language or None
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
            segments, info = self.model.transcribe(
                samples,
                beam_size=3,
                vad_filter=True,
                language=self.language,
                initial_prompt=WAKE_PROMPT,
            )
            text = " ".join(s.text.strip() for s in segments).strip()
            return {
                "text": text,
                "language": info.language,
                "silence": not bool(text),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
        finally:
            self.lock.release()
