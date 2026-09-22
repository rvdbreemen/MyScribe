"""The weights this installation downloads for itself (TASK-040.05).

No test here reaches the network: `fetch_file` is the seam. What they pin down
is the rule around it - a file that does not match its pin never survives, a
half-written one never looks finished, and a failure says which of the three
kinds it was, because three different things have to be done about them.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import email.message
import http.server
import json
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribe import models


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


BODIES = {"config.yaml": b"pipeline: {}\n", "weights.bin": b"not really weights"}


@pytest.fixture
def one_model(monkeypatch):
    """A two-file open model, pinned to the bytes the fake serves."""
    model = models.Model(
        repo="demo/model",
        revision="a" * 40,
        files={name: {"sha256": sha(body), "size": len(body)} for name, body in BODIES.items()},
        license="mit",
        credit="Demo model, MIT.",
        gated=False,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {model.repo: model})
    return model


def serve(*, corrupt: set[str] = frozenset(), seen: list | None = None, raises=None):
    def fake(model, rel, dest, token, on_bytes):
        if raises is not None:
            raise raises
        if seen is not None:
            seen.append(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"tampered" if rel in corrupt else BODIES[rel])
        on_bytes(1)

    return fake


def test_what_is_missing_is_fetched_and_verified(tmp_path, one_model, monkeypatch):
    monkeypatch.setattr(models, "fetch_file", serve())

    assert models.ensure(where=tmp_path) == ["demo/model"]
    assert models.present(one_model, where=tmp_path)


def test_a_file_that_does_not_match_its_pin_is_deleted_not_kept(tmp_path, one_model, monkeypatch):
    """On a machine about to go offline this is the only copy there will be,
    and a wrong one is worse than a missing one: nothing looks for it again."""
    monkeypatch.setattr(models, "fetch_file", serve(corrupt={"weights.bin"}))

    with pytest.raises(models.ModelError) as exc:
        models.ensure(where=tmp_path)

    assert exc.value.reason == "mismatch"
    assert not (tmp_path / one_model.folder / "weights.bin").exists()


def test_what_is_already_here_is_not_downloaded_again(tmp_path, one_model, monkeypatch):
    """A user who fetched the weights by hand must not be made to do it twice."""
    seen: list = []
    monkeypatch.setattr(models, "fetch_file", serve(seen=seen))
    models.ensure(where=tmp_path)
    before = len(seen)

    assert models.ensure(where=tmp_path) == []
    assert len(seen) == before


def test_a_gated_model_without_a_token_says_so_before_asking_the_hub(tmp_path, monkeypatch):
    """The pyannote case, and the reason nothing gated is redistributed: it is
    the user's own token and the conditions they accepted."""
    gated = models.Model(
        repo="pyannote/demo", revision="b" * 40,
        files={"config.yaml": {"sha256": sha(b"x"), "size": 1}},
        license="cc-by-4.0", credit="c", gated=True,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {gated.repo: gated})
    monkeypatch.setattr(models, "fetch_file", serve(raises=AssertionError("the hub must not be asked")))
    # This machine has a real token in .env, and `token=None` means "use the
    # default" rather than "no token" - so the absence has to be arranged.
    monkeypatch.setattr(models, "default_token", lambda: None)

    with pytest.raises(models.ModelError) as exc:
        models.ensure(where=tmp_path, token=None)

    assert exc.value.reason == "token"
    assert "hf.co/pyannote/demo" in str(exc.value)


def test_the_default_token_includes_the_one_saved_in_settings(tmp_path, monkeypatch, library_db_unstubbed):
    """`python -m scribe.models --fetch` is the command the doctor's message
    tells the user to run *after* saving the token in Settings, and it read
    the environment only - so it answered "no Hugging Face token is set" at
    the token they had just saved (TASK-089.04)."""
    from scribe import credentials, db, paths

    database = tmp_path / "myscribe.db"
    conn = db.connect(database)
    db.migrate(conn)
    conn.execute("INSERT INTO setting(key, value) VALUES ('hf_token', 'saved-in-settings')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(paths, "DB_PATH", database)
    for name in credentials.HUGGINGFACE.env_vars + credentials.HUGGINGFACE.legacy_env_vars:
        monkeypatch.delenv(name, raising=False)

    assert models.default_token() == "saved-in-settings"


def test_no_token_anywhere_is_none_rather_than_an_empty_string(tmp_path, monkeypatch, library_db_unstubbed):
    """`token=None` means "use the default"; the default being "" would send
    Hugging Face an empty token, which it answers with 401 instead of serving
    the public copy anonymously."""
    from scribe import credentials, paths

    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "no-library" / "myscribe.db")
    for name in credentials.HUGGINGFACE.env_vars + credentials.HUGGINGFACE.legacy_env_vars:
        monkeypatch.delenv(name, raising=False)

    assert models.default_token() is None


