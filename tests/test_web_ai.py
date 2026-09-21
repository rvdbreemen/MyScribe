"""Phase 5 Task 6: the AI panel, the chat page and the provider settings.

Everything here is the *web* side of the LLM layer, and the one rule that
shapes all of it is ADR-001: the web process never runs inference. So there is
no provider call anywhere in this file and none in the code it exercises - a
rail action writes a `job` row, a panel reads `llm_output` rows, and the chat
page reads `chat_message` rows. What is asserted is that the enqueued row says
the right thing and the rendered markup shows what is stored.

Four things earn their own tests because each is a promise the UI makes:

* **The privacy boundary is visible and enforced twice.** A pinned recording's
  panel disables every cloud option *and* the POST refuses one that is forced
  anyway. The disabled attribute is a courtesy; the 403 is the control, and the
  test posts around the form to prove it.
* **Model output is never trusted.** A stored answer containing `<script>` has
  to arrive at the browser escaped, whether it went through the markdown
  renderer (a free-text answer) or straight into a template (a structured one).
* **A pending panel is derived from the job row, not remembered.** The
  transcript panel re-fetches itself after any rail action, so a panel that
  kept its pending state in the markup would lose it; deriving it from a
  queued or running `llm` job means the re-render comes back pending.
* **A citation is a link that seeks.** `[1:23]` in a stored answer renders as
  an anchor carrying both `data-start` (what app.js seeks by) and a `#t=`
  href (what a browser without scripting follows).

No network: the only provider method the web layer calls is `available()`, and
Ollama's is the one that probes - over loopback, bounded - so it is stubbed to
report the daemon down. The cloud providers' availability is deliberately not
asserted: this machine has `OPENROUTER_TOKEN` set machine-wide in the registry
and a `.env` beside the repo, so "no key" is a fact about a laptop rather than
about the code.
"""

from __future__ import annotations

import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from scribe import db, paths
from scribe import media as scribe_media  # `media` is a fixture name in this file
from scribe.app import create_app
from scribe.llm import base, ollama, openai_like, selftest, tasks
from scribe.llm.chat_tool import ASSISTANT, USER
from scribe.web import ai_ui
from seed import seed_media, seed_run

HX = {"HX-Request": "true"}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    return data


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    c = db.connect(path)
    db.migrate(c)
    c.close()
    return path


@pytest.fixture
def conn(db_path):
    c = db.connect(db_path)
    yield c
    c.close()


@pytest.fixture
def client(db_path, data_dir):
    app = create_app(db_path=db_path, start_supervisor=False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def no_ollama(monkeypatch):
    """The one provider method the settings page calls that would open a socket.

    Ollama's `available()` probes loopback by design (it is the only way to
    tell "not running" from "running, model not pulled"); a test must not, so
    the daemon reports itself down and the row says so.

    `tags` is the seam and not `models`, since TASK-089.06: `models()` and
    `chat_models()` are both built on it, so stopping the method that makes the
    request stops every caller. Patching only `models` stopped being enough the
    moment the model refresh started asking `chat_models()` - and it showed,
    as this machine's real daemon answering a test.
    """
    def unreachable(self):
        raise base.NothingAnswered(f"Ollama is not running at {self.host}")

    monkeypatch.setattr(ollama.OllamaProvider, "tags", unreachable)


@pytest.fixture
def media(conn):
    """One recording with the default four-sentence transcript."""
    media_id = seed_media(conn, title="Guide", duration=20.0)
    seed_run(conn, media_id)
    return media_id


# --- helpers ------------------------------------------------------------------------


def current_run(conn, media_id):
    with db.LOCK:
        row = conn.execute(
            "SELECT id FROM run WHERE media_id=? AND is_current=1 ORDER BY id DESC LIMIT 1",
            (media_id,),
        ).fetchone()
    return None if row is None else row["id"]


def store_output(
    conn, media_id, kind, content, *, provider="openrouter", model="m-1", run_id=-1, params=None
):
    """One `llm_output` row, the way `tasks.store_output` writes it.

    ``run_id`` defaults to the media's current run, because that is what
    `tasks.store_output` records: an answer is about the words it was given.
    Pass None for a row from before the column existed.

    ``params`` is what `tasks.store_output` puts in `params_json` - the served
    model, the call count, and how the model stopped. Defaults to an empty
    object, which is also what a row written before a key existed looks like.
    """
    if run_id == -1:
        run_id = current_run(conn, media_id)
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO llm_output(media_id, run_id, kind, provider, model,"
            " prompt_version, content, params_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                media_id,
                run_id,
                kind,
                provider,
                model,
                tasks.PROMPT_VERSION,
                content,
                json.dumps(params or {}),
                time.time(),
            ),
        )
        conn.commit()
        return cur.lastrowid


def store_message(conn, media_id, role, content, citations=()):
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO chat_message(media_id, role, content, citations_json, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (media_id, role, content, json.dumps(list(citations)), time.time()),
        )
        conn.commit()
        return cur.lastrowid


def jobs_of(conn, type_="llm"):
    with db.LOCK:
        rows = conn.execute(
            "SELECT * FROM job WHERE type=? ORDER BY id", (type_,)
        ).fetchall()
    return [dict(row) for row in rows]


def queue_job(conn, media_id, kind, status="queued"):
    """A pending `llm` job for one kind, as the POST would leave it."""
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO job(type, media_id, status, params_json, created_at)"
            " VALUES ('llm', ?, ?, ?, ?)",
            (media_id, status, json.dumps({"media_id": media_id, "kind": kind}), time.time()),
        )
        conn.commit()
        return cur.lastrowid


def set_private(conn, *, media_id=None, folder_id=None, value=1):
    with db.LOCK:
        if media_id is not None:
            conn.execute("UPDATE media SET private=? WHERE id=?", (value, media_id))
        if folder_id is not None:
            conn.execute("UPDATE folder SET private=? WHERE id=?", (value, folder_id))
        conn.commit()


def option_of(body: str, select_name: str, value: str) -> str:
    """The whole `<option value="...">…</option>` inside the named select.

    Tag *and* label, because half of what is asserted about an option is an
    attribute (`disabled`, `selected`) and the other half is the words next to
    it (`leaves your machine`), which is what a reader actually sees.
    """
    select = re.search(
        rf'<select[^>]*name="{select_name}"[^>]*>(.*?)</select>', body, re.DOTALL
    )
    assert select, f"no <select name={select_name!r}> in the response"
    tag = re.search(
        rf'<option value="{re.escape(value)}"[^>]*>.*?</option>', select.group(1), re.DOTALL
    )
    assert tag, f"no option {value!r} in <select name={select_name!r}>"
    return tag.group(0)


# --- the rail -----------------------------------------------------------------------


def test_rail_lists_every_ai_action_each_posting_to_its_own_kind(client, media):
    resp = client.get(f"/media/{media}")

    assert resp.status_code == 200
    body = resp.text
    for kind in tasks.KINDS:
        assert f'formaction="/media/{media}/ai/{kind}"' in body, kind
        assert tasks.TASKS[kind].label in body
    # Every kind, named: a count alone would pass a rename, and the point of
    # this line is that adding a kind means facing this test.
    assert tasks.KINDS == (
        "summary",
        "action_items",
        "chapters",
        "minutes",
        "blog",
        "speakers",
        "labels",
        "cleanup",
        "custom",
    )


def test_the_panel_offers_every_provider_and_marks_the_ones_that_leave_the_machine(client, media):
    body = client.get(f"/media/{media}").text

    assert 'value="ollama"' in option_of(body, "provider", "ollama")
    assert "leaves your machine" not in option_of(body, "provider", "ollama")
    for cloud in ("openai", "openrouter"):
        assert "leaves your machine" in option_of(body, "provider", cloud)


