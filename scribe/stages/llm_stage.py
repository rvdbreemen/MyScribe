"""The `llm` job type: ask a model about a transcript, in a runner child.

This is the whole reason the web process never has to touch a provider
(ADR-001). A rail button enqueues a job of type `llm` with
`{media_id, kind, provider, model, prompt?}`; this module is what the runner
child then walks. The web layer only ever enqueues, reads `llm_output` rows and
renders stored text.

Two kinds of work come through here and they share the three stages: one of the
six preset outputs (`scribe/llm/tasks.py`) and a chat turn
(`scribe/llm/chat_tool.py`). The stages are about *when* a mistake is caught,
not about what is being asked, so both walk the same three and `handler_for`
decides which module does the work at each one.

Three stages, and the split is not cosmetic:

* **prepare** decides everything that can be decided for free - the kind, the
  provider, the model, whether this media may be sent there at all, how many
  calls it will take. Every mistake that costs nothing to catch is caught here,
  before a single token is paid for, and the jobs board says `prepare` when one
  is.
* **generate** makes the calls. It is the only stage that can be slow, the only
  one that spends money, and the one whose progress is worth watching: a chunk
  of a long recording ticks the bar.
* **store** writes the final row. On its own because "the answer exists" and
  "the answer is saved" are different facts, and the event this emits is what
  tells the panel which row to render.

The GPU lease comes free with the job type: an `llm` job goes through the same
supervisor queue as a `transcribe` job, so a local model and Whisper can never
be on the card at the same time (Global Constraints).

Cancellation is cooperative, as everywhere else in the pipeline, and here it is
checked between calls - the only place it can be: a request already sent to a
provider is not ours to interrupt. A cancelled map-reduce keeps the chunk rows
it has paid for, so retrying it resumes rather than starts again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING, Callable

from scribe import jobs, llm
from scribe.llm import chat_tool, selftest, tasks
from scribe.stages.transcribe import Cancelled

if TYPE_CHECKING:  # avoids a runtime import cycle: runner imports this module
    from scribe.runner import RunnerContext

JOB_TYPE = "llm"
"""Registered in `scribe.runner.STAGES`. One type for all six kinds and for
chat: the kind is a parameter, not a job type, so the queue, the GPU lease, the
privacy rules and the board stay one thing."""


def _media_id(ctx: "RunnerContext") -> int:
    """Which recording this job is about.

    The params are asked first and the column second. They are normally the
    same, and when they are not it is the params that say what was requested -
    the column exists so the library can show a job under its media row.
    """
    media_id = ctx.params.get("media_id") or ctx.job.get("media_id")
    if not media_id:
        raise ValueError("an llm job needs a media_id, in its params or on the job row")
    return int(media_id)


def _kind(ctx: "RunnerContext") -> str:
    """Which of the two things an `llm` job can be asked to do this one is.

    A preset output or a chat turn. Both go through these three stages, because
    the stages are about *when* a mistake is caught rather than about what is
    being asked; what differs is which module the stage hands the work to.
    """
    kind = (ctx.params.get("kind") or "").strip()
    if not kind:
        raise ValueError(
            "an llm job needs a kind in its params; one of: "
            + ", ".join((*tasks.KINDS, chat_tool.CHAT_KIND))
        )
    return kind


def _provider(ctx: "RunnerContext") -> str:
    """Which provider this job names.

    Refused here rather than answered from the settings row, for the same
    reason `_kind` and `_media_id` are refused here: the params are the
    request, and a request that names nobody is not one this stage may decide
    on somebody's behalf (ADR-016). Reading the row instead would be the same
    fall-through through another door - by the time a job runs, whoever made
    it is long gone. A job written before that rule still carries the name it
    was given and runs unchanged.
    """
    provider_name = (ctx.params.get("provider") or "").strip()
    if not provider_name:
        raise ValueError(
            "an llm job needs a provider in its params; nothing is sent until somebody "
            "has chosen one in Settings > AI providers (/settings#llm-providers)"
        )
    return provider_name


def task_prepare(ctx: "RunnerContext") -> None:
    """Work out what to ask, of whom - and refuse now if it cannot be asked."""
    kind = _kind(ctx)

    plan = tasks.plan_task(
        ctx.conn,
        media_id=_media_id(ctx),
        kind=kind,
        provider_name=_provider(ctx),
        model=ctx.params.get("model"),
        custom_prompt=ctx.params.get("prompt"),
        run_id=ctx.params.get("run_id"),
        context_tokens=ctx.params.get("context_tokens"),
        max_output_tokens=ctx.params.get("max_output_tokens"),
        budget_tokens=ctx.params.get("budget_tokens"),
    )
    ctx.state["plan"] = plan
    # The key every stage_perf row of this job is filed under. Without it the
    # runner would file these timings under `perf_model_for(params)`, which
    # reads the same "model" param through the Whisper tier rules - right by
    # accident today, and wrong the moment a job leaves the model to the
    # provider's default.
    ctx.state["model"] = plan.model

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "llm-plan",
        # `task`, not `kind`: `jobs.emit` spends that name on the event's own
        # kind, and a payload key of the same name lands on it instead.
        task=plan.kind,
        provider=plan.provider_name,
        model=plan.model,
        run_id=plan.run_id,
        chunks=len(plan.chunks),
        budget_tokens=plan.budget_tokens,
    )
    ctx.report(1.0)


def _progress(ctx: "RunnerContext") -> Callable[[float], None]:
    """A progress callback that also honours a cancel between two calls.

    Piggy-backing on progress rather than passing a second callback into
    `tasks.generate` keeps the job's vocabulary out of that module: it reports
    what it has finished, and what a cancel means is this layer's business.
    """

    def report(fraction: float) -> None:
        if ctx.cancelled():
            raise Cancelled("cancelled between calls of an llm job")
        ctx.report(fraction)

    return report


def _watch(ctx: "RunnerContext") -> Callable[[str, Any, Any], None]:
    """Turn each call into two live-log events: what went, and what came back.

    Watching only (ADR-014). Nothing reads these rows to decide anything, and
    `tasks._ask` swallows whatever this raises - a job must not fail because
    somebody was looking at it.

    Excerpted by `tasks.excerpt`, and deliberately in two events rather than
    one: the prompt is knowable the moment the call goes out, and on a local
    27B model the reply can be five minutes later. One event would mean
    watching a job that says nothing until it is over.
    """

    def watch(phase: str, request: Any, response: Any) -> None:
        if response is None:
            # The call is going out now. Said here rather than with the reply
            # so the log shows what is being asked while it is being asked.
            jobs.emit(
                ctx.conn,
                ctx.job["id"],
                "llm-prompt",
                phase=phase,
                model=getattr(request, "model", ""),
                system=tasks.excerpt(getattr(request, "system", "") or "", limit=300),
                prompt=tasks.excerpt(getattr(request, "user", "") or ""),
                prompt_chars=len(getattr(request, "user", "") or ""),
            )
            return
        jobs.emit(
            ctx.conn,
            ctx.job["id"],
            "llm-reply",
            phase=phase,
            reply=tasks.excerpt(getattr(response, "text", "") or ""),
            reply_chars=len(getattr(response, "text", "") or ""),
            prompt_tokens=getattr(response, "prompt_tokens", None),
            completion_tokens=getattr(response, "completion_tokens", None),
            finish_reason=getattr(response, "raw_finish_reason", "") or "",
        )

    return watch


def task_generate(ctx: "RunnerContext") -> None:
    """Make the calls. The slow stage, and the only one that spends anything."""
    ctx.state["result"] = tasks.generate(
        ctx.conn, ctx.state["plan"], on_progress=_progress(ctx), on_call=_watch(ctx)
    )


def task_store(ctx: "RunnerContext") -> None:
    """Write the answer, and say where it landed."""
    plan: tasks.TaskPlan = ctx.state["plan"]
    result: tasks.TaskResult = ctx.state["result"]
    output_id = tasks.store_output(ctx.conn, plan, result)
    ctx.state["llm_output_id"] = output_id

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "llm",
        output_id=output_id,
        task=plan.kind,
        provider=plan.provider_name,
        model=plan.model,
        calls=result.calls,
        chunks=len(plan.chunks),
        prompt_tokens=result.response.prompt_tokens,
        completion_tokens=result.response.completion_tokens,
    )
    # What it decided, in the kind's own terms: a live log that ends with
    # "stored output 7" has not said what the job was for (TASK-085).
    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "llm-conclusion",
        task=plan.kind,
        conclusion=tasks.conclusion(plan.kind, result.response.text),
    )
    ctx.report(1.0)


def task_apply(ctx: "RunnerContext") -> None:
    """Do what the answer says, for the kinds that change something.

    Five of the kinds answer a question and this is a no-op for them. For the
    rest it is the step that used to be a person clicking: the mapping reaches
    the speaker labels, the labels reach the recording, inside the job that
    produced them.

    A failure here fails the job, deliberately. The alternative - storing the
    answer, swallowing the error and reporting success - recreates exactly the
    state this stage exists to remove, except now it also lies about it.
    """
    plan: tasks.TaskPlan = ctx.state["plan"]
    hook = plan.spec.apply
    if hook is None:
        ctx.report(1.0)
        return

    result: tasks.TaskResult = ctx.state["result"]
    report = hook(ctx.conn, plan, result.payload, ctx.state["llm_output_id"])

    jobs.emit(ctx.conn, ctx.job["id"], "llm-apply", task=plan.kind, **(report or {}))
    ctx.report(1.0)


# --- chat ---------------------------------------------------------------------------------


def chat_prepare(ctx: "RunnerContext") -> None:
    """Decide what the model gets to read, and refuse now if it may not read it."""
    media_id = _media_id(ctx)
    question = (ctx.params.get("question") or "").strip()
    if not question:
        raise ValueError("a chat job needs a question in its params; there is nothing to answer")

    # The question arrives in the params because it was just typed; the history
    # is whatever this recording's conversation already holds, because that is
    # the only place it lives. A caller with its own idea of the history - a
    # test, a replay - may still pass one.
    history = ctx.params.get("history")
    if history is None:
        history = chat_tool.history_for(ctx.conn, media_id)

    plan = chat_tool.plan_chat(
        ctx.conn,
        media_id=media_id,
        question=question,
        provider_name=_provider(ctx),
        model=ctx.params.get("model"),
        history=history,
        run_id=ctx.params.get("run_id"),
        context_tokens=ctx.params.get("context_tokens"),
        max_output_tokens=ctx.params.get("max_output_tokens"),
        budget_tokens=ctx.params.get("budget_tokens"),
    )
    ctx.state["plan"] = plan
    # As in `task_prepare`: without this the runner files these timings under
    # `transcribe.perf_model_for(params)`, which reads the same "model" param
    # through the Whisper tier rules.
    ctx.state["model"] = plan.model

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "llm-plan",
        task=plan.kind,
        provider=plan.provider_name,
        model=plan.model,
        run_id=plan.run_id,
        segments=len(plan.segment_ids),
        whole_transcript=plan.whole_transcript,
        budget_tokens=plan.budget_tokens,
    )
    ctx.report(1.0)


def chat_generate(ctx: "RunnerContext") -> None:
    """Ask. One call: what to read was already decided, so there is nothing to
    chunk and nothing to resume - a cancelled chat turn has cost one request."""
    ctx.state["answer"] = chat_tool.answer(ctx.conn, ctx.state["plan"])
    ctx.report(1.0)


def chat_store(ctx: "RunnerContext") -> None:
    """Record the exchange, and say which rows to render."""
    plan: chat_tool.ChatPlan = ctx.state["plan"]
    answer: chat_tool.ChatAnswer = ctx.state["answer"]
    question_id, message_id = chat_tool.store_exchange(
        ctx.conn, media_id=plan.media_id, question=plan.question, answer=answer
    )
    ctx.state["chat_message_id"] = message_id

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "chat",
        message_id=message_id,
        question_id=question_id,
        task=plan.kind,
        provider=plan.provider_name,
        model=plan.model,
        citations=list(answer.citations),
        segments=len(plan.segment_ids),
        prompt_tokens=answer.response.prompt_tokens if answer.response else None,
        completion_tokens=answer.response.completion_tokens if answer.response else None,
    )
    ctx.report(1.0)


# --- the provider test -----------------------------------------------------------------------


def probe_prepare(ctx: "RunnerContext") -> None:
    """Work out which provider and which model, and refuse a bad name now.

    No `_media_id` here, and that is the point rather than an omission: a
    provider test is about no recording (`scribe/llm/selftest.py`). It sends
    two module constants, so there is no transcript to fetch, no privacy check
    to make and no media row to find - a job that needed one could not run
    before a single file had been added.
    """
    provider_name = _provider(ctx)
    cls = llm.provider_class(provider_name)  # an unknown name fails for free, here
    model = (ctx.params.get("model") or "").strip() or cls.default_model

    ctx.state["provider_name"] = provider_name
    ctx.state["probe_model"] = model
    # As in `task_prepare`: the runner would otherwise file these timings under
    # the Whisper tier rules, which know nothing about a provider's model ids.
    ctx.state["model"] = model
    ctx.report(1.0)


def probe_generate(ctx: "RunnerContext") -> None:
    """Make the one call. A provider that cannot answer is an answer.

    `selftest.probe` returns rather than raises for an `LlmError`, so the
    verdict survives into `probe_store` and gets written down before the job
    is failed - `doctor.gpu_stage` stores its red checks the same way, and for
    the same reason: a failed test is the most useful test there is.
    """
    ctx.state["result"] = selftest.probe(
        ctx.conn,
        provider_name=ctx.state["provider_name"],
        model=ctx.state["probe_model"],
        job_id=ctx.job["id"],
    )
    ctx.report(1.0)


def probe_store(ctx: "RunnerContext") -> None:
    """Record the verdict where the settings page reads it, then deliver it."""
    result: selftest.ProbeResult = ctx.state["result"]
    selftest.store_result(ctx.conn, result)

    jobs.emit(
        ctx.conn,
        ctx.job["id"],
        "llm-test",
        provider=result.provider,
        model=result.model,
        ok=result.ok,
        answer=result.answer,
        detail=result.detail,
        elapsed_s=round(result.elapsed_s, 3),
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
    ctx.report(1.0)

    if not result.ok:
        raise selftest.ProviderTestFailed(f"{result.provider}: {result.detail}")


# --- which of the three --------------------------------------------------------------------


@dataclass(frozen=True)
class Handler:
    """The stage functions for one kind of `llm` job.

    `apply` is optional and most kinds have none: a summary answers a question
    and changes nothing. The kinds that DO change something - naming a speaker,
    labelling a recording - used to leave their answer sitting in a row until
    somebody pressed a button, and that state is what the fourth stage removes.
    """

    prepare: Callable[["RunnerContext"], None]
    generate: Callable[["RunnerContext"], None]
    store: Callable[["RunnerContext"], None]
    apply: Callable[["RunnerContext"], None] | None = None


TASK_HANDLER = Handler(task_prepare, task_generate, task_store, task_apply)
CHAT_HANDLER = Handler(chat_prepare, chat_generate, chat_store)
PROBE_HANDLER = Handler(probe_prepare, probe_generate, probe_store)

HANDLERS: dict[str, Handler] = {
    chat_tool.CHAT_KIND: CHAT_HANDLER,
    selftest.KIND: PROBE_HANDLER,
}
"""Kinds that are not one of `tasks.KINDS`. A lookup rather than a branch in
each of the three stages, so "what kind of work is this" is asked once and in
one place; an unknown kind falls through to the tasks handler, which is what
already names the six it knows in its error."""


def handler_for(kind: str) -> Handler:
    return HANDLERS.get(kind, TASK_HANDLER)


def prepare(ctx: "RunnerContext") -> None:
    handler_for(_kind(ctx)).prepare(ctx)


def generate(ctx: "RunnerContext") -> None:
    handler_for(_kind(ctx)).generate(ctx)


def store(ctx: "RunnerContext") -> None:
    handler_for(_kind(ctx)).store(ctx)


def apply(ctx: "RunnerContext") -> None:
    """A handler without an apply step reports done and changes nothing, so the
    stage costs a job that has nothing to apply one function call."""
    hook = handler_for(_kind(ctx)).apply
    if hook is None:
        ctx.report(1.0)
        return
    hook(ctx)


STAGES: list[tuple[str, Callable]] = [
    ("prepare", prepare),
    ("generate", generate),
    ("store", store),
    ("apply", apply),
]
