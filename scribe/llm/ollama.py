"""Ollama: the provider whose text never leaves the machine.

Ollama speaks an OpenAI-compatible dialect on `/v1`, and this module
deliberately does not use it. The native `/api/chat` is the only place
`num_ctx` and `keep_alive` can be set, and both matter here:

* **`num_ctx`** decides how much transcript the model may actually read.
* **`keep_alive`** decides how long the weights stay in VRAM afterwards -
  which, on a machine that also runs Whisper on the same card, is the
  difference between a warm second call and a cold reload each time.

`is_local = True` is the single bit `privacy.assert_allowed` reads. Everything
this class does happens over loopback, which is why a pinned media may use it
and no cloud provider.

**Measured against the live daemon on this machine, 2026-09-02** (Ollama at
http://127.0.0.1:11434, `qwen3.5:4b` / `qwen3.5:9b` / `gemma4:12b` pulled):

* `GET /api/tags` -> `{"models":[{"name":"qwen3.5:4b", ...}, ...]}`.
* `POST /api/chat` -> `{"model", "message":{"role","content"[,"thinking"]},
  "done_reason", "prompt_eval_count", "eval_count", ...}`. The two counts are
  Ollama's names for prompt and completion tokens.
* A model that is not pulled -> **HTTP 404** `{"error":"model 'x' not found"}`.
  Recoverable by one `ollama pull`, so the message says so.
* A model runner that cannot start - VRAM exhausted, which is easy to reach on
  a 16 GB card with an embedding model pinned - -> **HTTP 500**
  `{"error":"model runner has unexpectedly stopped ..."}`. Transient by
  nature: it goes in the retryable half of the taxonomy alongside a refused
  connection.
* **A thinking model can answer with an empty `content`.** `qwen3.5:4b` spent
  its whole budget in `message.thinking` and returned `content: ""` with
  `done_reason: "length"` at 32, 600 *and* 2500 output tokens; at 4000 it
  answered in 7.3 s having spent 410 completion tokens, most of them thinking.
  An empty string is not a summary, so it is a `BadResponse` here rather than
  a successful empty answer. The consequence for callers: on the shipped local
  default, a per-task output budget below roughly a thousand tokens buys a
  `BadResponse` rather than a short answer.
* **`think: false` switches the thinking off** (TASK-029, Ollama 0.33.3,
  2026-09-11). `qwen3.5:4b`, `qwen3.5:9b` and `gemma4:12b` all report the
  `thinking` capability. On the real cleanup template and system prompt,
  `qwen3.5:4b` with thinking on spent 6,000 of 6,000 tokens, returned an
  empty `content` and took 78.7 s; with `think: false` it wrote a correct
  cleaning in 107 tokens and 1.5 s. So `ChatRequest.reasoning_off` is sent as
  a top-level `think: false`, and it is never dropped: the daemon's routes.go
  refuses only a truthy `think` for a model without the capability. Measured
  on the 4B only; what the 9B and the 12B write with thinking off is not.
  `eval_count` covers thinking and answer together, so the thinking is
  reported in the one unit the answer carries - its characters.
* **An overflowing prompt is not an error, and it takes two checks to catch.**
  Asked for `num_ctx: 512` with a ~3,600-token prompt, the daemon answered
  **HTTP 200** with `prompt_eval_count: 1026` and `done_reason: "length"` - and
  `/api/ps` then reported it had allocated a 2,048-token window, not the 512
  requested. So the requested window is a request, the endpoint never refuses,
  and a caller who trusts the 200 gets an answer written from a silently
  truncated prompt. `_answer` therefore compares `prompt_eval_count` against
  the window that was asked for and raises `ContextTooLong` when it did not fit.

  That check alone is not enough, and the second measurement (2026-09-03) is
  why. At the shipped `num_ctx` of 8192 with a **9,029-word** prompt the daemon
  answered **HTTP 200** with `prompt_eval_count: 4098` - exactly `num_ctx / 2
  + 2`, and 2050 at `num_ctx: 4096` - while `/api/ps` reported the requested
  window allocated *exactly*. The window is honoured; the prompt is halved to
  fit it. So the count comes back *below* `num_ctx` and the post-hoc check is
  structurally unable to see the case it exists for, and what came back was a
  fluent summary of whichever half survived. `_refuse_a_prompt_that_cannot_fit`
  is the other half: a word count is a lower bound on tokens that needs no
  tokenizer, so a prompt with more words than the whole window is refused
  before the request is sent. Together they are the plan's "the value the
  request asks for is the value the caller gets, or `ContextTooLong` is
  raised", implemented against what the API does rather than against a 400 it
  never sends.

There is no key: nothing here resolves one, nothing logs one, and `available()`
never mentions one.

And there is no remote host. `is_local = True` is a class attribute the pin
reads; `host` was a per-instance argument, so the two could disagree and a
settings row naming another machine would have carried a pinned recording to
it. `_this_machine_only` makes that unconstructable.
"""

