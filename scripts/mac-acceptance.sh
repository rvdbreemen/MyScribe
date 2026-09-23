#!/usr/bin/env bash
# TASK-020's acceptance run, as one command instead of a paragraph.
#
# The macOS acceleration was built without a Mac: mlx-whisper for
# transcription, MPS for diarization, the doctor reporting which it picked.
# Five of the task's six criteria are covered by the suite. The sixth is a
# measurement that can only be taken on Apple Silicon, and this script takes
# it and prints it in the shape the task wants pasted back.
#
#   ./scripts/mac-acceptance.sh                 doctor only
#   ./scripts/mac-acceptance.sh some/audio.m4a  doctor, then transcribe that file
#
# It changes nothing and installs nothing. If the venv or ffmpeg is missing it
# says so and stops, because a red line here is the bug report and a guess
# about why would spoil it.
set -u

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname -- "$here")"
py="$repo/.venv/bin/python"
audio="${1:-}"

echo "=== MyScribe macOS acceptance (TASK-020 AC6) ==="
echo

# The revision matters and is easy to forget. A doctor line from main and one
# from a feature branch can be two different code paths wearing the same
# words, so the evidence says which it came from.
echo "--- what this was run against ---"
if command -v git >/dev/null 2>&1 && [ -d "$repo/.git" ]; then
  echo "revision : $(git -C "$repo" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "branch   : $(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  if [ -n "$(git -C "$repo" status --porcelain 2>/dev/null)" ]; then
    echo "note     : the working tree has uncommitted changes"
  fi
fi
echo "machine  : $(uname -sm)"
[ "$(uname -s)" = "Darwin" ] || echo "note     : this is not macOS, so the numbers below are not the acceptance"
echo

if [ ! -x "$py" ]; then
  echo "No venv at $py"
  echo "README.md has the install; on a Mac it is:"
  echo "  python3 install.py"
  echo "  brew install ffmpeg"
  exit 1
fi

echo "--- what the app thinks it will run on ---"
"$py" -c "
import sys
sys.path.insert(0, '$repo')
from scribe import accel
print('transcription backend :', accel.transcription_backend())
print('diarization device    :', accel.diarization_device())
print('describe()            :', accel.describe())
" || echo "(accel could not be read - that answer is itself the bug report)"
echo

echo "--- the doctor, with the model load ---"
"$py" -m scribe.doctor
doctor_status=$?
echo
echo "doctor exit: $doctor_status"
echo

if [ -z "$audio" ]; then
  echo "No audio file given, so no transcription was run."
  echo "For the full acceptance, run it again with one:"
  echo "  ./scripts/mac-acceptance.sh path/to/a/recording.m4a"
  echo
  echo "Paste everything above into TASK-020."
  exit 0
fi

if [ ! -f "$audio" ]; then
  echo "No such file: $audio"
  exit 1
fi

echo "--- transcribing $audio with speakers on ---"
"$py" - "$audio" <<'PYEOF'
import sys, time
sys.path.insert(0, ".")
from scribe import db, jobs, media, paths
from scribe.options import TranscribeOptions

path = sys.argv[1]
paths.ensure_dirs()
conn = db.connect(paths.DB_PATH)
db.migrate(conn)

row = media.ingest_path(conn, path)
options = TranscribeOptions(diarize=True)
job_id = jobs.enqueue(conn, "transcribe", media_id=row["id"], params=options.to_params())
print(f"media {row['id']} {row['title']!r}, job {job_id} queued")
print("Now start the app so its supervisor runs the job:")
print("  .venv/bin/python -m scribe --no-browser")
print(f"and watch http://127.0.0.1:4242/jobs/{job_id} - paste that page's")
print("transcribe and diarize events (device, pipeline, timings) into TASK-020.")
conn.close()
PYEOF
