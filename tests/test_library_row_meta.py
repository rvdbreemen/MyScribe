"""What a library row says about where a file lives and what it is about
(TASK-048).

The folder and the labels were both in the sidebar and never on the row, so
"where does this one live" and "what is this one about" needed a click. They
are two columns of their own, beside each file - Robert's words: the left
navigation is the filter system, and the information belongs in the table.
They still link to the same filtered views, so the row and the sidebar cannot
disagree about what a click means.

Every label the recording carries, not the first few: TASK-048 capped this
at three with a +N, and Robert asked for the lot once he had seen it
(TASK-049). The cell wraps, so a recording holding eight shows eight.

The query count is pinned here too, and that is the assertion that matters
most: the obvious implementation asks the database once per row, which is
invisible on a seeded table of three and a storm on a real library.
"""

from __future__ import annotations

import json
import pathlib
import re
import unicodedata

import pytest

from scribe import db, jobs
from scribe.web import library as web_library
from scribe.web.library import State

from tests.test_web_library import (  # noqa: F401  (fixtures)
    client,
    conn,
    data_dir,
    db_path,
)
from seed import seed_media, seed_run


def _folder(conn, name, parent_id=None):
    with db.LOCK:
        row = conn.execute(
            "INSERT INTO folder(name, parent_id) VALUES (?, ?) RETURNING id",
            (name, parent_id),
        ).fetchone()
        conn.commit()
    return int(row["id"])


def _label(conn, media_id, *names, source="llm"):
    with db.LOCK:
        for name in names:
            conn.execute(
                "INSERT OR IGNORE INTO label(name, created_at) VALUES (?, 0.0)", (name,)
            )
            label_id = conn.execute(
                "SELECT id FROM label WHERE name = ? COLLATE NOCASE", (name,)
            ).fetchone()["id"]
            conn.execute(
                "INSERT OR IGNORE INTO media_label(media_id, label_id, source, created_at)"
                " VALUES (?, ?, ?, 0.0)",
                (media_id, label_id, source),
            )
        conn.commit()


def _row(rows, title):
    return next(r for r in rows if r["title"] == title)


def _tr(body: str, title: str) -> str:
    """The whole <tr> for this title, markup and all."""
    rows = re.findall(r"<tr>(.*?)</tr>", body, re.S)
    return next(r for r in rows if f">{title}</a>" in r)


def _cell(body: str, title: str, klass: str) -> str:
    """One <td class="..."> out of that row - the column under test.

    The class may be followed by other attributes (the folder and label cells
    carry an id so the status poll can swap them by it), so the pattern stops
    at the tag's own `>` rather than assuming the class closes it.
    """
    match = re.search(rf'<td class="{klass}"[^>]*>(.*?)</td>', _tr(body, title), re.S)
    assert match, f"no {klass} cell in the row for {title!r}"
    return match.group(1)


# --- where a file lives ---------------------------------------------------------


def test_a_row_carries_the_name_of_its_folder(conn):  # noqa: F811
    folder = _folder(conn, "Hacker History")
    seed_media(conn, title="Paul Wouters", folder_id=folder)

    rows = web_library.media_rows(conn, State())

    assert _row(rows, "Paul Wouters")["folder_name"] == "Hacker History"


def test_a_file_in_no_folder_reads_as_uncategorized(conn, client):  # noqa: F811
    seed_media(conn, title="Loose clip")

    assert _row(web_library.media_rows(conn, State()), "Loose clip")["folder_name"] is None

    cell = _cell(client.get("/").text, "Loose clip", "folder")
    assert "Uncategorized" in cell


def test_the_folder_on_a_row_links_where_the_sidebar_links(conn, client):  # noqa: F811
    """The same filtered view, so the row and the tree cannot disagree."""
    folder = _folder(conn, "WHYcast")
    seed_media(conn, title="Episode one", folder_id=folder)

    cell = _cell(client.get("/").text, "Episode one", "folder")

    assert f'href="/?folder={folder}"' in cell


def test_a_folder_name_that_is_markup_is_escaped(conn, client):  # noqa: F811
    folder = _folder(conn, "<script>alert(1)</script>")
    seed_media(conn, title="Sharp", folder_id=folder)

    body = client.get("/").text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# --- what a file is about -------------------------------------------------------


def test_a_rows_labels_come_back_with_it(conn):  # noqa: F811
    media_id = seed_media(conn, title="Lockpicking")
    _label(conn, media_id, "cryptography", "hacker history")

    row = _row(web_library.media_rows(conn, State()), "Lockpicking")

    assert row["labels"] == ["cryptography", "hacker history"]


