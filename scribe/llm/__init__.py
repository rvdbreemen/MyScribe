"""The LLM layer: one seam, one registry, one place keys are resolved.

`PROVIDERS` maps a name to a `Provider` class. Everything that needs a model -
the six preset outputs, the chat tool, the settings page - looks one up by
name and talks to the `base` types; nothing outside `scribe/llm/` imports a
vendor SDK or catches a vendor exception.

ADR-001 still holds above this line: none of this runs in the web process. The
web layer enqueues an `llm` job, reads rows and streams stored text; the calls
themselves happen in a runner child.

`chat()` is the front door: it resolves a provider by name, refuses to send a
pinned media's text anywhere that is not local, and retries only what is worth
retrying. Callers that skip it and drive a `Provider` directly skip the pin,
which is why nothing above this package should.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

from scribe.llm import privacy
from scribe.llm.base import (
    AuthError,
    BadResponse,
    ChatRequest,
    ChatResponse,
    ContextTooLong,
    LlmError,
    ModelNotFound,
    MODEL_SETTING_PREFIX,
    NothingAnswered,
    Provider,
    RateLimited,
    ResolvedKey,
    Unreachable,
    api_key,
    retarget,
    setting_key,
    with_retry,
)
from scribe.llm.ollama import OllamaProvider
from scribe.llm.openai_like import OpenAIProvider, OpenRouterProvider
from scribe.llm.privacy import PrivacyRefused

PROVIDERS: dict[str, type[Provider]] = {
    OpenAIProvider.name: OpenAIProvider,
    OpenRouterProvider.name: OpenRouterProvider,
    OllamaProvider.name: OllamaProvider,
}
"""Every provider this app can use. A new one is a `Provider` subclass and a
line here - never an `if provider == ...` in a caller (Global Constraints).

