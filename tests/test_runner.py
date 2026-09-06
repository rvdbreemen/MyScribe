import threading
import time

import pytest

from scribe import db, jobs, paths, runner


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Tmp DB that runner.main() also finds via the default paths.DB_PATH."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


def _job_row(conn, job_id):
    return conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()


# --- scenario ok --------------------------------------------------------------


def test_ok_scenario_runs_stages_in_order_to_done(conn):
    job_id = jobs.enqueue(conn, "fake", params={"scenario": "ok"})
    jobs.claim_next(conn)  # supervisor claims before spawning the child

    assert runner.main([str(job_id)]) == 0

    row = _job_row(conn, job_id)
    assert row["status"] == "done"
    assert row["finished_at"] is not None

    events = jobs.events_after(conn, job_id, 0)
    assert len(events) >= 3
    stage_names = [e["payload"]["name"] for e in events if e["kind"] == "stage"]
    assert stage_names == ["one", "two", "three"]

    perf_stages = [
        r["stage"]
        for r in conn.execute("SELECT stage FROM stage_perf ORDER BY id").fetchall()
    ]
    assert perf_stages == ["one", "two", "three"]


def test_report_throttles_set_stage_writes(conn, monkeypatch):
    real_set_stage = jobs.set_stage
    calls: list[tuple[str, float]] = []

    def counting(c, job_id, stage, progress):
        calls.append((stage, progress))
        real_set_stage(c, job_id, stage, progress)

    monkeypatch.setattr(jobs, "set_stage", counting)

    job_id = jobs.enqueue(conn, "fake", params={"scenario": "ok"})
    jobs.claim_next(conn)
    assert runner.main([str(job_id)]) == 0

    # fake stages tick progress within microseconds; the >=0.4 s throttle
    # collapses those, leaving only the unconditional per-stage entry write
    assert [c[0] for c in calls] == ["one", "two", "three"]


# --- scenario boom ------------------------------------------------------------


