# Installing MyScribe

- [From a release](#from-a-release) - the recommended way
- [The first start](#the-first-start)
- [From a clone](#from-a-clone) - for developers and early adopters
- [Stopping MyScribe](#stopping-myscribe)
- [Updating](#updating)
- [Uninstalling](#uninstalling)

## From a release

Each [release](https://github.com/rvdbreemen/MyScribe/releases) has one file
per platform. It carries its own Python, package manager and ffmpeg, so
nothing else needs to be installed first.

| Platform | File |
| --- | --- |
| Windows (x64) | `MyScribe-<version>-windows-x64.exe` |
| macOS (Apple Silicon) | `MyScribe-<version>-macos-arm64.dmg` |
| Linux (x86_64) | `MyScribe-<version>-linux-x64.AppImage` |

The files are not signed yet, so each system warns you the first time:

* **Windows** - SmartScreen warns about an unknown publisher: choose **More
  info → Run anyway**. MyScribe installs for your account only, without
  administrator rights: the program goes into
  `%LOCALAPPDATA%\Programs\MyScribe`, and its working files into
  `%LOCALAPPDATA%\MyScribe` unless you choose another folder at the first
  start. Uninstalling never touches your library.
* **macOS** - Gatekeeper refuses an unsigned app, and until you allow it the
  app does not start at all, not even from a terminal. Drag MyScribe to
  Applications and start it once; macOS says it could not verify it. Then open
  **System Settings → Privacy & Security**, scroll to *Security*, press
  **Open Anyway** beside MyScribe, confirm with Touch ID or your password, and
  start it again. On macOS 15 and later this is the only way: the refusal
  itself has no Open button any more, and Control-click → Open gives the same
  refusal. On macOS 14 and earlier, **Control-click → Open**, then **Open**,
  also works. The dmg's *Open me first* note says the same.
* **Linux** - make the AppImage executable (`chmod +x`) and run it.

### Verify a download

Each release publishes `SHA256SUMS` beside the files:

```sh
sha256sum -c SHA256SUMS --ignore-missing     # Linux
shasum -a 256 -c SHA256SUMS --ignore-missing # macOS
```

```powershell
# Windows: compare the one line for the file you downloaded
(Get-FileHash MyScribe-<version>-windows-x64.exe -Algorithm SHA256).Hash.ToLower()
Select-String -Path SHA256SUMS -Pattern windows-x64.exe$
```

Every file also has a build-provenance attestation, which proves it was built
by this repository's release workflow:

```sh
gh attestation verify MyScribe-<version>-windows-x64.exe --repo rvdbreemen/MyScribe
```

`<version>` is the release you downloaded, such as `0.8.2`. When the file
verifies, this command prints **nothing** and exits with 0; a file it cannot
verify ends in an error. Add `--format json` to see what it checked: the
workflow, the tag and the commit the file was built from.

## The first start

**1. Where MyScribe keeps your library.** The library is your recordings,
their transcripts, the database, the speech models and the app's log. On
Windows the installer asks where it goes, on the page after the program
folder; on macOS and Linux the first start asks, before anything is
downloaded, and shows the free space on each drive. A silent Windows install
takes the folder from `/LIBRARY=D:\Data\MyScribe`.

- Keep the default and the library goes where it always has, in the `data`
  folder of the per-user *home*.
- An empty folder, or one that does not exist yet, becomes the library.
- A folder with other files in it gets a `MyScribe` folder inside it.
- A folder that already holds a MyScribe library (`myscribe.db`, or a former
  home with `data\myscribe.db`) is shown with its recordings and size after
  the download, and used only when you choose it there.
- A network share, a file, a folder inside the program folder or one inside
  the home (other than its `data`) is refused with the reason.
- If the first start does not get to the setup questions, a library you chose
  is offered again at the next start.

The speech engine and its caches always go to the home; start MyScribe with
`--home PATH`, or set `MYSCRIBE_HOME`, to put them elsewhere. Before the
download MyScribe checks the free space on the home's drive for the engine and
on the library's drive for the models and working room:

```
<home>\                  %LOCALAPPDATA%\MyScribe by default
├── env\  python\  cache\  bin\    the speech engine - can be downloaded again
├── logs\launcher.log  logs\app.log
└── data\                          your library, unless you chose another folder
    ├── myscribe.db
    ├── media\  models\  work\  logs\
```

On macOS the default home is `~/Library/Application Support/MyScribe`, on
Linux `~/.local/share/MyScribe`. Where the library is, is remembered in a
small file beside the default home, `MyScribe.location`. An update never asks
again, and uninstalling leaves both.

The speech models (about 1.6 GB) are the one large thing that can live
outside the library. The models setup downloads go to the library's `models`
folder. A model
that was not downloaded there - when setup's download was skipped, for
instance - is fetched by the speech engine itself on first use, into the
Hugging Face cache: `~/.cache/huggingface/hub`, or wherever `HF_HUB_CACHE` or
`HF_HOME` points. MyScribe uses either copy and does not download a model
twice. To keep the models with the library, let setup download them, or
point `HF_HOME` at a folder on the library's drive.

**2. The download.** MyScribe downloads the speech engine (a few GB) and shows
its progress. Later starts skip this.

**3. Setup.** A short setup shows what it found on this computer - a Hugging
Face token, an OpenRouter or OpenAI key, an Ollama, an existing MyScribe
library - and then asks what is still open, one step at a time, with **Back**,
**Skip** and **Next**:

- **Your library.** Start a new one, or use one you already have: one found on
  this computer, or a folder you name. MyScribe shows its recordings and size
  before anything changes; using it upgrades it in place.
- **Who answers questions about a transcript.** A cloud provider, or Ollama
  on this computer. Without Ollama, MyScribe offers to install Ollama's newest
  release and a model that fits, showing the download, its size and the exact
  command first. The download must match both checksums the release
  publishes. An Ollama you already have is left alone.
- **Transcription quality**, **a folder to watch** for new recordings, and
  **whether MyScribe starts when you log in** (default: no).

The last step lists your answers; **Save and start** applies them. Anything
skipped can be answered later in **Settings**, or with the **Setup** button in
the MyScribe window. The speech models are downloaded when first needed;
**Settings** shows what is still missing and how big it is.

## From a clone

For running the latest code. Clone the repository, then one command on every
platform:

```sh
python install.py            # install, answer the setup, check the machine
python install.py --start    # the same, then start the app at http://127.0.0.1:4242
```

`install.py` needs Python 3.9 or newer and nothing else. It downloads the
package manager ([uv](https://docs.astral.sh/uv/)) version that the project
pins, checks its sha256, and creates `.venv` from the locked dependency set
(`uv.lock`). On Windows and Linux it also fetches ffmpeg when none is on
`PATH`; on a Mac it asks you to run `brew install ffmpeg`. It then asks the
setup questions in the terminal and ends with the doctor's report.

| Option | What it does |
| --- | --- |
| `--data-dir DIR` | Keep the library outside the clone. A library inside the clone is deleted by `git clean -fdx`. |
| `--check` | Print the doctor's report only. |
| `--no-sync` | Skip updating `.venv`, for example while MyScribe runs from this checkout. |

Running it again is quick: an up-to-date `.venv` is left alone. After a
`git pull` that changed the dependencies, `scripts/start.ps1` and
`scripts/start.sh` tell you to run `python install.py` again.

To start MyScribe afterwards:

```sh
scripts/start.sh           # Linux, macOS  (--detached keeps it running after the terminal closes)
scripts\start.ps1          # Windows       (-Detached)
```

`.tools/` in the clone holds what `install.py` fetched: `bin/` with the
pinned uv (and ffmpeg and ffprobe on Windows and Linux, when none was on
`PATH`), plus a stamp saying which version each came from. Git ignores it.
Deleting it is safe: MyScribe then uses whatever is on `PATH`, and the next
`python install.py` fetches it again, checked against the same sha256.

What a clone uses, per platform:

| | Windows | Linux | macOS |
| --- | --- | --- | --- |
| GPU | NVIDIA (CUDA 12.8 build) | NVIDIA, otherwise CPU | Apple GPU (MLX and Metal) |
| ffmpeg | fetched, or `winget install Gyan.FFmpeg` | fetched, or `apt install ffmpeg` | `brew install ffmpeg` |
| Optional | `yt-dlp` for links, `node` for the recorder tests | same | same |

## Stopping MyScribe

Started at login, MyScribe runs without a window. Stop it from Settings >
*Start at login* > **Stop MyScribe**, or from a terminal:

- Windows: `"%LOCALAPPDATA%\Programs\MyScribe\MyScribe.exe" --stop`
- macOS: `/Applications/MyScribe.app/Contents/MacOS/MyScribe --stop`
- Linux: `./MyScribe-<version>-linux-x64.AppImage --stop`
- A clone: `.venv/Scripts/python packaging/launcher/myscribe_launcher.py --stop`
  (`.venv/bin/python` on Linux and macOS)

A job that is running goes back to the front of the queue and starts over the
next time MyScribe starts, unless it finishes while MyScribe is stopping;
queued jobs wait for that start. If MyScribe has
not stopped within a minute, `--stop` ends it, and that job is then marked
*interrupted*: Retry on the jobs page starts it over. On Windows `--stop`
prints nothing; what it did is in `MyScribe-launcher.log` beside
`MyScribe.location`.

## Updating

**A release:** download the new file and install it over the old one. Your
home, library and settings stay where they are; the next start updates the
speech engine when the new version needs it. On Windows the installer stops a
running MyScribe first and starts it again - without a window when *Start at
login* is on - and every install ends with MyScribe open in your browser.
From the 0.9.1 installer on, a running 0.9.0 or later is asked to stop the
way Quit does, and a job that was running starts over afterwards; 0.8.x has
no stop, so it is ended, and its running job is marked interrupted: Retry on
the jobs page starts it over.

On Linux the new release is a new file. Put it in the same folder as the old
one: when you start it, a *Start at login* entry that starts another
`MyScribe-<version>-linux-x64.AppImage` from that folder is pointed at the
new file. If you keep the new file somewhere else, or renamed the old one,
Settings says the entry starts another file; switch *Start at login* off and
on there so it names the new one.

**A clone:** `git pull`, then `python install.py`.

## Uninstalling

Uninstalling removes the program. Your library - recordings, transcripts,
database, models and logs - always stays, wherever it is, and so does
anything MyScribe installed for you from somebody else, such as Ollama.

**Windows** - Settings > Apps > MyScribe > Uninstall stops a running MyScribe,
then removes
`%LOCALAPPDATA%\Programs\MyScribe`, the shortcuts, the login item if
*Start at login* was on, the `myscribe` command's entry in your PATH if you
ticked that at install, and `%LOCALAPPDATA%\MyScribe.lock` and
`%LOCALAPPDATA%\MyScribe-at-login.txt`: the launcher's lock, and the note a
failed start at login leaves for the next start by hand, beside the default
home. It leaves:

- your home and library: `%LOCALAPPDATA%\MyScribe`, or the folder you chose.
  Delete it by hand when you no longer want the recordings. Beside a folder
  you chose, say `D:\Data\MyScribe`, the two small files are
  `D:\Data\MyScribe.lock` and `D:\Data\MyScribe-at-login.txt`; they stay too,
  and can go with the home.
- `%LOCALAPPDATA%\MyScribe.location`, the small file that remembers where your
  library (and a home you moved) is. Safe to delete once both are gone.
- Ollama and its models, if MyScribe installed them: they are an ordinary
  Ollama install. Remove Ollama under Settings > Apps, as
  [Ollama's own page](https://docs.ollama.com/windows) describes; its models
  live in `%HOMEPATH%\.ollama`. If MyScribe set `OLLAMA_MODELS` because the
  default drive was short of space, the models are in the folder that
  variable names, and the variable is under Settings > System > About >
  Advanced system settings > Environment Variables, for your account.

**macOS** - drag `MyScribe.app` to the Bin. Switch *Start at login* off in
Settings first; if MyScribe was started at login, that also stops it at
once. By hand: run
`launchctl bootout gui/$(id -u)/io.github.rvdbreemen.myscribe` and delete
`~/Library/LaunchAgents/io.github.rvdbreemen.myscribe.plist`. The log
launchd wrote stays in `~/Library/Logs/MyScribe`. Your
home stays in `~/Library/Application Support/MyScribe` (or the folder you
chose), with `~/Library/Application Support/MyScribe.location` beside it.
Beside the home, wherever it is, also stay `MyScribe.lock`, the launcher's
lock, and after a failed start at login `MyScribe-at-login.txt`; they can go
with the home. An
Ollama installed through MyScribe is `/Applications/Ollama.app`; its models
are in `~/.ollama`.

**Linux** - delete the AppImage. Switch *Start at login* off first. By hand:
with a systemd user session the entry is a user unit, so run
`systemctl --user disable myscribe.service` and delete
`~/.config/systemd/user/myscribe.service`; without one it is
`~/.config/autostart/myscribe.desktop`. Both are under `$XDG_CONFIG_HOME` if
you set it. Your home stays in `~/.local/share/MyScribe` (under `$XDG_DATA_HOME` if
set), with `MyScribe.location` beside it. Beside the home, wherever it is,
also stay `MyScribe.lock` and after a failed start at login
`MyScribe-at-login.txt`; they can go with the home. MyScribe never installs
Ollama on
Linux - it shows Ollama's commands and you run them - so removing it is
[Ollama's uninstall section](https://docs.ollama.com/linux).

**A clone** is a folder: delete it. A library inside it (the default without
`--data-dir`) goes with it, and `git clean -fdx` deletes it too.
