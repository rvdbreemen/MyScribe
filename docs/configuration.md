# Configuration

MyScribe works without any configuration. This page covers the optional
parts: keys for speakers and AI, where files live, GPU and CPU, and the
command line.

## Keys and tokens

Most settings are made in the app, under **Settings**. Keys can also come from
the environment or from a `.env` file (in a clone: copy `.env.example` to
`.env`; in an installed MyScribe: `.env` in the home folder).

| Key | Needed for |
| --- | --- |
| `HF_TOKEN` (or `HUGGINGFACE_TOKEN`) | Recognising speakers. |
| `OPENROUTER_TOKEN` (or `OPENROUTER_API_KEY`) | AI answers through OpenRouter - one key for models from OpenAI, Anthropic, Google and others. |
| `OPENAI_API_KEY` | AI answers through OpenAI. |
| `SCRIBE_DATA_DIR` | Keeping the library in another folder (see [Where files live](#where-files-live)). |

Ollama on your own computer needs no key.

Three rules worth knowing:

- **A key set in Settings wins** over the environment. For OpenRouter the
  order is Settings, then `OPENROUTER_TOKEN`, then `OPENROUTER_API_KEY`.
- **Prefer `.env` over a machine-wide environment variable.** Every account on
  the computer can read an environment variable; only you can read your file.
- **A recording pinned *private* never goes to a cloud provider**, whatever
  keys are set. Ollama still answers, because it runs on this computer or in
  your local network ([Ollama on another computer](#ollama-on-another-computer)).

### Hugging Face token

Speaker recognition uses a pyannote model that requires a free Hugging Face
account:

1. Create a token at [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens).
2. Accept the model's conditions, with the same account, at
   [hf.co/pyannote/speaker-diarization-community-1](https://hf.co/pyannote/speaker-diarization-community-1).
3. Save the token under **Settings → Transcription**, or set `HF_TOKEN`.

Without a token a transcription still completes: the transcript has no
speakers, and says why and how to fix it. A pipeline you already have needs
no token at all - put it in `models/pyannote` inside the library.

## Ollama on another computer

Ollama normally runs on this computer. It can also run on another machine in
your local network - a PC with a bigger GPU, a NAS, a home server:

1. On that machine, let Ollama listen on the network: set the environment
   variable `OLLAMA_HOST=0.0.0.0` and restart Ollama. Allow port 11434 in its
   firewall for your local network.
2. In MyScribe, open **Settings → AI providers** and type the machine's
   address in **Ollama address**: an IP address such as `192.168.1.50`, or a
   local name such as `gpu-box.local`. The port defaults to 11434.
3. Press **Save**, then **Fetch list** and **Test now** in the Ollama row.

Only this computer and your local network are accepted: private addresses
(`10.x`, `172.16-31.x`, `192.168.x`), link-local and Tailscale addresses, and
local names (`nas`, `*.local`, `*.lan`, `*.internal`, `*.home.arpa`). An
address on the internet is refused - use OpenRouter or OpenAI for a model
there. Leave the field empty to use Ollama on this computer again.

A recording pinned *private* may be sent to an Ollama on your local network.
Its text then travels over the network in plain HTTP, so use this on a network
you trust.

## Where files live

The library is one folder. In an installed MyScribe it is the `data` folder
of the home you chose at the first start; in a clone it is `./data`, or the
folder set with `install.py --data-dir` or `SCRIBE_DATA_DIR`.

| | |
| --- | --- |
| `myscribe.db` | recordings, transcripts, jobs and settings (SQLite) |
| `media/` | the original files and their playback copies |
| `models/` | the speech and speaker models |
| `work/` | scratch space for a running job |
| `logs/` | the application log |

**Back up `myscribe.db` and `media/`.** Everything else can be downloaded or
rebuilt.

**Who else on the computer can open the library.** On Linux and macOS,
MyScribe removes access for everyone outside the folder's owner and group at
every start, and on a Mac also for the `staff` group every account is in. To
share it, for example with a media server under another account, give the
folder a group of its own (`chgrp`): MyScribe leaves that group's access
alone. A home the installed MyScribe creates is open to your account only,
and so are its `app.log` and `launcher.log`.

On Windows MyScribe changes no permissions. The default home, under
`%LOCALAPPDATA%`, is closed to other accounts. A home or library you put
anywhere else - another drive, a clone - gets that folder's permissions,
and the root of a second drive often lets every account that signs in to
the computer read and change it. Check the folder's *Security* tab if that
matters.

## GPU and CPU

| Your computer | Transcription | Speakers |
| --- | --- | --- |
| NVIDIA GPU (Windows, Linux) | Whisper on CUDA, float16 | pyannote on CUDA |
| Apple Silicon Mac | Whisper on the Apple GPU (MLX) | pyannote on the Apple GPU (Metal) |
| No supported GPU | Whisper on the CPU, int8 - several times slower than realtime | pyannote on the CPU |

**A broken NVIDIA driver stops a job instead of slowing it down.** On a
computer with an NVIDIA card that CUDA cannot reach, a transcription is
refused with a sentence naming the driver, rather than running many times
slower on the CPU without a word. If running on the CPU is what you want,
turn on **Settings → This machine → Transcribe on the CPU when the GPU is
unavailable**. A computer without an NVIDIA card uses the CPU as always.

The doctor's `accel` line shows what MyScribe will use, for example
`transcription on cuda, diarization on cuda` or, on a Mac,
`transcription on mlx, diarization on mps`.

## The command line

In a clone, run these with the environment's Python (`.venv\Scripts\python`
on Windows, `.venv/bin/python` elsewhere). They read the same library as the
app.

| Command | What it does |
| --- | --- |
| `python -m scribe` | Start the app. `--port`, `--no-supervisor`, `--no-browser`. |
| `python -m scribe.doctor` | Check this computer. `--no-gpu` skips loading a model. |
| `python -m scribe.setup` | The setup questions in the terminal. `--plan` prints what was found and what is open as JSON; `--provider`, `--tier`, `--diarize/--no-diarize` and `--fetch-models` answer without asking. |
| `python -m scribe.models --fetch` | Download the speech models. `--only <repo>` for one, `--dest` for another folder. |
| `python -m scribe.export` | Export without the browser: ids, `--all` or `--folder`, with `--preset`, `--format`, `--out` and subtitle options. `--help` lists them all. |
| `python -m scribe.proxies` | Make the exact-seeking playback copy for older recordings. `--dry-run` lists them. |

A token never goes on the command line: put it in `HF_TOKEN`, in the
environment or `.env`.

To try something without disturbing the MyScribe you use, run a second
instance on another port:

```sh
python -m scribe --port 4299 --no-supervisor --no-browser
```

## Driving MyScribe from a script

`python -m scribe.cli` is a small client for a MyScribe that is already
running. It sends every request to the JSON API at
`http://127.0.0.1:4242/api/v1` (`--port` for another port) and to nothing
else; it never opens the library itself. It needs only Python's standard
library, and ignores `HTTP_PROXY`, because a proxy cannot reach this
computer's 127.0.0.1. The full API is described at
`http://127.0.0.1:4242/openapi.json`.

`pyproject.toml` declares it as the console script `myscribe`, but the
environment does not install that command yet (`tool.uv.package = false`),
so call it as `python -m scribe.cli` with the environment's Python.

| Command | What it does |
| --- | --- |
| `status` | Whether the app answers, how many jobs run and wait, the AI provider |
| `list` | The library. `--view`, `--folder`, `--sort`, `--q`, `--label` |
| `search WORDS` | Transcript segments that contain every word |
| `upload FILE` | Send a recording in and queue its transcription. `--title`, `--folder-id`, `--option language=nl` (repeatable), `--params JSON`, `--idempotency-key KEY`; `--by-path` sends the path instead of the bytes, for a file under the browse roots |
| `transcribe ID` | Transcribe a recording again; `--option` and `--params` as for upload, none repeats the last request |
| `jobs [ID]` | Jobs, newest first (`--status`, `--limit`); with an id, that job and its events |
| `cancel ID` | Cancel a job |
| `wait ID` | Wait until a job ends. `--timeout SECONDS`, `--interval SECONDS`, `--quiet` |
| `transcript ID` | The transcript as JSON, word by word, to stdout or `--out` |
| `export ID` | Download an export: `--format srt,txt`, `--option speakers=false`, `--out` a file or folder (default the current folder), `-` for stdout |

`--json`, before the command, prints the API's answer instead of text.

```sh
python -m scribe.cli upload talk.mp3 --option language=nl --idempotency-key talk-1 --json
python -m scribe.cli wait 12 --timeout 3600 && python -m scribe.cli export 7 --format srt,txt
```

With `--idempotency-key`, running the same upload or transcribe again after
a lost answer gives the first answer back and queues nothing new.

### Exit codes

| Code | Name | When |
| --- | --- | --- |
| 0 | `OK` | It worked; for `wait`, the job is done |
| 1 | `SERVER_ERROR` | The app answered with a server error, or with something that is not the API's answer |
| 2 | `USAGE` | A wrong command line, or a file that is not there |
| 3 | `NOT_RUNNING` | Nothing answers on 127.0.0.1 at that port |
| 4 | `NOT_FOUND` | No such recording or job |
| 5 | `CONFLICT` | No transcript yet, or an idempotency key that is in use or was used for another request |
| 6 | `REFUSED` | The app refused the request itself: a wrong option, an empty search, a path outside the browse roots |
| 7 | `JOB_UNSUCCESSFUL` | `wait`: the job ended failed, cancelled or interrupted |
| 8 | `TIMEOUT` | `wait`: the job had not ended when `--timeout` ran out |

An error goes to stderr as the API's `code` and `detail`, for example
`myscribe: error 409 no_transcript: media 7 has no transcript yet`.
