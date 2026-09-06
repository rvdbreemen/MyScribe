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
"""

from __future__ import annotations

import sqlite3
from typing import Any, Callable, Mapping

import openai

from scribe.llm import base
from scribe.llm.base import ChatRequest, ChatResponse

DEFAULT_TIMEOUT = 60.0
"""Seconds for one completion. Long transcripts are chunked (Task 3), so a
single call that takes longer than this is a stuck call, not a big one."""

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
        return body

    # --- calls -----------------------------------------------------------------------

    def complete(self, req: ChatRequest) -> ChatResponse:
        resolved = self.resolve_key()
        if not resolved.found:
            ok, why = self.available()
            raise base.AuthError(f"{self.name}: {why}")
        key = resolved.value or ""

        body = self._body(req)
        try:
            completion = self._create(key, body, req)
        except openai.BadRequestError as exc:
            if not _refuses_temperature(exc):
                raise self._map(exc, key, model=req.model) from None
            # Measured on 2026-09-06: gpt-5.6 on api.openai.com answers 400
            # "Unsupported value: 'temperature' does not support 0.0 with this
            # model. Only the default (1) value is supported." The request is
            # fine; the knob is gone on this model. Once more without it - a
            # second 400 is a real refusal and maps like any other.
            body.pop("temperature", None)
            try:
                completion = self._create(key, body, req)
            except openai.BadRequestError as again:
                raise self._map(again, key, model=req.model) from None

        return self._answer(completion, req)

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

    def _answer(self, completion: Any, req: ChatRequest) -> ChatResponse:
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
        if text is None:
            raise base.BadResponse(
                f"{self.name} answered with no message content (finish_reason={finish!r})"
            )

        usage = getattr(completion, "usage", None)
        return ChatResponse(
            text=text,
            # The model the *answer* names, not the one that was asked for: a
            # request for `gpt-4o-mini` is served by `gpt-4o-mini-2024-07-18`,
            # and the stored row should say which snapshot actually wrote the
            # text. Falls back to the requested id when the API omits it.
            model=getattr(completion, "model", None) or req.model,
            provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            raw_finish_reason=finish,
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
            code = getattr(exc, "code", None)
            lowered = detail.lower()
            if code == "context_length_exceeded" or "context length" in lowered or "too long" in lowered:
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
    """Whether this 400 is the model saying it has no temperature knob."""
    if not isinstance(exc, openai.BadRequestError):
        return False
    text = str(exc).lower()
    param = getattr(exc, "param", None)
    return param == "temperature" or ("temperature" in text and "unsupported" in text)


class OpenAIProvider(OpenAILikeProvider):
    name = "openai"
    is_local = False
    base_url = "https://api.openai.com/v1"
    # Verified present on this account 2026-09-02, 128k context, the cheap tier.
    default_model = "gpt-4o-mini"
    key_env_vars = ("OPENAI_API_KEY",)
    # Not max_tokens: gpt-5* rejects it outright (module docstring).
    max_tokens_field = "max_completion_tokens"


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
