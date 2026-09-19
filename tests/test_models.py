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