def test_a_file_with_no_labels_says_nothing_rather_than_empty_furniture(conn, client):  # noqa: F811
    seed_media(conn, title="Bare")

    row = _row(web_library.media_rows(conn, State()), "Bare")
    assert row["labels"] == []

    cell = _cell(client.get("/").text, "Bare", "labels")
    assert "badge" not in cell and "chips" not in cell  # an empty cell, not furniture


def test_every_label_is_shown_not_the_first_few(conn, client):  # noqa: F811
    """TASK-049. Five labels means five chips - no cap, and no "+2" standing
    in for names a person cannot read."""
    media_id = seed_media(conn, title="Busy")
    _label(conn, media_id, "alpha", "bravo", "charlie", "delta", "echo")

    row = _row(web_library.media_rows(conn, State()), "Busy")
    assert row["labels"] == ["alpha", "bravo", "charlie", "delta", "echo"]

    cell = _cell(client.get("/").text, "Busy", "labels")
    assert cell.count('class="badge label"') == 5
    for name in ("alpha", "bravo", "charlie", "delta", "echo"):
        assert f">{name}</a>" in cell
    assert "+2" not in cell


def test_the_chips_sit_in_a_box_that_can_bound_them(conn, client):  # noqa: F811
    """Found by looking, not by reading (the first screenshot of this column).

    A max-width on a <td> does nothing in a table that sizes itself to its
    content, so three `white-space: nowrap` chips simply pushed Uploaded off
    the row and swallowed the "+2". The fix is the inner flex box, and this
    asserts the structure the stylesheet needs - the only part of a layout
    bug a test can hold onto.
    """
    media_id = seed_media(conn, title="Wide")
    _label(conn, media_id, "a very long label indeed", "another long one", "a third", "a fourth")

    cell = _cell(client.get("/").text, "Wide", "labels")

    assert '<div class="chips">' in cell
    box = re.search(r'<div class="chips">(.*?)</div>', cell, re.S).group(1)
    assert box.count('class="badge label"') == 4  # every chip inside the box


def test_the_order_does_not_shift_when_another_file_changes(conn):  # noqa: F811
    """A row ordered by how popular a label is across the library would
    reshuffle every time an unrelated recording was labelled. Alphabetical is
    the same three tomorrow."""
    media_id = seed_media(conn, title="Steady")
    _label(conn, media_id, "zeta", "alpha", "mu")
    before = _row(web_library.media_rows(conn, State()), "Steady")["labels"]

    other = seed_media(conn, title="Other")
    _label(conn, other, "zeta", "zeta")

    assert before == ["alpha", "mu", "zeta"]
    assert _row(web_library.media_rows(conn, State()), "Steady")["labels"] == before


def test_a_label_on_a_row_links_where_the_sidebar_links(conn, client):  # noqa: F811
    media_id = seed_media(conn, title="Tagged")
    _label(conn, media_id, "security conferences")

    cell = _cell(client.get("/").text, "Tagged", "labels")

    assert "/?label=security%20conferences" in cell or "/?label=security+conferences" in cell


def test_a_label_that_is_markup_is_escaped(conn, client):  # noqa: F811
    """A label can be written by the LLM pass, so it is a stranger's text."""
    media_id = seed_media(conn, title="Sharp label")
    _label(conn, media_id, '<img src=x onerror="alert(1)">')

    # Scoped to the row's own cell: the sidebar lists every label too, so a
    # body-wide assertion would pass without the row rendering anything.
    cell = _cell(client.get("/").text, "Sharp label", "labels")

    assert "onerror=\"alert(1)\"" not in cell
    assert "&lt;img" in cell


def test_labels_belong_to_their_own_row(conn):  # noqa: F811
    """The grouped query must not spill one file's labels onto another."""
    first = seed_media(conn, title="First")
    second = seed_media(conn, title="Second")
    _label(conn, first, "one")
    _label(conn, second, "two")

    rows = web_library.media_rows(conn, State())

    assert _row(rows, "First")["labels"] == ["one"]
    assert _row(rows, "Second")["labels"] == ["two"]


# --- what it costs --------------------------------------------------------------