def test_the_five_canned_questions_are_in_view_and_the_custom_one_is_beside_its_box(
    client, media
):
    """The rail's AI section used to open with two selects, a textarea and then
    the buttons, which put the thing it exists for last. The questions that
    need nothing but a click come first now; the sixth needs a prompt, so it
    lives inside the collapsed block next to the box it reads."""
    body = client.get(f"/media/{media}").text

    region = re.search(r'<section id="ai-region".*?</section>', body, re.DOTALL).group(0)
    # The tag carries an id and hx-preserve since TASK-053.03, so the
    # pattern stops at the tag's own ">" rather than assuming the class
    # closes it.
    collapsed = re.search(r"<details class=\"options\"[^>]*>.*?</details>", region, re.DOTALL).group(0)
    visible = region.replace(collapsed, "")

    custom = tasks.KINDS[-1]
    for kind in tasks.KINDS[:-1]:
        assert f'formaction="/media/{media}/ai/{kind}"' in visible, kind
        assert f'formaction="/media/{media}/ai/{kind}"' not in collapsed, kind
    assert f'formaction="/media/{media}/ai/{custom}"' in collapsed
    assert 'name="prompt"' in collapsed
    # Provider and model are settings, and settings are what collapses.
    assert 'name="provider"' in collapsed and 'name="model"' in collapsed


def test_the_collapsed_block_says_who_will_answer_and_whether_it_leaves(client, conn, media):
    """Collapsed is only honest when closed means answered rather than hidden."""
    # The row is written rather than assumed: since ADR-016 a missing one
    # selects no provider, and this test is about what the summary says when
    # somebody *has* chosen a cloud one.
    ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "openrouter")

    body = client.get(f"/media/{media}").text

    line = re.search(r'<span class="summary-line">([^<]*)</span>', body).group(1)
    assert "OpenRouter" in line
    assert "leaves your machine" in line


def test_the_privacy_warning_stays_out_of_the_collapsed_block(client, conn, media):
    """Which provider is selected is a privacy fact, and a fact that only shows
    when you go looking is not a warning."""
    ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "openrouter")  # as above (ADR-016)

    body = client.get(f"/media/{media}").text

    region = re.search(r'<section id="ai-region".*?</section>', body, re.DOTALL).group(0)
    # The tag carries an id and hx-preserve since TASK-053.03, so the
    # pattern stops at the tag's own ">" rather than assuming the class
    # closes it.
    collapsed = re.search(r"<details class=\"options\"[^>]*>.*?</details>", region, re.DOTALL).group(0)

    assert "is not on this machine" in region.replace(collapsed, "")


def test_summary_line_names_the_provider_and_model_and_is_quiet_about_local_ones():
    local = [{"name": "ollama", "label": "Ollama", "local": True, "selected": True}]
    cloud = [{"name": "openai", "label": "OpenAI", "local": False, "selected": True}]

    assert ai_ui.summary_line(local, "qwen3.5:4b") == "Ollama \u00b7 qwen3.5:4b"
    assert ai_ui.summary_line(cloud, "gpt-x") == "OpenAI \u00b7 gpt-x \u00b7 leaves your machine"
    assert ai_ui.summary_line([], "") == "no provider"


def test_posting_an_action_enqueues_an_llm_job_with_the_right_params(client, conn, media):
    resp = client.post(
        f"/media/{media}/ai/summary",
        data={"provider": "ollama", "model": "qwen3.5:4b"},
        headers=HX,
    )

    assert resp.status_code == 200
    queued = jobs_of(conn)
    assert len(queued) == 1
    assert queued[0]["type"] == "llm"
    assert queued[0]["media_id"] == media
    assert json.loads(queued[0]["params_json"]) == {
        "media_id": media,
        "kind": "summary",
        "provider": "ollama",
        "model": "qwen3.5:4b",
    }


def test_a_custom_action_carries_the_typed_prompt_into_the_job(client, conn, media):
    client.post(
        f"/media/{media}/ai/custom",
        data={"provider": "ollama", "model": "qwen3.5:4b", "prompt": "What about the towel?"},
        headers=HX,
    )

    params = json.loads(jobs_of(conn)[0]["params_json"])
    assert params["kind"] == "custom"
    assert params["prompt"] == "What about the towel?"


def test_a_custom_action_without_a_prompt_is_refused_before_a_job_exists(client, conn, media):
    resp = client.post(
        f"/media/{media}/ai/custom", data={"provider": "ollama", "prompt": "  "}, headers=HX
    )

    assert resp.status_code == 400
    assert jobs_of(conn) == []


def test_clicking_the_same_action_again_while_it_runs_queues_one_job(client, conn, media):
    """Five clicks on Summary is one question asked five times, and on a cloud
    provider it is five paid calls that all return the same answer. The panel
    polls and shows "Working…", but nothing stopped the button being pressed
    again. `queue_provider_test` already does exactly this guard for the
    provider tests, and cites `settings.queue_gpu_checks` as its own precedent.
    """
    form = {"provider": "ollama", "model": "qwen3.5:4b"}
    first = client.post(f"/media/{media}/ai/summary", data=form, headers=HX)
    for _ in range(4):
        again = client.post(f"/media/{media}/ai/summary", data=form, headers=HX)

    assert first.status_code == again.status_code == 200
    assert len(jobs_of(conn)) == 1
    # And the answer is still the pending panel, so the second click looks like
    # the first: the work is under way either way.
    assert f'hx-get="/media/{media}/ai/summary"' in again.text


def test_a_finished_action_can_be_asked_again(client, conn, media):
    """The guard is about a job that is still queued or running, not about a
    kind that has ever been asked - "regenerate" has to keep working."""
    form = {"provider": "ollama", "model": "qwen3.5:4b"}
    client.post(f"/media/{media}/ai/summary", data=form, headers=HX)
    with db.LOCK:
        conn.execute("UPDATE job SET status='done' WHERE type='llm'")
        conn.commit()

    client.post(f"/media/{media}/ai/summary", data=form, headers=HX)

    assert len(jobs_of(conn)) == 2


def test_a_different_question_is_a_different_job(client, conn, media):
    """`custom` is the kind where collapsing by (media, kind) would be wrong:
    two questions about one recording are two questions."""
    form = {"provider": "ollama", "model": "qwen3.5:4b"}
    client.post(f"/media/{media}/ai/custom", data={**form, "prompt": "What was decided?"}, headers=HX)
    client.post(f"/media/{media}/ai/custom", data={**form, "prompt": "Who spoke most?"}, headers=HX)

    queued = jobs_of(conn)
    assert len(queued) == 2
    assert [json.loads(j["params_json"])["prompt"] for j in queued] == [
        "What was decided?",
        "Who spoke most?",
    ]


def test_asking_the_same_kind_of_a_different_model_is_a_different_job(client, conn, media):
    """Changing the model and pressing again is a deliberate second request -
    the whole reason the panel carries a model select."""
    client.post(f"/media/{media}/ai/summary", data={"provider": "ollama", "model": "a"}, headers=HX)
    client.post(f"/media/{media}/ai/summary", data={"provider": "ollama", "model": "b"}, headers=HX)

    assert [json.loads(j["params_json"])["model"] for j in jobs_of(conn)] == ["a", "b"]


def test_posting_an_action_answers_with_a_panel_that_polls_until_a_row_exists(client, conn, media):
    resp = client.post(f"/media/{media}/ai/summary", data={"provider": "ollama"}, headers=HX)

    body = resp.text
    assert f'id="ai-summary"' in body
    assert f'hx-get="/media/{media}/ai/summary"' in body
    assert "every 2s" in body
    assert f'/jobs/{jobs_of(conn)[0]["id"]}' in body


def test_an_unknown_kind_is_refused(client, media):
    assert client.post(f"/media/{media}/ai/vogon-poetry", data={}).status_code == 404


