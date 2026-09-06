"""Fitting a transcript in a context window (Phase 5 Task 3).

The chunker is pure: a `TranscriptDoc` in, a list of `Chunk` out, no
connection, no network, no clock. So these tests are arithmetic, and they are
written against the properties the map-reduce in Task 4 will actually lean on
rather than against the shape of one example:

* **nothing is lost** - the union of the chunks' segment ids is every segment
  the document has, whatever the overlap;
* **nothing is invented** - a chunk starts at a segment start and ends at a
  segment end, never mid-sentence;
* **the estimate is additive** - the accumulator inside `plan` is the sum of
  the per-segment estimates, and that only equals the chunk's own estimate
  because `estimate_tokens` counts whitespace-separated words. It is asserted
  here rather than assumed, because everything in `plan` rests on it;
* **the plan is deterministic and its indexes are gapless** - Task 4 stores
  each chunk's answer under `kind:chunk:{i}` and resumes by looking for the
  missing ones, which silently misaligns if the same doc ever plans
  differently or the indexes ever start at one.

Sizes are chosen so the token arithmetic is checkable by hand: the test
segments are built from six-character words (`word00`), which cost exactly two
tokens each at three characters per token, and each line carries a `[m:ss]`
prefix that is six characters and therefore two more.
"""

from __future__ import annotations

import dataclasses

import pytest

from scribe import db, render
from scribe.exports import doc as docs
from scribe.llm import chunking
from tests.seed import seed_media, seed_run

# --- documents to plan over -------------------------------------------------------

SEGMENT_SECONDS = 10.0
GAP_SECONDS = 0.5

WORD = "word00"  # six characters: two tokens at three characters per token
LINE_OVERHEAD = 2  # the "[m:ss] " prefix, also six characters


def cost_of(words_per_segment: int) -> int:
    """What one segment of `words_per_segment` words costs, by hand."""
    return LINE_OVERHEAD + 2 * words_per_segment


def make_doc(sizes: list[int], *, title: str = "Clip") -> docs.TranscriptDoc:
    """A document whose segments hold `sizes[i]` words each.

    Built directly rather than through the database: these tests need a dozen
    segments of controlled size, and `TranscriptDoc` is a frozen dataclass of
    plain rows. One test below does go through sqlite, to prove the real row
    shape works.
    """
    segments = []
    t = 0.0
    for i, size in enumerate(sizes):
        segments.append(
            {
                "idx": i,
                "start": t,
                "end": t + SEGMENT_SECONDS,
                "text": " ".join([WORD] * size),
            }
        )
        t += SEGMENT_SECONDS + GAP_SECONDS
    end = segments[-1]["end"] if segments else 0.0
    return docs.TranscriptDoc(
        media={"id": 1, "title": title},
        run={"id": 1},
        words=(),
        segments=tuple(segments),
        labels={},
        duration=end + 60.0,  # trailing silence: the transcript ends before the media
        title=title,
    )


TEN_WORDS = 22  # cost_of(10): what every segment costs in the split tests
BUDGET = 100  # room for four such segments (88), never five (110)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


# --- the estimate ------------------------------------------------------------------


def test_estimate_tokens_is_pessimistic_at_three_characters_a_token():
    assert chunking.estimate_tokens("") == 0
    assert chunking.estimate_tokens("hello world") == 4  # two two-token words
    assert chunking.estimate_tokens("a") == 1  # never zero for a real word
    assert chunking.estimate_tokens("supercalifragilistic") == 7  # 20 chars, rounded up
    assert cost_of(10) == TEN_WORDS


def test_estimate_tokens_is_additive_over_joined_lines():
    """`plan` accumulates per-segment costs and calls the sum the chunk's
    estimate. That is only true because the estimate counts whitespace-separated
    words, so joining with a newline cannot merge or split one."""
    lines = ["[0:00] " + " ".join([WORD] * 3), "[0:10] " + " ".join([WORD] * 7)]
    parts = [chunking.estimate_tokens(line) for line in lines]

    assert sum(parts) == chunking.estimate_tokens("\n".join(lines))


# --- one chunk, or many ------------------------------------------------------------


def test_a_doc_under_budget_is_one_chunk_holding_the_whole_transcript():
    doc = make_doc([10, 10, 10])
    chunks = chunking.plan(doc, budget_tokens=BUDGET)

    assert len(chunks) == 1
    assert chunks[0].index == 0
    assert chunks[0].text == chunking.transcript_text(doc)
    assert chunks[0].segment_ids == (0, 1, 2)
    assert chunks[0].start == doc.segments[0]["start"]
    assert chunks[0].end == doc.segments[-1]["end"]
    assert chunks[0].tokens == 3 * TEN_WORDS
    assert chunks[0].oversized is False


def test_a_chunk_cannot_be_edited_after_it_is_planned():
    """Same convention as `TranscriptDoc`: two callers handed the same plan see
    the same plan."""
    chunk = chunking.plan(make_doc([10]), budget_tokens=BUDGET)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        chunk.text = "something else"


