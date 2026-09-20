import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("SCRIBE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
# The database carries the application's name. It was `scribe.db` until
# 2026-09-06 - the package is still `scribe`, the app is MyScribe - and an
# installation from before is adopted, not abandoned: see adopt_legacy_db().
DB_PATH = DATA_DIR / "myscribe.db"
LEGACY_DB_NAME = "scribe.db"


def adopt_legacy_db() -> bool:
    """Rename a `scribe.db` from before the rename to `myscribe.db`, with its
    WAL and shared-memory sidecars, when the new file does not exist yet.

    Called by db.connect() before the default database is opened, which is
    the one moment nothing has it open: the web process connects before it
    starts the supervisor, and a runner child finds the new name already
    there. The three files move together or not at all - a `.db` without its
    `-wal` is a database missing its last transactions. Returns whether a
    rename happened. Backups (`scribe.db.backup-*`) are left where they are.
    """
    new = DB_PATH
    old = new.with_name(LEGACY_DB_NAME)
    if new.exists() or not old.exists():
        return False
    for suffix in ("-wal", "-shm", ""):
        source = old.with_name(old.name + suffix)
        if source.exists():
            source.rename(new.with_name(new.name + suffix))
    return True
MEDIA_DIR = DATA_DIR / "media"
LOGS_DIR = DATA_DIR / "logs"
# Per-job scratch: the normalized wav and anything else a pipeline run needs
# on disk but nobody needs afterwards. Deleted wholesale when a job finalizes.
WORK_DIR = DATA_DIR / "work"
# Model weights this installation keeps for itself, rather than fetching from
# somebody's hub on every fresh machine. MODELS_DIR/pyannote is a pipeline
# directory: a config.yaml plus the checkpoints it names.
MODELS_DIR = DATA_DIR / "models"


def refresh() -> None:
    """Work out DATA_DIR, and every constant above, again from the environment.

    For a command that reads `.env` itself. `python -m scribe.setup` has
    imported this module - and fixed DATA_DIR - before its first line runs, so
    a SCRIBE_DATA_DIR that only the file names arrives too late for the
    assignments above. `python -m scribe` gets round that by importing the app
    after it has read the file; setup, models and the doctor cannot, because
    their own functions use what they import. They call env.bootstrap() and
    then this, once, before main().

    A reload rather than a second list of assignments: two spellings of where
    the media lives is how they come to disagree, and a constant added above
    later is picked up without anybody remembering this function.

    It only works while nobody copies a constant out of this module at import
    - `from scribe.paths import DATA_DIR`, a default argument - and nobody
    does: every reader says `paths.DATA_DIR` at the moment it needs it.
    """
    import importlib
    import sys

    importlib.reload(sys.modules[__name__])


def ensure_dirs() -> None:
    for p in (DATA_DIR, MEDIA_DIR, LOGS_DIR, WORK_DIR, MODELS_DIR):
        p.mkdir(parents=True, exist_ok=True)


def job_work_dir(job_id: int) -> Path:
    """Scratch directory for one job; one job per directory, no sharing."""
    return WORK_DIR / str(job_id)


def remove_job_work_dir(job_id: int) -> bool:
    """Delete one job's scratch; returns whether it is actually gone.

    Never raises. Called by finalize on success and by the runner on every
    other way out (failed, cancelled, crashed stage), because scratch that
    outlives its job is ~115 MB per hour of audio and a retry gets a fresh
    id - so nothing ever came back to collect it. A file another process
    still holds open is litter for a later sweep, not a failed job.
    """
    import shutil

    work = job_work_dir(job_id)
    try:
        shutil.rmtree(work)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return not work.exists()
