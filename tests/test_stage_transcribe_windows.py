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

    windows = list(
        transcribe.iter_windows(wav, window_seconds=10.0, search_seconds=1.0, min_tail_seconds=1.0)
    )
    largest = max(len(w.samples) for w in windows)

    assert largest <= (10.0 + 1.0) * SR
    # The look past the cut is bounded too: never more than half a window,
    # whatever lookahead was asked for (the default is 30 s, here 5 s).
    assert max(len(w.lookahead) for w in windows) <= 0.5 * 10.0 * SR


# --- hearing past the cut ----------------------------------------------------------
#
# Found in the library on 2026-09-10: 7 of 50 Hacker History episodes carried
# a run of ~100 identical words ("um, um, um", "uh, uh", "I, I") squeezed into
# 0.3 s, and every one sat just before a multiple of 600 s - WINDOW_SECONDS.
# Each cut is, to Whisper, the end of the file, and the end of a file is where
# Whisper hallucinates: a filler loop, "Thank you.", the hotword prompt read
# back. Decoding the same windows again (media 46 window 0, media 28 window 1)
# gave end-of-window garbage every time, in a different shape each run;
# decoding them with 30 s of the next window appended and everything past the
# cut dropped gave clean tails.


def _dipped_25s(tmp_path) -> Path:
    audio = tone(25.0)
    for at in (9.5, 19.2):
        audio[int(at * SR) : int((at + 0.2) * SR)] = 0.0
    return write_wav(tmp_path / "w.wav", audio)


def test_each_window_carries_the_audio_just_past_its_cut(tmp_path):
    wav = _dipped_25s(tmp_path)

    windows = list(
        transcribe.iter_windows(wav, window_seconds=10.0, search_seconds=2.0, lookahead_seconds=3.0)
    )

    assert len(windows) == 3
    for cur, nxt in zip(windows, windows[1:]):
        assert len(cur.lookahead) == 3.0 * SR
        np.testing.assert_array_equal(cur.lookahead, nxt.samples[: len(cur.lookahead)])
    assert len(windows[-1].lookahead) == 0  # the real end of the file needs no look past it
    # The windows themselves are what they were: contiguous and adding up to the file.
    assert sum(len(w.samples) for w in windows) == int(25.0 * SR)


def test_collect_segments_stops_at_the_cut_without_asking_for_more():
    """Past the limit nothing is kept, and the generator is not asked for the
    next segment - asking is what makes faster-whisper decode the next 30 s,
    and that would be the look-ahead costing a second decode of audio that is
    about to be thrown away."""
    pulled = []
    seen = []

    def decoded():
        for seg in (
            Seg(0.0, 4.0, "a b", [Word(0.0, 1.0, " a"), Word(2.0, 3.0, " b")]),
            Seg(
                8.0, 12.0, "c x y d",
                [Word(8.0, 9.0, " c"), Word(9.6, 10.2, " x"), Word(9.9, 10.5, " y"), Word(10.6, 11.0, " d")],
            ),
            Seg(12.0, 14.0, "e", [Word(12.0, 13.0, " e")]),
            Seg(14.0, 16.0, "f", [Word(14.0, 15.0, " f")]),
        ):
            pulled.append(seg.text)
            yield seg

    rows, words = transcribe.collect_segments(
        decoded(), 100.0, on_progress=seen.append, limit=10.0
    )

    # The cut runs through "x" and "y": each goes to the side that heard most
    # of it - "x" (midpoint 9.9) is this window's, "y" (10.2) the next one's.
    assert [w["text"] for w in words] == [" a", " b", " c", " x"]
    # The segment the cut runs through keeps its words before the cut, and
    # its end and text say so rather than describing words that were dropped.
    assert (rows[-1]["end"], rows[-1]["text"]) == (10.2, "c x")
    assert pulled == ["a b", "c x y d", "e"]  # "f" was never decoded
    assert max(seen) <= 0.10  # progress never ran ahead of the cut


def test_the_decoder_hears_past_the_cut_and_what_it_says_there_is_dropped(monkeypatch, tmp_path):
    calls: list[int] = []

    class Info:
        language, language_probability, duration, duration_after_vad = "en", 0.9, 10.0, 10.0

    class Model:
        def transcribe(self, audio, **options):
            call = len(calls)
            calls.append(len(audio))
            # One word a second across everything it was handed, named after
            # the call it came from.
            return iter(
                Seg(float(t), t + 0.5, f"c{call}", [Word(float(t), t + 0.5, f" c{call}")])
                for t in range(int(len(audio) / SR))
            ), Info()

    monkeypatch.setattr(transcribe, "load_model", lambda name, **kw: (Model(), "cpu", "int8"))
    wav = _dipped_25s(tmp_path)
    shape = dict(window_seconds=10.0, search_seconds=2.0, lookahead_seconds=3.0)
    windows = list(transcribe.iter_windows(wav, **shape))

    _info, _segments, words = transcribe.transcribe_audio(
        wav, language="en", on_progress=lambda p: None, **shape
    )

    assert calls == [len(w.samples) + len(w.lookahead) for w in windows]
    for i, window in enumerate(windows):
        cut = window.offset + len(window.samples) / SR
        from_this_call = [w["start"] for w in words if w["text"] == f" c{i}"]
        assert from_this_call, f"window {i} contributed nothing"
        assert max(from_this_call) < cut  # nothing said past the cut survives
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)


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
    # Compared word by word, not character by character. On a string longer
    # than 200 characters difflib's autojunk heuristic treats every character
    # that is more than 1% of the text - the space, e, a, o - as junk, and the
    # ratio then swings on where a single word moved: the look-ahead change
    # (2026-09-10) scored 0.55 against 0.88 before it while being closer to
    # the single-window text by every honest measure - word-level 0.928 vs
    # 0.921, characters without autojunk 0.974 vs 0.979. A list of ~75 words
    # is far below autojunk's threshold.
    similarity = difflib.SequenceMatcher(
        None, [w.lower() for w in whole_words], [w.lower() for w in windowed_words]
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
