"""TASK-032: a second decode of the stretches where the first failed by its own measure.

Measured on the library 2026-09-11 (31 flagged stretches, 21 recordings, each
decoded again from two clips that start at different points before it):
loops of five or more with their words squeezed into no time, and segments
that failed the compression check at every temperature, collapsed or cleared
in both second opinions; real repetitions - media 20's "no, no, no, no, no,
wait" - came back in both and stay. These tests hold the rule to that: flag
narrowly, replace only when both opinions agree, keep the segment text the
join of its words, and say what happened.
"""

from __future__ import annotations

import wave
from types import SimpleNamespace

import numpy as np
import pytest

from scribe.stages import second_opinion, transcribe

THRESHOLD = transcribe.COMPRESSION_RATIO_THRESHOLD


def seg(idx, start, end, words, compression=1.5, logprob=-0.2):
    text = "".join(w["text"] for w in words).strip()
    for w in words:
        w["segment_idx"] = idx
    return {
        "idx": idx, "start": start, "end": end, "text": text, "avg_logprob": logprob,
        "no_speech_prob": 0.01, "compression_ratio": compression, "temperature": 0.0,
    }


def spoken(start, text, *, step=0.4, dur=0.3, probability=0.9):
    """Words said at a human pace: one every `step` seconds."""
    return [
        {"start": start + i * step, "end": start + i * step + dur, "text": f" {t}", "probability": probability}
        for i, t in enumerate(text.split())
    ]


def squeezed(start, text):
    """Words the decoder squeezed into no time: 10 ms each, back to back."""
    return [
        {"start": start + i * 0.01, "end": start + i * 0.01 + 0.01, "text": f" {t}", "probability": 0.8}
        for i, t in enumerate(text.split())
    ]


def transcript(*parts):
    """Segments and words from (start, end, words, compression) parts, indexed as the stage does."""
    segments, words = [], []
    for i, (start, end, ws, compression) in enumerate(parts):
        segments.append(seg(i, start, end, ws, compression))
        words.extend(ws)
    for i, w in enumerate(words):
        w["idx"] = i
    return segments, words


def looping():
    """Media 14's shape: a long segment with a loop inside that never trips the threshold."""
    return transcript(
        (20.0, 28.0, spoken(20.0, "what we now call red teaming work"), 1.6),
        (40.0, 60.0, spoken(40.0, "and so") + squeezed(41.0, "we were " * 8)
         + spoken(42.0, "some of the very first to offer that"), 1.9),
        (80.0, 88.0, spoken(80.0, "you know NSA had their red team"), 1.5),
    )


class Opinions:
    """Stands in for the model: each call to `decode` answers with the next opinion.

    An opinion is a list of (start, end, text, compression) segments in file
    time; it is cut to the clip it was asked for and handed back as rows, the
    way transcribe's collect_segments would - clip-relative limit included.
    """

    def __init__(self, *opinions):
        self.opinions = list(opinions)
        self.clips: list[tuple[float, float]] = []

    def read_clip(self, start, end):
        self.clips.append((start, end))
        return np.zeros(int((end - start) * transcribe.SAMPLE_RATE), dtype=np.float32)

    def decode(self, audio, offset, limit):
        opinion = self.opinions[len(self.clips) - 1]
        segments, words = [], []
        for start, end, text, compression, probability in opinion:
            ws = [w for w in spoken(start, text, probability=probability)
                  if limit is None or (w["start"] + w["end"]) / 2 - offset < limit]
            if not ws:
                continue
            segments.append(seg(len(segments), start, end, ws, compression))
            words.extend(ws)
        for i, w in enumerate(words):
            w["idx"] = i
        return segments, words


def clean_opinion(probability=0.9, text="we were some of the very first to offer that"):
    return [(40.0, 60.0, "and so " + text, 1.7, probability)]


def review(segments, words, opinions, duration=100.0):
    return second_opinion.review(
        segments, words, read_clip=opinions.read_clip, decode=opinions.decode,
        duration=duration, threshold=THRESHOLD, cancelled=lambda: None,
    )


def text(words):
    return "".join(w["text"] for w in words).strip()


# --- what gets a second opinion ---------------------------------------------------


def test_a_loop_squeezed_into_no_time_is_flagged():
    segments, words = looping()

    (flag,) = second_opinion.find_flags(segments, words, threshold=THRESHOLD)

    assert flag.kinds == ("loop",)
    assert (flag.phrase, flag.repeats) == ("we were", 8)
    assert 41.0 <= flag.start < flag.end <= 41.2


