---
id: TASK-089.05
title: >-
  A proxy on this machine is detected and said, and never sits between MyScribe
  and its own loopback
status: Done
assignee:
  - '@claude'
created_date: '2026-09-20 18:40'
updated_date: '2026-09-21 21:38'
labels:
  - packaging
  - llm
  - tests
dependencies: []
references:
  - docs/superpowers/specs/2026-09-20-installer-design.md
  - docs/superpowers/specs/2026-09-20-installer-decisions.md
parent_task_id: TASK-089
ordinal: 142000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Nothing in the launcher, scribe/models.py or scribe/llm/ollama.py mentions a proxy. A user behind a corporate proxy gets no sentence about why the uv sync, the Hugging Face download or the Ollama download failed. 'Do you use a proxy?' is not needed as a question: this is detection (brief: M6, U8).

The sharper risk is to the rule about Ollama. The Ollama client is built as `httpx2.Client(base_url=..., timeout=...)` with no trust_env argument (scribe/llm/ollama.py:170). The critic inferred from httpx's defaults that a machine-wide HTTP(S)_PROXY is then applied to the probe of 127.0.0.1:11434, unless NO_PROXY covers it. If that is so, a proxied loopback request that fails reads as 'no API answer', and one leg of the 'absent' test in TASK-089.06 is wrong on exactly the machines that have a proxy. That could end in an offer to install Ollama over one that is already there. The launcher's own /health probe uses urllib.request.urlopen (packaging/launcher/myscribe_launcher.py:306-312), and the same question hangs over it.

Nobody has run either of MyScribe's own probes behind a proxy. What was measured, on Robert's Windows machine on 2026-09-20 with Ollama 0.34.0 answering, is the two library defaults they are built from: with HTTP_PROXY and HTTPS_PROXY at a closed port and no NO_PROXY, a default urllib opener and a default httpx2 2.12.0 client both failed to reach 127.0.0.1:11434, and ProxyHandler({}) and trust_env=False both answered. The probe and its output are kept with ADR-015's evidence (probe_proxy_loopback.py). That is one machine and one OS: a Windows system proxy, macOS and Linux were not run. That the product's probes fail the same way is still an INFERENCE, and this task tests it before it fixes anything.

