---
id: "ADR-010"
title: "Reasoning is a per-kind hint; an ignored hint is bounded only where the endpoint enforces the cap"
status: "Proposed"
date: "2026-09-11"
binding: false
gate: null
documents_shipped: false
verified_in: []
supersedes: []
superseded_by: null
format: "madr"
topics:
  - "llm"
  - "reasoning-models"
  - "output-budget"
  - "provenance"
aliases:
  - "reasoning hint"
  - "reasoning_off"
  - "think false"
  - "reasoning effort none"
  - "finish_reason length"
  - "cap not enforced"
  - "cleanup chunk size"
components:
  - "scribe.llm.base"
  - "scribe.llm.openai_like"
  - "scribe.llm.ollama"
  - "scribe.llm.tasks"
symbols:
  - "ChatRequest.reasoning_off"
  - "Provider.reasoning_off_body"
  - "ChatResponse.hint_sent"
  - "TaskSpec.reasoning_off"
  - "reasoning_record"
  - "CONCAT_CHUNK_TOKENS"
  - "check_cleaning"
  - "cleaned_parts"
context_scope: "selective"
---

<!-- markdownlint-disable MD025 -->

# ADR-010 Reasoning is a per-kind hint; an ignored hint is bounded only where the endpoint enforces the cap

## Status

