"""Does this provider actually work from here? One call, about no recording.

The settings table can say a key was found and that a model list came back.
Neither of those has ever completed anything, and the three failures that
actually bite are all downstream of them: the daemon is installed but not
running, the key that resolves is the stale environment one rather than the
one just typed, the model id is plausible but 404s on this account
(`anthropic/claude-3.5-haiku`, measured). A provider test is the one thing
that answers all three, by doing the smallest real thing.

It is an `llm` job like everything else here, because ADR-001 gives the web
process no way to reach a provider: the button writes a job row and the runner
child makes the call.

**Why this does not go through `llm.chat()`.** That is the front door for a
*recording's words*, and its first act is `assert_allowed(conn, media_id, ...)`
- the private-mode pin. A probe has no media id because it is about no
recording: it opens no transcript, interpolates nothing, and sends two
constants from this module. There is no text of the user's in it, so there is
nothing for the pin to protect, and giving it a media id in order to walk past
that check would be worse than not having one. `tests/test_llm_selftest.py`
holds this to equality rather than to a promise: the request that reaches the
provider must *be* `PROBE_SYSTEM` and `PROBE_USER`, so no user text can appear
without a test going red - including on a machine where every recording is
pinned.

**One attempt.** `base.with_retry` is right when a caller wants an answer. A
button that asks "is this working" wants the truth about now, and three
attempts with backoff would turn a stopped daemon into fifteen seconds of
nothing and a rate limit into a clean bill of health.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass

from scribe import db, llm
from scribe.llm import base

KIND = "selftest"
"""The `llm` job kind. Not one of `tasks.KINDS` - it produces no `llm_output`
row and is about no media - so `llm_stage.HANDLERS` routes it to its own three
stages, the same way a chat turn is routed."""

PROBE_SYSTEM = "You are a connection test. Reply with one word and nothing else."
PROBE_USER = "Reply with the single word: ok"
"""The entire prompt. Module constants with no interpolation anywhere, which is
what makes "a probe carries nothing of the user's" checkable rather than
believable."""

PROBE_MAX_OUTPUT_TOKENS = 4000
"""A cap, not a spend.

The plan calls this a "one-token job" and for a cloud provider that is exactly
what it costs: the answer is one word, and the completion tokens the result
records say so. The *cap* has to be far larger, and for a measured reason
already written down in `tasks.DEFAULT_MAX_OUTPUT_TOKENS`: this machine's
shipped local default (`qwen3.5:4b`) is a thinking model that spent 2674
completion tokens before writing a word, and returned an empty answer with
`done_reason: "length"` under a 2000-token cap. A provider test that a working
local model always fails is worse than no provider test. A cloud provider bills
what was generated, so carrying the floor a local model needs costs it nothing.

Deliberately its own number rather than an import from `tasks`: a connection
test has no business depending on the six preset outputs.
"""

SETTING_PREFIX = "llm_test_"
"""One row per provider: `llm_test_<name>`, holding the last result as JSON.

A `setting` row rather than a table, following `doctor.SETTING_LAST` exactly -
this is one small fact per provider that only the settings page reads, and it
is replaced rather than accumulated, so a row is the right size for it.
"""

ANSWER_LIMIT = 200
"""How much of the answer is kept. It should be one word; a model that writes
an essay instead has still proved the connection works, and the row is not the
place to store the essay."""


class ProviderTestFailed(RuntimeError):
    """The probe reached a verdict and the verdict was no.

    Its own type, like `doctor.CheckFailed`, so the stage can record the result
    first and fail second without the failure being mistaken for a bug.
    """


@dataclass(frozen=True)
class ProbeResult:
    """What one provider test found, as the settings row shows it."""

    provider: str
    model: str
    ok: bool
    answer: str = ""
    detail: str = ""
    at: float = 0.0
    elapsed_s: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    job_id: int | None = None


def setting_key(provider_name: str) -> str:
    return SETTING_PREFIX + provider_name


def probe_request(model: str) -> base.ChatRequest:
    """The one request a provider test ever makes.

    Temperature 0: this asks a question with one right answer, and a sampled
    one would make a working provider look flaky. No `json_schema` - a
    connection test must not also be a test of schema-constrained decoding,
    which not every provider supports and which would fail for a reason that
    has nothing to do with the connection.
    """
    return base.ChatRequest(
        system=PROBE_SYSTEM,
        user=PROBE_USER,
        model=model,
        temperature=0.0,
        max_output_tokens=PROBE_MAX_OUTPUT_TOKENS,
    )


def probe(
    conn: sqlite3.Connection,
    *,
    provider_name: str,
    model: str | None = None,
    job_id: int | None = None,
    **provider_kwargs,
) -> ProbeResult:
    """Ask one provider to say one word; return what happened either way.

    An `LlmError` is a *result*, not an exception: "this provider is not
    working" is precisely what was asked, and the caller has to be able to
    record it before deciding what to do about it. A name that is not in
    `PROVIDERS` still raises - that is a caller with a bug, not a provider with
    a problem.
    """
    cls = llm.provider_class(provider_name)
    wanted = (model or "").strip() or cls.default_model
    started = time.perf_counter()

    try:
        response = cls(conn, **provider_kwargs).complete(probe_request(wanted))
    except base.LlmError as exc:
        return ProbeResult(
            provider=provider_name,
            model=wanted,
            ok=False,
            detail=str(exc),
            at=time.time(),
            elapsed_s=time.perf_counter() - started,
            job_id=job_id,
        )

    # The model that was *asked for*, not the snapshot id the API answers with
    # (`gpt-4o-mini-2026-09-02`). `tasks.store_output` records `plan.model` for
    # the same reason: the settings row has to say whether the id configured
    # for this provider works, and a snapshot id is not an id anyone can pick.
    return ProbeResult(
        provider=provider_name,
        model=wanted,
        ok=True,
        answer=(response.text or "").strip()[:ANSWER_LIMIT],
        at=time.time(),
        elapsed_s=time.perf_counter() - started,
        prompt_tokens=response.prompt_tokens,
        completion_tokens=response.completion_tokens,
        job_id=job_id,
    )


def store_result(conn: sqlite3.Connection, result: ProbeResult) -> None:
    """Remember this as the provider's last test, replacing the previous one."""
    with db.LOCK:
        conn.execute(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            (setting_key(result.provider), json.dumps(asdict(result))),
        )
        conn.commit()


def last_result(conn: sqlite3.Connection, provider_name: str) -> ProbeResult | None:
    """The stored result of this provider's last test, or None.

    None also for a row that cannot be read - hand-edited, or written by a
    version that shaped it differently. A settings page that shows nothing
    about one provider is better than one that will not load.
    """
    with db.LOCK:
        row = conn.execute(
            "SELECT value FROM setting WHERE key=?", (setting_key(provider_name),)
        ).fetchone()
    if row is None:
        return None
    try:
        payload = json.loads(row["value"])
        return ProbeResult(
            provider=str(payload["provider"]),
            model=str(payload.get("model") or ""),
            ok=bool(payload["ok"]),
            answer=str(payload.get("answer") or ""),
            detail=str(payload.get("detail") or ""),
            at=float(payload.get("at") or 0.0),
            elapsed_s=float(payload.get("elapsed_s") or 0.0),
            prompt_tokens=_maybe_int(payload.get("prompt_tokens")),
            completion_tokens=_maybe_int(payload.get("completion_tokens")),
            job_id=_maybe_int(payload.get("job_id")),
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def _maybe_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
