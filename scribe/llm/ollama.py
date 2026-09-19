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
"""Seconds for one completion. Generous where the cloud providers get 60: a
local 9B model on a busy card takes tens of seconds to load before it emits a
first token, and killing it at 60 s would turn a slow answer into no answer."""

PROBE_TIMEOUT = 2.0
"""The bound on `available()`. See the note on that method - it is the one
sanctioned network call in a method the base class says makes none."""

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
    so both providers' transport seams read the same way."""
    return httpx2.Client(base_url=base_url, timeout=timeout)


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

        `conn` is accepted and unused so `llm.get_provider(name, conn)` builds
        every provider the same way.
        """
        self.conn = conn
        self.host = _this_machine_only(host).rstrip("/")
        self.model = model or self.default_model
        self.num_ctx = DEFAULT_NUM_CTX if num_ctx is None else num_ctx
        self._num_ctx_is_mine = num_ctx is None
        self._windows: dict[str, int] = {}
        self.client_factory = client_factory or default_client_factory
        self.timeout = timeout
        self._client: Any | None = None

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

    def client(self):
        if self._client is None:
            self._client = self.client_factory(base_url=self.host, timeout=self.timeout)
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
        """
        try:
            names = self.models()
        except base.Unreachable:
            return False, f"Ollama is not running at {self.host} (start it, then reload)"
        except base.LlmError as exc:
            return False, f"Ollama at {self.host} answered oddly: {exc}"

        if self.model not in names:
            pulled = ", ".join(names) if names else "none"
            return False, (
                f"Ollama is running at {self.host} but {self.model!r} is not pulled: "
                f"run `ollama pull {self.model}` (pulled: {pulled})"
            )
        return True, f"Ollama at {self.host} with {self.model} ready"

    def models(self) -> list[str]:
        """The model names this daemon has pulled, sorted."""
        try:
            response = self.client().get("/api/tags", timeout=PROBE_TIMEOUT)
        except httpx2.HTTPError as exc:
            raise self._unreachable(exc) from None
        self._raise_for_status(response)
        payload = self._json(response)
        return sorted(str(m.get("name")) for m in payload.get("models", []) if m.get("name"))

    # --- one completion ----------------------------------------------------------

    def complete(self, req: ChatRequest) -> ChatResponse:
        self._refuse_a_prompt_that_cannot_fit(req)
        try:
            response = self.client().post("/api/chat", json=self._body(req))
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
        return base.Unreachable(f"ollama at {self.host} did not answer: {exc}")

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