Proposed, 2026-09-11. Written with the TASK-029 implementation; a human
accepts it. Revised the same day, still Proposed: step 0 is measured and
keeps the 3,000-token chunk (Chunks); the cloud cleanup cap stays where it
is, and this record adopts a reworded AC3 for TASK-029 (Open Questions -
decided by the agent under a relayed instruction, for Robert to confirm by
accepting or to reverse; the backlog task's AC3 text is unchanged); and the
review's corrections are in (Decision Contract, Consequences).

## Status History

```yaml
status_history:
  - date: 2026-09-11
    status: Proposed
    changed_by: Claude Opus 5 (agent, session 2026-09-11)
    reason: Initial proposal
    changed_via: adr-kit
```

## Context and Problem Statement

A reasoning model thinks in hidden tokens before it writes its answer, and
most endpoints count those tokens against the same output cap
(`max_tokens`) as the answer. MyScribe's cleanup kind rewrites a transcript
chunk by chunk, so its answer is as long as its input, and it failed with
"no message content (finish_reason=length)" on real recordings. Measured on
2026-09-10 and 2026-09-11 (TASK-029):

* Media 1 (837 Dutch words, one chunk) through `openrouter/auto`:
  deepseek-v4-flash-0731 spent 22,515 and 21,512 reasoning tokens of 23,918
  and 22,589 completion tokens, 63-84 s, and came back 'stop' - past the
  6,000 cap. With OpenRouter's `reasoning: {enabled: false}`: 1,171 tokens,
  0 of them reasoning, 8.5 s, 94% of the words kept, stamps and speaker
  labels intact. With `effort: low`: 1,417 tokens, but the `[m:ss]` stamps
  and speaker labels were dropped. One sample of each, on one chunk; in the
  2026-09-11 live check deepseek with `enabled: false` kept 37 of 41 stamps
  on media 1 and between 10 of 81 and 96 of 99 per part on media 12, so the
  stamp difference between the two settings is not established (Open
  Questions).
* Media 12's chunk 0 succeeded on gpt-5.6-luna with 4,946 of 6,000 tokens
  (row 14); later chunks ended 'length'. That failure is recorded only in
  TASK-029's description: no job row in the live database carries it, and its
  upstream was not recorded. Step 0 did not reproduce it on 2026-09-11
  (Chunks). The speakers kind hit the same failure in the startup sweep
  (job 154, cap 8,000).
* Rows 6, 17 and 19 spent 5,651 (cap 4,000), 12,767 and 21,401 (cap 8,000),
  all deepseek, all 'stop': some upstreams do not enforce the cap at all.
* `openrouter/auto` has no `reasoning` object in `/models`, so whether the
  model it picks has mandatory reasoning, or what its default effort is,
  cannot be read before the call. All 28 deepseek-v4-flash-0731 upstreams
  list `max_tokens` in `supported_parameters`, so enforcement cannot be read
  there either.

The question is how the app talks to reasoning models, per provider, and what
bounds the spend when a model ignores what it is asked.

## Decision Drivers

* No caller may branch on the provider (the rule `scribe/llm/base.py` states
  at its top): the provider-specific part has to live in the provider classes.
* A mechanical kind should not pay for reasoning it does not need.
* An answer cut off at the cap must never be stored as if it were whole.
* What a call spent, and whether the cap held, has to be visible on the row,
  because the failure modes above were invisible until measured by hand.
* The pattern has to work when the model is chosen per request by a router.

## Considered Options

* **A. A per-kind hint, mapped per provider; send, detect, bound.**
* B. A reasoning effort ladder (`enabled:false`, then `minimal`, then drop).
* C. Raise the cloud cleanup cap (for example to 32,000).
* D. `provider.require_parameters: true` on OpenRouter.
* E. Do nothing: rely on TASK-026's length gate alone.

## Decision Outcome

Chosen option: **A**. `TaskSpec.reasoning_off` becomes
`ChatRequest.reasoning_off` on every call a kind makes, and each provider
class turns it into its own wire field (`Provider.reasoning_off_body`):
OpenRouter `reasoning: {enabled: false}` (sent through the `extra_body` of
the OpenAI SDK (software development kit)), OpenAI `reasoning_effort:
"none"`, Ollama `think: false`. What bounds a model that ignores the hint
depends on the endpoint, and the decision says so rather than promising more:

```text
                         hint honoured?
                   yes /              \ no (refused and dropped, or ignored)
                      /                \
         case 1: the hint           endpoint enforces max_tokens?
         is the bound             yes /                   \ no
                                     /                     \
                    case 2: reasoning + answer       case 3: only the hint
                    stay under the cap; a heavy      bounds it; the call can
                    reasoner ends 'length' and is    return 'stop' past the
                    refused before it is stored      cap, and the row says so
```

1. **Hint honoured: the hint is the bound.** Measured at 0 reasoning tokens:
   deepseek-v4-flash-0731 via `openrouter/auto` (3 calls, upstream Wafer
   where recorded, and 8 more parts in the 2026-09-11 live check, all Wafer);
   openai/gpt-5.6-luna via OpenRouter (upstream Azure; 107 reasoning tokens
   on the same puzzle without the hint; 0 on every hinted step-0 call that
   answered - 23 of 25, the other 2 got the rate-limit envelope, no usage);
   gpt-5.6 on api.openai.com; qwen3.5:4b (0 thinking characters -
   re-measured through the working tree's `OllamaProvider` on 2026-09-11:
   107 tokens and a correct cleaning with the hint; all 600 tokens spent
   thinking and no answer without it).
2. **Hint refused or ignored, and the endpoint enforces the cap:** reasoning
   plus answer stay under `max_output_tokens`. A light reasoner fits in the
   chunk reserve (`CONCAT_CHUNK_TOKENS`): at 3,000-token chunks luna on Azure
   without the hint spent 134-1,301 reasoning tokens per chunk and left at
   least 3,010 (step 0 R1, Chunks). A heavy one ends 'length', which every
   cleanup path refuses before storing: openai/gpt-5-mini, whose hint
   OpenRouter refused, spent 5,632 of 6,000 tokens reasoning on a
   2,993-token chunk of media 12 in the one call tried (upstream Azure,
   which a pinned 64-token call showed enforces the cap; 126.3 s), and its
   part was refused with nothing stored. The spend is at most the cap per
   chunk, and the first failed chunk ends the run.
3. **Hint refused or ignored, and the endpoint does not enforce the cap:**
   the hint is the only bound. The call returns 'stop' past the cap and the
   cleaning gate still judges the text. Seen without a hint (rows 6, 17, 19
   and the 2026-09-10 calls); never seen with the hint. Every row records
   `hint_sent`, `reasoning_tokens`, `upstream`, `hint_ignored` and
   `cap_not_enforced`, so this case shows up when it happens.

**A refused hint.** Any HTTP (Hypertext Transfer Protocol) 400 - the
status a server gives a request it will not serve - while the hint is on the
wire gets one more call without it, except the temperature refusal (checked
first, by `exc.param`) and a context-length refusal. The rule is structural -
it never matches vendor wording - because the three refusals measured share
none: OpenRouter's "Reasoning is mandatory for this endpoint and cannot be
disabled." (google/gemini-3.5-flash-lite, and openai/gpt-5-mini on
2026-09-11), gpt-4o-mini's "Unrecognized request argument supplied:
reasoning_effort" (the OpenAI provider's default model), and gpt-5 /
gpt-6-astra's `unsupported_value`. Each knob is dropped at most once, so a
call is at most three requests. After the drop the model reasons at its own
default and case 2 or 3 applies; the row says `hint_sent=false`. In step 0
the drop worked on all 25 gemini calls (400, then 200 without the hint).

