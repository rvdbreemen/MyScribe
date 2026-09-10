---
id: TASK-024
title: >-
  Speaker names are applied automatically after a successful diarization,
  instead of waiting for a tick
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-10 04:41'
updated_date: '2026-09-10 15:32'
labels: []
dependencies: []
type: enhancement
ordinal: 65000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
TASK-014 shipped the 'speakers' LLM kind: it reads a diarized transcript, maps SPEAKER_XX to names with evidence and confidence, and prefills a tick-to-apply form. Its AC3 was explicit that nothing is written to speaker_label until a person accepts. Robert asked on 2026-09-10 to reverse that: after transcribe and diarize succeed, the speaker analysis should run by itself, and on success the names should be applied. This task is that reversal, and it should be read as a deliberate change of a shipped decision rather than a missing feature. Measured on 2026-09-10, this is why it matters: 60 runs in the database carry diarized speaker clusters with stored embeddings, and speaker_label holds 3 names in total - every one of the 50 Hacker History episodes reads 'Speaker 1' and 'Speaker 2', while each is an interviewer and a guest who introduce themselves in the first minute. WHYcast's ADR-004 (D:/Users/Robert/Documents/GitHub/RvdB/WHYcast-transcribe/docs/adr/) is the design of record for the two-phase shape and its Must Not stands here too: the model produces a mapping, code applies it, and no prompt is allowed to rewrite transcript text. MyScribe is structurally safer than WHYcast on that point - the transcript is words in a table (ADR-003) and applying a mapping is an INSERT into speaker_label, not string replacement over prose, so WHYcast's content-loss check has no counterpart to port. Two adaptations agreed with Robert: a new run inherits the previous run's names for the same media when the transcript has not meaningfully changed, so a re-transcription does not pay a reasoning model to rediscover names that were already right (WHYcast solves this with a transcript fingerprint in a _speakers.json that the pipeline reads as an input); and a cluster the model cannot place keeps its 'Speaker N' rather than receiving a guess. Hooks: scribe/stages/__init__.py holds TRANSCRIBE_STAGES (probe, prepare, transcribe, diarize, attribute, correct, finalize); scribe/stages/diarize.py:685 writes speaker_embedding; scribe/web/transcript.py:371 is today's only writer of speaker_label; the 'speakers' kind lives in scribe/llm/tasks.py with its web side in scribe/web/ai_ui.py.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A successful diarization enqueues the 'speakers' analysis by itself, with no click, and a run without diarization enqueues nothing
- [ ] #2 A name a person typed is never overwritten by a later automatic run: speaker_label records who chose the name
- [ ] #3 A re-transcription of the same media inherits the previous run's names when the transcript has not meaningfully changed, and re-analyses when it has
- [ ] #4 A failed or malformed analysis leaves the recording with its default speaker names and is visible on the jobs board, never silent
- [ ] #5 The existing tick-to-apply form from TASK-014 still works as the correction path
- [ ] #6 The privacy pin still holds: a private recording reaches no cloud provider
- [ ] #7 Tests cover the automatic enqueue, the do-not-overwrite-a-human rule, the inheritance across runs and the failure path; a real run over a Hacker History episode is in the notes, with the names it produced
- [ ] #8 The 'speakers' analysis reports a numeric confidence per cluster, not the word high/medium/low, because a percentage threshold cannot be read off a three-word scale
- [ ] #9 A name is applied automatically only above SPEAKER_CONFIDENCE_THRESHOLD (90); at or below it the cluster keeps its default 'Speaker N' and the suggestion stays available to accept by hand
- [ ] #10 Every automatically applied name records which analysis decided it: speaker_label carries the llm_output row and the confidence, so 'why does this say Jeff Man' is answerable from the row
- [ ] #11 The analysis is readable back from the transcript view for any run that has one, including its evidence quotes and timestamps, and remains readable after the names are applied
- [ ] #12 A re-run stores a new llm_output row and never overwrites the previous one, so the record of what an earlier model decided survives
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Schema v12: speaker_label gains source TEXT CHECK('llm','human') DEFAULT 'human', llm_output_id INTEGER REFERENCES llm_output(id) ON DELETE SET NULL, and confidence REAL. Existing rows read as 'human' because every name in the table today was typed by one. SET NULL rather than CASCADE: losing the analysis must not silently unname a speaker, it must only lose the receipt.

2. The speakers schema gains a number. SpeakerGuess.confidence becomes numeric 0-100 with the word kept as a separate field for display, and prompts/speakers.md asks for both. PROMPT_VERSION is bumped this time - unlike adding labels.md, this EDITS a template that already has stored answers, and those answers were given to a different question.