def test_an_action_on_a_recording_with_no_transcript_is_refused(client, conn):
    media_id = seed_media(conn, title="Silent")

    resp = client.post(f"/media/{media_id}/ai/summary", data={"provider": "ollama"}, headers=HX)

    assert resp.status_code == 409
    assert jobs_of(conn) == []


# --- the output panels --------------------------------------------------------------


def test_a_stored_summary_renders_its_paragraph_and_bullets(client, conn, media):
    store_output(
        conn,
        media,
        "summary",
        json.dumps({"paragraph": "Two travellers discuss towels.", "bullets": ["Bring a towel"]}),
    )

    body = client.get(f"/media/{media}/ai/summary", headers=HX).text

    assert "Two travellers discuss towels." in body
    assert "Bring a towel" in body
    assert "every 2s" not in body  # nothing to wait for


def test_a_stored_output_shows_on_the_transcript_page_itself(client, conn, media):
    store_output(conn, media, "summary", json.dumps({"paragraph": "The towel wins.", "bullets": []}))

    assert "The towel wins." in client.get(f"/media/{media}").text


def test_stored_chapters_render_as_links_that_seek(client, conn, media):
    store_output(
        conn,
        media,
        "chapters",
        json.dumps({"chapters": [{"start": 83.0, "title": "Towels"}]}),
    )

    body = client.get(f"/media/{media}/ai/chapters", headers=HX).text

    assert 'data-start="83"' in body
    assert f'href="/media/{media}#t=83"' in body
    assert ">1:23<" in body


def sample_answer(kind: str) -> tuple[str, str]:
    """A legal stored answer for one kind, and a phrase that must reach the page.

    Built through the kind's own schema and `tasks.content_for` rather than by
    hand, because that is the only row worth rendering in a test: it is byte
    for byte what a finished job writes. Rendering hand-written JSON for three
    of the six kinds is how a panel that raises on the other three ships.
    """
    payloads: dict[str, object] = {
        "summary": tasks.Summary(
            paragraph="Two travellers pack a towel.", bullets=["Bring a towel"]
        ),
        "action_items": tasks.ActionItems(
            items=[tasks.ActionItem(text="Buy a towel", owner="Ford", evidence_ts=83.0)]
        ),
        "chapters": tasks.Chapters(chapters=[tasks.Chapter(start=83.0, title="On towels")]),
        "minutes": tasks.Minutes(
            agenda=["Towels"],
            decisions=["Always carry one"],
            actions=[tasks.ActionItem(text="Buy a towel", owner="Ford", evidence_ts=83.0)],
        ),
        "blog": tasks.Blog(title="Why a towel", body="A towel is **massively** useful."),
        "speakers": tasks.Speakers(
            format="conversation",
            speakers=[
                tasks.SpeakerGuess(
                    cluster="SPEAKER_00", name="Arthur", role="host", confidence="high",
                    evidence="[0:00] 'Don't panic'",
                ),
                tasks.SpeakerGuess(cluster="SPEAKER_01", name="Marvin", role="guest", confidence="low"),
                tasks.SpeakerGuess(cluster="SPEAKER_09", name="Nobody"),
            ],
        ),
        "labels": json.dumps({"labels": [{"label": "towels", "confidence": "high"}]}),
        "cleanup": "**Arthur:** Don't panic. The towel is the most important item.",
        "custom": "A towel is the most useful thing an interstellar hitchhiker can carry.",
    }
    markers = {
        "summary": "Two travellers pack a towel.",
        "action_items": "Buy a towel",
        "chapters": "On towels",
        "minutes": "Always carry one",
        "blog": "Why a towel",
        "speakers": 'value="Arthur"',
        "labels": "towels",
        "cleanup": "The towel is the most important item.",
        "custom": "the most useful thing an interstellar hitchhiker can carry",
    }
    assert set(payloads) == set(tasks.KINDS), "a kind was added without a sample answer"
    return tasks.content_for(tasks.task_spec(kind), payloads[kind]), markers[kind]


@pytest.mark.parametrize("kind", tasks.KINDS)
def test_every_kind_renders_the_answer_a_finished_job_would_have_stored(client, conn, media, kind):
    """All six, not the three that were easy to hand-write.

    `built-in method` is asserted against as well as the status: a template
    that reaches a dict key which happens to name a dict method gets the bound
    method, and Jinja renders that as text wherever it is not iterated. So the
    quiet half of the same mistake is caught here too, not only the 500.
    """
    content, marker = sample_answer(kind)
    store_output(conn, media, kind, content)

    resp = client.get(f"/media/{media}/ai/{kind}", headers=HX)

    assert resp.status_code == 200, resp.text[:400]
    assert marker in resp.text
    assert "built-in method" not in resp.text


def test_the_transcript_page_survives_an_answer_of_every_kind_at_once(client, conn, media):
    """The page draws all six sections, so one panel that raises takes it down.

    Six passing panel fetches do not prove this: a panel is only reachable on
    its own after the job that fills it lands, while `/media/{id}` is the page
    a browser opens.
    """
    markers = {}
    for kind in tasks.KINDS:
        content, markers[kind] = sample_answer(kind)
        store_output(conn, media, kind, content)

    resp = client.get(f"/media/{media}")

    assert resp.status_code == 200, resp.text[:400]
    for kind, marker in markers.items():
        assert marker in resp.text, kind
    assert "built-in method" not in resp.text


@pytest.mark.parametrize("value", ["half past", "", float("nan"), float("inf"), [83.0], {"at": 1}])
def test_a_timestamp_that_is_not_a_number_is_no_moment_rather_than_a_crash(value):
    """`_items` promises to render a row an older version wrote; this is the
    one place that promise was untrue. No moment means no seek link, which is
    honest - the item is still what somebody agreed to do."""
    assert ai_ui.moment(1, value) is None


def test_an_action_item_renders_its_owner_and_a_link_that_seeks(client, conn, media):
    content, _ = sample_answer("action_items")
    store_output(conn, media, "action_items", content)

    body = client.get(f"/media/{media}/ai/action_items", headers=HX).text

    assert "Ford" in body
    assert 'data-start="83"' in body
    assert f'href="/media/{media}#t=83"' in body


def test_an_action_item_with_an_unreadable_timestamp_still_renders_its_text(client, conn, media):
    store_output(
        conn,
        media,
        "action_items",
        json.dumps({"items": [{"text": "Buy a towel", "evidence_ts": "1:23"}]}),
    )

    resp = client.get(f"/media/{media}/ai/action_items", headers=HX)

    assert resp.status_code == 200, resp.text[:400]
    assert "Buy a towel" in resp.text
    assert "data-start" not in resp.text


def test_a_chapter_with_an_unreadable_start_still_renders_its_title(client, conn, media):
    store_output(
        conn, media, "chapters", json.dumps({"chapters": [{"start": "later", "title": "Towels"}]})
    )

    resp = client.get(f"/media/{media}/ai/chapters", headers=HX)

    assert resp.status_code == 200, resp.text[:400]
    assert "Towels" in resp.text
    assert "data-start" not in resp.text


@pytest.mark.parametrize("kind", [k for k in tasks.KINDS if tasks.TASKS[k].schema])
def test_a_row_that_does_not_parse_shows_what_was_stored_instead_of_raising(
    client, conn, media, kind
):
    """The tolerance `_items` claims in its docstring, for every schema'd kind.

    `output_view` already falls back to "render the stored text as markdown"
    when a row does not parse into the kind's shape - that is what makes a row
    written by an older version readable rather than fatal. The template has to
    take that fallback too, or the fallback renders under a block expecting
    fields it does not have.
    """
    store_output(conn, media, kind, "not JSON, just what some model said once")

    resp = client.get(f"/media/{media}/ai/{kind}", headers=HX)
    page = client.get(f"/media/{media}")

    assert resp.status_code == 200, resp.text[:400]
    assert "not JSON, just what some model said once" in resp.text
    assert page.status_code == 200, page.text[:400]
    assert "built-in method" not in resp.text


