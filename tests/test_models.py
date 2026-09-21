"""The weights this installation downloads for itself (TASK-040.05).

No test here reaches the network: `fetch_file` is the seam. What they pin down
is the rule around it - a file that does not match its pin never survives, a
half-written one never looks finished, and a failure says which of the three
kinds it was, because three different things have to be done about them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
