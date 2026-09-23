"""`python -m scribe.setup --prove`: the report that ends an install.

Two promises are what these tests are about, and both were broken by the plan
this task corrects (TASK-089.13). The proof must not *write* to the library it
reports on - today's doctor migrates it, drops a probe file in the data
directory and records a `smoke` row - and it must not load a model beside an
app or a job that may be holding the card (ADR-001).

The no-write test is the load-bearing one. It plants a library one schema
version behind, snapshots every file's name, size and mtime plus the
directory's own mtime and `user_version`, and asserts the whole thing is
untouched afterwards. Run against the checks as they were before this task it
fails with a list of what moved; that output is the red this task started from.

The gate tests all hand `prove()` a smoke that raises. A test that asserts "no
model was loaded" by looking at the report can be satisfied by a report that
lies; one whose double raises cannot.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import types

import pytest

from scribe import db, doctor, jobs, models, paths, setup
from scribe.web import ai_ui

_REAL_GPU_RUNTIME = doctor.check_gpu_runtime
"""Captured while this file is imported, before `_quiet` has stubbed it: the
one test about a card-less machine needs the check itself."""


# --- a library of this test's own ----------------------------------------------------


def _plant(tmp_path, monkeypatch, *, behind: int = 0):
    """A library at `tmp_path/library`, `behind` schema versions old.

    Planted through `db.connect`, so the file is in WAL mode exactly as a real
    one is - and closed, so SQLite checkpoints and removes the `-wal` and
    `-shm` it used. That detail decides which branch `setup.read_only` takes:
    with no `-wal` beside it the open is `immutable=1`, which is the one
    measured to create nothing. A library planted with a plain `sqlite3.connect`
    would stay in delete-journal mode and would prove nothing about the branch
    the product uses.
    """
    data = tmp_path / "library"
    data.mkdir()
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "MODELS_DIR", data / "models")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    # conftest points the log at a directory of its own; here it goes inside
    # the library on purpose, so a log line written by the proof shows up as a
    # file that appeared.
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")

    with monkeypatch.context() as older:
        older.setattr(db, "SCHEMA_VERSION", db.SCHEMA_VERSION - behind)
        conn = db.connect(data / "myscribe.db")
        db.migrate(conn)
        conn.close()
    return data


def _snapshot(root):
    """Every path under `root`, with the three facts a write moves."""
    seen = {".": (True, root.stat().st_mtime_ns)}
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        key = str(path.relative_to(root))
        seen[key] = (
            (True, stat.st_mtime_ns) if path.is_dir() else (False, stat.st_size, stat.st_mtime_ns)
        )
    return seen


def _user_version(path):
    """The schema version, read without touching the file the way a writer would."""
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _quiet(monkeypatch):
    """The checks that reach the network, the hub cache or the card, stilled.

    None of them is this file's subject, and each is somebody else's test:
    diarization asks the Hub, models walks a cache that belongs to the machine
    running the suite, accel and gpu-runtime import torch. Ollama is already
    absent by conftest's autouse fixture.

    The serve check belongs in that list for the same reason and one more: it
    starts a real MyScribe on a scratch directory, which costs about five
    seconds and a port in every test that only wanted a report. The real one
    has tests of its own, which pass `serve=` themselves and so are not
    stilled here.
    """
    monkeypatch.setattr(doctor, "_diarization_token", lambda: None)
    monkeypatch.setattr(doctor, "_diarization_token_read_only", lambda: None)
    monkeypatch.setattr(models, "status", lambda **kwargs: [])
    monkeypatch.setattr(doctor.accel, "describe", lambda: "transcription on cpu")
    monkeypatch.setattr(
        doctor,
        "check_gpu_runtime",
        lambda: doctor.Check(name="gpu-runtime", ok=True, detail="stubbed in this test"),
    )
    monkeypatch.setattr(
        setup,
        "serve_check",
        lambda _dir, **_kw: doctor.Check(name="app", ok=True, detail="stubbed in this test"),
    )


def _no_model_here():
    """A gpu smoke that fails the test if it is called at all."""

    def smoke():
        raise AssertionError("a model was loaded; the gate let it through")

    return smoke


def _smoke_that_works():
    return lambda: doctor.Check(name="gpu-smoke", ok=True, detail="stubbed; nothing was loaded")


def _nothing_answers(port):
    return None


# --- criterion 2: the proof never writes to the library ------------------------------


def test_the_checks_a_proof_runs_leave_the_library_byte_identical(tmp_path, monkeypatch):
    """The red this task started from, and the one keyword that answers it.

    Without `read_only=True` this same test failed on 2026-09-22 with
    `assert 17 == 16` - the database check had opened the planted library
    through `db.connect`, whose `journal_mode` pragma is a write, and migrated
    it. The data-dir probe had made a file in the directory beside it. A proof
    run after a `git pull`, with an older app still serving, would have done
    that to Robert's own library.
    """
    library = _plant(tmp_path, monkeypatch, behind=1)
    _quiet(monkeypatch)
    before = _snapshot(library)
    version_before = _user_version(library / "myscribe.db")

    doctor.checks(include_gpu=False, read_only=True)

    assert _user_version(library / "myscribe.db") == version_before, "the library was migrated"
    assert _snapshot(library) == before


def test_a_whole_prove_with_the_app_down_leaves_the_library_byte_identical(tmp_path, monkeypatch):
    """The same assertions over the report a person actually runs."""
    library = _plant(tmp_path, monkeypatch, behind=1)
    _quiet(monkeypatch)
    before = _snapshot(library)
    version_before = _user_version(library / "myscribe.db")

    with setup.read_only(paths.DB_PATH) as conn:
        setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    assert _user_version(library / "myscribe.db") == version_before, "the library was migrated"
    assert _snapshot(library) == before


def test_the_data_dir_line_names_the_scratch_directory_it_measured(tmp_path, monkeypatch):
    """A green line about a temp folder must never read as a green line about
    the library: the detail says which directory was written to."""
    library = _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    line = _line(report, "data-dir")
    assert line.ok is True
    assert str(library) not in line.detail, "this measured the library after all"


def test_the_database_line_reports_the_schema_version_it_found(tmp_path, monkeypatch):
    library = _plant(tmp_path, monkeypatch, behind=1)
    _quiet(monkeypatch)

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    line = _line(report, "database")
    assert f"v{db.SCHEMA_VERSION - 1}" in line.detail
    assert line.ok is True, "a library the app will migrate on its next start is not broken"


def test_a_library_from_a_newer_myscribe_is_not_reported_as_healthy(tmp_path, monkeypatch):
    """Behind is a state this twin forgives; ahead is not one it may.

    `check_database` fails any version that is not `SCHEMA_VERSION`. The
    read-only twin relaxes that downwards on purpose - the app migrates a
    library one version behind the next time it starts, and saying so is the
    answer. Upwards there is nothing to relax: `db.migrate` walks
    `range(version + 1, SCHEMA_VERSION + 1)`, which is empty going down, so a
    library written by a newer MyScribe is one this checkout would open
    without knowing its schema. Reachable by running a newer MyScribe once and
    then proving from an older checkout.
    """
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    newer = db.SCHEMA_VERSION + 1
    conn = sqlite3.connect(paths.DB_PATH)
    try:
        conn.execute("PRAGMA user_version = %d" % newer)
        conn.commit()
    finally:
        conn.close()

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    line = _line(report, "database")
    assert line.ok is False, "a library this checkout cannot migrate is not a pass"
    assert "v%d" % newer in line.detail
    assert "migrates it" not in line.detail, "nothing migrates a library downwards"


def _plant_with_a_wal(tmp_path, *, shm):
    """A library with a `-wal` standing beside it and no writer connected.

    The shape `_plant` cannot make: closing the last connection checkpoints the
    log away and removes it. So the files are copied out from under an open
    writer, which is what a killed app leaves behind - and what the data
    directory of this repository holds today. `shm=False` is the half-copied
    variant, a `.db` and a `-wal` without the index that belongs to them.

    The row written after the checkpoint lives only in the `-wal`, so a read
    that misses it can be told from one that does not.
    """
    work = tmp_path / "work"
    work.mkdir()
    conn = sqlite3.connect(work / "myscribe.db")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE setting(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO setting VALUES ('provider', 'checkpointed')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("UPDATE setting SET value='written since the checkpoint'")
        conn.execute("PRAGMA user_version = %d" % db.SCHEMA_VERSION)
        conn.commit()
        library = tmp_path / "library"
        library.mkdir()
        names = ["myscribe.db", "myscribe.db-wal"] + (["myscribe.db-shm"] if shm else [])
        for name in names:
            shutil.copy2(work / name, library / name)
    finally:
        conn.close()
    return library / "myscribe.db"


def _bytes_of(path):
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())


@pytest.mark.parametrize("shm", [True, False])
def test_a_library_with_a_wal_keeps_its_own_bytes_and_is_read_fresh(tmp_path, shm):
    """The exact scope of "never writes to the live library", measured.

    Where no `-wal` stands, `read_only` opens `immutable=1` and touches
    nothing - the branch every other test in this file runs in. Where one
    does, the open is a plain `mode=ro`, and SQLite writes the wal-index: it
    rewrites an existing `-shm` and creates a missing one. Measured on this
    machine on 2026-09-22 and pinned here, so that exception is a known one
    rather than a surprise on the first live run.

    The database and the log itself stay byte-identical, which is the promise
    that holds. The first assertion is why this branch exists at all: the row
    written after the checkpoint comes back, where `immutable=1` answers with
    the state before it and reports a schema version of 0.
    """
    path = _plant_with_a_wal(tmp_path, shm=shm)
    wal = path.with_name(path.name + "-wal")
    index = path.with_name(path.name + "-shm")
    before = {p.name: _bytes_of(p) for p in sorted(path.parent.iterdir())}

    with setup.read_only(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        said = conn.execute("SELECT value FROM setting WHERE key='provider'").fetchone()[0]

    assert version == db.SCHEMA_VERSION and said == "written since the checkpoint", (
        "the read missed what the -wal holds; immutable=1 answers like this"
    )
    assert _bytes_of(path) == before["myscribe.db"], "the database itself was written to"
    assert _bytes_of(wal) == before["myscribe.db-wal"], "the write-ahead log was written to"
    appeared = {p.name for p in path.parent.iterdir()} - set(before)
    assert appeared == (set() if shm else {"myscribe.db-shm"}), (
        "something other than the wal-index appeared: %s" % appeared
    )
    if shm:
        assert _bytes_of(index) != before["myscribe.db-shm"], (
            "the -shm was left alone after all; this test records the exception, "
            "so a read that stopped needing it should stop claiming it"
        )


def test_a_machine_with_no_library_yet_is_not_a_failure(tmp_path, monkeypatch):
    """The first run of all: nothing has been created, and that is the answer."""
    data = tmp_path / "library"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "DB_PATH", data / "myscribe.db")
    monkeypatch.setattr(paths, "LOGS_DIR", data / "logs")
    _quiet(monkeypatch)

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    line = _line(report, "database")
    assert line.ok is True and "no library yet" in line.detail
    assert not data.exists(), "the proof created the data directory"


# --- criterion 1: one report, in one order, and never a false green -------------------


def _line(report, name):
    for check in report:
        if check.name == name:
            return check
    raise AssertionError(f"no line named {name!r} in: {[c.name for c in report]}")


def _order(report, name):
    return [c.name for c in report].index(name)


def test_the_report_carries_every_line_in_the_order_the_criterion_names(tmp_path, monkeypatch):
    """environment, ffmpeg and ffprobe, the CPU checks, accel, the credentials,
    Ollama, the provider, transcription, the app, the log path."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    named = [
        "environment",
        "ffmpeg",
        "ffprobe",
        "database",
        "accel",
        "huggingface",
        "ollama",
        "ai-provider",
        "transcription",
        "app",
        "log",
    ]
    places = [_order(report, name) for name in named]
    assert places == sorted(places), [c.name for c in report]
    assert "found at" in _line(report, "ffmpeg").detail or not _line(report, "ffmpeg").ok, (
        "an ffmpeg that was found says where it came from"
    )
    assert str(paths.LOGS_DIR) in _line(report, "log").detail
    assert not paths.LOGS_DIR.exists(), "the log path is named, not written"


