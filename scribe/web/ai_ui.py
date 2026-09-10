"""The AI side of the web layer: rail actions, output panels, chat, the pin.

Everything here obeys one line of ADR-001: **the web process never runs
inference.** Nothing in this module calls `llm.chat()`, builds a `ChatRequest`
or touches a transport. A rail action writes a `job` row and answers with a
panel; the panel reads `llm_output` rows; the chat page reads `chat_message`
rows. The only provider method reached from a request is `available()`, which
the seam defines as answerable without a call to somebody else's API - and the
one exception, Ollama's bounded loopback probe, is the settings page asking
whether the daemon on *this* machine is up.

Four decisions shape the file.

**Pending is derived, never remembered.** A panel is "working" because a
queued or running `llm` job for that media and kind exists, not because a
previous response said so. The transcript panel re-fetches itself after every
rail action (rename, move, trash), so a panel that carried its state in the
markup would lose it on the next unrelated edit; reading the job row means the
re-render comes back exactly as pending as it was, and a job that died while
nobody was looking stops the polling by itself.

**The privacy boundary is drawn twice, and only one of the two is the
control.** The panel disables every cloud option for a pinned recording and
says why - that is the courtesy, and it is what stops the mistake. The POST
calls `privacy.assert_allowed` before it enqueues anything and answers 403 -
that is the control, and it holds for a form that was edited, a curl, or a
stale page whose media was pinned after it was rendered. The job would refuse
again in `plan_task`; refusing here means no job row is written to explain.

**Model output is text until this module says otherwise.** A structured answer
goes into the template as data and is escaped by the environment like every
other string in this app. A free-text answer goes through `render_markdown`,
which escapes *first* and then adds tags of its own - a small allowlist with
no links, no images and no raw HTML, so the only markup that reaches the
browser is markup this file wrote. Nothing here is `|safe` on anything a model
produced.

**Provider and model are chosen where the work is asked for.** The rail's
select opens on the saved default (`setting`), and the choice travels in the
job's params, so the answer's row records the provider and model that made it
rather than whatever the default happened to be when it is read back.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Annotated, Any, Iterable

from fastapi import APIRouter, Form, HTTPException, Request
from markupsafe import Markup, escape
from starlette.responses import RedirectResponse, Response

from scribe import db, jobs, llm, media as media_store, render
from scribe.llm import base, chat_tool, privacy, selftest, tasks
from scribe.llm.privacy import PrivacyRefused
from scribe.stages import llm_stage
from scribe.web import library, render as render_page, transcript

router = APIRouter()

POLL_SECONDS = 2
"""How often a working panel asks whether its row has landed. The plan's two
seconds: fast enough that a local model's answer appears while the user is
still looking at the panel, slow enough that a four-minute map-reduce is a
hundred cheap reads rather than a thousand."""

PROVIDER_SETTING = "llm_provider"
"""Which provider a new request opens with."""

MODEL_SETTING_PREFIX = "llm_model_"
"""One row per provider: model ids are provider-scoped (`base.retarget`), so a
single "default model" row would name a model that is a 404 the moment the
provider changes."""

MODEL_FIELD_PREFIX = "model_"
"""What the settings form calls the same field. Deliberately not the setting
key: a form field is part of the page's contract with a browser and the row
name is part of the database's, and spelling them the same is how one silently
becomes the other."""

PRIVATE_DEFAULT_FIELD = "private_default"
"""The settings form's name for the private-mode default. Spelled the same as
the setting row this time, because `media.PRIVATE_DEFAULT_SETTING` is the
authority on both and a second name would only be a second thing to keep in
step."""

MODELS_SETTING_PREFIX = "llm_models_"
"""The list `models()` last returned for a provider, as JSON.