@pytest.mark.parametrize("kind", [k for k in tasks.KINDS if tasks.TASKS[k].schema])
def test_a_row_holding_the_wrong_json_shape_is_shown_rather_than_guessed_at(
    client, conn, media, kind
):
    """A list where an object belongs: legal JSON, wrong shape. It must not be
    rendered as an empty answer of that kind - the row said something."""
    store_output(conn, media, kind, json.dumps(["towels", "forty-two"]))

    resp = client.get(f"/media/{media}/ai/{kind}", headers=HX)

    assert resp.status_code == 200, resp.text[:400]
    assert "towels" in resp.text


def test_a_pending_job_makes_the_panel_poll_even_after_a_re_render(client, conn, media):
    """The pending state is read off the job row, so a panel that is fetched
    again - which every rail action makes happen - comes back still pending."""
    job_id = queue_job(conn, media, "minutes", status="running")

    body = client.get(f"/media/{media}/ai/minutes", headers=HX).text

    assert "every 2s" in body
    assert f"/jobs/{job_id}" in body


def test_an_answer_made_from_an_earlier_transcript_says_so_and_is_still_shown(client, conn, media):
    """A re-transcription is different words, so yesterday's summary is about
    a transcript that no longer exists. It is not hidden - it is what that
    model said and it cost something - but it must not pass for current."""
    store_output(conn, media, "summary", json.dumps({"paragraph": "Old words.", "bullets": []}))
    seed_run(conn, media)  # a second run; `is_current` moves to it

    body = client.get(f"/media/{media}/ai/summary", headers=HX).text

    assert "Old words." in body
    assert "earlier transcript" in body


def test_an_answer_made_from_the_current_transcript_is_not_marked(client, conn, media):
    store_output(conn, media, "summary", json.dumps({"paragraph": "Fresh words.", "bullets": []}))

    body = client.get(f"/media/{media}/ai/summary", headers=HX).text

    assert "Fresh words." in body
    assert "earlier transcript" not in body


def test_an_answer_that_names_no_run_is_not_guessed_about(client, conn, media):
    """A row from before `llm_output.run_id` existed cannot be judged either
    way, and claiming it is stale would be an invention."""
    store_output(
        conn, media, "summary", json.dumps({"paragraph": "No run.", "bullets": []}), run_id=None
    )
    seed_run(conn, media)

    body = client.get(f"/media/{media}/ai/summary", headers=HX).text

    assert "No run." in body
    assert "earlier transcript" not in body


def test_an_answer_the_model_was_cut_off_writing_says_so(client, conn, media):
    """`custom` has no schema, so a truncated answer parses, stores and renders
    exactly like a finished one - the model simply stopped mid-sentence and
    nothing on the page said which. The row has always recorded how it stopped;
    until now no template read it."""
    store_output(
        conn,
        media,
        "custom",
        "They agreed to raise the budget by",
        params={"finish_reason": "length"},
    )

    body = client.get(f"/media/{media}/ai/custom", headers=HX).text

    assert "They agreed to raise the budget by" in body
    assert "cut off" in body


def test_an_answer_the_model_finished_is_not_marked(client, conn, media):
    store_output(
        conn, media, "custom", "They agreed to raise the budget.", params={"finish_reason": "stop"}
    )

    body = client.get(f"/media/{media}/ai/custom", headers=HX).text

    assert "cut off" not in body


def test_an_answer_that_does_not_say_how_it_stopped_is_not_guessed_about(client, conn, media):
    """The same rule the stale marker follows: a row that cannot be judged is
    not judged. A provider that reports no finish reason has not said the
    answer was truncated, and neither has this app."""
    store_output(conn, media, "custom", "They agreed to raise the budget.", params={})

    body = client.get(f"/media/{media}/ai/custom", headers=HX).text

    assert "cut off" not in body


def test_a_free_text_answer_containing_a_script_tag_is_escaped_in_the_html(client, conn, media):
    store_output(conn, media, "custom", "Here you go: <script>alert(1)</script>")

    body = client.get(f"/media/{media}/ai/custom", headers=HX).text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_a_structured_answer_containing_a_script_tag_is_escaped_too(client, conn, media):
    store_output(
        conn,
        media,
        "summary",
        json.dumps({"paragraph": "<script>alert(2)</script>", "bullets": []}),
    )

    body = client.get(f"/media/{media}/ai/summary", headers=HX).text

    assert "<script>alert(2)</script>" not in body
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in body


def test_markdown_from_a_model_becomes_tags_of_our_own_and_nothing_else():
    html = str(ai_ui.render_markdown("# Title\n\n- one\n- two\n\n**bold** and <b>raw</b>"))

    assert "<h1>Title</h1>" in html
    assert "<li>one</li>" in html
    assert "<strong>bold</strong>" in html
    assert "<b>raw</b>" not in html
    assert "&lt;b&gt;raw&lt;/b&gt;" in html


# --- the privacy boundary -----------------------------------------------------------


def test_a_private_medias_panel_disables_every_cloud_option_and_keeps_the_local_one(
    client, conn, media
):
    set_private(conn, media_id=media)

    body = client.get(f"/media/{media}").text

    assert "disabled" not in option_of(body, "provider", "ollama")
    for cloud in ("openai", "openrouter"):
        assert "disabled" in option_of(body, "provider", cloud)
    assert "🔒" in body


def test_a_private_media_refuses_a_forced_cloud_provider_with_403_and_no_job(client, conn, media):
    """Posted around the form, because the disabled attribute is a courtesy and
    this is the control."""
    set_private(conn, media_id=media)

    resp = client.post(f"/media/{media}/ai/summary", data={"provider": "openrouter"}, headers=HX)

    assert resp.status_code == 403
    assert "openrouter" in resp.text
    assert jobs_of(conn) == []


def test_a_private_media_still_allows_the_local_provider(client, conn, media):
    set_private(conn, media_id=media)

    resp = client.post(f"/media/{media}/ai/summary", data={"provider": "ollama"}, headers=HX)

    assert resp.status_code == 200
    assert len(jobs_of(conn)) == 1


def test_a_media_inside_a_private_folder_is_refused_the_same_way(client, conn):
    with db.LOCK:
        folder_id = conn.execute(
            "INSERT INTO folder(name, parent_id, private) VALUES ('Vault', NULL, 1)"
        ).lastrowid
        conn.commit()
    media_id = seed_media(conn, title="Inside", folder_id=folder_id)
    seed_run(conn, media_id)

    resp = client.post(
        f"/media/{media_id}/ai/summary", data={"provider": "openrouter"}, headers=HX
    )

    assert resp.status_code == 403
    assert jobs_of(conn) == []


def test_the_private_toggle_round_trips_on_a_media(client, conn, media):
    client.post(f"/media/{media}/private", data={"private": "1"}, headers=HX)
    with db.LOCK:
        assert conn.execute("SELECT private FROM media WHERE id=?", (media,)).fetchone()[0] == 1

    client.post(f"/media/{media}/private", data={"private": "0"}, headers=HX)
    with db.LOCK:
        assert conn.execute("SELECT private FROM media WHERE id=?", (media,)).fetchone()[0] == 0


def test_the_private_toggle_round_trips_on_a_folder(client, conn):
    with db.LOCK:
        folder_id = conn.execute("INSERT INTO folder(name) VALUES ('Vault')").lastrowid
        conn.commit()

    client.post(f"/folders/{folder_id}/private", data={"private": "1"}, headers=HX)
    with db.LOCK:
        assert conn.execute(
            "SELECT private FROM folder WHERE id=?", (folder_id,)
        ).fetchone()[0] == 1


def test_a_pinned_recording_wears_a_badge_in_the_library(client, conn, media):
    assert "badge private" not in client.get("/").text

    set_private(conn, media_id=media)

    assert "badge private" in client.get("/").text