`privacy.assert_allowed` reads `is_local` off these classes, so a provider
added here is covered by the pin the moment it is registered, and a new cloud
provider cannot be forgotten into the allowed set."""


def provider_class(name: str) -> type[Provider]:
    """The class registered under `name`, without building it.

    Separate from `get_provider` because `chat()` has to decide whether a
    provider is allowed to see this text *before* anything is constructed.

    A `ValueError` rather than a `KeyError` so the message can list what does
    exist: the name usually comes from a stored setting or a form field, and
    "openai-router" should not surface as a bare KeyError on the jobs board.
    """
    try:
        return PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown LLM provider {name!r}; known providers: {known}") from None


def get_provider(name: str, conn: sqlite3.Connection | None = None, **kwargs) -> Provider:
    """Build the provider called `name`."""
    return provider_class(name)(conn, **kwargs)


def why_unavailable(
    conn: sqlite3.Connection | None, provider_name: str, model: str = ""
) -> str:
    """Why this provider cannot answer `model` right now, or "" when it can.

    The question the web panel and the finalize stage both ask before they
    write a job row (TASK-089.10). Without it a skipped key, a stopped Ollama
    or a model nobody pulled became an LLM_FAILED job on the board - three
    retries deep on Ollama - which is a failure somebody has to read instead
    of a sentence naming the fix.

    Here rather than in either caller: "build it, ask `available()`, swallow
    an `LlmError`, close it" is four lines that would drift in two spellings,
    and the runner child must be able to ask it without importing the web
    layer (ADR-001 keeps that boundary in both directions).

    No remote call, and nothing is loaded. `available()` is the one provider
    method the seam defines as answerable without a request to somebody
    else's API: key presence for a cloud provider, and for the local runtime
    one loopback GET bounded by `PROBE_TIMEOUT` with `trust_env=False`
    (TASK-089.05 - a proxy variable made a running Ollama read as stopped).

    `model` reaches only a local provider's constructor, because only a local
    runtime's availability depends on one: `openai_like` takes no `model=`
    and answers "is there a key" whatever is asked of it. Passing it matters
    on Ollama, where the id the job would carry is the id to ask about -
    `OllamaProvider.__init__` otherwise falls back to the saved row, which is
    the model a *different* request would use.

    An `LlmError` is reported as the reason rather than raised, the way
    `ai_ui.provider_rows` treats the same call: a provider having a bad day
    blocks the request and does not take the page down with it. Nothing else
    is caught - a bug in here must not fail open into "ready".
    """
    try:
        cls = provider_class(provider_name)
    except ValueError as exc:
        return str(exc)

    provider = cls(conn, **({"model": model} if model and cls.is_local else {}))
    try:
        ready, why = provider.available()
    except LlmError as exc:
        return str(exc)
    finally:
        provider.close()
    return "" if ready else why


def chat(
    conn: sqlite3.Connection,
    *,
    media_id: int,
    provider_name: str,
    request: ChatRequest,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
    **provider_kwargs,
) -> ChatResponse:
    """One completion about `media_id`, from the provider called `provider_name`.

    The front door, and the only path that enforces the private-mode pin. The
    order of the first three lines is the whole point:

    1. resolve the provider *class* - a bad name fails before anything else;
    2. `assert_allowed`, which raises `PrivacyRefused` for a pinned media and a
       non-local provider. No client, no request body, no socket exists yet, so
       there is nothing built that could leak if this raises;
    3. only then build the provider and call it.

    `PrivacyRefused` is not caught by `with_retry` - it is not an `LlmError` -
    so a refusal happens once and stays refused.

    Extra keyword arguments go to the provider's constructor (`num_ctx`,
    `host`, and the `client_factory` the tests inject).
    """
    cls = provider_class(provider_name)
    privacy.assert_allowed(conn, media_id, cls)

    provider = cls(conn, **provider_kwargs)
    return with_retry(lambda: provider.complete(request), attempts=attempts, sleep=sleep)


__all__ = [
    "PROVIDERS",
    "AuthError",
    "BadResponse",
    "ChatRequest",
    "ChatResponse",
    "ContextTooLong",
    "LlmError",
    "ModelNotFound",
    "NothingAnswered",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "PrivacyRefused",
    "Provider",
    "RateLimited",
    "ResolvedKey",
    "Unreachable",
    "api_key",
    "chat",
    "get_provider",
    "privacy",
    "provider_class",
    "retarget",
    "setting_key",
    "why_unavailable",
    "with_retry",
]


# --- what a request opens with -------------------------------------------------------

PROVIDER_SETTING = "llm_provider"
# MODEL_SETTING_PREFIX is imported from `base` above rather than spelled here:
# `OllamaProvider` reads the row it names, and it cannot import this package -
# the package imports it - so the one spelling lives below both (TASK-089.06).


def setting_value(conn, key: str) -> str:
    """One settings row, or "" - the only settings read this package does.

    Here rather than in `scribe.web.ai_ui`, where it used to live alone,
    because the runner child needs the same answer and a pipeline stage
    importing the web layer would drag FastAPI into a process that exists to
    hold a model (ADR-001 keeps that boundary in the other direction; this
    keeps it in this one).
    """
    from scribe import db

    with db.LOCK:
        row = conn.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return "" if row is None else str(row["value"] or "")


def default_provider(conn) -> str:
    """The provider a new request opens with, or "" when nobody has chosen.

    A missing row and a stored name this version no longer registers answer
    the same way, and neither raises: a provider can be removed from
    `PROVIDERS` by a later version, and a settings row from before that must
    not break every transcript page.

    "" rather than the shipped default (ADR-016, decided by Robert on
    2026-09-20). This row is the only place a choice is recorded, so a row
    that is not there means nobody chose - and the paths that act on that
    answer by themselves, the startup sweep above all, run with nobody at the
    screen to read a warning. Every caller handles "": it is what
    `setting_value` already answers, and `default_model(conn, "")` already
    degrades to "" as well.
    """
    stored = setting_value(conn, PROVIDER_SETTING).strip()
    return stored if stored in PROVIDERS else ""


def default_model(conn, provider_name: str) -> str:
    """The model this provider opens with: the saved one, else its own default."""
    stored = setting_value(conn, MODEL_SETTING_PREFIX + provider_name).strip()
    if stored:
        return stored
    cls = PROVIDERS.get(provider_name)
    return cls.default_model if cls is not None else ""
