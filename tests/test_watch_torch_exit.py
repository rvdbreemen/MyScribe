"""The torch-exit watcher's decision, on fake wheels (GitHub #43).

torch stays at 2.10 until CTranslate2 ships a CUDA 13 build; the weekly
workflow (.github/workflows/torch-watch.yml) asks packaging/watch_torch_exit.py
whether the newest ctranslate2 wheels load cuBLAS 13 yet. These tests build the
wheels in memory, deflated like the real ones, so no network is touched. Each
non-trigger wheel carries a decoy `13` in another member: a scanner that greps
the whole wheel instead of the one library would fire on it.
"""

from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packaging"))
try:
    import watch_torch_exit as watch
finally:
    sys.path.pop(0)

DECOY = b"Release notes: a later build moves to cublas64_13.dll and libcublas.so.13.\n"


def _wheel(members: dict[str, bytes]) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as wheel:
        for name, data in members.items():
            wheel.writestr(name, data)
    buffer.seek(0)
    return buffer


def _library(name: bytes, utf16: bool = False) -> bytes:
    # Padding on both sides, so the name lands mid-stream in the inflated data.
    text = name.decode("ascii")
    encoded = text.encode("utf-16-le") if utf16 else name
    return b"\x00MZ" + b"\x90" * 300_000 + encoded + b"\x00" + b"\xcc" * 300_000


def _win(library: bytes) -> io.BytesIO:
    return _wheel({
        "ctranslate2/__init__.py": b"",
        "ctranslate2-9.9.9.dist-info/METADATA": DECOY,
        "ctranslate2/ctranslate2.dll": library,
    })


def _linux(library: bytes) -> io.BytesIO:
    return _wheel({
        "ctranslate2/__init__.py": b"",
        "ctranslate2-9.9.9.dist-info/METADATA": DECOY,
        "ctranslate2.libs/libctranslate2-0a1b2c3d.so.4.8.3": library,
    })


def test_a_windows_wheel_on_cublas_12_is_not_the_exit():
    majors = watch.cublas_majors(_win(_library(b"cublas64_12.dll")), "win")
    assert majors == {12}
    assert not watch.is_trigger(majors)


def test_a_windows_wheel_on_cublas_13_is_the_exit():
    majors = watch.cublas_majors(_win(_library(b"cublas64_13.dll")), "win")
    assert majors == {13}
    assert watch.is_trigger(majors)


def test_a_wide_string_name_is_found_too():
    """LoadLibraryW takes UTF-16: the name may be stored wide, not ASCII."""
    majors = watch.cublas_majors(_win(_library(b"cublas64_13.dll", utf16=True)), "win")
    assert majors == {13}


def test_a_linux_wheel_on_cublas_12_is_not_the_exit():
    majors = watch.cublas_majors(_linux(_library(b"libcublas.so.12")), "linux")
    assert majors == {12}
    assert not watch.is_trigger(majors)


def test_a_linux_wheel_on_cublas_13_is_the_exit():
    majors = watch.cublas_majors(_linux(_library(b"libcublas.so.13")), "linux")
    assert majors == {13}
    assert watch.is_trigger(majors)


def test_a_library_that_names_no_cublas_is_unknown_not_quiet():
    """A renamed library or a moved string must not read as 'still CUDA 12'."""
    majors = watch.cublas_majors(_win(_library(b"something_else.dll")), "win")
    assert majors == set()
    assert not watch.is_trigger(majors)
    assert watch.verdict(majors) == "UNKNOWN"


def test_a_wheel_without_the_library_is_unknown():
    wheel = _wheel({"ctranslate2-9.9.9.dist-info/METADATA": DECOY})
    assert watch.cublas_majors(wheel, "win") == set()


def test_the_newest_cp312_wheels_are_picked_from_pypi_json():
    release = {
        "info": {"version": "4.9.0"},
        "urls": [
            {"filename": "ctranslate2-4.9.0-cp311-cp311-win_amd64.whl", "url": "u311", "yanked": False},
            {"filename": "ctranslate2-4.9.0-cp312-cp312-win_amd64.whl", "url": "uwin", "yanked": False},
            {"filename": "ctranslate2-4.9.0-cp312-cp312-manylinux_2_27_aarch64.manylinux_2_28_aarch64.whl",
             "url": "uarm", "yanked": False},
            {"filename": "ctranslate2-4.9.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl",
             "url": "ulinux", "yanked": False},
        ],
    }
    assert watch.pick_wheels(release) == ("4.9.0", {"win": "uwin", "linux": "ulinux"})


def test_the_newest_cu130_torch_for_windows_cp312_is_read_from_the_index():
    page = (
        '<a href="/whl/cu130/torch-2.9.1%2Bcu130-cp312-cp312-win_amd64.whl#sha256=aa">x</a>'
        '<a href="/whl/cu130/torch-2.14.1%2Bcu130-cp312-cp312-win_amd64.whl#sha256=bb">x</a>'
        '<a href="/whl/cu130/torch-2.15.0%2Bcu130-cp313-cp313-win_amd64.whl#sha256=cc">x</a>'
        '<a href="/whl/cu130/torch-2.15.0%2Bcu130-cp312-cp312-manylinux_2_28_x86_64.whl#sha256=dd">x</a>'
        '<a href="/whl/cu130/torch-2.14.0%2Bcu130-cp312-cp312-win_amd64.whl#sha256=ee">x</a>'
    )
    assert watch.newest_torch(page) == "2.14.1+cu130"


def test_a_name_cut_by_a_chunk_boundary_adds_no_phantom_major():
    """On Linux the name has no suffix, so 'libcublas.so.12' cut after
    'libcublas.so.1' at a chunk boundary matched as major 1 as well."""
    cut = watch.CHUNK - len(b"libcublas.so.1")
    library = b"\xcc" * cut + b"libcublas.so.12" + b"\x00" * 1000
    assert watch._majors_in(io.BytesIO(library), "linux") == {12}


def test_main_prints_the_trigger_line_the_workflow_looks_for(monkeypatch, capsys):
    """The workflow greps '^TRIGGER:'; a renamed line would mean a CUDA 13
    build arrives and nobody is told."""
    monkeypatch.setattr(watch, "_fetch_text", lambda url: "{}")
    monkeypatch.setattr(watch, "pick_wheels", lambda data: ("9.9.9", {"win": "https://x/win.whl", "linux": "https://x/linux.whl"}))
    monkeypatch.setattr(watch, "newest_torch", lambda text: "2.14.1+cu130")

    class Remote(io.BytesIO):
        fetched, size, requests = 1, 1, 1

        def __init__(self, url):
            source = _win(_library(b"cublas64_13.dll")) if url.endswith("win.whl") else _linux(_library(b"libcublas.so.13"))
            super().__init__(source.getvalue())

    monkeypatch.setattr(watch, "RangeFile", Remote)

    assert watch.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert any(line.startswith("TRIGGER:") for line in lines), lines
    assert not any(line.startswith("CHECK-FAILED:") for line in lines), lines
