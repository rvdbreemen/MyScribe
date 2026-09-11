"""The provider seam and the two cloud providers (Phase 5 Task 1).

Not one of these tests touches the network. Every request goes through an
`httpx2.MockTransport` handed to a real `openai.OpenAI` client, so the SDK's
own status-to-exception mapping runs for real and only the socket is fake -
which is the half that would otherwise cost a key, a bill and a flaky suite.
The live endpoints were probed by hand while this was written; what they
answered is recorded in the module docstrings of `scribe/llm/openai_like.py`,
not asserted here.
"""

from __future__ import annotations

import copy
import json

import httpx2
import openai
import pytest

from scribe import db
from scribe.llm import base, openai_like


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    db.migrate(c)
    yield c
    c.close()


# --- fake transport -------------------------------------------------------------


class Recorder:
    """A MockTransport handler that remembers every request it answered."""

    def __init__(self, *responses):
        # One response per call; the last one repeats once they run out, so a
        # retry test can hand over a single 429 and still get three of them.
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self.responses) - 1)
        answer = self.responses[index]
        return answer(request) if callable(answer) else answer

    @property
    def calls(self) -> int:
        return len(self.requests)

    def body(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)

    def header(self, name: str, index: int = 0) -> str | None:
        return self.requests[index].headers.get(name)


def fake_factory(handler):
    """A `client_factory` that builds a real SDK client on a fake socket."""

    def factory(*, base_url, api_key, default_headers, timeout):
        return openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers,
            max_retries=0,
            http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
        )

    return factory


def completion(
    text="hello",
    *,
    prompt=11,
    completion_tokens=2,
    finish="stop",
    details=None,
    upstream=None,
    reasoning_trace="absent",
):
    """A chat completion. `details` is `usage.completion_tokens_details`,
    `upstream` OpenRouter's top-level `provider`, and `reasoning_trace` the
    `message.reasoning` field ("absent" leaves the key out)."""
    message: dict = {"role": "assistant", "content": text}
    if reasoning_trace != "absent":
        message["reasoning"] = reasoning_trace
    usage: dict = {
        "prompt_tokens": prompt,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt + completion_tokens,
    }
    if details is not None:
        usage["completion_tokens_details"] = details
    payload: dict = {
        "id": "cmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage,
    }
    if upstream is not None:
        payload["provider"] = upstream
    return httpx2.Response(200, json=payload)


def error(status: int, message: str, code=None, param=None):
    body = {"error": {"message": message}}
    if code is not None:
        body["error"]["code"] = code
    if param is not None:
        body["error"]["param"] = param
    return httpx2.Response(status, json=body)


def provider(cls, handler, conn=None, *, key="test-key"):
    """`cls` wired to `handler`, with a key that resolves without a database."""
    return cls(conn, client_factory=fake_factory(handler), api_key=key)


REQUEST = base.ChatRequest(system="You are terse.", user="Capital of France?", model="m")


# --- a normal completion ---------------------------------------------------------


def test_completion_returns_text_and_token_counts():
    rec = Recorder(completion("Paris", prompt=22, completion_tokens=5))
    answer = provider(openai_like.OpenAIProvider, rec).complete(REQUEST)

    assert answer.text == "Paris"
    assert answer.provider == "openai"
    assert answer.model == "m"
    assert (answer.prompt_tokens, answer.completion_tokens) == (22, 5)
    assert answer.raw_finish_reason == "stop"
    assert rec.body()["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Capital of France?"},
    ]


def test_missing_usage_leaves_token_counts_none_rather_than_zero():
    rec = Recorder(
        httpx2.Response(
            200,
            json={
                "id": "1",
                "object": "chat.completion",
                "created": 1,
                "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}}],
            },
        )
    )
    answer = provider(openai_like.OpenAIProvider, rec).complete(REQUEST)
    assert (answer.prompt_tokens, answer.completion_tokens) == (None, None)


def test_models_lists_the_ids_the_endpoint_offers():
    rec = Recorder(
        httpx2.Response(
            200,
            json={"object": "list", "data": [{"id": "z/y", "object": "model"}, {"id": "a/b", "object": "model"}]},
        )
    )
    assert provider(openai_like.OpenRouterProvider, rec).models() == ["a/b", "z/y"]