def test_progress_is_reported_as_bytes_arrive(tmp_path, one_model, monkeypatch):
    """A 1.6 GB download has to look like something happening."""
    seen: list = []
    monkeypatch.setattr(models, "fetch_file", serve())

    models.ensure(where=tmp_path, on_progress=lambda repo, done, total: seen.append((repo, done, total)))

    assert seen and all(t == one_model.bytes_total for _, _, t in seen)
    assert seen[-1][1] == one_model.bytes_total


def test_the_credit_lands_beside_the_weights(tmp_path, one_model, monkeypatch):
    monkeypatch.setattr(models, "fetch_file", serve())

    models.ensure(where=tmp_path)

    text = (tmp_path / one_model.folder / "LICENCE-AND-CREDIT.txt").read_text(encoding="utf-8")
    assert "demo/model" in text and one_model.revision in text


def test_pyannote_lands_where_the_diarize_stage_already_looks():
    """`diarize.local_weights_dir()` is MODELS_DIR/pyannote and is tried before
    the Hub, so a fetched pipeline is simply found."""
    from scribe.stages import diarize

    model = models.catalogue()[models.DIARIZE]
    assert models.root() / model.folder == diarize.local_weights_dir()


def test_the_shipped_pins_are_pins():
    """A revision that is not a commit sha is a moving target, and a file
    without a digest is not pinned at all."""
    raw = json.loads((Path(models.HERE) / "models.json").read_text(encoding="utf-8"))
    specs = {k: v for k, v in raw.items() if not k.startswith("_")}

    assert specs
    for name, spec in specs.items():
        assert len(spec["revision"]) == 40, f"{name}: not a full commit sha"
        assert spec["license"] and spec["credit"], f"{name}: redistribution needs a licence and credit"
        for rel, pin in spec["files"].items():
            assert len(pin["sha256"]) == 64, f"{name}/{rel}: no sha256"


def test_the_status_view_says_what_is_missing_and_how_big(tmp_path, one_model):
    rows = models.status(where=tmp_path)

    assert rows[0]["repo"] == "demo/model" and rows[0]["here"] is False
    assert rows[0]["bytes"] == one_model.bytes_total