def test_a_credential_line_names_its_source_and_never_a_value(tmp_path, monkeypatch):
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    monkeypatch.setenv("HF_TOKEN", "hf_a_value_that_must_not_be_printed")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    line = _line(report, "huggingface")
    assert line.ok is True and "HF_TOKEN" in line.detail
    assert "hf_a_value_that_must_not_be_printed" not in "\n".join(c.detail for c in report)


def test_an_untested_line_never_reads_ok_and_exits_one(tmp_path, monkeypatch, capsys):
    """Criterion 1's rule, at the two places it is visible: the mark and the
    exit code. `transcription` is the required line the gate can leave
    untested, so a proof beside a running app exits 1 on purpose."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    conn = db.connect(paths.DB_PATH)
    try:
        jobs.enqueue(conn, "transcribe")
        conn.execute("UPDATE job SET status='running'")
        conn.commit()
    finally:
        conn.close()

    code = setup.main(["--prove"])

    out = capsys.readouterr().out
    assert code == 1, out
    assert "[----] transcription" in out
    assert "not tested" in out
    assert "[OK  ] transcription" not in out
    # The closing line itself, and not only the mark: a machine nobody measured
    # is counted in a sentence of its own. "not tested" stands in the
    # transcription detail as well, so asserting those two words guards nothing
    # about the summary.
    assert "1 required check(s) not tested" in out
    assert "required check(s) failed" not in out
    # The driver question was withheld for the same reason and reads the same
    # way, and it is not counted a second time: `transcription` carries that
    # fact into the verdict once.
    assert "[----] gpu-runtime" in out
    # The skip roll-up is for optional lines that were asked and answered no.
    # A line the gate never asked would be noise in it.
    closing = out.strip().splitlines()[-1]
    assert "blind-spot" not in closing and "not looked for" not in closing, closing


def test_a_report_with_every_required_line_ok_exits_zero(tmp_path, monkeypatch, capsys):
    library = _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    monkeypatch.setattr(doctor, "check_gpu_smoke_read_only", _smoke_that_works())
    monkeypatch.setattr(setup, "_health", _nothing_answers)

    before = _snapshot(library)
    version_before = _user_version(library / "myscribe.db")

    code = setup.main(["--prove"])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "[OK  ] transcription" in out
    # The promise pinned at the command somebody types, and not only at the
    # function: `main` is where `ensure_dirs` and `db.connect` stand a few
    # lines below, and a `--prove` branch that slipped under them would leave
    # every other test in this file green.
    assert _user_version(library / "myscribe.db") == version_before, "the library was migrated"
    assert _snapshot(library) == before


def test_a_check_may_not_claim_to_be_ok_and_untested_at_once():
    """The invariant behind "never 'ok'": the dataclass refuses the pair."""
    with pytest.raises(ValueError):
        doctor.Check(name="transcription", ok=True, tested=False, detail="both at once")


# --- criteria 3, 4 and 9: what may load a model ---------------------------------------


def test_a_running_job_row_loads_nothing_and_asks_no_port(tmp_path, monkeypatch):
    """Criterion 4: the cheap half of the guard. No port is probed, because a
    job that is running is reason enough."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    conn = db.connect(paths.DB_PATH)
    try:
        jobs.enqueue(conn, "transcribe")
        conn.execute("UPDATE job SET status='running'")
        conn.commit()
    finally:
        conn.close()

    def refuse(port):
        raise AssertionError("a port was probed; the running-job check needs none")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_no_model_here(), health=refuse)

    line = _line(report, "transcription")
    assert line.tested is False and line.ok is False
    assert line.detail == "not tested (a job is running)"
    # The app line says so in its own words. Without this it could regress to
    # "nothing answers on port 4242" - a report about a port nobody probed,
    # which is the dishonesty criterion 4's last sentence is about.
    assert _line(report, "app").detail == "not tested (a job is running; no port was asked)"


