
<!-- BACKLOG.MD GUIDELINES START -->
<!-- backlog.md-instructions-version: 1.50.1 -->
<CRITICAL_INSTRUCTION>

## Backlog.md Workflow

This project uses Backlog.md for task and project management.

**For every user request in this project, run `backlog instructions overview` before answering or taking action.**

Use the overview to decide whether to search, read, create, or update Backlog tasks.

Before task lifecycle actions, read the matching detailed guide:
- `backlog instructions task-creation` before creating or splitting tasks
- `backlog instructions task-execution` before planning, changing status or assignee, adding a plan or implementation notes, or implementing task work
- `backlog instructions task-finalization` before checking acceptance criteria, writing final summaries, or moving tasks to terminal statuses

Use `backlog <command> --help` before running unfamiliar commands. Help shows options, fields, and examples.

Do not edit Backlog task, draft, document, decision, or milestone markdown files directly. Use the `backlog` CLI so metadata, relationships, and history stay consistent.

</CRITICAL_INSTRUCTION>
<!-- BACKLOG.MD GUIDELINES END -->

<!-- ADR-KIT CLAUDE START -->
## ADR Kit

Read `.adr-kit/ADR-guide.md` before architectural changes. Architecture decisions live in `docs/adr/`. Use `/adr-kit:context` before implementation, `/adr-kit:adr` for new decisions, and `/adr-kit:judge` before commit.
<!-- ADR-KIT CLAUDE END -->

## Working agreement

**Every feature or fix ships with its evidence.** Not "it works" but the
command, the output, and what it proves:

- A bugfix starts with the failing test and shows the red output, then the green.
- A feature reports the test names and pytest's summary line; anything touching
  the UI or the pipeline also gets a real run (start the app, put a file through,
  a measured number or a screenshot).
- A deliberate behaviour change shows the diff of the expected result - a golden
  file, an example output - not just a green suite. Never update a golden without
  showing the diff and saying why it moved.
- Say plainly what failed or was skipped. Half the evidence is worse than none.

This exists because the bugs that mattered here were found by running, not by
reading: a doctor that poisoned its own ETA data, an SRT export that repeated
the speaker name on every cue. Both passed review.

## Commands

- `.venv/Scripts/python -m pytest -q` — the suite. **Never bare `python`**: that is
  C:\Python312 without the dependencies. On Linux and macOS the venv is
  `.venv/bin/python`; README.md has the per-platform install.
- The whole-suite run stalls intermittently on Windows (CPython's socketpair
  emulation behind TestClient; see pytest.ini). If a run passes ~4 minutes, kill it
  and run halves: `tests/test_[a-r]*.py` then `tests/test_[s-z]*.py`.
- GPU tests are excluded by default (`addopts = -m "not gpu"`); run them with `-m gpu`.
- `.venv/Scripts/python -m scribe.doctor` — does this machine work? Add `--no-gpu` to
  skip the model load. Run it before blaming the code.
- `.venv/Scripts/python -m scribe --port 4299 --no-supervisor --no-browser` — manual
  check without disturbing the app on 4242.
- `SCRIBE_UPDATE_GOLDENS=1 …pytest tests/test_exports_text.py` — regenerate export
  golden files. Read the diff before committing it.
- `uv sync` installs the environment from `uv.lock` on every OS; Windows gets the
  cu128 torch through `[tool.uv.sources]` (ADR-009). Change pins in
  `pyproject.toml`, then `uv lock`, and commit both. There is no pip in the venv.