Cached rather than fetched on render, and that is the whole reason it exists:
`GET /models` at OpenRouter answers with 423 ids over the internet, and a
settings page that fetched it on every load would wait on somebody else's DNS
to draw a dropdown. The user presses Refresh, the list is remembered, and
every later render populates the dropdown from the row. Free text stays
available either way - a list that is a day old must not be able to forbid a
model that exists today."""

KEY_MASK = "••••"
"""What a stored key renders as. The value is never sent back to the page:
`base.ResolvedKey` will not print itself either, and the settings row shows the
*source* instead, so a stale environment variable outranking a typed key is
visible without the key being."""

FLASH_LLM_SAVED = "AI defaults saved."
FLASH_KEY_SAVED = "Key saved."
FLASH_KEY_CLEARED = "Key cleared; the environment applies again."


# --- settings rows ------------------------------------------------------------------


def setting_get(conn: sqlite3.Connection, key: str) -> str | None:
    with db.LOCK:
        row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return None if row is None else row["value"]


def setting_put(conn: sqlite3.Connection, key: str, value: str) -> None:
    with db.LOCK:
        conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def setting_drop(conn: sqlite3.Connection, key: str) -> None:
    with db.LOCK:
        conn.execute("DELETE FROM setting WHERE key=?", (key,))
        conn.commit()


# Both of these moved to `scribe.llm` in TASK-024, so the runner child can ask
# the same question without importing the web layer. Re-exported here because
# this module's readers - the panel, the settings page, the bulk pass - name
# them through `ai_ui`, and one spelling in two places is how they drift.
default_provider = llm.default_provider
default_model = llm.default_model


def known_models(conn: sqlite3.Connection, provider_name: str) -> list[str]:
    """The model ids this provider offered the last time it was asked.

    Empty when it has never been asked or answered - and empty is what turns
    the dropdown into a free-text field, which is the honest state: this app
    does not know what that endpoint has.
    """
    raw = setting_get(conn, MODELS_SETTING_PREFIX + provider_name)
    if not raw:
        return []
    try:
        found = json.loads(raw)
    except ValueError:
        return []
    return [str(name) for name in found] if isinstance(found, list) else []


def remember_models(conn: sqlite3.Connection, provider_name: str, models: Iterable[str]) -> None:
    setting_put(
        conn, MODELS_SETTING_PREFIX + provider_name, json.dumps(sorted(set(models)))
    )


# --- providers as the UI sees them ----------------------------------------------------


PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "ollama": "Ollama (this machine)",
}
"""Display names. A provider with no entry shows its registry name, which is
better than a KeyError and honest about what it is."""


def provider_label(name: str) -> str:
    return PROVIDER_LABELS.get(name, name)


def local_names() -> list[str]:
    """The registered providers whose text never leaves the machine."""
    return [name for name, cls in llm.PROVIDERS.items() if cls.is_local]


def allowed_provider(conn: sqlite3.Connection, media_id: int, wanted: str) -> str:
    """`wanted`, or a local provider when this recording may not leave.

    Only for choosing what the *form opens with*. A pinned recording whose
    saved default is a cloud provider should show a local one already selected
    rather than a disabled option and an error waiting to happen. It is not the
    check - `assert_allowed` is, on the way in.
    """
    if wanted in llm.PROVIDERS and not llm.PROVIDERS[wanted].is_local:
        if privacy.is_private(conn, media_id):
            local = local_names()
            return local[0] if local else wanted
    return wanted


def provider_choices(*, selected: str, private: bool) -> list[dict]:
    """One entry per registered provider, in name order.

    `disabled` is set for every non-local provider of a pinned recording, and
    the label says where the text would go. Read straight off `Provider.is_local`
    so a provider added to the registry tomorrow is marked correctly without
    this function learning its name (Global Constraints: no `if provider ==`).
    """
    return [
        {
            "name": name,
            "label": provider_label(name),
            "local": bool(cls.is_local),
            "selected": name == selected,
            "disabled": private and not cls.is_local,
        }
        for name, cls in sorted(llm.PROVIDERS.items())
    ]


# --- markdown, on an allowlist --------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_BOLD = re.compile(r"\*\*(\S(?:.*?\S)?)\*\*", re.DOTALL)
_ITALIC = re.compile(r"(?<!\*)\*(\S(?:.*?\S)?)\*(?!\*)", re.DOTALL)
_CODE = re.compile(r"`([^`\n]+)`")


def _inline(text: str) -> str:
    """Emphasis and code inside one already-escaped line.

    Code first, so a `**` inside backticks is not read as emphasis; bold
    before italic, so `**x**` is not seen as two italics. Nothing here can
    introduce a tag the caller did not escape: the input has been through
    `escape()`, and `<`, `>` and `&` are already entities by the time these
    patterns run.
    """
    text = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", text)
    text = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", text)
    text = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", text)
    return text


def render_markdown(text: str) -> Markup:
    """A model's markdown as HTML this module wrote, start to finish.

    The order is the safety property: **escape everything first**, then add
    tags. So `<script>` in an answer is `&lt;script&gt;` before any pattern
    looks at it, and the only elements in the result are the six this function
    emits - headings, paragraphs, lists, `strong`, `em`, `code`. No links and
    no images, deliberately: a URL is the one piece of markdown that carries a
    destination, and a model inventing one is a phishing link this app would be
    rendering on its own page.

    Returns `Markup`, so a template writes it as-is. That is the single place
    in the app where model output is not escaped by the environment, and it is
    safe only because the escaping already happened here.
    """
    source = str(escape(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for block in re.split(r"\n\s*\n", source):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue

        heading = _HEADING.match(lines[0]) if len(lines) == 1 else None
        if heading:
            level = len(heading.group(1))
            out.append(f"<h{level}>{_inline(heading.group(2).strip())}</h{level}>")
            continue

        bullets = [_BULLET.match(line) for line in lines]
        if all(bullets):
            items = "".join(f"<li>{_inline(m.group(1).strip())}</li>" for m in bullets)
            out.append(f"<ul>{items}</ul>")
            continue

        numbered = [_NUMBERED.match(line) for line in lines]
        if all(numbered):
            items = "".join(f"<li>{_inline(m.group(1).strip())}</li>" for m in numbered)
            out.append(f"<ol>{items}</ol>")
            continue

        out.append("<p>" + "<br>".join(_inline(line.strip()) for line in lines) + "</p>")
    return Markup("".join(out))


# --- moments: a timestamp that can be clicked ------------------------------------------


def moment(media_id: int, seconds: float | None) -> dict | None:
    """One seekable timestamp: what to show, where to go, what to seek to.

    `data-start` and the `#t=` href are both rendered because they serve two
    readers: app.js seeks in place from the attribute, and a browser with no
    scripting follows the link to the transcript at that moment. `%g` rather
    than the raw float so `83.0` is `83` in a URL somebody may paste.

    Anything that is not a finite number is no moment at all. Today's rows
    cannot hold one - `tasks.OptionalSeconds` normalises to float-or-None
    before a row is written - but `_items` promises to render a row an older
    version of this app wrote, and this was the single line where that promise
    was untrue: a `ValueError` here is a 500 on the whole transcript page. An
    item without a seek link is still an item; a page that will not load is
    not a page.
    """
    if seconds is None or isinstance(seconds, bool):
        return None
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):  # NaN and infinity survive float() and format as text
        return None
    value = max(0.0, value)
    at = f"{value:g}"
    return {"seconds": value, "at": at, "label": render.format_ts(value), "href": f"/media/{media_id}#t={at}"}


# --- what a stored answer looks like on the page ------------------------------------------


def _loads(content: str) -> Any:
    try:
        return json.loads(content)
    except ValueError:
        return None


def _items(media_id: int, rows: Any) -> list[dict]:
    """`ActionItem`s as the template wants them, tolerantly.

    A row is stored canonical JSON (`tasks.content_for` validated it), so this
    is not parsing so much as reshaping - but it stays tolerant because a row
    written by an older version of this app is still a row this page has to
    render rather than crash on.
    """
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append(
            {
                "text": str(row.get("text") or ""),
                "owner": row.get("owner") or None,
                "moment": moment(media_id, row.get("evidence_ts")),
            }
        )
    return out


def _strings(rows: Any) -> list[str]:
    return [str(row) for row in rows] if isinstance(rows, list) else []


TEXT_SHAPE = "text"
"""The view shape for an answer with no structure to show: `custom`, and any
row that did not parse into the shape its kind promises."""


def output_view(media_id: int, kind: str, content: str) -> dict:
    """A stored answer as the fields `_ai_output.html` renders.

    Shaped here rather than in the template because two of the six kinds carry
    timestamps that become links and two carry markdown that becomes HTML;
    doing either in Jinja would put the escaping decision in the template,
    which is exactly where it must not be.

    Every view names its own `shape`, and the template branches on *that*
    rather than on the kind that was asked for. The two are the same whenever
    the row parses - and when it does not, they must not be: a row that is not
    a legal `action_items` object still has to render, and rendering it under a
    block that expects a list of items is how it becomes a 500 instead. The
    shape is what the view actually holds, so the branch and the fields can no
    longer disagree.
    """
    data = _loads(content) if kind in tasks.TASKS and tasks.TASKS[kind].schema else None
    if kind == "summary" and isinstance(data, dict):
        return {
            "shape": kind,
            "paragraph": str(data.get("paragraph") or ""),
            "bullets": _strings(data.get("bullets")),
        }
    if kind == "action_items" and isinstance(data, dict):
        return {"shape": kind, "items": _items(media_id, data.get("items"))}
    if kind == "chapters" and isinstance(data, dict):
        chapters = data.get("chapters")
        return {
            "shape": kind,
            "chapters": [
                {"title": str(c.get("title") or ""), "moment": moment(media_id, c.get("start"))}
                for c in (chapters if isinstance(chapters, list) else [])
                if isinstance(c, dict)
            ],
        }
    if kind == "minutes" and isinstance(data, dict):
        return {
            "shape": kind,
            "agenda": _strings(data.get("agenda")),
            "decisions": _strings(data.get("decisions")),
            "actions": _items(media_id, data.get("actions")),
        }
    if kind == "blog" and isinstance(data, dict):
        return {
            "shape": kind,
            "title": str(data.get("title") or ""),
            "html": render_markdown(str(data.get("body") or "")),
        }
    if kind == "labels" and isinstance(data, dict):
        entries = data.get("labels")
        return {
            "shape": kind,
            "labels": [
                {
                    "label": str(entry.get("label") or ""),
                    "confidence": str(entry.get("confidence") or ""),
                    "evidence": str(entry.get("evidence") or ""),
                }
                for entry in (entries if isinstance(entries, list) else [])
                if isinstance(entry, dict) and str(entry.get("label") or "").strip()
            ],
        }
    if kind == "speakers" and isinstance(data, dict):
        return {
            "shape": kind,
            "format": str(data.get("format") or ""),
            "speakers": [
                {
                    "cluster": str(guess.get("cluster") or ""),
                    "name": str(guess.get("name") or "").strip(),
                    "role": str(guess.get("role") or ""),
                    "confidence": str(guess.get("confidence") or ""),
                    "evidence": str(guess.get("evidence") or ""),
                    "notes": str(guess.get("notes") or ""),
                }
                for guess in (data.get("speakers") or [])
                if isinstance(guess, dict) and guess.get("cluster")
            ],
        }
    # `custom` has no schema, and neither, in effect, does a row written before
    # a schema changed or by a version that stored something else: the model's
    # own words, rendered as the markdown they usually are. Showing the text is
    # the honest answer - an empty structured panel would claim the model said
    # nothing, when what happened is that this app could not read what it said.
    return {"shape": TEXT_SHAPE, "html": render_markdown(content)}


# --- reading the rows the panels show -------------------------------------------------


def latest_output(conn: sqlite3.Connection, media_id: int, kind: str) -> dict | None:
    """The newest stored answer of one kind, or None.

    `ORDER BY id DESC LIMIT 1` over `idx_llm_output_media_kind`: a re-run adds
    a row rather than replacing one (`tasks.store_output`), so "the answer" is
    always the last one written, and the older ones stay as the record of what
    an earlier model said.
    """
    with db.LOCK:
        row = conn.execute(
            "SELECT * FROM llm_output WHERE media_id=? AND kind=? ORDER BY id DESC LIMIT 1",
            (media_id, kind),
        ).fetchone()
    return None if row is None else dict(row)


def pending_jobs(conn: sqlite3.Connection, media_id: int) -> dict[str, dict]:
    """The queued or running `llm` job per kind for this recording.

    One query and a dict rather than a query per panel: the transcript page
    draws six panels and a chat link, and seven scans of `job` per page load
    would be seven more than this needs. The kind lives in `params_json`, so
    it is read here rather than matched in SQL - there are at most a handful of
    active jobs, and an index on a JSON field would be an index on a string.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT id, status, stage, stage_progress, params_json FROM job"
            " WHERE type=? AND media_id=? AND status IN ('queued','running') ORDER BY id",
            (llm_stage.JOB_TYPE, media_id),
        ).fetchall()
    found: dict[str, dict] = {}
    for row in rows:
        try:
            params = json.loads(row["params_json"])
        except ValueError:
            continue
        kind = str(params.get("kind") or "")
        if kind:
            found[kind] = {
                "id": row["id"],
                "status": row["status"],
                "stage": row["stage"],
                "progress": row["stage_progress"],
                # What was asked, so `ai_run` can tell "the same question
                # again" from "a different one while that one is still going".
                "provider": str(params.get("provider") or ""),
                "model": str(params.get("model") or ""),
                "prompt": str(params.get("prompt") or ""),
            }
    return found


