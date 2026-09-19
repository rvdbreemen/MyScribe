---
id: TASK-085
title: >-
  An LLM job shows its work in the live log: what went to the model, what came
  back, what it concluded
status: In Progress
assignee:
  - '@claude'
created_date: '2026-09-19 06:25'
updated_date: '2026-09-19 06:25'
labels: []
dependencies: []
references:
  - scribe/llm/tasks.py
  - scribe/stages/llm_stage.py
  - >-
    docs/adr/ADR-014-the-application-log-is-observation-nothing-reads-it-to-decide-anything-with-an-enforcement-rule-that-can-match.md
priority: high
ordinal: 132000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Watching a 'Who is speaking' job today tells you a percentage and nothing else. When it takes five minutes on a local 27B model, or produces a name that looks wrong, there is no way to see what was actually asked or what actually came back - the answer lands in llm_output and everything before it is invisible.

Robert, 2026-09-19: the transcript that is fed to the model, the model's reply, and the conclusion of the analysis should all be output in the live log while the job runs.

The job already has the machinery: jobs.emit writes job_event rows and the job page streams them as named frames (jobs_ui), and ADR-014 is explicit that the log is observation - nothing reads it to decide anything - so this adds watching, not control. tasks._ask is the one place every call passes through: a note, a part, a single answer, a combine.

The size question is the real design constraint. A chunk of transcript is thousands of tokens and a job can make a dozen calls, so writing every prompt in full would put megabytes into job_event rows and down an SSE stream that the page replays on every reload. And a recording can be pinned private, where the point of the pin is that its words stay put.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 While an llm job runs, the live log shows each call: which phase it is, the model, and an excerpt of the prompt that was sent
- [ ] #2 Each call's reply appears in the live log as it arrives, with its token counts and finish reason
- [ ] #3 When the job finishes, the live log states the conclusion in the kind's own terms - for speakers, which cluster became which name
- [ ] #4 What is written is bounded: a long prompt is excerpted, the event says how much was left out, and a dozen calls cannot put megabytes into job_event
- [ ] #5 A recording pinned private is not made less private by being watched
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. tasks._ask takes an on_call hook and invokes it with (phase, request, response) - the one place a note, a part, a single answer and a combine all pass through, so no call can be made that is not seen. generate threads it from the caller and names each phase.
2. Excerpting lives in tasks: EXCERPT_CHARS head and tail of the user prompt with the middle replaced by a count of what was dropped, the system prompt separately, the reply the same way. A dozen calls then cost kilobytes rather than megabytes.
3. llm_stage turns each hook call into a jobs.emit('llm-call', ...) and, after the answer is stored, one jobs.emit('llm-answer', ...) whose payload is the kind's own summary - for speakers the cluster-to-name mapping that apply_labels wrote.
4. Privacy: a pinned recording already refuses a non-local provider, so the words in an excerpt never leave the machine that was going to hold them anyway - but the excerpt is still the recording's text in a second place, so it is bounded and the event says so.
5. Tests: the hook fires once per call with the phase, an over-long prompt is excerpted and says how much was dropped, the conclusion names the mapping, and the payload of a whole job stays under a stated ceiling.
6. Evidence: red first, then green, then a real speakers job watched through /api/jobs/<id>/events.
<!-- SECTION:PLAN:END -->