def test_the_report_says_what_it_cannot_see(tmp_path, monkeypatch):
    """Criterion 4's last sentence: the blind spot is stated, not implied."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    said = "\n".join(check.detail for check in report)
    assert "another port" in said
    # And the other half of what "nothing answers" can mean: `_health` reads
    # any answer that is not a MyScribe /health as nothing there, which opens
    # the gate. Said, rather than left for somebody to discover.
    assert "not a MyScribe" in said


def test_an_app_serving_this_library_gets_the_doctor_job(tmp_path, monkeypatch):
    """Criterion 3's match case: queue it the way Settings does, name the job,
    and load nothing here."""
    library = _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def answers(port):
        return {"ok": True, "version": "9.9.9", "app_dir": "anywhere", "data_dir": str(library)}

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, port=4242, smoke=_no_model_here(), health=answers)

    line = _line(report, "transcription")
    assert line.tested is False and line.ok is False
    conn = db.connect(paths.DB_PATH)
    try:
        row = conn.execute("SELECT id, type FROM job ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        conn.close()
    assert row["type"] == doctor.JOB_TYPE
    assert f"doctor job {row['id']}" in line.detail
    assert "Settings" in line.detail
    # The driver question rides the same job, so the answer is not lost - it
    # comes back where the page reads it.
    assert "Settings" in _line(report, "gpu-runtime").detail
    assert _line(report, "app").ok is True


def test_an_app_serving_another_library_queues_nothing(tmp_path, monkeypatch):
    """Criterion 3's mismatch case. A queued row is only honest in the library
    the answering app serves."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def answers(port):
        return {"ok": True, "version": "9.9.9", "app_dir": "elsewhere", "data_dir": str(tmp_path / "somewhere-else")}

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, port=4299, smoke=_no_model_here(), health=answers)

    line = _line(report, "transcription")
    assert line.tested is False
    assert line.detail == "not tested (a MyScribe is running on port 4299)"
    conn = db.connect(paths.DB_PATH)
    try:
        assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 0
    finally:
        conn.close()