# --- error mapping ------------------------------------------------------------------


def test_http_401_is_an_auth_error_and_is_never_retried():
    rec = Recorder(error(401, "Incorrect API key provided"))
    prov = provider(openai_like.OpenAIProvider, rec)

    with pytest.raises(base.AuthError):
        base.with_retry(lambda: prov.complete(REQUEST), attempts=3, sleep=lambda _s: None)

    assert rec.calls == 1


def test_http_429_is_rate_limited_and_retried_exactly_three_times():
    rec = Recorder(error(429, "slow down"))
    prov = provider(openai_like.OpenAIProvider, rec)
    slept: list[float] = []

    with pytest.raises(base.RateLimited):
        base.with_retry(lambda: prov.complete(REQUEST), attempts=3, sleep=slept.append)

    assert rec.calls == 3
    assert len(slept) == 2 and slept[1] > slept[0]  # exponential, not a busy loop


def test_a_200_carrying_an_error_envelope_is_a_bad_response():
    # OpenRouter answers some upstream failures with HTTP 200 and an error body;
    # the SDK builds a ChatCompletion with choices=None out of it and returns it.
    rec = Recorder(httpx2.Response(200, json={"error": {"message": "upstream exploded", "code": 500}}))

    with pytest.raises(base.BadResponse) as exc:
        provider(openai_like.OpenRouterProvider, rec).complete(REQUEST)

    assert "upstream exploded" in str(exc.value)


def test_a_200_with_no_choices_is_a_bad_response():
    rec = Recorder(
        httpx2.Response(200, json={"id": "1", "object": "chat.completion", "created": 1, "model": "m", "choices": []})
    )
    with pytest.raises(base.BadResponse):
        provider(openai_like.OpenAIProvider, rec).complete(REQUEST)


def test_a_404_naming_a_model_is_model_not_found_carrying_the_id():
    # Measured against the live API on 2026-09-02: anthropic/claude-3.5-haiku is
    # a plausible id that OpenRouter does not have, while claude-haiku-4.5 is.
    rec = Recorder(error(404, "No endpoints found for anthropic/claude-3.5-haiku."))
    request = base.ChatRequest(system="", user="hi", model="anthropic/claude-3.5-haiku")

    with pytest.raises(base.ModelNotFound) as exc:
        provider(openai_like.OpenRouterProvider, rec).complete(request)

    assert exc.value.model == "anthropic/claude-3.5-haiku"
    assert "anthropic/claude-3.5-haiku" in str(exc.value)


def test_a_context_length_400_is_context_too_long_not_a_bad_response():
    rec = Recorder(error(400, "maximum context length is 128000 tokens", code="context_length_exceeded"))
    with pytest.raises(base.ContextTooLong):
        provider(openai_like.OpenAIProvider, rec).complete(REQUEST)


def test_another_400_is_a_bad_response_and_is_not_retried():
    rec = Recorder(error(400, "Unsupported parameter: 'max_tokens' is not supported with this model."))
    prov = provider(openai_like.OpenAIProvider, rec)

    with pytest.raises(base.BadResponse):
        base.with_retry(lambda: prov.complete(REQUEST), attempts=3, sleep=lambda _s: None)

    assert rec.calls == 1


def test_a_400_refusing_temperature_is_retried_once_without_it():
    """gpt-5.6 on api.openai.com, 2026-09-06: 400 "Unsupported value:
    'temperature' does not support 0.0 with this model. Only the default (1)
    value is supported." The knob is gone on that model; the request is not
    wrong. Once more without it, and only once."""
    refusal = error(
        400,
        "Unsupported value: 'temperature' does not support 0.0 with this model. "
        "Only the default (1) value is supported.",
        code="unsupported_value",
    )
    rec = Recorder(refusal, completion("Paris"))
    prov = provider(openai_like.OpenAIProvider, rec)

    answer = prov.complete(base.ChatRequest(system="s", user="u", model="gpt-5.6", temperature=0.0))

    assert answer.text == "Paris"
    assert rec.calls == 2
    assert rec.body(0)["temperature"] == 0.0
    assert "temperature" not in rec.body(1)


