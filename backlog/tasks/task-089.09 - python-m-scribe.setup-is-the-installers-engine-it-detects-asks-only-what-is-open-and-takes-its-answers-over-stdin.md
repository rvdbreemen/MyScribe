---
id: TASK-089.09
title: >-
  python -m scribe.setup is the installer's engine: it detects, asks only what
  is open, and takes its answers over stdin
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
labels:
  - packaging
  - llm
  - security
dependencies:
  - TASK-089.02
  - TASK-089.04
  - TASK-089.06
  - TASK-089.08
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 146000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
This closes the confirmed API-key gap and gives requirement 6 its asker. Today `python -m scribe.setup --provider openrouter` exits 0 with 'saved: provider', no credential and no warning: Answers has no key field (scribe/setup.py:46-53), there is no key flag (:160-167), and the first Summary fails as a job on the board. The one secret it does take rides on argv (`--hf-token`, :161; the launcher passes it at packaging/launcher/myscribe_launcher.py:475-476), where it sits in the process list for the length of a 1.6 GB download. Run bare, it prints 'saved: nothing' and stamps setup as done (:142-154), after which the launcher never asks again.

The questions live only in frozen Tk code (myscribe_launcher.py:515-594). A terminal installer has nothing to reuse, and a fifth question can be forgotten in one of two places. apply() also writes the token before it checks the provider (:114-121), so a bad `--provider` leaves a half-applied sitting.

The engine makes the question list data. `--plan` prints what was found and where - never a value - the Ollama state, and only the open questions, each with its default, what skipping costs and where to answer it later. `--apply-stdin` takes the answers as one JSON document. Run bare on a terminal, it asks the same questions itself. The proof that ends an install is TASK-089.13.

Ordering (brief: M9). This task does NOT wait for the models re-pin in TASK-089.16. That one needs pins nobody has collected and a Mac that was not available in the design run; the key question, the found table and the Ollama state need none of it. Until TASK-089.16 lands, `--plan` reports today's catalogue and says what it is.

It does follow TASK-089.08. The sentence under the token question says that a job which still asks for speakers keeps its transcript, and that is only true once TASK-089.08 has landed. Setup does not write default_diarize at all (brief: W6): no sitting does, and the typed `--diarize/--no-diarize` flag, the one other path, is removed in criterion 17. Today even a tier-only answer writes the row, because save_defaults writes all three rows at once (scribe/web/transcribe_dialog.py:97-108).

Verified by nobody: an OpenRouter endpoint that checks a key for free.

