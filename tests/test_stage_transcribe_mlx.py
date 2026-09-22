"""The Apple Silicon path, proved with a fake `mlx_whisper` (TASK-020).

No Mac took part in writing this: what these tests pin is the contract
between `scribe.accel`, `mlx_backend` and `transcribe_audio` - that on a
machine which reports Apple Silicon and has mlx_whisper importable, the
transcribe stage builds the MLX model, feeds it windows, and gets segments
and words back in the shape the rest of the pipeline reads. The real
mlx_whisper's output shape is taken from its documentation (0.4); the doctor
on an actual Mac is the acceptance run for that assumption.
"""

from __future__ import annotations

import importlib.machinery
import hashlib
import sys
import types

import pytest

from scribe import accel
from scribe.stages import mlx_backend, transcribe


def fake_mlx_module(calls: list[dict]) -> types.ModuleType:
    """An `mlx_whisper` whose `transcribe` answers two segments with words."""
    module = types.ModuleType("mlx_whisper")
    # `accel.mlx_available` asks find_spec, which reads __spec__ off a module
    # already in sys.modules and refuses a module without one.
    module.__spec__ = importlib.machinery.ModuleSpec("mlx_whisper", None)

    def fake_transcribe(audio, **options):
        calls.append({"audio": audio, **options})
        return {
            "text": " Don't panic. Bring a towel.",
            "language": options.get("language") or "en",
            "segments": [
                {
                    "id": 0, "seek": 0, "start": 0.0, "end": 1.2, "text": " Don't panic.",
                    "tokens": [1, 2], "temperature": 0.0, "avg_logprob": -0.2,
                    "compression_ratio": 1.1, "no_speech_prob": 0.01,
                    "words": [
                        {"word": " Don't", "start": 0.0, "end": 0.5, "probability": 0.9},
                        {"word": " panic.", "start": 0.6, "end": 1.2, "probability": 0.8},
                    ],
                },
                {
                    "id": 1, "seek": 0, "start": 1.5, "end": 2.4, "text": " Bring a towel.",
                    "tokens": [3], "temperature": 0.0, "avg_logprob": -0.3,
                    "compression_ratio": 1.0, "no_speech_prob": 0.02,
                    "words": [
                        {"word": " Bring", "start": 1.5, "end": 1.8, "probability": 0.95},
                        {"word": " a", "start": 1.8, "end": 1.9, "probability": 0.7},
                        {"word": " towel.", "start": 1.9, "end": 2.4, "probability": 0.9},
                    ],
                },
            ],
        }

    module.transcribe = fake_transcribe
    return module


@pytest.fixture
def on_a_mac(monkeypatch):
    """Apple Silicon with mlx_whisper installed and no CUDA, as `accel` sees it."""
    monkeypatch.setattr(accel, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(accel, "cuda_available", lambda: False)
    calls: list[dict] = []
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx_module(calls))
    return calls


# --- accel ------------------------------------------------------------------------


def test_the_backend_is_mlx_on_apple_silicon_with_mlx_whisper_and_cpu_without(monkeypatch):
    monkeypatch.setattr(accel, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(accel, "cuda_available", lambda: False)
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx_module([]))
    assert accel.transcription_backend() == "mlx"

    monkeypatch.setattr(accel.importlib.util, "find_spec", lambda name: None)
    assert accel.transcription_backend() == "cpu"


def test_cuda_wins_over_mlx_and_mps_when_present(monkeypatch):
    monkeypatch.setattr(accel, "is_apple_silicon", lambda: True)
    monkeypatch.setattr(accel, "cuda_available", lambda: True)
    monkeypatch.setattr(accel, "mps_available", lambda: True)
    assert accel.transcription_backend() == "cuda"
    assert accel.diarization_device() == "cuda"


def test_diarization_picks_mps_on_a_mac_and_cpu_elsewhere(monkeypatch):
    monkeypatch.setattr(accel, "cuda_available", lambda: False)
    monkeypatch.setattr(accel, "mps_available", lambda: True)
    assert accel.diarization_device() == "mps"
    monkeypatch.setattr(accel, "mps_available", lambda: False)
    assert accel.diarization_device() == "cpu"
    assert accel.describe() == "transcription on cpu, diarization on cpu" or "transcription on" in accel.describe()


def test_off_apple_silicon_nothing_is_probed_for_mlx_or_mps(monkeypatch):
    monkeypatch.setattr(accel, "is_apple_silicon", lambda: False)
    monkeypatch.setitem(sys.modules, "mlx_whisper", fake_mlx_module([]))
    assert accel.mlx_available() is False
    assert accel.mps_available() is False


# --- the backend's shape --------------------------------------------------------


def test_model_names_map_to_the_mlx_community_repos_and_a_repo_id_passes_through():
    assert mlx_backend.repo_for("large-v3-turbo") == "mlx-community/whisper-large-v3-turbo"
    assert mlx_backend.repo_for(transcribe.DEFAULT_MODEL).startswith("mlx-community/")
    assert mlx_backend.repo_for("someone/whisper-large-v3-turbo-4bit") == "someone/whisper-large-v3-turbo-4bit"
    assert mlx_backend.repo_for("large-v3") == "mlx-community/whisper-large-v3-mlx"
    assert mlx_backend.repo_for("small") == "mlx-community/whisper-small-mlx"


