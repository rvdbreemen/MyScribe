"""Do MyScribe's OWN two probes fail behind a proxy variable, the way the two
library defaults were measured to on 2026-09-20?

This is ADR-015's open question. Read-only towards the repository: no write,
no model load, no app started, no request that leaves this machine. It needs
an Ollama answering at 127.0.0.1:11434 and the repository's environment.

    <repo>/.venv/Scripts/python probe_product_proxy.py <repo>

The two probes are the product's own, not stand-ins:
  * scribe.llm.ollama.OllamaProvider.available() - httpx2, through client()
  * the launcher's running_instance(port) - urllib, stdlib only (ADR-011)

Two traps this avoids, both of which produced a wrong answer on the first try:
  * OllamaProvider.client() caches in self._client and builds on first use
    (scribe/llm/ollama.py:361-375), and httpx reads the proxy variables when
    the client is BUILT. Asking one provider twice therefore measures the
    first environment twice. A fresh provider is made per case.
  * running_instance() answers False for a port nobody serves, which looks
    exactly like a proxy swallowing it. A throwaway /health server is started
    here so that False means something.
  * urllib.request.urlopen builds a default opener on first use and keeps it
    in the module (_opener), and that opener holds the proxies read when it
    was built. install_opener(None) before each case drops it, or every case
    after the first measures the first environment again.
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(repo))

PROXY = {"HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9"}
ALL = ("NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy",
       "HTTPS_PROXY", "https_proxy")


def clean() -> None:
    for name in ALL:
        os.environ.pop(name, None)


def timed(label: str, call) -> None:
    start = time.perf_counter()
    try:
        outcome = f"OK    {call()!r}"[:74]
    except BaseException as exc:  # noqa: BLE001 - the failure IS the measurement
        outcome = f"FAIL  {type(exc).__name__}: {str(exc)[:44]}"
    print(f"  {label:36s} {time.perf_counter() - start:5.2f}s  {outcome}")


class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the stdlib's spelling
        body = json.dumps({"ok": True, "version": "probe"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


with socket.socket() as probe_sock:
    probe_sock.bind(("127.0.0.1", 0))
    PORT = probe_sock.getsockname()[1]
server = http.server.HTTPServer(("127.0.0.1", PORT), Health)
threading.Thread(target=server.serve_forever, daemon=True).start()

spec = importlib.util.spec_from_file_location(
    "myscribe_launcher", repo / "packaging" / "launcher" / "myscribe_launcher.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

from scribe.llm import ollama  # noqa: E402 - after sys.path is set

print(f"python {sys.version.split()[0]} | httpx2 {ollama.httpx2.__version__} | "
      f"a /health server of this script's own on 127.0.0.1:{PORT}")

for title, env in (
    ("no proxy variable set", {}),
    (f"HTTP_PROXY and HTTPS_PROXY at a closed port ({PROXY['HTTP_PROXY']}), NO_PROXY unset", PROXY),
    ("the same, plus NO_PROXY=127.0.0.1 (the bypass a fix would set)",
     {**PROXY, "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}),
):
    print(f"\n{title}:")
    clean()
    os.environ.update(env)
    urllib.request.install_opener(None)  # drop the opener that cached the previous case's proxies
    timed("OllamaProvider.available()", ollama.OllamaProvider().available)  # fresh: client not built yet
    timed(f"launcher.running_instance({PORT})", lambda: launcher.running_instance(PORT))

server.shutdown()