def test_the_row_query_does_not_grow_with_the_number_of_rows(conn):  # noqa: F811
    """AC5, counted rather than read. One SELECT per row is invisible on the
    three rows a test seeds and a storm on a real library, so the assertion is
    that twenty rows cost exactly what two do."""
    def statements_for(n):
        for i in range(n):
            media_id = seed_media(conn, title=f"Clip {i:02d}")
            _label(conn, media_id, f"label-{i}", "shared")
        seen = []
        conn.set_trace_callback(seen.append)
        try:
            rows = web_library.media_rows(conn, State())
        finally:
            conn.set_trace_callback(None)
        return len(rows), len([s for s in seen if s.lstrip().upper().startswith("SELECT")])

    rows_two, cost_two = statements_for(2)
    rows_twenty, cost_twenty = statements_for(18)

    assert (rows_two, rows_twenty) == (2, 20)
    assert cost_two == cost_twenty, f"{cost_two} statements for 2 rows, {cost_twenty} for 20"


# --- labelling a whole selection -------------------------------------------------


def test_the_bulk_bar_offers_labelling(client):  # noqa: F811
    """TASK-049. library.BULK_ACTIONS has carried "label" since the labels
    pass existed and this select never offered it, so the only way to label a
    library was one file at a time on its own page."""
    body = client.get("/").text

    assert "label" in web_library.BULK_ACTIONS
    assert '<option value="label">' in body


def test_labelling_a_selection_queues_one_job_per_file(conn, client):  # noqa: F811
    ids = []
    for n in range(3):
        media_id = seed_media(conn, title=f"Talk {n}")
        seed_run(conn, media_id, words=[{"start": 0.0, "end": 1.0, "text": "hello"}])
        ids.append(media_id)

    resp = client.post("/media/bulk", data={"action": "label", "ids": ids}, headers={"HX-Request": "true"})

    assert resp.status_code == 200
    queued = [
        json.loads(row["params_json"])
        for row in conn.execute("SELECT params_json FROM job WHERE type='llm' ORDER BY id")
    ]
    assert [p["media_id"] for p in queued] == ids
    assert {p["kind"] for p in queued} == {"labels"}


def test_a_file_without_a_transcript_is_skipped_and_said_so(conn, client):  # noqa: F811
    """AC3: there is nothing for the pass to read, and a person who ticked
    forty rows should not have to count the jobs to find that out."""
    with_words = seed_media(conn, title="Has words")
    seed_run(conn, with_words, words=[{"start": 0.0, "end": 1.0, "text": "hello"}])
    seed_media(conn, title="Silent so far")

    resp = client.post(
        "/media/bulk", data={"action": "label", "ids": [with_words, with_words + 1]},
        headers={"HX-Request": "true"},
    )

    assert conn.execute("SELECT COUNT(*) c FROM job WHERE type='llm'").fetchone()["c"] == 1
    notice = json.loads(resp.headers["HX-Trigger"])["scribe-notice"]
    assert "without a transcript" in notice


# --- the tier icons --------------------------------------------------------------


def test_the_old_sea_mammals_are_gone_from_every_surface():
    """TASK-049, and the guard is name-based for a reason (TASK-052).

    The first version of this listed the two codepoints it expected to find,
    and one of them was wrong: the templates carried U+1F40B WHALE and the
    test looked for U+1F433 SPOUTING WHALE. The whale survived in four files,
    the settings page kept showing it, and this test stayed green through all
    of it - the exact half-done rename it exists to catch, defeated by a
    number I typed from memory.

    So it asks Unicode what each character *is* rather than trusting a
    constant. There is no codepoint to get wrong.
    """
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for path in sorted((root / "scribe").rglob("*.html")) + sorted((root / "scribe").rglob("*.py")):
        for char in set(path.read_text(encoding="utf-8")):
            if ord(char) < 0x1F000:
                continue
            name = unicodedata.name(char, "")
            if "WHALE" in name or "DOLPHIN" in name:
                offenders.append(f"{path.relative_to(root)} still has U+{ord(char):04X} {name}")
    assert offenders == [], offenders


def test_the_two_tier_icons_are_named_once(conn, client):  # noqa: F811
    """They were five literals across three templates and a Python module,
    which is how one of them got missed. One definition, and both the pages
    and the jobs summary read it."""
    from scribe.web import TIER_ICONS

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in ("scribe/templates/_settings_watch.html",
                "scribe/templates/_settings_defaults.html",
                "scribe/templates/_transcribe_options.html",
                "scribe/web/jobs_ui.py"):
        text = (root / rel).read_text(encoding="utf-8")
        for icon in TIER_ICONS.values():
            assert icon not in text, f"{rel} spells the icon out instead of reading TIER_ICONS"

    body = client.get("/settings").text
    assert TIER_ICONS["turbo"] in body and TIER_ICONS["max"] in body