**Per kind.** `cleanup`: hint on. `speakers` and `labels`: no hint yet - they
are queued without a person (finalize, the startup sweep) and `speakers`
writes names unattended, so they get the same mappings only after an A/B on a
database copy. Every other kind, chat and the provider self-test: no hint, and
byte-identical requests to before. `effort: low` or `minimal`: used nowhere,
on evidence that is weak (Open Questions).

**Chunks.** A cleanup chunk is at most `CONCAT_CHUNK_TOKENS` = 3,000
estimated tokens, its own constant so a later cap change buys reasoning room
rather than bigger chunks. Derived: a part at the gate ceiling is about 1.07 x
its chunk, so a 5,999-token chunk needs about 6,420 tokens from a 6,000 cap,
while a 3,000 chunk needs about 3,210 and leaves about 2,790 for reasoning.

Step 0 kept it: measured 2026-09-11 with `scripts/task029_headroom.py` on
media 12 (7 chunks of 1,722-2,993 at 3,000; 4 of 1,605-5,999 at 6,000),
through OpenRouter, $0.357 for six runs and six manual probes. Cap 6,000
throughout; "least left" is the cap minus the largest completion.

| Run | Model @ upstream | Chunk limit | Hint | Answered | Finish | Reasoning tokens | Least left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| R1 | gpt-5.6-luna @ Azure | 3,000 | not sent | 18 of 21 (3 rate-limited) | all 'stop' | 134-597; tail 504-1,301 | 3,010 |
| R2 | gpt-5.6-luna @ Azure | 3,000 | sent | 19 of 21 (2 rate-limited) | all 'stop' | 0 | 3,415 |
| R4 | gemini-3.5-flash-lite @ Google | 3,000 | refused, dropped | 21 of 21 | all 'stop' | 0 reported | 2,895 |
| R3 | gpt-5.6-luna @ Azure | 6,000 | sent | 4 of 4 | all 'stop' | 0 | 1,147 |
| R5 | gpt-5.6-luna @ Azure | 6,000 | not sent | 4 of 4 | all 'stop' | 233-516 | 997 |
| R6 | gemini-3.5-flash-lite @ Google | 6,000 | refused, dropped | 4 of 4 | chunk 2 'length' | 0 reported | 4 |

* The rule step 0 had to pass - every counted chunk ends 'stop' with content
  and spends at most half its reserve (about 1,395 tokens on a full chunk)
  reasoning - held at 3,000 for both models; for gemini, with Google's
  enforcement taken from a manual probe rather than the script's pin check,
  which misread Google (last bullet). 3,000 runs are 3 repeats, 6,000
  runs one; "rate-limited" is OpenRouter's error envelope (HTTP 200, no
  usage), not a cap failure.
* The deciding reason is margin, not a luna failure: at 6,000 luna finished
  every chunk, hinted and not, but left 997-1,801 on the full chunks, about a
  third of the room at 3,000. The 2026-09-10 'length' on its later chunks did
  not reproduce. gemini's second 6,000 chunk ended 'length' at 5,996 of 6,000
  with 0 reasoning tokens reported: as far as its usage shows, the answer
  alone overran the cap - the first measured case of what the constant
  guards against, in one run. R6's chunk 3 came back short (3,779 tokens)
  and was not checked against the cleaning gate.
* gemini reported 0 reasoning tokens on all 25 answers although OpenRouter
  calls its reasoning mandatory. The prompt tokens implied by `usage.cost`
  stay constant across repeats (chunk 3: 3,258, 3,258 and 3,250), so no
  hidden reasoning appears to be billed - inferred from that arithmetic, not
  reported. Its largest part at 3,000 was 3,105 tokens for a 2,974-token
  chunk (1.04x), inside the derived band.
