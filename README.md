# MyScribe

**MyScribe is a local-first transcription program with privacy by design.**

- **Transcription and speaker detection always run locally**, on your own
  computer. Your audio never leaves it.
- **Post-processing is optional, and your choice.** Label your recordings, run
  an analysis - a summary, action points, a cleaned-up reading - or chat with
  a transcript, using local models through
  [Ollama](https://ollama.com) - on this computer or on another machine in
  your local network. Or, if you prefer, use OpenRouter or OpenAI as a
  backend in the public cloud.

MyScribe runs in your browser at `http://127.0.0.1:4242` and keeps everything
in one library on your disk. It runs on Windows, macOS (Apple Silicon) and
Linux.

---

## What it does

| | |
| --- | --- |
| **Transcribe anything** | A file, a pasted link, a podcast feed, a YouTube channel or playlist, a watched folder, a recording made in the browser, or a file sent from your phone. |
| **Know who is speaking** | Speaker detection on the GPU, then an AI pass that names speakers from what they say. A name you type is never overwritten. |
| **Get the words right** | A glossary of your names and terms is applied to every transcript, and a word you correct by hand becomes a rule for next time. |
| **Keep a library** | Folders, labels, full-text search, bulk actions, and a *private* pin that keeps a recording's text on this machine. |
| **Play back exactly** | Click a word to hear it; the highlight follows the audio. |
| **Export** | TXT, Markdown, SRT, VTT, DOCX, CSV, JSON and a standalone HTML page, with presets - from the app or the command line. |
| **Ask questions** | Summaries, action points, a cleaned-up reading and a chat, through Ollama on your machine or a cloud provider you pick. |

Transcription uses Whisper (`large-v3-turbo` by default). On an NVIDIA GPU it
runs many times faster than realtime; on an Apple Silicon Mac it uses the
Apple GPU; without a GPU it runs on the CPU, more slowly.

## Get started

### 1. Install

Download the file for your computer from the
[latest release](https://github.com/rvdbreemen/MyScribe/releases/latest):

| Your computer | Download |
| --- | --- |
| Windows (x64) | `MyScribe-<version>-windows-x64.exe` |
| Mac with Apple Silicon | `MyScribe-<version>-macos-arm64.dmg` |
| Linux (x86_64) | `MyScribe-<version>-linux-x64.AppImage` |

The downloads are not signed yet, so your system warns you the first time.
[Installing MyScribe](docs/installation.md) shows what to click on each system,
how to verify a download, and how to install from source.

### 2. First start

The first start asks where MyScribe should keep its files, shows the free
space on each drive, and downloads the speech engine (a few GB) with a
progress bar. Then a short setup walks you through what is still open, one
step at a time:

1. **Your library** - start a new one, or use a MyScribe library you already
   have. MyScribe shows what it found before anything changes.
2. **AI answers** - Ollama on this computer, or a cloud provider. Without
   Ollama, MyScribe can install it for you, showing the download first.
3. **Transcription quality** - *Turbo* (the fast default) or *Maximum*.
4. **A folder to watch** for new recordings, and whether MyScribe **starts
   when you log in**.

Every step has **Skip**, and everything can be changed later in **Settings**.

### 3. Transcribe

Open **Transcribe**, drop in a file or paste a link, and press start. The job
page shows the text as it is written. When it is done, the recording opens
with its transcript, speakers and the AI panel beside it.

## Everyday use

- **From your phone** - press **Receive from phone** in the library, scan the
  QR code, and send a recording over your own Wi-Fi.
  [How it works](docs/receive-from-phone.md).
- **Speakers** need a free Hugging Face token once. Without it, a transcript
  is complete but has no speakers, and says how to fix that.
  [Configuration](docs/configuration.md#hugging-face-token).
- **Is this computer set up right?** Run the doctor: every line that fails
  names the command that fixes it. [Troubleshooting](docs/troubleshooting.md).

## Your data

Everything lives in one library folder:

| | |
| --- | --- |
| `myscribe.db` | recordings, transcripts, jobs and settings |
| `media/` | your original files and their playback copies |
| `models/` | the speech models |
| `work/`, `logs/` | scratch space and the application log |

**Back up `myscribe.db` and `media/`**; everything else can be downloaded or
rebuilt. Uninstalling MyScribe never deletes your library.

## Privacy

MyScribe listens only on `127.0.0.1` and refuses requests from any other name,
so other computers - and web pages you happen to visit - cannot reach your
library. Audio is transcribed on your machine. Text goes to a cloud AI
provider only when you choose one, and never for a recording pinned
*private*: that text stays on this computer or, when Ollama runs on another
machine in your local network, within that network. *Receive from phone* is the one exception: a temporary, upload-only
door that closes by itself - see [its page](docs/receive-from-phone.md).

## Documentation

| Guide | For |
| --- | --- |
| [Installing MyScribe](docs/installation.md) | Downloads, first start, installing from source, updating, uninstalling |
| [Configuration](docs/configuration.md) | Keys and tokens, where files live, GPU and CPU, the command line |
| [Receive from phone](docs/receive-from-phone.md) | Sending a recording from a phone to MyScribe |
| [Troubleshooting](docs/troubleshooting.md) | The doctor, and what to do when something is wrong |
| [Development](docs/development.md) | Running from source, tests, architecture and releases |

What changed in each version is in the [changelog](CHANGELOG.md).
