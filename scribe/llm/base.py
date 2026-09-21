"""The provider seam: one request shape, one response shape, one error taxonomy.

Everything above this module - the six preset outputs, the chat tool, the web
layer - talks to a `Provider` and catches an `LlmError`. Nothing above it
imports a vendor SDK or knows what an `openai.RateLimitError` is. A new
provider is a subclass plus a line in `scribe.llm.PROVIDERS`, never an
`if provider == ...` in a caller.

Three things live here rather than in the providers, because all of them must
answer the same way:

* **The taxonomy.** Six failure kinds, chosen by what a caller would *do*
  about them: retry (`RateLimited`, `Unreachable`), fix a key (`AuthError`),
  fix a model name (`ModelNotFound`), send less text (`ContextTooLong`), or
  give up and show the raw text (`BadResponse`).
* **Retrying.** `with_retry` backs off on the two transient kinds only. A
  retried `AuthError` is three times the wait for the same answer, and a
  retried `BadResponse` is money spent to be lied to twice.
* **Key resolution.** One function, one fixed order, and a *source name* the
  settings page can show, so a stale environment variable cannot silently
  outrank a key the user just typed without the page saying which one won.

Nothing here logs, and `ResolvedKey` will not print its own value: a key
reaches its provider and goes nowhere else - not into a log line, an error
message, an `llm_output` row or a commit.
"""

from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping

from scribe import credentials

# --- what a call looks like ---------------------------------------------------------


@dataclass(frozen=True)
class ChatRequest:
    """One turn: a system prompt, a user prompt, and how to answer it.

    `max_output_tokens` is the seam's name for the budget; each provider spells
    it the way its endpoint insists on (see `openai_like`). `json_schema`, when
    given, is the OpenAI `json_schema` object - `{"name": ..., "schema": ...}` -
    and the provider turns it into whatever `response_format` its API wants.
    """

    system: str
    user: str
    model: str
    temperature: float = 0.2
    max_output_tokens: int = 4000
    json_schema: dict | None = None
    reasoning_off: bool = False
    """Ask the model not to reason before it answers (TASK-029). A hint, not a
    guarantee: each provider sends it as its own wire field
    (`Provider.reasoning_off_body`); an endpoint that refuses it with a 400 gets
    one more call without it; and a model that ignores it is recorded on the
    answer (`ChatResponse.hint_sent`, `reasoning_tokens`) and bounded only
    where the endpoint enforces `max_output_tokens`. False sends nothing, so a
    request without the hint is byte-for-byte the request this app always
    sent."""


@dataclass(frozen=True)
class ChatResponse:
    """What came back, plus what it cost and who produced it.

    `provider`, `model` and the token counts are stored with every output row:
    a better model later is a new row, and the row has to be able to say what
    made it. Token counts are `None` when the API did not report them - not 0,
    which would read as "this call was free".

    The four reasoning fields follow the same rule: `None` is "not reported",
    never 0. `hint_sent` is None when no hint was asked for, True when the call
    that answered carried it, and False when it was asked for, refused with a
    400 and dropped - so a dropped hint and an ignored one look different on
    the row. `reasoning_tokens` is the cloud count; `reasoning_chars` is the
    length of Ollama's `thinking`, the only measure it gives; `upstream` is
    who OpenRouter forwarded the call to.
    """

    text: str
    model: str
    provider: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw_finish_reason: str | None = None
    hint_sent: bool | None = None
    reasoning_tokens: int | None = None
    reasoning_chars: int | None = None
    upstream: str | None = None


def hint_words(hint_sent: bool | None) -> str:
    """`ChatResponse.hint_sent` in words, for an error message.

    Here rather than in a provider because two kinds of caller need it: a
    provider whose call came back with nothing, and `tasks` refusing a part
    cut off at its cap. Neither writes a row, so the message is the receipt.
    """
    if hint_sent is None:
        return "no reasoning hint asked"
    return "reasoning hint sent" if hint_sent else "reasoning hint refused and dropped"


