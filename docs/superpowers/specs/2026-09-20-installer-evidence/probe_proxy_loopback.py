"""Does a proxy variable sit between a default HTTP client and the loopback?

Read-only: four GET requests to Ollama's version endpoint on 127.0.0.1:11434, with
HTTP_PROXY and HTTPS_PROXY pointing at a closed port and NO_PROXY unset. It needs an
Ollama that answers there, and the repository's environment for httpx2.
Usage: <repo>/.venv/Scripts/python probe_proxy_loopback.py      Written 2026-09-20.

What it proves: the behaviour of the two library defaults that the product's probes are
built from (scribe/llm/ollama.py:170, packaging/launcher/myscribe_launcher.py:309).
What it does not prove: OllamaProvider's own probe and the launcher's running_instance
were not called; a Windows system proxy set in the registry, macOS and Linux were not run.
"""
import os
import platform
import time

os.environ.update(HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9")
for name in ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy"):  # Windows names are case-blind
    os.environ.pop(name, None)

import urllib.request  # noqa: E402 - after the environment is set, on purpose

import httpx2  # noqa: E402

URL = "http://127.0.0.1:11434/api/version"


def timed(label, call):
    start = time.perf_counter()
    try:
        result = ("OK", call())
    except Exception as exc:  # noqa: BLE001 - the failure is the measurement
        result = ("FAIL", type(exc).__name__, str(exc)[:60])
    print(f"{label:26s} {time.perf_counter() - start:5.2f}s  {result}")


print(platform.platform(), "| python", platform.python_version(), "| httpx2", httpx2.__version__)
print("HTTP_PROXY =", os.environ["HTTP_PROXY"], "| NO_PROXY =", os.environ.get("NO_PROXY"))
timed("urllib default", lambda: urllib.request.urlopen(URL, timeout=5).read())
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
timed("urllib ProxyHandler({})", lambda: opener.open(URL, timeout=5).read())
timed("httpx2 default", lambda: httpx2.Client(timeout=5).get(URL).text)
timed("httpx2 trust_env=False", lambda: httpx2.Client(timeout=5, trust_env=False).get(URL).text)