# --- the chat page -------------------------------------------------------------------


def test_the_chat_page_shows_the_conversation_oldest_first(client, conn, media):
    store_message(conn, media, USER, "What about towels?")
    store_message(conn, media, ASSISTANT, "They are essential [1:23].", citations=[83.0])

    body = client.get(f"/media/{media}/chat").text

    assert body.index("What about towels?") < body.index("They are essential")


def test_a_citation_renders_as_a_link_that_seeks_the_player(client, conn, media):
    store_message(conn, media, ASSISTANT, "They are essential [1:23].", citations=[83.0])

    body = client.get(f"/media/{media}/chat").text

    assert 'data-start="83"' in body
    assert f'href="/media/{media}#t=83"' in body
    assert ">1:23<" in body


def test_asking_a_question_enqueues_a_chat_job_carrying_the_question(client, conn, media):
    resp = client.post(
        f"/media/{media}/chat",
        data={"question": "Who mentioned the towel?", "provider": "ollama"},
        headers=HX,
    )

    assert resp.status_code == 200
    params = json.loads(jobs_of(conn)[0]["params_json"])
    assert params["kind"] == "chat"
    assert params["question"] == "Who mentioned the towel?"
    assert params["provider"] == "ollama"


def test_an_empty_question_is_refused_before_a_job_exists(client, conn, media):
    resp = client.post(f"/media/{media}/chat", data={"question": "   "}, headers=HX)

    assert resp.status_code == 400
    assert jobs_of(conn) == []


def test_a_private_media_refuses_a_cloud_chat_too(client, conn, media):
    set_private(conn, media_id=media)

    resp = client.post(
        f"/media/{media}/chat", data={"question": "Anything?", "provider": "openai"}, headers=HX
    )

    assert resp.status_code == 403
    assert jobs_of(conn) == []


def test_an_answer_containing_a_script_tag_is_escaped_on_the_chat_page(client, conn, media):
    store_message(conn, media, ASSISTANT, "<script>alert(3)</script>")

    body = client.get(f"/media/{media}/chat").text

    assert "<script>alert(3)</script>" not in body
    assert "&lt;script&gt;alert(3)&lt;/script&gt;" in body


# --- the settings page ----------------------------------------------------------------


def test_settings_lists_every_provider_with_its_locality_and_availability(
    client, no_ollama
):
    body = client.get("/settings").text

    rows = re.findall(r'<tr[^>]*data-provider="([^"]+)"', body)
    assert sorted(rows) == ["ollama", "openai", "openrouter"]
    # The one row whose availability does not depend on this machine's keys.
    assert "Ollama is not running" in body
    assert "local" in body


def test_settings_names_the_source_a_key_came_from_without_showing_it(client, conn, no_ollama):
    with db.LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            (base.setting_key("openai"), "sk-not-a-real-key-0123456789"),
        )
        conn.commit()

    body = client.get("/settings").text

    assert "sk-not-a-real-key-0123456789" not in body
    assert "••••" in body
    assert "settings" in body


def test_saving_the_llm_defaults_stores_the_provider_and_its_model(client, conn, no_ollama):
    resp = client.post(
        "/settings/llm",
        data={"provider": "ollama", "model_ollama": "gemma4:12b", "model_openai": "gpt-4o-mini"},
        headers=HX,
    )

    assert resp.status_code == 200
    assert ai_ui.default_provider(conn) == "ollama"
    assert ai_ui.default_model(conn, "ollama") == "gemma4:12b"
    assert ai_ui.default_model(conn, "openai") == "gpt-4o-mini"


def test_the_saved_default_provider_is_the_one_the_panel_opens_with(client, conn, media, no_ollama):
    client.post("/settings/llm", data={"provider": "ollama", "model_ollama": "qwen3.5:4b"}, headers=HX)

    body = client.get(f"/media/{media}").text

    assert "selected" in option_of(body, "provider", "ollama")


def test_a_key_typed_into_settings_is_stored_and_can_be_cleared(client, conn, no_ollama):
    client.post("/settings/llm/openai/key", data={"key": "sk-typed-here-0123456789"}, headers=HX)
    with db.LOCK:
        row = conn.execute(
            "SELECT value FROM setting WHERE key=?", (base.setting_key("openai"),)
        ).fetchone()
    assert row["value"] == "sk-typed-here-0123456789"

    client.post("/settings/llm/openai/key", data={"clear": "1"}, headers=HX)
    with db.LOCK:
        assert conn.execute(
            "SELECT value FROM setting WHERE key=?", (base.setting_key("openai"),)
        ).fetchone() is None


def _tags(*pairs):
    """`/api/tags` rows as the live daemon sends them, capabilities and all.

    `tags` is the seam these tests patch and not `chat_models`: the filter that
    keeps an embedder out of the dropdown is the thing under test, so patching
    the method that does the filtering would leave the test green while proving
    nothing (TASK-089.06).
    """
    return lambda self: [{"name": name, "model": name, "capabilities": list(caps)} for name, caps in pairs]


def test_refreshing_a_providers_model_list_remembers_what_it_offered(client, conn, monkeypatch, no_ollama):
    monkeypatch.setattr(
        ollama.OllamaProvider,
        "tags",
        _tags(("gemma4:12b", ["completion"]), ("qwen3.5:4b", ["completion"])),
    )

    body = client.post("/settings/llm/ollama/models", headers=HX).text

    assert ai_ui.known_models(conn, "ollama") == ["gemma4:12b", "qwen3.5:4b"]
    assert "gemma4:12b" in body


def test_a_refresh_stores_the_chat_models_and_never_an_embedder(client, conn, monkeypatch, no_ollama):
    """Five of the eight models on this machine are embedders, and before this
    the refresh stored every name it was given - so an embedder could be picked
    as the chat model, everything showed green, and the first summary failed
    inside a job (TASK-089.06).

    `qwen3-embedding:0.6b` is the one that makes this a real list: it reports
    `tools` and `thinking` as well, so no capability but `completion` would
    sort these eight correctly.
    """
    monkeypatch.setattr(
        ollama.OllamaProvider,
        "tags",
        _tags(
            ("bge-m3:latest", ["embedding"]),
            ("granite-embedding:278m", ["embedding"]),
            ("embeddinggemma:latest", ["embedding"]),
            ("qwen3-embedding:0.6b", ["tools", "thinking", "embedding"]),
            ("qwen3-embedding:4b", ["tools", "embedding"]),
            ("qwen3.5:4b", ["completion", "vision", "tools", "thinking"]),
            ("qwen3.5:9b", ["completion", "vision", "tools", "thinking"]),
            ("gemma4:12b", ["completion", "vision", "audio", "tools", "thinking"]),
        ),
    )

    body = client.post("/settings/llm/ollama/models", headers=HX).text

    assert ai_ui.known_models(conn, "ollama") == ["gemma4:12b", "qwen3.5:4b", "qwen3.5:9b"]
    assert "3 model(s) offered" in body
    assert "embedding" not in body


def test_a_daemon_that_will_not_say_stores_nothing_and_says_so(client, conn, monkeypatch, no_ollama):
    """"It would not say" is not "it has none": storing an empty list would
    leave the dropdown claiming this endpoint offers nothing."""
    monkeypatch.setattr(ollama.OllamaProvider, "chat_models", lambda self: None)

    body = client.post("/settings/llm/ollama/models", headers=HX).text

    assert ai_ui.known_models(conn, "ollama") == []
    assert "would not say" in body


