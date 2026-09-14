---
id: TASK-053.03
title: 'Delete what nobody asked for, and cap the rail'
status: To Do
assignee: []
created_date: '2026-09-14 21:08'
labels:
  - ui
dependencies: []
parent_task_id: TASK-053
ordinal: 94000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Step 3 of TASK-053. Nine empty 'Not asked yet.' cards render at the end of every recording page for a reader who never touches AI. The rail is position:sticky with top but no max-height and no overflow, so on a recording with many speakers its own bottom can become unreachable. And eleven unrelated actions silently destroy a typed question, the chosen provider and any open details, because the AI panel is re-rendered from settings on every panel refresh. This step also produces the number that decides step 5: the page height on media 20 after the empty cards are gone.
<!-- SECTION:DESCRIPTION:END -->