def test_progress_never_goes_backwards(tmp_path, one_model, monkeypatch):
    """Found by watching a real 33 MB fetch print 84.4% and then 83.2%: the
    first version added each chunk to the *file's* baseline instead of
    accumulating within it. A bar that goes backwards reads as a restart."""
    def chunked(model, rel, dest, token, on_bytes):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(BODIES[rel])
        for _ in range(4):  # four chunks of the same file
            on_bytes(max(1, len(BODIES[rel]) // 4))

    monkeypatch.setattr(models, "fetch_file", chunked)
    seen: list[int] = []

    models.ensure(where=tmp_path, on_progress=lambda repo, done, total: seen.append(done))

    assert seen == sorted(seen), f"progress went backwards: {seen}"


# --- a copy the hub already holds (found on the Mac) -------------------------------


def hub_copy(tmp_path: Path, model: models.Model, *, revision: str | None = None) -> Path:
    """A huggingface_hub cache laid out the way the library lays it out."""
    base = (
        tmp_path / "hub" / f"models--{model.repo.replace('/', '--')}"
        / "snapshots" / (revision or model.revision)
    )
    for rel, pin in model.files.items():
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * int(pin["size"]))
    return base


def test_a_model_the_hub_cache_already_holds_is_not_missing(tmp_path, one_model, monkeypatch):
    """Found by running the doctor on the Mac: it said "1.6 GB still to
    download" on a machine that had just transcribed with that model. The
    weights were in the hub cache rather than under MODELS_DIR, and a download
    this app already has is not a download to ask for again."""
    from scribe import doctor

    hub_copy(tmp_path, one_model)
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")

    assert models.present(one_model) is True
    assert models.missing() == []


def test_the_cache_only_counts_at_the_pinned_revision(tmp_path, one_model, monkeypatch):
    """Another revision of the same repo is a different model, and the pin is
    the whole point of pinning."""
    from scribe import doctor

    hub_copy(tmp_path, one_model, revision="b" * 40)
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")

    assert models.present(one_model) is False


def test_assembling_a_payload_does_not_count_a_cache_somewhere_else(tmp_path, one_model, monkeypatch):
    """`--dest` asks "is it *here*", which a copy in a user cache does not
    answer: a build has to put the files in the artifact."""
    from scribe import doctor

    hub_copy(tmp_path, one_model)
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")

    assert models.present(one_model, where=tmp_path / "payload") is False


# --- who loads what, and at which quality (TASK-089.16) ---------------------------


def test_the_catalogue_pins_the_ids_the_loaders_actually_request():
    """The alias is the whole point of the pin: weights downloaded under an id
    no loader asks for are weights nothing ever reads.

    faster-whisper's own table and `mlx_backend.repo_for` are asked here rather
    than copied, so a library upgrade that re-points a name is a red test and
    not a silent second download inside the first transcription.
    """
    from faster_whisper.utils import _MODELS

    from scribe.stages import mlx_backend

    for model in models.catalogue().values():
        if model.alias is None:
            continue
        if model.backends and "mlx" in model.backends:
            assert mlx_backend.repo_for(model.alias) == model.repo
        else:
            assert _MODELS[model.alias] == model.repo


def test_every_transcription_entry_says_which_backend_and_which_tier():
    """A row without both is a row nothing can decide about: it would be
    offered on every platform at every setting, which is the fault this task
    exists for."""
    for model in models.catalogue().values():
        if model.repo == models.DIARIZE:
            assert model.backends is None and model.tier is None
            continue
        assert model.backends, f"{model.repo}: no backends"
        assert model.tier in ("turbo", "max"), f"{model.repo}: no tier"
        assert model.alias, f"{model.repo}: no loader alias"


def test_the_backend_a_plan_can_answer_without_torch_has_one_spelling():
    """`not-mlx` is what a plan answers off Apple Silicon rather than pay a
    torch import to learn whether it is cuda or cpu; both ends spell it once."""
    assert models.NOT_MLX == "not-mlx"


def test_the_diarization_pipeline_loads_on_every_backend():
    pipeline = models.catalogue()[models.DIARIZE]
    for backend in ("cuda", "cpu", "mlx", "not-mlx"):
        assert models.loads_here(pipeline, backend) is True


@pytest.mark.parametrize("backend", ["cuda", "cpu", "not-mlx"])
def test_the_mlx_conversions_are_not_a_download_off_apple_silicon(backend):
    """`not-mlx` is the cheap answer a plan gets without importing torch, and
    it has to exclude the MLX rows exactly as `cuda` and `cpu` do."""
    wanted = {m.repo for m in models.wanted_here(backend=backend, tier="turbo")}

    assert not any(repo.startswith("mlx-community/") for repo in wanted)
    assert models.DIARIZE in wanted


def test_the_tier_decides_which_whisper_repo_is_wanted():
    turbo = {m.repo for m in models.wanted_here(backend="cuda", tier="turbo")}
    largest = {m.repo for m in models.wanted_here(backend="cuda", tier="max")}

    assert turbo != largest
    assert len(turbo) == len(largest) == 2, "one Whisper repo and the pipeline"
    assert not (turbo & largest) - {models.DIARIZE}


def test_on_apple_silicon_the_mlx_conversion_is_the_one_wanted():
    wanted = {m.repo for m in models.wanted_here(backend="mlx", tier="turbo")}

    assert all(repo.startswith("mlx-community/") or repo == models.DIARIZE for repo in wanted)
    assert len(wanted) == 2


def test_the_pinned_local_folder_is_offered_to_the_loader_only_when_it_is_whole(tmp_path, monkeypatch):
    """What `transcribe.load_model` and the MLX backend ask. A half-written
    folder is not an answer: faster-whisper would open it and fail, where the
    bare name still resolves through the hub cache."""
    monkeypatch.setattr(models, "root", lambda: tmp_path)
    entry = next(m for m in models.catalogue().values() if m.tier == "turbo" and "mlx" not in (m.backends or ()))

    assert models.local_dir(entry.alias, "cuda") is None

    folder = tmp_path / entry.folder
    for rel, pin in entry.files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * int(pin["size"]))

    assert models.local_dir(entry.alias, "cuda") == folder