* What it does not show: one recording, and one upstream per model. OpenAI
  and Amazon Bedrock (luna) and Google AI Studio (gemini) answered a pinned
  call with 404 'No endpoints found' - this account's routing filters them
  out - so their enforcement is unknown, and step 0 says nothing about case
  3. The script's per-chunk rule cannot judge 6,000-token chunks: the
  reserve, 6,000 - 1.07 x 5,999, is negative (-386 to -419), so its FAIL for
  R3 and R5 is arithmetic, not model evidence; finish and tokens left were
  read instead. Its pin check also misread Google's answer: 0 completion
  tokens, printed as enforced=True in R4 and R6, where a manual probe with
  the same prompt showed Google blocking it as recitation (finish 'error',
  usage all zero). The check now says 'unknown' for such an answer (Open
  Questions). Google's enforcement rests on another manual probe (60
  completion tokens at `max_tokens` 64, finish 'length') and on R6's
  'length'.

**A cleaning that is a copy.** The cleaning gate refuses a reading in which
every part came back as it went in, word for word with only whitespace
ignored: nothing was cleaned, and it would publish the transcript under the
name of its cleaning. Punctuation counts as a change, because fixing it is
the job. A word-count ratio cannot see a copy: it sits at 1.0, in the middle
of the band.

What it sees is narrower than "a copy". The `[m:ss]` stamps and speaker
labels count as words, and `cleanup.md` asks for paragraphs that keep only
the stamp and label starting each stretch - not for whitespace. So the rule
catches a copy that kept every line's stamp and label; a copy regrouped the
way the prompt asks, words untouched, drops the inner ones and passes (a
known gap, shown on constructed text). It can also refuse an honest answer,
in one narrow case: every line starts its own stretch (a one-segment
recording, or speakers alternating line by line) and the text needs no
fixing, so the answer asked for is the input with blank lines added. That
reading is stored and not published, and the recording keeps what it had:
its transcript, or an earlier cleaning of the same run, which
`apply_cleanup` leaves in place because it writes only on a pass.

That guards a total copy, and the live check produced none; the copy it did
produce still publishes. qwen3.5:4b with `think: false` answered media 12's
part 0 with its input's counts, twice - 963 words in and out, 45 of 45
stamps, ratio 1.000, 1,667 completion tokens both times. The answers were
not kept, only their counts: 5,486 characters, the chunk's 5,442 plus one
for each of its 44 line breaks, consistent with a blank line added between
lines and nothing else. So "a copy" is inferred from counts, not compared.
The other ten parts changed words (ratios 0.79-0.999), so the reading
published at 0.894 and 0.902, as it did before the rule; replayed through
the gate with part 0 as its own source text and the recorded counts
elsewhere, both readings pass with no reason. Part 0 was not a stretch with
nothing to tidy: 43 of its 45 lines continue the speaker before them
(counted on a backup copy of the library), and `cleanup.md` keeps only the
stamp that starts each stretch. Whether one such part should refuse the
reading is open (Open Questions).

### Confirmation

* The suite (Verification below), run in halves on 2026-09-11 after the
  second review's fixes: 1,217 and 811 passed (1,209 and 811 before them;
  the 8 new cases are the headroom script's).
* `scripts/task029_headroom.py --fake` after those fixes prints the hint as
  `hint=not-asked` and, for gemini hinted, `hint=asked:dropped`; the pins
  now print their finish.
* Step 0 live on 2026-09-11 (Chunks): six runs of
  `scripts/task029_headroom.py` and six manual probes, $0.357.
* AC2 and AC3 live on 2026-09-11, in an independent check: a scratch script,
  not in the repo, driving `run_task` and `generate` on sqlite backup copies
  of the library, on the implementation before its commit (`a28295f` and
  `4680b2e` plus uncommitted changes), so before the review fixes. Media 1
  and 12 were cleaned and published on `openrouter/auto` (deepseek via
  Wafer, hint sent, 0 reasoning) and on qwen3.5:4b; gpt-5-mini's cut-off
  part was refused before storage (case 2). A rerun on the cloud after the
  local run was refused because the gate read the local model's newer parts,
  which is why the gate now reads the parts the final row names.
* `scripts/task029_acceptance.py` (AC2) has run with `--fake` only, before
  and after the review fixes.

## Decision Contract

### Must

* Each provider class declares the hint's wire form in `reasoning_off_body`;
  a request without the hint carries none of `reasoning`, `reasoning_effort`
  or `think`.
* A 400 while the hint is on the wire is retried once without it, unless it
  is a temperature refusal or a context-length refusal; the decision reads
  what was sent, never the vendor's wording.
