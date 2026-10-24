"""A preset run composed with each real provider, on a fake socket (Phase 5).

The provider suites prove each transport in isolation; the task suite proves
the planning, chunking and schema validation against a fake `Provider`.
Neither proves the join - that `run_task` and a *real* provider class agree on
how an answer comes back - and the join is where a provider's own response
shape would break a preset. This covers it for all three, without a key, a
bill or a socket: `client_factory` is the seam and `run_task(**provider_kwargs)`
hands it through to the constructor.
"""

from __future__ import annotations

import json

import httpx2
import openai
import pytest

from scribe import db
from scribe.llm import tasks
from tests.seed import seed_media, seed_run

# What the model "answers". Valid against the summary schema, so a provider
# that mangled the text on the way back fails validation rather than storing it.
SUMMARY = json.dumps(
    {
        "paragraph": "Two travellers discuss towels and the answer to everything.",
        "bullets": ["A towel is the most useful thing", "The answer is forty-two"],
    }
)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def media(conn):
    """A media with the default four-sentence transcript."""
    media_id = seed_media(conn, title="Guide")
    seed_run(conn, media_id)
    return media_id


class Sent:
    """Every request a provider actually put on the wire."""

    def __init__(self):
        self.requests: list[httpx2.Request] = []

    def record(self, request: httpx2.Request) -> None:
        self.requests.append(request)

    def chat_body(self) -> dict:
        """The first request carrying messages.

        Ollama's availability check hits `/api/tags` first, so the chat call is
        not reliably request zero.
        """
        for request in self.requests:
            payload = json.loads(request.content)
            if "messages" in payload:
                return payload
        raise AssertionError("no chat request was ever sent")


def cloud_answer() -> httpx2.Response:
    """An OpenAI-shaped completion carrying `SUMMARY`."""
    return httpx2.Response(
        200,
        json={
            "id": "cmpl-1",
            "object": "chat.completion",
            "created": 1,
            "model": "m",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": SUMMARY},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 30, "total_tokens": 70},
        },
    )


def cloud_factory(handler):
    """An OpenAI client whose every request goes to `handler`."""

    def factory(*, base_url, api_key, default_headers, timeout):
        return openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )

    return factory


def cloud_wiring(sent: Sent) -> dict:
    """Constructor kwargs putting an OpenAI-shaped provider on a fake socket."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.record(request)
        return cloud_answer()

    # The key is supplied here so the test never depends on the environment,
    # the setting table or the registry - those paths have their own tests.
    return {"client_factory": cloud_factory(handler), "api_key": "test-key"}


def ollama_wiring(sent: Sent) -> dict:
    """Constructor kwargs putting the local provider on a fake socket."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.record(request)
        if request.url.path == "/api/tags":
            return httpx2.Response(200, json={"models": [{"name": "m", "model": "m"}]})
        return httpx2.Response(
            200,
            json={
                "model": "m",
                "created_at": "2026-09-03T09:00:00.0000000Z",
                "message": {"role": "assistant", "content": SUMMARY},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": 40,
                "eval_count": 30,
            },
        )

    def factory(*, base_url, timeout):
        return httpx2.Client(
            base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(handler)
        )

    return {"client_factory": factory}


WIRING = {"openai": cloud_wiring, "openrouter": cloud_wiring, "ollama": ollama_wiring}


@pytest.mark.parametrize("provider_name", sorted(WIRING))
def test_a_summary_runs_through_each_real_provider_and_stores_a_valid_row(
    conn, media, provider_name
):
    sent = Sent()

    output_id = tasks.run_task(
        conn,
        media_id=media,
        kind="summary",
        provider_name=provider_name,
        model="m",
        **WIRING[provider_name](sent),
    )

    row = conn.execute("select * from llm_output where id = ?", (output_id,)).fetchone()
    assert row["kind"] == "summary"
    assert row["provider"] == provider_name
    assert row["model"] == "m"
    # The stored content is valid for its kind, so the provider handed back
    # what the schema expects rather than something the row merely holds.
    tasks.TASKS["summary"].schema.model_validate_json(row["content"])


@pytest.mark.parametrize("provider_name", sorted(WIRING))
def test_each_real_provider_carries_the_transcript_to_the_model(conn, media, provider_name):
    """The join is only proved if the words actually left with the request."""
    sent = Sent()

    tasks.run_task(
        conn,
        media_id=media,
        kind="summary",
        provider_name=provider_name,
        model="m",
        **WIRING[provider_name](sent),
    )

    prompt = json.dumps(sent.chat_body())
    assert "forty-two" in prompt
    assert "Vogon poetry" in prompt


NO_REASONING = {
    "openai": ("reasoning_effort", "none"),
    "openrouter": ("reasoning", {"enabled": False}),
    "ollama": ("think", False),
}
"""What each real provider puts on the wire for TASK-029's hint."""

PLAIN_REASONING = {"openrouter": {"reasoning": {"max_tokens": 1600}}}
"""What a call without the hint carries instead (TASK-123)."""


@pytest.mark.parametrize("provider_name", sorted(WIRING))
def test_cleanup_reaches_each_real_provider_with_its_own_no_reasoning_field(
    conn, media, provider_name
):
    """The join for the hint: `TaskSpec.reasoning_off` through `_ask`,
    `llm.chat` and the real provider class, onto the body that leaves. A kind
    without the hint sends none of the three fields, except OpenRouter's
    reasoning limit (TASK-123)."""
    sent = Sent()
    tasks.run_task(
        conn,
        media_id=media,
        kind="cleanup",
        provider_name=provider_name,
        model="m",
        **WIRING[provider_name](sent),
    )

    body = sent.chat_body()
    key, value = NO_REASONING[provider_name]
    assert body[key] == value
    others = [k for k, _ in NO_REASONING.values() if k != key]
    assert [k for k in others if k in body] == []

    plain = Sent()
    tasks.run_task(
        conn,
        media_id=media,
        kind="summary",
        provider_name=provider_name,
        model="m",
        **WIRING[provider_name](plain),
    )
    # Without the hint OpenRouter carries its reasoning limit, a share of the
    # 4,000-token summary cap (TASK-123); the others send nothing.
    sent_plain = {k: plain.chat_body()[k] for k, _ in NO_REASONING.values() if k in plain.chat_body()}
    assert sent_plain == PLAIN_REASONING.get(provider_name, {})


def test_a_cleanup_whose_hint_openrouter_refuses_goes_again_with_no_reasoning_limit(conn, media):
    """ADR-030: the reasoning limit rides only on calls that ask no hint, and
    cleanup asks the hint on every call. When an upstream refuses it with a
    400 (job 426's mandatory reasoner), the call without it is the request
    ADR-020 describes - no `reasoning` object at all, not the limit in its
    place: a share near OpenRouter's effort levels may map to `low`, which on
    media 1 dropped the [m:ss] stamps and the speaker labels."""
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        if len(bodies) == 1:
            return httpx2.Response(
                400,
                json={"error": {"message": "Reasoning is mandatory for this endpoint and cannot be disabled."}},
            )
        return cloud_answer()

    tasks.run_task(
        conn,
        media_id=media,
        kind="cleanup",
        provider_name="openrouter",
        model="m",
        client_factory=cloud_factory(handler),
        api_key="test-key",
    )

    assert len(bodies) == 2
    assert bodies[0]["reasoning"] == {"enabled": False}
    assert "reasoning" not in bodies[1], "the call without the hint carries no limit either"