def test_boom_scenario_fails_with_runtime_and_trace_event(conn):
    job_id = jobs.enqueue(conn, "fake", params={"scenario": "boom"})
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    row = _job_row(conn, job_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "RUNTIME"
    assert row["error_detail"]

    errors = [e for e in jobs.events_after(conn, job_id, 0) if e["kind"] == "error"]
    assert errors
    assert "RuntimeError" in errors[-1]["payload"]["trace"]

    # stage one completed before the boom in stage two; only it has perf
    perf_stages = [
        r["stage"]
        for r in conn.execute("SELECT stage FROM stage_perf ORDER BY id").fetchall()
    ]
    assert perf_stages == ["one"]


# --- scenario slow + cancel ---------------------------------------------------


def test_slow_scenario_cancel_mid_run_exits_cancelled(conn):
    job_id = jobs.enqueue(conn, "fake", params={"scenario": "slow"})
    jobs.claim_next(conn)

    result: dict[str, int] = {}
    t = threading.Thread(
        target=lambda: result.setdefault("rc", runner.main([str(job_id)]))
    )
    t.start()

    # wait until the runner has entered a stage, then request cancel
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _job_row(conn, job_id)["stage"] is not None:
            break
        time.sleep(0.05)
    else:
        pytest.fail("runner never entered a stage")

    jobs.request_cancel(conn, job_id)
    t.join(timeout=10.0)
    assert not t.is_alive(), "runner did not honour the cancel flag"

    assert result["rc"] == 2
    row = _job_row(conn, job_id)
    assert row["status"] == "cancelled"
    assert row["finished_at"] is not None


# --- what a stage_perf row is worth -------------------------------------------
#
# Both of these are about `eta_seconds`, which divides wall time by media
# duration and keys the rolling median on (stage, model). A row with the wrong
# duration or the wrong model is not a slightly-off sample; it is a sample that
# either gets discarded outright or drags a different model's estimate.


def test_stage_perf_records_the_duration_the_probe_stage_measured(conn, monkeypatch):
    """A brand-new file has no duration until probe writes one.

    Read once before the first stage, as it was, every timing of every
    first-time file was filed against a duration of 0 - and eta_seconds skips
    every sample whose duration is 0, so the ETA never calibrated at all.
    """
    with db.LOCK:
        cur = conn.execute(
            "INSERT INTO media(sha256, store_path, orig_name, title, size_bytes,"
            " created_at) VALUES ('deadbeef', 'media/de/deadbeef.wav', 'a.wav', 'a', 1, 0)"
        )
        conn.commit()
    media_id = cur.lastrowid

    def measure(ctx):
        with db.LOCK:
            ctx.conn.execute("UPDATE media SET duration=42.0 WHERE id=?", (media_id,))
            ctx.conn.commit()

    monkeypatch.setitem(
        runner.STAGES,
        "transcribe",
        [("probe", measure), ("prepare", lambda ctx: None)],
    )
    job_id = jobs.enqueue(conn, "transcribe", media_id=media_id)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    rows = conn.execute(
        "SELECT media_duration FROM stage_perf ORDER BY id"
    ).fetchall()
    assert [r["media_duration"] for r in rows] == [42.0, 42.0]


def test_stage_perf_records_the_model_the_stage_actually_used(conn, monkeypatch):
    # translate substitutes large-v3 for turbo; filing that timing under turbo
    # would make every turbo estimate four times too pessimistic.
    monkeypatch.setitem(
        runner.STAGES,
        "transcribe",
        [("transcribe", lambda ctx: ctx.state.update(model="large-v3"))],
    )
    job_id = jobs.enqueue(
        conn, "transcribe", params={"model": "large-v3-turbo", "task": "translate"}
    )
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert conn.execute("SELECT model FROM stage_perf").fetchone()["model"] == "large-v3"


def test_stage_perf_falls_back_to_the_model_that_was_asked_for(conn, monkeypatch):
    # A stage that names no model (probe, prepare) is still this job's work.
    monkeypatch.setitem(runner.STAGES, "transcribe", [("probe", lambda ctx: None)])
    job_id = jobs.enqueue(conn, "transcribe", params={"model": "large-v3-turbo"})
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert (
        conn.execute("SELECT model FROM stage_perf").fetchone()["model"]
        == "large-v3-turbo"
    )


# --- registry / bad input -----------------------------------------------------


def test_unknown_job_type_fails_cleanly(conn):
    job_id = jobs.enqueue(conn, "no-such-type")
    jobs.claim_next(conn)
    assert runner.main([str(job_id)]) == 1
    row = _job_row(conn, job_id)
    assert row["status"] == "failed"
    assert row["error_code"] == "UNKNOWN_JOB_TYPE"


def test_missing_job_id_returns_failure(conn):
    assert runner.main(["999"]) == 1
    assert runner.main([]) == 1


def test_stage_registry_shape():
    assert "fake" in runner.STAGES
    names = [name for name, fn in runner.STAGES["fake"]]
    assert names == ["one", "two", "three"]
    assert all(callable(fn) for _, fn in runner.STAGES["fake"])


# --- work directory never outlives the job -----------------------------------


def _stage_that_leaves_scratch(ctx):
    work = paths.job_work_dir(ctx.job["id"])
    work.mkdir(parents=True, exist_ok=True)
    (work / "audio.wav").write_bytes(b"not really audio")


def test_work_dir_is_removed_when_a_stage_fails(conn, monkeypatch, tmp_path):
    # Review finding: finalize only cleaned up on success, prepare only on its
    # own failure, so every job that died in a later stage stranded ~115 MB
    # per hour of audio - and a retry gets a fresh id, so they piled up.
    monkeypatch.setattr(paths, "WORK_DIR", tmp_path / "work")

    def stage_two(ctx):
        raise RuntimeError("scripted failure after scratch was written")

    monkeypatch.setitem(runner.STAGES, "fake", [("one", _stage_that_leaves_scratch), ("two", stage_two)])
    job_id = jobs.enqueue(conn, "fake")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    assert _job_row(conn, job_id)["status"] == "failed"
    assert not paths.job_work_dir(job_id).exists()


def test_work_dir_is_removed_when_a_job_is_cancelled(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "WORK_DIR", tmp_path / "work")

    def stage_one(ctx):
        _stage_that_leaves_scratch(ctx)
        jobs.request_cancel(ctx.conn, ctx.job["id"])

    monkeypatch.setitem(runner.STAGES, "fake", [("one", stage_one), ("two", lambda ctx: None)])
    job_id = jobs.enqueue(conn, "fake")
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 2

    assert _job_row(conn, job_id)["status"] == "cancelled"
    assert not paths.job_work_dir(job_id).exists()


def test_stage_perf_for_a_default_job_is_filed_under_the_resolved_model(conn, monkeypatch):
    # No model param means the spec default; filing early stages under NULL
    # while later ones land under "large-v3-turbo" splits one job's history.
    monkeypatch.setitem(runner.STAGES, "transcribe", [("probe", lambda ctx: None)])
    job_id = jobs.enqueue(conn, "transcribe", params={})
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    assert conn.execute("SELECT model FROM stage_perf").fetchone()["model"] == "large-v3-turbo"
