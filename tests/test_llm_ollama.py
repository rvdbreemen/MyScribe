"""The local provider (Phase 5 Task 2).

No test here touches the network. Every request goes through an
`httpx2.MockTransport`, so the real client builds the real request and only
the socket is fake. The live daemon on this machine was probed by hand while
this was written; what it answered is recorded in `scribe/llm/ollama.py`'s
module docstring and drives the shapes asserted below - in particular the
404 body for a model that is not pulled, and the fact that a prompt which
overflows the requested `num_ctx` comes back as a *successful* 200.
"""

from __future__ import annotations

import json

import httpx2
import pytest

from scribe.llm import base, ollama

# --- fake transport ---------------------------------------------------------------


class Recorder:
    """A MockTransport handler that remembers every request it answered.

    A near-twin of the one in `test_llm_providers.py`, deliberately duplicated:
    fifteen lines of test scaffolding are cheaper than a shared fixture that
    couples two provider suites together.
    """

    def __init__(self, *responses, show=None):
        # One response per call; the last repeats once they run out, so a retry
        # test can hand over a single 500 and still get three of them.
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []
        # `/api/show` is answered from here rather than from the script
        # (TASK-084). The provider asks it once per model to learn the window,
        # and a lookup that consumed a scripted response would renumber every
        # assertion in this file for a request that is not what it is testing.
        self.show = show

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.path == "/api/show":
            return self.show or httpx2.Response(200, json={"model_info": {}})
        index = min(len(self.asked) - 1, len(self.responses) - 1)
        answer = self.responses[index]
        return answer(request) if callable(answer) else answer

    @property
    def asked(self) -> list[httpx2.Request]:
        """Every request but the window lookup - what the tests are about."""
        return [r for r in self.requests if r.url.path != "/api/show"]

    @property
    def calls(self) -> int:
        return len(self.asked)

    @property
    def lookups(self) -> list[httpx2.Request]:
        return [r for r in self.requests if r.url.path == "/api/show"]

    def body(self, index: int = 0) -> dict:
        return json.loads(self.asked[index].content)

    def path(self, index: int = 0) -> str:
        return self.asked[index].url.path


def fake_factory(handler):
    """A `client_factory` that builds a real client on a fake socket."""

    def factory(*, base_url, timeout):
        return httpx2.Client(
            base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(handler)
        )

    return factory


def provider(handler, **kwargs) -> ollama.OllamaProvider:
    return ollama.OllamaProvider(None, client_factory=fake_factory(handler), **kwargs)


def chat_answer(
    text="Paris",
    *,
    model="qwen3.5:4b",
    prompt_eval=24,
    eval_count=5,
    done_reason="stop",
    thinking=None,
):
    """The shape `/api/chat` returned on this machine on 2026-09-02."""
    message: dict = {"role": "assistant", "content": text}
    if thinking is not None:
        message["thinking"] = thinking
    return httpx2.Response(
        200,
        json={
            "model": model,
            "created_at": "2026-09-02T20:15:31.8099578Z",
            "message": message,
            "done": True,
            "done_reason": done_reason,
            "total_duration": 8975223500,
            "prompt_eval_count": prompt_eval,
            "eval_count": eval_count,
        },
    )


def tags(*names):
    return httpx2.Response(200, json={"models": [{"name": n, "model": n} for n in names]})


REQUEST = base.ChatRequest(system="You are terse.", user="Capital of France?", model="qwen3.5:4b")


# --- a normal chat ------------------------------------------------------------------


def test_a_normal_chat_returns_the_text_and_what_it_cost():
    rec = Recorder(chat_answer("Paris", prompt_eval=24, eval_count=5))

    answer = provider(rec).complete(REQUEST)

    assert answer.text == "Paris"
    assert answer.provider == "ollama"
    assert answer.model == "qwen3.5:4b"
    # Ollama's own names for the two counts, mapped onto the seam's names.
    assert (answer.prompt_tokens, answer.completion_tokens) == (24, 5)
    assert answer.raw_finish_reason == "stop"
    assert rec.path() == "/api/chat"
    assert rec.body()["messages"] == [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "Capital of France?"},
    ]


