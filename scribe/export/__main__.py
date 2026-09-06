"""`python -m scribe.export` - the command-line export. See `scribe.exports.cli`.

`.env` is read first, before anything imports `scribe.paths`, for the reason
`python -m scribe` does the same: `SCRIBE_DATA_DIR` has to be in the
environment by the time `DATA_DIR` is computed, or the command exports from
a database the app is not using. Hence the import below the call.
"""

from scribe import env

env.load_dotenv()

from scribe.exports.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
