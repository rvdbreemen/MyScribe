"""The look-ahead must not shout over the window it follows (TASK-036).

faster-whisper extracts features once over everything it is handed and floors
every log-mel bin at `max - 8`. Since TASK-030 a window is handed its own audio
plus 30 s of the next one, so a louder look-ahead raises that floor for the
whole window and the decoder reads different features from frame 0 - measured
on this library in 7 of 156 windows, which is how "Intellivision" became
"television" and "Novell NetWare" became "Novell network".

These tests use faster-whisper's own extractor on synthetic audio: no model, no
GPU. The two frames straddling the junction always differ - they are partly
made of look-ahead samples, whatever their loudness - so every assertion here
stops short of them.
"""

import numpy as np
import pytest
from faster_whisper.feature_extractor import FeatureExtractor

from scribe.stages import loudness

RATE = 16000
FE = FeatureExtractor(feature_size=128)  # large-v3-turbo's filterbank


def _noise(seconds: float, amplitude: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (amplitude * rng.standard_normal(int(seconds * RATE))).astype(np.float32)


def _window(amplitude: float, seed: int) -> np.ndarray:
    """Loud audio and then near-silence, because only bins more than 8 decades
    under the loudest one are floored: flat noise would be clamped nowhere and
    a raised floor would leave it bit-identical for the wrong reason."""
    return np.concatenate([_noise(4.0, amplitude, seed), _noise(4.0, 1e-7, seed + 100)])


def _frames_inside(samples: int) -> int:
    """Frames whose 400-sample STFT support lies inside the first `samples`."""
    return (samples - 400) // 160


def test_a_louder_look_ahead_leaves_the_windows_features_alone():
    window, lookahead = _window(0.05, 1), _noise(3.0, 0.5, 2)

    scaled = loudness.scale_lookahead(FE, window, lookahead)

    alone = FE(window)
    both = FE(np.concatenate([window, scaled]))
    keep = _frames_inside(len(window))
    assert np.array_equal(alone[:, :keep], both[:, :keep])


def test_a_louder_look_ahead_without_the_fix_changes_every_frame():
    """The bug itself, so the test above cannot pass by saying nothing."""
    window, lookahead = _window(0.05, 1), _noise(3.0, 0.5, 2)

    alone = FE(window)
    both = FE(np.concatenate([window, lookahead]))
    keep = _frames_inside(len(window))
    assert not np.array_equal(alone[:, :keep], both[:, :keep])


def test_a_look_ahead_that_cannot_raise_the_floor_is_handed_back_untouched():
    window, lookahead = _window(0.5, 3), _noise(3.0, 0.02, 4)

    assert loudness.scale_lookahead(FE, window, lookahead) is lookahead


def test_no_look_ahead_is_no_work():
    window = _noise(2.0, 0.1, 5)
    empty = np.empty(0, dtype=np.float32)

    assert loudness.scale_lookahead(FE, window, empty) is empty


def test_the_scaled_look_ahead_is_quieter_than_the_window_it_follows():
    window, lookahead = _window(0.05, 6), _noise(3.0, 0.5, 7)

    scaled = loudness.scale_lookahead(FE, window, lookahead)

    assert scaled.dtype == np.float32
    assert loudness.log_mel_max(FE, scaled) <= loudness.log_mel_max(FE, window)
    assert len(scaled) == len(lookahead)


def test_the_window_is_measured_over_the_speech_the_decoder_will_keep(monkeypatch):
    """faster-whisper extracts features from the VAD's speech chunks, so a loud
    bin the VAD drops is not part of the floor. Measuring the window over
    everything would aim at that bin and leave the look-ahead louder than the
    audio the decoder actually reads."""
    kept_from, kept_to = 2 * RATE, 6 * RATE
    window = np.concatenate([_noise(2.0, 0.9, 10), _noise(4.0, 0.02, 11), _noise(2.0, 1e-7, 12)])
    lookahead = _noise(3.0, 0.5, 13)
    # Patched where it is defined: loudness imports it inside the call, so that
    # the web process never pulls a model runtime in (ADR-006).
    monkeypatch.setattr(
        "faster_whisper.vad.get_speech_timestamps",
        lambda audio, *a, **k: [{"start": kept_from, "end": kept_to}],
    )

    scaled = loudness.scale_lookahead(FE, window, lookahead)

    assert loudness.log_mel_max(FE, scaled) <= loudness.log_mel_max(FE, window[kept_from:kept_to])


def test_the_look_ahead_is_no_quieter_than_it_has_to_be():
    """It is there to be heard: a factor that overshoots would hand the decoder
    30 s of near-silence past the cut, which is the hallucination TASK-030
    removed, back by another route."""
    window, lookahead = _window(0.05, 14), _noise(3.0, 0.5, 15)

    scaled = loudness.scale_lookahead(FE, window, lookahead)

    here = loudness.log_mel_max(FE, loudness._speech(window))
    assert here - loudness.log_mel_max(FE, scaled) < 0.01  # decades of mel power


def test_the_scaled_look_ahead_stays_under_the_window_not_level_with_it():
    """A tie in float32 is a floor that may or may not move; MARGIN keeps it
    on the safe side of the comparison."""
    window, lookahead = _window(0.05, 16), _noise(3.0, 0.5, 17)

    scaled = loudness.scale_lookahead(FE, window, lookahead)

    assert loudness.log_mel_max(FE, scaled) < loudness.log_mel_max(FE, loudness._speech(window))


@pytest.mark.parametrize("amplitude", [0.02, 0.2, 1.0])
def test_the_floor_holds_whatever_the_look_ahead_says(amplitude):
    window, lookahead = _window(0.1, 8), _noise(3.0, amplitude, 9)

    both = FE(np.concatenate([window, loudness.scale_lookahead(FE, window, lookahead)]))
    keep = _frames_inside(len(window))
    assert np.array_equal(FE(window)[:, :keep], both[:, :keep])


# --- one VAD pass, two uses (TASK-056) --------------------------------------------------


def test_speech_measured_once_gives_the_same_look_ahead():
    """The transcribe stage counts a window's speech from the chunks it hands
    in here, so the scaling must not come out different for having been handed
    them rather than finding them itself."""
    window, lookahead = _window(0.05, 18), _noise(3.0, 0.5, 19)

    chunks = loudness.speech_chunks(window)

    assert np.array_equal(
        loudness.scale_lookahead(FE, window, lookahead, chunks=chunks),
        loudness.scale_lookahead(FE, window, lookahead),
    )


def test_speech_seconds_add_up_the_chunks_and_nothing_else():
    chunks = [{"start": 0, "end": RATE}, {"start": 2 * RATE, "end": 2 * RATE + RATE // 2}]

    assert loudness.speech_seconds(chunks) == 1.5
    assert loudness.speech_seconds([]) == 0.0