def test_a_second_400_after_dropping_temperature_is_a_bad_response():
    refusal = error(400, "Unsupported value: 'temperature' does not support 0.0 with this model.")
    other = error(400, "Unsupported parameter: 'max_completion_tokens' is not supported.")
    rec = Recorder(refusal, other)
    with pytest.raises(base.BadResponse):
        provider(openai_like.OpenAIProvider, rec).complete(REQUEST)
    assert rec.calls == 2


# --- the reasoning hint (TASK-029) ---------------------------------------------------


def hinted() -> base.ChatRequest:
    """A request carrying the hint. Built per test rather than at import, so a
    seam without the field fails each test that needs it and not the module."""
    return base.ChatRequest(system="s", user="u", model="m", reasoning_off=True)


REASONING_KEYS = ("reasoning", "reasoning_effort")


@pytest.mark.parametrize(
    "cls, key, value",
    [
        (openai_like.OpenRouterProvider, "reasoning", {"enabled": False}),
        (openai_like.OpenAIProvider, "reasoning_effort", "none"),
    ],
    ids=["openrouter", "openai"],
)
def test_each_cloud_provider_says_no_reasoning_its_own_way(cls, key, value):
    """One hint on the seam, one wire field per provider, and no caller that
    knows which. Measured 2026-09-11: OpenRouter's `reasoning: {enabled:
    false}` took deepseek-v4-flash and gpt-5.6-luna to 0 reasoning tokens;
    api.openai.com's `reasoning_effort: "none"` did the same for gpt-5.6.

    Asserted on the JSON that left, not on the kwargs: OpenRouter's field goes
    through the SDK's `extra_body`, and a body with `extra_body` in it would be
    the SDK not doing its job."""
    with_hint = Recorder(completion())
    provider(cls, with_hint).complete(hinted())

    sent = with_hint.body()
    assert sent[key] == value
    assert [k for k in REASONING_KEYS if k != key and k in sent] == []
    assert "extra_body" not in sent

    plain = Recorder(completion())
    provider(cls, plain).complete(REQUEST)
    assert [k for k in REASONING_KEYS if k in plain.body()] == []


@pytest.mark.parametrize(
    "cls, refusal",
    [
        # google/gemini-3.5-flash-lite through OpenRouter, 2026-09-11: its
        # reasoning is mandatory (default effort minimal).
        (
            openai_like.OpenRouterProvider,
            error(400, "Reasoning is mandatory for this endpoint and cannot be disabled."),
        ),
        # gpt-4o-mini, OpenAIProvider.default_model, 2026-09-11.
        (
            openai_like.OpenAIProvider,
            error(400, "Unrecognized request argument supplied: reasoning_effort"),
        ),
        # gpt-5 and gpt-6-astra answered code unsupported_value. The wording
        # here is reconstructed from that code, and nothing reads it.
        (
            openai_like.OpenAIProvider,
            error(
                400,
                "Unsupported value: 'reasoning_effort' does not support 'none' with this model.",
                code="unsupported_value",
                param="reasoning_effort",
            ),
        ),
        # Worded like no refusal measured, and with no param: the rule is
        # structural, so a vendor rephrasing tomorrow changes nothing.
        (openai_like.OpenRouterProvider, error(400, "thinking cannot be turned off")),
    ],
    ids=["openrouter-mandatory", "gpt-4o-mini-unrecognized", "gpt-5-unsupported-value", "reworded"],
)
def test_a_refused_hint_is_retried_once_without_it(cls, refusal):
    """A 400 while the hint is on the wire gets exactly one more try without
    it. The model then reasons at its own default, and the answer says the
    hint did not go (`hint_sent` False) so the row can tell a dropped hint
    from an ignored one."""
    rec = Recorder(refusal, completion("Paris"))

    answer = provider(cls, rec).complete(hinted())

    assert answer.text == "Paris"
    assert rec.calls == 2
    assert [k for k in REASONING_KEYS if k in rec.body(0)] != []
    assert [k for k in REASONING_KEYS if k in rec.body(1)] == []
    assert rec.body(1)["temperature"] == hinted().temperature, "only the hint was dropped"
    assert answer.hint_sent is False