def test_a_cloud_providers_refresh_still_offers_every_model_it_lists(
    client, conn, monkeypatch, no_ollama
):
    """The base class answers `chat_models()` with the whole list, so nothing
    changed for a provider that serves no embedders - and there is no
    `if provider ==` anywhere to make that true."""
    monkeypatch.setattr(
        openai_like.OpenAIProvider, "models", lambda self: ["gpt-4o-mini", "gpt-5.6-luna"]
    )

    client.post("/settings/llm/openai/models", headers=HX)

    assert ai_ui.known_models(conn, "openai") == ["gpt-4o-mini", "gpt-5.6-luna"]


def test_an_unreachable_provider_leaves_the_model_field_as_free_text(client, conn, no_ollama):
    resp = client.post("/settings/llm/ollama/models", headers=HX)

    assert resp.status_code == 200
    assert ai_ui.known_models(conn, "ollama") == []
    assert "Ollama is not running" in resp.text


# --- picking a model from the fetched list (TASK-054) ---------------------------------
#
# Robert asked for a dropdown filled from what each provider returned. The trap,
# measured on 2026-09-14: the stored OpenAI model was gpt-5.6, which is not among
# the 134 ids OpenAI listed (its first is babbage-002). A plain <select> shows
# the first option for a value it lacks, and the one form posts every provider's
# model on each Save - so ticking "New recordings start private" would have made
# babbage-002 the OpenAI model.