* `ChatResponse.hint_sent` records what the answering call carried: None not
  asked, True sent, False refused and dropped. Unreported counts are None,
  never 0.
* Every `llm_output` row a call produces records `hint_sent`,
  `reasoning_tokens`, `reasoning_chars`, `upstream`, `hint_ignored` and
  `cap_not_enforced` in `params_json`, judged against the cap that call was
  asked with.
* A concat kind's answer that ended 'length' is refused before it is stored,
  whether it is one call or one of many, and the refusal names what the row
  would have held: finish_reason, completion and reasoning tokens, reasoning
  characters, upstream, and the hint in words.
* A stored part is reused only when its `segment_ids` are the chunk's.
* The cleaning gate judges the parts the final row names
  (`chunk_output_ids`), in that order - never the newest row per chunk index.
* A cleaning whose every part came back as it went in, whitespace aside, is
  stored but not published.

### Must Not

* Branch on the provider's name in a caller to decide the hint.
* Use `effort: low` or `minimal` for a kind that must keep `[m:ss]` stamps and
  speaker labels.
* Count OpenRouter's `message.reasoning` trace as reasoning spent: luna spent
  107 reasoning tokens with no trace.
* Merge a provider's `reasoning_off_body` into a body by reference.

### Exceptions

* `speakers` and `labels` stay at the provider default until their own A/B.
* Ollama's hint is never dropped: its daemon refuses only a truthy `think`.

### Verification

* `tests/test_llm_providers.py::test_each_cloud_provider_says_no_reasoning_its_own_way`,
  `::test_a_refused_hint_is_retried_once_without_it`,
  `::test_a_context_length_400_with_the_hint_on_is_context_too_long_after_one_call`,
  `::test_a_temperature_refusal_then_a_hint_refusal_is_three_calls`,
  `::test_a_hint_refusal_that_mentions_temperature_drops_only_the_hint`,
  `::test_the_hint_mapping_is_copied_into_the_body_never_shared`,
  `::test_the_answer_carries_reasoning_tokens_and_upstream`.
* `tests/test_llm_ollama.py::test_the_hint_is_think_false_and_its_absence_sends_no_think`.
* `tests/test_llm_tasks.py::test_the_hint_follows_the_kind`,
  `::test_every_row_records_what_was_sent_and_what_reasoning_cost`.
* `tests/test_llm_task_providers.py::test_cleanup_reaches_each_real_provider_with_its_own_no_reasoning_field`.
* `tests/test_llm_speakers.py::test_a_one_call_cleanup_cut_off_at_the_cap_is_refused_and_nothing_is_stored`,
  `::test_a_cut_off_part_names_what_it_spent_and_whether_the_hint_went`,
  `::test_a_stored_part_cut_on_other_boundaries_is_asked_again`,
  `::test_a_cleanup_chunk_is_cut_to_the_concat_limit_not_the_answer_cap`.
* `tests/test_llm_cleaning_gate.py::test_a_rerun_publishes_its_own_parts_not_another_models_newer_ones`,
  `::test_a_cleaning_that_came_back_as_it_went_in_is_refused`,
  `::test_a_cleaning_with_some_parts_unchanged_is_still_published`,
  `::test_a_part_with_its_punctuation_fixed_is_a_change`,
  `::test_a_verbatim_copy_is_stored_but_not_published`.
* `tests/test_llm_headroom_script.py::test_the_headroom_script_plans_the_chunk_size_it_is_given`
  (step 0 measures the chunk size it is asked for),
  `::test_the_pin_check_reads_no_verdict_from_a_blocked_or_uncounted_answer`,
  `::test_a_call_that_got_no_answer_prints_what_was_asked_not_a_guess`.

## Consequences

### Positive

* Cleanup on the models measured costs about a twentieth of the tokens
  (1,171 against 23,918 on media 1) and an eighth to a tenth of the time
  (8.5 s against 63-84 s).
* A dropped hint and an ignored hint are told apart on the row, and case 3 -
  an endpoint that does not enforce the cap - is named per row instead of
  being found by hand.
* A cut-off cleaning can no longer reach the gate or the reading (row 15:
  12,000 of 12,000 tokens, stored; gpt-5-mini on 2026-09-11: refused, nothing
  stored).
