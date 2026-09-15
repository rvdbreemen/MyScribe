"""`python -m scribe.proxies [--dry-run]` - make the exact-seeking copy of
every recording that needs one and has none yet. See `scribe.playback`.

`.env` is read first, before anything imports `scribe.paths`, for the reason
`python -m scribe.export` does the same: `SCRIBE_DATA_DIR` has to be in the
environment by the time `DATA_DIR` is computed, or the command fills the
proxies of a library the app is not using. Hence the import below the call.
"""

from scribe import env

env.load_dotenv()

from scribe.playback import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