def is_stale(row: dict | None, run_id: int | None) -> bool:
    """Whether this answer was made from a transcript that is no longer current.

    A re-transcription is different words, so an answer made before it is about
    something that no longer exists - which is why `llm_output.run_id` is a
    column at all (see db.py's v4 note) and why `tasks.stored_chunk` keys on it.
    The panel does not hide such an answer: it is what that model said, it cost
    something, and the audio it points at has not moved. It says so instead.

    A row that names no run - written before the column existed - cannot be
    judged either way, and calling it stale would be an invention. So would
    calling it fresh; it is simply not marked.
    """
    if row is None or run_id is None:
        return False
    stored = row.get("run_id")
    return stored is not None and int(stored) != int(run_id)


def was_truncated(row: dict | None) -> bool:
    """Whether the model stopped writing this answer because it ran out of room.

    `tasks.store_output` has always recorded `finish_reason` in `params_json`;
    until now nothing read it. It matters most for `custom`, which has no
    schema to fail against: a truncated answer parses, stores and renders
    exactly like a finished one, so an answer the model was cut off halfway
    through is presented as what it had to say.

    The rule `is_stale` follows applies here too - a row that cannot be judged
    is not judged. A provider that reports no finish reason has not said the
    answer was truncated, and neither does this.
    """
    if row is None:
        return False
    try:
        params = json.loads(row.get("params_json") or "{}")
    except ValueError:
        return False
    return isinstance(params, dict) and params.get("finish_reason") == tasks.TRUNCATED_FINISH


