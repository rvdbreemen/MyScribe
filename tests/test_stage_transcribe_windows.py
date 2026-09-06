"""Windowed transcription: memory bounded by the window, not by the file.

Found in the wild on 2026-09-02: a 40-minute interview died in faster-whisper's
feature extractor with `Unable to allocate 743 MiB` for a (1, 242298, 201)
complex128 STFT - the whole file's spectrogram in one array, on a machine at
95% commit. That array grows linearly with duration; a three-hour recording
would want 3.4 GB for that one intermediate. The spec's rule "never load an
entire media file into RAM" exists for this, and faster-whisper breaks it on
our behalf when handed a path. So the stage now hands it windows.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from scribe.stages import transcribe

CLIP = Path(__file__).parent / "fixtures" / "clip30.wav"
SR = 16000


def write_wav(path: Path, samples: np.ndarray) -> Path:
    pcm = np.clip(samples * 32767, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return path


def tone(seconds: float, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * 440 * t)).astype(np.float32)


# --- where to cut -----------------------------------------------------------------


def test_quietest_cut_finds_the_dip_in_the_search_span():
    # 10 s of tone with 0.3 s of near-silence at 8.0-8.3 s: the cut lands there.
    audio = tone(10.0)
    audio[int(8.0 * SR) : int(8.3 * SR)] = 0.001

    cut = transcribe.quietest_cut(audio, SR, search_seconds=3.0)

    assert 8.0 * SR <= cut <= 8.3 * SR


def test_quietest_cut_only_looks_back_as_far_as_asked():
    # The only dip is at 2 s; the search span is the last 3 s of 10, so it
    # must not be chosen - the cut still lands somewhere in [7, 10].
    audio = tone(10.0)
    audio[int(2.0 * SR) : int(2.3 * SR)] = 0.0

    cut = transcribe.quietest_cut(audio, SR, search_seconds=3.0)

    assert 7.0 * SR <= cut <= 10.0 * SR


def test_quietest_cut_on_a_short_buffer_returns_its_length():
    audio = tone(0.5)
    assert transcribe.quietest_cut(audio, SR, search_seconds=3.0) == len(audio)


# --- the windows ------------------------------------------------------------------


def test_windows_are_contiguous_and_cover_the_whole_file(tmp_path):
    # 25 s, windows of 10 s with a 2 s search span: three windows, each
    # starting exactly where the last one ended, adding up to the file.
    audio = tone(25.0)
    for at in (9.5, 19.2):  # dips just before each nominal boundary
        audio[int(at * SR) : int((at + 0.2) * SR)] = 0.0
    wav = write_wav(tmp_path / "w.wav", audio)

    windows = list(transcribe.iter_windows(wav, window_seconds=10.0, search_seconds=2.0))

    assert len(windows) == 3
    offsets = [w.offset for w in windows]
    assert offsets[0] == 0.0
    for prev, cur in zip(windows, windows[1:]):
        assert cur.offset == pytest.approx(prev.offset + len(prev.samples) / SR)
    assert sum(len(w.samples) for w in windows) == len(audio)
    assert all(len(w.samples) <= 10.0 * SR for w in windows)
    assert all(w.samples.dtype == np.float32 for w in windows)
    # And the cuts landed in the dips, not mid-tone.
    assert 9.5 <= offsets[1] <= 9.7
    assert 19.2 <= offsets[2] <= 19.4


def test_a_short_tail_is_merged_into_the_last_window(tmp_path):
    # 10.4 s with a 10 s window: a 0.4 s tail on its own would be a window
    # Whisper cannot do anything with, so it rides along with the previous one.
    wav = write_wav(tmp_path / "w.wav", tone(10.4))

    windows = list(transcribe.iter_windows(wav, window_seconds=10.0, search_seconds=0.0))

    assert len(windows) == 1
    assert len(windows[0].samples) == int(10.4 * SR)


def test_a_file_shorter_than_a_window_is_one_window(tmp_path):
    wav = write_wav(tmp_path / "w.wav", tone(3.0))
    windows = list(transcribe.iter_windows(wav, window_seconds=600.0))
    assert len(windows) == 1 and windows[0].offset == 0.0


def test_windows_never_hold_more_than_one_window_of_samples(tmp_path):
    # The whole point. iter_windows is a generator: at any moment it holds the
    # window it is handing out, not the file. Proven by reading a 60 s file
    # through 10 s windows and checking the largest array ever yielded. The
    # bound is the window plus the tail it may absorb (min_tail_seconds) - a
    # constant, whatever the file's length - not the nominal window alone.
    wav = write_wav(tmp_path / "w.wav", tone(60.0, amp=0.1))

    largest = max(
        len(w.samples)
        for w in transcribe.iter_windows(wav, window_seconds=10.0, search_seconds=1.0, min_tail_seconds=1.0)
    )

    assert largest <= (10.0 + 1.0) * SR


# --- offsets in collect_segments --------------------------------------------------


class Seg:
    def __init__(self, start, end, text, words=()):
        self.start, self.end, self.text, self.words = start, end, text, list(words)
        self.avg_logprob = self.no_speech_prob = self.compression_ratio = self.temperature = None


class Word:
    def __init__(self, start, end, word):
        self.start, self.end, self.word, self.probability = start, end, word, 0.9


def test_collect_segments_offsets_times_and_continues_indices():
    seen = []
    segs = [Seg(0.0, 2.0, "a", [Word(0.0, 1.0, " a")]), Seg(2.0, 4.0, "b", [Word(2.0, 3.0, " b")])]

    rows, words = transcribe.collect_segments(
        segs, 100.0, on_progress=seen.append, offset=60.0, idx0=5, word_idx0=17
    )

    assert [r["start"] for r in rows] == [60.0, 62.0]
    assert [r["idx"] for r in rows] == [5, 6]
    assert [w["start"] for w in words] == [60.0, 62.0]
    assert [w["idx"] for w in words] == [17, 18]
    assert [w["segment_idx"] for w in words] == [5, 6]
    # Progress is against the whole media, so the second window does not restart at 0.
    assert seen == [pytest.approx(0.62), pytest.approx(0.64)]


# --- the whole thing, CPU, tiny -----------------------------------------------------


def test_windowed_transcription_matches_a_single_window_on_the_real_clip():
    """clip30.wav through 10 s windows must say what it says in one go.

    The cut points are chosen at silence, so no word straddles one; the text
    is compared as a whole and the timestamps must stay monotonic and end
    near the clip's 30 s, not near 10.
    """
    kw = dict(model_name="tiny", language="en", device="cpu", compute_type="int8", on_progress=lambda p: None)
    _, _, whole = transcribe.transcribe_audio(CLIP, window_seconds=600.0, **kw)
    info, _, windowed = transcribe.transcribe_audio(CLIP, window_seconds=10.0, search_seconds=2.0, **kw)

    # Token equality is the wrong bar. Whisper pads a 10 s window to its 30 s
    # frame and decodes it with different context than the full clip - on
    # tiny/int8 that already flips "80" to "and" at word six, and "see." to
    # "see you" at the end. That is decoder variance, not a lost or doubled
    # word, and this test is about the windowing, not the model. So: the same
    # content (a high similarity ratio over the whole text), the same number
    # of words within a few percent, and nothing out of order.
    import difflib

    whole_words = [w["text"].strip() for w in whole]
    windowed_words = [w["text"].strip() for w in windowed]
    similarity = difflib.SequenceMatcher(
        None, " ".join(whole_words).lower(), " ".join(windowed_words).lower()
    ).ratio()
    assert similarity >= 0.85, f"windowed text drifted: similarity {similarity:.2f}"
    assert abs(len(windowed_words) - len(whole_words)) <= max(2, len(whole_words) // 20)

    starts = [w["start"] for w in windowed]
    assert starts == sorted(starts)
    assert windowed[-1]["end"] > 25.0
    assert info["duration"] == pytest.approx(30.0, abs=0.1)
    # Windows really were used: at least one word sits past the first cut.
    assert any(w["start"] > 10.0 for w in windowed)


def test_the_first_window_pins_the_language_for_the_rest(monkeypatch, tmp_path):
    """Auto-detect runs once. A file that flips language mid-way would
    otherwise be transcribed as two languages, one per window."""
    calls = []

    class Info:
        language, language_probability, duration, duration_after_vad = "nl", 0.9, 10.0, 10.0

    class Model:
        def transcribe(self, audio, **options):
            calls.append(options.get("language"))
            return iter(()), Info()

    monkeypatch.setattr(transcribe, "load_model", lambda name, **kw: (Model(), "cpu", "int8"))
    wav = write_wav(tmp_path / "w.wav", tone(25.0))

    transcribe.transcribe_audio(wav, language=None, window_seconds=10.0, search_seconds=0.0, on_progress=lambda p: None)

    assert calls[0] is None  # detect on the first window
    assert calls[1:] == ["nl", "nl"]  # and hold it for the rest
