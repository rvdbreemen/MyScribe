---
id: TASK-100
title: Ollama can run on another computer in the local network
status: Done
assignee:
  - '@claude'
created_date: '2026-09-28 08:23'
updated_date: '2026-09-29 03:48'
labels:
  - llm
  - settings
  - feature
  - privacy
dependencies: []
priority: medium
ordinal: 174000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Robert, 2026-09-28: the AI settings get an address for Ollama, so MyScribe can use an Ollama on another machine in the local network (a GPU box, a NAS) and not only on this computer. Decided with Robert: an address field on the existing Ollama provider (not a separate provider); a recording pinned private may go to an Ollama on the local network ('trust my network'), so the privacy promise becomes 'never leaves your local network' - recorded in a new Proposed ADR; released as a beta, 0.8.0b1, published as a GitHub pre-release so Latest stays on the stable release. Before this, scribe/llm/ollama.py refused any host but loopback (_this_machine_only), and nothing wired a host.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [x] #1 Settings > AI providers has an Ollama address field (default http://127.0.0.1:11434); a saved address is what every Ollama call uses - chat, analysis, labels, speakers, the self-test and the model list
- [x] #2 Only this machine or the local network is accepted: loopback, private, link-local and 100.64.0.0/10 (Tailscale) addresses, and local names (single-label, .local, .lan, .internal, .home.arpa); a public IP or an internet domain is refused with a sentence and nothing is saved
- [x] #3 An address may be typed as host, host:port or a full http(s) URL; it is stored normalised, with 11434 as the default port
- [x] #4 A recording pinned private may be sent to an Ollama at a local-network address; README, docs and the privacy text say 'never leaves your local network', and a Proposed ADR records the change
- [x] #5 Red first for each behaviour, mutants on a copy, affected test files green, and a real run: MyScribe on this laptop talks to Ollama through this laptop's LAN address
- [x] #6 release.yml publishes a tag like v0.8.0b1 as a pre-release that does not become Latest; 0.8.0b1 is released that way
<!-- AC:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Built 2026-09-28. scribe/llm/ollama.py: SETTING_HOST llm_ollama_host read in OllamaProvider.__init__ (explicit host > saved row > 127.0.0.1:11434); normalise_host (host, host:port, http(s) URL -> scheme://host:port, 11434 default; paths, other schemes, bad ports refused); _local_network_only replaces _this_machine_only (loopback, private, link-local, 100.64/10, localhost, single-label, .local .lan .internal .home.arpa; else ValueError naming the host). Settings: ollama_host field in the Save form, validated before anything is written; empty drops the row; label 'Ollama (local)'; page says 'Nothing leaves your local network' and shows the address. ollama_setup.state (the install offer) stays about this computer. release.yml + packaging/release_kind.py: a PEP 440 pre-release tag is published as a pre-release, make_latest false. tests/test_app.py version regex allows a pre-release (deliberate). Evidence: new tests red on the old code (41 failed in test_llm_ollama + test_release_kind, 5 in test_web_ai), then green: test_llm_ollama 83, test_release_kind 11, test_web_ai 146, test_app 17, test_install 56, test_launcher 103, test_llm_privacy 19, providers 61, tasks 160, chat 27, labels 19, ollama_setup 104, proxy 29, setup_plan 181, web_settings 55, doctor 56. Mutants on a copy: 13 of 13 killed. Real run: a TCP relay on this laptop's LAN address 192.168.88.32:11435 to the real Ollama; MyScribe 4299 (fenced library) refused ollama.example.com with the sentence, saved 192.168.88.32:11435 as http://192.168.88.32:11435, showed 'local network', fetched 3 models through it, and provider test job 1 answered ok (qwen3.5:4b, 12.8 s) with 3 relay connections during the test. ADR-024 Proposed.

Released 2026-09-28: merged to main (0576431), tag v0.8.0b1; release run 36449935588 built linux-x64, macos-arm64, windows-x64 and published. GitHub reports prerelease=true, draft=false; releases/latest stays v0.7.2. SHA256SUMS equals GitHub's asset digests. CI on the branch green on three OSes (run 36398663222: 3597 passed on Ubuntu and macOS, 2249 + 1352 on Windows). ADR-024 stays Proposed until Robert accepts it. Not done: a real second machine running Ollama (Robert cannot test it; the relay on this laptop's LAN address stood in).

Stable 0.8.0 released 2026-09-28 from main 4090de0 (same code as 0.8.0b1): CI 36475514662 green on three OSes, release run 36477046820 all jobs success, Latest is v0.8.0, 7 assets match SHA256SUMS. Installed on Robert's laptop over the beta: /health reports 0.8.0 with library D:/Data/MyScribe/data.

ADR-024 accepted by Robert on 2026-09-29 (adr accept --confirm); open question answered, adr-lint --strict docs/adr clean, index regenerated.
<!-- SECTION:NOTES:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Ollama may run on another computer in the local network. Settings > AI providers has an Ollama address; only this machine and local-network addresses are accepted, and a private recording may go there (ADR-024, accepted 2026-09-29). Verified with red-first tests, 13/13 mutants killed, CI green on three OSes, and a real run through this laptop's LAN address. Shipped as pre-release 0.8.0b1, then as stable 0.8.0 (Latest), installed on Robert's laptop.
<!-- SECTION:FINAL_SUMMARY:END -->
