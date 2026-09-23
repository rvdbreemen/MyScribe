# macOS acceptance, in one sitting

Every point below needs a real Apple Silicon Mac, and none of them has been
run. The Mac this project was verified on belongs to somebody else, so its
points are collected here and run together, not asked one at a time (Robert's
decision of 2026-09-20, recorded in TASK-089 and ADR-015's Open Questions).
Until that sitting each of them reads **not run**, and no macOS box under
TASK-089 is ticked without a line from this list.

How to use it: work from top to bottom in one Terminal window, paste each
block's output back into the task it names, and say which points were
skipped. A point that goes red is a bug report, not a failure of the sitting.

Before you start:

    xcode-select --install          # if git is missing
    python3 --version               # 3.9 or newer
    brew install ffmpeg             # MyScribe never runs brew itself (ADR-017)

Leave Ollama **uninstalled** until point 5 says otherwise; point 5 needs a Mac
without it. If Ollama is already installed, do points 1-4 and 6-7 and mark 5
as "Ollama was present".

---

## 1. A clone installs with one command

TASK-089 criterion 2, TASK-089.17 criterion 1.

    git clone https://github.com/rvdbreemen/MyScribe.git ~/MyScribe-mac
    cd ~/MyScribe-mac
    python3 install.py
    git status --short

Expected, in this order:

- `python 3.x.y on darwin arm64 (macos-arm64)`
- a `disk` line; the environment's size on macOS reads "has not been measured"
- `tools fetching uv 0.12.13` and a line naming both the pinned version and
  what `.tools/bin/uv --version` says
- `tools` saying ffmpeg was found on PATH (you installed it above)
- `sync uv sync --frozen into .../.venv`, uv's own lines, then a `stamp` line
- the setup questions in the terminal; press Enter through all of them
- the proof report: an `accel` line naming MLX, and a transcription line with
  words and seconds for the 30-second clip
- `start MyScribe is installed. Start it with:` and the command
- `git status --short` prints nothing: uv.lock was not touched

## 2. Ollama's install locations are the ones MyScribe looks in

TASK-089.06 criterion 2. Read from Ollama's `install.sh:66-83`, never checked
on a Mac.

    ls -l /Applications/Ollama.app/Contents/Resources/ollama /usr/local/bin/ollama 2>&1
    .venv/bin/python -c "from scribe import ollama_setup as o; print(o.install_locations()); print(o.state())"

Expected with Ollama absent: both `ls` lines say "No such file", the list
names those two paths, and the state is `absent`. Run it again after point 5
has installed Ollama: one path exists and the state is no longer `absent`.

## 3. mlx-whisper loads from a local folder, offline

TASK-089.16 criterion 8. Nobody has verified that mlx-whisper loads from a
folder rather than from the Hub.

    export HF_HOME="$(mktemp -d)"
    export SCRIBE_DATA_DIR="$(mktemp -d)"
    .venv/bin/python -m scribe.models --fetch
    HF_HUB_OFFLINE=1 .venv/bin/python -m scribe.doctor

Expected: the models list shows `mlx-community/whisper-large-v3-turbo` as
`have` after the fetch, and the doctor's `gpu-smoke` line is green with the
MLX backend, a word count and seconds - with the Hub switched off. A red line
saying the model cannot be found offline is the answer "no", and is the bug
report.

## 4. The release's first run

TASK-089.15 criterion 13. The dmg from the latest release, a fresh home.

    open ~/Downloads/MyScribe-*-macos-arm64.dmg      # drag MyScribe to Applications
    /Applications/MyScribe.app/Contents/MacOS/MyScribe --home ~/MyScribe-fresh-home

Expected: Gatekeeper asks once (Control-click, Open, then Open Anyway); the
window asks where everything goes before anything is downloaded; after the
sync it shows what it found and only the open questions, each with a Skip;
"Save and start" ends with the app open in the browser. A photo or a screen
recording of the window is worth more than words: nobody has seen it on a Mac.

## 5. Ollama installed from the dmg MyScribe shows

TASK-089.18 criterion 16, the dmg path. Needs a Mac **without** Ollama.

    cd ~/MyScribe-mac
    .venv/bin/python -m scribe.setup

Keep "Ollama, on this machine" and answer yes to the install. Expected, before
the question: the dmg's URL on github.com/ollama/ollama/releases/download/v0.34.3,
197,972,871 bytes and sha256 `7bde8d83cc7c2ac54e3dc51f618f4357e3b976f29f01f3efee896a301576b44f`,
and `/Applications/Ollama.app`. After yes: Finder opens the image and the
sitting waits with "Check again". Drag Ollama to Applications, start it, press
Check again: it finds Ollama. `gemma4:12b` is never offered on a Mac. No
marker file appears in the data directory (`ls "$SCRIBE_DATA_DIR"` or
`~/MyScribe-mac/data` shows no `ollama_setup.json`).

## 6. Start at login

TASK-089.21 criterion 6. Built from Apple's launchd documentation; no login
has honoured it anywhere.

1. Settings > Start at login: switch it on.
2. `ls -l ~/Library/LaunchAgents/io.github.rvdbreemen.myscribe.plist && plutil -p ~/Library/LaunchAgents/io.github.rvdbreemen.myscribe.plist`
   Expected: the file exists, `RunAtLoad` is true, and `ProgramArguments`
   is `/usr/bin/open -a <the .app> --args --at-login --no-browser`.
3. Log out and in. Expected: `curl -s http://127.0.0.1:4242/health` answers
   without anybody having started MyScribe.
4. Switch it off in Settings, log out and in. Expected: the plist is gone and
   `/health` does not answer.

## 7. What an uninstall leaves behind

TASK-089.23, the macOS section of the README's "Uninstalling". Written from
reading. Remove `/Applications/MyScribe.app`, then check that the home
(`~/Library/Application Support/MyScribe` or the folder you chose) is still
there, and that the LaunchAgent from point 6 is gone if you switched it off
first. Say what you found.

---

## What to send back

One message per point: the command, its full output, and "ok" or what was
different. The tasks each point closes are named in its heading.
