---
id: "ADR-031"
title: "Ollama is asked never to truncate, and the token estimate is calibrated against measured tokenizer counts"
status: "Accepted"
date: "2026-10-25"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
related:
  - "ADR-029"
topics:
  - "ollama"
  - "context-window"
  - "chunking"
aliases:
  - "truncate"
  - "estimate_tokens"
  - "halving signature"
components:
  - "scribe/llm/ollama.py"
  - "scribe/llm/chunking.py"
symbols:
  - "estimate_tokens"
  - "halved_prompt_tokens"
  - "CONTEXT_REFUSAL"
context_scope: "selective"
format: "madr"
---

<!-- markdownlint-disable MD025 -->

# ADR-031 Ollama is asked never to truncate, and the token estimate is calibrated against measured tokenizer counts

## Status

Accepted, 2026-10-25.

## Status History

```yaml
status_history:
  - date: 2026-10-24
    status: Proposed
    changed_by: Claude (agent, session 2026-10-24)
    reason: Initial proposal
    changed_via: adr-kit
  - date: 2026-10-25
    status: Accepted
    changed_by: Robert van den Breemen (said yes in session 2026-10-25; recorded by Claude)
    reason: Robert accepted ADR-031 in the session of 2026-10-25 with the truncate probe and the calibration in front of him
    changed_via: adr-kit lifecycle
```

## Context and Problem Statement

On 2026-10-23, on a copy of media 61 (2h28m, Dutch) with qwen3.5:4b and a
32,768-token window, two minutes notes of 87,180 and 89,374 characters came
back with `prompt_eval_count` 16,386 = 32,768 / 2 + 2 (TASK-124). The daemon
had dropped the front half of each chunk, answered HTTP 200, and the job ended
done on half a transcript. A blog note of 81,018 characters read 32,016
tokens: the planner's three characters a word token had under-counted.

Measured 2026-10-24 on Ollama 0.40.8 (scratchpad `run-124`):

* `probe.out`: a 7,909-token prompt at `num_ctx` 512 reads 1,026 tokens, HTTP 200. With `"truncate": false` the same request is HTTP 400 "request (7909 tokens) exceeds the available context size (2048 tokens)".
* `shiftprobe.out`: `shift` true, false or absent changes nothing; a full window stops at prompt + answer = 2,048 with `done_reason: length`.
* `allocprobe.out`: without the key the count is (max(num_ctx, 2048) + 1) // 2 + 2 (2049 reads 1,027; 6001 reads 3,003; below 2048 always 1,026).
* `tok-qwen.out`, `tok-gemma.out`: 1,000 lines of `[m:ss]` read 8,000 tokens on both models, `[h:mm:ss]` 10,000; every digit, colon, bracket and line break is a token. Dutch prose reads 3.69 (qwen) and 4.01 (gemma) characters a token.
* `calib.out`: six slices of media 61's transcript, real qwen counts 11,404 to 12,803. The old estimate was 14-26% under; the new one 15-19% over.

## Decision Drivers

* An answer about half a transcript must never be stored as done.
* Planning happens before any call and must be deterministic, so an interrupted job resumes on the same chunks.
* A long recording must still be possible on a local model.

## Considered Options

* **A.** Send `"truncate": false`, keep a check for the halving signature, and price tokens with a pessimistic estimate calibrated on measured counts.
* **B.** Count every chunk with the model's own tokenizer through the daemon before planning.
* **C.** Keep the old estimate and plan against the 8,192 floor, as in 0.9.0 and 0.9.1.

## Decision Outcome

Chosen option: **A**, proposed by the agent. On 2026-10-24 Robert asked for
this record: Ollama never truncates, the estimate is calibrated on measured
counts. He did not weigh A against B and C; the agent wrote those labels.
B makes the plan depend on a running daemon and costs a prompt evaluation per
chunk. C is honest (ContextTooLong) but leaves local AI unable to take a long
recording.

