"""A real transcribe job that asks for speakers on a machine whose token will
not open the weights: does it keep its transcript?

This is TASK-089.08 criterion 1's real-run half, which the mocked stage tests
cannot answer. Run it once in a tree with HEAD's scribe/stages/diarize.py (the
job should FAIL) and once in the working tree (it should end DONE with the
transcript and a note).

    <repo>/.venv/Scripts/python real_run_diarize.py <tree> <scratch> <label>

`tree` is the source tree to import scribe from; `scratch` a directory of its
own for the data and the Hugging Face cache; `label` goes in the output.

Everything is real: ffprobe, ffmpeg, faster-whisper on the tiny model, and the
actual runner walking the stages - only the token is arranged, and only so
that the weights genuinely cannot be fetched. Nothing touches the live
library: SCRIBE_DATA_DIR is the scratch directory and HF_HOME is a copy.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

tree = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2]).resolve()
label = sys.argv[3]

data_dir = scratch / "data"
hf_home = scratch / "hf"
data_dir.mkdir(parents=True, exist_ok=True)
hf_home.mkdir(parents=True, exist_ok=True)

# The whisper weights are copied in so transcription is real and offline; the
# pyannote ones are deliberately absent, which is the point of the run.
src_hub = Path.home() / ".cache" / "huggingface" / "hub"
dst_hub = hf_home / "hub"
dst_hub.mkdir(parents=True, exist_ok=True)
tiny = src_hub / "models--Systran--faster-whisper-tiny"
if tiny.is_dir() and not (dst_hub / tiny.name).exists():
    shutil.copytree(tiny, dst_hub / tiny.name)

os.environ.update(
    SCRIBE_DATA_DIR=str(data_dir),
    SCRIBE_ENV_FILE=str(scratch / "no.env"),
    HF_HOME=str(hf_home),
    # A token that exists and opens nothing. It beats the machine-wide one in
    # the registry, so the stage meets a real refusal from Hugging Face rather
    # than a missing-token short circuit - the harder of the two paths.
    HF_TOKEN="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0000",
    HUGGINGFACE_TOKEN="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0000",
    HUGGING_FACE_HUB_TOKEN="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0000",
)
sys.path.insert(0, str(tree))

from scribe import db, jobs, media, paths, runner  # noqa: E402

paths.refresh()
conn = db.connect()
db.migrate(conn)

clip = tree / "tests" / "fixtures" / "clip30.wav"
row = media.ingest_path(conn, clip)
media_id = row["id"] if not isinstance(row, int) else row
options = {"diarize": True, "tier": "turbo", "model": "tiny"}
job_id = jobs.enqueue(conn, "transcribe", media_id=media_id, params=options)

print(f"=== {label} ===")
print(f"  tree        {tree}")
print(f"  data        {data_dir}")
print(f"  clip        {clip.name} ({clip.stat().st_size} bytes)")
print(f"  job         {job_id}, options {options}")

started = time.perf_counter()
exit_code = runner.main([str(job_id)])
elapsed = time.perf_counter() - started

job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
words = conn.execute(
    "SELECT COUNT(*) FROM word w JOIN run r ON w.run_id = r.id WHERE r.media_id=?", (media_id,)
).fetchone()[0]
run = conn.execute(
    "SELECT * FROM run WHERE media_id=? ORDER BY id DESC LIMIT 1", (media_id,)
).fetchone()
note = ""
if run is not None and run["params_json"]:
    note = (json.loads(run["params_json"]) or {}).get("diarization_note", "")

print(f"  runner exit {exit_code} after {elapsed:.1f}s")
print(f"  job status  {job['status']}")
print(f"  error       {(job['error_detail'] or '')[:150]}")
print(f"  words kept  {words}")
print(f"  note        {note[:220] or '(none)'}")
