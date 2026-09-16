---
id: TASK-067
title: No test pins the selected marker for a model that is in the fetched list
status: Done
assignee:
  - '@claude'
created_date: '2026-09-16 15:21'
updated_date: '2026-09-16 15:22'
labels:
  - review-2026-09-16
  - tests
dependencies: []
priority: medium
type: bug
ordinal: 112000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Found in the whole-codebase review of 2026-09-16. tests/test_web_ai.py covers the model the fetched list has never heard of - rendered as an extra pinned option - but never the ordinary case: a model the user picked out of the list. Dropping the selected marker from the loop in scribe/templates/_settings_llm.html would therefore pass the suite. That matters because a select shows its first option for a value it has no marker for, and the settings form posts every provider's model on every save: the next Save would store babbage-002 for a person who chose gpt-4o-mini. This is a test gap, not a defect - the marker works today, which is why the new test is green from the start and its worth is shown by a mutation instead.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 A test asserts the in-list model carries selected and its neighbour does not
- [x] #2 The same test asserts that what a browser would post back is that model, not the list's first
- [x] #3 Removing the selected marker from the template makes the test fail (mutation shown, template restored)
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Test in tests/test_web_ai.py met option_of en browser_post.
2. Mutatieproef: selected uit de lus halen, test moet vallen, sjabloon terugzetten met git checkout.
3. Draai tests/test_web_ai.py.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Test in tests/test_web_ai.py: een model dat wel in de opgehaalde lijst staat (gpt-4o-mini) moet na opslaan de selected-markering dragen, de buurman niet, en browser_post moet datzelfde model teruggeven in plaats van het eerste uit de lijst.

Deze test was vanaf het begin groen, want de markering werkt; het gat zat in de dekking. De waarde is daarom met een mutatie aangetoond in plaats van met een rode run: de selected-markering uit de lus in scribe/templates/_settings_llm.html gehaald, test viel op '<option value="gpt-4o-mini">gpt-4o-mini</option>' zonder selected, daarna het sjabloon teruggezet met git checkout en opnieuw groen.

Groen na herstel: gerichte test 1 passed, tests/test_web_ai.py 107 passed.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Het gewone geval - een model uit de opgehaalde lijst - is nu vastgepind, inclusief wat de browser bij de volgende Save zou terugsturen. Omdat de markering al werkte is de waarde met een mutatie aangetoond: zonder de selected-markering valt de test, met sjabloon teruggezet slaagt hij, en het bestand blijft op 107 geslaagde tests.
<!-- SECTION:FINAL_SUMMARY:END -->