def test_a_name_no_catalogue_entry_serves_has_no_local_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "root", lambda: tmp_path)

    assert models.local_dir("tiny", "cuda") is None
    assert models.local_dir(None, "cuda") is None


# --- what a download does when the connection is not perfect (TASK-089.16) --------


@contextlib.contextmanager
def hub_server(*, body: bytes = b"", send: list | None = None, status: int = 200,
               redirect_to: str | None = None, host: str = "127.0.0.1",
               ignore_range: bool = False):
    """A stand-in for the Hub, on loopback and never a real host.

    `send` is how many bytes of the requested range each successive request
    actually writes after announcing the full Content-Length - the dropped
    connection this probe exists to reproduce. It records the path, the Range
    header and *whether* an Authorization header arrived; never its value.

    `ignore_range` is the intermediary that reads the Range header and answers
    200 with the whole file anyway. It is still recorded as having arrived, so
    a test can say the server was asked and did not honour it.
    """
    log: list[dict] = []
    remaining = list(send or [])

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - the stdlib's spelling
            rng = self.headers.get("Range")
            log.append({"path": self.path, "range": rng, "auth": "Authorization" in self.headers})
            if redirect_to is not None:
                self.send_response(302)
                self.send_header("Location", redirect_to + self.path)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if status != 200:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            start = int(rng[len("bytes="):].split("-")[0]) if rng and rng.startswith("bytes=") else 0
            if ignore_range:
                start = 0
            if start and start >= len(body):
                self.send_response(416)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            chunk = body[start:]
            self.send_response(206 if start else 200)
            if start:
                self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            count = remaining.pop(0) if remaining else None
            self.wfile.write(chunk if count is None else chunk[:count])

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(base=f"http://{host}:{server.server_address[1]}", log=log)
    finally:
        server.shutdown()
        server.server_close()


WEIGHTS = bytes(range(256)) * 400
"""102 400 bytes with every position distinct, so a resume that starts at the
wrong offset produces a different file rather than the same one."""


@pytest.fixture
def served_model(monkeypatch):
    """One open model whose single file the loopback server holds."""
    model = models.Model(
        repo="demo/model",
        revision="a" * 40,
        files={"weights.bin": {"sha256": sha(WEIGHTS), "size": len(WEIGHTS)}},
        license="mit",
        credit="Demo model, MIT.",
        gated=False,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {model.repo: model})
    monkeypatch.setattr(models, "default_token", lambda: None)
    return model


def test_a_connection_that_drops_mid_file_is_offline_and_not_tampering(tmp_path, served_model, monkeypatch):
    """Found with this probe: a dropped connection was reported as "does not
    match its pin ... it was deleted rather than used", which reads as somebody
    having interfered with the download. It is the network."""
    with hub_server(body=WEIGHTS, send=[64, 64, 64, 64]) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        with pytest.raises(models.ModelError) as exc:
            models.ensure(where=tmp_path)

    assert exc.value.reason == "offline", str(exc.value)
    assert "match its pin" not in str(exc.value)


def test_a_dropped_download_resumes_where_it_stopped(tmp_path, served_model, monkeypatch):
    """The second request asks for the rest by Range rather than for the whole
    file again."""
    with hub_server(body=WEIGHTS, send=[64]) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        assert models.ensure(where=tmp_path) == ["demo/model"]

        assert [row["range"] for row in hub.log] == [None, "bytes=64-"]
    assert (tmp_path / served_model.folder / "weights.bin").read_bytes() == WEIGHTS


def test_a_server_that_ignores_the_range_header_does_not_make_one_file_of_two(
    tmp_path, served_model, monkeypatch
):
    """An intermediary that reads `Range` and answers 200 with the whole body.

    Appended to what is already in the `.part`, that reaches the announced
    length out of too many bytes, so the pin check rejects it with "it was
    deleted rather than used" - the sentence that reads as tampering and is
    what a resume is supposed to stop producing. The answer is to write rather
    than append whenever the server did not say 206.
    """
    dest = tmp_path / "weights.bin"
    with hub_server(body=WEIGHTS, send=[64], ignore_range=True) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        models.fetch_file(served_model, "weights.bin", dest, None, lambda n: None)

        assert [row["range"] for row in hub.log] == [None, "bytes=64-"], "asked, and not honoured"
    assert dest.read_bytes() == WEIGHTS


