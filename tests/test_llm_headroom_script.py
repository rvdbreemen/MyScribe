"""`scripts/task029_headroom.py`, TASK-029's step 0: it plans what it says,
and it prints only what it knows.

The script is the measurement that decides whether `tasks.CONCAT_CHUNK_TOKENS`
stays, so it has to be able to plan chunks bigger than that constant. It
could not: `plan_task` clamps a concat chunk to the constant, so
`--chunk-tokens 6000` planned 3,000-token chunks and printed "7 chunks at
--chunk-tokens 6000: 1,722-2,993" (review of 3c679fd, 2026-09-11). Run here
with --fake on a tmp library, or on a canned transport: nothing leaves the
machine.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx2
import pytest

from scribe import db
from scribe.llm import tasks
from tests.seed import seed_media
from tests.test_llm_tasks import seed_long_run

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "task029_headroom.py"


def load_script(monkeypatch):
    """The script as a module. Registered in `sys.modules` for the length of
    the test because `@dataclass` looks its own module up there."""
    spec = importlib.util.spec_from_file_location("task029_headroom", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_the_headroom_script_plans_the_chunk_size_it_is_given(tmp_path, monkeypatch, capsys):
    path = tmp_path / "library.db"
    conn = db.connect(path)
    db.migrate(conn)
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=300)  # about 7,800 estimated tokens
    conn.close()
    shipped = tasks.CONCAT_CHUNK_TOKENS
    planned: list[tasks.TaskPlan] = []
    real_plan_task = tasks.plan_task

    def spy(*args, **kwargs):
        planned.append(real_plan_task(*args, **kwargs))
        return planned[-1]

    monkeypatch.setattr(tasks, "plan_task", spy)
    # Restores the constant at teardown whatever the script does to it, so a
    # failing run cannot leak a changed chunk limit into the tests after it.
    monkeypatch.setattr(tasks, "CONCAT_CHUNK_TOKENS", shipped)

    load_script(monkeypatch).main(
        ["--fake", "--db", str(path), "--media", str(media_id), "--chunk-tokens", "6000",
         "--first", "1"]
    )

    (plan,) = planned
    biggest = max(chunk.tokens for chunk in plan.chunks)
    assert shipped < biggest <= 6000, f"--chunk-tokens 6000 planned chunks of at most {biggest}"
    out = capsys.readouterr().out.splitlines()
    plan_line = next(line for line in out if line.startswith("plan"))
    assert "limit 6,000" in plan_line and f"{biggest:,}" in plan_line, plan_line
    verdict_line = next(line for line in out if line.startswith("verdict"))
    assert "6,000-token chunks" in verdict_line, verdict_line
    assert tasks.CONCAT_CHUNK_TOKENS == shipped, "the script left the shipped limit changed"


def seeded_library(tmp_path):
    path = tmp_path / "library.db"
    conn = db.connect(path)
    db.migrate(conn)
    media_id = seed_media(conn, title="Long one")
    seed_long_run(conn, media_id, n_segments=300)  # 3 chunks at 3,000
    conn.close()
    return path, media_id


RATE_LIMITED = {
    "error": {
        "message": "openai/gpt-5.6-luna is temporarily rate-limited upstream. Please retry "
        "shortly, or add your own key to accumulate your rate limits",
        "code": 429,
    }
}
"""What five of step 0's luna calls got on 2026-09-11 (R1 three, R2 two): HTTP
200, an `error` object and no usage. The message is as far as the run printed
it; the code is constructed."""


def first_chunk_rate_limited(module, monkeypatch):
    """--fake's canned OpenRouter, except that the first chunk call gets
    RATE_LIMITED. Pins (`provider.order`) and later calls pass through."""
    canned = module.fake_openrouter

    def transport():
        inner = canned()
        seen = {"chunks": 0}

        def handler(request):
            if request.method == "POST":
                body = json.loads(request.content)
                if not (body.get("provider") or {}).get("order"):
                    seen["chunks"] += 1
                    if seen["chunks"] == 1:
                        return httpx2.Response(200, json=RATE_LIMITED)
            return inner.handle_request(request)

        return httpx2.MockTransport(handler)

    monkeypatch.setattr(module, "fake_openrouter", transport)


@pytest.mark.parametrize(
    "flags, unanswered, answered",
    [
        ([], "hint=not-asked", "hint=not-asked"),
        (["--hint"], "hint=asked:no-answer", "hint=asked:sent"),
        (
            ["--hint", "--model", "google/gemini-3.5-flash-lite"],
            "hint=asked:no-answer",
            "hint=asked:dropped",
        ),
    ],
    ids=["no-hint", "hinted", "hinted-refused"],
)
def test_a_call_that_got_no_answer_prints_what_was_asked_not_a_guess(
    tmp_path, monkeypatch, capsys, flags, unanswered, answered
):
    """`hint_sent` means what the answering call carried (`ChatResponse`):
    None not asked, True sent, False refused and dropped. For a call that got
    no answer the script filled it from whether the last request carried the
    hint - so step 0's three rate-limited calls without the hint printed
    hint_sent=False, 'refused and dropped', for a hint nobody asked for (R1),
    and its two hinted ones printed True, 'sent', with no answer to have
    carried it (R2). What is known is what was asked; what the answer carried
    is known only when an answer comes. An answered call still takes the
    provider's own `hint_sent`, dropped hint included."""
    path, media_id = seeded_library(tmp_path)
    module = load_script(monkeypatch)
    first_chunk_rate_limited(module, monkeypatch)
    monkeypatch.setattr(tasks, "CONCAT_CHUNK_TOKENS", tasks.CONCAT_CHUNK_TOKENS)

    module.main(["--fake", "--db", str(path), "--media", str(media_id), "--first", "2", *flags])

    out = capsys.readouterr().out.splitlines()
    failed, served = [line for line in out if line.lstrip().startswith("chunk ")]
    assert "hint_sent=" not in failed, f"a call with no answer claims a hint state: {failed}"
    assert "rate-limited" in failed, "the first chunk has to be the one without an answer"
    assert unanswered in failed, failed
    assert answered in served, served


