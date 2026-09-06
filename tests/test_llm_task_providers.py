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


def cloud_wiring(sent: Sent) -> dict:
    """Constructor kwargs putting an OpenAI-shaped provider on a fake socket."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.record(request)
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

    def factory(*, base_url, api_key, default_headers, timeout):
        return openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )

    # The key is supplied here so the test never depends on the environment,
    # the setting table or the registry - those paths have their own tests.
    return {"client_factory": factory, "api_key": "test-key"}


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