def panel_for(
    conn: sqlite3.Connection,
    media_id: int,
    kind: str,
    *,
    job: dict | None = None,
    run_id: int | None = None,
) -> dict:
    """One output section: the kind, what is stored, and what is on its way."""
    spec = tasks.task_spec(kind)
    row = latest_output(conn, media_id, kind)
    view = output_view(media_id, kind, row["content"]) if row is not None else None
    if view is not None and view.get("shape") == "speakers":
        view = with_current_names(conn, view, run_id)
    return {
        "media_id": media_id,
        "kind": kind,
        "label": spec.label,
        "needs_prompt": spec.needs_prompt,
        "output": row,
        "view": view,
        "stale": is_stale(row, run_id),
        "truncated": was_truncated(row),
        "job": job,
        "poll": POLL_SECONDS,
    }


def with_current_names(conn: sqlite3.Connection, view: dict, run_id: int | None) -> dict:
    """The speakers view with what each cluster is called *now*, so the apply
    form can show a suggestion next to the name it would replace, and so a
    cluster the model invented (one the run does not have) is dropped rather
    than offered: applying it would name nobody.

    `current` is the display name (`Speaker 3` for an unnamed cluster);
    `named` says whether somebody already chose one, because a suggestion
    over a chosen name is a different proposition from one over a default.
    """
    from scribe.web import transcript

    if run_id is None:
        return view
    known = {s["cluster"]: s for s in transcript.run_speakers(conn, run_id)}
    rows = []
    for guess in view.get("speakers", []):
        have = known.get(guess["cluster"])
        if have is None:
            continue
        rows.append({**guess, "current": have["name"], "named": have["label"] is not None})
    return {**view, "speakers": rows, "unknown": len(view.get("speakers", [])) - len(rows)}