def pin_answer(finish: str, completion: int, *, prompt: int = 27, native: str | None = None) -> dict:
    """A pinned call's answer, in the shape OpenRouter sends."""
    choice = {
        "index": 0,
        "finish_reason": finish,
        "message": {"role": "assistant", "content": "One, two, three, four, five"},
    }
    if native is not None:
        choice["native_finish_reason"] = native
    return {
        "id": "gen-fake", "object": "chat.completion", "created": 1,
        "model": "google/gemini-3.5-flash-lite", "provider": "Google",
        "choices": [choice],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion, "cost": 0.0,
                  "completion_tokens_details": {"reasoning_tokens": 0}},
    }


@pytest.mark.parametrize(
    "answer, enforced",
    [
        # Measured 2026-09-11 by hand, the script's PIN_PROMPT pinned to Google
        # at max_tokens 64: blocked as recitation, some content, usage all zero.
        # The script's own pin calls to Google reported 0 completion tokens in
        # both gemini runs and printed enforced=True.
        (pin_answer("error", 0, prompt=0, native="RECITATION"), None),
        (pin_answer("error", 12), None),  # constructed: an error finish with a count
        (pin_answer("stop", 0, prompt=0), None),  # constructed: nothing counted
        # Measured 2026-09-11 by hand, Google on another prompt: 60 tokens,
        # 'length' at max_tokens 64. The cap held.
        (pin_answer("length", 60), True),
        (pin_answer("stop", 612), False),  # constructed: the cap ignored
    ],
    ids=["recitation-blocked", "error-finish", "zero-usage", "stopped-at-the-cap", "past-the-cap"],
)
def test_the_pin_check_reads_no_verdict_from_a_blocked_or_uncounted_answer(
    monkeypatch, answer, enforced
):
    """The pin check asks one upstream for about 600 tokens at max_tokens=64
    and reads the completion count. At most 64 said 'enforced' - including 0,
    which is what an answer blocked before anything was counted reports. So
    on Google, whose upstream blocked the prompt, step 0 printed a verdict
    the answer could not give. An answer that ended 'error' or counted
    nothing is 'unknown'; the other two rows keep the check from collapsing
    into 'unknown' for everything."""
    module = load_script(monkeypatch)
    recorder = module.RecordingTransport(
        httpx2.MockTransport(lambda request: httpx2.Response(200, json=answer))
    )
    sdk = module.recording_factory(recorder)(
        base_url=module.OPENROUTER, api_key="fake-key", default_headers={}, timeout=10.0
    )

    call = module.enforcement_check(sdk, recorder, "google/gemini-3.5-flash-lite", "Google")

    assert call.extra["enforced"] is enforced, call.extra["why"]
    assert call.extra["why"]