def test_a_second_400_after_dropping_the_hint_is_a_bad_response():
    refusal = error(400, "Reasoning is mandatory for this endpoint and cannot be disabled.")
    other = error(400, "Unsupported parameter: 'max_tokens' is not supported with this model.")
    rec = Recorder(refusal, other)

    with pytest.raises(base.BadResponse):
        provider(openai_like.OpenRouterProvider, rec).complete(hinted())

    assert rec.calls == 2


def test_a_context_length_400_with_the_hint_on_is_context_too_long_after_one_call():
    """The one refusal the hint retry must not swallow: the prompt does not
    fit, and a second call without the hint would not fit either."""
    rec = Recorder(error(400, "maximum context length is 128000 tokens", code="context_length_exceeded"))

    with pytest.raises(base.ContextTooLong):
        provider(openai_like.OpenRouterProvider, rec).complete(hinted())

    assert rec.calls == 1


@pytest.mark.parametrize(
    "cls", [openai_like.OpenRouterProvider, openai_like.OpenAIProvider], ids=["openrouter", "openai"]
)
def test_a_400_with_no_hint_on_the_wire_is_not_retried(cls):
    rec = Recorder(error(400, "Reasoning is mandatory for this endpoint and cannot be disabled."))

    with pytest.raises(base.BadResponse):
        provider(cls, rec).complete(REQUEST)

    assert rec.calls == 1


def test_a_temperature_refusal_with_the_hint_on_drops_only_temperature():
    """gpt-5.6 on api.openai.com refuses temperature 0.2 (2026-09-06) and
    honours reasoning_effort 'none' (2026-09-11). `param` names the knob, so
    the hint stays on the second call."""
    refusal = error(
        400,
        "Unsupported value: 'temperature' does not support 0.2 with this model. "
        "Only the default (1) value is supported.",
        code="unsupported_value",
        param="temperature",
    )
    rec = Recorder(refusal, completion("Paris"))

    answer = provider(openai_like.OpenAIProvider, rec).complete(hinted())

    assert rec.calls == 2
    assert "temperature" not in rec.body(1)
    assert rec.body(1)["reasoning_effort"] == "none"
    assert answer.hint_sent is True


def test_a_hint_refusal_that_mentions_temperature_drops_only_the_hint():
    """`exc.param` decides when the endpoint names a knob. A refusal naming
    `reasoning_effort` whose message happens to contain "temperature" and
    "unsupported" is about the hint. Read by its wording - the predicate before
    TASK-029 - it would drop the app's temperature, keep the refused hint, be
    refused again and drop the hint on a third request: an answer at the
    model's default temperature, for a refusal that never mentioned it. The
    wording is constructed; no endpoint has been seen sending it. It is the
    case the `param` rule exists for."""

    def endpoint(request):
        if "reasoning_effort" in json.loads(request.content):
            return error(
                400,
                "Unsupported value: 'reasoning_effort' does not support 'none' with this "
                "model; on reasoning models it takes the place of temperature.",
                code="unsupported_value",
                param="reasoning_effort",
            )
        return completion("Paris")

    rec = Recorder(endpoint)

    answer = provider(openai_like.OpenAIProvider, rec).complete(hinted())

    assert rec.calls == 2, [sorted(rec.body(i)) for i in range(rec.calls)]
    assert "reasoning_effort" in rec.body(0)
    assert "reasoning_effort" not in rec.body(1), "the refused hint was sent again"
    assert rec.body(1)["temperature"] == hinted().temperature, "temperature was dropped"
    assert answer.hint_sent is False


def test_a_temperature_refusal_then_a_hint_refusal_is_three_calls():
    """Each knob is dropped at most once, so the worst case is three calls and
    every extra one follows a 400."""
    temperature = error(400, "Unsupported value: 'temperature'", param="temperature")
    hint = error(400, "Unrecognized request argument supplied: reasoning_effort")
    rec = Recorder(temperature, hint, completion("Paris"))

    answer = provider(openai_like.OpenAIProvider, rec).complete(hinted())

    assert rec.calls == 3
    assert "temperature" not in rec.body(2)
    assert "reasoning_effort" not in rec.body(2)
    assert answer.hint_sent is False


