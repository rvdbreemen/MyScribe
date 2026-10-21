"""Watch for the way out of the torch 2.10 pin (GitHub #43).

torch stays at 2.10 while CTranslate2 needs CUDA 12: torch 2.14 with CUDA 12
exists only on the cu126 index, whose builds carry no Blackwell kernels (see the
comment above the pin in pyproject.toml). The exit is a CTranslate2 release
built against CUDA 13; then Windows takes torch from the cu130 index and Linux
from plain PyPI. This script asks the newest ctranslate2 wheels which cuBLAS
they load, by name, from the one library that loads it:

    win_amd64   ctranslate2/ctranslate2.dll        cublas64_<major>.dll
    manylinux   ctranslate2.libs/libctranslate2*   libcublas.so.<major>

It reads only that member, through HTTP range reads of the wheel, so the zip
work (central directory, zip64, inflate) stays with the standard library's
zipfile. It also prints the newest Windows cp312 torch on the cu130 index.

Standard library only: torch-watch.yml runs it on the runner's own Python. It
always exits 0 and prints a summary; a `TRIGGER:` line means CUDA 13 was found,
a `CHECK-FAILED:` line means a source could not be read - which must not pass
for a quiet week, so the workflow fails on it.

    python packaging/watch_torch_exit.py
"""

from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from urllib.request import Request, urlopen

PYPI = "https://pypi.org/pypi/ctranslate2/json"
TORCH_INDEX = "https://download.pytorch.org/whl/cu130/torch/"
TIMEOUT = 60
USER_AGENT = "MyScribe torch-watch (github.com/rvdbreemen/MyScribe/issues/43)"
EXIT_MAJOR = 13
CHUNK = 1 << 20

# The library that loads cuBLAS, by file name inside the wheel, per platform.
LIBRARY = {
    "win": re.compile(r"(^|/)ctranslate2\.dll$", re.IGNORECASE),
    "linux": re.compile(r"(^|/)libctranslate2[^/]*\.so[^/]*$"),
}
# The cuBLAS file name the library carries, split around its major version.
CUBLAS = {"win": ("cublas64_", ".dll"), "linux": ("libcublas.so.", "")}


def _patterns(prefix: str, suffix: str) -> list[re.Pattern[bytes]]:
    """The name as ASCII, and as UTF-16LE in case it is stored wide for
    LoadLibraryW; the major is the first group either way."""
    narrow = re.escape(prefix.encode()) + rb"(\d{1,3})" + re.escape(suffix.encode())
    wide = (b"".join(re.escape(c.encode()) + b"\x00" for c in prefix)
            + rb"((?:\d\x00){1,3})"
            + b"".join(re.escape(c.encode()) + b"\x00" for c in suffix))
    return [re.compile(narrow, re.IGNORECASE), re.compile(wide, re.IGNORECASE)]


def _majors_in(stream, platform: str) -> set[int]:
    patterns = _patterns(*CUBLAS[platform])
    found: set[int] = set()
    tail = b""
    while True:
        chunk = stream.read(CHUNK)
        window = tail + chunk
        for pattern in patterns:
            for match in pattern.finditer(window):
                # A name touching the window's end may be cut by the chunk
                # boundary - "libcublas.so.1" of "...so.12", which has no
                # suffix to close it - so it is counted from the next window,
                # whose 64-byte tail holds it whole.
                if chunk and match.end() > len(window) - 16:
                    continue
                found.add(int(match.group(1).replace(b"\x00", b"")))
        if not chunk:
            return found
        tail = window[-64:]


def cublas_majors(wheel, platform: str) -> set[int]:
    """The cuBLAS majors the platform's CTranslate2 library names, read from a
    seekable wheel. Empty means the library or the name was not found."""
    found: set[int] = set()
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if LIBRARY[platform].search(name):
                with archive.open(name) as member:
                    found |= _majors_in(member, platform)
    return found


def is_trigger(majors: set[int]) -> bool:
    return any(major >= EXIT_MAJOR for major in majors)


def verdict(majors: set[int]) -> str:
    if not majors:
        return "UNKNOWN"
    return "TRIGGER" if is_trigger(majors) else "WAIT"


def pick_wheels(release: dict) -> tuple[str, dict[str, str]]:
    """The newest release's cp312 wheel URLs for win_amd64 and x86_64 manylinux."""
    wheels: dict[str, str] = {}
    for file in release.get("urls", []):
        name = file.get("filename", "")
        if file.get("yanked") or "-cp312-" not in name or not name.endswith(".whl"):
            continue
        if name.endswith("-win_amd64.whl"):
            wheels.setdefault("win", file["url"])
        elif "manylinux" in name and name.endswith("_x86_64.whl"):
            wheels.setdefault("linux", file["url"])
    return release["info"]["version"], wheels