def summary_line(choices: list[dict], model: str) -> str:
    """What the collapsed provider block currently says.

    A section that folds away is only honest when closed means answered rather
    than hidden, so the summary names the two things inside it: who will answer
    and with which model. The provider's own label is used, not its key, since
    that is the word the select shows.
    """
    selected = next((c for c in choices if c.get("selected")), None)
    parts = [str(selected["label"]) if selected else "no provider"]
    if model:
        parts.append(str(model))
    if selected is not None and not selected.get("local"):
        parts.append("leaves your machine")
    return " · ".join(parts)


def panel_context(
    conn: sqlite3.Connection, media_id: int, *, has_transcript: bool, run_id: int | None = None
) -> dict:
    """Everything `_ai_panel.html` and the six `_ai_output.html` sections need.

    Merged into the transcript page's context, so the rail and the outputs are
    rendered by the same request that renders the words - and re-rendered by
    the panel's own `refresh` after any rail action, which is safe because
    every piece of state here is read from a row.
    """
    private = privacy.is_private(conn, media_id)
    chosen = allowed_provider(conn, media_id, default_provider(conn))
    active = pending_jobs(conn, media_id)
    choices = provider_choices(selected=chosen, private=private)
    model = default_model(conn, chosen)
    return {
        "ai": {
            "media_id": media_id,
            "private": private,
            "has_transcript": has_transcript,
            "providers": choices,
            "provider": chosen,
            "model": model,
            "summary_line": summary_line(choices, model),
            "models": known_models(conn, chosen),
            "panels": [
                panel_for(conn, media_id, kind, job=active.get(kind), run_id=run_id)
                for kind in tasks.KINDS
            ],
            "chat_job": active.get(chat_tool.CHAT_KIND),
            "poll": POLL_SECONDS,
        }
    }


# --- asking for one --------------------------------------------------------------------


def _spec(kind: str) -> tasks.TaskSpec:
    try:
        return tasks.task_spec(kind)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