def test_the_hint_mapping_is_copied_into_the_body_never_shared(monkeypatch):
    """`reasoning_off_body` is a class attribute every instance shares, and
    OpenRouter's is nested: `{"extra_body": {"reasoning": {...}}}`. Merged by
    reference - or copied one level, which `dict(...)` does - the `extra_body`
    handed to the SDK *is* the class's own dict, and whatever writes into it
    rewrites the hint for every later call in the process. So this checks the
    object that was sent, not only the JSON: a fresh dict at both levels.

    Popping the hint from the body never touched the class mapping, which is
    why an earlier version of this test, asserting only that the mapping still
    compared equal, passed against a by-reference merge. Also pins that
    `extra_body` carries the hint and nothing else, since dropping the hint
    drops the whole key."""
    assert openai_like.OpenRouterProvider.reasoning_off_body == {
        "extra_body": {"reasoning": {"enabled": False}}
    }
    assert openai_like.OpenAIProvider.reasoning_off_body == {"reasoning_effort": "none"}
    # A fresh mapping under monkeypatch: if the copy is ever lost, whatever the
    # failing run did to it is undone at teardown instead of leaking onward.
    shared = copy.deepcopy(openai_like.OpenRouterProvider.reasoning_off_body)
    monkeypatch.setattr(openai_like.OpenRouterProvider, "reasoning_off_body", shared)
    refusal = error(400, "Reasoning is mandatory for this endpoint and cannot be disabled.")
    prov = provider(openai_like.OpenRouterProvider, Recorder(refusal, completion()))
    handed: list = []
    real_create = prov._create

    def spy(key, body, req):
        # Taken at the call, before `complete` pops the hint for the retry.
        handed.append(body.get("extra_body"))
        return real_create(key, body, req)

    monkeypatch.setattr(prov, "_create", spy)

    prov.complete(hinted())

    sent = handed[0]
    assert sent == {"reasoning": {"enabled": False}}
    assert sent is not shared["extra_body"], "the body holds the class's own extra_body"
    assert sent["reasoning"] is not shared["extra_body"]["reasoning"], "copied one level only"
    assert handed[1] is None, "the retry without the hint sends no extra_body at all"
    sent["reasoning"]["enabled"] = True  # anything downstream writing into what it was handed
    assert shared == {"extra_body": {"reasoning": {"enabled": False}}}
    later = Recorder(completion())
    provider(openai_like.OpenRouterProvider, later).complete(hinted())
    assert later.body()["reasoning"] == {"enabled": False}


def test_the_answer_carries_reasoning_tokens_and_upstream():
    """Media 1, 2026-09-10: 22,515 of 23,918 completion tokens were reasoning,
    served by deepseek-v4-flash through the upstream OpenRouter calls Wafer.
    Both numbers reach the answer, through the real SDK on a fake socket - the
    top-level `provider` is not a field the SDK declares, and this is what
    proves it survives as an attribute."""
    rec = Recorder(
        completion(
            "cleaned",
            completion_tokens=23918,
            details={"reasoning_tokens": 22515},
            upstream="Wafer",
            reasoning_trace=None,
        )
    )

    answer = provider(openai_like.OpenRouterProvider, rec).complete(hinted())

    assert answer.reasoning_tokens == 22515
    assert answer.upstream == "Wafer"
    assert answer.completion_tokens == 23918
    assert answer.hint_sent is True
    assert answer.reasoning_chars is None, "a cloud trace is not counted: luna spends without one"


def test_unreported_reasoning_is_none_never_zero():
    """None means nobody said; 0 would read as "it did not reason", which is
    the claim `hint_ignored` is built on."""
    for details in (None, {"reasoning_tokens": None}):
        rec = Recorder(completion("hi", details=details))
        answer = provider(openai_like.OpenAIProvider, rec).complete(REQUEST)
        assert answer.reasoning_tokens is None
        assert answer.upstream is None, "api.openai.com names no upstream"
        assert answer.hint_sent is None, "no hint was asked for"


