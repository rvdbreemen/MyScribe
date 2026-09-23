"""TASK-093: how often does the doctor die with the card hidden, and where?

**This loads the 1.6 GB model many times.** Run it on Robert's machine with
nothing else heavy running, from the repository root, in your own terminal:

    .venv/Scripts/python scripts/task093_crash_census.py --out task093-census.jsonl

It runs one process at a time, never two, and writes one JSON line per run as
it goes, so a run you stop with Ctrl+C still leaves everything measured so
far. Send back the .jsonl file and the summary it prints at the end.

Expect about 20-25 minutes for the twenty full runs (the one surviving run on
2026-09-22 took 62 s for the smoke alone) and about as long again for the
reduced reproductions. `--runs` and `--reduced-runs` shorten it; `--only`
picks one part.

Every run, full or reduced, gets the same fence:

* `CUDA_VISIBLE_DEVICES=-1`, except the variants whose name says `visible`;
* a fresh `SCRIBE_DATA_DIR` under `--data-root` per run, so no run touches the
  library and no run sees another's database;
* an empty `SCRIBE_ENV_FILE`, so `.env` cannot move the data directory back;
* `PYTHONFAULTHANDLER=1`, so a crash leaves the Python frames it died in -
  with or without the doctor's own faulthandler (TASK-093 criterion 2).

`HF_HOME` is left alone on purpose: pointing it at the fence would download
the 1.6 GB model again for every run.

What each record holds: the variant, the run number, the exit code (and in
hex, since 0xC0000005 is Windows' access violation and Git Bash shows it as
139), seconds, the byte counts of stdout and stderr, the last non-empty line,
and the fault block - the lines from "Windows fatal exception" or "Fatal
Python error" on - when there is one. A run counts as a crash by its exit
code alone: not 0, not 1 (the doctor's two verdicts) and not a timeout. The
fault text is kept beside it and never decides, because faulthandler can print
a block for an exception that native code went on to handle.

The reduced variants are TASK-093's ruled-out list plus the combination that
did not crash twice, run enough times to say "does not reduce" with a number:

* `full` - `python -m scribe.doctor`, the only reliable trigger known;
* `smoke-probe-turbo` - the doctor's GPU pair in-process: check_gpu_runtime,
  then gpu_smoke, which builds large-v3-turbo through the accelerator probe;
* `load-probe-turbo` - `load_model("large-v3-turbo")` with no device, so the
  probe runs, and a transcription of the clip;
* `load-probe-tiny` - the same with `tiny`: does it need the big model?
* `load-cpu-turbo-hidden` - device="cpu" passed, so the probe never runs;
* `load-cpu-turbo-visible` - the card visible, device="cpu";
* `probe-then-cpu-visible` - the card visible, is_available() first, then cpu;
* `diarize-import-then-probe-turbo` - `scribe.stages.diarize` imported first
  (the doctor's diarization check does that before the GPU checks), then the
  probe path. Not in the task's list; it is the one import the full command
  makes that the reduced ones did not.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLIP = REPO / "tests" / "fixtures" / "clip30.wav"

FAULT_MARKERS = ("Windows fatal exception", "Fatal Python error")

_TRANSCRIBE = (
    "segments, info = model.transcribe({clip!r}, word_timestamps=True, vad_filter=True)\n"
    "words = sum(len(s.words or []) for s in segments)\n"
    "print(f'{{device}}/{{compute}}: {{words}} words from {{info.duration:.0f}}s')\n"
)

REDUCED: dict[str, tuple[bool, str]] = {
    # name: (card hidden, script)
    "smoke-probe-turbo": (
        True,
        "from scribe import doctor\n"
        "print(doctor.check_gpu_runtime())\n"
        "print(doctor.gpu_smoke(record=False))\n",
    ),
    "load-probe-turbo": (
        True,
        "from scribe.stages import transcribe\n"
        "model, device, compute = transcribe.load_model('large-v3-turbo')\n" + _TRANSCRIBE,
    ),
    "load-probe-tiny": (
        True,
        "from scribe.stages import transcribe\n"
        "model, device, compute = transcribe.load_model('tiny')\n" + _TRANSCRIBE,
    ),
    "load-cpu-turbo-hidden": (
        True,
        "from scribe.stages import transcribe\n"
        "model, device, compute = transcribe.load_model('large-v3-turbo', device='cpu')\n"
        + _TRANSCRIBE,
    ),
    "load-cpu-turbo-visible": (
        False,
        "from scribe.stages import transcribe\n"
        "model, device, compute = transcribe.load_model('large-v3-turbo', device='cpu')\n"
        + _TRANSCRIBE,
    ),
    "probe-then-cpu-visible": (
        False,
        "import torch\n"
        "from scribe import cuda_setup\n"
        "cuda_setup.ensure_cuda_libs()\n"
        "print('cuda available:', torch.cuda.is_available())\n"
        "from scribe.stages import transcribe\n"
        "model, device, compute = transcribe.load_model('large-v3-turbo', device='cpu')\n"
        + _TRANSCRIBE,
    ),
    "diarize-import-then-probe-turbo": (
        True,
        "import scribe.stages.diarize\n"
        "from scribe.stages import transcribe\n"
        "model, device, compute = transcribe.load_model('large-v3-turbo')\n" + _TRANSCRIBE,
    ),
}


def reduced_script(name: str) -> str:
    return REDUCED[name][1].format(clip=str(CLIP))


def fenced_env(data_dir: Path, empty_env: Path, *, hidden: bool, base: dict | None = None) -> dict:
    """The environment every run gets; see the module docstring."""
    env = dict(os.environ if base is None else base)
    env.pop("CUDA_VISIBLE_DEVICES", None)
    if hidden:
        env["CUDA_VISIBLE_DEVICES"] = "-1"
    env["SCRIBE_DATA_DIR"] = str(data_dir)
    env["SCRIBE_ENV_FILE"] = str(empty_env)
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def fault_block(text: str) -> str:
    """The crash report's lines, from its first marker on, or ""."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(FAULT_MARKERS):
            return "\n".join(lines[index : index + 25])
    return ""


