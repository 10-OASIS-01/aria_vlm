import numpy as np
import pytest

from aria_guidance.audio import mono_to_wav, pcm32_to_mono, read_wav


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
