"""URL import: the yt-dlp wrapper and the `ingest_url` job type (Phase 6 Task 1).

Not one of these tests reaches the network, and that is a rule rather than a
convenience: yt-dlp's whole job is to keep up with sites that change under it,
so a suite that talked to YouTube would fail on somebody else's release
schedule and tell us nothing about this code. Everything goes through a
`FakeYdl` that records what it was asked and returns what the test says a site
returned.

Three things get most of the attention here, because they are the three that
bite in the field:

* **A playlist is fanned out, never downloaded.** One job per entry, so a
  fifty-video playlist is fifty rows with fifty progress bars and fifty
  independent failures - not one job that dies on video forty-one.
* **A failure says what it was.** yt-dlp's `DownloadError` is one class for
  "this video is private", "the site changed and the extractor is stale" and
  "your network dropped"; the user needs to know which, because the fix for
  each is a different action.
* **A title is untrusted input.** It arrives over the network and it ends up
  in a filename, so `..\\..\\evil` has to come out the other side as a name
  inside the work directory.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from scribe import db, doctor, jobs, media, paths, runner
from scribe.ingest import urls
from scribe.stages import transcribe, url_stage
from scribe.web import jobs_ui

# --- the fake ------------------------------------------------------------------


@dataclass
class Call:
    """One `extract_info` the code under test made."""

    url: str
    download: bool


class FakeYdl:
    """Stands in for `yt_dlp.YoutubeDL`; never touches a network.

    Faithful about the two things this module depends on: `extract_info`
    returns the site's info dict (or raises), and `add_progress_hook`
    registers a callback that a download drives. Everything else a real
    YoutubeDL does is deliberately absent - if this code starts needing it,
    the fake should have to grow on purpose.
    """

    def __init__(self, info=None, *, raises=None, progress=(), files=("download.m4a",)):
        self.info = info or {}
        self.raises = raises
        self.progress = list(progress)
        self.files = list(files)
        self.calls: list[Call] = []
        self.hooks: list = []
        self.opts: dict = {}
        self.dest: Path | None = None

    # `urls.build_ydl` is the seam: the tests hand this object back from it and
    # read the options the code would have given a real YoutubeDL.
    def configure(self, opts: dict) -> "FakeYdl":
        self.opts = opts
        template = opts.get("outtmpl")
        if isinstance(template, str):
            self.dest = Path(template).parent
        return self

    def add_progress_hook(self, hook) -> None:
        self.hooks.append(hook)

    def extract_info(self, url, download=False):
        self.calls.append(Call(url, download))
        if self.raises is not None:
            raise self.raises
        if download:
            assert self.dest is not None, "the test must say where the fake writes"
            self.dest.mkdir(parents=True, exist_ok=True)
            for name in self.files:
                (self.dest / name).write_bytes(f"bytes of {name}".encode())
            for payload in self.progress:
                for hook in self.hooks:
                    hook(payload)
        return self.info

    @property
    def downloads(self) -> list[Call]:
        return [call for call in self.calls if call.download]


def build_returning(fake: FakeYdl):
    """A `build_ydl` replacement that hands back ``fake``, configured."""
    return lambda opts: fake.configure(opts)


def single_info(**over) -> dict:
    info = {
        "id": "abc123",
        "title": "Vogon Poetry Slam",
        "duration": 42.0,
        "uploader": "Prostetnic Jeltz",
        "webpage_url": "https://example.test/watch?v=abc123",
        "ext": "m4a",
    }
    info.update(over)
    return info


def playlist_info(count: int = 3) -> dict:
    return {
        "_type": "playlist",
        "id": "PL42",
        "title": "The Hitchhiker Lectures",
        "uploader": "Megadodo Publications",
        "webpage_url": "https://example.test/playlist?list=PL42",
        "entries": [
            {
                "id": f"vid{i}",
                "title": f"Lecture {i}",
                "duration": 60.0 + i,
                "url": f"https://example.test/watch?v=vid{i}",
            }
            for i in range(count)
        ],
    }


def downloading(done: int, total: int | None) -> dict:
    payload = {"status": "downloading", "downloaded_bytes": done}
    if total is not None:
        payload["total_bytes"] = total
    return payload


# --- fixtures ------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Tmp DB that runner.main() also finds via the default paths.DB_PATH."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(paths, "DB_PATH", path)
    c = db.connect(path)
    db.migrate(c)
    yield c
    c.close()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA_DIR", data)
    monkeypatch.setattr(paths, "MEDIA_DIR", data / "media")
    monkeypatch.setattr(paths, "WORK_DIR", data / "work")
    (data / "media").mkdir(parents=True)
    (data / "work").mkdir(parents=True)
    return data


def make_ctx(conn, job_id, progress=None):
    """A RunnerContext as the runner would build it, minus the throttle."""
    job = dict(conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone())
    return runner.RunnerContext(
        conn=conn,
        job=job,
        params=json.loads(job["params_json"] or "{}"),
        report=(lambda p: None) if progress is None else progress.append,
        cancelled=lambda: False,
        media_path=None,
    )


def url_job(conn, url="https://example.test/watch?v=abc123", **params) -> int:
    return jobs.enqueue(conn, url_stage.JOB_TYPE, params={"url": url, **params})


def run_stages(ctx) -> None:
    for _name, fn in url_stage.STAGES:
        fn(ctx)


# --- what a URL says it is -----------------------------------------------------


def test_probe_reads_a_single_video_as_kind_single():
    fake = FakeYdl(single_info())

    info = urls.probe("https://example.test/watch?v=abc123", ydl=fake)

    assert info.kind == "single"
    assert info.title == "Vogon Poetry Slam"
    assert info.duration == pytest.approx(42.0)
    assert info.uploader == "Prostetnic Jeltz"
    assert info.entries == []
    assert fake.downloads == []  # a probe never downloads


def test_probe_reads_a_playlist_as_its_entries():
    fake = FakeYdl(playlist_info(3))

    info = urls.probe("https://example.test/playlist?list=PL42", ydl=fake)

    assert info.kind == "playlist"
    assert len(info.entries) == 3
    assert [e["title"] for e in info.entries] == ["Lecture 0", "Lecture 1", "Lecture 2"]
    assert info.entries[0]["url"] == "https://example.test/watch?v=vid0"
    assert fake.downloads == []


def test_probe_drops_a_playlist_entry_with_no_url_to_follow():
    # A flat playlist entry that carries no URL is an entry we could not fetch
    # even if we tried; keeping it would enqueue a job that can only fail.
    info = playlist_info(2)
    info["entries"][0].pop("url")

    probed = urls.probe("https://example.test/playlist?list=PL42", ydl=FakeYdl(info))

    assert [e["title"] for e in probed.entries] == ["Lecture 1"]


def test_a_non_http_url_never_reaches_yt_dlp():
    fake = FakeYdl(single_info())

    for hostile in (
        "file:///etc/passwd",
        "file://C:/Windows/win.ini",
        "javascript:alert(1)",
        "ytsearch:how to make bread",
        "not a url at all",
        "",
    ):
        with pytest.raises(urls.UnsupportedUrl):
            urls.probe(hostile, ydl=fake)

    assert fake.calls == []


# --- downloading ---------------------------------------------------------------


def test_download_lands_in_the_work_dir_under_the_video_title(tmp_path):
    fake = FakeYdl(single_info(), files=("download.m4a", "download.info.json"))

    got = urls.download(
        "https://example.test/watch?v=abc123",
        tmp_path / "work",
        ydl=fake.configure(urls.download_opts(tmp_path / "work")),
    )

    assert got.path == tmp_path / "work" / "Vogon Poetry Slam.m4a"
    assert got.path.is_file()
    assert got.title == "Vogon Poetry Slam"
    assert got.uploader == "Prostetnic Jeltz"
    assert got.duration == pytest.approx(42.0)
    assert not (tmp_path / "work" / "download.m4a").exists()  # renamed, not copied


def test_the_info_json_is_kept_next_to_the_media(tmp_path):
    fake = FakeYdl(single_info(), files=("download.m4a", "download.info.json"))

    got = urls.download(
        "https://example.test/watch?v=abc123",
        tmp_path / "work",
        ydl=fake.configure(urls.download_opts(tmp_path / "work")),
    )

    assert got.path.with_suffix(".info.json").is_file()
    assert not (tmp_path / "work" / "download.info.json").exists()


def test_the_download_options_ask_for_audio_and_refuse_to_re_encode(tmp_path):
    opts = urls.download_opts(tmp_path / "work")

    assert opts["format"] == "bestaudio/best"
    assert opts["postprocessors"] == []  # no --extract-audio, so no re-encode
    assert opts["writeinfojson"] is True
    assert opts["noplaylist"] is True  # a ?list= on a video URL fetches the video
    assert Path(opts["outtmpl"]).parent == tmp_path / "work"


def test_a_cookies_file_is_passed_through_only_when_there_is_one(tmp_path):
    assert "cookiefile" not in urls.download_opts(tmp_path)
    assert urls.download_opts(tmp_path, cookies_file="C:/cookies.txt")["cookiefile"] == "C:/cookies.txt"


def test_progress_is_non_decreasing_and_ends_at_one(tmp_path):
    fake = FakeYdl(
        single_info(),
        progress=[
            downloading(0, 1000),
            downloading(250, 1000),
            downloading(120, 1000),  # a fragment restarting: never go backwards
            downloading(900, 1000),
            {"status": "finished", "downloaded_bytes": 1000, "total_bytes": 1000},
        ],
    )
    seen: list[float] = []

    urls.download(
        "https://example.test/watch?v=abc123",
        tmp_path / "work",
        on_progress=seen.append,
        ydl=fake.configure(urls.download_opts(tmp_path / "work")),
    )

    assert seen == sorted(seen)
    assert seen[-1] == 1.0
    assert all(0.0 <= f <= 1.0 for f in seen)
    assert 0.25 in seen


def test_progress_says_nothing_when_the_size_is_unknown(tmp_path):
    # The house rule (prepare.progress_fractions): a stage that cannot measure
    # itself reports 0 and then 1, never a convincing-looking guess.
    fake = FakeYdl(single_info(), progress=[downloading(250, None), downloading(900, None)])
    seen: list[float] = []

    urls.download(
        "https://example.test/watch?v=abc123",
        tmp_path / "work",
        on_progress=seen.append,
        ydl=fake.configure(urls.download_opts(tmp_path / "work")),
    )

    assert seen == [1.0]


def test_progress_ends_at_one_even_when_no_hook_ever_fired(tmp_path):
    fake = FakeYdl(single_info())
    seen: list[float] = []

    urls.download(
        "https://example.test/watch?v=abc123",
        tmp_path / "work",
        on_progress=seen.append,
        ydl=fake.configure(urls.download_opts(tmp_path / "work")),
    )

    assert seen == [1.0]


# --- untrusted titles ----------------------------------------------------------


def test_a_traversing_title_cannot_escape_the_work_dir(tmp_path):
    work = tmp_path / "work"
    fake = FakeYdl(single_info(title=r"..\..\evil"), files=("download.m4a",))

    got = urls.download(
        "https://example.test/watch?v=abc123",
        work,
        ydl=fake.configure(urls.download_opts(work)),
    )

    assert got.path.resolve().is_relative_to(work.resolve())
    assert got.path.is_file()
    assert got.title == r"..\..\evil"  # the row keeps the real title; the path does not


def test_a_title_that_scrubs_to_nothing_still_gets_a_name(tmp_path):
    work = tmp_path / "work"
    fake = FakeYdl(single_info(title="..."), files=("download.m4a",))

    got = urls.download(
        "https://example.test/watch?v=abc123",
        work,
        ydl=fake.configure(urls.download_opts(work)),
    )

    assert got.path.stem == urls.FALLBACK_STEM
    assert got.path.is_file()


# --- failures that say what they were ------------------------------------------


def download_error(message: str, cause: BaseException | None = None):
    from yt_dlp.utils import DownloadError

    if cause is None:
        return DownloadError(message)
    return DownloadError(message, (type(cause), cause, None))


def test_a_download_error_becomes_download_failed_and_keeps_the_reason():
    fake = FakeYdl(raises=download_error("ERROR: unable to download video data: HTTP Error 403"))

    with pytest.raises(urls.DownloadFailed) as exc:
        urls.probe("https://example.test/watch?v=abc123", ydl=fake)

    assert exc.value.code == "DOWNLOAD_FAILED"
    assert "HTTP Error 403" in str(exc.value)
    assert "ERROR:" not in str(exc.value)  # yt-dlp's shouting is not information


def test_an_unsupported_url_gets_its_own_code():
    from yt_dlp.utils import UnsupportedError

    cause = UnsupportedError("https://example.test/whatever")
    fake = FakeYdl(raises=download_error("ERROR: Unsupported URL: https://example.test/whatever", cause))

    with pytest.raises(urls.UnsupportedUrl) as exc:
        urls.probe("https://example.test/whatever", ydl=fake)

    assert exc.value.code == "UNSUPPORTED_URL"


def test_a_private_or_removed_video_is_unavailable():
    for message in (
        "ERROR: [youtube] abc123: Private video. Sign in if you've been granted access to this video",
        "ERROR: [youtube] abc123: Video unavailable. This video has been removed by the uploader",
        "ERROR: [vimeo] 9: The uploader has not made this video available in your country",
    ):
        with pytest.raises(urls.Unavailable) as exc:
            urls.probe("https://example.test/watch?v=abc123", ydl=FakeYdl(raises=download_error(message)))
        assert exc.value.code == "UNAVAILABLE"


def test_a_stale_extractor_says_so_instead_of_showing_a_stack_trace():
    fake = FakeYdl(
        raises=download_error(
            "ERROR: [youtube] abc123: Unable to extract nsig function\n"
            "Please report this issue on https://github.com/yt-dlp/yt-dlp/issues"
        )
    )

    with pytest.raises(urls.DownloadFailed) as exc:
        urls.probe("https://example.test/watch?v=abc123", ydl=fake)

    message = str(exc.value)
    assert "Unable to extract nsig function" in message  # the reason, not the boilerplate
    assert "out of date" in message
    assert "pip install -U yt-dlp" in message
    assert urls.installed_version() in message
    assert "Traceback" not in message


def test_a_plain_failure_is_not_blamed_on_a_stale_extractor():
    fake = FakeYdl(raises=download_error("ERROR: [youtube] abc123: Read timed out"))

    with pytest.raises(urls.DownloadFailed) as exc:
        urls.probe("https://example.test/watch?v=abc123", ydl=fake)

    assert "out of date" not in str(exc.value)


def test_the_runner_taxonomy_knows_these_four():
    assert runner._error_code(urls.UnsupportedUrl("no")) == "UNSUPPORTED_URL"
    assert runner._error_code(urls.Unavailable("gone")) == "UNAVAILABLE"
    assert runner._error_code(urls.DownloadFailed("403")) == "DOWNLOAD_FAILED"
    # Not DOWNLOAD_FAILED: nothing failed to download, and the thing to do
    # about it is not the thing you do about a network error.
    assert runner._error_code(urls.TooManyEntries("5000")) == "TOO_MANY_ENTRIES"


# --- hotword terms from the metadata -------------------------------------------


def test_hotword_terms_pulls_names_from_title_uploader_and_chapters():
    terms = urls.hotword_terms(
        {
            "title": "Marieke on the improbability drive",
            "uploader": "Megadodo Publications",
            "chapters": [{"title": "Meeting Zaphod"}, {"title": "the towel question"}],
        }
    )

    assert "Marieke" in terms
    assert "Megadodo" in terms and "Publications" in terms
    assert "Zaphod" in terms
    assert "the" not in terms and "towel" not in terms  # lowercase is not a name


def test_hotword_terms_keeps_the_first_of_each_name_and_drops_digits():
    terms = urls.hotword_terms(
        {"title": "Marieke and Marieke, part 2026", "uploader": "Marieke", "chapters": []}
    )

    assert terms == ["Marieke"]


def test_hotword_terms_survives_a_metadata_dict_that_is_missing_everything():
    assert urls.hotword_terms({}) == []


def test_the_name_rule_is_the_same_one_the_hotwords_already_use():
    # Two spellings of one rule drift, so Task 5 moved the definition into
    # `scribe.glossary` and both doors now import it. Identity, not equality:
    # equal patterns is what this asserted while there were two copies, and it
    # would go on passing if somebody made a third.
    from scribe import glossary

    assert urls._WORD is glossary._WORD
    assert transcribe._WORD is glossary._WORD


# --- the job type --------------------------------------------------------------


def test_the_ingest_url_job_type_is_registered_with_fetch_then_register():
    stages = runner.STAGES[url_stage.JOB_TYPE]

    assert [name for name, _ in stages] == ["fetch", "register"]
    assert all(callable(fn) for _, fn in stages)


def test_the_jobs_board_can_draw_a_stepper_for_the_new_type():
    assert jobs_ui.stage_names_for(url_stage.JOB_TYPE) == ("fetch", "register")


def test_a_single_video_is_ingested_and_queued_for_transcription(
    conn, data_dir, monkeypatch
):
    fake = FakeYdl(single_info(), files=("download.m4a", "download.info.json"))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, options={"model": "large-v3", "language": "nl"})

    run_stages(make_ctx(conn, job_id))

    row = conn.execute("SELECT * FROM media").fetchone()
    assert row["title"] == "Vogon Poetry Slam"
    assert (paths.DATA_DIR / row["store_path"]).is_file()
    queued = conn.execute(
        "SELECT * FROM job WHERE type='transcribe'"
    ).fetchone()
    assert queued["media_id"] == row["id"]
    # The options as given, plus the names the metadata already knew. Task 5
    # added the second half: `hotword_terms` reads the info-json here, while
    # the download job still has a work directory, and the transcribe job's
    # params are the only place they can wait for a GPU that is minutes away.
    assert json.loads(queued["params_json"]) == {
        "model": "large-v3",
        "language": "nl",
        "extra_hotwords": ["Vogon", "Poetry", "Slam", "Prostetnic", "Jeltz"],
    }


def test_the_downloaded_file_is_hardlinked_rather_than_copied(conn, data_dir, monkeypatch):
    # The work directory and the store are both under DATA_DIR, so the store
    # takes the same inode and the runner's work-dir sweep completes the move.
    fake = FakeYdl(single_info())
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn)

    run_stages(make_ctx(conn, job_id))

    row = conn.execute("SELECT * FROM media").fetchone()
    stored = paths.DATA_DIR / row["store_path"]
    downloaded = paths.job_work_dir(job_id) / "Vogon Poetry Slam.m4a"
    assert stored.stat().st_ino == downloaded.stat().st_ino


def test_a_folder_choice_follows_the_download_into_the_library(conn, data_dir, monkeypatch):
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(single_info())))
    with db.LOCK:
        folder_id = conn.execute("INSERT INTO folder(name) VALUES ('Talks')").lastrowid
        conn.commit()
    job_id = url_job(conn, folder_id=folder_id)

    run_stages(make_ctx(conn, job_id))

    assert conn.execute("SELECT folder_id FROM media").fetchone()["folder_id"] == folder_id


def test_a_playlist_fans_out_one_job_per_entry_and_downloads_nothing(
    conn, data_dir, monkeypatch
):
    fake = FakeYdl(playlist_info(3))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, "https://example.test/playlist?list=PL42", options={"model": "large-v3"})

    run_stages(make_ctx(conn, job_id))

    assert fake.downloads == []
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    children = conn.execute(
        "SELECT * FROM job WHERE type=? AND id<>? ORDER BY id", (url_stage.JOB_TYPE, job_id)
    ).fetchall()
    assert len(children) == 3
    followed = [json.loads(row["params_json"]) for row in children]
    assert [p["url"] for p in followed] == [
        "https://example.test/watch?v=vid0",
        "https://example.test/watch?v=vid1",
        "https://example.test/watch?v=vid2",
    ]
    assert all(p["options"] == {"model": "large-v3"} for p in followed)


def test_a_playlist_larger_than_the_cap_is_refused_with_the_count(
    conn, data_dir, monkeypatch
):
    """A channel URL is a one-level playlist, so the nested guard never fires
    for it - and `POST /transcribe/url` deliberately does not probe, so the
    preview's "all N" is advisory: nobody has agreed to anything by the time
    this stage runs. Five thousand entries would be five thousand job rows in
    one pass, and a job storm is very hard to get back out of a real database.
    """
    monkeypatch.setattr(url_stage, "MAX_FAN_OUT", 3)
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(playlist_info(4))))
    job_id = url_job(conn, "https://example.test/channel/UCbig")

    with pytest.raises(urls.TooManyEntries) as exc:
        run_stages(make_ctx(conn, job_id))

    message = str(exc.value)
    assert "4" in message and "3" in message  # what it holds, and what is allowed
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 1


def test_a_playlist_exactly_at_the_cap_still_fans_out(conn, data_dir, monkeypatch):
    """The boundary is inclusive: the cap is a size that works, not the first
    one that does not."""
    monkeypatch.setattr(url_stage, "MAX_FAN_OUT", 3)
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(playlist_info(3))))
    job_id = url_job(conn, "https://example.test/playlist?list=PL42")

    run_stages(make_ctx(conn, job_id))

    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 4  # parent + 3


def test_a_fanned_out_job_refuses_to_fan_out_again(conn, data_dir, monkeypatch):
    # A channel URL nested inside a playlist would otherwise fan out forever,
    # and a runaway job storm is very hard to get back out of a real database.
    fake = FakeYdl(playlist_info(2))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, "https://example.test/playlist?list=PLnested", from_playlist=True)

    with pytest.raises(urls.UnsupportedUrl) as exc:
        run_stages(make_ctx(conn, job_id))

    assert "playlist" in str(exc.value).lower()
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 1


def test_the_fetch_stage_reports_the_download_s_progress(conn, data_dir, monkeypatch):
    fake = FakeYdl(single_info(), progress=[downloading(500, 1000)])
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn)
    seen: list[float] = []

    url_stage.fetch(make_ctx(conn, job_id, seen))

    assert 0.5 in seen
    assert seen[-1] == 1.0


def test_a_job_with_no_url_says_so_rather_than_guessing(conn, data_dir):
    job_id = jobs.enqueue(conn, url_stage.JOB_TYPE, params={})

    with pytest.raises(ValueError) as exc:
        url_stage.fetch(make_ctx(conn, job_id))

    assert "url" in str(exc.value)


def test_a_download_failure_fails_the_whole_job_with_its_own_code(
    conn, data_dir, monkeypatch
):
    monkeypatch.setattr(
        urls,
        "build_ydl",
        build_returning(FakeYdl(raises=download_error("ERROR: [youtube] a: Video unavailable"))),
    )
    job_id = url_job(conn)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 1

    job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "failed"
    assert job["error_code"] == "UNAVAILABLE"
    assert "Video unavailable" in job["error_detail"]


def test_a_url_job_runs_end_to_end_through_the_runner(conn, data_dir, monkeypatch):
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(single_info())))
    job_id = url_job(conn)
    jobs.claim_next(conn)

    assert runner.main([str(job_id)]) == 0

    job = conn.execute("SELECT * FROM job WHERE id=?", (job_id,)).fetchone()
    assert job["status"] == "done"
    assert conn.execute("SELECT COUNT(*) FROM job WHERE type='transcribe'").fetchone()[0] == 1
    kinds = [e["kind"] for e in jobs.events_after(conn, job_id, 0)]
    assert "url" in kinds
    # The scratch is gone and the bytes survived it: the store holds the file.
    assert not paths.job_work_dir(job_id).exists()
    stored = paths.DATA_DIR / conn.execute("SELECT store_path FROM media").fetchone()["store_path"]
    assert stored.is_file()


# --- a video URL that came from a playlist -------------------------------------
#
# The address bar gives you `watch?v=VIDEO&list=PLAYLIST` while you are watching
# a video inside a playlist, and that is the URL people paste - not the tidy
# `watch?v=VIDEO` the other tests in this file use. Whether it means "this
# video" or "these fifty videos" is not our decision to make: yt-dlp's
# `InfoExtractor._yes_playlist` makes it, from the `noplaylist` option we hand
# it. So these tests ask the installed yt-dlp instead of restating its rule -
# a re-implementation would go on agreeing with itself after yt-dlp changed its
# mind, which is the one failure this whole section exists to catch.

# The URL YouTube actually gives you. Two ids, and without `noplaylist` the
# second one wins.
WATCH_IN_PLAYLIST = "https://example.test/watch?v=abc123&list=PL42"

# A link that is only ever a playlist: no video id at all, so no guard can turn
# it into one video. Fanning it out is the feature, not the bug.
PLAYLIST_ONLY = "https://example.test/playlist?list=PL42"


def yes_playlist(url: str, opts: dict) -> bool:
    """yt-dlp's own answer to "does this URL mean the playlist?", given ``opts``.

    Not a re-implementation: this calls the real decision function out of the
    installed yt-dlp - `InfoExtractor._yes_playlist` in
    `yt_dlp/extractor/common.py`, whose only caller is the YouTube tab
    extractor, where a True returns the tab rather than the video. The two ids
    are read off the query string, which is all that function is ever given.

    Measured 2026-09-04 against yt-dlp 2026.08.19, and it is the table this
    section is built on:

    ==========================  ==============  ================
    URL                         plain           `noplaylist`
    ==========================  ==============  ================
    watch?v=X&list=Y            the playlist    the video
    playlist?list=Y             the playlist    the playlist
    watch?v=X                   the video       the video
    ==========================  ==============  ================

    The middle row is why the guard is safe to set on the probe: `noplaylist`
    only ever decides a tie between two ids, so a link that names no video is
    left exactly as it was.
    """
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.common import InfoExtractor

    query = parse_qs(urlparse(url).query)
    video_id = (query.get("v") or [None])[0]
    playlist_id = (query.get("list") or [None])[0]
    # Closed, because a YoutubeDL holds a request director and this suite runs
    # hundreds of tests in one process.
    with YoutubeDL({**opts, "quiet": True, "no_warnings": True}) as ydl:
        return InfoExtractor(ydl)._yes_playlist(playlist_id, video_id)


class ResolvingYdl(FakeYdl):
    """A `FakeYdl` that works out what it is looking at, the way yt-dlp would.

    The plain `FakeYdl` is *told* what to return, which is right for every
    other test here and useless for this one: the question is whether our
    options make yt-dlp see one video or a playlist, and hard-coding the answer
    would assert only that the test author knows what they wrote. This one
    reads the URL and the options it was configured with - the real
    `probe_opts` and `download_opts` - and returns what those two would have
    produced between them.
    """

    def extract_info(self, url, download=False):
        self.info = playlist_info(3) if yes_playlist(url, self.opts) else single_info()
        return super().extract_info(url, download)


def test_a_video_url_carrying_a_list_probes_as_the_video_it_names():
    fake = ResolvingYdl().configure(urls.probe_opts())

    info = urls.probe(WATCH_IN_PLAYLIST, ydl=fake)

    assert info.kind == "single"
    assert info.entries == []


def test_a_bare_playlist_url_still_probes_as_a_playlist():
    # The other half of the fix, and the half that could be broken by it: the
    # guard must narrow a video-plus-list URL without touching a link that is
    # nothing but a list.
    fake = ResolvingYdl().configure(urls.probe_opts())

    info = urls.probe(PLAYLIST_ONLY, ydl=fake)

    assert info.kind == "playlist"
    assert len(info.entries) == 3


def test_a_plain_video_url_probes_the_same_way_with_the_guard_or_without_it():
    plain = "https://example.test/watch?v=abc123"

    for opts in (urls.probe_opts(), {**urls.probe_opts(), "noplaylist": False}):
        info = urls.probe(plain, ydl=ResolvingYdl().configure(opts))
        assert info.kind == "single"


def test_neither_option_builder_lets_a_video_url_become_its_playlist(tmp_path):
    """`download_opts` set `noplaylist` and `probe_opts` did not, and `probe` is
    what decides: `url_stage.fetch` probes first and returns without ever
    reaching `download` when the answer is "playlist". A guard on the second
    builder alone sits on a path this input never takes.
    """
    for opts in (urls.probe_opts(), urls.download_opts(tmp_path / "work")):
        assert opts["noplaylist"] is True
        assert yes_playlist(WATCH_IN_PLAYLIST, opts) is False

    # And it stays narrow: neither builder turns a playlist into a video.
    assert yes_playlist(PLAYLIST_ONLY, urls.probe_opts()) is True
    assert yes_playlist(PLAYLIST_ONLY, urls.download_opts(tmp_path / "work")) is True


def test_a_video_url_carrying_a_list_becomes_one_recording_not_a_fan_out(
    conn, data_dir, monkeypatch
):
    """One paste, one download. Without the guard on the probe this URL fanned
    out into one `ingest_url` job per entry - up to `MAX_FAN_OUT`, so one click
    could become five hundred downloads and five hundred queued transcriptions.
    """
    fake = ResolvingYdl()
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, WATCH_IN_PLAYLIST)

    run_stages(make_ctx(conn, job_id))

    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    children = conn.execute(
        "SELECT COUNT(*) FROM job WHERE type=? AND id<>?", (url_stage.JOB_TYPE, job_id)
    ).fetchone()[0]
    assert children == 0
    assert len(fake.downloads) == 1


def test_a_bare_playlist_url_still_fans_out_one_job_per_entry(
    conn, data_dir, monkeypatch
):
    fake = ResolvingYdl()
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, PLAYLIST_ONLY)

    run_stages(make_ctx(conn, job_id))

    assert fake.downloads == []
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
    children = conn.execute(
        "SELECT COUNT(*) FROM job WHERE type=? AND id<>?", (url_stage.JOB_TYPE, job_id)
    ).fetchone()[0]
    assert children == 3


# --- the doctor's yt-dlp check -------------------------------------------------


def test_the_doctor_reports_the_installed_yt_dlp_and_its_age():
    check = doctor.check_ytdlp()

    assert check.name == "yt-dlp"
    assert check.ok is True
    assert urls.installed_version() in check.detail
    assert re.search(r"\d+ days? old", check.detail)


def test_the_doctor_calls_an_old_yt_dlp_old_without_failing_the_gate(monkeypatch):
    monkeypatch.setattr(urls, "installed_version", lambda: "2024.01.01")

    check = doctor.check_ytdlp()

    assert check.ok is True  # URL import still works; it just may not, per site
    assert "out of date" in check.detail
    assert "pip install -U yt-dlp" in check.detail


def test_the_doctor_says_so_when_yt_dlp_is_not_installed(monkeypatch):
    monkeypatch.setattr(urls, "installed_version", lambda: None)

    check = doctor.check_ytdlp()

    assert check.ok is False
    assert check.optional is True  # nothing else in the app needs it
    assert "pip install" in check.fix_hint


def test_reading_the_version_does_not_import_yt_dlp(monkeypatch):
    """`check_ytdlp` is in CPU_CHECKS, which `settings_page` runs in the
    request - so reading the version from `yt_dlp.version` imported the whole
    package into the web process and left it there. Measured: 554 ms of import
    time, 0.62 s wall for one call, resident for the life of the process. That
    is precisely the cost `build_ydl`'s docstring says is deferred so that "a
    process which never fetches a URL should not pay" it.
    """
    for name in [n for n in list(sys.modules) if n == "yt_dlp" or n.startswith("yt_dlp.")]:
        monkeypatch.delitem(sys.modules, name)

    version = urls.installed_version()

    assert version, "yt-dlp is not installed; this test cannot say anything"
    assert not any(n == "yt_dlp" or n.startswith("yt_dlp.") for n in sys.modules)


def test_the_version_is_read_as_a_release_date():
    assert urls.release_date("2026.08.19") == date(2026, 8, 19)
    assert urls.release_date("2026.08.19.232303") == date(2026, 8, 19)  # a nightly
    # And the same two as the distribution metadata spells them: PEP 440
    # normalisation drops the leading zero, which a fixed-width slice of the
    # string reads as a date and then fails on for the nightly.
    assert urls.release_date("2026.8.19") == date(2026, 8, 19)
    assert urls.release_date("2026.8.19.232303") == date(2026, 8, 19)
    assert urls.release_date("not-a-version") is None
    assert urls.release_date("2026.08") is None
    assert urls.release_date(None) is None


def test_the_staleness_threshold_is_measured_from_the_release_date(monkeypatch):
    fresh = date.today() - timedelta(days=urls.STALE_DAYS - 1)
    old = date.today() - timedelta(days=urls.STALE_DAYS + 1)

    monkeypatch.setattr(urls, "installed_version", lambda: fresh.strftime("%Y.%m.%d"))
    assert urls.is_stale() is False
    monkeypatch.setattr(urls, "installed_version", lambda: old.strftime("%Y.%m.%d"))
    assert urls.is_stale() is True


# --- the feed import (TASK-021): a listing with a total, entries that know who they are ---

"""
A podcast feed is the input the feed import is about, and it is not shaped
like the YouTube playlists above: yt-dlp's generic extractor reads one into
entries with a timestamp, a duration, **no id**, and an enclosure URL whose
fragment carries the item's ``<guid>`` smuggled the yt-dlp way. `feed_info`
below asks that extractor rather than restating its output, so if yt-dlp
changes the shape the fixture changes with it and these tests say so. The
XML it parses is the literal below, never a real feed.
"""

import xml.etree.ElementTree as ET

FEED_URL = "https://feeds.test/podcast.xml"

FEED_XML = """<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<title>The Hitchhiker Lectures</title><description>Mostly harmless.</description>
<item><title>Trump drinks Venezuela's milkshake &amp; "more" &lt;3 café</title><guid>guid-0</guid>
  <pubDate>Fri, 04 Sep 2026 12:00:00 GMT</pubDate><itunes:duration>25:27</itunes:duration>
  <enclosure url="https://cdn.test/ep/default.mp3?d=1&amp;e=0" type="audio/mpeg"/></item>