def test_the_posted_body_asks_for_the_context_window_and_the_budget_it_needs():
    """`num_ctx` is the point of using the native endpoint rather than
    Ollama's OpenAI-compatible shim, so it must be on the wire; `num_predict`
    carries the seam's `max_output_tokens`, without which the budget is a
    number the caller passes to nobody."""
    rec = Recorder(chat_answer())
    request = base.ChatRequest(
        system="", user="hi", model="qwen3.5:4b", temperature=0.4, max_output_tokens=256
    )

    provider(rec, num_ctx=8192).complete(request)

    body = rec.body()
    assert body["model"] == "qwen3.5:4b"
    assert body["stream"] is False
    assert body["keep_alive"] == "5m"
    assert body["options"]["num_ctx"] == 8192
    assert body["options"]["num_predict"] == 256
    assert body["options"]["temperature"] == 0.4


def test_the_hint_is_think_false_and_its_absence_sends_no_think():
    """Ollama's own spelling of "no reasoning", at the top level of the body
    and not inside `options`. Measured 2026-09-11 on the real cleanup template:
    qwen3.5:4b spent 6,000 of 6,000 tokens thinking and wrote nothing, and with
    `think: false` answered a correct cleaning in 107 tokens and 1.5 s.

    Absent, not `true`, when the hint is off: a model without the thinking
    capability refuses a truthy `think`, and today's body sends no key."""
    hinted = Recorder(chat_answer())
    provider(hinted).complete(
        base.ChatRequest(system="", user="hi", model="qwen3.5:4b", reasoning_off=True)
    )
    assert hinted.body()["think"] is False
    assert "think" not in hinted.body()["options"]

    plain = Recorder(chat_answer())
    provider(plain).complete(REQUEST)
    assert "think" not in plain.body()


def test_thinking_is_reported_in_characters_never_as_a_token_count():
    """Ollama reports one `eval_count` for thinking and answer together, so the
    thinking is measured in the only unit the answer carries - its characters -
    and `reasoning_tokens` stays None rather than a number nobody reported."""
    rec = Recorder(chat_answer("Paris", thinking="abc"))

    answer = provider(rec).complete(
        base.ChatRequest(system="", user="hi", model="qwen3.5:4b", reasoning_off=True)
    )

    assert answer.reasoning_chars == 3
    assert answer.reasoning_tokens is None
    assert answer.hint_sent is True, "ollama never refuses think:false, so the hint went"
    assert answer.upstream is None

    unhinted = provider(Recorder(chat_answer("Paris"))).complete(REQUEST)
    assert unhinted.reasoning_chars == 0, "no thinking field is no thinking"
    assert unhinted.hint_sent is None, "no hint was asked for"


def test_an_empty_system_prompt_sends_no_system_message():
    rec = Recorder(chat_answer())
    provider(rec).complete(base.ChatRequest(system="   ", user="hi", model="m"))

    assert [m["role"] for m in rec.body()["messages"]] == ["user"]


# --- what goes wrong ------------------------------------------------------------------


def test_a_refused_connection_is_unreachable_and_names_the_host():
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    rec = Recorder(refuse)
    prov = provider(rec)

    with pytest.raises(base.Unreachable) as exc:
        base.with_retry(lambda: prov.complete(REQUEST), attempts=3, sleep=lambda _s: None)

    assert "127.0.0.1:11434" in str(exc.value)
    assert rec.calls == 3  # transient: worth a second look


def test_a_model_that_is_not_pulled_is_model_not_found_carrying_the_id():
    # Measured 2026-09-02: POST /api/chat for an absent model answers HTTP 404
    # {"error":"model 'llama-nonexistent:99b' not found"}.
    rec = Recorder(httpx2.Response(404, json={"error": "model 'gemma9:70b' not found"}))
    request = base.ChatRequest(system="", user="hi", model="gemma9:70b")

    with pytest.raises(base.ModelNotFound) as exc:
        provider(rec).complete(request)

    assert exc.value.model == "gemma9:70b"
    assert "gemma9:70b" in str(exc.value)
    assert "ollama pull" in str(exc.value)  # the fix, not just the diagnosis