Needs a real machine: Robert's machine for the found table, and a real terminal (PowerShell or cmd) for the console asker. An OpenRouter key-check endpoint is verified by nobody.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 `--plan` prints JSON with the contract number (`contract`, the name the design spec and ADR-015 use), found[] (name, found, source, also_in, conflict, and the proxy entry from TASK-089.05), ollama, questions[] and downloads. Each question carries id, kind, text, choices, current, default, shown_if, if_skipped and answer_later. No secret value appears anywhere in it. A main()-level test pins the shape; today no test calls setup.main at all. The wall time on Robert's machine is measured and reported.
- [ ] #2 `--plan` writes nothing. On an empty scratch SCRIBE_DATA_DIR the directory is still empty afterwards, and on a copy of a library no file's size or mtime moves. Today main() creates the directories and migrates the database before it answers `--status` (scribe/setup.py:171-174).
- [ ] #3 A question is present only when it is open. A real run on Robert's machine, output shown, has no credential question and no Ollama question.
- [ ] #4 Until TASK-089.16 lands, `downloads` reports today's catalogue from scribe/models.json and labels it. On a platform whose transcriber is not MLX (accel.transcription_backend()), the mlx-community entry reads 'not loaded on this platform' and is left out of the offer and of the total. One line says this platform's Whisper weights are not pinned yet and still arrive inside the first job. No number is shown as a download size that is not one.
- [ ] #5 `--apply-stdin` reads one JSON document. A missing or null answer means skipped and writes nothing for that question. No new secret flag exists. `--hf-token` is a stated, time-boxed exception to the house rule that a secret is never put on a command line. The reason: README.md:224 documents it for people's own scripts, and launcher and app ship together, so nothing in the product needs it. Until Robert decides, it keeps working, writes the settings row only, never echoes the value, and prints one line saying it is deprecated and why. This is this record's choice, not Robert's; whether the flag survives is an open question in ADR-015, and the parent's criterion 6 names the exception. If he removes it, the flag is refused with one sentence that points at stdin and getpass and does not echo the value, README.md:224 is corrected, and a test pins the refusal. Either way no code in this task passes a secret on argv. The launcher's existing use (packaging/launcher/myscribe_launcher.py:475-476) goes in TASK-089.15 criterion 3, and install.py never has one (TASK-089.17).
- [ ] #6 A typed Hugging Face token is checked with one announced HEAD before it is saved: 401 reads 'token not recognised', 403 reads 'conditions not accepted' with the URL. A refused token is not saved, and its id comes back in `reopen`. A typed OpenAI key is checked with the authenticated model list, and a rejected key is not written. For OpenRouter a free key check is used only if its endpoint is verified at implementation - nobody has verified one - and otherwise the paid one-word probe runs on consent only, default No.
- [ ] #7 Typed secrets are written to the settings rows only (hf_token, llm_key_<provider>): the rows Settings writes and can clear. `.env` is not rewritten for a secret. A SENTINEL test covers stdout, setup.json and the install log.
- [ ] #8 Red first (today: exit 0, 'saved: provider', no warning): choosing openrouter or openai with no key found and the key question skipped saves the provider, states in words that AI actions will show a no-key card until a key exists, and says where to add one.
- [ ] #9 A sitting never writes default_diarize: not as a guard, not for `{}` answers, and not as a side effect of saving the tier. A test reads the row before and after a tier-only sitting on a fresh database, and finds it absent both times.
- [ ] #10 Run bare on a TTY, it is the console asker over the same questions: Enter accepts the shown default, `s` skips, secrets go through getpass. On a non-TTY it never calls getpass, prints the found table and what is open, exits 0 and writes no stamp. Red first: today it prints 'saved: nothing' and writes one. The TTY run needs a real terminal - PowerShell or cmd, not Git Bash - and the notes say which was used.
- [ ] #11 A stamp is written when a sitting ends, including after a failed or skipped download, and it lists the answered and the skipped ids. Its format, its gate and its migration are TASK-089.11's. Exit codes 3, 2 and 1 are preserved and tested.
- [ ] #12 `--provider` rejects an unknown value before anything is written. Red first: today a token given together with a bad provider is already saved when the ValueError arrives (scribe/setup.py:114-121).
- [ ] #13 Progress is JSON lines when stdout is a pipe, at most one line per whole percent. On a TTY it is a bar with speed and ETA.
- [ ] #14 An all-skipped run in a scratch directory exits 0, writes no settings row at all, leaves `.env` unchanged, and the stamp lists every id as skipped.
- [ ] #15 The 'Four answers, and no more than four' docstring (scribe/setup.py:9), CHANGELOG.md:107-110 and the tests that state that number are revised knowingly, and the diff is shown. Two more recorded items are reversed by criterion 7 and are named with them: TASK-040.06's ticked criterion #2, 'writes it to the per-user .env', and write_token's docstring, which gives the reason - the first run happens before there is a browser to type into, and `.env` is the file the per-user home already carries (scribe/setup.py:85-93). Today apply() writes both `.env` and the row (:115-116). The reason for the reversal: a settings row is what Settings can show and clear, and a typed secret is not copied to a second place. TASK-040.06 is not edited; this task's notes say what changed and why.
- [ ] #16 The key question itself (requirement 2), red first: today no key question exists at all. With provider openrouter chosen and no key in any source the resolver reads, `--plan` contains the question llm_key_openrouter with kind secret and a shown_if on the provider answer; the same for openai. With a key planted in any one source - the settings row, the process environment, `.env`, the registry - the question is absent and the found table names the source. A table-driven test covers both providers and every source. Ollama has no key question.
- [ ] #17 `--diarize/--no-diarize` goes, with Answers.diarize (scribe/setup.py:51, :164-165; written through save_defaults at :125-135). It is the one path by which setup still writes default_diarize, and the brief's W6 says setup does not write it 'at all': that is only checkable when no path does. Nothing asks it - the Tk form never sets it (packaging/launcher/myscribe_launcher.py:579-584) - and the row stays reachable where it belongs, in the transcribe dialog and under Settings > Transcription. Red first: today `python -m scribe.setup --no-diarize` writes the row. After the change the parser refuses the flag, a stdin document carrying a diarize answer does not write the row either, and one test shows that no setup code path writes it. README.md:224 is corrected, and the launcher's pass-through (packaging/launcher/myscribe_launcher.py:481-482, pinned by tests/test_launcher.py:407) goes in TASK-089.15. The design spec and ADR-015 say the same. This is this record's reading, not Robert's decision, and ADR-015 lists it as an open question. The other way: keep the flag as an explicit-only answer outside the rule - a person typing `--no-diarize` is choosing, not guarding - and then it stays documented, it is the one writer setup keeps, and no `--plan` question, no `--apply-stdin` answer and no door sends it.
- [ ] #18 The model question for an Ollama that was already there (the design spec's question 9). It is present only when the provider is Ollama, Ollama was already there and is running, no llm_model_ollama row exists, the default qwen3.5:4b is not pulled and another chat-capable model is (TASK-089.06). It lists chat-capable models only, and its default is skip - TASK-054: never save a model nobody chose. An answer writes MyScribe's own llm_model_ollama row, the row Settings writes (scribe/web/settings.py:909-911), and changes nothing in Ollama: a MockTransport test asserts that the sitting sends Ollama no request besides the reads TASK-089.06 makes. Skipped, nothing is written. One test for the question being present, and one each for the four conditions that keep it out of `--plan`.
- [ ] #19 The provider question (the design spec's question 5). It is present only when no llm_provider row exists; a re-run shows the stored value and never resets it. Its default is Ollama when TASK-089.06 reports it ready, and otherwise 'decide later', which writes no row - and no row means no provider (TASK-089.07). A cloud provider is never the default that Enter accepts, and each cloud choice carries the sentence that transcript text leaves this machine and that recordings pinned private are always refused. One test per default, over MockTransport states, and one that a skipped or 'decide later' answer leaves the row absent.
<!-- AC:END -->
