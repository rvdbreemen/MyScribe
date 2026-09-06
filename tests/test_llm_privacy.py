"""The fail-closed private-mode pin (Phase 5 Task 2).

Private mode is the one promise in this phase that has to be enforceable
rather than aspirational, so these tests are written to fail if the check is
*removed*, not merely if it is wrong. Two things make that true:

* every refusal is exercised through `llm.chat()` - the door callers actually
  walk through - and never by calling `assert_allowed` directly, so deleting
  the call site has somewhere to show up;
* the provider handed to `chat()` has a working key and a fake transport that
  would answer happily, so a deleted check produces a *successful completion*,
  and `rec.calls == 0` is the assertion that catches a check which merely ran
  too late.

Deleting the `assert_allowed` line from `llm.chat()` was tried while this was
written; the result is recorded in the commit message.
"""

from __future__ import annotations

import json

import httpx2
import openai
import pytest

from scribe import db, llm
from scribe.llm import base, privacy
from tests.seed import seed_media

# --- a transport that answers, and remembers whether it was asked ------------------


class Recorder:
    def __init__(self, response):
        self.response = response
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return self.response

    @property
    def calls(self) -> int:
        return len(self.requests)


COMPLETION = httpx2.Response(
    200,
    json={
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "leaked"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    },
)
"""A perfectly good answer. If the pin is removed, the test that asked for a
private media gets this back instead of an exception - which is the whole
point of making the happy path available to a call that must never make it."""

OLLAMA_ANSWER = httpx2.Response(
    200,
    json={
        "model": "qwen3.5:4b",
        "message": {"role": "assistant", "content": "kept at home"},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 3,
        "eval_count": 3,
    },
)


def cloud_factory(rec):
    """A real OpenAI SDK client on a fake socket (both cloud providers use it)."""

    def factory(*, base_url, api_key, default_headers, timeout):
        return openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(rec)),
        )

    return factory


def local_factory(rec):
    def factory(*, base_url, timeout):
        return httpx2.Client(base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(rec))

    return factory


REQUEST = base.ChatRequest(
    system="Summarise this transcript.",
    user="The witness named her doctor and her diagnosis.",
    model="m",
)

CLOUD = sorted(name for name, cls in llm.PROVIDERS.items() if not cls.is_local)
LOCAL = sorted(name for name, cls in llm.PROVIDERS.items() if cls.is_local)


# --- fixtures --------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


def folder(conn, name, parent_id=None, *, private=False):
    with db.LOCK:
        row = conn.execute(
            "INSERT INTO folder(name, parent_id, private) VALUES (?, ?, ?) RETURNING id",
            (name, parent_id, 1 if private else 0),
        ).fetchone()
        conn.commit()
    return row["id"]


def pin(conn, media_id, private=True):
    with db.LOCK:
        conn.execute("UPDATE media SET private=? WHERE id=?", (1 if private else 0, media_id))
        conn.commit()


def cloud_chat(conn, media_id, rec, provider_name="openrouter"):
    """`llm.chat` at a cloud provider that would answer if it were allowed to."""
    return llm.chat(
        conn,
        media_id=media_id,
        provider_name=provider_name,
        request=REQUEST,
        client_factory=cloud_factory(rec),
        api_key="test-key",
    )


# --- the pin itself ----------------------------------------------------------------------


def test_the_refusal_happens_before_the_transport_is_ever_touched(conn):
    """The load-bearing test of the whole phase.

    The provider is fully able to answer: a resolvable key, a transport that
    returns a 200 with text in it. The only thing standing between a private
    transcript and the wire is the pin. Delete `assert_allowed` from
    `llm.chat()` and this test reports a `ChatResponse` where it demanded an
    exception, with `rec.calls == 1` where it demanded 0.
    """
    media_id = seed_media(conn, title="Therapy session")
    pin(conn, media_id)
    rec = Recorder(COMPLETION)

    with pytest.raises(privacy.PrivacyRefused):
        cloud_chat(conn, media_id, rec)

    assert rec.calls == 0, "a private transcript reached the network"


def test_a_private_media_refuses_every_cloud_provider(conn):
    """Read off the registry, not a hard-coded pair: a provider added in a
    later task is covered by this test the moment it is registered."""
    media_id = seed_media(conn)
    pin(conn, media_id)
    assert CLOUD, "no cloud providers registered - this test would pass vacuously"

    for name in CLOUD:
        rec = Recorder(COMPLETION)
        with pytest.raises(privacy.PrivacyRefused):
            cloud_chat(conn, media_id, rec, provider_name=name)
        assert rec.calls == 0, f"{name} was contacted for a private media"