def test_a_500_is_unreachable_so_a_model_runner_that_died_is_retried():
    # Measured on this machine while VRAM was full: Ollama answers HTTP 500
    # "model runner has unexpectedly stopped ...". The card being busy is the
    # textbook transient failure, so it belongs in the retryable half.
    rec = Recorder(httpx2.Response(500, json={"error": "model runner has unexpectedly stopped"}))
    prov = provider(rec)

    with pytest.raises(base.Unreachable) as exc:
        base.with_retry(lambda: prov.complete(REQUEST), attempts=3, sleep=lambda _s: None)

    assert "model runner" in str(exc.value)
    assert rec.calls == 3


def test_a_body_that_is_not_json_is_a_bad_response():
    rec = Recorder(httpx2.Response(200, text="<html>proxy got in the way</html>"))
    with pytest.raises(base.BadResponse):
        provider(rec).complete(REQUEST)


def test_an_empty_answer_is_a_bad_response_rather_than_an_empty_summary():
    """Measured: a thinking model whose output budget runs out returns HTTP 200,
    `done_reason: "length"`, everything in `message.thinking` and `content` an
    empty string. Handing that back as a summary is presenting a non-answer as
    a complete one."""
    rec = Recorder(
        chat_answer("", done_reason="length", thinking="Thinking Process: the user asks...")
    )

    with pytest.raises(base.BadResponse) as exc:
        provider(rec).complete(REQUEST)

    assert "length" in str(exc.value)


def test_an_answer_budget_spent_entirely_on_thinking_says_so_and_names_the_budget():
    """The same non-answer, but the message has to point at the cause.

    Measured against the live daemon on 2026-09-03: `qwen3.5:4b` with
    `num_predict: 200` returned `content: ""`, `done_reason: "length"` and
    ~780 characters of `message.thinking` on 4 runs out of 4. Nothing is
    broken there - the model simply never got out of its own preamble - so the
    message this used to give, "nothing was generated to store", sent the
    reader looking for a dead model instead of at the one number that fixes it.
    """
    rec = Recorder(
        chat_answer("", done_reason="length", thinking="Thinking Process: first I must...")
    )
    request = base.ChatRequest(
        system="", user="hi", model="qwen3.5:4b", max_output_tokens=200
    )

    with pytest.raises(base.BadResponse) as exc:
        provider(rec).complete(request)

    message = str(exc.value)
    assert "200" in message, f"the budget that ran out has to be in the message: {message}"
    assert "max_output_tokens" in message, f"and so does the knob that fixes it: {message}"
    assert "thinking" in message.lower(), f"and where the budget actually went: {message}"


def test_an_empty_answer_that_did_not_run_out_of_budget_is_not_blamed_on_the_budget():
    """The other half: `done_reason: "stop"` with empty content is a different
    fault - the model finished and wrote nothing - and telling that reader to
    raise `max_output_tokens` would be a wrong instruction, not a vague one."""
    rec = Recorder(chat_answer("", done_reason="stop"))

    with pytest.raises(base.BadResponse) as exc:
        provider(rec).complete(REQUEST)

    assert "max_output_tokens" not in str(exc.value)


# --- the window, before the request rather than after it ---------------------------------


def test_a_prompt_that_cannot_possibly_fit_the_window_is_refused_before_the_transport():
    """The guard the post-hoc check cannot be: refuse *before* sending.

    Measured 2026-09-03 against the live daemon at the shipped `num_ctx` of
    8192 with a 9029-word prompt: HTTP 200, `prompt_eval_count: 4098` -
    exactly `num_ctx / 2 + 2` - and a fluent summary of the half of the prompt
    that survived. The daemon allocates the window it was asked for and
    silently discards the front of the conversation to fit, so the count comes
    back *below* `num_ctx` and `_answer`'s `prompt_tokens > num_ctx` check is
    structurally unable to see it.

    A word count is the check that can see it, because it is a *lower* bound
    that needs no tokenizer: these models' pre-tokenizers split before each
    space-prefixed word, so no merge spans a space and a word costs at least
    one token. More words than the whole window therefore means the prompt
    cannot fit, with no argument about tokens-per-word available - so the
    refusal is sound in the only direction that matters.
    """
    rec = Recorder(chat_answer("a summary of half your prompt"))
    request = base.ChatRequest(
        system="You summarise documents.",
        user=" ".join(f"word{i}" for i in range(600)),
        model="qwen3.5:4b",
    )

    with pytest.raises(base.ContextTooLong) as exc:
        provider(rec, num_ctx=512).complete(request)

    assert "512" in str(exc.value)
    assert rec.calls == 0, "the refusal must happen before the daemon is asked to truncate"