def run_one(variant: str, number: int, argv: list[str], env: dict, timeout: float) -> dict:
    """Run one child to its end and describe what it left behind."""
    started = time.monotonic()
    try:
        child = subprocess.run(
            argv, cwd=REPO, env=env, capture_output=True, timeout=timeout,
        )
        code: int | None = child.returncode
        out = child.stdout.decode("utf-8", "replace")
        err = child.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as expired:
        code = None
        out = (expired.stdout or b"").decode("utf-8", "replace")
        err = (expired.stderr or b"").decode("utf-8", "replace")
    seconds = round(time.monotonic() - started, 1)
    both = [line for line in (out + "\n" + err).splitlines() if line.strip()]
    return {
        "crashed": code is not None and code not in (0, 1),
        "variant": variant,
        "run": number,
        "exit": code,
        "exit_hex": None if code is None else f"0x{code & 0xFFFFFFFF:08X}",
        "timed_out": code is None,
        "seconds": seconds,
        "stdout_bytes": len(out.encode("utf-8")),
        "stderr_bytes": len(err.encode("utf-8")),
        "last_line": both[-1] if both else "",
        "fault": fault_block(err) or fault_block(out),
    }


def plan(only: str, runs: int, reduced_runs: int, python: str) -> list[tuple[str, int, list[str], bool]]:
    """(variant, run number, argv, card hidden) for every run, in order."""
    todo: list[tuple[str, int, list[str], bool]] = []
    if only in ("all", "full"):
        todo += [("full", n, [python, "-m", "scribe.doctor"], True) for n in range(1, runs + 1)]
    if only in ("all", "reduced"):
        for name, (hidden, _script) in REDUCED.items():
            todo += [
                (name, n, [python, "-c", reduced_script(name)], hidden)
                for n in range(1, reduced_runs + 1)
            ]
    return todo


def summarise(records: list[dict]) -> str:
    by_variant: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        by_variant[record["variant"]].append(record)
    lines = []
    for variant, rows in by_variant.items():
        crashed = [r for r in rows if r["crashed"]]
        codes = Counter(r["exit_hex"] or "timeout" for r in rows)
        lines.append(
            f"{variant:34} {len(crashed):2}/{len(rows):2} crashed; exits "
            + ", ".join(f"{code} x{count}" for code, count in sorted(codes.items()))
        )
        frames = Counter(
            next((l.strip() for l in r["fault"].splitlines() if l.strip().startswith("File ")), "?")
            for r in crashed
        )
        for frame, count in frames.most_common():
            lines.append(f"    x{count} innermost: {frame}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True, help="where the JSON lines go")
    parser.add_argument("--runs", type=int, default=20, help="full doctor runs (default 20)")
    parser.add_argument("--reduced-runs", type=int, default=10, help="runs per reduced variant (default 10)")
    parser.add_argument("--only", choices=("all", "full", "reduced"), default="all")
    parser.add_argument("--data-root", type=Path, default=None, help="fence directories go here")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds before a run counts as hung")
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args(argv)

    root = args.data_root or Path(tempfile.mkdtemp(prefix="task093-"))
    root.mkdir(parents=True, exist_ok=True)
    empty_env = root / "empty.env"
    empty_env.write_text("", encoding="utf-8")

    todo = plan(args.only, args.runs, args.reduced_runs, args.python)
    print(f"{len(todo)} runs, one at a time; fences under {root}; records to {args.out}", flush=True)
    records: list[dict] = []
    with args.out.open("a", encoding="utf-8") as sink:
        for variant, number, child_argv, hidden in todo:
            env = fenced_env(root / f"{variant}-{number:02}", empty_env, hidden=hidden)
            record = run_one(variant, number, child_argv, env, args.timeout)
            records.append(record)
            sink.write(json.dumps(record) + "\n")
            sink.flush()
            print(
                f"{variant} #{number}: exit {record['exit_hex']} in {record['seconds']}s"
                f"{' CRASH' if record['crashed'] else ''} - {record['last_line'][:100]}",
                flush=True,
            )
    print("\n" + summarise(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