3. The apply mechanism, which is the part Robert calls 'the coordinator'. TaskSpec gains an optional apply hook; llm_stage grows a fourth stage after task_store that runs it when the spec has one. Inside the same job, so a failure to apply is reported where the analysis is, and a stored-but-unapplied answer stops being a reachable state.

4. apply_speakers: writes speaker_label for every cluster above SPEAKER_CONFIDENCE_THRESHOLD (90), recording llm_output_id and the confidence; leaves a cluster at or below it with its default; never overwrites a row whose source is 'human'.

5. The chain: a transcribe job whose diarize stage produced clusters enqueues the speakers pass itself. No diarization means no clusters means nothing to enqueue.

6. Inheritance across runs: a new run over the same media adopts the previous run's names when the transcript has not meaningfully changed, so a re-transcription does not pay a model to rediscover names that were already right.

7. Reading it back: the transcript view shows which analysis named a speaker and with what confidence, and the analysis stays readable after the names are applied.

8. Evidence: tests per slice, then a real run over a Hacker History episode with the names it produced and the confidences it claimed - and an honest note that a model's self-reported confidence is not calibrated, so the threshold is a policy dial and the evidence quote is the real check.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
CLARIFIED BY ROBERT 2026-09-10, and it changes the shape of the task.

Wanted: after register -> transcribe -> diarize, speaker identification ALWAYS runs, and names are applied automatically when confidence on a speaker is above 90%. Not a manual step when it can be automatic. And the analysis must be readable back and stored as an artifact, so accountability is always possible.

Three consequences worth writing down before anyone implements this.

1. THE SCALE DOES NOT FIT THE THRESHOLD. The shipped 'speakers' kind returns confidence as a word - SpeakerGuess.confidence is 'high | medium | low' (scribe/llm/tasks.py, prompts/speakers.md). A 90% gate cannot be read off that, so the schema and the prompt have to ask for a number. Keep the evidence quote and its [m:ss]: it is what makes the decision checkable.

2. A NUMBER FROM A MODEL IS NOT A CALIBRATED PROBABILITY. An LLM answering '95' is not right 95 times in a hundred; it is answering a question about its own certainty, which nothing trained it to answer well. The threshold is therefore a policy dial, not a measurement, and its real safety net is that every applied name stays traceable to the quote it rests on. That is why requirement 3 matters more than requirement 2.

3. THE ARTIFACT STORE ALREADY EXISTS; THE LINK BACK DOES NOT. llm_output already holds content, provider, model, prompt_version, run_id, both token counts and created_at, and tasks.py's rule is one key and never an overwrite - so a re-run adds a row and the earlier decision survives. Three speakers analyses are already stored. What is missing is provenance on the other end: speaker_label is (run_id, cluster_label, display_name, color), so an automatically applied name cannot say which analysis decided it or with what confidence. 'Why does this say Jeff Man' has to be answerable from the row, which means speaker_label needs the llm_output id and the confidence alongside the source column already planned.

THE MISSING LINK, named by Robert 2026-09-10 as 'the coordinator'. Verified: no component of that name exists here - the word appears in this repo only as ADR-002's 'SQLite is the only coordination'. What he is pointing at is real all the same, and it is one gap rather than two.

scribe/stages/llm_stage.py task_store() is the last stage of an llm job. It calls tasks.store_output(), puts the id in ctx.state, emits a job event, reports 1.0 - and ends. NOTHING applies an answer. That is true of 'speakers' (the mapping never reaches speaker_label) and equally of 'cleanup' (the rewritten text never reaches anything). Applying is, in both cases, a POST a person clicks.

So the mechanism this task needs is not speakers-specific: a kind should be able to declare an apply step that the runner performs after the answer is stored, inside the same job, with the same failure reporting. Build it once here, and TASK-026 uses it for cleanup.

Ordering that follows from it: register -> transcribe -> diarize -> (speakers analysis + apply) is one chain, and the apply belongs in the llm job that produced the answer rather than in a fourth job, because a stored answer nobody applied is exactly the state this is meant to end.

Slices 1-5 green: 430 passed over test_llm_*, test_stage_finalize, test_db and test_web_ai.

v12 puts source, llm_output_id and confidence on speaker_label. Existing rows read 'human' because that is what every one of them is. ON DELETE SET NULL on the analysis link: losing the receipt must never unname a speaker who was correctly identified.

Confidence became a number. _confidence() takes 96, 0.96, '87%' and the old words; every word maps BELOW the threshold on purpose, because a model that answered 'high' where a number was asked for has not given the evidence an automatic write needs - its guess is still shown and can still be accepted by hand. Anything unreadable is 0, never a middling default that could clear a bar. PROMPT_VERSION bumped to 2, because this EDITS a template with stored answers, unlike adding labels.md.

