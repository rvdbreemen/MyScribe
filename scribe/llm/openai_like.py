"""OpenAI and OpenRouter: one implementation, two configurations.

Both speak the OpenAI chat-completions wire format, so both are the same class
with a different `base_url`, key variables, default model and headers. What
they do *not* share is spelled out as class attributes rather than as branches,
because the differences are the whole reason this file needs care.

Everything below was measured against the live APIs from this repository on
2026-09-02, not read off a blog post:

* `GET https://openrouter.ai/api/v1/models` returned **423 models** for this
  account, and **every one** of the 423 ids is namespaced (`vendor/model`).
  `GET https://api.openai.com/v1/models` returned **128**, and **none** of them
  contain a slash. An id is provider-scoped: carrying one across is a 404, and
  `base.retarget` exists so a fallback cannot.
* `anthropic/claude-3.5-haiku` -> HTTP 404 `"No endpoints found for
  anthropic/claude-3.5-haiku."`, while `anthropic/claude-haiku-4.5` is present.
  A plausible model id is not a valid one; that 404 is what `ModelNotFound`
  is for, and it carries the id so the message can name it.
* `openai/gpt-5.6-luna` on OpenRouter with `temperature=0.2` and
  `max_tokens=64` answered in one call (22 prompt / 5 completion tokens,
  $0.0000104). That was the shipped default then; it is `openrouter/auto`
  now, re-probed on 2026-09-05 - the router forwarded a schema-carrying
  request to deepseek/deepseek-v4-flash-0731 and the schema was honoured.
* `gpt-5-mini` on api.openai.com with `max_tokens` -> HTTP 400 *"Unsupported
  parameter: 'max_tokens' is not supported with this model. Use
  'max_completion_tokens' instead."* OpenAI therefore gets
  `max_completion_tokens`, which `gpt-4o-mini` accepts too (verified), while
  OpenRouter gets `max_tokens`, the name its normalization layer accepts for
  all 423 upstreams. That is `_body_extra`'s reason to exist: the budget key
  one endpoint accepts is the key the other rejects.

Two more rules, each with a test:

* **A 200 is not automatically an answer.** The SDK builds its response object
  without validating it, so an error envelope returned with HTTP 200 - which
  OpenRouter does for some upstream failures - arrives as a `ChatCompletion`
  with `choices=None` and an `error` attribute, and an empty `choices` list
  arrives as a success. Both are `BadResponse` here.
* **A key goes to its provider and nowhere else.** It is resolved, used, and
  redacted out of every error message on the way back (`base.redact`), because
  more than one API quotes the credential it just rejected.

**The reasoning hint (TASK-029), measured 2026-09-10 and 2026-09-11** with
synthetic prompts unless a media is named:

* Media 1 (837 Dutch words) through `openrouter/auto`: deepseek-v4-flash-0731
  spent 22,515 and 21,512 reasoning tokens of 23,918 and 22,589, 63-84 s, and
  came back 'stop' past `max_tokens` 6,000. With `reasoning: {enabled:
  false}`: 1,171 tokens, 0 of them reasoning, 8.5 s, the same cleaning.
* `openai/gpt-5.6-luna` via OpenRouter (upstream Azure): 107 reasoning tokens
  without the hint and 0 with `enabled: false`, and no `message.reasoning`
  trace in either case - which is why a trace is not counted here. On
  api.openai.com `reasoning_effort: "none"` took gpt-5.6 to 0.
* Refusals, all HTTP 400: OpenRouter for a model whose reasoning is mandatory
  ("Reasoning is mandatory for this endpoint and cannot be disabled.",
  google/gemini-3.5-flash-lite); `gpt-4o-mini`, this provider's default
  ("Unrecognized request argument supplied: reasoning_effort"); `gpt-5` and
  `gpt-6-astra` (code `unsupported_value`). So a 400 while the hint is on the
  wire gets one call without it - decided by what was sent, never by the
  wording, because three refusals already came in three wordings. Not for a
  context-length 400, which a second call would not fix.
* `usage.completion_tokens_details.reasoning_tokens` is the count, and
  OpenRouter's top-level `provider` names the upstream (Wafer, for the
  deepseek calls above); api.openai.com sends no such field.
"""

from __future__ import annotations