def browser_post(body: str, action: str) -> dict[str, list[str]]:
    """What a browser posts from the form with `action` when nobody touches a
    field: each select's selected option (its first when none is marked), each
    text or hidden input's value, and a checkbox only while it is checked."""
    form = re.search(rf'<form[^>]*action="{re.escape(action)}"[^>]*>(.*?)</form>', body, re.S)
    assert form, f"no form posting to {action}"
    fields: dict[str, list[str]] = {}
    for tag in re.finditer(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>|<input([^>]*)>', form.group(1), re.S):
        if tag.group(1):
            options = re.findall(r'<option value="([^"]*)"([^>]*)>', tag.group(2))
            chosen = [value for value, attrs in options if re.search(r"\bselected\b", attrs)]
            fields.setdefault(tag.group(1), []).append(chosen[0] if chosen else (options[0][0] if options else ""))
            continue
        attrs = tag.group(3)
        name = re.search(r'name="([^"]*)"', attrs)
        kind = re.search(r'type="([^"]*)"', attrs)
        kind = kind.group(1) if kind else "text"
        if not name or kind in ("submit", "button") or (kind == "checkbox" and not re.search(r"\bchecked\b", attrs)):
            continue
        value = re.search(r'value="([^"]*)"', attrs)
        fields.setdefault(name.group(1), []).append(value.group(1) if value else "")
    return fields


def model_rows(conn) -> dict[str, str]:
    """Every provider-model row. Filtered here, not with LIKE: `_` is LIKE's
    one-character wildcard, and `llm_model_%` also matches `llm_models_openai`."""
    with db.LOCK:
        rows = conn.execute("SELECT key, value FROM setting").fetchall()
    return {row["key"]: row["value"] for row in rows if row["key"].startswith(ai_ui.MODEL_SETTING_PREFIX)}


def test_settings_offers_each_providers_fetched_ids_as_a_select(client, conn, no_ollama):
    ai_ui.remember_models(conn, "ollama", ["gemma4:12b", "qwen3.5:4b"])

    body = client.get("/settings").text

    assert "gemma4:12b" in option_of(body, "model_ollama", "gemma4:12b")
    assert "qwen3.5:4b" in option_of(body, "model_ollama", "qwen3.5:4b")


def test_the_blank_option_is_selected_when_no_model_is_stored(client, conn, no_ollama):
    body = client.get("/settings").text

    blank = option_of(body, "model_openai", "")
    assert "selected" in blank
    assert "Its own default" in blank and "gpt-4o-mini" in blank


def test_a_saved_model_the_list_never_heard_of_is_still_the_selected_option(client, conn, no_ollama):
    ai_ui.setting_put(conn, ai_ui.MODEL_SETTING_PREFIX + "openai", "gpt-5.6")
    ai_ui.remember_models(conn, "openai", ["babbage-002", "gpt-4o-mini", "gpt-5.6-luna"])

    body = client.get("/settings").text

    kept = option_of(body, "model_openai", "gpt-5.6")
    assert "selected" in kept and "not in the fetched list" in kept
    assert "selected" not in option_of(body, "model_openai", "babbage-002")


def test_a_model_from_the_fetched_list_is_the_selected_option_after_a_save(
    client, conn, no_ollama
):
    """TASK-067: the in-list `selected` marker was never pinned.

    Its sibling above covers the model the list has never heard of, which is
    rendered as an extra pinned option. The ordinary case - a model the user
    picked out of the fetched list - had no test at all, so dropping the
    marker from the loop in _settings_llm.html would pass the suite. A select
    shows its first option for a value it lacks a marker for, and this form
    posts every provider's model on every save: the next Save would then store
    `babbage-002` for a person who chose `gpt-4o-mini`.
    """
    ai_ui.setting_put(conn, ai_ui.MODEL_SETTING_PREFIX + "openai", "gpt-4o-mini")
    ai_ui.remember_models(conn, "openai", ["babbage-002", "gpt-4o-mini", "gpt-5.6-luna"])

    body = client.get("/settings").text

    assert "selected" in option_of(body, "model_openai", "gpt-4o-mini")
    assert "selected" not in option_of(body, "model_openai", "babbage-002")
    # And what the browser would post next is that same model, not the first.
    assert browser_post(body, "/settings/llm")["model_openai"] == ["gpt-4o-mini"]


def test_saving_the_form_untouched_changes_no_model(client, conn, no_ollama):
    """The Save a person makes after ticking only "New recordings start
    private", posted the way a browser posts the page it was given. The stored
    gpt-5.6 survives, and a provider nobody set gets no row: before TASK-054
    the box carried each provider's shipped default, so this Save froze it
    into a row for every provider."""
    ai_ui.setting_put(conn, ai_ui.MODEL_SETTING_PREFIX + "openai", "gpt-5.6")
    ai_ui.remember_models(conn, "openai", ["babbage-002", "gpt-4o-mini", "gpt-5.6-luna"])
    fields = browser_post(client.get("/settings").text, "/settings/llm")
    fields["private_default"] = ["0", "1"]

    resp = client.post("/settings/llm", data=fields, headers=HX)

    assert resp.status_code == 200
    assert model_rows(conn) == {"llm_model_openai": "gpt-5.6"}


def test_a_typed_model_id_wins_over_the_dropdowns_pick(client, conn, no_ollama):
    client.post("/settings/llm", data={"model_openai": "gpt-4o-mini", "custom_model_openai": "gpt-5.7"}, headers=HX)

    assert ai_ui.default_model(conn, "openai") == "gpt-5.7"


def test_a_provider_without_a_fetched_list_still_takes_a_typed_model(client, conn, no_ollama):
    body = client.get("/settings").text
    assert re.search(r'<input[^>]*name="custom_model_ollama"', body)

    client.post("/settings/llm", data={"model_ollama": "", "custom_model_ollama": "llama9:70b"}, headers=HX)

    assert ai_ui.default_model(conn, "ollama") == "llama9:70b"


def test_the_blank_option_drops_the_row_back_to_the_providers_own_default(client, conn, no_ollama):
    ai_ui.setting_put(conn, ai_ui.MODEL_SETTING_PREFIX + "openai", "gpt-5.6")

    client.post("/settings/llm", data={"model_openai": "", "custom_model_openai": ""}, headers=HX)

    assert ai_ui.setting_get(conn, ai_ui.MODEL_SETTING_PREFIX + "openai") is None


def test_openrouter_ids_are_grouped_by_vendor_and_flat_ids_are_not():
    assert ai_ui.model_groups(["anthropic/claude-x", "openai/gpt-y", "openai/gpt-z"]) == [
        ("anthropic", ["anthropic/claude-x"]),
        ("openai", ["openai/gpt-y", "openai/gpt-z"]),
    ]
    assert ai_ui.model_groups(["babbage-002", "gpt-4o-mini"]) is None
    assert ai_ui.model_groups(["gpt-4o", "openrouter/auto"]) is None  # one id without a vendor: flat
    assert ai_ui.model_groups([]) is None


def test_grouped_ids_render_in_optgroups_with_the_whole_id_as_the_label(client, conn, no_ollama):
    ai_ui.remember_models(conn, "openrouter", ["anthropic/claude-x", "openai/gpt-y"])

    body = client.get("/settings").text

    select = re.search(r'<select[^>]*name="model_openrouter"[^>]*>(.*?)</select>', body, re.S).group(1)
    assert '<optgroup label="anthropic">' in select
    assert ">anthropic/claude-x</option>" in option_of(body, "model_openrouter", "anthropic/claude-x")


# --- testing a provider, and what a new recording starts as ---------------------------


def row_of(body: str, provider: str) -> str:
    """The provider's whole `<tr>` in the settings table."""
    found = re.search(
        rf'<tr[^>]*data-provider="{re.escape(provider)}".*?</tr>', body, re.DOTALL
    )
    assert found, f"no settings row for {provider!r}"
    return found.group(0)


def test_the_test_form_says_it_saves_nothing(client, conn, no_ollama):
    """TASK-074: app.js marks the pressed button 'Saved' after any successful
    settings POST, and a provider test queues a job and saves nothing. The
    form says so, and app.js leaves a form that says so alone."""
    body = client.post("/settings/llm/ollama/test", headers=HX).text

    assert re.search(
        r'<form[^>]*action="/settings/llm/ollama/test"[^>]*data-saves-nothing', body
    ), "the test form does not say it saves nothing"


def test_pressing_test_on_a_provider_queues_a_job_about_no_recording(client, conn, no_ollama):
    """The button's whole job. ADR-001: the web process writes a row and the
    runner child makes the call, exactly as a rail action does."""
    resp = client.post("/settings/llm/ollama/test", headers=HX)

    assert resp.status_code == 200
    rows = jobs_of(conn)
    assert len(rows) == 1
    assert rows[0]["media_id"] is None  # a probe is about no recording
    params = json.loads(rows[0]["params_json"])
    assert params["kind"] == selftest.KIND
    assert params["provider"] == "ollama"
    assert params["model"] == ai_ui.default_model(conn, "ollama")
    assert resp.headers.get("HX-Trigger") == "jobs-changed"


def test_pressing_test_asks_no_provider_anything_in_the_web_process(client, conn, monkeypatch):
    """The one line of ADR-001 this route could break. A provider that would
    raise if it were called proves the request never calls one."""

    def never(self, req):
        raise AssertionError("the web process completed a request")

    monkeypatch.setattr(ollama.OllamaProvider, "complete", never)
    # `tags` and not `models`: it is the method that opens the socket, and
    # `available()` reads the capability off those rows since TASK-089.06, so
    # stopping `models` no longer stops the request this test's own render
    # makes - it reached this machine's daemon instead.
    monkeypatch.setattr(
        ollama.OllamaProvider, "tags", lambda self: (_ for _ in ()).throw(
            base.Unreachable("not running")
        )
    )

    assert client.post("/settings/llm/ollama/test", headers=HX).status_code == 200
    assert len(jobs_of(conn)) == 1


def test_the_jobs_board_shows_a_test_that_is_about_no_recording(client, conn, no_ollama):
    """A provider test is the first job in this app with no media row, and every
    page that lists jobs joins one. The lesson of the action-items panel is that
    a feature whose own route answers 200 can still take another page down."""
    client.post("/settings/llm/ollama/test", headers=HX)
    job_id = jobs_of(conn)[0]["id"]

    board = client.get("/jobs")
    detail = client.get(f"/jobs/{job_id}")

    assert board.status_code == 200, board.text[:400]
    assert detail.status_code == 200, detail.text[:400]
    assert client.get("/").status_code == 200
    assert selftest.KIND in detail.text
    assert "built-in method" not in detail.text


def test_a_second_press_waits_for_the_first_test_rather_than_queueing_another(
    client, conn, no_ollama
):
    client.post("/settings/llm/ollama/test", headers=HX)

    body = client.post("/settings/llm/ollama/test", headers=HX).text

    assert len(jobs_of(conn)) == 1
    assert "already" in body


def test_each_provider_gets_its_own_test(client, conn, no_ollama):
    client.post("/settings/llm/ollama/test", headers=HX)
    client.post("/settings/llm/openai/test", headers=HX)

    queued = [json.loads(row["params_json"])["provider"] for row in jobs_of(conn)]
    assert sorted(queued) == ["ollama", "openai"]


def test_an_unknown_provider_cannot_be_tested(client, conn):
    assert client.post("/settings/llm/deep-thought/test", headers=HX).status_code == 404
    assert jobs_of(conn) == []


def test_the_settings_row_says_how_the_last_test_went(client, conn, no_ollama):
    selftest.store_result(
        conn,
        selftest.ProbeResult(
            provider="ollama",
            model="qwen3.5:4b",
            ok=True,
            answer="ok",
            at=time.time(),
            elapsed_s=1.25,
            completion_tokens=1,
        ),
    )

    row = row_of(client.get("/settings").text, "ollama")

    assert "answered" in row
    assert "qwen3.5:4b" in row


def test_a_failed_test_says_why_in_the_row_it_belongs_to(client, conn, no_ollama):
    selftest.store_result(
        conn,
        selftest.ProbeResult(
            provider="openrouter",
            model="anthropic/claude-3.5-haiku",
            ok=False,
            detail="no endpoints found for anthropic/claude-3.5-haiku",
            at=time.time(),
        ),
    )

    body = client.get("/settings").text

    assert "no endpoints found" in row_of(body, "openrouter")
    assert "no endpoints found" not in row_of(body, "ollama")


def test_a_running_test_says_so_in_its_row(client, conn, no_ollama):
    client.post("/settings/llm/openai/test", headers=HX)

    row = row_of(client.get("/settings").text, "openai")

    assert "Testing" in row


def test_the_private_default_round_trips_through_the_ai_settings(client, conn, no_ollama):
    """A checkbox that changed nothing was the gap this closes: `media.py` reads
    the row when it inserts, so this is the whole path."""
    client.post("/settings/llm", data={"provider": "ollama", "private_default": "1"}, headers=HX)
    assert scribe_media.private_default(conn) is True

    client.post("/settings/llm", data={"provider": "ollama", "private_default": "0"}, headers=HX)
    assert scribe_media.private_default(conn) is False


def test_the_private_default_shows_the_way_it_is_stored(client, conn, no_ollama):
    assert "checked" not in _private_field(client.get("/settings").text)

    client.post("/settings/llm", data={"private_default": "1"}, headers=HX)

    assert "checked" in _private_field(client.get("/settings").text)


def _private_field(body: str) -> str:
    found = re.search(r'<input[^>]*type="checkbox"[^>]*name="private_default"[^>]*>', body)
    assert found, "no private-mode default checkbox on the settings page"
    return found.group(0)


def test_a_recording_added_while_the_default_is_on_opens_with_the_cloud_disabled(
    client, conn, no_ollama, tmp_path
):
    """The end of the path the setting exists for: ticked, ingested, and the
    recording's own panel already refuses to leave the machine."""
    client.post("/settings/llm", data={"private_default": "1"}, headers=HX)
    source = tmp_path / "quiet.wav"
    source.write_bytes(b"a quiet meeting")
    row = scribe_media.ingest_path(conn, source)
    seed_run(conn, row["id"])

    body = client.get(f"/media/{row['id']}").text

    assert "disabled" in option_of(body, "provider", "openrouter")
    assert "disabled" not in option_of(body, "provider", "ollama")


# --- who is speaking: suggestions, then names -------------------------------------------


def test_the_speakers_answer_offers_a_form_that_writes_nothing_until_it_is_sent(client, conn, media):
    content, _ = sample_answer("speakers")
    store_output(conn, media, "speakers", content)

    body = client.get(f"/media/{media}").text

    section = body[body.index('id="ai-speakers"'):]
    section = section[: section.index("</section>")]
    assert f'action="/media/{media}/speakers/apply"' in section
    # An unnamed cluster with a suggestion starts ticked; the label the run
    # does not have is not offered at all.
    assert 'name="apply" value="SPEAKER_00" checked' in section
    assert 'name="name:SPEAKER_00" value="Arthur"' in section
    assert "now “Speaker 1”" in section
    assert "SPEAKER_09" not in section
    assert "1 suggestion(s) named a label this transcript does not have" in section
    assert speaker_names(conn, media) == {}


def test_applying_the_ticked_suggestions_names_only_those(client, conn, media):
    content, _ = sample_answer("speakers")
    store_output(conn, media, "speakers", content)

    resp = client.post(
        f"/media/{media}/speakers/apply",
        data={"apply": ["SPEAKER_00"], "name:SPEAKER_00": "Arthur Dent", "name:SPEAKER_01": "Marvin"},
        headers=HX,
    )

    assert resp.status_code == 200
    assert resp.headers.get("HX-Trigger") == "speakers-applied"
    assert speaker_names(conn, media) == {"SPEAKER_00": "Arthur Dent"}
    assert "Arthur Dent" in resp.text  # the transcript panel, re-rendered

    # A cluster already named by a person starts unticked next time.
    body = client.get(f"/media/{media}").text
    section = body[body.index('id="ai-speakers"'):]
    assert 'name="apply" value="SPEAKER_00" checked' not in section
    assert 'name="apply" value="SPEAKER_01" checked' in section
    assert "now “Arthur Dent”" in section


def test_applying_a_label_the_run_does_not_have_is_a_404_and_writes_nothing(client, conn, media):
    resp = client.post(
        f"/media/{media}/speakers/apply",
        data={"apply": ["SPEAKER_09"], "name:SPEAKER_09": "Nobody"},
        headers=HX,
    )
    assert resp.status_code == 404
    assert speaker_names(conn, media) == {}


def speaker_names(conn, media_id) -> dict[str, str]:
    rows = conn.execute(
        "SELECT l.cluster_label, l.display_name FROM speaker_label l"
        " JOIN run r ON r.id = l.run_id WHERE r.media_id=? ORDER BY l.cluster_label",
        (media_id,),
    ).fetchall()
    return {r["cluster_label"]: r["display_name"] for r in rows}


# --- what is actually in force (TASK-040.06) ---------------------------------------


def test_settings_states_what_actually_answers_a_question(client, conn):
    """Three controls and the knowledge that a question can override the
    default is not an answer to "is this leaving my machine?"."""
    from scribe.web import ai_ui

    ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "ollama")

    page = client.get("/settings").text

    assert "In effect now:" in page
    assert "Nothing leaves this machine." in page