_TORCH_WHEEL = re.compile(r"torch-(\d+(?:\.\d+)+)(?:%2B|\+)(cu\d+)-cp312-cp312-win_amd64\.whl")


def newest_torch(page: str) -> str | None:
    """The newest torch version on an index page with a Windows cp312 wheel."""
    found = {(m.group(1), m.group(2)) for m in _TORCH_WHEEL.finditer(page)}
    if not found:
        return None
    version, local = max(found, key=lambda v: tuple(int(part) for part in v[0].split(".")))
    return f"{version}+{local}"


class RangeFile(io.RawIOBase):
    """A remote file read through HTTP Range requests, seekable for zipfile."""

    def __init__(self, url: str):
        self.url = url
        self.position = 0
        self.fetched = 0
        self.requests = 0
        _, total = self._get(0, 0)
        self.size = total

    def _get(self, start: int, end: int) -> tuple[bytes, int]:
        request = Request(self.url, headers={"Range": f"bytes={start}-{end}", "User-Agent": USER_AGENT})
        with urlopen(request, timeout=TIMEOUT) as response:
            if response.status != 206:
                raise OSError(f"no range support: HTTP {response.status} for {self.url}")
            data = response.read()
            total = int(response.headers["Content-Range"].rsplit("/", 1)[1])
        self.requests += 1
        self.fetched += len(data)
        return data, total

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = max(0, base + offset)
        return self.position

    def readinto(self, buffer) -> int:
        if self.position >= self.size or len(buffer) == 0:
            return 0
        end = min(self.position + len(buffer), self.size) - 1
        data, _ = self._get(self.position, end)
        buffer[: len(data)] = data
        self.position += len(data)
        return len(data)


def _fetch_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode("utf-8")


def main() -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    print(f"torch-watch (GitHub #43), {stamp}")
    triggered: list[str] = []
    failed = False
    version = "?"
    try:
        version, wheels = pick_wheels(json.loads(_fetch_text(PYPI)))
        print(f"ctranslate2 newest on PyPI: {version}")
        for platform in ("win", "linux"):
            url = wheels.get(platform)
            if url is None:
                print(f"CHECK-FAILED: {platform}: no cp312 wheel in ctranslate2 {version}")
                failed = True
                continue
            try:
                remote = RangeFile(url)
                majors = cublas_majors(io.BufferedReader(remote, buffer_size=4 * CHUNK), platform)
            except Exception as error:  # report it; a crash would read as a quiet week
                print(f"CHECK-FAILED: {platform}: {type(error).__name__}: {error}")
                failed = True
                continue
            state = verdict(majors)
            shown = ", ".join(str(m) for m in sorted(majors)) or "none found"
            print(f"  {platform:5} {url.rsplit('/', 1)[-1]}: cuBLAS major {shown} -> {state}"
                  f" ({remote.fetched / 1e6:.1f} of {remote.size / 1e6:.1f} MB read in {remote.requests} requests)")
            if state == "UNKNOWN":
                print(f"CHECK-FAILED: {platform}: no cuBLAS name in the CTranslate2 library; the layout changed")
                failed = True
            elif state == "TRIGGER":
                triggered.append(platform)
    except Exception as error:
        print(f"CHECK-FAILED: PyPI: {type(error).__name__}: {error}")
        failed = True

    torch = None
    try:
        torch = newest_torch(_fetch_text(TORCH_INDEX))
        print(f"torch newest on cu130 for win cp312: {torch or 'none listed'}")
    except Exception as error:
        print(f"CHECK-FAILED: torch cu130 index: {type(error).__name__}: {error}")
        failed = True

    if triggered:
        print(f"TRIGGER: ctranslate2 {version} loads cuBLAS {EXIT_MAJOR}+ on {', '.join(triggered)}."
              f" torch can leave 2.10: the cu130 index on Windows (newest {torch or 'unknown'}),"
              " plain PyPI on Linux. Re-run the doctor on all three platforms (GitHub #43).")
    elif not failed:
        print("RESULT: no trigger - CTranslate2 still loads CUDA 12 cuBLAS; the torch pin stays (GitHub #43).")
    else:
        print("RESULT: incomplete - see CHECK-FAILED above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