def test_a_repetition_said_at_a_human_pace_is_not_flagged():
    """"no, no, no, no, no" over two seconds is somebody talking."""
    segments, words = transcript((0.0, 10.0, spoken(0.0, "they say no no no no no wait"), 1.8))

    assert second_opinion.find_flags(segments, words, threshold=THRESHOLD) == []


def test_a_short_loop_is_left_alone():
    """Three-fold was a coin flip in the measurement ("blah, blah, blah" came
    back as two), so it is not the second opinion's to judge."""
    segments, words = transcript((0.0, 10.0, spoken(0.0, "my") + squeezed(1.0, "um um um")
                                  + spoken(2.0, "partner at the time"), 1.8))

    assert second_opinion.find_flags(segments, words, threshold=THRESHOLD) == []


def test_a_segment_that_failed_every_temperature_is_flagged():
    segments, words = transcript(
        (0.0, 8.0, spoken(0.0, "until next time keep hacking"), 1.4),
        (8.0, 12.0, spoken(8.0, "Thank you. Thank you."), 9.0),
    )

    (flag,) = second_opinion.find_flags(segments, words, threshold=THRESHOLD)

    assert flag.kinds == ("failed",)
    assert (flag.start, flag.end, flag.compression) == (8.0, 12.0, 9.0)


# --- when both opinions agree the loop was not said --------------------------------


def test_the_loop_is_replaced_by_what_both_second_opinions_heard():
    segments, words = looping()
    opinions = Opinions(clean_opinion(), clean_opinion())

    segments, words, records = review(segments, words, opinions)

    assert text(words) == (
        "what we now call red teaming work and so we were some of the very first"
        " to offer that you know NSA had their red team"
    )
    (record,) = records
    assert record["outcome"] == "replaced"
    assert "we were we were we were" in record["before"]
    assert record["after"] == "and so we were some of the very first to offer that"


def test_the_repaired_transcript_keeps_the_stage_invariants():
    """Indices run 0..n, every segment's text is the join of its words, and
    words and segments stay in time order - what _persist and FTS expect."""
    segments, words = looping()

    segments, words, _ = review(segments, words, Opinions(clean_opinion(), clean_opinion()))

    assert [s["idx"] for s in segments] == list(range(len(segments)))
    assert [w["idx"] for w in words] == list(range(len(words)))
    for s in segments:
        own = [w for w in words if w["segment_idx"] == s["idx"]]
        assert own and "".join(w["text"] for w in own).strip() == s["text"]
    assert [w["start"] for w in words] == sorted(w["start"] for w in words)
    assert [s["start"] for s in segments] == sorted(s["start"] for s in segments)


def test_the_opinion_with_the_surer_words_is_the_one_used():
    """Media 19: one opinion heard "I do identity work", the other "I do my
    daddy work". Both lost the loop; the one the decoder was surer of wins."""
    segments, words = looping()
    opinions = Opinions(
        clean_opinion(probability=0.6, text="we were the first"),
        clean_opinion(probability=0.95, text="we were some of the very first"),
    )

    _, words, _ = review(segments, words, opinions)

    assert "we were some of the very first" in text(words)
    assert "we were the first" not in text(words)


def test_a_hallucinated_tail_both_opinions_hear_nothing_in_is_removed():
    """Media 32: "Hacker History is a production of Hacker History. Thank
    you. Thank you." over the outro music; decoded again, nothing is there."""
    segments, words = transcript(
        (0.0, 8.0, spoken(0.0, "until next time keep hacking"), 1.4),
        (8.0, 12.0, spoken(8.0, "Thank you. Thank you."), 9.0),
    )
    nothing_after = [(0.0, 8.0, "until next time keep hacking", 1.4, 0.9)]

    segments, words, records = review(segments, words, Opinions(nothing_after, nothing_after), duration=12.0)

    assert text(words) == "until next time keep hacking"
    assert [s["text"] for s in segments] == ["until next time keep hacking"]
    assert records[0]["outcome"] == "replaced" and records[0]["after"] == ""


# --- when they do not ---------------------------------------------------------------


def test_a_repetition_both_opinions_hear_again_is_kept():
    """Squeezed timestamps, but decoded again the speaker still says it four
    times: it was said, the stored text stays."""
    segments, words = looping()
    again = [(40.0, 60.0, "and so we were we were we were we were some of the very first", 1.9, 0.9)]
    before = text(words)

    segments, words, records = review(segments, words, Opinions(again, again))

    assert text(words) == before
    assert records[0]["outcome"].startswith("kept")


