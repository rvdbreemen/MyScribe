"""TASK-029 AC2: cleanup of media 1 and 12 through run_task, on two providers.

**This spends money and holds the GPU.** The cloud half is a real cleanup of
two recordings on `openrouter/auto`; the local half runs `qwen3.5:4b` over 12
chunks. It belongs in Robert's terminal, not a background task. Run it with
--fake first: that sends nothing anywhere and prints every line this would.

    .venv/Scripts/python scripts/task029_acceptance.py --fake
    .venv/Scripts/python scripts/task029_acceptance.py
    .venv/Scripts/python scripts/task029_acceptance.py --providers ollama

What it does, in order - and it stops at the first step that fails, before
anything is sent:

1. `media.private` is asserted 0 for every media asked for, on the live
   database opened read-only. A private recording never reaches a cloud call.
2. Each provider run gets its own fresh copy of `data/myscribe.db` (SQLite's
   backup API, so the WAL's pages come along). `run_task` writes to the copy;
   the live library is never touched. A copy is deleted afterwards unless
   --keep says otherwise.
3. `run_task(kind="cleanup")` - the app's own path: the plan, the hint, the
   resume check, the refusal of a cut-off part, the gate and the reading.
   Media 12's copy still holds row 14 (its old part 0, 184 segments); the
   segment check is what makes this run ask that chunk again.
4. Per part it prints the served model and upstream, `hint_sent`, reasoning
   tokens (Ollama: thinking characters, labelled so) and their share of the
   completion, cap - completion, `hint_ignored`, `cap_not_enforced`, the
   part's word ratio next to the overall one, a rough filler count before and
   after, whether the `[m:ss]` stamps and `SPEAKER_` labels survived, and
   whether the reading was published - and when it was refused, why.
5. One synthetic call - no recording text - through `OpenAIProvider.complete`
   on `gpt-5.6` with the hint and the app's temperature 0.2, counting wire
   calls: gpt-5.6 refused temperature 0.2 on 2026-09-06 and honoured
   `reasoning_effort: "none"` on 2026-09-11, so the expected sequence is
   400 (temperature) -> 200 with the hint still on. Skipped without an
   OpenAI key.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import httpx2  # noqa: E402

import task029_headroom as headroom  # noqa: E402  (the recording transport)
from scribe import db, env  # noqa: E402
from scribe.llm import base, openai_like, tasks  # noqa: E402

RUNS = {
    "openrouter": ("openrouter", "openrouter/auto"),
    "ollama": ("ollama", "qwen3.5:4b"),
}

STAMP = re.compile(r"\[\d+:\d{2}(?::\d{2})?\]")
SPEAKER = re.compile(r"SPEAKER_\d+")
FILLER = re.compile(r"\b(uh|uhm|um|umm|eh|ehm|er|erm|you know|like|zeg maar|nou ja)\b", re.I)
"""A rough count, English and Dutch: enough to see filler go, not a linguistic
measure - "like" is sometimes a verb."""


# --- the database, copied --------------------------------------------------------------


def read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def fresh_copy(source: Path, target: Path) -> sqlite3.Connection:
    """`source` into `target` through the backup API, then opened the way the
    app opens a database."""
    src = read_only(source)
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return db.connect(target)


# --- one run ---------------------------------------------------------------------------


def run_one(conn, media_id: int, provider_name: str, model: str, provider_kwargs: dict,
            recorder: headroom.RecordingTransport | None) -> None:
    title = conn.execute("SELECT title FROM media WHERE id=?", (media_id,)).fetchone()["title"]
    plan = tasks.plan_task(conn, media_id=media_id, kind="cleanup",
                           provider_name=provider_name, model=model)
    last_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM llm_output").fetchone()[0]
    wire_before = len(recorder.calls) if recorder else 0
    print(f"\n### media {media_id} {title!r} on {provider_name} ({model}): "
          f"{len(plan.chunks)} part(s), chunks {min(c.tokens for c in plan.chunks):,}-"
          f"{max(c.tokens for c in plan.chunks):,} est. tokens, cap {plan.max_output_tokens:,}")

    started = time.perf_counter()
    failure = None
    output_id = None
    try:
        output_id = tasks.run_task(conn, media_id=media_id, kind="cleanup",
                                   provider_name=provider_name, model=model, **provider_kwargs)
    except base.LlmError as exc:
        failure = f"{type(exc).__name__}: {exc}"
    seconds = time.perf_counter() - started

    new_rows = [dict(r) for r in conn.execute(
        "SELECT * FROM llm_output WHERE media_id=? AND id>? ORDER BY id", (media_id, last_id))]
    part_rows = [r for r in new_rows if tasks.is_chunk_kind(r["kind"])]
    final = next((r for r in new_rows if r["id"] == output_id), None)

    if final is not None and len(plan.chunks) <= 1:
        # One call: the final row is the only part. With a chunked run, every
        # part - reused or new - is named by chunk_output_ids.
        parts = [final]
    elif final is not None:
        ids = json.loads(final["params_json"]).get("chunk_output_ids") or []
        by_id = {r["id"]: dict(r) for r in conn.execute(
            f"SELECT * FROM llm_output WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
        parts = [by_id[i] for i in ids if i in by_id]
    else:
        parts = part_rows

    for row in parts:
        params = json.loads(row["params_json"] or "{}")
        index = int(params.get("chunk", 0))
        source = plan.chunks[index].text if index < len(plan.chunks) else ""
        print_part(index, row, params, source, plan.max_output_tokens,
                   reused=row["id"] <= last_id)

    if recorder is not None:
        sequence = [w.status for w in recorder.calls[wire_before:] if w.path.endswith("/chat/completions")]
        refused = sum(1 for s in sequence if s == 400)
        costs = [w.response.get("usage", {}).get("cost") for w in recorder.calls[wire_before:]
                 if isinstance(w.response, dict)]
        known = [c for c in costs if isinstance(c, (int, float))]
        print(f"  wire: {len(sequence)} chat calls ({refused} refused with 400), "
              f"cost ${sum(known):.5f} over {len(known)} that reported one")

    if failure is not None:
        print(f"  RESULT: failed after {seconds:.1f}s - {failure}")
        return
    verdict = tasks.check_cleaning([c.text for c in plan.chunks], [r["content"] for r in parts])
    reading = tasks.clean_reading(conn, plan.run_id)
    published = reading is not None and reading["llm_output_id"] == output_id
    print(f"  overall: {verdict['words_in']:,} -> {verdict['words_out']:,} words, "
          f"ratio {verdict['overall']:.2f}")
    if published:
        print(f"  RESULT: published in {seconds:.1f}s (clean_reading for run {plan.run_id} "
              f"-> llm_output {output_id})")
    else:
        print(f"  RESULT: stored as llm_output {output_id} in {seconds:.1f}s but REFUSED by the gate: "
              + "; ".join(verdict["reasons"]))


def print_part(index: int, row: dict, params: dict, source: str, cap: int, *, reused: bool) -> None:
    completion = row["completion_tokens"]
    tokens = params.get("reasoning_tokens")
    chars = params.get("reasoning_chars")
    if tokens is not None:
        reasoning = f"reasoning_tokens={tokens:,}"
        share = f" ({tokens / completion:.0%} of completion)" if completion else ""
    elif chars is not None:
        reasoning, share = f"thinking_chars={chars:,} (ollama: characters, not tokens)", ""
    else:
        reasoning, share = "reasoning=unreported", ""
    words_in, words_out = len(source.split()), len(row["content"].split())
    print(
        f"  part {index}{' (reused)' if reused else ''}: served={params.get('served_model')} "
        f"upstream={params.get('upstream')} hint_sent={params.get('hint_sent')} "
        f"{reasoning}{share} completion={completion} "
        f"cap-completion={cap - completion if completion is not None else 'unknown'} "
        f"hint_ignored={params.get('hint_ignored')} cap_not_enforced={params.get('cap_not_enforced')} "
        f"finish={params.get('finish_reason')}"
    )
    print(
        f"          words {words_in:,} -> {words_out:,} (ratio {words_out / words_in if words_in else 0:.2f}), "
        f"filler {len(FILLER.findall(source))} -> {len(FILLER.findall(row['content']))}, "
        f"[m:ss] {len(STAMP.findall(source))} -> {len(STAMP.findall(row['content']))}, "
        f"SPEAKER_ {len(SPEAKER.findall(source))} -> {len(SPEAKER.findall(row['content']))}"
    )


# --- the synthetic gpt-5.6 call ------------------------------------------------------------


def openai_sequence(fake: bool, conn: sqlite3.Connection) -> None:
    """`conn` is the live database, read-only, so the key resolves the way the
    app resolves it: the settings row first, then OPENAI_API_KEY."""
    print("\n### gpt-5.6 on api.openai.com: temperature 0.2 plus the hint (synthetic text)")
    recorder = headroom.RecordingTransport(fake_openai() if fake else httpx2.HTTPTransport())
    kwargs = {"client_factory": headroom.recording_factory(recorder)}
    if fake:
        kwargs["api_key"] = "fake-key"
    provider = openai_like.OpenAIProvider(conn, **kwargs)
    if not provider.resolve_key().found:
        print("  skipped: no OpenAI key (settings or OPENAI_API_KEY)")
        return
    request = base.ChatRequest(
        system="You clean transcripts. Keep every [m:ss] timestamp and speaker label.",
        user="Rewrite without filler: [0:00] SPEAKER_00: uh so um towels are, like, you know, really useful",
        model="gpt-5.6",
        temperature=0.2,
        max_output_tokens=300,
        reasoning_off=True,
    )
    try:
        answer = provider.complete(request)
    except base.LlmError as exc:
        answer = None
        print(f"  failed: {type(exc).__name__}: {exc}")
    for n, wire in enumerate(recorder.calls, 1):
        sent = wire.request if isinstance(wire.request, dict) else {}
        said = ""
        if wire.status and wire.status >= 400 and isinstance(wire.response, dict):
            said = f" -> {(wire.response.get('error') or {}).get('message', '')[:120]!r}"
        print(f"  call {n}: HTTP {wire.status} temperature={'temperature' in sent} "
              f"reasoning_effort={sent.get('reasoning_effort')!r}{said}")
    if answer is not None:
        print(f"  answer: hint_sent={answer.hint_sent} reasoning_tokens={answer.reasoning_tokens} "
              f"completion={answer.completion_tokens} text={answer.text[:100]!r}")


# --- fakes, for --fake -----------------------------------------------------------------------


def _echo_lines(user: str) -> str:
    lines = [line for line in user.splitlines() if STAMP.match(line)]
    return "\n\n".join(FILLER.sub("", line).replace("  ", " ") for line in lines)


def fake_openrouter() -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        hinted = (body.get("reasoning") or {}).get("enabled") is False
        text = _echo_lines(body["messages"][-1]["content"])
        answer = len(text) // 3
        reasoning = 0 if hinted else 5000
        return headroom._fake_answer("deepseek/deepseek-v4-flash-0731", "Wafer", text,
                                     answer + reasoning, reasoning, "stop")

    return httpx2.MockTransport(handler)


def fake_ollama():
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        text = _echo_lines(body["messages"][-1]["content"])
        return httpx2.Response(200, json={
            "model": body["model"], "created_at": "2026-09-11T09:00:00Z",
            "message": {"role": "assistant", "content": text,
                        **({} if body.get("think") is False else {"thinking": "Let me think..."})},
            "done": True, "done_reason": "stop", "prompt_eval_count": 1800,
            "eval_count": len(text) // 3,
        })

    def factory(*, base_url, timeout):
        return httpx2.Client(base_url=base_url, timeout=timeout, transport=httpx2.MockTransport(handler))

    return factory


def fake_openai() -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        if "temperature" in body:
            return httpx2.Response(400, json={"error": {
                "message": "Unsupported value: 'temperature' does not support 0.2 with this model. "
                           "Only the default (1) value is supported.",
                "param": "temperature", "code": "unsupported_value"}})
        reasoning = 0 if body.get("reasoning_effort") == "none" else 180
        return headroom._fake_answer("gpt-5.6-2026-08-01", None,
                                     "[0:00] SPEAKER_00: So towels are really useful.",
                                     12 + reasoning, reasoning, "stop")

    return httpx2.MockTransport(handler)


# --- the run -----------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--media", default="1,12")
    parser.add_argument("--providers", default="openrouter,ollama")
    parser.add_argument("--db", default=str(REPO / "data" / "myscribe.db"))
    parser.add_argument("--keep", action="store_true", help="keep the database copies")
    parser.add_argument("--skip-openai", action="store_true")
    parser.add_argument("--fake", action="store_true", help="canned providers; nothing leaves")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    media_ids = [int(m) for m in args.media.split(",") if m.strip()]
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    source = Path(args.db)

    print("=== TASK-029 AC2: cleanup through run_task ===")
    print(f"revision : {headroom.revision()}")
    print(f"mode     : {'FAKE - canned providers, nothing leaves this machine' if args.fake else 'LIVE - this spends money and holds the GPU'}")

    # 1. The pin, before anything is copied or sent.
    live = read_only(source)
    for media_id in media_ids:
        row = live.execute("SELECT private FROM media WHERE id=?", (media_id,)).fetchone()
        assert row is not None, f"no media {media_id}"
        assert int(row["private"]) == 0, f"media {media_id} is private: it never goes to a cloud provider"
    live.close()
    print(f"media    : {media_ids}, all private=0")
    for name in providers:
        assert name in RUNS, f"unknown provider run {name!r}; known: {sorted(RUNS)}"

    if not args.fake:
        env.load_dotenv()

    workdir = Path(tempfile.mkdtemp(prefix="task029-"))
    print(f"copies   : {workdir}{' (kept)' if args.keep else ' (deleted afterwards)'}")
    try:
        for name in providers:
            provider_name, model = RUNS[name]
            conn = fresh_copy(source, workdir / f"{name}.db")
            recorder = None
            if provider_name == "openrouter":
                recorder = headroom.RecordingTransport(
                    fake_openrouter() if args.fake else httpx2.HTTPTransport())
                kwargs: dict = {"client_factory": headroom.recording_factory(recorder)}
                if args.fake:
                    kwargs["api_key"] = "fake-key"
            else:
                kwargs = {"client_factory": fake_ollama()} if args.fake else {}
            print(f"\n=== {name}: a fresh copy, {provider_name} ({model}) ===")
            for media_id in media_ids:
                run_one(conn, media_id, provider_name, model, kwargs, recorder)
            conn.close()

        if not args.skip_openai:
            keys = read_only(source)
            try:
                openai_sequence(args.fake, keys)
            finally:
                keys.close()
    finally:
        if not args.keep:
            for path in workdir.glob("*"):
                try:
                    path.unlink()
                except OSError:
                    pass
            try:
                workdir.rmdir()
            except OSError:
                print(f"note     : could not remove {workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