from __future__ import annotations

import ipaddress
import sqlite3

from scribe import db
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx2

from scribe.llm import base
from scribe.llm.base import ChatRequest, ChatResponse

DEFAULT_HOST = "http://127.0.0.1:11434"

DEFAULT_TIMEOUT = 300.0
"""Seconds for one completion at the shipped window, and the floor under any
other. Generous where the cloud providers get 60: a local 9B model on a busy
card takes tens of seconds to load before it emits a first token, and killing
it at 60 s would turn a slow answer into no answer.

It is a floor and no longer the whole answer, because the work is no longer
one size. This number was measured with a 9B model against 8192 tokens; a 27B
at q8 against 32768 has four times the prompt to read and three times the
weights to read it with, and 300 s stopped being generous - it killed job 126
at exactly five minutes with "ollama did not answer: timed out". See
`timeout_for`."""


def timeout_for(num_ctx: int) -> float:
    """How long one completion may take, given the window it was planned for.

    Linear in the window, floored at `DEFAULT_TIMEOUT`. Prompt evaluation is
    the part that grows - the model has to read every token before it writes
    one - so a window four times larger is roughly four times the wait, and a
    timeout that ignores that turns "slow" into "failed" precisely when a user
    has chosen the bigger model on purpose.

    Not a promise that the answer arrives: it is the point at which waiting
    longer stops being useful, and it still ends a daemon that has hung.
    """
    scale = max(1.0, float(num_ctx) / float(DEFAULT_NUM_CTX))
    return DEFAULT_TIMEOUT * scale

PROBE_TIMEOUT = 2.0
"""The bound on `available()`. See the note on that method - it is the one
sanctioned network call in a method the base class says makes none."""

CHAT_CAPABILITY = "completion"
"""The one capability that says a model can hold a conversation (`chat_models`).

Ollama serves embedders and chat models from one endpoint, and nothing else
separates them. The name does not: `qwen3-embedding:0.6b` is an embedder and
`bge-m3` does not say so. No other capability does either: measured on this
machine on 2026-09-21, `qwen3-embedding:0.6b` reports `['tools', 'thinking',
'embedding']`, so `tools` and `thinking` are both useless as a test.

Asked as a presence and not as "does not say `embedding`". On this machine's
eight models the two would answer the same - every chat model here lacks
`embedding` and every embedder has it - so the difference is not measurable
here, and it is still the right way round: `completion` names the thing being
asked for, while the absence of one known kind accepts every kind nobody has
thought of yet. Ollama's capability list has grown before."""

DEFAULT_NUM_CTX = 8192
"""What this plans against when the daemon will not say what a model holds.

An assumption, and deliberately a small one: assuming a large context is how a
prompt gets silently truncated, because the daemon answers 200 either way. A
value a 4B model holds on this card without evicting Whisper, and the number
every measurement in this docstring was taken at."""

SETTING_NUM_CTX = "llm_num_ctx"
"""The settings row that moves `MAX_NUM_CTX`, for a machine that knows better."""

MAX_NUM_CTX = 32768
"""The ceiling a *measured* window is planned against.

The distinction this turns on, and the first version got wrong: an assumption
and a measurement are not the same thing. `DEFAULT_NUM_CTX` is what this plans
against when nothing is known, and it stays small on purpose - assuming a large
context is how a prompt gets silently truncated. But when the daemon *reports*
a model's window, that is not an assumption, and refusing to use it made
choosing a bigger model do nothing at all: two models reporting 262144 tokens
were both planned at 8192, while the error message told the user to switch
between them (jobs 118-122, 2026-09-19).

What the ceiling is actually for is memory. A window is KV cache and the cache
is this machine's: a quarter of a million tokens beside a 27B model is how a
local box starts swapping. 32k is four times the default - room for an hour of
transcript in one call - and a cache a 16 GB card can hold. A machine that
knows better moves it with the `llm_num_ctx` setting, in either direction."""