def test_one_dissenting_opinion_keeps_what_was_stored():
    segments, words = looping()
    still_failing = [(40.0, 60.0, "and so we were some of the very first", 3.1, 0.9)]
    before = text(words)

    _, words, records = review(segments, words, Opinions(clean_opinion(), still_failing))

    assert text(words) == before
    assert records[0]["outcome"].startswith("kept")


# --- what the decoder is given ------------------------------------------------------


def test_each_opinion_hears_the_stretch_with_context_either_side():
    """Two clips, starting 20 s and 7 s before the stretch, so each arrives
    with different context; both run 15 s past it, because the end of a clip
    is, to Whisper, the end of the file."""
    segments, words = looping()
    opinions = Opinions(clean_opinion(), clean_opinion())

    review(segments, words, opinions)

    # The stretch is the whole segment the loop sits in, bounded at the middle
    # of the pause either side: from the last word before it to its first
    # word, and from its last word to the first word after it.
    before = spoken(20.0, "what we now call red teaming work")[-1]["end"]
    r0 = (before + 40.0) / 2
    last = spoken(42.0, "some of the very first to offer that")[-1]["end"]
    r1 = (last + 80.0) / 2
    assert opinions.clips == [
        (pytest.approx(r0 - 20.0), pytest.approx(r1 + 15.0)),
        (pytest.approx(r0 - 7.0), pytest.approx(r1 + 15.0)),
    ]


def test_a_first_opinion_that_hears_the_loop_too_saves_the_second_decode():
    segments, words = looping()
    again = [(40.0, 60.0, "and so we were we were we were some of the very first", 1.9, 0.9)]
    opinions = Opinions(again, clean_opinion())

    review(segments, words, opinions)

    assert len(opinions.clips) == 1


# --- in the stage ---------------------------------------------------------------------


def _as_whisper(start, end, words, compression=1.5):
    """Rows in faster-whisper's shape, times relative to the audio it was handed."""
    return SimpleNamespace(
        start=start, end=end, text="".join(w["text"] for w in words), avg_logprob=-0.2,
        no_speech_prob=0.01, compression_ratio=compression, temperature=0.0,
        words=[SimpleNamespace(start=w["start"], end=w["end"], word=w["text"],
                               probability=w["probability"]) for w in words],
    )


def test_transcribe_audio_gives_a_loop_a_second_opinion_and_says_so(monkeypatch, tmp_path):
    """The window decode loops; both second opinions hear plain speech there
    (a word every half second, all through the clip). The loop is gone from
    what the stage returns, the opinions were asked with the window's own
    parameters, and info carries the record."""
    wav = tmp_path / "w.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(transcribe.SAMPLE_RATE)
        w.writeframes(np.zeros(100 * transcribe.SAMPLE_RATE, dtype="<i2").tobytes())
    calls = []
    info = SimpleNamespace(language="en", language_probability=0.9, duration=100.0, duration_after_vad=100.0)
    first_segments, _ = looping()

    class Model:
        def transcribe(self, audio, **options):
            calls.append((len(audio) / transcribe.SAMPLE_RATE, options))
            if len(calls) == 1:
                return iter(
                    _as_whisper(s["start"], s["end"], [w for w in looping()[1] if w["segment_idx"] == s["idx"]])
                    for s in first_segments
                ), info
            length = len(audio) / transcribe.SAMPLE_RATE
            plain = [{"start": t, "end": t + 0.3, "text": " ok", "probability": 0.9}
                     for t in np.arange(0.25, length - 0.5, 0.5)]
            return iter([_as_whisper(0.0, length, plain)]), info

    monkeypatch.setattr(transcribe, "load_model", lambda name, **kw: (Model(), "cpu", "int8"))

    got, _segments, words = transcribe.transcribe_audio(wav, language="en", on_progress=lambda p: None)

    said = "".join(w["text"] for w in words)
    assert "we were we were" not in said
    assert " ok ok ok" in said
    assert len(calls) == 3  # the window, then one decode per opinion
    assert calls[1][1] == calls[0][1] == calls[2][1]
    (record,) = got["second_opinions"]
    assert (record["flag"], record["phrase"], record["repeats"], record["outcome"]) == (
        "loop", "we were", 8, "replaced")