* A refused cleanup part's error, and an OpenAI-style provider's no-content
  error (OpenAI, OpenRouter), name what the call spent (completion and
  reasoning tokens, upstream, hint state), so the jobs board carries the
  receipt; a refused note (the notes kinds) names the same spend. Ollama's
  no-content error names the budget and the thinking characters only when it
  ended on 'length'; any other empty answer names the model and done_reason.
  Neither names the hint.

### Negative

* Case 3 has no app-side bound on spend. Mitigation: never seen with the
  hint; the row flags make it visible when it happens.
* The step-0 measurement counts only upstreams that pass the enforcement
  check, so it says nothing about case 3. Mitigation: stated here and in the
  script's output ('cap not enforced - not counted'). In step 0 only
  upstreams that enforce the cap served (Azure, Google); the AC2 check the
  same day was served by Wafer (deepseek), whose enforcement was not probed.
* A model whose reasoning is mandatory and heavy may not clean at this cap:
  gpt-5-mini could not clean media 12's largest chunk (2,993 tokens) in the
  one call tried - 5,632 of 6,000 tokens reasoning, 'length'. Smaller
  chunks and repeats were not tried. A cleanup run fails at the first chunk
  that ends 'length'. Raising the cap was rejected anyway (option C).
* A 400 unrelated to the hint is read as a hint refusal. When the call
  without the hint fails too, that costs one more unbilled 400. When it
  succeeds, the answer was bought at the model's default reasoning effort -
  the spend the hint exists to avoid, 22,515 and 21,512 reasoning tokens on
  media 1 - and under `openrouter/auto`, which routes each request afresh,
  possibly from another model than the one that refused. The row says
  `hint_sent=false`; that is the price of not reading vendor wording.
* Nothing bounds how long a call takes. `DEFAULT_TIMEOUT` (60 s) applies to
  each network operation - the connect, each write, each socket read - not
  to the call: the gpt-5-mini call above took 126.3 s on a client built with
  timeout=60, and answered. A heavy reasoner on an enforcing endpoint costs
  up to the cap per chunk before it is refused, and 126.3 s in the one case
  timed.
* More parts make the per-part 0.55/1.15 cleaning gate judge shorter spans:
  media 12 goes from 4 parts to 7. Mitigation: the acceptance script prints
  the per-part ratios next to the overall one.
* The copy rule catches only a reading that is a copy throughout, and only
  a copy that kept every line's stamp and label, so it does not catch the
  one copy measured: qwen3.5:4b's part 0 of media 12, 1 of 11 parts,
  published inside a reading at 0.894 and 0.902 (A cleaning that is a
  copy). A near-copy passes too: its part 3 came back 886 -> 883 words with
  70 of 70 stamps. So does a copy regrouped the way `cleanup.md` asks. And
  the rule refuses an honest answer when every line starts its own stretch
  and the text needs no fixing. Whether a copied part should refuse the
  reading is open (Open Questions).
* Three extra calls on media 12 when the hint is honoured, about +1,180
  estimated prompt tokens (about 6%).
* `openrouter/auto` has served only deepseek-v4-flash-0731 and gpt-5.6-luna
  for cleanup on this account; the evidence is about those two models, not
  the router. Step 0 and the gpt-5-mini case named their models.

## Pros and Cons of the Options

### A. Per-kind hint, mapped per provider

* Good, because it is measured to work on every model auto has served for
  cleanup, and a hint refusal costs one unbilled 400.
* Good, because it keeps the provider seam: one field on the request.
* Bad, because a model that ignores it on a non-enforcing endpoint is bounded
  by nothing the app controls.

### B. Effort ladder

* Good, because a mandatory reasoner would reason less than at its default.
* Bad, because `effort: low` dropped the stamps and speaker labels on this
  very kind, and it adds a third retry state for a case never seen under auto.
  That first reason rests on one media-1 sample, while with the hint deepseek
  kept between 10 of 81 and 96 of 99 stamps per part on media 12, so it is
  weak evidence (Open Questions).

### C. Raise the cloud cleanup cap (rejected 2026-09-11)

* Good, because a heavy reasoner on an enforcing endpoint would succeed where
  today it fails loudly at no more than 6,000: gpt-5-mini needed more than
  6,000 for a 2,993-token chunk in the one call tried (5,632 reasoning,
  'length'); how much more is not measured (about 25k per chunk if media 1
  is a guide - inferred).
* Bad, because a runaway can spend up to the new cap (row 15 spent all
  12,000 of its cap).
