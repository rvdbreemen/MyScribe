"""TASK-029 step 0: how much room does a cleanup chunk leave for reasoning?

**This spends money.** Every chunk is a real OpenRouter call, and step 0 as
designed is about 42 of them plus one 64-token call per upstream. Run it only
with Robert's go-ahead, and run it with --fake first: that sends nothing
anywhere and shows every line of output this would print.

    .venv/Scripts/python scripts/task029_headroom.py --fake
    .venv/Scripts/python scripts/task029_headroom.py --model openai/gpt-5.6-luna --repeats 3
    .venv/Scripts/python scripts/task029_headroom.py --model google/gemini-3.5-flash-lite --repeats 3
    .venv/Scripts/python scripts/task029_headroom.py --model google/gemini-3.5-flash-lite --hint
    .venv/Scripts/python scripts/task029_headroom.py --model openrouter/auto --hint

What it does, in order:

1. **Nothing it can store.** `data/myscribe.db` is opened read-only
   (`file:...?mode=ro`); `run_task` is never called and no row is written.
   `media.private` is asserted 0 before any call leaves this machine, and
   `plan_task` asserts the privacy pin again.
2. **The chunks the app would send.** `plan_task(kind="cleanup",
   budget_tokens=--chunk-tokens)`. Media 12 at 3,000 plans 7 chunks of
   1,722-2,993 estimated tokens (read-only, 2026-09-11; the design's
   "2,953-2,993" is the six head chunks - the tail is the short one). Each
   chunk's `ChatRequest` is built as `tasks._ask` builds a cleanup part, with
   the hint only when --hint says so, and goes through the real
   `OpenRouterProvider.complete()`.
3. **Every wire call kept.** The provider's `client_factory` is the transport
   seam the tests use; here it wraps the real transport and records each
   request and response - a 400, a content-less 'length' answer - before the
   SDK or `complete()` can turn it into an exception. That is what keeps
   `usage` on a failed call. The recorder is asserted non-empty after the
   first call, so a broken seam stops the run before it spends more.
4. **Which upstreams hold the cap.** For each provider name OpenRouter lists
   for the model (`/models/{slug}/endpoints`), one call pinned to it
   (`provider.order=[name]`, `allow_fallbacks=false`) at `max_tokens=64` on a
   prompt that needs about 600 tokens: the upstream enforces the cap iff it
   answers with at most 64 completion tokens. Per name, not per endpoint:
   luna listed 7 endpoints under 3 names on 2026-09-11, and a chunk answer
   names only the provider - so a name's endpoints (its tags, printed) share
   one verdict, which is an approximation this output says out loud. Azure's
   luna endpoints list `max_completion_tokens` and not the `max_tokens` the
   app sends, which is the case this check exists to catch. For
   `openrouter/auto` the (model, upstream) pairs are only known after the
   chunk calls, so they are checked then. A pinned call that errors, or comes
   back from another upstream than the one named, is 'unknown' - never a
   verdict.
5. **The verdict.** A chunk counts only when its upstream enforced the cap
   and the answer reported `reasoning_tokens`. A counted chunk passes when it
   ends 'stop' with content and spent at most half its reserve on reasoning,
   the reserve being `cap - 1.07 x chunk estimate` (about 2,790 for a 3,000
   chunk, so about 1,395). A subject with no counted chunk is NOT MEASURED,
   never passed. Chunks on a non-enforcing upstream are listed as 'cap not
   enforced': step 0 is no evidence about them (TASK-029's case 3).

What it prints per call: model, upstream, the wire sequence (`400 -> 200`),
finish, completion and reasoning tokens (or 'unknown'), cap - completion, the
reserve, and `usage.cost`; then a summary per (model, upstream) and the total
cost of every recorded call.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import httpx2  # noqa: E402
import openai  # noqa: E402

from scribe import env  # noqa: E402
from scribe.llm import base, openai_like, tasks  # noqa: E402

OPENROUTER = openai_like.OpenRouterProvider.base_url
ANSWER_SHARE = 1.07
"""A part at the gate's ceiling is at most about 1.07 x its chunk estimate
(tasks.CONCAT_CHUNK_TOKENS has the arithmetic)."""

PIN_CAP = 64
PIN_PROMPT = (
    "Count from one to two hundred in English words, separated by commas. "
    "Write the list only, nothing before or after it."
)
"""About 600 tokens of answer: far past PIN_CAP, so an upstream that does not
enforce max_tokens shows it in one cheap call."""


# --- every wire call, kept ------------------------------------------------------------


@dataclass
class Wire:
    method: str
    path: str
    request: Any
    status: int | None
    response: Any
    seconds: float
    error: str | None = None


class RecordingTransport(httpx2.BaseTransport):
    """The real transport (or a fake one), with every exchange written down
    before anybody downstream gets to raise about it."""

    def __init__(self, inner: httpx2.BaseTransport):
        self.inner = inner
        self.calls: list[Wire] = []

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        started = time.perf_counter()
        sent = _json_or_text(request.content)
        try:
            response = self.inner.handle_request(request)
            response.read()
        except Exception as exc:  # a timeout or a refused connection is a record too
            self.calls.append(
                Wire(request.method, request.url.path, sent, None, None,
                     time.perf_counter() - started, error=f"{type(exc).__name__}: {exc}")
            )
            raise
        self.calls.append(
            Wire(request.method, request.url.path, sent, response.status_code,
                 _json_or_text(response.content), time.perf_counter() - started)
        )
        return response

    def close(self) -> None:
        self.inner.close()


def _json_or_text(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw[:400].decode("utf-8", "replace")


def recording_factory(recorder: RecordingTransport):
    """A `client_factory` for the OpenAI-shaped providers: the real SDK, on
    the recording transport."""

    def factory(*, base_url, api_key, default_headers, timeout):
        return openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers=default_headers or {},
            timeout=timeout,
            max_retries=0,
            http_client=httpx2.Client(transport=recorder, timeout=timeout),
        )

    return factory


# --- reading a recorded answer ---------------------------------------------------------


@dataclass
class Call:
    label: str
    model: str | None
    upstream: str | None
    statuses: list[int | None]
    finish: str | None
    completion: int | None
    reasoning: int | None
    content: bool
    cost: float | None
    estimate: int | None = None
    hint_sent: bool | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


def read_call(label: str, wire: list[Wire], *, estimate: int | None = None) -> Call:
    """What the last exchange of one logical call says, and the statuses of
    all of them (a dropped hint is `400 -> 200`)."""
    statuses = [w.status for w in wire]
    last = next((w for w in reversed(wire) if isinstance(w.response, dict)), None)
    payload = last.response if last is not None else {}
    choices = payload.get("choices") or [{}]
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    body = last.request if last is not None and isinstance(last.request, dict) else {}
    return Call(
        label=label,
        model=payload.get("model"),
        upstream=payload.get("provider"),
        statuses=statuses,
        finish=(choices[0] or {}).get("finish_reason"),
        completion=usage.get("completion_tokens"),
        reasoning=details.get("reasoning_tokens"),
        content=bool(((choices[0] or {}).get("message") or {}).get("content")),
        cost=usage.get("cost"),
        estimate=estimate,
        hint_sent=("reasoning" in body) if body else None,
        error=next((w.error for w in reversed(wire) if w.error), None),
    )


# --- the cap: which upstreams hold it ------------------------------------------------------


def endpoints(client: httpx2.Client, key: str, slug: str) -> list[dict]:
    response = client.get(
        f"{OPENROUTER}/models/{slug}/endpoints", headers={"Authorization": f"Bearer {key}"}
    )
    if response.status_code != 200:
        print(f"  endpoints for {slug}: HTTP {response.status_code} {response.text[:160]}")
        return []
    return list(((response.json() or {}).get("data") or {}).get("endpoints") or [])


def enforcement_check(sdk: Any, recorder: RecordingTransport, slug: str, upstream: str) -> Call:
    """One call pinned to `upstream` at max_tokens=64. `extra['enforced']` is
    True, False, or None when the call cannot say."""
    before = len(recorder.calls)
    why = None
    try:
        sdk.chat.completions.create(
            model=slug,
            messages=[{"role": "user", "content": PIN_PROMPT}],
            max_tokens=PIN_CAP,
            temperature=0.2,
            extra_body={"provider": {"order": [upstream], "allow_fallbacks": False}},
        )
    except openai.OpenAIError as exc:
        why = f"{type(exc).__name__}: {str(exc)[:160]}"
    call = read_call(f"pin {slug} @ {upstream}", recorder.calls[before:])
    if why is not None:
        call.extra["enforced"], call.extra["why"] = None, why
    elif (call.upstream or "").casefold() != upstream.casefold():
        call.extra["enforced"] = None
        call.extra["why"] = f"routed to {call.upstream!r}, not the {upstream!r} it was pinned to"
    elif call.completion is None:
        call.extra["enforced"], call.extra["why"] = None, "no usage came back"
    else:
        call.extra["enforced"] = call.completion <= PIN_CAP
        call.extra["why"] = f"{call.completion} completion tokens at max_tokens={PIN_CAP}"
    return call


# --- printing ------------------------------------------------------------------------------


def num(value: Any) -> str:
    return "unknown" if value is None else f"{value:,}" if isinstance(value, int) else str(value)


def print_call(call: Call, cap: int) -> None:
    reserve = cap - math.ceil(ANSWER_SHARE * call.estimate) if call.estimate else None
    headroom = cap - call.completion if call.completion is not None else None
    wire = " -> ".join(str(s) for s in call.statuses) or "none"
    cost = "unknown" if call.cost is None else f"${call.cost:.5f}"
    print(
        f"  {call.label:<22} model={call.model or '?'} upstream={call.upstream or '?'} "
        f"wire={wire} finish={call.finish} completion={num(call.completion)} "
        f"reasoning={num(call.reasoning)} cap-completion={num(headroom)} "
        f"reserve={num(reserve)} content={'yes' if call.content else 'NO'} "
        f"hint_sent={call.hint_sent} cost={cost}"
        + (f" error={call.error}" if call.error else "")
    )


def verdict_for(call: Call, cap: int) -> bool:
    reserve = cap - ANSWER_SHARE * (call.estimate or 0)
    return (
        call.finish == "stop"
        and call.content
        and call.reasoning is not None
        and call.reasoning <= reserve / 2
    )


def summarise(calls: list[Call], enforced: dict[tuple[str, str], bool | None], cap: int) -> str:
    """Per (model, upstream), then one verdict for the run."""
    groups: dict[tuple[str, str], list[Call]] = {}
    for call in calls:
        groups.setdefault((call.model or "?", call.upstream or "?"), []).append(call)

    print("\n--- per (model, upstream) ---")
    counted_any = False
    failed_any = False
    for (model, upstream), group in sorted(groups.items()):
        holds = enforced.get((model, upstream))
        counted = [c for c in group if holds is True and c.reasoning is not None]
        reasoning = [c.reasoning for c in counted]
        headroom = [cap - c.completion for c in group if c.completion is not None]
        finishes: dict[str, int] = {}
        for c in group:
            finishes[str(c.finish)] = finishes.get(str(c.finish), 0) + 1
        if holds is False:
            status = "cap not enforced - not counted"
        elif holds is None:
            status = "enforcement unknown - not counted"
        elif not counted:
            status = "no reasoning_tokens reported - not counted"
        else:
            passed = [verdict_for(c, cap) for c in counted]
            status = "PASS" if all(passed) else f"FAIL ({passed.count(False)} of {len(passed)})"
            counted_any = True
            failed_any = failed_any or not all(passed)
        print(
            f"  {model} @ {upstream}: calls={len(group)} counted={len(counted)} "
            f"finish={finishes} reasoning min/median/max="
            + (
                f"{min(reasoning)}/{statistics.median(reasoning):g}/{max(reasoning)}"
                if reasoning
                else "-"
            )
            + f" min(cap-completion)={min(headroom) if headroom else '-'} -> {status}"
        )
    if failed_any:
        return "FAIL"
    return "PASS" if counted_any else "NOT MEASURED"


# --- a fake OpenRouter, for --fake ------------------------------------------------------------


def fake_openrouter() -> httpx2.MockTransport:
    """Answers the three requests this script makes, in the shapes the live
    API used on 2026-09-10/11, so --fake runs every line of the real path.
    OpenAI holds the cap and Azure does not; a hinted gemini call is refused
    the way google/gemini-3.5-flash-lite refused it."""
    seen = {"chunks": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.method == "GET" and request.url.path.endswith("/endpoints"):
            return httpx2.Response(200, json={"data": {"endpoints": [
                {"provider_name": "OpenAI", "tag": "openai", "status": 0,
                 "supported_parameters": ["max_tokens"]},
                {"provider_name": "OpenAI", "tag": "openai/flex", "status": 0,
                 "supported_parameters": ["max_tokens"]},
                {"provider_name": "Azure", "tag": "azure/us", "status": -2,
                 "supported_parameters": ["max_completion_tokens"]},
            ]}})
        body = json.loads(request.content)
        model = body["model"] if body["model"] != "openrouter/auto" else "deepseek/deepseek-v4-flash-0731"
        pinned = (body.get("provider") or {}).get("order")
        if pinned:
            spent = PIN_CAP if pinned[0] == "OpenAI" else 612
            return _fake_answer(model, pinned[0], "1, 2, 3", spent, 0,
                                "length" if spent == PIN_CAP else "stop")
        hinted = (body.get("reasoning") or {}).get("enabled") is False
        if hinted and "gemini" in model:
            return httpx2.Response(400, json={"error": {
                "message": "Reasoning is mandatory for this endpoint and cannot be disabled.",
                "code": 400}})
        seen["chunks"] += 1
        user = body["messages"][-1]["content"]
        lines = [line for line in user.splitlines() if line.startswith("[")]
        estimate = sum(max(1, math.ceil(len(w) / 3)) for w in " ".join(lines).split())
        reasoning = 0 if hinted else 900
        answer = int(estimate * 0.9)
        return _fake_answer(model, "OpenAI" if seen["chunks"] % 2 else "Azure",
                            "\n".join(lines), answer + reasoning, reasoning, "stop")

    return httpx2.MockTransport(handler)


def _fake_answer(model, upstream, content, completion, reasoning, finish) -> httpx2.Response:
    return httpx2.Response(200, json={
        "id": "fake", "object": "chat.completion", "created": 1, "model": model,
        "provider": upstream,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": finish}],
        "usage": {"prompt_tokens": 100, "completion_tokens": completion,
                  "total_tokens": 100 + completion, "cost": 0.0,
                  "completion_tokens_details": {"reasoning_tokens": reasoning}},
    })


# --- the run -----------------------------------------------------------------------------------


def revision() -> str:
    try:
        rev = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return rev + (" (uncommitted changes)" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--media", type=int, default=12)
    parser.add_argument("--chunk-tokens", type=int, default=3000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--model", default="openai/gpt-5.6-luna")
    parser.add_argument("--hint", action="store_true", help="send reasoning {enabled:false}")
    parser.add_argument("--first", type=int, default=0, help="only the first N chunks (0: all)")
    parser.add_argument("--timeout", type=float, default=openai_like.DEFAULT_TIMEOUT,
                        help="seconds, as the app's provider uses (default %(default)s)")
    parser.add_argument("--db", default=str(REPO / "data" / "myscribe.db"))
    parser.add_argument("--fake", action="store_true", help="a canned OpenRouter; nothing leaves")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    print("=== TASK-029 step 0: cleanup chunk headroom ===")
    print(f"revision : {revision()}")
    print(f"model    : {args.model}   hint: {args.hint}   repeats: {args.repeats}")
    print(f"mode     : {'FAKE - a canned OpenRouter, nothing leaves this machine' if args.fake else 'LIVE - this spends money'}")

    conn = sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    media = conn.execute("SELECT id, title, private FROM media WHERE id=?", (args.media,)).fetchone()
    assert media is not None, f"no media {args.media}"
    assert int(media["private"]) == 0, f"media {args.media} is private: it never goes to a cloud provider"

    plan = tasks.plan_task(
        conn, media_id=args.media, kind="cleanup", provider_name="openrouter",
        model=args.model, budget_tokens=args.chunk_tokens,
    )
    chunks = list(plan.chunks[: args.first] if args.first else plan.chunks)
    cap = plan.max_output_tokens
    sizes = [c.tokens for c in plan.chunks]
    print(f"media    : {args.media} {media['title']!r}, private=0")
    print(f"plan     : {len(sizes)} chunks at --chunk-tokens {args.chunk_tokens}: "
          f"{min(sizes):,}-{max(sizes):,} estimated tokens, cap {cap:,}; sending {len(chunks)}")

    recorder = RecordingTransport(fake_openrouter() if args.fake else httpx2.HTTPTransport())
    if args.fake:
        provider = openai_like.OpenRouterProvider(
            conn, client_factory=recording_factory(recorder), api_key="fake-key", timeout=args.timeout)
    else:
        env.load_dotenv()
        provider = openai_like.OpenRouterProvider(
            conn, client_factory=recording_factory(recorder), timeout=args.timeout)
    key = provider.resolve_key()
    assert key.found, "no OpenRouter key (settings, OPENROUTER_TOKEN or OPENROUTER_API_KEY)"
    sdk = provider.client(key.value or "")
    lister = httpx2.Client(transport=recorder, timeout=30)

    enforced: dict[tuple[str, str], bool | None] = {}

    def check(slug: str, upstream: str) -> None:
        call = enforcement_check(sdk, recorder, slug, upstream)
        enforced[(slug, upstream)] = call.extra["enforced"]
        print(f"  {slug} @ {upstream}: enforced={call.extra['enforced']} ({call.extra['why']})"
              f" cost={'unknown' if call.cost is None else f'${call.cost:.5f}'}")

    if args.model != "openrouter/auto":
        print(f"\n--- does each upstream of {args.model} hold max_tokens? ---")
        # One check per provider name, not per endpoint: on 2026-09-11 luna
        # listed 7 endpoints under 3 names (OpenAI x3 - tags openai, openai/flex,
        # openai/fast; Azure x3; Amazon Bedrock), and a chunk answer names only
        # the provider, so a verdict per tag could not be matched to a chunk.
        # Pinning by name lets OpenRouter pick among that name's endpoints.
        by_name: dict[str, list[str]] = {}
        for endpoint in endpoints(lister, key.value or "", args.model):
            name = str(endpoint.get("provider_name") or "")
            if name:
                by_name.setdefault(name, []).append(
                    f"{endpoint.get('tag')} (status {endpoint.get('status')})")
        for name, tags in by_name.items():
            print(f"  {name}: {', '.join(tags)}")
            check(args.model, name)

    print(f"\n--- the chunks ({'with' if args.hint else 'without'} the hint) ---")
    calls: list[Call] = []
    for repeat in range(1, args.repeats + 1):
        for chunk in chunks:
            request = base.ChatRequest(
                system=tasks.system_for(plan.spec),
                user=tasks.user_prompt(plan.spec, transcript=chunk.text, title=plan.title,
                                       duration=plan.duration, custom_prompt=plan.custom_prompt),
                model=plan.model,
                max_output_tokens=cap,
                json_schema=None,
                reasoning_off=args.hint,
            )
            before = len(recorder.calls)
            error = None
            answer = None
            try:
                answer = provider.complete(request)
            except base.LlmError as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
            assert recorder.calls, "the recording transport saw nothing: stop before spending more"
            call = read_call(f"chunk {chunk.index + 1}/{len(plan.chunks)} rep {repeat}",
                             recorder.calls[before:], estimate=chunk.tokens)
            if answer is not None:
                call.hint_sent = answer.hint_sent
            call.error = call.error or error
            calls.append(call)
            print_call(call, cap)

    unchecked = {(c.model, c.upstream) for c in calls if c.model and c.upstream} - set(enforced)
    if unchecked:
        print("\n--- the upstreams the chunks were served by, checked now ---")
        for slug, upstream in sorted(unchecked):
            check(slug, upstream)

    verdict = summarise(calls, enforced, cap)
    costs = [w.response.get("usage", {}).get("cost") for w in recorder.calls
             if isinstance(w.response, dict)]
    known = [c for c in costs if isinstance(c, (int, float))]
    print(f"\ncost     : ${sum(known):.5f} over {len(known)} of {len(recorder.calls)} recorded calls"
          f" that reported one")
    print(f"verdict  : {verdict} for {args.model} at {args.chunk_tokens:,}-token chunks"
          f" ({'hinted' if args.hint else 'no hint'})")
    return 0 if verdict != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
