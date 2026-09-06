"""Scripted fake stages for exercising the runner and supervisor in tests.

Job type "fake" runs three fixed stages (one, two, three); what each
stage does is driven by params["scenario"]:

  ok    - every stage ticks progress and returns (the default)
  boom  - stage two raises RuntimeError
  slow  - stage one sleeps 10 s in 0.1 s slices, honouring cancelled()

No real work happens here; these fakes exist so the job-system state
machine can be proven without any GPU or media in sight.
"""

import time

_SLOW_TOTAL_SECONDS = 10.0
_SLOW_SLICE_SECONDS = 0.1


def _scenario(ctx) -> str:
    return ctx.params.get("scenario", "ok")


def _tick_progress(ctx) -> None:
    for progress in (0.25, 0.5, 0.75, 1.0):
        ctx.report(progress)


def _sleep_cancellable(ctx, total: float) -> None:
    deadline = time.monotonic() + total
    while time.monotonic() < deadline:
        if ctx.cancelled():
            return
        time.sleep(_SLOW_SLICE_SECONDS)
        ctx.report(1.0 - max(deadline - time.monotonic(), 0.0) / total)


def stage_one(ctx) -> None:
    if _scenario(ctx) == "slow":
        _sleep_cancellable(ctx, _SLOW_TOTAL_SECONDS)
    else:
        _tick_progress(ctx)


def stage_two(ctx) -> None:
    if _scenario(ctx) == "boom":
        raise RuntimeError("scripted failure in fake stage two")
    _tick_progress(ctx)


def stage_three(ctx) -> None:
    _tick_progress(ctx)


# Ordered registry entry for job type "fake" (consumed by runner.STAGES).
STAGES: list[tuple[str, callable]] = [
    ("one", stage_one),
    ("two", stage_two),
    ("three", stage_three),
]
