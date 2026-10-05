# Installing MyScribe

- [From a release](#from-a-release) - the recommended way
- [The first start](#the-first-start)
- [From a clone](#from-a-clone) - for developers and early adopters
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

**1. Where MyScribe keeps its files.** The first window asks for one folder -
the *home* - and shows the free space on each drive and how much will be
downloaded. The home holds the speech engine and its caches, and your library
in its `data` folder:

```
<home>\                  %LOCALAPPDATA%\MyScribe by default
├── env\  python\  cache\  bin\    the speech engine - can be downloaded again
├── logs\launcher.log
└── data\                          your library
    ├── myscribe.db
    ├── media\  models\  work\  logs\
```

On macOS the default home is `~/Library/Application Support/MyScribe`, on
Linux `~/.local/share/MyScribe`. Your choice is remembered in a small file
beside the default home, `MyScribe.location`.

The speech models (about 1.6 GB) are the one large thing that can live
outside the home. The models setup downloads go to `data\models`. A model
that was not downloaded there - when setup's download was skipped, for
instance - is fetched by the speech engine itself on first use, into the
Hugging Face cache: `~/.cache/huggingface/hub`, or wherever `HF_HUB_CACHE` or
`HF_HOME` points. MyScribe uses either copy and does not download a model
twice. To keep everything on the home's drive, let setup download the models,
or point `HF_HOME` at a folder on that drive.

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

## Updating

**A release:** download the new file and install it over the old one. Your
home, library and settings stay where they are; the next start updates the
speech engine when the new version needs it.

**A clone:** `git pull`, then `python install.py`.

## Uninstalling

Uninstalling removes the program. Your library - recordings, transcripts,
database, models and logs - always stays, wherever it is, and so does
anything MyScribe installed for you from somebody else, such as Ollama.

**Windows** - Settings > Apps > MyScribe > Uninstall removes
`%LOCALAPPDATA%\Programs\MyScribe`, the shortcuts, and the login item if
*Start at login* was on. It leaves:

- your home and library: `%LOCALAPPDATA%\MyScribe`, or the folder you chose.
  Delete it by hand when you no longer want the recordings.
- `%LOCALAPPDATA%\MyScribe.location`, the small file that remembers where your
  home is. Safe to delete once the home is gone.
- Ollama and its models, if MyScribe installed them: they are an ordinary
  Ollama install. Remove Ollama under Settings > Apps, as
  [Ollama's own page](https://docs.ollama.com/windows) describes; its models
  live in `%HOMEPATH%\.ollama`. If MyScribe set `OLLAMA_MODELS` because the
  default drive was short of space, the models are in the folder that
  variable names, and the variable is under Settings > System > About >
  Advanced system settings > Environment Variables, for your account.

**macOS** - drag `MyScribe.app` to the Bin. Switch *Start at login* off in
Settings first, or delete
`~/Library/LaunchAgents/io.github.rvdbreemen.myscribe.plist` by hand. Your
home stays in `~/Library/Application Support/MyScribe` (or the folder you
chose), with `~/Library/Application Support/MyScribe.location` beside it. An
Ollama installed through MyScribe is `/Applications/Ollama.app`; its models
are in `~/.ollama`.

**Linux** - delete the AppImage. Switch *Start at login* off first, or delete
`~/.config/autostart/myscribe.desktop` (under `$XDG_CONFIG_HOME` if you set
it). Your home stays in `~/.local/share/MyScribe` (under `$XDG_DATA_HOME` if
set), with `MyScribe.location` beside it. MyScribe never installs Ollama on
Linux - it shows Ollama's commands and you run them - so removing it is
[Ollama's uninstall section](https://docs.ollama.com/linux).

**A clone** is a folder: delete it. A library inside it (the default without
`--data-dir`) goes with it, and `git clean -fdx` deletes it too.
