#!/usr/bin/env bash
# Start MyScribe on Linux and macOS.
#
# Three things this does that typing the command by hand does not:
#
#   * It uses the venv's python. `python -m scribe` finds whatever is on PATH,
#     which is the interpreter without the dependencies, and the failure that
#     produces names a missing module rather than the real mistake.
#   * It refuses to start a second instance on a port that already answers.
#     Two supervisors against one SQLite file is not a scenario this app is
#     written for (ADR-002 coordinates processes, but the second one has no
#     business existing).
#   * With --detached it survives the shell that started it. A long transcribe
#     queue outlives a terminal window, and an agent's background task can be
#     reaped by its harness mid-queue - that is what this flag is for.
#
# Everything after the script's own flags is handed to `python -m scribe`, so
# `start.sh --detached -- --port 4299 --no-supervisor` works.
set -euo pipefail

here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname -- "$here")"
py="$repo/.venv/bin/python"

detached=0
args=()
while [ $# -gt 0 ]; do
  case "$1" in
    --detached|-d) detached=1; shift ;;
    --) shift; args+=("$@"); break ;;
    *) args+=("$1"); shift ;;
  esac
done

if [ ! -x "$py" ]; then
  echo "No venv at $py" >&2
  echo "README.md has the install; the short version is:" >&2
  echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-ml.txt" >&2
  exit 1
fi

# The port the app will actually use, so the check below asks about the right
# one. `--port N` and `--port=N` both appear in the README.
port=4242
for i in "${!args[@]}"; do
  case "${args[$i]}" in
    --port) port="${args[$((i + 1))]:-$port}" ;;
    --port=*) port="${args[$i]#--port=}" ;;
  esac
done

# No curl or nc dependency: the interpreter we just found can open a socket.
if "$py" - "$port" <<'PY'
import socket, sys
with socket.socket() as s:
    s.settimeout(1.0)
    sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PY
then
  echo "Something already answers on http://127.0.0.1:$port - not starting a second one."
  exit 0
fi

cd "$repo"

if [ "$detached" -eq 1 ]; then
  log="$repo/data/logs/start.log"
  mkdir -p "$(dirname -- "$log")"
  nohup "$py" -m scribe "${args[@]+"${args[@]}"}" >>"$log" 2>&1 &
  pid=$!
  disown "$pid" 2>/dev/null || true
  echo "MyScribe started detached, pid $pid"
  echo "  http://127.0.0.1:$port"
  echo "  log:  $log"
  echo "  stop: kill $pid"
else
  exec "$py" -m scribe "${args[@]+"${args[@]}"}"
fi