def test_a_doc_over_budget_splits_into_chunks_that_each_fit():
    doc = make_doc([10] * 12)
    chunks = chunking.plan(doc, budget_tokens=BUDGET)

    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk.oversized is False
        assert chunk.tokens <= BUDGET
        assert chunking.estimate_tokens(chunk.text) <= BUDGET
        assert chunk.tokens == chunking.estimate_tokens(chunk.text)


def test_every_chunk_starts_at_a_segment_start_and_ends_at_a_segment_end():
    """Never mid-sentence: a chunk boundary is a boundary Whisper already drew."""
    doc = make_doc([10] * 12)
    starts = {s["start"] for s in doc.segments}
    ends = {s["end"] for s in doc.segments}

    for chunk in chunking.plan(doc, budget_tokens=BUDGET):
        assert chunk.start in starts
        assert chunk.end in ends
        assert chunk.text.startswith(f"[{render.format_ts(chunk.start)}] ")
        assert chunk.text.endswith(WORD)  # a whole segment's text, not a prefix of one


def test_chunk_text_is_timestamped_so_the_model_can_cite_times():
    doc = make_doc([2] * 7)  # ten seconds and a half apart: 0:00, 0:10, 0:21, ...
    text = chunking.plan(doc, budget_tokens=BUDGET)[0].text

    lines = text.split("\n")
    assert lines[0] == f"[0:00] {WORD} {WORD}"
    assert lines[1] == f"[0:10] {WORD} {WORD}"
    assert lines[6].startswith("[1:03] ")  # past a minute, still m:ss


def test_chunk_indexes_are_zero_based_and_gapless():
    """Task 4 stores chunk i under `kind:chunk:{i}` and resumes by asking which
    are missing. A one-based or gapped index misaligns that silently."""
    chunks = chunking.plan(make_doc([10] * 12), budget_tokens=BUDGET)

    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_planning_the_same_doc_twice_gives_the_same_boundaries():
    """Resume rests on this as much as on the indexes: chunk 3 of the resumed
    run has to be the chunk 3 whose answer is already stored."""
    doc = make_doc([10] * 12)
    first = chunking.plan(doc, budget_tokens=BUDGET)
    second = chunking.plan(doc, budget_tokens=BUDGET)

    assert [c.segment_ids for c in first] == [c.segment_ids for c in second]
    assert [c.text for c in first] == [c.text for c in second]


# --- overlap and coverage -----------------------------------------------------------


@pytest.mark.parametrize("overlap", [1, 2, 3])
def test_overlap_repeats_exactly_the_last_n_segments(overlap):
    doc = make_doc([10] * 12)
    chunks = chunking.plan(doc, budget_tokens=BUDGET, overlap_segments=overlap)

    assert len(chunks) > 1
    for before, after in zip(chunks, chunks[1:]):
        assert after.segment_ids[:overlap] == before.segment_ids[-overlap:]


def test_time_ranges_are_contiguous_and_cover_the_whole_transcript():
    doc = make_doc([10] * 12)
    chunks = chunking.plan(doc, budget_tokens=BUDGET)

    assert chunks[0].start == doc.segments[0]["start"]
    assert chunks[-1].end == doc.segments[-1]["end"]
    for before, after in zip(chunks, chunks[1:]):
        # The default overlap of one segment makes the ranges actually overlap;
        # what matters is that no second of speech falls between two chunks.
        assert after.start <= before.end
        assert after.start > before.start


def test_with_no_overlap_the_chunks_still_leave_no_segment_out():
    """Without overlap the wall-clock ranges have the silences between segments
    in them, so contiguity is a statement about segments rather than seconds:
    the chunks are consecutive runs that partition the transcript."""
    doc = make_doc([10] * 12)
    chunks = chunking.plan(doc, budget_tokens=BUDGET, overlap_segments=0)

    ids = [i for chunk in chunks for i in chunk.segment_ids]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))  # nothing sent to the model twice
    assert set(ids) == {s["idx"] for s in doc.segments}


@pytest.mark.parametrize("overlap", [0, 1, 2, 5])
def test_every_segment_reaches_at_least_one_chunk(overlap):
    doc = make_doc([10] * 12)
    chunks = chunking.plan(doc, budget_tokens=BUDGET, overlap_segments=overlap)

    covered = {i for chunk in chunks for i in chunk.segment_ids}
    assert covered == {s["idx"] for s in doc.segments}


def test_an_overlap_wider_than_a_chunk_still_advances():
    """The infinite loop this guard exists for: a chunk of four segments with an
    overlap of five would start the next chunk at or before its own start, and
    `plan` would never reach the end of the transcript."""
    doc = make_doc([10] * 12)
    chunks = chunking.plan(doc, budget_tokens=BUDGET, overlap_segments=5)

    assert [c.segment_ids[0] for c in chunks] == sorted({c.segment_ids[0] for c in chunks})
    assert chunks[-1].segment_ids[-1] == doc.segments[-1]["idx"]
    for before, after in zip(chunks, chunks[1:]):
        assert after.segment_ids[0] > before.segment_ids[0]