def _provider(name: str) -> str:
    try:
        llm.provider_class(name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return name


def _refuse_if_private(conn: sqlite3.Connection, media_id: int, provider_name: str) -> None:
    """The control, not the courtesy.

    The panel disables cloud options for a pinned recording; this is what holds
    when the form was edited, the page was rendered before the pin, or the post
    came from curl. `assert_allowed` reads the provider *class*, so the refusal
    happens before anything is built - and before a job row exists to explain.
    """
    try:
        privacy.assert_allowed(conn, media_id, llm.provider_class(provider_name))
    except PrivacyRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from None


def _already_queued(pending: dict | None, params: dict) -> bool:
    """Whether `pending` is this same request, already on its way.

    Compared field by field rather than as two dicts: `pending` comes from
    `pending_jobs`, which flattens what a panel needs out of the row, and a
    dict equality between that shape and the params would be an accident
    waiting for the day either gains a key.
    """
    if pending is None:
        return False
    return (
        pending.get("provider") == params.get("provider")
        and pending.get("model") == params.get("model")
        and pending.get("prompt") == (params.get("prompt") or "")
    )


def _require_transcript(conn: sqlite3.Connection, media_id: int) -> dict:
    run = transcript.current_run(conn, media_id)
    if run is None:
        raise HTTPException(
            status_code=409,
            detail=f"media {media_id} has no transcript yet; there is nothing to ask about",
        )
    return run


def _one_panel(request: Request, conn: sqlite3.Connection, media_id: int, kind: str) -> Response:
    """One output section, rendered against the run that is current now.

    The run is looked up here rather than passed in because a panel outlives
    the request that drew it: it polls itself, and between two polls the
    recording may have been re-transcribed. Reading the current run each time
    is what lets the next poll say so.
    """
    run = transcript.current_run(conn, media_id)
    panel = panel_for(
        conn,
        media_id,
        kind,
        job=pending_jobs(conn, media_id).get(kind),
        run_id=run["id"] if run else None,
    )
    return render_page(request, "_ai_output.html", panel=panel)


@router.get("/media/{media_id}/ai/{kind}", include_in_schema=False)
def ai_output(media_id: int, kind: str, request: Request) -> Response:
    """One kind's panel. What a working panel polls, and what it stops at."""
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    _spec(kind)
    return _one_panel(request, conn, media_id, kind)


@router.post("/media/{media_id}/ai/{kind}", include_in_schema=False)
def ai_run(
    media_id: int,
    kind: str,
    request: Request,
    provider: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
    prompt: Annotated[str, Form()] = "",
) -> Response:
    """Queue one question about this recording; answer with its panel.

    Nothing is asked of a model here (ADR-001): this writes a `job` row and
    the runner child makes the call. The checks in front of the row are the
    ones that cost nothing and would otherwise become a failed job somebody has
    to read - an unknown kind, a recording with no words, a missing custom
    prompt, the pin, and the identical request that is already on its way.
    """
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    spec = _spec(kind)
    _require_transcript(conn, media_id)

    provider_name = _provider((provider or "").strip() or default_provider(conn))
    _refuse_if_private(conn, media_id, provider_name)

    asked = (prompt or "").strip()
    if spec.needs_prompt and not asked:
        raise HTTPException(
            status_code=400,
            detail=f"the {kind!r} action needs a question; there is nothing to ask without one",
        )

    params: dict[str, Any] = {
        "media_id": media_id,
        "kind": kind,
        "provider": provider_name,
        "model": (model or "").strip() or default_model(conn, provider_name),
    }
    if asked:
        params["prompt"] = asked

    # A second click while the first is still queued or running is the same
    # question asked twice, and on a cloud provider that is a second paid call
    # for the same answer. `queue_provider_test` guards its own enqueue exactly
    # this way. The match is on the whole request rather than on (media, kind):
    # a user who changed the model or typed a different question pressed the
    # button on purpose, and collapsing that would discard what they asked for.
    if not _already_queued(pending_jobs(conn, media_id).get(kind), params):
        jobs.enqueue(conn, llm_stage.JOB_TYPE, media_id, params)
    if not library._is_htmx(request):
        return RedirectResponse(f"/media/{media_id}#ai-{kind}", status_code=303)
    response = _one_panel(request, conn, media_id, kind)
    response.headers["HX-Trigger"] = "jobs-changed"
    return response


# --- the pin ------------------------------------------------------------------------------


@router.post("/media/{media_id}/private", include_in_schema=False)
def set_media_private(
    media_id: int, request: Request, private: Annotated[str, Form()] = ""
) -> Response:
    """Pin or un-pin one recording.

    Posted from the transcript rail and from the library row, so the answer
    depends on where it came from: the row's form names `#media-table` as its
    target and gets the table back, the rail's gets the transcript panel.
    """
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    with db.LOCK:
        conn.execute(
            "UPDATE media SET private=? WHERE id=?", (1 if library._truthy(private) else 0, media_id)
        )
        conn.commit()
    if request.headers.get("HX-Target") == "media-table":
        return library._after_change(request, conn)
    if library._is_htmx(request):
        return render_page(
            request, "_transcript_panel.html", **transcript.page_context(conn, media_id)
        )
    return RedirectResponse(f"/media/{media_id}", status_code=303)


@router.post("/folders/{folder_id}/private", include_in_schema=False)
def set_folder_private(
    folder_id: int, request: Request, private: Annotated[str, Form()] = ""
) -> Response:
    """Pin or un-pin a folder - and with it everything inside it, at any depth.

    One column, no walk: `privacy.is_private` climbs the chain for each media
    when it is asked, so pinning a folder is a single row and a file three
    levels down is covered the moment it is written.
    """
    conn = request.app.state.conn
    library._get_folder(conn, folder_id)
    with db.LOCK:
        conn.execute(
            "UPDATE folder SET private=? WHERE id=?",
            (1 if library._truthy(private) else 0, folder_id),
        )
        conn.commit()
    return library._after_change(request, conn)


# --- chat ----------------------------------------------------------------------------------


def _citation_links(media_id: int, row: dict) -> list[dict]:
    try:
        found = json.loads(row.get("citations_json") or "[]")
    except ValueError:
        return []
    links = [moment(media_id, seconds) for seconds in found if isinstance(seconds, (int, float))]
    return [link for link in links if link is not None]


def chat_context(conn: sqlite3.Connection, media_id: int) -> dict:
    """Everything chat.html renders from: the conversation and the ask form.

    The whole conversation, oldest first - not `history_for`'s window. That
    window is what the *model* is shown, and it is capped so turn twelve still
    leaves room for the recording; a reader wants what was said.
    """
    media = dict(library._get_media(conn, media_id))
    with db.LOCK:
        rows = conn.execute(
            "SELECT * FROM chat_message WHERE media_id=? ORDER BY id", (media_id,)
        ).fetchall()
    private = privacy.is_private(conn, media_id)
    chosen = allowed_provider(conn, media_id, default_provider(conn))
    return {
        "media": media,
        "run": transcript.current_run(conn, media_id),
        "private": private,
        "providers": provider_choices(selected=chosen, private=private),
        "provider": chosen,
        "model": default_model(conn, chosen),
        "models": known_models(conn, chosen),
        "job": pending_jobs(conn, media_id).get(chat_tool.CHAT_KIND),
        "poll": POLL_SECONDS,
        "messages": [
            {
                "id": row["id"],
                "role": row["role"],
                "mine": row["role"] == chat_tool.USER,
                "created_at": row["created_at"],
                "html": render_markdown(row["content"]),
                "citations": _citation_links(media_id, dict(row)),
            }
            for row in rows
        ],
    }


@router.get("/media/{media_id}/chat", include_in_schema=False)
def chat_page(media_id: int, request: Request) -> Response:
    """The conversation about one recording.

    A page of its own rather than a panel on the transcript: a chat is read
    top to bottom and grows, and the transcript page is already a transcript, a
    rail and a player. The citations are links to `/media/{id}#t=`, so clicking
    one lands on the transcript at that moment with the player seeking there -
    the same `data-start` handler the transcript's own timestamps use.
    """
    conn = request.app.state.conn
    return render_page(request, "chat.html", **chat_context(conn, media_id))


@router.post("/media/{media_id}/chat", include_in_schema=False)
def chat_ask(
    media_id: int,
    request: Request,
    question: Annotated[str, Form()] = "",
    provider: Annotated[str, Form()] = "",
    model: Annotated[str, Form()] = "",
) -> Response:
    """Queue one question. The answer arrives as two `chat_message` rows.

    The question is not stored here: `chat_tool.store_exchange` writes the pair
    in one transaction when the answer exists, because half an exchange would
    render as a turn the app forgot to reply to.
    """
    conn = request.app.state.conn
    library._get_media(conn, media_id)
    _require_transcript(conn, media_id)

    asked = (question or "").strip()
    if not asked:
        raise HTTPException(
            status_code=400, detail="a chat turn needs a question; there is nothing to answer"
        )

    provider_name = _provider((provider or "").strip() or default_provider(conn))
    _refuse_if_private(conn, media_id, provider_name)

    jobs.enqueue(
        conn,
        llm_stage.JOB_TYPE,
        media_id,
        {
            "media_id": media_id,
            "kind": chat_tool.CHAT_KIND,
            "question": asked,
            "provider": provider_name,
            "model": (model or "").strip() or default_model(conn, provider_name),
        },
    )

    if not library._is_htmx(request):
        return RedirectResponse(f"/media/{media_id}/chat", status_code=303)
    response = render_page(request, "chat.html", **chat_context(conn, media_id))
    response.headers["HX-Trigger"] = "jobs-changed"
    return response


# --- the settings section --------------------------------------------------------------------


def private_default(conn: sqlite3.Connection) -> bool:
    """Whether a newly added recording starts pinned private.

    The row lives in `scribe/media.py` because that is what reads it, at the
    one moment it means anything: the INSERT that creates a media row. This is
    only the settings page's view of the same row.
    """
    return media_store.private_default(conn)


def set_private_default(conn: sqlite3.Connection, on: bool) -> None:
    setting_put(conn, media_store.PRIVATE_DEFAULT_SETTING, "1" if on else "0")


# --- testing a provider ------------------------------------------------------------------------


def pending_tests(conn: sqlite3.Connection) -> dict[str, dict]:
    """The queued or running provider test per provider name.

    One query for the whole table rather than one per row, the same shape as
    `pending_jobs` and for the same reason: the provider a test is about lives
    in `params_json`, so it is read here rather than matched in SQL.
    """
    with db.LOCK:
        rows = conn.execute(
            "SELECT id, status, params_json FROM job"
            " WHERE type=? AND media_id IS NULL AND status IN ('queued','running') ORDER BY id",
            (llm_stage.JOB_TYPE,),
        ).fetchall()
    found: dict[str, dict] = {}
    for row in rows:
        try:
            params = json.loads(row["params_json"])
        except ValueError:
            continue
        if params.get("kind") != selftest.KIND:
            continue
        name = str(params.get("provider") or "")
        if name:
            found.setdefault(name, {"id": row["id"], "status": row["status"]})
    return found


def queue_provider_test(conn: sqlite3.Connection, provider_name: str) -> tuple[int, bool]:
    """Queue one provider test; returns (job id, whether it is new).

    A test already waiting for the same provider is returned instead of a
    second one - `settings.queue_gpu_checks` does exactly this for the doctor
    job, and the reason is the same: the answer would be the same endpoint
    asked twice, a second apart.

    Nothing here calls the provider (ADR-001). The model is resolved from the
    settings row so the test asks about the id this app would actually use.
    """
    pending = pending_tests(conn).get(provider_name)
    if pending is not None:
        return pending["id"], False
    job_id = jobs.enqueue(
        conn,
        llm_stage.JOB_TYPE,
        None,  # a probe is about no recording: see scribe/llm/selftest.py
        {
            "kind": selftest.KIND,
            "provider": provider_name,
            "model": default_model(conn, provider_name),
        },
    )
    return job_id, True


def test_view(result: selftest.ProbeResult | None) -> dict | None:
    """The last test as `_settings_llm.html` shows it.

    `detail` is the provider's own mapped error, which `base.redact` has
    already stripped any key out of - it is the same string the jobs board
    shows, and it is the useful half of a failed test.
    """
    if result is None:
        return None
    return {
        "ok": result.ok,
        "model": result.model,
        "answer": result.answer,
        "detail": result.detail,
        "at": result.at,
        "seconds": round(result.elapsed_s, 1),
        "job_id": result.job_id,
    }


def provider_rows(conn: sqlite3.Connection) -> list[dict]:
    """One row per registered provider for the settings table.

    `available()` is the only provider method a page render calls, and the seam
    guarantees it answers without a request to somebody else's API: for a cloud
    provider it is "is there a key", and Ollama's is a loopback probe bounded by
    its own `PROBE_TIMEOUT`. Any `LlmError` from it is reported as the reason
    rather than raised - a provider having a bad day must not take the settings
    page down with it.
    """
    testing = pending_tests(conn)
    rows = []
    for name, cls in sorted(llm.PROVIDERS.items()):
        provider = cls(conn)
        try:
            ready, why = provider.available()
        except base.LlmError as exc:  # pragma: no cover - defensive
            ready, why = False, str(exc)
        key = base.api_key(conn, cls)
        rows.append(
            {
                "name": name,
                "label": provider_label(name),
                "local": bool(cls.is_local),
                "ready": ready,
                "why": why,
                "model": default_model(conn, name),
                "models": known_models(conn, name),
                "needs_key": bool(cls.key_env_vars),
                "key_source": key.source,
                "key_mask": KEY_MASK if key.found else "",
                "env_names": list(cls.key_env_vars),
                "test": test_view(selftest.last_result(conn, name)),
                "test_job": testing.get(name),
            }
        )
    return rows


def settings_context(conn: sqlite3.Connection, *, flash: str | None = None) -> dict:
    """What `_settings_llm.html` renders from."""
    return {
        "llm_providers": provider_rows(conn),
        "llm_provider": default_provider(conn),
        "llm_private_default": private_default(conn),
        "llm_flash": flash,
    }