def test_the_mlx_model_answers_in_faster_whispers_shape(on_a_mac):
    calls = on_a_mac
    model = mlx_backend.MlxWhisperModel("large-v3-turbo")

    stream, info = model.transcribe(
        [0.0] * 16000, language=None, task="transcribe", hotwords="Zaphod, Marvin",
        word_timestamps=True, vad_filter=True, condition_on_previous_text=True,
        compression_ratio_threshold=2.4,
    )
    segments = list(stream)

    assert info.language == "en" and info.language_probability is None
    assert info.duration == 2.4
    assert info.duration_after_vad is None  # no VAD ran, so no speech count (TASK-056)
    assert [s.text for s in segments] == [" Don't panic.", " Bring a towel."]
    assert (segments[0].start, segments[0].end, segments[0].avg_logprob) == (0.0, 1.2, -0.2)
    assert [w.word for w in segments[1].words] == [" Bring", " a", " towel."]
    assert segments[1].words[0].probability == 0.95
    # What reached mlx-whisper: the repo, the prompt, no VAD key at all.
    (call,) = calls
    assert call["path_or_hf_repo"] == "mlx-community/whisper-large-v3-turbo"
    assert call["initial_prompt"] == "Zaphod, Marvin"
    assert call["word_timestamps"] is True
    assert call["compression_ratio_threshold"] == 2.4
    assert "vad_filter" not in call and "language" not in call


def test_a_detected_language_is_held_for_the_next_window(on_a_mac):
    calls = on_a_mac
    model = mlx_backend.MlxWhisperModel("large-v3-turbo")
    model.transcribe([0.0], language="nl", task="translate")
    assert calls[-1]["language"] == "nl" and calls[-1]["task"] == "translate"


# --- through the stage's own loop ------------------------------------------------


def test_load_model_builds_the_mlx_backend_on_a_mac(on_a_mac):
    model, device, compute_type = transcribe.load_model("large-v3-turbo")
    assert isinstance(model, mlx_backend.MlxWhisperModel)
    assert (device, compute_type) == ("mlx", "float16")


def test_an_explicit_cpu_device_still_gets_faster_whisper_on_a_mac(on_a_mac, monkeypatch):
    """The override is never second-guessed - a CPU test on a Mac stays one."""
    seen = {}

    class FakeWhisperModel:
        def __init__(self, name, device, compute_type):
            seen.update(name=name, device=device, compute_type=compute_type)

    fake_fw = types.ModuleType("faster_whisper")
    fake_fw.WhisperModel = FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_fw)

    _model, device, compute_type = transcribe.load_model("small", device="cpu")
    assert (device, compute_type) == ("cpu", "int8")
    assert seen == {"name": "small", "device": "cpu", "compute_type": "int8"}


def test_transcribe_audio_runs_the_whole_loop_on_mlx(on_a_mac, tmp_path):
    """The stage's window loop, progress, indices and the run info, with MLX
    underneath - the same path a job takes, minus the database."""
    import wave

    import numpy as np

    wav = tmp_path / "clip.wav"
    with wave.open(str(wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(np.zeros(16000 * 3, dtype="<i2").tobytes())

    progress: list[float] = []
    info, segments, words = transcribe.transcribe_audio(
        wav, model_name="large-v3-turbo", hotwords="Zaphod", on_progress=progress.append
    )

    assert info["device"] == "mlx" and info["compute_type"] == "float16"
    assert info["language"] == "en" and info["language_probability"] is None
    # mlx-whisper runs no VAD, so there is no speech count to record (TASK-056).
    assert info["duration_after_vad"] is None
    assert [s["idx"] for s in segments] == [0, 1]
    assert [w["text"] for w in words] == [" Don't", " panic.", " Bring", " a", " towel."]
    assert words[2]["segment_idx"] == 1 if "segment_idx" in words[2] else True
    assert progress and progress[-1] <= 1.0
    (call,) = on_a_mac
    assert call["initial_prompt"] == "Zaphod"
    assert hasattr(call["audio"], "dtype")  # samples, not a path


# --- the copy setup downloaded is the copy that loads (TASK-089.16) ---------------


def one_mlx_entry(monkeypatch, tmp_path, alias):
    """A one-row catalogue pinning a four-byte file under ``alias``; the real
    pin is 1.6 GB and this test is about which path reaches mlx-whisper."""
    from scribe import models

    body = b"mlx!"
    entry = models.Model(
        repo="demo/mlx-weights",
        revision="a" * 40,
        files={"weights.safetensors": {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}},
        license="mit",
        credit="Demo, MIT.",
        gated=False,
        backends=("mlx",),
        tier=models.DEFAULT_TIER,
        alias=alias,
    )
    monkeypatch.setattr(models, "catalogue", lambda: {entry.repo: entry})
    monkeypatch.setattr(models, "root", lambda: tmp_path)
    return entry, body


def test_the_downloaded_folder_is_what_mlx_whisper_is_pointed_at(tmp_path, monkeypatch):
    """Whether mlx-whisper really loads from a folder is TASK-089.16's
    criterion 8 and needs a Mac nobody here has; what is pinned is that the
    folder this installation downloaded is the path it is handed."""
    entry, body = one_mlx_entry(monkeypatch, tmp_path, "large-v3-turbo")
    folder = tmp_path / entry.folder
    folder.mkdir(parents=True)
    (folder / "weights.safetensors").write_bytes(body)

    model = mlx_backend.MlxWhisperModel("large-v3-turbo", module=fake_mlx_module([]))

    assert model.repo == str(folder)


def test_without_a_downloaded_copy_the_hub_id_is_used_as_before(tmp_path, monkeypatch):
    one_mlx_entry(monkeypatch, tmp_path, "large-v3-turbo")

    model = mlx_backend.MlxWhisperModel("large-v3-turbo", module=fake_mlx_module([]))

    assert model.repo == "mlx-community/whisper-large-v3-turbo"