# --- the segment that does not fit ---------------------------------------------------


def test_a_segment_bigger_than_the_budget_is_its_own_oversized_chunk():
    """A caller cannot split a segment without cutting mid-sentence, and raising
    would mean one runaway Whisper segment makes a whole transcript
    unsummarisable. So the chunk is emitted, marked, and the provider gets to
    say whether it fits - `oversized` is what Task 4 branches on."""
    doc = make_doc([5, 200, 5])
    chunks = chunking.plan(doc, budget_tokens=BUDGET)

    assert [c.segment_ids for c in chunks] == [(0,), (1,), (2,)]
    assert [c.oversized for c in chunks] == [False, True, False]
    assert chunks[1].tokens > BUDGET
    assert chunks[1].tokens == chunking.estimate_tokens(chunks[1].text)


def test_an_oversized_segment_does_not_drag_its_neighbours_over_budget():
    doc = make_doc([10, 200, 10, 10])
    chunks = chunking.plan(doc, budget_tokens=BUDGET)

    for chunk in chunks:
        assert chunk.tokens <= BUDGET or chunk.oversized


# --- needs_chunking, and its agreement with plan ---------------------------------------


def test_needs_chunking_answers_for_the_whole_timestamped_transcript():
    doc = make_doc([10] * 12)
    assert chunking.needs_chunking(doc, BUDGET) is True
    assert chunking.needs_chunking(doc, 12 * TEN_WORDS) is False  # exactly fits
    assert chunking.needs_chunking(doc, 12 * TEN_WORDS - 1) is True


def test_a_doc_that_does_not_need_chunking_plans_as_one_whole_chunk():
    """The direction a caller relies on: `needs_chunking` says no, so it sends
    the transcript in one call. Two implementations of "does it fit" that
    disagree would mean a caller skipping the map-reduce and then overflowing."""
    for sizes in ([10], [10] * 4, [1] * 40, [3, 9, 27]):
        doc = make_doc(sizes)
        if chunking.needs_chunking(doc, BUDGET):
            continue
        chunks = chunking.plan(doc, budget_tokens=BUDGET)
        assert len(chunks) == 1, sizes
        assert chunks[0].oversized is False
        assert chunks[0].text == chunking.transcript_text(doc)


# --- edges ------------------------------------------------------------------------------


def test_a_transcript_with_no_segments_plans_nothing():
    """An empty plan rather than one empty chunk: there is no point paying a
    model to summarise "", and a caller can say "no transcript yet" from a plan
    of length zero."""
    doc = make_doc([])

    assert chunking.transcript_text(doc) == ""
    assert chunking.needs_chunking(doc, BUDGET) is False
    assert chunking.plan(doc, budget_tokens=BUDGET) == []


@pytest.mark.parametrize("budget", [0, -1])
def test_a_budget_of_nothing_is_refused_rather_than_chunked_to_death(budget):
    """Zero would mark every segment oversized and emit one chunk per segment -
    a plausible-looking plan that is really a bug upstream."""
    with pytest.raises(ValueError):
        chunking.plan(make_doc([10] * 3), budget_tokens=budget)


def test_a_negative_overlap_is_refused():
    with pytest.raises(ValueError):
        chunking.plan(make_doc([10] * 3), budget_tokens=BUDGET, overlap_segments=-1)


def test_map_reduce_prompts_names_both_halves():
    """`tasks.py` reads these rather than spelling the names, so the chunk-level
    and combine-level templates are named in one place."""
    assert set(chunking.MAP_REDUCE_PROMPTS) == {"chunk", "combine"}
    assert chunking.MAP_REDUCE_PROMPTS["chunk"] != chunking.MAP_REDUCE_PROMPTS["combine"]
    assert all(name and name.strip() for name in chunking.MAP_REDUCE_PROMPTS.values())


# --- and once against real rows ------------------------------------------------------------


def test_a_plan_over_a_stored_run_uses_the_segments_the_database_holds(conn):
    """Everything above builds a `TranscriptDoc` by hand. This one goes through
    the loader, so the plan is made from sqlite rows in their real shape."""
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)  # the default transcript: four sentences, four segments
    doc = docs.load(conn, media_id)

    whole = chunking.plan(doc, budget_tokens=1000)
    assert len(whole) == 1
    assert "Don't panic" in whole[0].text
    assert whole[0].segment_ids == tuple(s["idx"] for s in doc.segments)

    split = chunking.plan(doc, budget_tokens=50)
    assert len(split) > 1
    assert {i for c in split for i in c.segment_ids} == {s["idx"] for s in doc.segments}
    for chunk in split:
        assert chunk.tokens <= 50
        assert chunk.text.startswith(f"[{render.format_ts(chunk.start)}] ")
