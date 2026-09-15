---
id: TASK-054
title: >-
  Settings: pick the model per provider from its fetched list without ever
  saving a model nobody chose
status: Done
assignee:
  - '@claude'
created_date: '2026-09-15 17:13'
updated_date: '2026-09-15 20:44'
labels:
  - settings
  - llm
dependencies: []
ordinal: 99000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert asked for a model dropdown in Settings > AI providers, filled from the lists OpenRouter, OpenAI and Ollama return. Today each provider has a free-text field with a datalist (_settings_llm.html). A naive <select> is a trap measured on 2026-09-14: the stored llm_model_openai is gpt-5.6, which is not among the 134 ids OpenAI returned, so a select would show its first option (babbage-002) and the next Save - the form saves provider, all three models and the privacy default together - would silently make that the OpenAI model.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Each provider offers its fetched models as a list to choose from
- [x] #2 A stored model that is not in the fetched list stays selected, is shown as such, and survives a Save of any other field unchanged
- [x] #3 A provider with no fetched list still accepts a typed model name
- [x] #4 Saving never changes a provider model the user did not touch
- [x] #5 Red then green in tests, and seen in a browser against a copy of the real settings
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
Design A (workflow 2026-09-14/15, re-checked against code and a read-only snapshot; recommendations taken):
1. ai_ui: CUSTOM_MODEL_FIELD_PREFIX = 'custom_model_'; provider_rows gives model_saved (the stored row or ''), model_default (the class default) and model_groups; model_groups(ids) groups by the vendor before '/' only when every id has one (OpenRouter), else None (flat).
2. _settings_llm.html: per provider a select name=model_<p> - a blank 'Its own default · <default>' option, the stored id pinned as selected (labelled 'not in the fetched list' / 'no list fetched yet' when the list lacks it), then the ids flat or in optgroups - plus an always-visible text box custom_model_<p> (value empty, datalist kept for substring search).
3. settings.save_llm_defaults: a typed id wins, otherwise the select's value; blank drops the row (unchanged). Consequence reported with before/after: an untouched provider no longer gets today's default frozen into a row by Save.
4. app.js: picking from the select clears the typed box (delegated, optional; without scripts the typed id still wins, which the page says).
5. Non-chat ids stay in the list (the app cannot tell which ids chat; gpt-5.6 answered while unlisted). Rail and chat keep their input: not this task.
6. Red first (test_web_ai / test_web_settings incl. a DOM test for the listener); then on a copy of the real settings in a browser: Save with only the private box ticked leaves every llm_model_ row unchanged, gpt-5.6 stays selected.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
2026-09-15 implementation and evidence.
- ai_ui: CUSTOM_MODEL_FIELD_PREFIX; model_groups(ids) (vendor groups only when every id has a '/', else flat); provider_rows gives model_saved (stored row or ''), model_default, model_groups.
- _settings_llm.html: per provider a select model_<p> (blank 'Its own default · <default>', the stored id pinned as selected and labelled 'not in the fetched list' or 'no list fetched yet', then the ids flat or in optgroups) plus a text box custom_model_<p> with the datalist.
- settings.save_llm_defaults: a typed id wins, else the select; blank drops the row.
- app.js: an input event on [data-model-select] clears the typed box in the same [data-model-pick]. 'input' rather than 'change' in the DOM test: the node stub cannot dispatch change (app.js's dropzone listener asks a descendant selector the stub refuses); a first red run with change was a harness crash, not a red, and is not counted.
Red before the code: 8 in test_web_ai (no select model_*, untouched Save wrote rows for all three providers, typed id lost, model_groups missing, no optgroups), DOM: typed stayed 'gpt-5.7'. Guards green before and after: blank option drops the row; leaving the typed box keeps it.
Green: test_web_ai 106, test_web_settings 37, test_web_scaffold 27, test_web_url_dialog 90.
Browser (headless Chrome over CDP, app on 4299 over a copy of today's live settings: llm_model_openai gpt-5.6, 134 OpenAI ids without it, 430 OpenRouter ids, 8 Ollama ids): pickers show gpt-5.6 · not in the fetched list (136 options), qwen3.5:4b (9), openrouter/auto (431 options, 58 optgroups). Picking in Chrome cleared a typed id. Ticked only 'New recordings start private' and pressed Save: flash 'AI defaults saved.'; rows before {ollama qwen3.5:4b, openai gpt-5.6, openrouter openrouter/auto, private_default 0}, after the same with private_default 1. Screenshot scratch task054_picker.png. Output task054_copy.txt.

Layout: .llm .model-pick puts the select and the typed box side by side where there is room (app.css); test_web_scaffold 27 green after it. Screenshot after the CSS: scratch task054_picker_css.png.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Settings > AI providers now offers each provider's fetched models as a dropdown (OpenRouter's 430 ids grouped by vendor), with a box beside it for an id the list lacks; a typed id wins and picking from the list clears the box. The stored model is always the selected option, so an unlisted one (gpt-5.6) is shown as 'not in the fetched list' and survives any Save, and a provider nobody set no longer gets its default frozen into a row. Verified: red then green (test_web_ai 106, test_web_settings 37 incl. two DOM tests), and in headless Chrome on a copy of today's settings: ticking only 'New recordings start private' and pressing Save left all three model rows unchanged.
<!-- SECTION:FINAL_SUMMARY:END -->
