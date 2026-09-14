"""What a library row says about where a file lives and what it is about
(TASK-048).

The folder and the labels were both in the sidebar and never on the row, so
"where does this one live" and "what is this one about" needed a click. They
are two columns of their own, beside each file - Robert's words: the left
navigation is the filter system, and the information belongs in the table.
They still link to the same filtered views, so the row and the sidebar cannot
disagree about what a click means.

The cap is three, and a file with more says how many more - hiding the rest
silently would make the column a lie about a recording with eight labels.

The query count is pinned here too, and that is the assertion that matters
most: the obvious implementation asks the database once per row, which is
invisible on a seeded table of three and a storm on a real library.
"""

from __future__ import annotations

import re

import pytest

from scribe import db
from scribe.web import library as web_library
from scribe.web.library import State

from tests.test_web_library import (  # noqa: F401  (fixtures)
    client,
    conn,
    data_dir,
    db_path,
)
from seed import seed_media


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
    """One <td class="..."> out of that row - the column under test."""
    match = re.search(rf'<td class="{klass}">(.*?)</td>', _tr(body, title), re.S)
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
    assert row["label_overflow"] == 0


def test_a_file_with_no_labels_says_nothing_rather_than_empty_furniture(conn, client):  # noqa: F811
    seed_media(conn, title="Bare")

    row = _row(web_library.media_rows(conn, State()), "Bare")
    assert row["labels"] == []

    cell = _cell(client.get("/").text, "Bare", "labels")
    assert "badge" not in cell and "+" not in cell  # an empty cell, not furniture


def test_at_most_three_labels_are_shown(conn):  # noqa: F811
    media_id = seed_media(conn, title="Busy")
    _label(conn, media_id, "alpha", "bravo", "charlie", "delta", "echo")

    row = _row(web_library.media_rows(conn, State()), "Busy")

    assert row["labels"] == ["alpha", "bravo", "charlie"]


def test_a_file_with_more_than_three_says_how_many_more_and_names_them(conn, client):  # noqa: F811
    """AC3: the cap must not quietly misrepresent a recording that carries
    eight. The count is on screen and the rest are in the title attribute,
    which is where a hover can reach them without a second request."""
    media_id = seed_media(conn, title="Busy")
    _label(conn, media_id, "alpha", "bravo", "charlie", "delta", "echo")

    row = _row(web_library.media_rows(conn, State()), "Busy")
    assert row["label_overflow"] == 2

    cell = _cell(client.get("/").text, "Busy", "labels")
    assert "+2" in cell
    assert "delta" in cell and "echo" in cell


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
    assert box.count('class="badge label"') == 3 and "+1" in box


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