def test_an_answer_without_the_fields_is_doubt(tmp_path, monkeypatch):
    """Today's `/health` carries only `ok` and `version`, so this is the live
    branch until TASK-089.17 lands. Doubt reads the same as a mismatch."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def answers(port):
        return {"ok": True, "version": "0.5.1"}

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, port=4242, smoke=_no_model_here(), health=answers)

    line = _line(report, "transcription")
    assert line.tested is False
    assert line.detail == "not tested (a MyScribe is running on port 4242)"
    conn = db.connect(paths.DB_PATH)
    try:
        assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_data_dir_that_differs_only_in_spelling_still_matches(tmp_path, monkeypatch):
    """`os.path.normcase(os.path.realpath(...))` on both sides, the way
    TASK-089.17 criterion 9 compares paths: a Windows app that answers with a
    different case serves the same library."""
    library = _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def answers(port):
        return {"ok": True, "version": "9.9.9", "app_dir": "anywhere", "data_dir": str(library).upper()}

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_no_model_here(), health=answers)

    if sys.platform != "win32":
        pytest.skip("case-insensitive paths are what this is about")
    assert "doctor job" in _line(report, "transcription").detail


def test_with_nothing_answering_the_smoke_runs_here(tmp_path, monkeypatch):
    """Criterion 9. With no app and no job, nothing else holds the card, and
    this process measures exactly as `python -m scribe.doctor` does."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    ran = []

    def smoke():
        ran.append(True)
        return doctor.Check(name="gpu-smoke", ok=True, detail="turbo on cuda: 41 words")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=smoke, health=_nothing_answers)

    assert ran == [True]
    line = _line(report, "transcription")
    assert line.ok is True and line.tested is True
    assert "41 words" in line.detail