### Confirmation

Unit tests pin the rules below. On a first copy of the library (its jobs 430-432,
2026-10-24), minutes on media 61 with qwen3.5:4b read four chunks whole:
every `prompt_eval_count` (24,007, 24,691, 24,373, 1,698) at or below its
planned estimate, none equal to 16,386, job done. Blog with qwen3.5:4b failed
twice on chunk 0 at its 4,000-token note budget (22,293 prompt tokens), a
spent cap, not a full window.

## Decision Contract

### Must

* Every Ollama `/api/chat` body carries `"truncate": false`.
* An HTTP 400 carrying the daemon's context refusal (`CONTEXT_REFUSAL`) is `ContextTooLong`.
* A `prompt_eval_count` equal to `halved_prompt_tokens(window)` is `ContextTooLong`, with the window this call asked for.
* `estimate_tokens` prices a run of letters at three characters a token, every other character at one token, and each non-blank line at one more.
* The estimate is at least the measured qwen3.5:4b count of the test fixture; a new tokenizer measurement that beats it changes the estimate, not the test.

### Must Not

* Send `shift`; it was measured to change nothing.
* Store an answer whose prompt count matches the halving signature.
* Ask the daemon for a token count while planning.

### Exceptions

* A prompt whose real count is exactly the halving signature is refused too; one count in half a window is the accepted price.

### Verification

* `tests/test_llm_ollama.py::test_the_body_asks_the_daemon_never_to_truncate`, `::test_a_daemon_that_refuses_to_truncate_is_context_too_long`, `::test_a_prompt_the_daemon_halved_is_refused`, `::test_the_halving_signature_is_the_window_the_daemon_really_ran`, `::test_a_count_next_to_the_signature_is_not_refused`.
* `tests/test_llm_chunking.py::test_estimate_tokens_is_pessimistic_at_three_letters_a_token`, `::test_the_estimate_is_not_below_what_the_local_tokenizer_counted`, `::test_a_timestamp_and_a_speaker_label_cost_a_token_a_character`.

## Consequences

### Positive

* A prompt that does not fit is a refusal with a reason, on new and old daemons alike.

### Negative

* Long recordings split into more chunks: more calls, a longer job.
* Text without spaces (Chinese, Japanese) can still be under-counted; then the 400 or the signature check refuses it, after the call.
* The calibration covers Dutch on qwen3.5 and gemma4 only. Other languages and models are not measured.

## Open Questions

- [x] Does blog on media 61 finish with qwen3.5:9b, every chunk read whole? Robert's choice B for TASK-124 AC2, to tell a 4B limit from a prompt problem. — **Answered 2026-10-24 by Claude (agent, session 2026-10-24):** No, and not because of the window. On a second, fresh library copy (its job 430), chunk 0 of 3 read 22,293 prompt tokens against 26,687 planned (not the 16,386 halving signature): read whole. The first call went entirely into thinking, the ADR-029 retry with think:false copied the transcript line by line until the 4,000-token note budget, and the job failed. The 4B did the same (first copy, jobs 431 and 432, also 10,708 characters). So the blog note prompt invites copying on both models; that is a prompt problem outside this decision.

## Related Decisions

* ADR-029: unchanged - an Ollama answer lost to thinking is still asked once more with `think: false`; that retry carries `truncate: false` too.

## References

* TASK-124; scratchpad `run-124` of session 2026-10-24: `probe.out`, `shiftprobe.out`, `allocprobe.out`, `tok-qwen.out`, `tok-gemma.out`, `calib.out`, `evidence-430.txt`, `evidence-431-432.txt`, `evidence-r3-blog-9b.txt`, `ollama-log-r3.txt`.
* `scribe/llm/ollama.py`: `_body`, `_answer`, `_raise_for_status`, `halved_prompt_tokens`; `scribe/llm/chunking.py`: `estimate_tokens`.