def test_a_connection_that_keeps_dropping_gives_up_after_three_tries(tmp_path, served_model, monkeypatch):
    with hub_server(body=WEIGHTS, send=[64, 64, 64, 64, 64]) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        with pytest.raises(models.ModelError):
            models.ensure(where=tmp_path)

        assert len(hub.log) == 3, "three attempts, not an endless retry"


def test_a_token_the_hub_does_not_know_is_not_a_gate_that_was_not_accepted(tmp_path, served_model, monkeypatch):
    """The fixes differ, so the sentences differ: a 401 token is replaced, a
    403 account has conditions to accept."""
    said = {}
    for code in (401, 403):
        with hub_server(status=code) as hub:
            monkeypatch.setattr(models, "HUB", hub.base)
            with pytest.raises(models.ModelError) as exc:
                models.fetch_file(served_model, "weights.bin", tmp_path / "w.bin", "t", lambda n: None)
            assert exc.value.reason == "token"
            said[code] = str(exc.value)

    assert said[401] != said[403]
    assert "not accepted" in said[401] and f"hf.co/{served_model.repo}" in said[403]


# --- the token goes to the gated repo and stays on its host (TASK-089.16) ---------


def test_a_public_repo_is_never_sent_the_token(tmp_path, served_model, monkeypatch):
    """A token is a credential for the one repository whose conditions this
    account accepted; every other repository gets an anonymous request."""
    with hub_server(body=WEIGHTS) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        models.fetch_file(served_model, "weights.bin", tmp_path / "w.bin", "a-token", lambda n: None)

        assert [row["auth"] for row in hub.log] == [False]


def test_the_gated_repo_is_sent_the_token(tmp_path, monkeypatch):
    gated = models.Model(
        repo="pyannote/demo", revision="b" * 40,
        files={"weights.bin": {"sha256": sha(WEIGHTS), "size": len(WEIGHTS)}},
        license="cc-by-4.0", credit="c", gated=True,
    )
    with hub_server(body=WEIGHTS) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        models.fetch_file(gated, "weights.bin", tmp_path / "w.bin", "a-token", lambda n: None)

        assert [row["auth"] for row in hub.log] == [True]


def test_the_token_is_dropped_when_a_redirect_changes_host(tmp_path, monkeypatch):
    """urllib copies a request's headers across a redirect, so without this the
    Bearer token follows the Location header to whatever host it names."""
    gated = models.Model(
        repo="pyannote/demo", revision="b" * 40,
        files={"weights.bin": {"sha256": sha(WEIGHTS), "size": len(WEIGHTS)}},
        license="cc-by-4.0", credit="c", gated=True,
    )
    with hub_server(body=WEIGHTS, host="localhost") as elsewhere:
        with hub_server(redirect_to=elsewhere.base) as hub:
            monkeypatch.setattr(models, "HUB", hub.base)

            models.fetch_file(gated, "weights.bin", tmp_path / "w.bin", "a-token", lambda n: None)

            assert [row["auth"] for row in hub.log] == [True], "the repository it was meant for"
        assert [row["auth"] for row in elsewhere.log] == [False], "and nowhere else"


def test_the_token_is_dropped_when_a_redirect_drops_the_s_off_https():
    """The same host over plain http is not the same place for a credential.

    `netloc` alone says it is - https://huggingface.co and http://huggingface.co
    are one host - so a redirect that only downgrades the scheme would carry
    the Bearer token out in the clear, which is the one way of losing it that
    costs the credential itself rather than a download.

    Asked of the handler directly: reproducing it end to end would need a TLS
    server, and what is being pinned is which redirects count as elsewhere.
    """
    handler = models.DropAuthAcrossHosts()
    request = urllib.request.Request(
        "https://huggingface.co/pyannote/demo/resolve/main/config.yaml",
        headers={"Authorization": "Bearer never-a-real-token"},
    )

    followed = handler.redirect_request(
        request, None, 302, "Found", email.message.Message(),
        "http://huggingface.co/pyannote/demo/resolve/main/config.yaml",
    )

    assert "Authorization" not in followed.headers
    assert "Authorization" not in followed.unredirected_hdrs