import copy
import sqlite3
from typing import Any, Callable, Mapping

import openai

from scribe.llm import base
from scribe.llm.base import ChatRequest, ChatResponse

DEFAULT_TIMEOUT = 60.0
"""Seconds any one network operation of a completion may wait - the connect,
each write, each read of the response - and not a limit on the call as a
whole.

The SDK passes a float to httpx2, which makes it `Timeout(60.0)`: one value
for all four phases (`openai/_base_client.py`, `_build_request`). httpcore2
then gives the read timeout to every socket read separately
(`httpcore2/_sync/http11.py`, `_receive_event`). So 60 s catches a
connection that goes silent for a minute - `APITimeoutError`, an
`APIConnectionError`, which `_map` reports as `Unreachable` - but not a slow
answer whose bytes keep arriving. Measured 2026-09-11: a gpt-5-mini
completion took 126.3 s on a client built with timeout=60, and answered. Why
its reads kept succeeding is inferred, not measured: something must have
arrived within every 60 s, and what the server sent in the meantime was not
recorded.

This used to say a call longer than 60 s was stuck, not big. That was wrong
twice: the number does not limit the call, and a reasoning call can honestly
take longer (deepseek took 63-84 s on media 1, module docstring)."""

ATTRIBUTION_REFERER = "http://127.0.0.1:4242"
"""OpenRouter shows a referer and a title on its activity page. This app's own
address and name - no user content, ever (Global Constraints)."""


def default_client_factory(*, base_url: str, api_key: str, default_headers: dict, timeout: float):
    """The real SDK client.

    `max_retries=0` on purpose: the SDK retries twice by itself, and layering
    that under `base.with_retry`'s three attempts would send nine requests for
    one call - three times the rate-limit pressure, from code that reads like
    it sends three.
    """
    return openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
        default_headers=default_headers or {},
        timeout=timeout,
        max_retries=0,
    )