def test_a_cloud_provider_says_the_transcript_leaves(client, conn):
    from scribe.web import ai_ui

    ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "openrouter")

    page = client.get("/settings").text

    assert "sends transcript text off this machine" in page


def test_a_window_that_cannot_be_worked_out_is_simply_not_shown(conn, monkeypatch):
    """A daemon that is down must cost a settings page nothing."""
    from scribe.llm import tasks
    from scribe.web import ai_ui

    ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "ollama")
    monkeypatch.setattr(tasks, "context_tokens_for", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))

    row = ai_ui.effective_llm(conn)

    assert row["window"] is None and row["provider"] == "ollama"


# --- ADR-016: with nobody chosen, the panel asks instead of picking -------------------
#
# The row is `llm_provider` and none of these write it. What they assert is
# what a machine where nobody answered the provider question actually shows and
# actually sends: a placeholder rather than a provider, a sentence a reader can
# see without opening anything, and no `job` row.

SETTINGS_LINK = "/settings#llm-providers"


def test_the_panel_preselects_no_provider_when_nobody_has_chosen(client, conn, media):
    """The assertion is that the placeholder carries `selected`, not that
    openrouter does not: a browser picks the first option when none is marked,
    so "openrouter is not selected" would pass on a page that still shows a
    chosen provider."""
    body = client.get(f"/media/{media}").text

    assert "selected" in option_of(body, "provider", "")
    assert "selected" not in option_of(body, "provider", "openrouter")


def test_the_panel_says_choose_a_provider_where_a_reader_can_see_it(client, conn, media):
    """Outside `<details class="options">`, which arrives closed: a sentence
    that only shows when you go looking is not a sentence anybody reads."""
    body = client.get(f"/media/{media}").text
    above_the_fold = body.split('<details class="options"')[0]

    assert "choose a provider" in above_the_fold.lower()
    assert SETTINGS_LINK in above_the_fold


def test_a_transcript_page_still_renders_when_the_stored_provider_is_gone(client, conn, media):
    """A provider a later version removed leaves its row behind."""
    ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "a-provider-this-version-does-not-have")

    resp = client.get(f"/media/{media}")

    assert resp.status_code == 200
    assert "selected" in option_of(resp.text, "provider", "")


def test_asking_with_nobody_chosen_writes_no_job_and_says_where_to_choose(client, conn, media):
    resp = client.post(f"/media/{media}/ai/summary", headers=HX)

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "choose a provider" in detail.lower()
    assert SETTINGS_LINK in detail
    # This screen does carry a select, so the refusal names the second way out.
    # The bulk action's notice must not: see tests/test_web_library.py.
    assert "for this request" in detail.lower()
    assert jobs_of(conn) == []


def test_a_provider_picked_in_the_panel_is_somebody_choosing(client, conn, media, no_ollama):
    """The request names one, so this request has an answer even though the
    row does not (ADR-016: the panel's select for that one request)."""
    resp = client.post(f"/media/{media}/ai/summary", data={"provider": "ollama"}, headers=HX)

    assert resp.status_code == 200
    (job,) = jobs_of(conn)
    assert json.loads(job["params_json"])["provider"] == "ollama"


def test_the_chat_page_preselects_no_provider_and_says_where_to_choose(client, conn, media):
    body = client.get(f"/media/{media}/chat").text

    assert "selected" in option_of(body, "provider", "")
    assert "choose a provider" in body.lower()
    assert SETTINGS_LINK in body


def test_a_chat_turn_with_nobody_chosen_writes_no_job_and_says_where_to_choose(
    client, conn, media
):
    resp = client.post(f"/media/{media}/chat", data={"question": "Who is speaking?"}, headers=HX)

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "choose a provider" in detail.lower()
    assert SETTINGS_LINK in detail
    assert "for this request" in detail.lower()
    assert jobs_of(conn) == []


def test_a_chat_turn_that_names_a_provider_is_somebody_choosing(client, conn, media, no_ollama):
    resp = client.post(
        f"/media/{media}/chat",
        data={"question": "Who is speaking?", "provider": "ollama"},
        headers=HX,
    )

    assert resp.status_code == 200
    (job,) = jobs_of(conn)
    assert json.loads(job["params_json"])["provider"] == "ollama"


def test_the_settings_page_preselects_no_provider_and_says_nothing_is_sent(
    client, conn, no_ollama
):
    body = client.get("/settings").text

    assert "selected" in option_of(body, "provider", "")
    assert "No provider is chosen" in body


def test_the_effective_line_says_no_provider_rather_than_an_empty_label(conn):
    """`effective_llm` answers the settings page's "is this leaving my
    machine?" line, and an empty label there reads as a provider with no name."""
    row = ai_ui.effective_llm(conn)

    assert row["provider"] == ""
    assert "no provider chosen" in row["label"].lower()
    assert row["local"] is False