def retarget(req: ChatRequest, provider: "Provider | type[Provider]") -> ChatRequest:
    """The same request aimed at another provider, with the model *replaced*.

    Model ids are provider-scoped and there is no translation between the
    namespaces: `openai/gpt-4o-mini` is a real id at OpenRouter and a
    guaranteed 404 at api.openai.com, and `gpt-4o-mini` is the other way
    round. So a fallback never carries the old name over - it takes the new
    provider's own default and says so. Measured on 2026-09-02: all 423 ids
    OpenRouter offers this account are namespaced, and none of api.openai.com's
    128 are.

    Everything else carries over, `reasoning_off` included: the hint is a
    property of the question, and the new provider spells it its own way.
    """
    return replace(req, model=provider.default_model)


# --- what can go wrong -----------------------------------------------------------------


class LlmError(RuntimeError):
    """Base for every failure a provider is allowed to raise."""


class AuthError(LlmError):
    """No key, a rejected key, or a key without access to this endpoint."""


class RateLimited(LlmError):
    """The provider asked us to slow down. Worth retrying."""


class ModelNotFound(LlmError):
    """The model id is not one this provider has."""

    def __init__(self, message: str, *, model: str | None = None):
        super().__init__(message)
        self.model = model


class ContextTooLong(LlmError):
    """The prompt does not fit. The fix is chunking, never a retry."""


class BadResponse(LlmError):
    """A 200 that is not an answer, or an answer that does not parse."""


class Unreachable(LlmError):
    """Nothing answered, or the endpoint failed in a way that may pass."""


# --- keys ------------------------------------------------------------------------------


SETTING_KEY_PREFIX = "llm_key_"
"""Key rows are `llm_key_<provider name>`; plain text in myscribe.db, the same
honest trade as the gitignored .env (Global Constraints), which is why the
settings page says so next to the field."""


def setting_key(provider_name: str) -> str:
    return f"{SETTING_KEY_PREFIX}{provider_name}"


@dataclass(frozen=True)
class ResolvedKey:
    """A key and the name of the source that answered - never the value in a
    repr. `source` is "" when nothing answered; it is what the settings page
    prints next to the masked field."""

    value: str | None
    source: str

    @property
    def found(self) -> bool:
        return bool(self.value)

    def __repr__(self) -> str:  # never the value: this ends up in tracebacks
        return f"ResolvedKey(source={self.source!r}, found={self.found})"

    __str__ = __repr__


windows_env = credentials.windows_env
"""A machine- or user-wide environment variable, read from the registry.

It lives in `scribe.credentials` now, with every other place a credential can
hide, and is re-exported here because this is where it was written and where
its reason is recorded: Windows only broadcasts a `setx` variable to *new*
processes, so a shell that started before the variable was set never sees it.
"""


def api_key(
    conn: sqlite3.Connection | None,
    provider: "Provider | type[Provider]",
    *,
    environ: Mapping[str, str] | None = None,
    registry: Callable[[str], str | None] | None = None,
) -> ResolvedKey:
    """The key for `provider`, and the name of where it came from.

    The lookup itself is `scribe.credentials` (TASK-089.04), which is also
    what the Hugging Face token and the setup sitting ask; what stays here is
    the vocabulary. The settings page prints this `source` next to the masked
    field (`_settings_llm.html`), so it keeps the two strings it has always
    had - the bare variable name, and `<NAME> (Windows registry)`. The fuller
    wording the resolver can produce - which hive, which `.env` file - belongs
    to `credentials.find_all` and the found table.

    The order is fixed (Global Constraints): the `setting` row the user typed
    in the app, then the provider's environment variables in the order it
    lists them. First non-empty wins. The registry fallback is per *variable*,
    not appended after all of them: `OPENROUTER_TOKEN` outranks
    `OPENROUTER_API_KEY` wherever each is found, because the constraint fixes
    the order of the variables and not of the places a variable can live.
    """
    if credentials.credential(provider.name) is None:
        return ResolvedKey(None, "")  # Ollama: loopback, nothing to resolve
    resolved = credentials.resolve(
        conn,
        provider.name,
        environ=environ,
        registry=None if registry is None else credentials.one_hive(registry),
    )
    return ResolvedKey(resolved.value, credentials.short_source(resolved.source))