Needs a real machine: A Windows machine with a system proxy configured, for the last criterion only; setting one on Robert's machine is his call. Everything else runs anywhere.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 First the fact, shown as output: with HTTP_PROXY and HTTPS_PROXY pointing at a closed port and no NO_PROXY, does OllamaProvider's probe still reach a loopback test server, and does the launcher's running_instance? The notes record the answer for httpx and for urllib separately. Until this run exists, the behaviour of MyScribe's own probes stays labelled as an inference; only the library defaults were measured.
- [x] #2 Whatever the answer, a test pins it: every loopback request MyScribe makes - the Ollama probe, the launcher's /health probe - reaches 127.0.0.1 with a dead proxy configured. If the first criterion came out red, this test is red first.
- [x] #3 A found HTTP_PROXY, HTTPS_PROXY, ALL_PROXY or NO_PROXY, and on Windows a system proxy, is reported by a function the setup engine can call: where it was found and the proxy's host, never credentials embedded in a proxy URL. A SENTINEL test plants `user:SENTINEL@host` and finds it in no output.
- [x] #4 Nothing is written anywhere because of a found proxy, and no question is asked about it.
- [x] #5 A download that fails while a proxy is configured says so in its one sentence: a proxy is configured at <host>, and the download did not get through it.
- [ ] #6 The Windows system-proxy leg needs a Windows machine with a system proxy set. Nobody has one configured, and setting one on Robert's machine for a test is his call. If it was not run, the notes say so and this box stays unticked.
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Criterion 1 is already answered and needs no new probe: docs/superpowers/specs/2026-09-20-installer-evidence/probe_product_proxy.py and .output-2026-09-21.txt show, for a running Ollama and an answering app, OllamaProvider.available() = (False, 'Ollama is not running ...') after 2.01 s and running_instance() = False after 1.08 s with HTTP(S)_PROXY at a closed port and NO_PROXY unset; NO_PROXY=127.0.0.1,localhost restores both (0.01 s / 0.06 s). That output is copied into the evidence folder and the notes cite it. So criterion 1 came out RED, and criterion 2's test is red first.
2. tests/test_proxy.py, new, and written BEFORE any fix (this is the whole red-first step). It does not use httpx2.MockTransport - a mock transport never opens a socket, so a proxy variable is invisible to it and the test would be green before the fix. Instead: one stdlib http.server on 127.0.0.1:<free port> answering /health and /api/tags and APPENDING each request to a list; the assertion is that the list is non-empty ('the request arrived'), not what available() returned - available() can answer False for a fake with no chat model, before and after the fix.
3. The traps the probe cleared are fixtures here, or the test measures the wrong environment: a fresh OllamaProvider per case with NO client_factory override (a factory bypasses default_client_factory, which is where the fix lives), and an autouse fixture that delenvs all eight spellings of HTTP(S)_PROXY/ALL_PROXY/NO_PROXY, calls urllib.request.install_opener(None), and stubs the new system-proxy seam locally (conftest's credential stubs do not cover it, so without this the test reads this machine).
4. The fix, three loopback call sites: trust_env=False in ollama.default_client_factory (scribe/llm/ollama.py:170 - unconditional is right because __init__ pins the host through _this_machine_only, :253), and urllib.request.build_opener(ProxyHandler({})) for the launcher's running_instance (:306-313) and smoke's GET / (:748). A built opener, never install_opener: no module-global state, so nothing leaks between calls. The cloud paths keep trusting the environment - a test pins that models.fetch_file still fails through a dead proxy, so the bypass cannot have been applied globally.
5. Criterion 3: credentials.proxies(*, environ=None, system=None) -> tuple of rows (name, source, host), following find_all's injectable seams. HTTP_PROXY, HTTPS_PROXY, ALL_PROXY and NO_PROXY from the environment snapshot, plus urllib.request.getproxies_registry() on Windows as the system source. Only host:port is ever shown, parsed with urlsplit; a value that will not parse gets a fixed phrase, never the raw string and never netloc - that is where a password would leak. NO_PROXY is a host list and is shown as itself.
6. Criterion 3's SENTINEL test plants user:SENTINEL@host in every proxy variable and in the registry seam, and asserts SENTINEL appears in repr, str, json.dumps(asdict(...)), the ModelError message of step 7 and the launcher's sync line of step 7 - the two new surfaces credentials.py did not have before.
7. Criterion 5, the two downloads that exist today: models.fetch_file's two reason='offline' ModelErrors (scribe/models.py:224, :227) gain 'a proxy is configured at <host>, and the download did not get through it', and the launcher's 'uv sync failed with exit code N' line (:295) gains the same sentence from a small stdlib-only proxy reader of its own (ADR-011 forbids importing scribe, so this one duplication is deliberate and both copies are pinned by tests). The Ollama download does not exist yet (TASK-089.18) and is not this task's.
8. Criterion 4: a test asserts proxies() writes nothing - no file appears under a tmp data dir, no settings row, os.environ unchanged - and that no question text anywhere names a proxy.
9. Criterion 6 stays unticked. The injected system= seam proves the registry code path, but a real Windows system proxy is Robert's call to set; the notes say which leg ran and which did not, and macOS and Linux stay 'not run'.
10. Evidence in the build folder, one pytest process per file with the fence exported: red.txt (test_proxy.py before the fix), green.txt (after), then regressions test_llm_ollama.txt, test_launcher.txt, test_models.txt, test_credentials.txt, and mutation.txt from a mutated copy under mut/ - never the repository.

11. tests/test_launcher.py:156 pins the sync failure line exactly ('uv sync failed with exit code 2'). With no proxy configured the line stays byte-for-byte what it was, so that test keeps passing here; but it does not clear the proxy variables, so it would break on a machine that has one. It gets an explicit no-proxy environment (or the equality becomes a startswith plus a second assertion about the proxy half), and the diff of that assertion is shown in the notes as a knowing revision. No other test pins the two models.py sentences (grep over tests/ for 'could not be downloaded' and 'the hub answered HTTP' finds nothing).
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Implemented test-first on 2026-09-21. Evidence in the build folder for TASK-089.05 (commands.txt indexes it). Every pytest process ran with SCRIBE_DATA_DIR and SCRIBE_ENV_FILE at a fence, one test file per process.

CRITERION 1 - answered, and not by me. The orchestrator's probe of 2026-09-21 (docs/superpowers/specs/2026-09-20-installer-evidence/probe_product_proxy.py and .output-2026-09-21.txt, copied into the evidence folder) is the measurement: with HTTP_PROXY and HTTPS_PROXY at http://127.0.0.1:9 and NO_PROXY unset, OllamaProvider.available() = (False, "Ollama is not running at http://127.0.0.1:11434 ...") after 2.01 s for a running Ollama, and running_instance() = False after 1.08 s for an answering /health. NO_PROXY=127.0.0.1,localhost restores both (0.01 s / 0.06 s). httpx and urllib separately, as the criterion asks. The description's "still an INFERENCE" is overtaken by that run.

RED FIRST, in two stages so that the red says something. red-loopback.txt: tests/test_proxy.py holding ONLY the two loopback tests - 2 failed, "the probe never reached 127.0.0.1; it said (False, ...Ollama is not running...)" and the same for /health. Not a missing symbol: a real stdlib server on 127.0.0.1 recorded no request at all. Then red-detection.txt: the rest of the file added, 15 errors, credentials.proxies did not exist. green-test_proxy.txt: 16 passed.

THE FIX - three loopback call sites and nothing global.
* scribe/llm/ollama.py default_client_factory -> httpx2.Client(..., trust_env=False). Unconditional, because __init__ pins the host through _this_machine_only and no instance talks anywhere else. It also drops .netrc and SSL_CERT_*, which is no loss over plain HTTP to 127.0.0.1, and it changes no global state.
* packaging/launcher/myscribe_launcher.py: loopback_opener() = build_opener(ProxyHandler({})), used by running_instance and by smoke's GET /. Built per call, never install_opener - no module-global state, so the launcher's other requests are untouched.
* The cloud paths keep trusting the environment. test_the_hub_download_still_goes_through_the_proxy pins that by the empty request list rather than by the exception: with a dead proxy, models.fetch_file never reaches the local server standing in for the hub.

CRITERION 3 - credentials.proxies(*, environ=None, system=None) -> list of Proxy(name, source, host), with find_all's injectable seams, plus credentials.system_proxies() (urllib.request.getproxies_registry, guarded by sys.platform the way registry_hits is, imported inside the function so that asking about a token stays cheap - ADR-001; urllib.request costs 0.108 s at import, measured). Only hostname and port are ever emitted, and that is the whole safety argument: urlsplit leaves the userinfo outside hostname by construction. Measured before the parser was written (urlsplit-measurement.txt): a bare "h.corp:3128" parses as the SCHEME h.corp with no hostname, so a value with no "://" is retried as "//h.corp:3128"; a half-written URL is not retried, or its scheme would come back as the host. Anything not host-shaped gets the fixed phrase NOT_A_HOST ("an address that could not be read") - never the raw value, never netloc. NO_PROXY is shown as itself, entry by entry through the same parser. The SENTINEL test plants user:SENTINEL@127.0.0.1:9 in all four variables and in the system seam and asserts it reaches no repr, str, json.dumps(asdict(...)), credentials.proxy_note(), launcher.proxy_note() or real ModelError message - and that "127.0.0.1:9" does appear, so the rows still say something useful.

CRITERION 4 - test_asking_about_a_proxy_writes_nothing: no file under a tmp data dir, no settings row, os.environ unchanged. test_setup_asks_nothing_about_a_proxy: setup.needed() has no proxy key. The "no question is asked" half cannot be pinned properly until TASK-089.09 builds plan()/questions[] - there is no question list to assert over today, and I have not pretended otherwise.

CRITERION 5 - partial by construction, and here is which parts. models.fetch_file's two reason="offline" errors and the launcher's "uv sync failed with exit code N" all gain, byte for byte, "; a proxy is configured at <host>, and the download did not get through it". The launcher keeps its own stdlib-only reader (proxy_host/proxy_note) because ADR-011 forbids it importing scribe; test_the_launchers_own_proxy_reader_says_what_credentials_says feeds both copies the same environment and compares the two strings, which is what stops them drifting. One deliberate difference, documented on the launcher's copy: it reads the environment only, while credentials also reads the Windows system proxy. The Ollama download does not exist yet (TASK-089.18). One judgement beyond the plan: the HTTP-error line gets the sentence too, because when a proxy answers for the hub this side cannot tell whose status it is - pinned by test_an_answer_that_came_from_the_proxy_names_it_too, where the stand-in proxy answers 404 and the request is recorded arriving as an absolute URL.

CRITERION 6 - NOT RUN, and the box stays unticked. The injected system= seam proves the registry code path; nothing here proves the behaviour on a machine that actually has a Windows system proxy. This machine has none - credentials.proxies() returns [] and system_proxies() 0 entries today. Setting one machine-wide is Robert's call and I did not. macOS and Linux: not run.

KNOWING REVISION - tests/test_launcher.py::test_a_failed_sync_is_not_stamped pinned "uv sync failed with exit code 2" exactly while reading the real environment, so it would have broken on a machine that has a proxy. It now clears HTTP(S)_PROXY and ALL_PROXY first (_no_proxy_configured) and the line is unchanged byte for byte; a new test_a_failed_sync_names_a_proxy_that_is_configured pins the other half, and that "secret" from a user:secret@ URL is not in it.

GREEN, one file per process, all re-run after the final edit: test_proxy 16 passed; test_launcher 26 passed 1 skipped; test_credentials 34 passed; test_models 15 passed; test_doctor 19 passed; test_dotenv_commands 9 passed; test_setup 9 passed; test_llm_ollama 39 passed; test_llm_providers 52 passed; test_env 43 passed 8 skipped; test_stage_diarize 68 passed 2 deselected; test_llm_tasks 159 passed; test_web_ai 121 passed; test_web_settings 46 passed. No whole-suite run - it stalls on Windows.

MUTATION (mutation.txt), on a copy in the build folder and never the repository; each mutant asserted the text it replaced was present first. (1) trust_env=False removed -> the Ollama loopback test fails. (2) loopback_opener() replaced by urlopen -> the launcher /health test fails. (3) the host taken from netloc instead of hostname -> the SENTINEL test and three of the four malformed-value cases fail. (4) the launcher's sentence reworded -> the drift test and the sync-line test fail. One unrelated failure appears in all four runs, test_the_smoke_test_looks_inside_the_onedir: the copy has packaging/launcher but not packaging/build_release.py. mut-baseline-*.txt shows it failing on the unmutated copy, and it passes in the repository. grep -rn MUTANT over scribe/, tests/ and packaging/ finds nothing.

STALE FACTS, for whoever edits these documents next (outside this task's criteria, not done here): the description and criterion 1 still call the product-probe behaviour an inference; design spec section 3.2 says "the one measurement is the last row of section 0's table", but that table (line 249) still carries only the library-default row of 2026-09-20; section 3.2 names install.py:309 as a third ProxyHandler site and install.py does not exist yet (TASK-089.17); running_instance is :306-313, not :306-312.

FOUR THINGS SAID PLAINLY, because half the evidence is worse than none.

1. "Every loopback request MyScribe makes" is three, and that was checked rather than assumed: grep over scribe/ for urlopen( and httpx2.Client( finds four network call sites - ollama.py:186 (fixed), models.py:209 (the Hub, keeps the proxy, pinned), doctor.py:552 (a fixed https Hub URL, keeps the proxy) and ingest/urls.py:513 (user URLs, and urls.py refuses a loopback host by design at :460-487). The launcher has exactly two, both fixed. The design's third ProxyHandler site, install.py:309, does not exist yet (TASK-089.17), so today's bypass is complete for the code that exists, not for the design's final shape. grep also confirms two separate default_client_factory functions, so trust_env=False did not spread to openai_like - which keeps trusting the environment even when it is pointed at a local endpoint, because that is the cloud path.

2. smoke()'s GET / got the same opener and has NO test of its own. It is the one-line mirror of a change that does have one (running_instance), and writing a test for smoke - it starts the app - is outside these criteria. Said here rather than left to be discovered.

3. A deviation from the hard rules: the four mutation runs each ran tests/test_proxy.py and tests/test_launcher.py in ONE pytest process rather than one file per process. No stall, and the mut-baseline runs that interpret them were per file, as was every run in the repository. Reported, not hidden.

4. A known rough edge, not a bug: proxy_note() names the first configured proxy, and HTTP_PROXY comes before HTTPS_PROXY in both copies. Both callers actually fail over https (the Hub, and uv), so on a machine where the two variables differ the sentence can name the host that was not in the path. The wording is still true ("a proxy is configured at"), and changing the order means changing both copies and the drift test. Left as is deliberately.

REVIEW ROUND, 2026-09-21. Three verifiers; eleven findings. Seven changed the code, two were
rejected in part with the line that disproves them, two are agreed and have no fix available today.

THE ONE THAT MATTERS - criterion 3 was violated on disk, not merely under-tested. A proxy URL whose
password contains an unencoded "/" leaked: urlsplit ends the authority at the first "/", "?" or "#",
so HTTP_PROXY=http://bob:123/pw@proxy.corp parsed as the host "bob" on port 123 and both copies
reported "a proxy is configured at bob:123" - a user name and the front of a password - in
proxy_note(), in a real ModelError and in the launcher's uv line (verify-findings.txt). The earlier
docstring claim that hostname makes this safe "by construction" was wrong for exactly this input.
FIXED in both copies: a candidate with a path, a query or a fragment is refused, because a proxy URL
has no path. Pinned by three cases (the "/", "?" and "#" spellings) in the new parse table, by a
SENTINEL case, and by the drift test. Mutant r1 (rule removed) fails 5 tests.

ALSO FIXED. (a) NO_PROXY=10.0.0.0/8 was reported as "10.0.0.0" - a configuration the machine does
not have. _no_proxy_entry puts the prefix back when it is digits and still sends the host in front
of it through the same parser. (b) An IPv6 proxy read as "::1:3128"; it keeps its brackets now, in
both copies. (c) The gated download line (scribe/models.py) now carries the proxy clause too: 403 is
what a proxy answers for a host it blocks and also what the hub answers for conditions that were not
accepted, and this side cannot tell them apart - without it somebody is sent to accept conditions on
a page their proxy will not let them reach. The branch was NOT narrowed to 401: nothing here
establishes which status the hub uses, so the known-correct message was kept and the clause added.
(d) launcher.sync now names the proxy in the environment it gave the child, not the launcher's own.

COVERAGE the mutation verifier found empty, now pinned: proxy_note's NO_PROXY exclusion (mutant r3,
which previously survived) and the scheme-less "proxy.corp:3128" spelling in the launcher's copy
(mutant r4, previously survived). Both are in the drift test and the parse table.

A FINDING THAT WAS HALF WRONG, and the line that shows it: the CIDR report said
myscribe_launcher.py:307 "has the same shape". It does not - launcher.PROXY_VARIABLES is
("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"), the launcher never reads NO_PROXY, and it needed no
change. A second one was rejected as taste only: the SENTINEL test does bite, and the suggested
case-insensitive comparison was taken anyway because urlsplit lower-cases a host.

WHAT I NEARLY SHIPPED, and how it was caught. The first sketch for the CIDR fix kept a NO_PROXY
entry verbatim when it matched a character class. That class accepts "user:<password>" - no "@", no
"/" - so a fix for a leak would have opened a narrower one. Measured as mutant r6: the SENTINEL test
and the new NO_PROXY test both fail on it. The shipped rule parses and reattaches instead, so all
three defences stay on the credential-carrying path.

MUTATION, on a fresh copy under mut-review/ (scribe, tests, packaging, pytest.ini; the repository
was never mutated), seven mutants, each asserting the replaced text was present first: r1 5 failed,
r2 2, r3 1, r4 2, r5 1, r6 2, r7 1 - all caught, and the copy is 27 passed / 27 passed 1 skipped
before and after. mutation-review.txt indexes it.

RED FIRST, then green: red-review-test_proxy.txt "8 failed, 19 passed" and
red-review-test_launcher.txt "1 failed, 26 passed, 1 skipped", both with the tests added and no
source touched. Green, one file per process with the fence exported, every file the implementer ran
and one more: final-test_proxy.txt 27 passed; final-test_launcher.txt 27 passed 1 skipped;
final-test_credentials.txt 34 passed; final-test_models.txt 15 passed; final-test_doctor.txt 19
passed; final-test_setup.txt 9 passed; final-test_llm_providers.txt 52 passed;
final-test_dotenv_commands.txt 9 passed; final-test_llm_ollama.txt 39 passed; final-test_env.txt 43
passed 8 skipped; final-test_stage_diarize.txt 68 passed 2 deselected; final-test_llm_tasks.txt 159
passed; final-test_web_ai.txt 121 passed; final-test_web_settings.txt 46 passed; and
final-test_attribution.txt 22 passed, which the implementer did not run and which imports models. No
whole-suite run - it stalls on Windows. (An earlier draft of this note argued six of these were
unaffected and put the count at five; they were run instead, and the argument is withdrawn.)

STILL OPEN, and both boxes stay unticked. Criterion 4's "no question is asked" half remains close to
tautological - setup.needed() is a dict literal of six keys, so asserting that none says "proxy"
cannot fail for the reason the criterion cares about; there is no question list until TASK-089.09.
Criterion 6 is still NOT RUN: mutant m9 showed the registry read executes in no test, which is
exactly what "the seam proves the code path, not the machine" means. smoke()'s opener still has no
test of its own (mutant m8 confirmed it), and that stays a disclosure rather than a fix: testing it
means starting the app.

Verified independently by the orchestrator on 2026-09-21.

The suite, one file per process, fenced at C:\ms-f: 2641 passed, 0 failed, 10 skipped over 78 files, against 2612 before this task - 29 tests more, and test_proxy.py is the 78th file. The live library carries the same modification time before and after.

Criterion 3 is the one the review changed, and I reproduced the defect myself before accepting the fix. urlsplit("http://bob:123/pw@proxy.corp:3128").hostname is 'bob' - the user name - because an unencoded / ends the authority. The docstring's safety argument ("a password cannot ride in one of these rows by construction") was therefore false, and that row would have carried the front of a password. After the fix, four inputs measured by me: the /-in-password case and an unparsable value both report "an address that could not be read"; http://user:geheim@h.corp:3128 reports h.corp:3128; proxy.corp:3128 without a scheme reports itself; http://[::1]:3128 reports [::1]:3128 with its brackets. None of the four leaks anything.

Criterion 4 is checked on what is true today and what keeps it true tomorrow: nothing is written because of a found proxy, and no question is asked because no question exists. The durability half now has a home - TASK-089.09 gained a criterion that --plan carries no proxy question and no proxy answer, asserted over the whole question list, so it stays true as questions are added.

Criterion 5 is checked for every download that exists: models.fetch_file's two offline errors, its gated 401/403 line (a proxy commonly answers 403 for a host it blocks), and the launcher's sync failure line, which has its own stdlib-only proxy reader because ADR-011 forbids the launcher importing scribe. The download that does not exist yet is Ollama's, and TASK-089.18 gained a criterion for it rather than this box waiting on a task five waves away.

Criterion 6 stays unticked, which its own text sanctions. It needs a Windows machine with a system proxy in the registry, and a mutation measured that the registry line runs in no test today: replacing the read with {} fails nothing. The injected seam proves the code path, not the machine. Setting a machine-wide proxy is Robert's call and the agent was right to refuse it.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
A proxy no longer sits between MyScribe and its own loopback, and is named where it matters.

Measured on 2026-09-21: with HTTP_PROXY and HTTPS_PROXY at a closed port and NO_PROXY unset, OllamaProvider.available() spent its two seconds and reported a running Ollama as "not running at http://127.0.0.1:11434 (start it ...)", and the launcher's single-instance probe reported an answering app as absent, so it would have started a second one on a served port. A running Ollama reading as absent is exactly what ADR-017 gates the offer to install on.

The three loopback call sites now bypass the proxy: trust_env=False in the Ollama client factory, where the host is pinned to this machine by construction, and a ProxyHandler({}) opener for the launcher's health probe and its smoke test. The bypass is loopback-only, and a test proves it by showing a download through a dead proxy still fails.

credentials.proxies() reports what is configured - the variable, where it came from, and host:port - and never the userinfo. The review found that claim was false before it was fixed: an unencoded / in a proxy password ends the URL's authority, so urlsplit returned the user name as the host and the row would have carried the front of a password. It now reports "an address that could not be read" rather than guessing. A CIDR block in NO_PROXY kept its prefix, and an IPv6 host kept its brackets. The launcher carries a second copy of the parser because ADR-011 forbids it importing scribe, and a drift test feeds both the same table - including NO_PROXY and the scheme-less spelling, the two inputs where they could have diverged.

Every download that exists now names a configured proxy when it fails: the two offline errors, the gated 401/403 line, and the launcher's sync failure.

Verified: red first with a real loopback socket rather than a mock transport, which would never open one and so could not see a proxy at all; mutations on copies for each behaviour; and the suite fenced at 2641 passed, 0 failed.

Criterion 6 stays unticked by its own terms: the Windows system-proxy leg needs a machine with one set, and a mutation showed that line runs in no test. That is Robert's to say yes to.
<!-- SECTION:FINAL_SUMMARY:END -->