def test_a_prompt_whose_words_fit_the_window_is_still_sent():
    """The pre-flight guard only fires on certainty. 600 words against a
    100k window is nowhere near it, and a check that refused here would make
    the provider useless for every real transcript."""
    rec = Recorder(chat_answer("Paris"))
    request = base.ChatRequest(
        system="", user=" ".join(f"word{i}" for i in range(600)), model="qwen3.5:4b"
    )

    assert provider(rec, num_ctx=100_000).complete(request).text == "Paris"
    assert rec.calls == 1


def test_a_prompt_that_overflowed_the_requested_window_is_context_too_long():
    """Measured 2026-09-02: asked for `num_ctx: 512`, Ollama answered HTTP 200
    with `prompt_eval_count: 1026` and `done_reason: "length"` - and `/api/ps`
    showed it had allocated 2048, not the 512 asked for. The endpoint never
    says no, so the only honest place to notice is the answer: more prompt
    tokens than the window we asked for means the model read a truncated
    prompt, and an answer written from a truncated prompt is not an answer."""
    rec = Recorder(chat_answer("...ish", prompt_eval=1026, done_reason="length"))

    with pytest.raises(base.ContextTooLong) as exc:
        provider(rec, num_ctx=512).complete(REQUEST)

    assert "1026" in str(exc.value) and "512" in str(exc.value)
    assert rec.calls == 1  # chunking is the fix; a retry is the same prompt again


def test_a_prompt_that_fits_the_requested_window_is_not_refused():
    rec = Recorder(chat_answer("Paris", prompt_eval=511))
    assert provider(rec, num_ctx=512).complete(REQUEST).text == "Paris"


# --- what the daemon offers -------------------------------------------------------------


def test_models_lists_the_pulled_names_sorted():
    rec = Recorder(tags("qwen3.5:9b", "gemma4:12b", "qwen3.5:4b"))

    assert provider(rec).models() == ["gemma4:12b", "qwen3.5:4b", "qwen3.5:9b"]
    assert rec.path() == "/api/tags"


def test_available_says_not_running_when_nothing_answers():
    def refuse(request):
        raise httpx2.ConnectError("connection refused", request=request)

    ok, why = provider(Recorder(refuse)).available()

    assert ok is False
    assert "127.0.0.1:11434" in why
    assert "not running" in why.lower()


def test_available_distinguishes_a_missing_model_from_a_missing_daemon():
    ok, why = provider(Recorder(tags("gemma4:12b")), model="qwen3.5:4b").available()

    assert ok is False
    assert "qwen3.5:4b" in why
    assert "ollama pull qwen3.5:4b" in why  # running, just not pulled
    assert "not running" not in why.lower()


def test_available_is_true_when_the_default_model_is_pulled():
    prov = provider(Recorder(tags("qwen3.5:4b", "gemma4:12b")))
    ok, why = prov.available()

    assert ok is True
    assert prov.default_model in why


# --- what makes it the local one ----------------------------------------------------------


def test_ollama_is_local_and_needs_no_key():
    """`is_local` is the single bit `privacy.assert_allowed` reads, and the
    absence of a key is why there is nothing here for `api_key` to resolve."""
    assert ollama.OllamaProvider.is_local is True
    assert ollama.OllamaProvider.key_env_vars == ()
    assert ollama.OllamaProvider.default_model


def test_a_custom_host_is_used_for_requests_and_named_in_failures():
    """A different port on this machine is a real configuration - a second
    daemon, a tunnel's near end - and the failure has to name it."""

    def refuse(request):
        raise httpx2.ConnectError("nope", request=request)

    prov = provider(Recorder(refuse), host="http://127.0.0.1:11500")
    ok, why = prov.available()

    assert "127.0.0.1:11500" in why