KEEP_ALIVE = "5m"
"""How long the weights stay resident after an answer. Long enough that the
chunks of one map-reduce share a single load, short enough that the card is
free again well before the next transcription needs it."""


def default_client_factory(*, base_url: str, timeout: float):
    """The real HTTP client. Named to match `openai_like.default_client_factory`
    so both providers' transport seams read the same way.

    **`trust_env=False`, unconditionally** (TASK-089.05). httpx reads
    HTTP(S)_PROXY when a client is *built*, and measured here on 2026-09-21
    with a running daemon: with those variables at a closed port and no
    NO_PROXY, `available()` spent its whole 2 s budget and answered "Ollama is
    not running" for an Ollama that was running. A proxy has no business
    between this process and this machine, and a running daemon reading as
    absent is what an offer to install one would be gated on (ADR-017).

    Unconditional because `__init__` pins the host through `_this_machine_only`:
    there is no instance of this client that talks anywhere else. The flag also
    switches off `.netrc` and the SSL_CERT_* variables, which is no loss over
    plain HTTP to 127.0.0.1. The cloud providers keep trusting the environment
    - somebody behind a corporate proxy needs it for OpenAI and OpenRouter -
    and `tests/test_proxy.py` pins that the hub download still goes through one.
    """
    return httpx2.Client(base_url=base_url, timeout=timeout, trust_env=False)


LOOPBACK_NAME = "localhost"
"""The one hostname every operating system reserves for this machine. Trusted
by name rather than resolved, because resolving it would mean a DNS lookup in a
constructor - and a hosts file that lies about `localhost` is a compromise of a
different order than the mistake this check exists to catch."""


def _this_machine_only(host: str) -> str:
    """`host`, or a `ValueError` if it is somewhere else.

    `is_local = True` is a **class** attribute, and it is the single bit
    `privacy.assert_allowed` reads to decide whether a pinned recording's words
    may leave this machine. `host` is a per-instance constructor argument. So
    the two can disagree, and the disagreement is exactly the failure the pin
    exists to prevent: one settings row naming a remote box would post a
    private transcript to it while the pin still answered "local, allow".

    Deriving `is_local` from the resolved host would not help. The pin resolves
    the provider *class* through `llm.provider_class` and never sees an
    instance, so a per-instance flag is a flag nothing reads - a worse bug,
    because it would look like a fix. Making the disagreement impossible to
    construct is the only version that holds.

    Nothing wires a `host` today (traced: `llm_stage`, `chat_tool.answer` and
    `selftest.probe` all call with no provider kwargs, and no settings row
    writes one). This is the guard for the day something does.
    """
    hostname = urlsplit(host).hostname
    if hostname == LOOPBACK_NAME:
        return host
    try:
        if ipaddress.ip_address(hostname or "").is_loopback:
            return host
    except ValueError:
        pass
    raise ValueError(
        f"OllamaProvider host {host!r} is not this machine, and this provider is registered as "
        f"local: privacy.assert_allowed reads OllamaProvider.is_local and would let a recording "
        f"pinned private be sent there. Use {DEFAULT_HOST} (or another loopback address); a "
        f"remote model is a cloud provider and needs a Provider of its own."
    )


def _names(rows: list[dict]) -> list[str]:
    """The model names in `/api/tags` rows, sorted."""
    return sorted(str(row.get("name")) for row in rows if row.get("name"))


def _capabilities_in(rows: list[dict], model: str) -> list[str] | None:
    """What those rows say `model` can do, or None when they do not say.

    Off the rows a caller already has rather than through `/api/show`: this is
    what lets `available()` ask the capability question without a second
    request on every settings render.
    """
    for row in rows:
        if str(row.get("name") or "") == model:
            found = row.get("capabilities")
            return found if isinstance(found, list) else None
    return None