def test_a_contentless_length_answer_says_what_it_spent():
    """What job 154 left on the board was "no message content
    (finish_reason='length')" - and nothing about where 8,000 tokens went. A
    failed call writes no row, so the error is the only receipt."""
    rec = Recorder(
        httpx2.Response(
            200,
            json={
                "id": "1",
                "object": "chat.completion",
                "created": 1,
                "model": "deepseek/deepseek-v4-flash-0731",
                "provider": "Wafer",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": None, "reasoning": None},
                        "finish_reason": "length",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1900,
                    "completion_tokens": 8000,
                    "total_tokens": 9900,
                    "completion_tokens_details": {"reasoning_tokens": 7990},
                },
            },
        )
    )

    with pytest.raises(base.BadResponse) as caught:
        provider(openai_like.OpenRouterProvider, rec).complete(REQUEST)

    message = str(caught.value)
    assert "length" in message
    assert "8000" in message and "7990" in message, message
    assert "Wafer" in message, message


def test_a_refused_connection_is_unreachable_naming_the_endpoint_and_is_retried():
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    rec = Recorder(refuse)
    prov = provider(openai_like.OpenAIProvider, rec)

    with pytest.raises(base.Unreachable) as exc:
        base.with_retry(lambda: prov.complete(REQUEST), attempts=3, sleep=lambda _s: None)

    assert "api.openai.com" in str(exc.value)
    assert rec.calls == 3


def test_a_500_is_unreachable_so_the_retry_covers_a_flapping_endpoint():
    rec = Recorder(error(500, "internal error"))
    with pytest.raises(base.Unreachable):
        provider(openai_like.OpenRouterProvider, rec).complete(REQUEST)


# --- what each provider puts on the wire -------------------------------------------


def test_openrouter_has_its_own_base_url_default_model_and_attribution_headers():
    rec = Recorder(completion())
    prov = provider(openai_like.OpenRouterProvider, rec)
    prov.complete(base.ChatRequest(system="", user="hi", model=prov.default_model))

    assert prov.base_url == "https://openrouter.ai/api/v1"
    assert str(rec.requests[0].url).startswith("https://openrouter.ai/api/v1/")
    assert openai_like.OpenRouterProvider.default_model != openai_like.OpenAIProvider.default_model
    assert rec.header("HTTP-Referer") == "http://127.0.0.1:4242"
    assert rec.header("X-Title") == "MyScribe"
    assert rec.body()["model"] == "openrouter/auto"


def test_openai_sends_no_openrouter_attribution_headers():
    rec = Recorder(completion())
    provider(openai_like.OpenAIProvider, rec).complete(REQUEST)

    assert rec.header("HTTP-Referer") is None
    assert rec.header("X-Title") is None


def test_each_provider_uses_the_token_budget_key_its_endpoint_accepts():
    # Measured 2026-09-02: api.openai.com answers gpt-5-mini with HTTP 400
    # "Unsupported parameter: 'max_tokens' ... Use 'max_completion_tokens'",
    # while openrouter.ai takes max_tokens for all 423 of its models.
    request = base.ChatRequest(system="", user="hi", model="m", max_output_tokens=64)

    openai_rec = Recorder(completion())
    provider(openai_like.OpenAIProvider, openai_rec).complete(request)
    assert openai_rec.body()["max_completion_tokens"] == 64
    assert "max_tokens" not in openai_rec.body()

    router_rec = Recorder(completion())
    provider(openai_like.OpenRouterProvider, router_rec).complete(request)
    assert router_rec.body()["max_tokens"] == 64
    assert "max_completion_tokens" not in router_rec.body()


def test_a_json_schema_becomes_a_response_format_and_is_absent_otherwise():
    schema = {"name": "summary", "schema": {"type": "object", "properties": {}}}
    with_schema = Recorder(completion())
    provider(openai_like.OpenAIProvider, with_schema).complete(
        base.ChatRequest(system="", user="hi", model="m", json_schema=schema)
    )
    assert with_schema.body()["response_format"] == {"type": "json_schema", "json_schema": schema}

    without = Recorder(completion())
    provider(openai_like.OpenAIProvider, without).complete(REQUEST)
    assert "response_format" not in without.body()