def redact(text: str, secret: str | None) -> str:
    """`text` with the key taken out of it.

    Providers quote the credential they rejected back at you often enough that
    this cannot be left to their good manners: an error message ends up in
    `job.error_detail`, on the jobs board and in whatever the user pastes into
    a bug report.
    """
    if not secret or len(secret) < 8:  # too short to be a key; too short to be safe to strip
        return text
    return text.replace(secret, "***")


# --- the provider itself ------------------------------------------------------------------


class Provider(ABC):
    """One model endpoint. Subclass, add a `PROVIDERS` entry, and you are done."""

    name: str = ""
    """Registry name; also the suffix of the provider's `setting` key row."""

    is_local: bool = False
    """True only when the text never leaves this machine. `privacy.assert_allowed`
    (Task 2) reads exactly this to decide whether a private media may be sent."""

    supports_json_schema: bool = False
    """True when `complete()` honours `ChatRequest.json_schema` by constraining
    what the model may emit - the OpenAI `response_format={"type":"json_schema"}`
    contract.

    A capability, deliberately, rather than a list of provider names in
    `tasks.py`: Global Constraints forbid an `if provider == ...` in a caller,
    and this is the question such a branch would be asking. Default False, so a
    provider that does not answer it gets the prompt-plus-repair path, which
    works everywhere - claiming the capability without implementing it is the
    only way to be wrong here, and that is a claim a provider makes about
    itself.
    """

    default_model: str = ""
    key_env_vars: tuple[str, ...] = ()

    reasoning_off_body: Mapping[str, Any] = {}
    """What `ChatRequest.reasoning_off` adds to this provider's request body:
    the hint's wire form, one per provider class, so no caller branches on the
    provider (TASK-029). Empty means the provider has no way to say it, and the
    hint is not sent. Shared by every instance, so a provider merges a copy and
    never this mapping itself."""

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(ready?, why not - or where the key came from when it is).

        Answered without a call to somebody else's API: the settings page
        renders one row per provider in the web process, and a page render must
        not wait on a remote host's DNS, TLS or queue. For a cloud provider
        that means no request at all - the question is only ever "is there a
        key".

        A *local* provider is the one sanctioned exception, added in Task 2: it
        may make a short, explicitly bounded call to this machine (see
        `ollama.OllamaProvider.available` and its `PROBE_TIMEOUT`), because
        "the daemon is not running" and "the daemon is running but the model is
        not pulled" are different problems with different fixes, and neither is
        answerable from a key.
        """

    @abstractmethod
    def models(self) -> list[str]:
        """The model ids this endpoint offers, sorted. May do I/O."""

    @classmethod
    def window_for_model(cls, model: str | None = None) -> int | None:
        """This provider's own answer for how big ``model``'s window is.

        None - the default, and every provider that cannot ask - means "use
        the planner's number for providers like me". A local runtime can do
        better: it has the model on disk and a daemon that will read its
        metadata. Kept a classmethod because planning happens before anything
        is constructed (`tasks.context_tokens_for`).
        """
        return None

    @abstractmethod
    def complete(self, req: ChatRequest) -> ChatResponse:
        """One completion, or an `LlmError`. Never a vendor exception."""


# --- retrying -----------------------------------------------------------------------------


RETRYABLE = (RateLimited, Unreachable)
"""The only two kinds worth a second attempt. An AuthError will be an AuthError
in four seconds' time, a BadResponse costs money to reproduce, a ContextTooLong
needs a smaller prompt and a ModelNotFound a different name."""


def with_retry(
    fn: Callable[[], "ChatResponse"],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] | None = None,
):
    """Call `fn`, retrying the transient failures with exponential backoff.

    `sleep` is injectable so the tests can prove the backoff grows without
    spending the time; `attempts` counts calls, not retries, so 3 means at
    most three requests reach the provider.
    """
    if sleep is None:
        import time

        sleep = time.sleep
    last: LlmError | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except RETRYABLE as exc:
            last = exc
            if attempt == attempts - 1:
                break
            sleep(base_delay * (2**attempt))
    assert last is not None
    raise last