class OllamaProvider(base.Provider):
    """A local Ollama daemon, spoken to natively."""

    name = "ollama"
    is_local = True
    # The model that provably answered on this machine on 2026-09-02, and the
    # one that fits beside whatever else holds VRAM: the 9B and the 12B both
    # failed to start their runner while 12.7 GB of the 16 GB card was in use.
    # A bigger model is a settings change, not a code change.
    default_model = "qwen3.5:4b"
    key_env_vars = ()  # nothing to resolve: loopback, no credential
    # Top level of the /api/chat body, not `options` (module docstring).
    reasoning_off_body = {"think": False}

    def __init__(
        self,
        conn: sqlite3.Connection | None = None,
        *,
        host: str = DEFAULT_HOST,
        model: str | None = None,
        num_ctx: int | None = None,
        client_factory: Callable[..., Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """`num_ctx` is a constructor knob rather than a `ChatRequest` field on
        purpose: `ChatRequest` is the shape *every* provider understands, and
        the context window is a thing only a local runtime lets you choose. The
        chunker (Task 3) will size it per transcript and pass it here.

        `None` - the default - means ask the daemon what the model holds
        (`context_tokens`) rather than assume the floor. A number means the
        caller has a reason, and is never second-guessed.

        `conn` is where the saved model comes from, the way `ceiling()` reads
        `llm_num_ctx` - it used to be accepted and unused. `provider_rows`
        builds `cls(conn)` with no model, so the settings page tested the class
        default: pull `gemma4:12b`, save it, and Settings still said
        `qwen3.5:4b` was not pulled and offered a download nobody needed
        (TASK-089.06). An explicit `model` still wins, because a caller that
        names one - `queue_provider_test` resolves the row itself - has a
        reason.
        """
        self.conn = conn
        self.host = _this_machine_only(host).rstrip("/")
        self.model = model or self._saved_model() or self.default_model
        self.num_ctx = DEFAULT_NUM_CTX if num_ctx is None else num_ctx
        self._num_ctx_is_mine = num_ctx is None
        self._windows: dict[str, int] = {}
        self.client_factory = client_factory or default_client_factory
        self.timeout = timeout
        self._client: Any | None = None
        self._client_timeout: float | None = None

    # --- what window this model actually has ----------------------------------

    def context_tokens(self, model: str | None = None) -> int:
        """How many tokens ``model`` may be planned against on this machine.

        The daemon knows: `/api/show` reports `<arch>.context_length` from the
        GGUF itself. Before this asked, every local call was planned against
        `DEFAULT_NUM_CTX` whatever the model held, which is how the `speakers`
        kind - whose answer budget alone is 8000 tokens - became arithmetically
        impossible on Ollama while working on every cloud provider (TASK-084).

        Clamped both ways. `MAX_NUM_CTX` is the cache this machine can hold;
        `DEFAULT_NUM_CTX` is the floor, because a 4k model is not a reason to
        plan for less than the daemon has always been asked for. A daemon that
        cannot answer - old, offline, a model that is not pulled - is not an
        error here: it gets the floor, which is what it got before.

        Cached per model: this is asked once per plan and once per call, and
        the answer is a property of a file on disk.
        """
        if not self._num_ctx_is_mine:
            return self.num_ctx
        name = (model or self.model or "").strip()
        if not name:
            return self.num_ctx
        if name not in self._windows:
            found = self._model_window(name)
            # No floor upwards: a model that really holds 4096 is planned
            # against 4096. Asking for more is how a prompt gets truncated
            # behind a 200.
            self._windows[name] = self.num_ctx if found is None else min(self.ceiling(), found)
        return self._windows[name]

    def ceiling(self) -> int:
        """The most a measured window may be used up to, here on this machine.

        `MAX_NUM_CTX` is a guess about memory that is right for a 16 GB card
        and wrong for both a 64 GB Mac and a 4 GB laptop, so it is a default
        and not a rule: the `llm_num_ctx` setting moves it either way. Read per
        call rather than cached, because a user who lowers it after a job
        swapped their machine means it now.
        """
        if self.conn is not None:
            try:
                with db.LOCK:
                    row = self.conn.execute(
                        "SELECT value FROM setting WHERE key=?", (SETTING_NUM_CTX,)
                    ).fetchone()
                if row is not None:
                    wanted = int(str(row["value"]).strip())
                    if wanted > 0:
                        return wanted
            except Exception:  # noqa: BLE001 - a setting that is not a number is not a ceiling
                pass
        return MAX_NUM_CTX

    def _saved_model(self) -> str:
        """The model this installation has chosen, or "".

        Read once in `__init__` rather than per call, the way `self.model` has
        always been a fixed attribute: a provider is short-lived and a model
        that changed underneath one would make `available()` and the request it
        reports on disagree. `ceiling()` is the other way round for its own
        reason, written there.

        A blank row is "" and leaves the class default standing: clearing the
        dropdown writes one (TASK-054's "its own default" option).
        """
        if self.conn is None:
            return ""
        try:
            with db.LOCK:
                row = self.conn.execute(
                    "SELECT value FROM setting WHERE key=?",
                    (base.model_setting_key(self.name),),
                ).fetchone()
            # Inside the try with the query, the way `ceiling()` has it: a
            # connection built without `row_factory` raises here and not there,
            # and a settings page must not go down over a row that has a
            # perfectly good default.
            return "" if row is None else str(row["value"] or "").strip()
        except Exception:  # noqa: BLE001 - a row we cannot read is no saved model
            return ""

    def _model_window(self, model: str) -> int | None:
        """`<arch>.context_length` from `/api/show`, or None if it cannot be had.

        Every failure is the same answer - None - on purpose: this is a better
        number when it can be got, never a new way for a task to fail.
        """
        try:
            response = self.client().post("/api/show", json={"model": model})
            if response.status_code != 200:
                return None
            info = (response.json() or {}).get("model_info") or {}
        except Exception:  # noqa: BLE001 - a window we could not read is not a failure
            return None
        arch = str(info.get("general.architecture") or "").strip()
        for key in ([f"{arch}.context_length"] if arch else []) + [
            k for k in info if k.endswith(".context_length")
        ]:
            value = info.get(key)
            if isinstance(value, int) and value > 0:
                return value
        return None

    @classmethod
    def window_for_model(cls, model: str | None = None) -> int | None:
        """`context_tokens` for a caller holding the class, not an instance.

        `tasks.context_tokens_for` plans before anything is constructed. Asking
        the provider to answer for itself keeps that planner free of a table of
        provider names, and keeps the daemon call inside the module that owns
        the daemon.
        """
        try:
            provider = cls()
            try:
                return provider.context_tokens(model)
            finally:
                provider.close()
        except Exception:  # noqa: BLE001 - planning never fails on a missing daemon
            return None

    # --- the transport --------------------------------------------------------

    def client(self, timeout: float | None = None):
        """The http client, built on first use.

        ``timeout`` is the per-call budget: a completion against a large window
        may take far longer than the probe that found the window (`timeout_for`).
        A different budget needs its own client, because the one being reused
        carries the timeout it was built with.
        """
        wanted = self.timeout if timeout is None else timeout
        if self._client is not None and wanted != self._client_timeout:
            self.close()
        if self._client is None:
            self._client = self.client_factory(base_url=self.host, timeout=wanted)
            self._client_timeout = wanted
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # --- is it there? ----------------------------------------------------------

    def available(self) -> tuple[bool, str]:
        """(ready?, why not) - and the one place this layer makes a network call
        from a method the base class says makes none.

        The base contract exists so a settings page render never waits on
        somebody else's API. This one waits on *this machine*: a loopback GET
        bounded by `PROBE_TIMEOUT` (2 s), which cannot hang on someone's DNS or
        TLS handshake. It is the only way to answer the question the plan
        actually asks - "not running" is a different problem, with a different
        fix, from "running, model not pulled" - and a cheaper way than showing
        the user a green tick for a daemon that is not there.

        Three answers, not two, since this row started reporting on the *saved*
        model: pulled is not the same as "you can talk to it". Every embedder
        is pulled, so `bge-m3:latest` in `llm_model_ollama` - typed into the
        field beside the dropdown, which takes any id - showed green here and
        failed in the first summary inside a job. The capability comes off the
        row this call already has, so the render still costs one GET: no
        `/api/show` fan-out here, whatever `chat_models()` may do elsewhere.
        """
        try:
            rows = self.tags()
        except base.Unreachable:
            return False, f"Ollama is not running at {self.host} (start it, then reload)"
        except base.LlmError as exc:
            return False, f"Ollama at {self.host} answered oddly: {exc}"

        names = _names(rows)
        if self.model not in names:
            pulled = ", ".join(names) if names else "none"
            return False, (
                f"Ollama is running at {self.host} but {self.model!r} is not pulled: "
                f"run `ollama pull {self.model}` (pulled: {pulled})"
            )

        # A daemon too old to report capabilities says None, and is taken at
        # its word: the alternative is one POST per render to second-guess it.
        capabilities = _capabilities_in(rows, self.model)
        if capabilities is not None and CHAT_CAPABILITY not in capabilities:
            reports = ", ".join(capabilities) or "nothing at all"
            return False, (
                f"Ollama is running at {self.host} but {self.model!r} is not a model you "
                f"can chat with: it reports {reports}, not `{CHAT_CAPABILITY}` - pick "
                f"another in Settings"
            )
        return True, f"Ollama at {self.host} with {self.model} ready"

    def tags(self) -> list[dict]:
        """The `/api/tags` rows, as the daemon sends them.

        The payload and not just the names, because what the rows carry is the
        answer to a question the names cannot settle: at Ollama 0.34.2 each row
        has a `capabilities` list, and that is the only way to tell an
        embedding model from one you can talk to (`chat_models`).

        One cheap GET on the one client, bounded by `PROBE_TIMEOUT` - the same
        call `models()` has always made, in the same place, so a settings
        render still costs exactly one request (`available`).
        """
        try:
            response = self.client().get("/api/tags", timeout=PROBE_TIMEOUT)
        except httpx2.HTTPError as exc:
            raise self._unreachable(exc) from None
        self._raise_for_status(response)
        payload = self._json(response)
        rows = payload.get("models", [])
        return [row for row in rows if isinstance(row, dict)]

    def models(self) -> list[str]:
        """The model names this daemon has pulled, sorted.

        *Every* pulled name, embedders included. That is what `available()`
        reports as "pulled: ..." and what `tasks.suggest_bigger_window` walks
        looking for a roomier window, so narrowing it would change two
        messages that are about what is on the disk. `chat_models()` is the
        narrower question.
        """
        return _names(self.tags())

    def chat_models(self) -> list[str] | None:
        """The pulled models that can hold a conversation, sorted - or None
        when this daemon cannot say.

        Measured against the live daemon on this machine on 2026-09-21 (Ollama
        0.34.2, 8 models). Five of the eight are embedders and three are not,
        and until this nothing asked: `available()` accepted any pulled name,
        so an embedder could be saved as the chat model, everything showed
        green, and the first summary failed inside a job. The test is
        `CHAT_CAPABILITY` and its reason is written there.

        Every row carried a `capabilities` key at 0.34.2. An older daemon that
        does not gets one `POST /api/show` per such row, which is why this is
        never called from a page render (`scribe/doctor.py`'s check list).

        None, never [], whenever a row could not be read and nothing
        chat-capable was seen anyway: "this daemon does not report
        capabilities" is not "this daemon has no chat model". The first must
        never read as ready and never as absent (ADR-017); the second is fixed
        by one `ollama pull`, and telling somebody to pull a model they may
        already have under the name nobody could read is the wrong sentence.

        A row that did answer still counts, though, so a daemon with one
        unreadable row and one chat model is ready and not unknown: what was
        read is not thrown away by what was not.
        """
        found: list[str] = []
        unreadable = 0
        for row in self.tags():
            name = str(row.get("name") or "")
            if not name:
                continue
            capabilities = row.get("capabilities")
            if not isinstance(capabilities, list):
                capabilities = self.capabilities(name)
            if capabilities is None:
                unreadable += 1
            elif CHAT_CAPABILITY in capabilities:
                found.append(name)
        if unreadable and not found:
            return None
        return sorted(found)

    def capabilities(self, model: str) -> list[str] | None:
        """What `POST /api/show` says `model` can do, or None if it will not say.

        The fallback for a daemon whose `/api/tags` rows carry no
        `capabilities` key. Per model, because that is the only way this
        endpoint answers - it takes one name.
        """
        try:
            response = self.client().post(
                "/api/show", json={"model": model}, timeout=PROBE_TIMEOUT
            )
            if response.status_code != 200:
                return None
            found = (response.json() or {}).get("capabilities")
        except Exception:  # noqa: BLE001 - a capability we could not read is not a failure
            return None
        return found if isinstance(found, list) else None

    def version(self) -> str:
        """What `GET /api/version` reports, or "" when nothing answers.

        Decoration, deliberately: it names the daemon in a doctor line and it
        decides nothing. `/api/tags` is the one call that says whether Ollama
        is answering (`ollama_setup.state`), because that is also the call
        whose payload the state is built from - asking two endpoints would
        leave a daemon that answers one and not the other in two states at
        once.
        """
        try:
            response = self.client().get("/api/version", timeout=PROBE_TIMEOUT)
            if response.status_code != 200:
                return ""
            return str((response.json() or {}).get("version") or "").strip()
        except Exception:  # noqa: BLE001 - a version we could not read is not a failure
            return ""

    # --- one completion ----------------------------------------------------------

    def complete(self, req: ChatRequest) -> ChatResponse:
        self._refuse_a_prompt_that_cannot_fit(req)
        try:
            # The budget that matches the window this call was planned for,
            # not the one a model-info lookup was built with.
            budget = timeout_for(self.context_tokens(req.model))
            response = self.client(budget).post("/api/chat", json=self._body(req))
        except httpx2.HTTPError as exc:
            raise self._unreachable(exc) from None
        self._raise_for_status(response, model=req.model)
        return self._answer(self._json(response), req)

    # --- the window, checked before the daemon gets a chance to truncate -------------

    @staticmethod
    def word_floor(*texts: str) -> int:
        """A *lower* bound on the token count of `texts`, needing no tokenizer.

        The pre-tokenizer of every model Ollama serves splits before each
        space-prefixed word, so a BPE merge cannot span a space and a word
        costs at least one token. Counting words therefore under-counts and
        never over-counts, which is the only direction a refusal may err in:
        this bound says "certainly more than N tokens", never "probably".

        Weakest for text without spaces - CJK above all, where a line is one
        "word" and many tokens. That weakness costs a missed refusal, not a
        wrong one, and the post-hoc check below is the other half.
        """
        return sum(len(text.split()) for text in texts)

    def _refuse_a_prompt_that_cannot_fit(self, req: ChatRequest) -> None:
        """Refuse, before sending, a prompt the window provably cannot hold.

        Measured 2026-09-03 at the shipped `num_ctx` of 8192 with a 9029-word
        prompt: HTTP 200, `prompt_eval_count: 4098` - exactly `num_ctx / 2 + 2`
        - and a fluent summary of whichever half survived. The daemon allocates
        the window that was asked for (`/api/ps` confirms it) and drops the
        front of the conversation to make the prompt fit, so the count comes
        back *below* `num_ctx` and `_answer`'s check is structurally unable to
        see it. Waiting for the answer cannot work; the only place left to
        notice is before the request.

        This is deliberately not the pessimistic `chunking.estimate_tokens`
        (3 chars/token). An estimator that over-counts would refuse prompts
        that would have fitted; the floor only refuses what certainly does not.
        """
        floor = self.word_floor(req.system, req.user)
        if floor >= self.num_ctx:
            raise base.ContextTooLong(
                f"the prompt is {floor} words, so at least {floor} tokens, and the window "
                f"asked for is {self.num_ctx}: ollama would drop the front of it and answer "
                f"from the rest without saying so; send less text (chunk it) or raise num_ctx"
            )

    def _messages(self, req: ChatRequest) -> list[dict]:
        messages = []
        if req.system.strip():
            messages.append({"role": "system", "content": req.system})
        messages.append({"role": "user", "content": req.user})
        return messages

    def _body(self, req: ChatRequest) -> dict:
        """Exactly what `/api/chat` takes.

        `num_predict` carries the seam's `max_output_tokens`; without it the
        budget is a number the caller passes to nobody, and a thinking model
        will happily spend an unbounded one. `stream: false` because a job
        stores a finished answer - streaming to a runner child that writes one
        row at the end would buy nothing.

        The reasoning hint adds `think: false`; without it no `think` key is
        sent at all, which is the body this provider always sent.
        """
        body: dict[str, Any] = {
            "model": req.model,
            "messages": self._messages(req),
            "stream": False,
            "options": {
                "temperature": req.temperature,
                "num_ctx": self.context_tokens(req.model),
                "num_predict": req.max_output_tokens,
            },
            "keep_alive": KEEP_ALIVE,
        }
        if req.reasoning_off:
            body.update(dict(self.reasoning_off_body))
        return body

    # --- reading the answer ---------------------------------------------------------

    def _answer(self, payload: dict, req: ChatRequest) -> ChatResponse:
        prompt_tokens = payload.get("prompt_eval_count")
        finish = payload.get("done_reason")

        # Measured, and the reason this check exists at all: the daemon accepts
        # a prompt that does not fit the window it was asked for and answers 200.
        # It fires when the daemon allocated *more* than was asked (512 -> 2048
        # on this machine) and read a prompt too big for the request.
        #
        # The case it cannot see - the daemon honouring the window exactly and
        # halving the prompt, so the count comes back *below* `num_ctx` - is
        # caught before the request instead, by
        # `_refuse_a_prompt_that_cannot_fit`. Neither check subsumes the other:
        # this one needs the answer, that one cannot wait for it.
        if isinstance(prompt_tokens, int) and prompt_tokens > self.num_ctx:
            raise base.ContextTooLong(
                f"ollama read {prompt_tokens} prompt tokens into the {self.num_ctx}-token "
                f"window asked for, so the prompt was truncated before the model saw it; "
                f"send less text (chunk it) or raise num_ctx"
            )

        message = payload.get("message") or {}
        text = message.get("content")
        # `""` and `None` are both non-answers here, where `openai_like` only has
        # to guard `None`: Ollama returns an empty string when a thinking model
        # spends its whole budget in `message.thinking`.
        if not (text or "").strip():
            raise base.BadResponse(self._empty_answer_reason(payload, message, req, finish))

        return ChatResponse(
            text=text,
            model=payload.get("model") or req.model,
            provider=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=payload.get("eval_count"),
            raw_finish_reason=finish,
            # Never refused (module docstring), so a hint asked for is a hint sent.
            hint_sent=True if req.reasoning_off else None,
            # Characters, because `eval_count` does not split thinking from
            # answer; `reasoning_tokens` stays None rather than a guess.
            reasoning_chars=len(message.get("thinking") or ""),
        )

    def _empty_answer_reason(self, payload: dict, message: dict, req: ChatRequest, finish) -> str:
        """Why there is no answer, in the words that name the fix.

        Two different faults arrive as the same empty string, and telling them
        apart is the whole point of this method - "raise the budget" is a wrong
        instruction for the second one, not merely a vague one:

        * `done_reason: "length"` - the model hit `num_predict` and stopped
          mid-thought. Measured 2026-09-03, `qwen3.5:4b` at `num_predict: 200`
          did this on 4 runs out of 4, spending every one of the 200 tokens in
          `message.thinking` and emitting no `content` at all. Nothing is
          broken: it never got out of its own preamble. The number to change is
          `max_output_tokens`, so that number is what the message carries.
        * anything else - the model finished of its own accord and wrote
          nothing. A larger budget would not have helped and the message must
          not say it would.
        """
        model = payload.get("model") or req.model
        if finish == "length":
            thought = len((message.get("thinking") or "").strip())
            where = (
                f", all of it on {thought} characters of `thinking`," if thought else ""
            )
            return (
                f"ollama's {model!r} spent its whole {req.max_output_tokens}-token answer "
                f"budget{where} and wrote no answer at all (done_reason={finish!r}); this is "
                f"a budget too small for this model rather than a model that is broken - "
                f"raise max_output_tokens"
            )
        return (
            f"ollama returned an empty answer for {model!r} "
            f"(done_reason={finish!r}); nothing was generated to store"
        )

    def _json(self, response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise base.BadResponse(f"ollama at {self.host} did not answer JSON: {exc}") from None
        if not isinstance(payload, dict):
            raise base.BadResponse(f"ollama at {self.host} answered {type(payload).__name__}, not an object")
        return payload

    # --- failures, translated -----------------------------------------------------------

    def _unreachable(self, exc: httpx2.HTTPError) -> base.LlmError:
        """A transport failure: nothing on that port sent anything back.

        `NothingAnswered` and not plain `Unreachable`, which `_raise_for_status`
        still raises for an HTTP 500 - both are retryable and every caller sees
        no change, but only this one means the port is silent, and that is the
        signal the install offer is gated on (ADR-017).
        """
        return base.NothingAnswered(f"ollama at {self.host} did not answer: {exc}")

    def _error_text(self, response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:400]
        if isinstance(payload, dict) and payload.get("error"):
            return str(payload["error"])
        return response.text[:400]

    def _raise_for_status(self, response, *, model: str | None = None) -> None:
        status = response.status_code
        if status < 400:
            return
        detail = self._error_text(response)

        if status == 404:
            # "model 'x' not found" - one pull away from working, so say which.
            name = model or self.model
            raise base.ModelNotFound(
                f"ollama has no model {name!r}: {detail} (run `ollama pull {name}`)",
                model=name,
            )
        if status >= 500:
            # A dead or unstartable model runner. Transient: the card frees up.
            raise base.Unreachable(f"ollama at {self.host} failed with HTTP {status}: {detail}")
        raise base.BadResponse(f"ollama refused the request (HTTP {status}): {detail}")
