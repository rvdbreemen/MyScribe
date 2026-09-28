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