def test_a_private_media_still_reaches_a_local_provider(conn):
    """Private mode is not "no AI" - it is "no AI that leaves the machine"."""
    media_id = seed_media(conn)
    pin(conn, media_id)
    assert LOCAL, "no local provider registered - private mode would mean no AI at all"
    rec = Recorder(OLLAMA_ANSWER)

    answer = llm.chat(
        conn,
        media_id=media_id,
        provider_name="ollama",
        request=REQUEST,
        client_factory=local_factory(rec),
    )

    assert answer.text == "kept at home"
    assert rec.calls == 1


def test_unpinning_restores_cloud_access(conn):
    media_id = seed_media(conn)
    pin(conn, media_id)
    rec = Recorder(COMPLETION)
    with pytest.raises(privacy.PrivacyRefused):
        cloud_chat(conn, media_id, rec)

    pin(conn, media_id, private=False)
    answer = cloud_chat(conn, media_id, rec)

    assert answer.text == "leaked"  # the same call, now allowed
    assert rec.calls == 1


def test_a_privacy_refusal_is_not_retried(conn):
    """`with_retry` exists for transient failures. A refusal is a decision, and
    three attempts at a decision is three chances for one of them to slip.

    Driven straight at `with_retry` rather than through `chat()`: in `chat()`
    the refusal happens before the retry loop is entered, so a call count of
    zero there would prove nothing about the retry policy. This version stays
    honest even if someone adds `PrivacyRefused` to `RETRYABLE`.
    """
    attempts: list[int] = []

    def refuse():
        attempts.append(1)
        raise privacy.PrivacyRefused("nope")

    with pytest.raises(privacy.PrivacyRefused):
        base.with_retry(refuse, attempts=3, sleep=lambda _s: None)

    assert len(attempts) == 1

    # And end to end: no request is made, once.
    media_id = seed_media(conn)
    pin(conn, media_id)
    rec = Recorder(COMPLETION)
    with pytest.raises(privacy.PrivacyRefused):
        cloud_chat(conn, media_id, rec)
    assert rec.calls == 0


def test_a_privacy_refusal_is_not_an_llm_error(conn):
    """Structural, and deliberate: `LlmError` is the family a caller may answer
    with "retry" or "try another provider" (`base.retarget` exists for exactly
    that). A privacy refusal must never be reachable from such a handler, so it
    sits outside that tree where no `except LlmError` can swallow it."""
    assert not issubclass(privacy.PrivacyRefused, base.LlmError)

    media_id = seed_media(conn)
    pin(conn, media_id)
    with pytest.raises(privacy.PrivacyRefused):
        try:
            cloud_chat(conn, media_id, Recorder(COMPLETION))
        except base.LlmError:  # a plausible fallback handler
            pytest.fail("a privacy refusal was caught as an LLM failure")


# --- what counts as private ----------------------------------------------------------------


def test_a_media_in_a_private_folder_is_private(conn):
    private = folder(conn, "Clients", private=True)
    media_id = seed_media(conn, folder_id=private)

    assert privacy.is_private(conn, media_id) is True
    rec = Recorder(COMPLETION)
    with pytest.raises(privacy.PrivacyRefused):
        cloud_chat(conn, media_id, rec)
    assert rec.calls == 0


def test_a_media_in_a_nested_private_folder_is_private(conn):
    """The pin is inherited all the way down: pinning a folder has to mean
    something to the file three levels inside it, or "pin the folder" is advice
    rather than a control."""
    top = folder(conn, "Work", private=True)
    middle = folder(conn, "2026", top)
    bottom = folder(conn, "Q3", middle)
    media_id = seed_media(conn, folder_id=bottom)

    assert privacy.is_private(conn, media_id) is True
    rec = Recorder(COMPLETION)
    with pytest.raises(privacy.PrivacyRefused):
        cloud_chat(conn, media_id, rec)
    assert rec.calls == 0


def test_a_public_media_in_a_public_tree_is_not_private(conn):
    top = folder(conn, "Podcast")
    inner = folder(conn, "Season 1", top)
    media_id = seed_media(conn, folder_id=inner)

    assert privacy.is_private(conn, media_id) is False
    assert cloud_chat(conn, media_id, Recorder(COMPLETION)).text == "leaked"