@pytest.mark.parametrize(
    "host",
    [
        "http://10.0.0.5:11434",
        "http://ollama.example.com:11434",
        "https://ollama.internal",
    ],
)
def test_a_host_that_is_not_this_machine_cannot_be_constructed(host):
    """`is_local = True` is a *class* attribute, and it is the single bit
    `privacy.assert_allowed` reads to decide whether a pinned recording's words
    may be sent. `host` was a per-instance constructor argument, so the two
    could disagree: one settings row naming a remote box would send a private
    transcript off this machine while the pin still read True.

    Deriving `is_local` from the host cannot fix it - the pin resolves the
    provider *class* and never sees an instance - so the only sound repair is
    to make the disagreement impossible to construct.
    """
    with pytest.raises(ValueError) as exc:
        ollama.OllamaProvider(None, host=host)

    assert host in str(exc.value)


@pytest.mark.parametrize(
    "host",
    ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434", "http://127.9.9.9"],
)
def test_every_way_of_spelling_this_machine_is_accepted(host):
    assert ollama.OllamaProvider(None, host=host).host


# --- the window a model actually has (TASK-084) ------------------------------------


def show(context_length=262144, arch="qwen35"):
    """The shape `/api/show` returned for qwen3.8:27b-q8_0 on 2026-09-18."""
    return httpx2.Response(
        200,
        json={
            "model_info": {"general.architecture": arch, f"{arch}.context_length": context_length},
            "capabilities": ["completion", "tools", "thinking"],
        },
    )


def test_a_models_window_is_read_from_the_daemon_not_assumed():
    rec = Recorder(show=show(262144))

    got = provider(rec).context_tokens("qwen3.8:27b-q8_0")

    assert [r.url.path for r in rec.lookups] == ["/api/show"]
    assert json.loads(rec.lookups[0].content)["model"] == "qwen3.8:27b-q8_0"
    assert got == ollama.MAX_NUM_CTX, "262144 tokens of KV cache is not a window this machine holds"


def test_a_model_with_a_smaller_window_is_planned_against_that():
    """The direction discovery really matters in. The daemon answers 200 to a
    prompt it quietly truncated, so a plan that assumed 8192 of a 4096 model
    would never find out it was cut (Robert, 2026-09-19: assume a limited
    context, not what a model claims)."""
    assert provider(Recorder(show=show(4096))).context_tokens("tiny:1b") == 4096


def test_a_large_advertised_window_is_not_taken_up():
    """A model reporting 262144 is not a reason to ask for it: a window is KV
    cache, and the cache is this machine's memory."""
    assert provider(Recorder(show=show(262144))).context_tokens("big:27b") == ollama.MAX_NUM_CTX
    assert ollama.MAX_NUM_CTX == ollama.DEFAULT_NUM_CTX


def test_a_daemon_that_cannot_say_falls_back_to_the_floor():
    """An old daemon, a 404, a model_info without the key: none of them is a
    reason to fail a task that would have run at the shipped default."""
    for answer in (
        httpx2.Response(404, json={"error": "model 'x' not found"}),
        httpx2.Response(200, json={"model_info": {"general.architecture": "qwen35"}}),
        httpx2.Response(500, text="boom"),
    ):
        assert provider(Recorder(show=answer)).context_tokens("x") == ollama.DEFAULT_NUM_CTX


def test_the_window_asked_of_the_daemon_is_the_window_that_was_planned():
    """The two must agree. `LOCAL_CONTEXT_TOKENS` says planning for more than
    the provider requests means sending a prompt the daemon quietly truncates,
    and it answers 200 while doing it - so a disagreement here is invisible."""
    rec = Recorder(chat_answer("Paris"), show=show(4096))
    p = provider(rec)

    planned = p.context_tokens("mid:7b")
    p.complete(base.ChatRequest(system="s", user="u", model="mid:7b"))

    assert planned == 4096
    assert rec.body(0)["options"]["num_ctx"] == planned


def test_an_explicit_num_ctx_is_never_overridden():
    """A caller who names a window has a reason - a small card, a measurement."""
    p = provider(Recorder(show=show(262144)), num_ctx=4096)

    assert p.num_ctx == 4096
    assert p.context_tokens("qwen3.8:27b-q8_0") == 4096