# --- room on the disk before the first byte (TASK-089.16) -------------------------


def test_too_little_room_refuses_with_both_numbers_and_asks_for_nothing(tmp_path, monkeypatch):
    big = models.Model(
        repo="demo/big", revision="c" * 40,
        files={"weights.bin": {"sha256": sha(b"x"), "size": 2_000_000_000}},
        license="mit", credit="c", gated=False,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {big.repo: big})
    monkeypatch.setattr(models, "default_token", lambda: None)
    seen: list = []
    monkeypatch.setattr(models, "fetch_file", serve(seen=seen))
    asked_about: list = []

    def usage(path):
        asked_about.append(Path(path))
        return SimpleNamespace(total=10 ** 9, used=10 ** 9 - 5_000_000, free=5_000_000)

    monkeypatch.setattr(models.shutil, "disk_usage", usage)

    with pytest.raises(models.ModelError) as exc:
        models.ensure(where=tmp_path)

    assert exc.value.reason == "disk"
    assert seen == [], "nothing is requested when there is nowhere to put it"
    assert "2.0 GB" in str(exc.value), "what it needs"
    assert "5 MB" in str(exc.value), "and what there is"
    assert asked_about == [tmp_path], "the volume the files land on, not this process's home"


def test_the_disk_filling_up_mid_download_is_not_reported_as_offline(tmp_path, served_model, monkeypatch):
    """"could not be downloaded" sends somebody to look at their connection.
    ENOSPC is an OSError like any other, so that is what it said."""
    real_open = Path.open

    class Full:
        def write(self, _block):
            raise OSError(errno.ENOSPC, "No space left on device")

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def refuse(self, *args, **kwargs):
        return Full() if self.suffix == ".part" else real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse)
    with hub_server(body=WEIGHTS) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        with pytest.raises(models.ModelError) as exc:
            models.fetch_file(served_model, "weights.bin", tmp_path / "w.bin", None, lambda n: None)

    assert exc.value.reason == "disk"


# --- the copy that is already here (TASK-089.16) ----------------------------------


def test_a_copy_the_hub_cache_holds_is_not_downloaded_again(tmp_path, one_model, monkeypatch):
    """`status()` called it present and `ensure()` fetched it anyway: ensure
    asked "is it in MODELS_DIR" where the status line asked "can this machine
    use it". Two answers to one question, and the download was the loud one."""
    from scribe import doctor

    hub_copy(tmp_path, one_model)
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")
    monkeypatch.setattr(models, "root", lambda: tmp_path / "models")
    monkeypatch.setattr(models, "default_token", lambda: None)
    seen: list = []
    monkeypatch.setattr(models, "fetch_file", serve(seen=seen))

    assert models.ensure() == []
    assert seen == []


def test_a_named_destination_still_means_that_folder_only(tmp_path, one_model, monkeypatch):
    """`--dest` assembles a payload, and a copy in this machine's user cache
    does not put a file in the artifact."""
    from scribe import doctor

    hub_copy(tmp_path, one_model)
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")
    monkeypatch.setattr(models, "default_token", lambda: None)
    seen: list = []
    monkeypatch.setattr(models, "fetch_file", serve(seen=seen))

    assert models.ensure(where=tmp_path / "payload") == ["demo/model"]
    assert seen


def test_the_gated_entry_refuses_before_any_public_byte_is_requested(tmp_path, monkeypatch):
    """Walked in catalogue order the public weights arrived first and the gated
    pipeline refused afterwards - a download somebody waited through for a
    sitting that could not finish."""
    public = models.Model(
        repo="demo/model", revision="a" * 40,
        files={name: {"sha256": sha(body), "size": len(body)} for name, body in BODIES.items()},
        license="mit", credit="c", gated=False,
    )
    gated = models.Model(
        repo="pyannote/demo", revision="b" * 40,
        files={"config.yaml": {"sha256": sha(BODIES["config.yaml"]), "size": len(BODIES["config.yaml"])}},
        license="cc-by-4.0", credit="c", gated=True,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {public.repo: public, gated.repo: gated})
    monkeypatch.setattr(models, "default_token", lambda: None)
    seen: list = []
    monkeypatch.setattr(models, "fetch_file", serve(seen=seen))

    with pytest.raises(models.ModelError) as exc:
        models.ensure(where=tmp_path, token=None)

    assert exc.value.reason == "token" and "pyannote/demo" in str(exc.value)
    assert seen == [], "and not after the public weights had already been fetched"


