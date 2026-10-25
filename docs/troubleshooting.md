# Troubleshooting

## Start with the doctor

The doctor checks this computer and says, per line, what it found. Every line
that can fail names the command that fixes it.

```sh
python -m scribe.doctor            # in a clone, with the environment's Python
python -m scribe.doctor --no-gpu   # skip loading a model
```

In an installed MyScribe the same check runs at the end of setup. A healthy
report looks like this:

```
[OK  ] python       3.12.9 at ...\.venv\Scripts\python.exe
[OK  ] sqlite       3.45.3, FTS5 available
[OK  ] ffmpeg       ffmpeg version 8.1.2-full_build-www.gyan.dev
[OK  ] ffprobe      ffprobe version 8.1.2-full_build-www.gyan.dev
[OK  ] yt-dlp       yt-dlp 2026.8.19 (31 days old)
[OK  ] data-dir     ...\data writable
[OK  ] disk-space   30.2 GB free at ...\data
[OK  ] database     schema v17 at ...\data\myscribe.db
[OK  ] accel        transcription on cuda, diarization on cuda
[OK  ] diarization  pyannote/speaker-diarization-community-1 reachable with this token
[SKIP] models       1.6 GB still to download: whisper-large-v3-turbo (1.6 GB)
                    -> Run `python -m scribe.models --fetch`.
```

## Common problems

| What you see | Why | What to do |
| --- | --- | --- |
| SmartScreen: "unknown publisher" | The installer is not signed yet. | **More info → Run anyway**. |
| macOS refuses to open the app, or calls it "damaged" | Gatekeeper blocks unsigned apps. | Start it once, then **System Settings → Privacy & Security → Open Anyway** and confirm. On macOS 14 and earlier, **Control-click → Open** also works. |
| Windows, right after installing: "untrusted mount point (os error 448)" and "Installing failed" | Up to 0.9.0, the MyScribe the installer starts runs under a Windows protection that blocks the speech engine's Python. | Close that MyScribe and start it from the Start menu. 0.9.1 and later do not have this. |
| Windows: "Windows has reserved port 4242", or up to 0.9.1 "Port 4242 is used by another program" while nothing uses it | Hyper-V, WSL, Docker or Windows Sandbox reserved a block of ports that holds 4242. This happens when the dynamic port range starts low, for example at 1024. | Run `netsh int ipv4 show excludedportrange protocol=tcp` to see the blocks. Start MyScribe with `--port` and a port outside them; a start at login always uses 4242. Or, as an administrator, run `netsh int ipv4 set dynamicport tcp start=49152 num=16384` to put the range back to the Windows default, and restart Windows. |
| A start at login could not start | MyScribe started at login without a window, so nobody saw the reason. | On Windows a message box says why at login. On every system, the next start says it once, and **Settings → Start at login** shows it until you dismiss it. |
| Linux or macOS: "Port 4242 is used by another program" just after MyScribe stopped | Up to 0.9.0, the port check counted the old server's last connections for about a minute. | Wait a minute and start again. 0.9.1 and later do not have this. |
| A transcript has no speakers, and says why | No Hugging Face token, or the model's conditions were not accepted. | Nothing is lost. Follow [Hugging Face token](configuration.md#hugging-face-token), then run the transcription again. |
| A transcription is refused with a sentence about the NVIDIA driver | CUDA cannot reach the GPU. | Update or reinstall the NVIDIA driver, or allow the CPU under **Settings → This machine**. |
| The doctor says `gpu-runtime: CPU build` (clone) | torch was installed without CUDA. | Run `python install.py` again. Plain `pip` installs a CPU-only torch on Windows. |
| `accel` says `cpu` on a Mac (clone) | `mlx-whisper` is missing. | Run `python install.py` again. |
| The doctor fails on disk space | Models and running jobs need free space. | Free space, or move the library to a larger drive. |
| Starting MyScribe a second time does nothing visible | MyScribe is already running. | It opens the browser at the running MyScribe instead. |
| Playback jumps to the wrong place in an older recording | It has no exact-seeking playback copy yet. | Run `python -m scribe.proxies`. |
| The phone cannot reach the computer | Network or firewall. | See [Receive from phone](receive-from-phone.md#if-the-phone-cannot-connect). |

## Reporting a problem

Open an issue on [GitHub](https://github.com/rvdbreemen/MyScribe/issues) with
the doctor's output and what you did. The application log is in the library's
`logs` folder and may help; look through it before you share it.