The apply mechanism - Robert's 'coordinator'. TaskSpec.apply, a fourth llm stage, and the same call added to run_task. That last one mattered more than it looked: run_task has no production callers and exists only so a direct caller and the job cannot drift, and a version stopping at store would have made every test using it a test of three quarters of the pipeline - the missing quarter being the one that changes the library. Its own docstring already said so.

The hook takes the PLAN, not a media id: speaker_label hangs off a run, and a re-transcription landing while an analysis is in flight must not have its clusters named from an answer about the previous run.

apply_speakers: above the threshold only (exclusive - 90 does not clear 90), never over a row whose source is 'human', and a role without a name is skipped because 'Guest' replaces 'Speaker 2' with something no more informative and harder to spot as a default.

finalize.queue_speaker_pass closes the chain. In finalize rather than diarize because attribute runs in between, so by then the words carry their cluster and the transcript the pass reads is finished. No clusters, no question. And a private recording is not sent to an external provider by a pipeline step - Robert's bulk rule, binding harder here, because a pipeline step is the least conscious act there is.

Five of this project's tripwires fired across the four commits and every one was right to: the sample-answer guard, the canonical kind list, the prompt digest, and the three-stage assertions in test_llm_tasks and test_llm_chat.

Incidental: scribe.web.ai_ui's default_provider/default_model moved to scribe.llm so a runner stage can ask the same question without importing the web layer; ai_ui re-exports them so there is still one spelling.

Slices 6 and 7 green: 477 passed over test_llm_*, test_stage_finalize, test_web_transcript, test_web_ai and test_db.

Inheritance keys on the CLUSTER SET, not on a transcript fingerprint. WHYcast compares text because its names live in a file beside the transcript; here they hang off cluster labels, so if diarization came back with a different set the old mapping is not stale, it is meaningless - copying it would put a real person's name on somebody else's voice. Same labels, inherit everything including who chose each name; anything else, inherit nothing and let the pass ask. A run whose clusters are all named already queues no pass at all, which is the saving the criterion was written for.

A DEFECT FOUND WHILE WIRING THE READBACK, not by a test asking for it. transcript.py's _upsert_label wrote display_name and colour and left  alone. On the insert path that is harmless - the column defaults to 'human'. On the UPDATE path it was the whole rule failing quietly: rename a speaker the model had named and the row still said 'llm', because that is who wrote it first, so the next automatic pass was free to overwrite the name a person had just typed. Fixed to claim ownership on both paths and to clear llm_output_id and confidence, because 'which analysis chose this' has no honest answer once somebody has typed over it. Two tests pin it.

Readback: run_speakers now carries source, analysis_id and confidence per cluster, so the panel can say where a name came from rather than the answer stopping at the table.

REAL RUN 2026-09-10 against openrouter/auto, five Hacker History episodes. This is the run that answers the question the unit tests cannot: does a model's self-reported confidence discriminate, or does it say 99 for everything?

Eleven guesses over five episodes. Ten cleared 90 and were applied; one was held.

  media 10 Michael Lenz     100.0 Josh Bressers (host)   100.0 Michael Lenz (guest)
  media 11 Jeff Man          99.0 Josh Bressers           97.0 Jeff Man
  media 12 Bill Bernard      99.0 Josh Bressers           99.0 Bill Bernard
  media 13 Rebecca Jones     80.0 Josh Bressers HELD      98.0 Josh Bressers   97.0 Rebecca Jones
  media 14 Pyr0             100.0 Luke McCormie          100.0 Josh Bressers

MEDIA 13 IS THE EVIDENCE THAT MATTERS. Diarization split one voice across SPEAKER_00 and SPEAKER_01 and the model recognised both as the host - but rated the weaker one 80 and the stronger 98. The threshold held the weak one back and let the strong one through. The gate does work on real data; it is not a rubber stamp. Every applied name also carried the quote it rested on ('My name is Josh Bressers, your guide through this tale of hacker history.' at [0:07]).

A DEFECT THE RUN FOUND, fixed and re-verified. Two of the five episodes failed outright: 'openrouter answered with no message content (finish_reason=length)'. The speakers kind used DEFAULT_MAX_OUTPUT_TOKENS (4000) and the answer is tiny - two or three names - but the THINKING in front of it varies by more than a factor of ten depending on which model auto picks: 347 completion tokens on media 12, 3779 on media 10 with the same two clusters. Media 10 was a near miss at 3779/4000; media 13 (three clusters) and media 14 hit the wall. Raised to 8000 with the measurement cited at the constant, and both previously failing episodes then succeeded at 3849 and 3391 completion tokens. It is a cap, not a spend - a cloud provider bills what was generated - so the room costs a terse model nothing while the failure it prevents costs the whole call.

Also confirmed on real data: apply wrote source='llm', the confidence and the llm_output_id on every row, so each name points back at the analysis that chose it.
<!-- SECTION:NOTES:END -->