class OpenAILikeProvider(base.Provider):
    """A chat-completions endpoint. Subclasses set the six class attributes."""

    base_url: str = ""
    extra_headers: dict[str, str] = {}
    max_tokens_field: str = "max_tokens"
    # `_body` turns `ChatRequest.json_schema` into `response_format`, which both
    # endpoints take, so a caller with a schema gets constrained decoding here.
    # OllamaProvider leaves this False: its native `/api/chat` has a `format`
    # field that would do the same job, and this app's body does not send it.
    supports_json_schema = True

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        client_factory: Callable[..., Any] | None = None,
        api_key: str | None = None,
        environ: Mapping[str, str] | None = None,
        registry: Callable[[str], str | None] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """`client_factory` is the transport seam: the tests hand in a factory
        whose client sits on a fake socket, so the SDK's own status-to-exception
        mapping still runs and no test ever opens one. `api_key` skips
        resolution entirely, for a caller that already has one in hand.
        """
        self.conn = conn
        self.client_factory = client_factory or default_client_factory
        self.explicit_key = api_key
        self.environ = environ
        self.registry = registry
        self.timeout = timeout
        self._client: Any | None = None
        self._client_key: str | None = None

    # --- the key -----------------------------------------------------------------

    def resolve_key(self) -> base.ResolvedKey:
        if self.explicit_key:
            return base.ResolvedKey(self.explicit_key, "given")
        return base.api_key(self.conn, self, environ=self.environ, registry=self.registry)

    def available(self) -> tuple[bool, str]:
        """Ready when a key resolves. No request: the settings page renders one
        of these per provider, in the web process, on every load."""
        resolved = self.resolve_key()
        if resolved.found:
            return True, f"key from {resolved.source}"
        names = " or ".join(self.key_env_vars)
        return False, f"no API key: save one in settings, or set {names}"

    # --- the client --------------------------------------------------------------

    def client(self, key: str):
        """The SDK client for `key`, rebuilt if the key it was built with is no
        longer the one that resolves (the settings page can change it under a
        long-lived provider instance)."""
        if self._client is None or self._client_key != key:
            self._client = self.client_factory(
                base_url=self.base_url,
                api_key=key,
                default_headers=dict(self.extra_headers),
                timeout=self.timeout,
            )
            self._client_key = key
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
            self._client_key = None

    # --- what goes on the wire -----------------------------------------------------

    def _messages(self, req: ChatRequest) -> list[dict]:
        messages = []
        if req.system.strip():
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        return messages

    def _body(self, req: ChatRequest) -> dict:
        """Exactly the keys this endpoint accepts, and no others.

        The budget key is per provider (see the module docstring); a schema
        becomes `response_format` only when there is one, so a provider that
        would reject an empty `response_format` never sees the key.
        """
        body: dict[str, Any] = {
            "model": req.model,
            "messages": self._messages(req),
            "temperature": req.temperature,
            self.max_tokens_field: req.max_output_tokens,
        }
        if req.json_schema is not None:
            schema = req.json_schema
            # A bare JSON Schema is accepted for convenience and wrapped into
            # the object OpenAI's response_format wants.
            if "schema" not in schema:
                schema = {"name": "output", "schema": schema}
            body["response_format"] = {"type": "json_schema", "json_schema": schema}
        if req.reasoning_off:
            # A copy: the mapping is a class attribute every instance shares,
            # and dropping a refused hint pops from this body.
            body.update(copy.deepcopy(dict(self.reasoning_off_body)))
        return body

    def _carries_hint(self, body: Mapping[str, Any]) -> bool:
        """Whether `body` still carries the reasoning hint."""
        return bool(self.reasoning_off_body) and all(key in body for key in self.reasoning_off_body)

    # --- calls -----------------------------------------------------------------------

    def complete(self, req: ChatRequest) -> ChatResponse:
        resolved = self.resolve_key()
        if not resolved.found:
            ok, why = self.available()
            raise base.AuthError(f"{self.name}: {why}")
        key = resolved.value or ""

        body = self._body(req)
        dropped: set[str] = set()
        while True:
            try:
                completion = self._create(key, body, req)
                break
            except openai.BadRequestError as exc:
                knob = self._refused_knob(exc, body, dropped)
                if knob is None:
                    raise self._map(exc, key, model=req.model) from None
                # One more call without the knob. Each is dropped at most once,
                # so this is at most three calls and every extra one follows a
                # refused, unbilled 400 (its latency is not measured).
                dropped.add(knob)
                if knob == "temperature":
                    body.pop("temperature", None)
                else:
                    for field in self.reasoning_off_body:
                        body.pop(field, None)

        hint_sent = self._carries_hint(body) if req.reasoning_off else None
        return self._answer(completion, req, hint_sent=hint_sent)

    def _refused_knob(
        self, exc: openai.BadRequestError, body: Mapping[str, Any], dropped: set[str]
    ) -> str | None:
        """Which knob this 400 is worth one more call without, if any.

        * `"temperature"` - gpt-5.6 on api.openai.com, 2026-09-06: 400
          "Unsupported value: 'temperature' does not support 0.0 with this
          model. Only the default (1) value is supported." The request is fine;
          the knob is gone on that model. Checked first, because it is the one
          refusal that names its knob (`exc.param`).
        * `"hint"` - any other 400 while the reasoning hint is on the wire,
          except one saying the prompt does not fit. Structural on purpose: the
          three measured refusals share no wording (module docstring), and a
          missed refusal costs the call. A false positive - a 400 about
          something else - is not free. When the call without the hint fails
          too, it costs one more unbilled 400. When it succeeds, the answer
          was bought at the model's default reasoning effort, the spend the
          hint exists to avoid (22,515 and 21,512 reasoning tokens on media
          1, module docstring). Under
          `openrouter/auto`, which routes each request afresh, the answer may
          even come from another model than the one that refused. The row says
          so (`hint_sent` False); that is the price of not reading wording.
          On OpenRouter the hint is all of `extra_body`, which carries
          nothing else today; a test pins that
          (`test_the_hint_mapping_is_copied_into_the_body_never_shared`).
        * None - a real refusal, mapped like any other.
        """
        if "temperature" not in dropped and "temperature" in body and _refuses_temperature(exc):
            return "temperature"
        if "hint" not in dropped and self._carries_hint(body) and not _refuses_context(exc):
            return "hint"
        return None

    def _create(self, key: str, body: dict, req: ChatRequest) -> Any:
        """One call, every vendor exception mapped - except a 400, which
        `complete` looks at first."""
        try:
            return self.client(key).chat.completions.create(**body)
        except openai.BadRequestError:
            raise
        except openai.OpenAIError as exc:
            raise self._map(exc, key, model=req.model) from None

    def models(self) -> list[str]:
        """The ids this endpoint offers, sorted.

        The settings page populates its dropdown from this rather than from a
        hard-coded list, which is what makes a 404 like
        `anthropic/claude-3.5-haiku` impossible to pick in the first place.
        """
        resolved = self.resolve_key()
        if not resolved.found:
            _ok, why = self.available()
            raise base.AuthError(f"{self.name}: {why}")
        key = resolved.value or ""
        try:
            page = self.client(key).models.list()
        except openai.OpenAIError as exc:
            raise self._map(exc, key) from None
        return sorted(model.id for model in page.data)

    # --- reading the answer ------------------------------------------------------------

    def _answer(
        self, completion: Any, req: ChatRequest, *, hint_sent: bool | None = None
    ) -> ChatResponse:
        """The answer, or `BadResponse`. `hint_sent` is decided by `complete`,
        which knows what the answering call's body carried."""
        envelope = getattr(completion, "error", None)
        if envelope:
            raise base.BadResponse(
                f"{self.name} answered HTTP 200 with an error envelope: {envelope}"
            )

        choices = getattr(completion, "choices", None) or []
        if not choices:
            raise base.BadResponse(f"{self.name} answered with no choices for model {req.model!r}")

        choice = choices[0]
        text = getattr(getattr(choice, "message", None), "content", None)
        finish = getattr(choice, "finish_reason", None)
        usage = getattr(completion, "usage", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        # `completion_tokens_details` may be absent or null; either is "not
        # reported", and so is None - never 0, which would claim no reasoning.
        reasoning_tokens = getattr(
            getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None
        )
        # OpenRouter's upstream. Not a field the SDK declares; its models keep
        # unknown fields as attributes, which a test pins.
        upstream = getattr(completion, "provider", None)

        if text is None:
            # A failed call writes no row, so this message is the only receipt
            # of what it spent (job 154: 8,000 tokens, and the board said only
            # "no message content").
            raise base.BadResponse(
                f"{self.name} answered with no message content (finish_reason={finish!r}, "
                f"completion_tokens={completion_tokens}, reasoning_tokens={reasoning_tokens}, "
                f"upstream={upstream!r}, {base.hint_words(hint_sent)})"
            )

        return ChatResponse(
            text=text,
            # The model the *answer* names, not the one that was asked for: a
            # request for `gpt-4o-mini` is served by `gpt-4o-mini-2024-07-18`,
            # and the stored row should say which snapshot actually wrote the
            # text. Falls back to the requested id when the API omits it.
            model=getattr(completion, "model", None) or req.model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=completion_tokens,
            raw_finish_reason=finish,
            hint_sent=hint_sent,
            reasoning_tokens=reasoning_tokens,
            upstream=upstream if isinstance(upstream, str) else None,
        )

    # --- vendor failures, translated ------------------------------------------------------

    def _map(self, exc: openai.OpenAIError, key: str, *, model: str | None = None) -> base.LlmError:
        """One vendor exception to one `LlmError`, with the key taken out.

        Order matters: `RateLimitError`, `AuthenticationError`, `NotFoundError`
        and `BadRequestError` are all `APIStatusError`s, so the specific ones
        are matched before the status check that catches the rest.
        """
        detail = base.redact(str(exc), key)

        if isinstance(exc, openai.RateLimitError):
            return base.RateLimited(f"{self.name} rate-limited the request: {detail}")
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return base.AuthError(f"{self.name} rejected the key: {detail}")
        if isinstance(exc, openai.NotFoundError):
            return base.ModelNotFound(f"{self.name} has no model {model!r}: {detail}", model=model)
        if isinstance(exc, openai.BadRequestError):
            if _refuses_context(exc):
                return base.ContextTooLong(f"{self.name} says the prompt does not fit: {detail}")
            return base.BadResponse(f"{self.name} refused the request: {detail}")
        if isinstance(exc, openai.APIConnectionError):
            # Covers timeouts too; both mean "no answer from this endpoint".
            return base.Unreachable(f"{self.name} at {self.base_url} did not answer: {detail}")
        status = getattr(exc, "status_code", None)
        if isinstance(status, int) and status >= 500:
            # A 5xx is the endpoint failing, not the request being wrong, so it
            # goes in the retryable half of the taxonomy.
            return base.Unreachable(f"{self.name} at {self.base_url} failed with HTTP {status}: {detail}")
        return base.BadResponse(f"{self.name} failed: {detail}")


def _refuses_temperature(exc: openai.OpenAIError) -> bool:
    """Whether this 400 is the model saying it has no temperature knob.

    `param` decides when the endpoint names one: a refusal that names
    `reasoning_effort` and happens to mention temperature is not about
    temperature. Read by its wording, that refusal would cost the app's
    temperature and a third request, which
    `test_a_hint_refusal_that_mentions_temperature_drops_only_the_hint` pins.
    The wording is read only when no param came back, which is how the
    2026-09-06 gpt-5.6 refusal arrived.
    """
    if not isinstance(exc, openai.BadRequestError):
        return False
    param = getattr(exc, "param", None)
    if param is not None:
        return param == "temperature"
    text = str(exc).lower()
    return "temperature" in text and "unsupported" in text


def _refuses_context(exc: openai.OpenAIError) -> bool:
    """Whether this 400 says the prompt does not fit: `ContextTooLong`, and
    never a reason to retry without the hint - the prompt would not fit then
    either."""
    if not isinstance(exc, openai.BadRequestError):
        return False
    lowered = str(exc).lower()
    return (
        getattr(exc, "code", None) == "context_length_exceeded"
        or "context length" in lowered
        or "too long" in lowered
    )


class OpenAIProvider(OpenAILikeProvider):
    name = "openai"
    is_local = False
    base_url = "https://api.openai.com/v1"
    # Verified present on this account 2026-09-02, 128k context, the cheap tier.
    default_model = "gpt-4o-mini"
    key_env_vars = ("OPENAI_API_KEY",)
    # Not max_tokens: gpt-5* rejects it outright (module docstring).
    max_tokens_field = "max_completion_tokens"
    # A named argument of `create()` in SDK 3.7.0. gpt-5.6 honours it (0
    # reasoning tokens, 2026-09-11); gpt-4o-mini - the default above - and
    # gpt-5 refuse it with a 400, and `complete` drops it and asks again.
    reasoning_off_body = {"reasoning_effort": "none"}


class OpenRouterProvider(OpenAILikeProvider):
    name = "openrouter"
    is_local = False
    base_url = "https://openrouter.ai/api/v1"
    # Let OpenRouter choose. `openrouter/auto` is a model id, not a setting:
    # the request is classified in flight and forwarded to whatever the router
    # judges suitable, and the response's own `model` field names what actually
    # answered - which is what `ChatResponse.model` carries, so a stored output
    # says which model produced it rather than "auto".
    #
    # The thing worth checking before trusting this was structured output: every
    # preset here sends a `response_format` with a JSON schema, and a routed
    # model has to honour it too. Verified live on 2026-09-05 - it routed to
    # deepseek/deepseek-v4-flash-0731 and returned both required keys. The
    # docs say the same, and a doc is not a measurement.
    #
    # No routing fee; the selected model's own rate applies. A specific model
    # is still one field away in Settings when a job wants one.
    default_model = "openrouter/auto"
    # This machine sets OPENROUTER_TOKEN machine-wide; OPENROUTER_API_KEY is the
    # name the rest of the world uses, and comes second.
    key_env_vars = ("OPENROUTER_TOKEN", "OPENROUTER_API_KEY")
    max_tokens_field = "max_tokens"
    extra_headers = {"HTTP-Referer": ATTRIBUTION_REFERER, "X-Title": "MyScribe"}
    # OpenRouter's own reasoning object, which `create()` has no argument for,
    # so it rides in `extra_body` and the SDK merges it into the JSON. Not the
    # `reasoning_effort` shorthand: `enabled: false` is what was measured on
    # both models auto has served for cleanup (module docstring), and dropping
    # the hint removes `extra_body` whole - which carries nothing else.
    reasoning_off_body = {"extra_body": {"reasoning": {"enabled": False}}}