* Bad, because it helps only models that ignore or refuse the hint: luna,
  hinted, left at least 3,415 of 6,000 per chunk at 3,000-token chunks in
  step 0 (1,147 at 6,000).
* Bad, because it needs separate local and cloud caps (`budget_for` raises on
  the 8,192 local window), and it does nothing for models whose reasoning
  budget is a share of the cap.

### D. `provider.require_parameters: true`

* Good, because one call showed auto accepts it (deepseek via Wafer, 0
  reasoning).
* Bad, because 127 of the 130 non-reasoning models do not list `reasoning`
  and would leave the pool, and it cannot exclude non-enforcing upstreams.

### E. Do nothing

* Good, because TASK-026's gate already refuses cut-off parts in chunked runs.
* Bad, because cleanup keeps failing on real recordings, a one-call cleanup
  went round the gate (row 15), and the spend stays invisible.

## Open Questions

- [x] Step 0 (`scripts/task029_headroom.py --model openai/gpt-5.6-luna` and `--model google/gemini-3.5-flash-lite`, 3 repeats, no hint): does every counted chunk end 'stop' with content and spend at most half its reserve? If luna fails, `CONCAT_CHUNK_TOKENS` is removed and this record says chunk size buys no headroom. It spends money and waits for Robert's go-ahead. — **Answered 2026-09-11 by the step-0 run (Claude Opus 5, agent; $0.357):** yes for both models at 3,000, so `CONCAT_CHUNK_TOKENS` stays at 3,000 (Chunks).
  Luna was run with and without the hint, because the hint zeroes its
  reasoning and so cannot test reasoning room; gemini refuses the hint, so
  its answers were un-hinted either way. The deciding reason is margin: at
  6,000 luna finished too, with about a third of the room left.