# --- criterion 5: the verdict about a card is inherited, not re-formed ----------------


def test_a_machine_with_no_nvidia_card_is_not_declared_broken(tmp_path, monkeypatch):
    """TASK-089.12 decided this; the proof inherits it. Nobody has run this on
    a real machine without NVIDIA hardware - the first will be the GitHub
    runner of TASK-089.24."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    monkeypatch.setattr(doctor, "check_gpu_runtime", _REAL_GPU_RUNTIME)
    module = types.ModuleType("torch")
    module.__version__ = "2.10.0+cu128"
    module.version = types.SimpleNamespace(cuda="12.8")
    module.cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "torch", module)
    monkeypatch.setattr(doctor.cuda_setup, "ensure_cuda_libs", lambda: None)
    monkeypatch.setattr(doctor.accel, "nvidia_hardware_present", lambda: False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers)

    assert [c.name for c in report if not c.ok and not c.optional] == []
    assert "transcription on cpu" in _line(report, "gpu-runtime").detail


# --- criterion 6: a cloud provider costs money ----------------------------------------


def test_the_gpu_runtime_line_waits_on_the_same_gate(tmp_path, monkeypatch):
    """`check_gpu_runtime` asks the driver, and that is work with a gate on it.

    It is a member of `GPU_CHECKS`, which is this repository's own word for
    "not in this process": `checks(include_gpu=False)` drops it and the
    settings page queues it as a `doctor` job rather than running it.
    `check_accelerators` one line above answers with `torch.cuda.is_available()`
    and stays ungated; this one reaches `get_device_name(0)` and
    `get_device_properties(0)`, which go through torch's lazy init and open a
    CUDA context on device 0. Beside a transcription that is holding the card,
    ADR-001 says that belongs in a runner child.
    """
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def refuse():
        raise AssertionError("the driver was asked while a job was running")

    monkeypatch.setattr(doctor, "check_gpu_runtime", refuse)
    conn = db.connect(paths.DB_PATH)
    try:
        jobs.enqueue(conn, "transcribe")
        conn.execute("UPDATE job SET status='running'")
        conn.commit()
    finally:
        conn.close()

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_no_model_here(), health=_nothing_answers)

    line = _line(report, "gpu-runtime")
    assert line.tested is False and line.ok is False
    assert line.detail == "not tested (a job is running)"


def test_the_card_is_free_when_the_smoke_loads(tmp_path, monkeypatch):
    """Report order is not execution order, and here they differ on purpose.

    A local provider answers on the same card and ollama keeps the model it
    answered with resident (`KEEP_ALIVE = "5m"`, chosen so the card is free
    again well before the next transcription). Asked first it would still hold
    VRAM seconds later when the smoke loads - the collision this repository
    measured in scribe/llm/ollama.py, where the 9B and the 12B both failed to
    start their runner while 12.7 GB of the 16 GB card was in use. So the
    smoke is measured first, and the report still reads in criterion 1's
    order.
    """
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    conn = db.connect(paths.DB_PATH)
    try:
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "ollama")
    finally:
        conn.close()
    ran = []

    def smoke():
        ran.append("transcription")
        return doctor.Check(name="gpu-smoke", ok=True, detail="stubbed; nothing was loaded")

    def probe(conn, provider):
        ran.append("ai-provider")
        return doctor.Check(name="ai-provider", ok=True, optional=True, detail="one word")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(
            conn, smoke=smoke, health=_nothing_answers, provider_probe=probe
        )

    assert ran == ["transcription", "ai-provider"], "the provider held the card first"
    assert _order(report, "ai-provider") < _order(report, "transcription"), (
        "the report still reads in criterion 1's order"
    )


def test_a_provider_this_version_does_not_know_is_a_line_and_not_a_traceback(tmp_path, monkeypatch):
    """Every other line in this report degrades into a sentence; so does this.

    `llm.provider_class` raises `ValueError` by design for a name it does not
    have, and the row can hold one: written by a newer MyScribe, or a provider
    dropped from `PROVIDERS` since. A report whose whole job is to always
    print one may not die on it - and it may not ask a question about it
    either, because there is nothing to ask.
    """
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    conn = db.connect(paths.DB_PATH)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO setting(key, value) VALUES (?, ?)",
            (ai_ui.PROVIDER_SETTING, "thumb-of-a-passing-starship"),
        )
        conn.commit()
    finally:
        conn.close()

    def refuse_question(question):
        raise AssertionError("a question was asked about a provider that does not exist")

    def refuse_probe(conn, provider):
        raise AssertionError("an unknown provider was probed")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(
            conn,
            smoke=_smoke_that_works(),
            health=_nothing_answers,
            provider_probe=refuse_probe,
            confirm=refuse_question,
        )

    line = _line(report, "ai-provider")
    assert line.tested is False and line.optional is True
    assert "thumb-of-a-passing-starship" in line.detail


def test_the_log_line_names_the_one_write_the_queued_job_makes(tmp_path, monkeypatch):
    """`jobs.enqueue` appends `job.enqueued` to the library's own log.

    That is inside criterion 2's exception - the app that is serving holds the
    file open already, and a queue that leaves no trace in the log is worse
    than one that does - but the `log` line may not say "not written" on the
    one branch where something is. The assertion is the file, not the wording
    alone: a disclosure nobody checked is how this started.
    """
    library = _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def answers(port):
        return {"ok": True, "version": "9.9.9", "app_dir": "anywhere", "data_dir": str(library)}

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(conn, smoke=_no_model_here(), health=answers)

    line = _line(report, "log")
    assert "not written" not in line.detail
    assert "job.enqueued" in line.detail
    assert paths.LOGS_DIR.exists(), "the line disclosed a write that did not happen"


# --- criterion 6: the default that keeps the money in somebody's pocket ---------------


def test_a_question_nobody_is_there_to_answer_is_a_no(monkeypatch):
    """The production seam, not the gate that uses it.

    Every test above hands `prove()` a `confirm` double, which pins the
    branching and leaves the function an unattended install actually reaches
    untouched. Without a terminal it must answer No and it must not read
    stdin: an install that blocked on input would hang, and one that took
    silence for yes would spend money nobody agreed to.
    """
    monkeypatch.setattr(setup, "at_a_terminal", lambda *args, **kwargs: False)

    def refuse(prompt=""):
        raise AssertionError("an unattended install was asked a question")

    monkeypatch.setattr("builtins.input", refuse)

    assert setup._confirm(setup.PROVIDER_QUESTION.format(label="OpenRouter")) is False


@pytest.mark.parametrize(
    "typed, spends",
    [("y", True), ("Y", True), ("yes", True), (" YES ", True),
     ("", False), ("n", False), ("no", False), ("maybe", False), ("later", False)],
)
def test_only_a_typed_yes_spends_money(monkeypatch, typed, spends):
    """Anything that is not yes is No - the empty line above all, because that
    is what a hurried person presses."""
    monkeypatch.setattr(setup, "at_a_terminal", lambda *args, **kwargs: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": typed)

    assert setup._confirm("ask?") is spends


def test_a_terminal_that_goes_away_mid_question_is_a_no(monkeypatch):
    """stdin closed under a prompt raises EOFError, and a bill is not the
    right answer to that."""
    monkeypatch.setattr(setup, "at_a_terminal", lambda *args, **kwargs: True)

    def closed(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", closed)

    assert setup._confirm("ask?") is False


def test_a_cloud_provider_is_not_asked_for_its_word_by_default(tmp_path, monkeypatch):
    """No is the default, and nothing reaches the provider. The double raises,
    so a request would be a red test rather than a bill."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    conn = db.connect(paths.DB_PATH)
    try:
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "openrouter")
    finally:
        conn.close()

    def refuse(conn, provider):
        raise AssertionError("a paid request was made")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(
            conn,
            smoke=_smoke_that_works(),
            health=_nothing_answers,
            provider_probe=refuse,
            confirm=lambda question: False,
        )

    line = _line(report, "ai-provider")
    assert line.tested is False and line.optional is True
    assert "configured, not tested" in line.detail


