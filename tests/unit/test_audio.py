import sys
from types import SimpleNamespace

import numpy as np
import pytest

from aria_guidance.audio import (
    Endpointer,
    SpeechRecognizer,
    mono_to_wav,
    pcm32_to_mono,
    read_wav,
)


def test_stereo_int32_downmixes_with_gain():
    quarter = 2**29  # 0.25 of full scale
    mono = pcm32_to_mono([quarter, quarter, -quarter, -quarter], channels=2, gain=2.0)
    np.testing.assert_allclose(mono, [0.5, -0.5])


def test_gain_clips_at_full_scale():
    assert pcm32_to_mono([2**30], channels=1, gain=16.0)[0] == 1.0


def test_odd_interleaving_is_rejected():
    with pytest.raises(ValueError, match="multiple of channel count"):
        pcm32_to_mono([1, 2, 3], channels=2)


def test_wav_round_trip():
    samples = np.sin(np.linspace(0, 100, 16000)).astype(np.float32) * 0.5
    decoded, rate = read_wav(mono_to_wav(samples, 16000))
    assert rate == 16000
    np.testing.assert_allclose(decoded.ravel()[: len(samples)], samples, atol=1e-4)


def block(level, seconds=0.1, rate=16000):
    return np.full(round(seconds * rate), level, dtype=np.float32)


def feed(endpointer, *blocks):
    results = [endpointer.feed(b, 16000) for b in blocks]
    return [r for r in results if r is not None]


def test_endpointer_cuts_one_utterance_with_its_pre_roll():
    endpointer = Endpointer()
    quiet = [block(0.001) for _ in range(10)]  # Room noise, below the threshold.
    speech = [block(0.02) for _ in range(5)]
    assert feed(endpointer, *quiet, *speech) == []
    assert endpointer.speaking
    [utterance] = feed(endpointer, *[block(0.001) for _ in range(12)])
    assert not endpointer.speaking
    # 0.3 s of pre-roll (including the first loud block), 0.4 s more speech, 1.2 s of trail.
    assert len(utterance) == pytest.approx(1.9 * 16000, abs=1600)
    assert utterance[0] == pytest.approx(0.001)
    assert utterance.max() == pytest.approx(0.02)


def test_endpointer_drops_a_click_and_keeps_listening():
    endpointer = Endpointer()
    assert feed(endpointer, block(0.5), *[block(0.0) for _ in range(13)]) == []
    assert not endpointer.speaking
    [_] = feed(endpointer, *[block(0.02) for _ in range(3)], *[block(0.0) for _ in range(12)])


def test_endpointer_caps_a_long_utterance_and_starts_the_next():
    endpointer = Endpointer(max_s=1.0)
    [first] = feed(endpointer, *[block(0.02) for _ in range(10)])
    assert len(first) <= 1.1 * 16000
    assert not endpointer.speaking  # The cap closed it.
    feed(endpointer, block(0.02))
    assert endpointer.speaking


def test_endpointer_reset_forgets_speech_in_progress():
    endpointer = Endpointer()
    feed(endpointer, *[block(0.02) for _ in range(5)])
    endpointer.reset()
    assert not endpointer.speaking
    assert feed(endpointer, *[block(0.0) for _ in range(20)]) == []


def test_a_pause_of_a_second_stays_in_one_utterance():
    endpointer = Endpointer()
    speech, pause = [block(0.02) for _ in range(5)], [block(0.0) for _ in range(10)]
    assert feed(endpointer, *speech, *pause, *speech) == []
    [utterance] = feed(endpointer, *[block(0.0) for _ in range(12)])
    assert len(utterance) >= 2.0 * 16000


class FakeWhisper:
    """Records what SpeechRecognizer asks of faster-whisper."""

    def __init__(self, *args, **kwargs):
        pass

    def transcribe(self, samples, **kwargs):
        FakeWhisper.calls.append(kwargs)
        return [], SimpleNamespace(language="en")


@pytest.mark.parametrize("language, expected", [("en", "en"), ("", None)])
def test_recognizer_biases_toward_the_wake_word(tmp_path, monkeypatch, language, expected):
    module = SimpleNamespace(WhisperModel=FakeWhisper)
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    FakeWhisper.calls = []
    wav = mono_to_wav(np.full(16000, 0.1, dtype=np.float32), 16000)
    SpeechRecognizer(str(tmp_path), language).transcribe(wav)
    [call] = FakeWhisper.calls
    assert call["language"] == expected and "Aria" in call["initial_prompt"]
    assert "stop" not in call["initial_prompt"].lower()  # A repeated prompt must not end a trial.
