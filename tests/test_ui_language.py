"""The interface is English throughout (TASK-106.05).

The review of 2026-10-05 found "Maximaal" - the large-v3 tier's name - in an
otherwise English interface: the settings, the transcribe dialog, the job
board and a row's tier icon. A language detector would be a heavy and
unreliable gate for a few hundred lines of labels, so this reads what the
templates actually put on the screen and refuses Dutch words that have no
English twin. The list holds the words a Dutch speaker writes without
noticing; extend it when one slips through.
"""

from __future__ import annotations

import re
from pathlib import Path

from scribe import web

DUTCH = {
    "maximaal", "het", "een", "niet", "geen", "wordt", "zijn", "naar", "deze",
    "voor", "opslaan", "bestand", "bestanden", "instellingen", "wachtrij",
    "spreker", "sprekers", "opname", "samenvatting", "annuleren", "verwijderen",
}


def _screen_text(template: str) -> str:
    """What a template can show: no comments, no Jinja, no tags."""
    text = re.sub(r"\{#.*?#\}", " ", template, flags=re.S)
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"\{%.*?%\}|\{\{.*?\}\}", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return text


def _quoted_in(template: str) -> str:
    """String literals inside Jinja expressions - `'Turbo' if turbo else ...`."""
    blocks = re.findall(r"\{[{%].*?[}%]\}", template, flags=re.S)
    return " ".join(a or b for block in blocks for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", block))


def test_no_template_shows_a_dutch_word():
    found = {}
    for path in sorted(Path(web.TEMPLATES_DIR).glob("*.html")):
        source = path.read_text(encoding="utf-8")
        words = set(re.findall(r"[A-Za-zÀ-ÿ]+", _screen_text(source) + " " + _quoted_in(source)))
        dutch = sorted(w for w in words if w.lower() in DUTCH)
        if dutch:
            found[path.name] = dutch
    assert found == {}


def test_the_python_that_builds_ui_lines_names_the_tiers_in_english():
    """Three modules compose a tier's name into a line the page shows."""
    root = Path(web.__file__).parent
    for module in ("jobs_ui.py", "settings.py", "transcribe_dialog.py"):
        literals = re.findall(r"[\"']([^\"'\n]*)[\"']", (root / module).read_text(encoding="utf-8"))
        assert not [s for s in literals if "Maximaal" in s], module