def test_both_tiers_render_their_own_icon_with_a_label_a_screen_reader_can_read(conn, client):  # noqa: F811
    """An emoji carrying meaning on its own needs a text alternative, or the
    Mode column announces "high voltage" and "direct hit"."""
    def with_model(title, model):
        media_id = seed_media(conn, title=title)
        run_id = seed_run(conn, media_id, words=[{"start": 0.0, "end": 1.0, "text": "hi"}])
        with db.LOCK:  # seed_run pins DEFAULT_MODEL; the tier is the subject here
            conn.execute("UPDATE run SET model=? WHERE id=?", (model, run_id))
            conn.commit()

    with_model("Quick one", "large-v3-turbo")
    with_model("Careful one", "large-v3")

    body = client.get("/").text

    quick = _cell(body, "Quick one", "mode")
    careful = _cell(body, "Careful one", "mode")
    assert "\u26A1" in quick and 'aria-label="Turbo model, large-v3-turbo"' in quick
    assert "\U0001F3AF" in careful and 'aria-label="Maximaal model, large-v3"' in careful
    assert 'role="img"' in quick and 'role="img"' in careful


# --- keeping the row current (TASK-050) ------------------------------------------


def _poll(client, media_id):
    resp = client.get(f"/media/{media_id}/status", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    return resp.text


def test_the_table_renders_the_cells_without_out_of_band_markers(conn, client):  # noqa: F811
    """The same fragment serves the table and the poll; only the poll's copy
    is marked for an out-of-band swap, or the first page load would try to
    swap cells into a table it is still building."""
    seed_media(conn, title="Plain")

    body = client.get("/").text

    assert 'id="labels-' in body and 'id="folder-' in body
    assert "hx-swap-oob" not in _tr(body, "Plain")


def test_a_running_jobs_poll_carries_the_folder_and_the_labels(conn, client):  # noqa: F811
    media_id = seed_media(conn, title="Being labelled")
    jobs.enqueue(conn, "llm", media_id, {"media_id": media_id, "kind": "labels"})

    text = _poll(client, media_id)

    assert f'id="labels-{media_id}"' in text and 'hx-swap-oob="true"' in text
    assert f'id="folder-{media_id}"' in text
    assert "every 2s" in text  # still watching


def test_labels_written_while_the_job_runs_reach_the_next_poll(conn, client):  # noqa: F811
    """The whole point. The labels pass writes labels from a runner child;
    nothing on the page clicked, so without this the row went on showing what
    was true when it loaded."""
    media_id = seed_media(conn, title="Being labelled")
    jobs.enqueue(conn, "llm", media_id, {"media_id": media_id, "kind": "labels"})
    assert "badge label" not in _poll(client, media_id)

    _label(conn, media_id, "lockpicking", "hardware")

    text = _poll(client, media_id)
    assert ">lockpicking</a>" in text and ">hardware</a>" in text


def test_the_last_poll_carries_the_result_and_then_stops(conn, client):  # noqa: F811
    """runner.main calls jobs.finish("done") after every stage has run, so the
    labels are committed before the status the poll reads turns terminal - the
    answer that switches the polling off is the one carrying the result."""
    media_id = seed_media(conn, title="Nearly there")
    job_id = jobs.enqueue(conn, "llm", media_id, {"media_id": media_id, "kind": "labels"})
    _label(conn, media_id, "cryptography")
    jobs.finish(conn, job_id, "done")

    text = _poll(client, media_id)

    assert ">cryptography</a>" in text
    assert "every 2s" not in text  # nothing left to watch


def test_a_move_reaches_the_row_the_same_way(conn, client):  # noqa: F811
    """A category changes without a click too - an import drops a file in a
    folder while the job that fetched it is still on the board."""
    folder = _folder(conn, "Hacker History")
    media_id = seed_media(conn, title="Just arrived")
    jobs.enqueue(conn, "llm", media_id, {"media_id": media_id, "kind": "labels"})
    assert "Uncategorized" in _poll(client, media_id)

    with db.LOCK:
        conn.execute("UPDATE media SET folder_id=? WHERE id=?", (folder, media_id))
        conn.commit()

    text = _poll(client, media_id)
    assert f'href="/?folder={folder}"' in text and "Hacker History" in text