def test_retargeting_a_request_never_carries_a_model_across_providers():
    asked = base.ChatRequest(system="", user="hi", model="openai/gpt-4o-mini")
    moved = base.retarget(asked, openai_like.OpenAIProvider)

    assert moved.model == openai_like.OpenAIProvider.default_model
    assert moved.user == asked.user and moved.system == asked.system
    assert asked.model == "openai/gpt-4o-mini"  # the original is untouched


def test_the_default_client_factory_leaves_retrying_to_with_retry():
    client = openai_like.default_client_factory(
        base_url="https://example.invalid/v1", api_key="k", default_headers={}, timeout=1.0
    )
    assert client.max_retries == 0


# --- keys: where they come from, and what is said about them --------------------------


SENTINEL = "sk-sentinel-do-not-leak-42"


def test_api_key_prefers_the_setting_over_both_environment_variables(conn):
    conn.execute(
        "INSERT INTO setting(key, value) VALUES (?, ?)",
        (base.setting_key("openrouter"), SENTINEL),
    )
    conn.commit()

    resolved = base.api_key(
        conn,
        openai_like.OpenRouterProvider,
        environ={"OPENROUTER_TOKEN": "from-env", "OPENROUTER_API_KEY": "also-env"},
        registry={}.get,
    )

    assert resolved.value == SENTINEL
    assert resolved.source == "settings"


def test_api_key_falls_back_to_openrouter_token_before_openrouter_api_key(conn):
    resolved = base.api_key(
        conn,
        openai_like.OpenRouterProvider,
        environ={"OPENROUTER_API_KEY": "second", "OPENROUTER_TOKEN": "first"},
        registry={}.get,
    )
    assert (resolved.value, resolved.source) == ("first", "OPENROUTER_TOKEN")

    resolved = base.api_key(
        conn,
        openai_like.OpenRouterProvider,
        environ={"OPENROUTER_API_KEY": "second"},
        registry={}.get,
    )
    assert (resolved.value, resolved.source) == ("second", "OPENROUTER_API_KEY")


def test_a_machine_wide_variable_is_read_from_the_registry_when_the_process_lacks_it(conn):
    # This machine sets OPENROUTER_TOKEN machine-wide; a shell started before
    # that has no such variable, and os.environ would report nothing.
    resolved = base.api_key(
        conn,
        openai_like.OpenRouterProvider,
        environ={},
        registry={"OPENROUTER_TOKEN": "from-registry"}.get,
    )
    assert resolved.value == "from-registry"
    assert resolved.source == "OPENROUTER_TOKEN (Windows registry)"


def test_the_variable_order_holds_across_the_registry_fallback(conn):
    # A registry OPENROUTER_TOKEN still outranks a process OPENROUTER_API_KEY:
    # the constraint fixes the order of the variables, not of the two lookups.
    resolved = base.api_key(
        conn,
        openai_like.OpenRouterProvider,
        environ={"OPENROUTER_API_KEY": "second"},
        registry={"OPENROUTER_TOKEN": "first"}.get,
    )
    assert (resolved.value, resolved.source) == ("first", "OPENROUTER_TOKEN (Windows registry)")


def test_a_blank_value_is_no_key_at_all(conn):
    conn.execute("INSERT INTO setting(key, value) VALUES (?, ?)", (base.setting_key("openai"), "   "))
    conn.commit()

    resolved = base.api_key(
        conn, openai_like.OpenAIProvider, environ={"OPENAI_API_KEY": ""}, registry={}.get
    )
    assert resolved.value is None
    assert resolved.source == ""


def test_the_resolved_source_is_reportable_and_the_value_is_not(conn):
    resolved = base.api_key(
        conn, openai_like.OpenAIProvider, environ={"OPENAI_API_KEY": SENTINEL}, registry={}.get
    )

    assert resolved.found is True
    assert resolved.source == "OPENAI_API_KEY"
    assert SENTINEL not in repr(resolved)
    assert SENTINEL not in str(resolved)
    assert "OPENAI_API_KEY" in repr(resolved)


def test_available_is_false_with_a_readable_reason_when_no_key_is_found(conn):
    prov = openai_like.OpenAIProvider(conn, environ={}, registry={}.get)
    ok, why = prov.available()

    assert ok is False
    assert "OPENAI_API_KEY" in why
    assert "settings" in why.lower()