def test_a_cloud_provider_is_asked_on_an_explicit_yes(tmp_path, monkeypatch):
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    conn = db.connect(paths.DB_PATH)
    try:
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "openrouter")
    finally:
        conn.close()
    asked = []

    def probe(conn, provider):
        asked.append(provider)
        return doctor.Check(name="ai-provider", ok=True, optional=True, detail="OpenRouter said: ok")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(
            conn,
            smoke=_smoke_that_works(),
            health=_nothing_answers,
            provider_probe=probe,
            confirm=lambda question: True,
        )

    assert asked == ["openrouter"]
    assert _line(report, "ai-provider").ok is True


def test_no_provider_chosen_is_a_state_and_not_a_failure(tmp_path, monkeypatch):
    """ADR-016: a missing row selects no provider and nothing is sent."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def refuse(conn, provider):
        raise AssertionError("a request was made with no provider chosen")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(
            conn,
            smoke=_smoke_that_works(),
            health=_nothing_answers,
            provider_probe=refuse,
            confirm=lambda question: True,
        )

    line = _line(report, "ai-provider")
    assert line.optional is True and "no provider" in line.detail


def test_a_local_provider_follows_the_same_gate_as_the_smoke(tmp_path, monkeypatch):
    """Criterion 3's last sentence: the one-word probe of a ready Ollama is on
    the same card, so a running job stops it too."""
    _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    conn = db.connect(paths.DB_PATH)
    try:
        ai_ui.setting_put(conn, ai_ui.PROVIDER_SETTING, "ollama")
        jobs.enqueue(conn, "transcribe")
        conn.execute("UPDATE job SET status='running'")
        conn.commit()
    finally:
        conn.close()

    def refuse(conn, provider):
        raise AssertionError("the local provider was asked beside a running job")

    with setup.read_only(paths.DB_PATH) as conn:
        report = setup.prove(
            conn,
            smoke=_no_model_here(),
            health=lambda port: None,
            provider_probe=refuse,
            confirm=lambda question: True,
        )

    line = _line(report, "ai-provider")
    assert line.tested is False and "a job is running" in line.detail


def test_the_app_line_starts_one_where_it_can_do_no_harm(tmp_path, monkeypatch):
    """Criterion 2 allows a serve check, on a scratch SCRIBE_DATA_DIR.

    Until now the line read "not tested" and handed the person a command to
    run themselves, which proves nothing about the install that just finished
    - and "the install ends on a proof" is this task's title. The app is
    started on an empty directory and a port nobody was using, asked for
    /health, and stopped.

    The library must not learn that this happened: the app's own startup
    migrates the database, reconciles job rows and queues a speaker pass for
    every diarized recording that never had one, which is exactly why this may
    not happen on the real one.
    """
    library = _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)
    before = _snapshot(library)
    served: list[Path] = []

    def serve(data_dir: Path) -> doctor.Check:
        served.append(data_dir)
        return doctor.Check(name="app", ok=True, detail=f"served from {data_dir}")

    with setup.read_only(library / "myscribe.db") as conn:
        report = setup.prove(conn, smoke=_smoke_that_works(), health=_nothing_answers, serve=serve)

    line = _line(report, "app")
    assert line.ok is True and line.tested is True
    assert served and served[0].resolve() != library.resolve(), "it served from the real library"
    assert _snapshot(library) == before, "the library learned that this happened"


def test_a_serve_check_is_not_started_beside_a_running_app(tmp_path, monkeypatch):
    """The same gate that withholds the model withholds this: starting a
    second app on this library would migrate and sweep it while the first one
    is serving from it."""
    library = _plant(tmp_path, monkeypatch)
    _quiet(monkeypatch)

    def refuse(_data_dir: Path) -> doctor.Check:
        raise AssertionError("a serve check was started beside a running app")

    with setup.read_only(library / "myscribe.db") as conn:
        report = setup.prove(
            conn,
            smoke=_smoke_that_works(),
            health=lambda _port: {"ok": True, "version": "0.5.1"},
            serve=refuse,
        )

    assert _line(report, "app").ok is True  # the one that answers is the answer


def test_the_real_serve_check_starts_an_app_and_stops_it(tmp_path):
    """The one test that lets `serve_check` do what it does, because every
    other test in this file stills it.

    What it proves is what an install's last line claims: a MyScribe built by
    this environment starts, answers /health, and is gone afterwards. It gets
    an empty directory of its own and a port the operating system picked, so
    it can disturb neither the library nor an app on 4242.
    """
    scratch = tmp_path / "somewhere-empty"
    scratch.mkdir()

    line = setup.serve_check(scratch, timeout=120.0)

    assert line.ok is True, line.detail
    assert line.name == "app" and line.tested is True
    assert "/health" in line.detail
    # It served from the folder it was given, and left a library there rather
    # than anywhere else - which is the whole reason the folder exists.
    assert (scratch / "myscribe.db").exists(), sorted(p.name for p in scratch.iterdir())
