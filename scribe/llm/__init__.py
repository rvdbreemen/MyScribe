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
    "with_retry",
]


# --- what a request opens with -------------------------------------------------------

PROVIDER_SETTING = "llm_provider"
MODEL_SETTING_PREFIX = "llm_model_"


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
    """The provider a new request opens with.

    A stored name that is no longer registered falls back to the shipped
    default rather than raising: a provider can be removed from `PROVIDERS` by
    a later version, and a settings row from before that must not break every
    transcript page.
    """
    from scribe.llm import tasks

    stored = setting_value(conn, PROVIDER_SETTING).strip()
    return stored if stored in PROVIDERS else tasks.DEFAULT_PROVIDER


def default_model(conn, provider_name: str) -> str:
    """The model this provider opens with: the saved one, else its own default."""
    stored = setting_value(conn, MODEL_SETTING_PREFIX + provider_name).strip()
    if stored:
        return stored
    cls = PROVIDERS.get(provider_name)
    return cls.default_model if cls is not None else ""