def test_available_is_true_and_names_the_source_without_the_value(conn):
    prov = openai_like.OpenRouterProvider(conn, environ={"OPENROUTER_TOKEN": SENTINEL}, registry={}.get)
    ok, why = prov.available()

    assert ok is True
    assert "OPENROUTER_TOKEN" in why
    assert SENTINEL not in why


def test_a_missing_key_fails_before_a_request_is_built(conn):
    rec = Recorder(completion())
    prov = openai_like.OpenAIProvider(
        conn, client_factory=fake_factory(rec), environ={}, registry={}.get
    )

    with pytest.raises(base.AuthError):
        prov.complete(REQUEST)

    assert rec.calls == 0


def test_an_api_error_never_echoes_the_key_back(conn):
    # A provider that quotes the key in its own error message must not get it
    # copied into ours, a log line or an llm_output row.
    rec = Recorder(error(401, f"Incorrect API key provided: {SENTINEL}"))
    prov = provider(openai_like.OpenAIProvider, rec, key=SENTINEL)

    with pytest.raises(base.AuthError) as exc:
        prov.complete(REQUEST)

    assert SENTINEL not in str(exc.value)
    assert SENTINEL not in repr(exc.value)


# --- the registry ---------------------------------------------------------------------


def test_every_registered_provider_is_reachable_by_name_and_declares_itself():
    """Task 2 registered `ollama`, so the expected set grew and the blanket
    `is_local is False` had to be split: the two cloud providers are still
    pinned to False (the assertion that matters - a provider that wrongly
    claimed to be local would be handed private transcripts), and the local one
    to True. Every provider must still declare `is_local` explicitly, because
    `privacy.assert_allowed` reads exactly that bit and nothing else."""
    from scribe import llm

    assert set(llm.PROVIDERS) == {"openai", "openrouter", "ollama"}
    assert llm.PROVIDERS["openai"].is_local is False
    assert llm.PROVIDERS["openrouter"].is_local is False
    assert llm.PROVIDERS["ollama"].is_local is True

    for name, cls in llm.PROVIDERS.items():
        assert cls.name == name
        assert isinstance(cls.is_local, bool)
        assert cls.default_model
        assert issubclass(cls, base.Provider)


def test_get_provider_builds_the_named_one_and_refuses_an_unknown_one(conn):
    from scribe import llm

    assert isinstance(llm.get_provider("openrouter", conn), openai_like.OpenRouterProvider)
    with pytest.raises(ValueError):
        llm.get_provider("gpt-9000", conn)


def test_openrouter_lets_the_router_pick_the_model():
    """`openrouter/auto` is a model id, not a setting: OpenRouter classifies the
    request in flight and forwards it to whatever it judges suitable, and the
    response's own `model` field names what actually answered.

    Verified live against the API on 2026-09-05 with the JSON schema this app
    sends for every preset - the one thing that could have broken, since a
    routed model has to honour `response_format` too. It routed to
    deepseek/deepseek-v4-flash-0731 and came back with both required keys.
    """
    assert openai_like.OpenRouterProvider.default_model == "openrouter/auto"

    rec = Recorder(completion("hi", prompt=1, completion_tokens=1))
    provider(openai_like.OpenRouterProvider, rec).complete(
        base.ChatRequest(system="", user="hi", model=openai_like.OpenRouterProvider.default_model)
    )

    assert rec.body()["model"] == "openrouter/auto"
    assert openai_like.OpenRouterProvider.supports_json_schema, (
        "the presets send a schema; auto routing must not quietly turn that off"
    )


def test_the_answer_says_which_model_the_router_chose():
    """With auto routing the requested model and the answering model differ, and
    the row this app stores has to be the one that answered - otherwise every
    timing and every stored output says 'openrouter/auto' and none of them say
    what produced it."""
    rec = Recorder(completion("hi", prompt=1, completion_tokens=1))
    answer = provider(openai_like.OpenRouterProvider, rec).complete(
        base.ChatRequest(system="", user="hi", model="openrouter/auto")
    )

    assert answer.model == "m", "the answer reports the requested id, not the one that replied"