def test_a_token_the_hub_refuses_costs_no_public_download_either(tmp_path, monkeypatch):
    """The refusal above is free, so the order of the walk does not show in it.

    A token that exists and is not accepted is the case where it does: that
    refusal arrives at download time, and in catalogue order the public
    gigabyte is spent first and the sitting ends in the same error anyway.
    """
    public = models.Model(
        repo="demo/model", revision="a" * 40,
        files={name: {"sha256": sha(body), "size": len(body)} for name, body in BODIES.items()},
        license="mit", credit="c", gated=False,
    )
    gated = models.Model(
        repo="pyannote/demo", revision="b" * 40,
        files={"config.yaml": {"sha256": sha(BODIES["config.yaml"]), "size": len(BODIES["config.yaml"])}},
        license="cc-by-4.0", credit="c", gated=True,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {public.repo: public, gated.repo: gated})
    seen: list = []

    def fetch(model, rel, dest, token, on_bytes):
        seen.append(model.repo)
        if model.gated:
            raise models.ModelError(f"{model.repo}: this token was not accepted", reason="token")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(BODIES[rel])

    monkeypatch.setattr(models, "fetch_file", fetch)

    with pytest.raises(models.ModelError) as exc:
        models.ensure(where=tmp_path, token="a-token-the-hub-refuses")

    assert exc.value.reason == "token"
    assert set(seen) == {gated.repo}, "the public weights were never asked for"


def test_a_part_file_from_an_older_pin_is_thrown_away_and_the_file_starts_over(
    tmp_path, served_model, monkeypatch
):
    """Bytes are only a prefix of the file they were fetched for.

    A `.part` named per file alone outlived a re-pin, and the next run appended
    the new revision's bytes to the old one's: the right length out of the
    wrong content, which fails its digest with "does not match its pin ... it
    was deleted rather than used" - the sentence that reads as tampering.
    Shorter leftovers are the dangerous half; a longer one only ever cost a
    round trip. The leftover planted here is the one an upgrade really meets:
    the name this app used before the revision was part of it.
    """
    dest = tmp_path / "weights.bin"
    dest.parent.mkdir(parents=True, exist_ok=True)
    stale = dest.with_name(f"{dest.name}.part")
    stale.write_bytes(b"z" * 64)

    with hub_server(body=WEIGHTS) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        models.fetch_file(served_model, "weights.bin", dest, None, lambda n: None)

        assert [row["range"] for row in hub.log] == [None], "from the beginning, not from 64"
    assert dest.read_bytes() == WEIGHTS
    assert list(tmp_path.glob("*.part")) == [], "and no gigabyte nobody will look at again"


