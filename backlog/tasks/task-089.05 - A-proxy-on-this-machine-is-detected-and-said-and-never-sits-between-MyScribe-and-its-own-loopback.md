---
id: TASK-089.05
title: >-
  A proxy on this machine is detected and said, and never sits between MyScribe
  and its own loopback
status: To Do
assignee: []
created_date: '2026-09-20 18:40'
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
- [ ] #1 First the fact, shown as output: with HTTP_PROXY and HTTPS_PROXY pointing at a closed port and no NO_PROXY, does OllamaProvider's probe still reach a loopback test server, and does the launcher's running_instance? The notes record the answer for httpx and for urllib separately. Until this run exists, the behaviour of MyScribe's own probes stays labelled as an inference; only the library defaults were measured.
- [ ] #2 Whatever the answer, a test pins it: every loopback request MyScribe makes - the Ollama probe, the launcher's /health probe - reaches 127.0.0.1 with a dead proxy configured. If the first criterion came out red, this test is red first.
- [ ] #3 A found HTTP_PROXY, HTTPS_PROXY, ALL_PROXY or NO_PROXY, and on Windows a system proxy, is reported by a function the setup engine can call: where it was found and the proxy's host, never credentials embedded in a proxy URL. A SENTINEL test plants `user:SENTINEL@host` and finds it in no output.
- [ ] #4 Nothing is written anywhere because of a found proxy, and no question is asked about it.
- [ ] #5 A download that fails while a proxy is configured says so in its one sentence: a proxy is configured at <host>, and the download did not get through it.
- [ ] #6 The Windows system-proxy leg needs a Windows machine with a system proxy set. Nobody has one configured, and setting one on Robert's machine for a test is his call. If it was not run, the notes say so and this box stays unticked.
<!-- AC:END -->