def test_a_media_with_no_folder_is_judged_by_its_own_flag(conn):
    """Uncategorized has no folder row to inherit from; the walk must answer
    from the media itself rather than falling off the end."""
    media_id = seed_media(conn, folder_id=None)
    assert privacy.is_private(conn, media_id) is False

    pin(conn, media_id)
    assert privacy.is_private(conn, media_id) is True


def test_a_private_media_in_a_public_folder_stays_private(conn):
    public = folder(conn, "Inbox")
    media_id = seed_media(conn, folder_id=public)
    pin(conn, media_id)

    assert privacy.is_private(conn, media_id) is True


def test_an_unknown_media_is_an_error_rather_than_permission_to_send(conn):
    """The one answer that must never be given for a row that is not there is
    "not private". A caller asking about a media that does not exist has a bug;
    a bug must not read as consent."""
    with pytest.raises(LookupError):
        privacy.is_private(conn, 4242)

    with pytest.raises(LookupError):
        cloud_chat(conn, 4242, Recorder(COMPLETION))


def test_a_cycle_in_the_folder_tree_cannot_hang_the_walk(conn):
    """A parent pointing back at its own descendant is only reachable via a bug
    or a hand-edited database, but an unbounded recursive walk would spin
    forever inside a request. The bound is on depth, not on trust."""
    a = folder(conn, "A")
    b = folder(conn, "B", a)
    with db.LOCK:
        conn.execute("UPDATE folder SET parent_id=? WHERE id=?", (b, a))
        conn.commit()
    media_id = seed_media(conn, folder_id=b)

    assert privacy.is_private(conn, media_id) is False


def test_a_private_folder_in_a_cycle_is_still_found(conn):
    a = folder(conn, "A", private=True)
    b = folder(conn, "B", a)
    with db.LOCK:
        conn.execute("UPDATE folder SET parent_id=? WHERE id=?", (b, a))
        conn.commit()

    assert privacy.is_private(conn, seed_media(conn, folder_id=b)) is True


# --- assert_allowed on its own ---------------------------------------------------------------


def test_assert_allowed_reads_is_local_and_takes_a_class_or_an_instance(conn):
    """It is checked against the provider *class*, before an instance (and its
    client) exists - so "before any request object is built" is structural
    rather than a claim about call order."""
    media_id = seed_media(conn)
    pin(conn, media_id)

    with pytest.raises(privacy.PrivacyRefused) as exc:
        privacy.assert_allowed(conn, media_id, llm.PROVIDERS["openrouter"])
    assert "openrouter" in str(exc.value)

    privacy.assert_allowed(conn, media_id, llm.PROVIDERS["ollama"])
    privacy.assert_allowed(conn, media_id, llm.get_provider("ollama", conn))


def test_the_refusal_message_says_nothing_about_the_transcript(conn):
    """The message reaches a jobs board and a bug report. It may name the media
    and the provider; it may not carry the text the pin exists to protect."""
    media_id = seed_media(conn, title="Therapy session")
    pin(conn, media_id)

    with pytest.raises(privacy.PrivacyRefused) as exc:
        cloud_chat(conn, media_id, Recorder(COMPLETION))

    assert REQUEST.user not in str(exc.value)
    assert REQUEST.system not in str(exc.value)


# --- chat() itself ------------------------------------------------------------------------------


def test_chat_refuses_an_unknown_provider_before_reading_the_pin(conn):
    with pytest.raises(ValueError):
        llm.chat(conn, media_id=seed_media(conn), provider_name="gpt-9000", request=REQUEST)


def test_chat_retries_a_transient_failure_and_returns_the_answer(conn):
    """`chat()` is the seam's front door, so the retry policy lives behind it
    rather than in every caller."""
    media_id = seed_media(conn)
    calls: list[httpx2.Request] = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx2.Response(429, json={"error": {"message": "slow down"}})
        return COMPLETION

    answer = llm.chat(
        conn,
        media_id=media_id,
        provider_name="openrouter",
        request=REQUEST,
        client_factory=cloud_factory(flaky),
        api_key="test-key",
        sleep=lambda _s: None,
    )

    assert answer.text == "leaked"
    assert len(calls) == 2


def test_the_private_column_defaults_to_public_for_existing_rows(conn):
    """Turning the pin on is a decision someone makes; a library that predates
    the column is not silently pinned, and not silently exposed either - it is
    what it was."""
    media_id = seed_media(conn)
    with db.LOCK:
        row = conn.execute("SELECT private FROM media WHERE id=?", (media_id,)).fetchone()
    assert row["private"] == 0
    assert privacy.is_private(conn, media_id) is False