def test_a_connection_that_resets_keeps_what_had_already_arrived(tmp_path, served_model, monkeypatch):
    """A reset and a timeout are OSErrors, and both used to delete the `.part`
    on the way out - so the resume this function promises worked only for a
    body that ended early without raising, which is not the drop a long
    download over a flaky line is most likely to hit."""
    real_open = Path.open
    opened = {"count": 0}

    class Drops:
        """A file that takes two blocks and then loses the connection."""

        def __init__(self, handle):
            self._handle, self._left = handle, 2

        def write(self, block):
            if not self._left:
                raise ConnectionResetError(errno.ECONNRESET, "forcibly closed by the remote host")
            self._left -= 1
            return self._handle.write(block)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            self._handle.close()
            return False

    def refuse(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.name.endswith(".part"):
            opened["count"] += 1
            if opened["count"] == 1:
                return Drops(handle)
        return handle

    monkeypatch.setattr(models, "CHUNK", 4096)
    monkeypatch.setattr(Path, "open", refuse)
    dest = tmp_path / "weights.bin"
    with hub_server(body=WEIGHTS) as hub:
        monkeypatch.setattr(models, "HUB", hub.base)

        with pytest.raises(models.ModelError) as exc:
            models.fetch_file(served_model, "weights.bin", dest, None, lambda n: None)
        assert exc.value.reason == "offline"

        models.fetch_file(served_model, "weights.bin", dest, None, lambda n: None)

        assert hub.log[1]["range"], "the next run asks for the rest, not for all of it"
    assert dest.read_bytes() == WEIGHTS


def test_asking_which_backend_loads_here_never_imports_torch(monkeypatch):
    """ADR-001, Must Not: `doctor.check_models` reads this and is in
    WEB_SAFE_CHECKS, so a settings page render would import torch every time on
    the one platform where `mlx_available()` is true. `transcription_backend()`
    asks CUDA first and `cuda_available()` is that import."""
    from scribe import accel

    def refuse():
        raise AssertionError("the web process must not import torch to answer this")

    monkeypatch.setattr(accel, "transcription_backend", refuse)
    monkeypatch.setattr(accel, "cuda_available", refuse)

    monkeypatch.setattr(accel, "mlx_available", lambda: True)
    assert models.backend_here() == "mlx"

    monkeypatch.setattr(accel, "mlx_available", lambda: False)
    assert models.backend_here() == models.NOT_MLX


def test_the_credit_is_written_beside_a_copy_somebody_put_there_by_hand(tmp_path, one_model, monkeypatch):
    """The attribution belongs beside the weights, not beside the download: a
    machine whose weights were copied in by hand has the same files under the
    same licence."""
    monkeypatch.setattr(models, "default_token", lambda: None)
    monkeypatch.setattr(models, "fetch_file", serve(raises=AssertionError("nothing to fetch")))
    for name, body in BODIES.items():
        path = tmp_path / one_model.folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    assert models.ensure(where=tmp_path) == []

    assert (tmp_path / one_model.folder / "LICENCE-AND-CREDIT.txt").exists()


def test_a_copy_only_the_hub_cache_holds_gets_no_credit_file_here(tmp_path, one_model, monkeypatch):
    """This app placed no copy there, so there is nothing of its doing to
    attribute - and writing a licence beside an empty folder would say there
    were weights in it."""
    from scribe import doctor

    hub_copy(tmp_path, one_model)
    monkeypatch.setattr(doctor, "hf_cache_dir", lambda: tmp_path / "hub")
    monkeypatch.setattr(models, "root", lambda: tmp_path / "models")
    monkeypatch.setattr(models, "default_token", lambda: None)
    monkeypatch.setattr(models, "fetch_file", serve(raises=AssertionError("nothing to fetch")))

    assert models.ensure() == []

    assert not (tmp_path / "models" / one_model.folder / "LICENCE-AND-CREDIT.txt").exists()


# --- assembling a payload is not the same question (TASK-089.16) ------------------


def test_a_payload_is_assembled_from_every_repository(tmp_path, monkeypatch):
    """`--dest` builds an artifact for a platform that is not necessarily this
    one, so it names the whole catalogue.

    `ensure(None)` has meant "what this machine loads" since the platform
    filter landed, and a Windows build assembled without the Apple weights
    would first be noticed by the Mac that unpacked it.
    """
    asked: list = []

    def ensure(wanted=None, *, where=None, on_progress=None, **_ignored):
        asked.append((wanted, where))
        return []

    monkeypatch.setattr(models, "ensure", ensure)

    assert models.main(["--fetch", "--dest", str(tmp_path)]) == 0

    named, where = asked[0]
    assert set(named) == set(models.catalogue()), "every repository, not only this platform's"
    assert where == tmp_path


def test_a_fetch_with_no_destination_is_still_about_this_machine(monkeypatch):
    """The other half of the same question, so that naming the catalogue for a
    payload cannot quietly become naming it always."""
    asked: list = []
    monkeypatch.setattr(models, "ensure", lambda wanted=None, **_kw: asked.append(wanted) or [])

    assert models.main(["--fetch"]) == 0

    assert asked == [None]


def test_the_payload_script_asks_for_every_repository_too(tmp_path, monkeypatch):
    """packaging/fetch_models.py is a second door onto the same question, and
    it is the one whose mistake costs a build rather than a test run."""
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
    import fetch_models

    asked: list = []
    monkeypatch.setattr(models, "ensure", lambda wanted=None, **kw: asked.append(wanted) or [])

    fetch_models.ensure(tmp_path)

    assert set(asked[0]) == set(models.catalogue())