- [x] TASK-029 AC3 as written cannot hold (locally `num_predict` lives inside `num_ctx` 8,192; on cloud deepseek at its default effort spent 22,515 and 21,512 reasoning tokens on a 1,557-token chunk). Proposed wording, for Robert: "A model that ignores the hint either finishes each chunk inside its cap with measured headroom, or fails before anything is stored. On an endpoint that does not enforce the cap only the hint bounds reasoning, and each row records which happened." — **Answered 2026-09-11 by Claude Opus 5 (agent), under Robert's instruction "doe alles" as relayed to this session; Robert confirms it by accepting this record, or reverses it:** adopted as proposed, as one sentence: "A model that ignores the hint either finishes each chunk inside its cap with measured headroom, or fails before anything is stored; on an endpoint that does not enforce the cap only the hint bounds reasoning, and each row records which happened."
  Both branches are measured. Finishing with headroom: luna on Azure without
  the hint, at least 3,010 of 6,000 left per chunk at 3,000-token chunks
  (step 0 R1; 997 at 6,000, R5). Failing before
  storage: openai/gpt-5-mini through OpenRouter, hint refused ("Reasoning is
  mandatory"), 5,632 of 6,000 tokens reasoning on a 2,993-token chunk of
  media 12 in one call, finish 'length' on Azure, part refused and nothing
  stored. The AC3 text in the backlog task is not changed by this record.
- [x] Should the cloud cleanup cap be raised (option C) so heavy reasoners are covered? — **Answered 2026-09-11 by Claude Opus 5 (agent), under the same relayed instruction:** no, the cap stays at 6,000.
  A runaway could then spend up to the new cap (row 15 spent all 12,000 of
  its cap), and a higher cap helps only the models that ignore or refuse the
  hint. The cost of that choice is a Negative consequence above: gpt-5-mini
  could not clean media 12's largest chunk in the one call tried, and a
  heavy mandatory reasoner may not clean at all.
- [ ] Is `effort: low` worse for `[m:ss]` stamps and speaker labels than `enabled: false`, or was the single media-1 comparison per-part variance?
  The Must Not and the Enforcement pattern rest on that one sample, while in
  the 2026-09-11 live check deepseek with `enabled: false` kept between 10 of
  81 and 96 of 99 stamps per part of media 12. Settling it needs both
  settings over several parts; until then the ban stands as the cautious
  default.
- [x] `scripts/task029_headroom.py`'s pin check reads any answer of at most 64 completion tokens as enforced, including Google's RECITATION-blocked one (finish 'error', usage all 0), and its `PIN_PROMPT` ("count from one to two hundred") trips that filter. — **Answered 2026-09-11 by Claude Opus 5 (agent):** the misread is fixed; the prompt is not changed.
  An answer that ended 'error' or counted 0 completion tokens is now
  'unknown', never a verdict:
  `test_the_pin_check_reads_no_verdict_from_a_blocked_or_uncounted_answer`
  failed on the old check for the recitation, error-finish and zero-usage
  shapes (enforced=True each time) and passes now, and its two controls (60
  tokens with 'length', 612 with 'stop') keep True and False. A replacement
  prompt would need a paid live call to show it gets past Google's filter,
  so on Google the check now says 'unknown', and Google's enforcement still
  rests on the manual probe and R6's 'length' (Chunks). R4's PASS came
  through the misread: rerun with the fixed script, a gemini run on Google
  ends NOT MEASURED, because no chunk from an 'unknown' upstream is counted
  (shown with `--fake` on a canned transport shaped like Google, not live).
  A short answer that
  stopped early on its own would still read as enforced; the script's
  docstring names that gap. The same review found the script printing a
  hint state for calls that got no answer - `hint_sent=False`, 'refused and
  dropped', on R1's three rate-limited calls, which asked for no hint. It
  now prints what was asked and, only when an answer came, what that answer
  carried (`test_a_call_that_got_no_answer_prints_what_was_asked_not_a_guess`).
- [ ] Should a part that came back as it went in refuse the reading when its source has a line that continues the speaker before it?
  `cleanup.md` keeps only the stamp that starts each stretch, so such a
  part skipped the grouping it was asked for; a one-line part, or one of
  alternating speakers, could honestly come back unchanged and would still
  pass. If yes, qwen3.5:4b's media 12 reading is refused, and a rerun on the
  same provider, model and prompt version reuses the stored parts, copy
  included (`stored_chunk` does not ask whether a reading was published),
  and is refused again. The per-part ratio gates already refuse a whole
  reading for one bad part, but they guard lost words and a copy loses
  none. A near-copy passes either way.
  Raised by the review of 2026-09-11; start from a failing test built on
  the 1-of-11 shape (`test_a_cleaning_with_some_parts_unchanged_is_still_published`
  pins today's answer).

## Related Decisions

* ADR-002 / ADR-009 (SQLite in WAL (write-ahead log) mode is the only
  coordination): the
  reasoning numbers go into `llm_output.params_json` under `db.LOCK`, with no
  new table or column.
* ADR-007 (the application log is observation): the per-row flags are data a
  person reads; nothing decides anything from them.

## References

* TASK-029 (backlog) and the design it was built from.
* `scribe/llm/base.py:57` (`ChatRequest.reasoning_off`), `:92`
  (`ChatResponse.hint_sent`), `:98` (`hint_words`), `:314`
  (`Provider.reasoning_off_body`).
* `scribe/llm/openai_like.py:79` (`DEFAULT_TIMEOUT`), `:237` (`complete`,
  the drop loop), `:267` (`_refused_knob`), `:417` (`_refuses_temperature`),
  `:437` (`_refuses_context`), `:463` and `:495` (the two wire forms).
* `scribe/llm/ollama.py:177` (`think: false`), `:385` (thinking characters).
* `scribe/llm/tasks.py:316` (`TaskSpec.reasoning_off`), `:436` (cleanup on),
  `:527` (`CONCAT_CHUNK_TOKENS`, with the step-0 numbers), `:1029` (the chunk
  limit), `:1250` (`stored_chunk`), `:1309` (`reasoning_record`), `:1728`
  (`_refuse_a_cut_off_part`), `:2091` (`check_cleaning`), `:2196`
  (`cleaned_parts`).
* `scripts/task029_headroom.py` (`enforcement_check`, `hint_state`),
  `scripts/task029_acceptance.py`.

## Enforcement

```json
{
  "forbid_pattern": [
    {"pattern": "[\"']effort[\"']\\s*:\\s*[\"'](?:low|minimal)[\"']", "path_glob": "scribe/**", "message": "effort low/minimal dropped the [m:ss] stamps and speaker labels on cleanup (ADR-010)."},
    {"pattern": "reasoning_effort\\s*=\\s*[\"'](?:low|minimal)[\"']", "path_glob": "scribe/**", "message": "effort low/minimal dropped the [m:ss] stamps and speaker labels on cleanup (ADR-010)."}
  ],
  "forbid_import": [],
  "require_pattern": []
}
```