<item><title>Lecture 1</title><guid>guid-1</guid><pubDate>Wed, 02 Sep 2026 12:00:00 GMT</pubDate>
  <enclosure url="https://cdn.test/ep/default.mp3?d=1&amp;e=1" type="audio/mpeg"/></item>
<item><title>Lecture 2</title><guid>guid-2</guid>
  <enclosure url="https://cdn.test/ep/default.mp3?d=1&amp;e=2" type="audio/mpeg"/></item>
<item><title>A page, not an enclosure</title><guid>guid-3</guid><link>https://example.test/page/3</link></item>
<item><title>Nothing to fetch</title><guid>guid-4</guid></item>
</channel></rss>"""


def feed_info() -> dict:
    """A feed as yt-dlp's own generic extractor reads one - derived, not mirrored.

    Four entries come back from five items: the one with neither an enclosure
    nor a link is skipped by yt-dlp itself, before anything of ours runs. The
    `playlist_count` is set the way `YoutubeDL` fills it for a list-shaped
    playlist, so the fixture answers "how long is the feed" like a real probe.
    """
    from yt_dlp.extractor.generic import GenericIE

    info = GenericIE()._extract_rss(FEED_URL, None, ET.fromstring(FEED_XML))
    info["extractor"] = "generic"
    info["extractor_key"] = "Generic"
    info["webpage_url"] = FEED_URL
    info["playlist_count"] = len(info["entries"])
    return info


def test_probe_opts_limit_becomes_playlistend_and_leaves_playlist_items_alone():
    # `playlistend` is honoured only while `playlist_items` is None; both are
    # asserted so the guard cannot be lost in a later tidy-up.
    assert urls.probe_opts(limit=5)["playlistend"] == 5
    assert urls.probe_opts(limit=5)["playlist_items"] is None
    assert urls.probe_opts().get("playlistend") is None


def test_yt_dlp_honours_playlistend_on_a_flat_playlist_and_still_reports_the_total():
    """Asked of yt-dlp itself, offline, the way the yes_playlist tests ask it:
    a list-shaped playlist (a feed) cut at 5 still says it held 6. The
    `FakeYdl` never trims, so this is the one place the cut is real."""
    from yt_dlp import YoutubeDL

    info = playlist_info(6)
    info["extractor"], info["extractor_key"] = "generic", "Generic"
    with YoutubeDL({**urls.probe_opts(limit=5)}) as ydl:
        out = ydl.process_ie_result(dict(info), download=False)

    assert len(out["entries"]) == 5
    assert out["playlist_count"] == 6


@pytest.mark.parametrize(
    "count, playlist_count, limit, expected_truncated, expected_total",
    [
        (3, 7, 3, True, 7),  # the total says more was there
        (3, 3, 3, False, 3),  # exactly the limit, and the total agrees: not cut
        (3, None, 3, True, None),  # no total (a channel): the count is the signal
        (3, None, None, False, None),
        (2, None, 3, False, None),
    ],
)
def test_truncated_follows_the_total_and_falls_back_to_the_limit(
    count, playlist_count, limit, expected_truncated, expected_total
):
    info = playlist_info(count)
    if playlist_count is not None:
        info["playlist_count"] = playlist_count

    probed = urls.probe(
        "https://example.test/playlist?list=PL42", limit=limit, ydl=FakeYdl(info)
    )

    assert probed.truncated is expected_truncated
    assert probed.total == expected_total


def test_probe_passes_the_limit_to_the_options_it_builds(monkeypatch):
    fake = FakeYdl(playlist_info(2))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))

    urls.probe("https://example.test/playlist?list=PL42", limit=42)

    assert fake.opts["playlistend"] == 42


def test_entries_carry_a_timestamp_from_either_field_and_none_otherwise():
    info = playlist_info(3)
    info["entries"][0]["timestamp"] = 1788523200
    info["entries"][1]["release_timestamp"] = 1788350400

    probed = urls.probe("https://example.test/playlist?list=PL42", ydl=FakeYdl(info))

    assert [e["timestamp"] for e in probed.entries] == [1788523200.0, 1788350400.0, None]


def test_a_feed_entry_keeps_its_smuggled_url_and_gets_the_guid_as_its_source_id():
    probed = urls.probe(FEED_URL, ydl=FakeYdl(feed_info()))

    first = probed.entries[0]
    assert first["url"].startswith("https://cdn.test/ep/default.mp3?d=1&e=0#__youtubedl_smuggle=")
    assert first["source_id"] == "Generic:guid-0"
    assert first["title"] == 'Trump drinks Venezuela\'s milkshake & "more" <3 café'
    assert first["timestamp"] == 1788523200.0
    assert first["duration"] == 1527.0
    assert probed.entries[2]["timestamp"] is None
    assert probed.entries[3]["url"] == (
        "https://example.test/page/3#__youtubedl_smuggle=%7B%22force_videoid%22%3A+%22guid-3%22%7D"
    )
    assert len(probed.entries) == 4
    assert probed.total == 4
    assert probed.truncated is False


def test_a_youtube_entry_s_source_id_is_the_extractor_and_the_video_id():
    info = playlist_info(1)
    info["entries"][0]["ie_key"] = "Youtube"

    probed = urls.probe("https://example.test/playlist?list=PL42", ydl=FakeYdl(info))

    assert probed.entries[0]["source_id"] == "Youtube:vid0"


def test_an_entry_with_neither_an_ie_key_nor_a_guid_has_no_source_id():
    probed = urls.probe("https://example.test/playlist?list=PL42", ydl=FakeYdl(playlist_info(1)))

    assert probed.entries[0]["source_id"] is None


def test_download_names_the_file_and_its_sidecar_after_the_hint(tmp_path, monkeypatch):
    fake = FakeYdl(single_info(), files=("download.m4a", "download.info.json"))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))

    got = urls.download(
        "https://example.test/watch?v=abc123", tmp_path,
        title_hint="Love in the time of Palantir",
    )

    assert got.path.name == "Love in the time of Palantir.m4a"
    assert (tmp_path / "Love in the time of Palantir.info.json").is_file()
    assert got.title == "Vogon Poetry Slam"  # the hint names the file, not the media


def test_a_traversing_title_hint_cannot_escape_the_work_dir(tmp_path, monkeypatch):
    work = tmp_path / "work"
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(single_info())))

    got = urls.download(
        "https://example.test/watch?v=abc123", work, title_hint=r"..\..\evil"
    )

    assert got.path.resolve().is_relative_to(work.resolve())
    assert got.path.is_file()


def test_a_hint_that_scrubs_to_nothing_still_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(single_info())))

    got = urls.download("https://example.test/watch?v=abc123", tmp_path, title_hint="...")

    assert got.path.stem == urls.FALLBACK_STEM


def test_an_empty_hint_falls_back_to_the_site_s_title(tmp_path, monkeypatch):
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(single_info())))

    got = urls.download("https://example.test/watch?v=abc123", tmp_path, title_hint="")

    assert got.path.stem == "Vogon Poetry Slam"


# --- the job: an episode keeps its name and its provenance ------------------------


def test_a_fan_out_child_carries_the_entry_s_title_and_the_feed_it_came_from(
    conn, data_dir, monkeypatch
):
    monkeypatch.setattr(urls, "build_ydl", build_returning(FakeYdl(feed_info())))
    job_id = url_job(conn, FEED_URL, options={"model": "large-v3"})

    run_stages(make_ctx(conn, job_id))

    children = [
        json.loads(row["params_json"])
        for row in conn.execute(
            "SELECT params_json FROM job WHERE type=? AND id<>? ORDER BY id",
            (url_stage.JOB_TYPE, job_id),
        )
    ]
    assert len(children) == 4
    assert children[1]["entry"] == {"title": "Lecture 1", "source_id": "Generic:guid-1"}
    assert children[1]["source"] == {"url": FEED_URL, "title": "The Hitchhiker Lectures"}
    assert children[1]["url"].startswith("https://cdn.test/ep/default.mp3?d=1&e=1#__youtubedl_smuggle=")
    assert children[1]["from_playlist"] is True
    assert children[1]["options"] == {"model": "large-v3"}


def test_register_names_a_feed_episode_after_the_feed_not_the_cdn(conn, data_dir, monkeypatch):
    """A bare enclosure probes as its CDN filename (measured 2026-09-08:
    `default.mp3_ywr3ahjkcgo_…`, no uploader); the feed knew the episode's
    name and the child carries it. The feed's own title stands in for the
    missing uploader, so the decoder is biased towards "Hitchhiker"."""
    fake = FakeYdl(
        single_info(title="default.mp3_ywr3ahjkcgo_6c70", uploader="", extractor_key="Generic", id="guid-1"),
        files=("download.mp3", "download.info.json"),
    )
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(
        conn,
        "https://cdn.test/ep/default.mp3?d=1&e=1#__youtubedl_smuggle=x",
        from_playlist=True,
        options={"model": "large-v3"},
        entry={"title": "Lecture 1", "source_id": "Generic:guid-1"},
        source={"url": FEED_URL, "title": "The Hitchhiker Lectures"},
    )

    run_stages(make_ctx(conn, job_id))

    row = conn.execute("SELECT * FROM media").fetchone()
    assert row["title"] == "Lecture 1"
    assert row["orig_name"] == "Lecture 1.mp3"
    assert row["source_url"] == "https://cdn.test/ep/default.mp3?d=1&e=1"  # no fragment, ever
    assert row["source_id"] == "Generic:guid-1"
    queued = conn.execute("SELECT params_json FROM job WHERE type='transcribe'").fetchone()
    assert json.loads(queued["params_json"])["extra_hotwords"] == [
        "Lecture", "The", "Hitchhiker", "Lectures",
    ]


def test_register_takes_the_download_s_extractor_id_when_the_entry_has_none(
    conn, data_dir, monkeypatch
):
    fake = FakeYdl(single_info(extractor_key="Youtube"), files=("download.m4a",))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, "https://youtu.be/abc123#t=42")

    run_stages(make_ctx(conn, job_id))

    row = conn.execute("SELECT source_url, source_id FROM media").fetchone()
    assert row["source_url"] == "https://youtu.be/abc123"
    assert row["source_id"] == "Youtube:abc123"


def test_register_stores_no_id_for_a_generic_download_without_an_entry(
    conn, data_dir, monkeypatch
):
    """The generic extractor's id for a bare file is its filename, which would
    mark the wrong episode as known; a typed mp3 link is matched by URL only."""
    fake = FakeYdl(single_info(extractor_key="Generic", id="default"), files=("download.mp3",))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(conn, "https://cdn.test/some.mp3")

    run_stages(make_ctx(conn, job_id))

    assert conn.execute("SELECT source_id FROM media").fetchone()["source_id"] is None


def test_register_keeps_the_download_s_uploader_when_it_has_one(conn, data_dir, monkeypatch):
    """The source title is a fallback, not an override: a YouTube video from a
    channel listing keeps its channel as the uploader for the hotwords."""
    fake = FakeYdl(single_info(extractor_key="Youtube"), files=("download.m4a",))
    monkeypatch.setattr(urls, "build_ydl", build_returning(fake))
    job_id = url_job(
        conn, "https://example.test/watch?v=abc123", from_playlist=True,
        entry={"title": "Vogon Poetry Slam", "source_id": "Youtube:abc123"},
        source={"url": "https://example.test/@channel", "title": "Megadodo Publications"},
    )

    run_stages(make_ctx(conn, job_id))

    queued = conn.execute("SELECT params_json FROM job WHERE type='transcribe'").fetchone()
    assert json.loads(queued["params_json"])["extra_hotwords"] == [
        "Vogon", "Poetry", "Slam", "Prostetnic", "Jeltz",
    ]
