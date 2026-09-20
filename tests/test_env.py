"""`.env`: what the file may fill, how a line gets into it, and what a command
does before it reads anything (TASK-089.03)."""

from __future__ import annotations

import codecs
import os
import stat
import sys
import types
from pathlib import Path

import pytest

from scribe import env, paths


@pytest.fixture
def environ(monkeypatch):
    """A throwaway process environment, made the way tests/test_web_scaffold.py
    makes one: swapping the whole mapping out is the only sandbox a key set by
    the code under test cannot leak from."""
    sandbox: dict[str, str] = {}
    monkeypatch.setattr(os, "environ", sandbox)
    return sandbox


def dotenv(tmp_path: Path, text: str, *, encoding: str = "utf-8") -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding=encoding)
    return path


def lines_for(path: Path, name: str) -> list[str]:
    """The lines of ``path`` that define ``name``, read the way the app reads."""
    text = path.read_text(encoding="utf-8-sig")
    return [line for line in text.splitlines() if line.partition("=")[0].strip() == name]


# --- a blank variable is not an answer ------------------------------------------


def test_a_blank_process_variable_does_not_hide_the_value_in_the_file(tmp_path, environ):
    """`export HF_TOKEN=` in a shell, `HF_TOKEN:` in a compose file and an
    empty `Environment=` in a service unit all leave the name set and empty.
    setdefault took that for an answer, and the token in `.env` never arrived."""
    environ["SCRIBE_T3_TOKEN"] = ""

    env.load_dotenv(dotenv(tmp_path, "SCRIBE_T3_TOKEN=from-the-file\n"))

    assert environ["SCRIBE_T3_TOKEN"] == "from-the-file"


def test_a_whitespace_only_process_variable_does_not_hide_it_either(tmp_path, environ):
    environ["SCRIBE_T4_TOKEN"] = " \t "

    env.load_dotenv(dotenv(tmp_path, "SCRIBE_T4_TOKEN=from-the-file\n"))

    assert environ["SCRIBE_T4_TOKEN"] == "from-the-file"


def test_a_process_value_that_says_something_still_wins(tmp_path, environ):
    """The reason this is not python-dotenv: `SCRIBE_DATA_DIR=... python -m
    scribe` overrides the file, never the other way round."""
    environ["SCRIBE_T5_DIR"] = "from-the-shell"

    env.load_dotenv(dotenv(tmp_path, "SCRIBE_T5_DIR=from-the-file\n"))

    assert environ["SCRIBE_T5_DIR"] == "from-the-shell"


def test_a_blank_value_in_the_file_is_not_exported(tmp_path, environ):
    """`.env.example` ships `HF_TOKEN=`. Copied as it stands, that line must
    not put an empty HF_TOKEN into the process - to Hugging Face an empty
    string is a token, and it answers 401."""
    values = env.load_dotenv(
        dotenv(tmp_path, "SCRIBE_T6_EMPTY=\nSCRIBE_T6_QUOTED=\"\"\nSCRIBE_T6_SPACES='  '\nSCRIBE_T6_REAL=x\n")
    )

    # pytest keeps PYTEST_CURRENT_TEST in the environment, so: ours only.
    assert {key: environ[key] for key in environ if key.startswith("SCRIBE_")} == {"SCRIBE_T6_REAL": "x"}
    assert values["SCRIBE_T6_EMPTY"] == "", "still reported: it is what the file defines"


# --- which names came from the file ----------------------------------------------


def test_applied_names_what_the_file_filled_and_nothing_else(tmp_path, environ):
    environ["SCRIBE_T7_HELD"] = "from-the-shell"
    environ["SCRIBE_T7_BLANK"] = ""
    before = env.applied()

    env.load_dotenv(
        dotenv(tmp_path, "SCRIBE_T7_NEW=1\nSCRIBE_T7_HELD=2\nSCRIBE_T7_BLANK=3\nSCRIBE_T7_EMPTY=\n")
    )

    assert env.applied() - before == {"SCRIBE_T7_NEW", "SCRIBE_T7_BLANK"}


def test_applied_remembers_across_loads(tmp_path, environ):
    """setup.main() loads the file and needed() loads it again. The second
    load finds every key set and applies nothing, so a record that started
    over on each call would say `.env` supplied nothing at all."""
    path = dotenv(tmp_path, "SCRIBE_T8_ONCE=1\n")

    env.load_dotenv(path)
    env.load_dotenv(path)

    assert "SCRIBE_T8_ONCE" in env.applied()


def test_applied_holds_names_and_never_a_value(tmp_path, environ):
    env.load_dotenv(dotenv(tmp_path, "SCRIBE_T9_SECRET=hf_not_for_the_record\n"))

    names = env.applied()

    assert isinstance(names, frozenset)
    assert "SCRIBE_T9_SECRET" in names
    assert not any("hf_not_for_the_record" in name for name in names)


def test_applied_spells_a_name_the_way_the_environment_does(tmp_path, environ, monkeypatch):
    """Windows has one variable per name whatever the case, and spells it in
    capitals: `hf_token=...` in the file fills HF_TOKEN. HF_TOKEN is what a
    caller asks after, so that is the name on record."""
    monkeypatch.setattr(env, "_CASELESS", True, raising=False)

    env.load_dotenv(dotenv(tmp_path, "scribe_t13_lower=1\n"))

    assert "SCRIBE_T13_LOWER" in env.applied()
    assert "scribe_t13_lower" not in env.applied()


# --- writing a line ---------------------------------------------------------------


def test_a_file_saved_with_a_bom_keeps_one_line_for_the_name(tmp_path):
    """Notepad leaves a BOM. Read as plain utf-8 it sticks to the first key,
    `HF_TOKEN` is not recognised, and a second line for it is appended."""
    path = dotenv(tmp_path, "HF_TOKEN=old\nOTHER=keep\n", encoding="utf-8-sig")

    env.write_env("HF_TOKEN", "new", path)

    assert lines_for(path, "HF_TOKEN") == ["HF_TOKEN=new"]
    assert env.parse(path.read_text(encoding="utf-8-sig")) == {"HF_TOKEN": "new", "OTHER": "keep"}
    # Both lines above read past a BOM, so neither can see one come back.
    assert not path.read_bytes().startswith(codecs.BOM_UTF8), "a compose file would read it as part of the name"


def test_a_line_written_with_spaces_is_replaced_not_joined(tmp_path):
    path = dotenv(tmp_path, "  HF_TOKEN = old  \nOTHER=keep\n")

    env.write_env("HF_TOKEN", "new", path)

    assert lines_for(path, "HF_TOKEN") == ["HF_TOKEN=new"]


def test_lines_that_were_already_double_collapse_to_one(tmp_path):
    """Whatever left two lines behind - an older version of this writer, an
    editor - the last one silently won. After a write there is one."""
    path = dotenv(tmp_path, "HF_TOKEN=first\nOTHER=keep\nHF_TOKEN = second\n")

    env.write_env("HF_TOKEN", "third", path)

    assert path.read_text(encoding="utf-8").splitlines() == ["HF_TOKEN=third", "OTHER=keep"]


def test_on_windows_a_line_in_another_case_is_a_line_for_the_same_name(tmp_path, monkeypatch):
    """There `hf_token` and `HF_TOKEN` are one variable. Left standing above
    the new line, the old one would go on winning: load_dotenv fills the name
    from the first and finds it taken when it reaches the second."""
    monkeypatch.setattr(env, "_CASELESS", True, raising=False)
    path = dotenv(tmp_path, "hf_token=old\nOTHER=keep\nHf_Token = older\n")

    env.write_env("HF_TOKEN", "new", path)

    assert path.read_text(encoding="utf-8").splitlines() == ["HF_TOKEN=new", "OTHER=keep"]


def test_where_case_tells_names_apart_the_other_spelling_is_left_alone(tmp_path, monkeypatch):
    """On Linux and macOS `hf_token` is somebody else's variable."""
    monkeypatch.setattr(env, "_CASELESS", False, raising=False)
    path = dotenv(tmp_path, "hf_token=somebody-elses\n")

    env.write_env("HF_TOKEN", "new", path)

    assert path.read_text(encoding="utf-8").splitlines() == ["hf_token=somebody-elses", "HF_TOKEN=new"]


def test_every_other_line_is_left_as_it_was(tmp_path):
    before = "# HF_TOKEN=a comment is not a line for the name\n\nOTHER = spaced  \nthis line is not a pair\n"
    path = dotenv(tmp_path, before)

    env.write_env("HF_TOKEN", "new", path)

    assert path.read_text(encoding="utf-8") == before + "HF_TOKEN=new\n"


def test_a_file_with_windows_line_endings_keeps_them(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"OTHER=keep\r\nHF_TOKEN=old\r\n")

    env.write_env("HF_TOKEN", "new", path)

    assert path.read_bytes() == b"OTHER=keep\r\nHF_TOKEN=new\r\n"


def test_a_last_line_without_a_newline_is_not_run_into(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"OTHER=keep")

    env.write_env("HF_TOKEN", "new", path)

    assert path.read_bytes() == b"OTHER=keep\nHF_TOKEN=new\n"


def test_any_name_can_be_written_and_reads_back(tmp_path):
    """Not only HF_TOKEN: what still writes `.env` after TASK-089.09 is
    SCRIBE_DATA_DIR, and a library lives in folders with spaces in them."""
    path = tmp_path / ".env"
    library = r"D:\My Recordings\MyScribe library"

    env.write_env("SCRIBE_DATA_DIR", library, path)

    assert env.parse(path.read_text(encoding="utf-8")) == {"SCRIBE_DATA_DIR": library}


@pytest.mark.parametrize("value", ["  padded  ", '"quoted"', "'single'", "has # a hash", ""])
def test_a_value_reads_back_as_it_was_written_quoted_only_when_it_has_to_be(tmp_path, value):
    """parse() strips the ends and takes one layer of matching quotes off, so
    a value with either would come back as something else."""
    path = tmp_path / ".env"

    env.write_env("SCRIBE_T10", value, path)

    assert env.parse(path.read_text(encoding="utf-8")) == {"SCRIBE_T10": value}


@pytest.mark.parametrize("name", ["", "TWO WORDS", "1STARTS_WITH_A_DIGIT", "A=B", "# COMMENT", "DASH-ED"])
def test_a_name_that_is_not_a_variable_name_is_refused(tmp_path, name):
    path = dotenv(tmp_path, "OTHER=keep\n")

    with pytest.raises(ValueError):
        env.write_env(name, "x", path)

    assert path.read_text(encoding="utf-8") == "OTHER=keep\n"


@pytest.mark.parametrize("value", ["a\nINJECTED=1", "a\rINJECTED=1", "a\u2028INJECTED=1", "trailing\n"])
def test_a_value_with_a_line_break_is_refused(tmp_path, value):
    """One value, one line. Anything parse() would split on is a second line
    whose contents somebody else chose."""
    path = tmp_path / ".env"

    with pytest.raises(ValueError) as refused:
        env.write_env("HF_TOKEN", value, path)

    assert not path.exists()
    assert "INJECTED" not in str(refused.value), "a refusal never repeats the value"


def test_the_write_goes_through_a_temp_file_and_os_replace(tmp_path, monkeypatch):
    """Never a half-written `.env`: the new text is whole in a neighbour
    before it takes the name."""
    path = dotenv(tmp_path, "HF_TOKEN=old\n")
    moves: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def replace(src, dst):
        moves.append((Path(src), Path(dst)))
        assert Path(dst).read_text(encoding="utf-8") == "HF_TOKEN=old\n", "untouched until the move"
        assert Path(src).read_text(encoding="utf-8") == "HF_TOKEN=new\n", "and the temp file is whole"
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)

    env.write_env("HF_TOKEN", "new", path)

    assert len(moves) == 1
    source, target = moves[0]
    assert target == Path(os.path.realpath(path)) and source != target and source.parent == target.parent
    assert [p.name for p in tmp_path.iterdir()] == [".env"], "no temp file left behind"
    assert path.read_text(encoding="utf-8") == "HF_TOKEN=new\n"


def test_a_move_that_fails_surfaces_and_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    """On Windows os.replace fails while another process holds `.env` open.
    That is an error to report, not a reason to write in place after all."""
    path = dotenv(tmp_path, "HF_TOKEN=old\n")

    def held_open(src, dst):
        raise PermissionError(13, "held open by somebody else")

    monkeypatch.setattr(os, "replace", held_open)

    with pytest.raises(PermissionError):
        env.write_env("HF_TOKEN", "new", path)

    assert path.read_text(encoding="utf-8") == "HF_TOKEN=old\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


def test_a_symlinked_file_is_written_through_and_stays_a_link(tmp_path):
    """A `.env` kept in a dotfiles folder and linked in. Replacing the link
    would leave a regular file here and a stale one there."""
    real = tmp_path / "dotfiles" / "myscribe.env"
    real.parent.mkdir()
    real.write_text("HF_TOKEN=old\n", encoding="utf-8")
    link = tmp_path / ".env"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("this user may not create symlinks here (Windows without developer mode)")

    env.write_env("HF_TOKEN", "new", link)

    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == "HF_TOKEN=new\n"


def test_without_a_path_it_writes_the_file_the_app_reads(tmp_path, environ):
    environ["SCRIBE_ENV_FILE"] = str(tmp_path / "home.env")

    written = env.write_env("SCRIBE_T11", "x")

    assert written == tmp_path / "home.env"
    assert env.load_dotenv() == {"SCRIBE_T11": "x"}


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no file modes to measure")
def test_a_new_file_is_private_to_its_owner(tmp_path):
    path = tmp_path / ".env"
    old_umask = os.umask(0o022)
    try:
        env.write_env("HF_TOKEN", "new", path)
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no file modes to measure")
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o600])
def test_an_existing_file_keeps_the_mode_somebody_chose(tmp_path, mode):
    path = dotenv(tmp_path, "HF_TOKEN=old\n")
    path.chmod(mode)

    env.write_env("HF_TOKEN", "new", path)

    assert stat.S_IMODE(path.stat().st_mode) == mode


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no file owners to keep")
def test_an_existing_file_is_handed_back_to_its_owner_before_the_move(tmp_path, monkeypatch):
    """`sudo python -m scribe.setup --hf-token ...`: the temp file is root's,
    and after the move so is `.env` - 0600, and closed to the user the app
    runs as. The writer this replaced wrote in place and never had to ask."""
    path = dotenv(tmp_path, "HF_TOKEN=old\n")
    was = path.stat()
    steps: list[tuple] = []
    real_replace = os.replace

    def replace(src, dst):
        steps.append(("replace", Path(src)))
        real_replace(src, dst)

    monkeypatch.setattr(os, "chown", lambda file, uid, gid: steps.append(("chown", Path(file), uid, gid)))
    monkeypatch.setattr(os, "replace", replace)

    env.write_env("HF_TOKEN", "new", path)

    assert [step[0] for step in steps] == ["chown", "replace"]
    assert steps[0][1:] == (steps[1][1], was.st_uid, was.st_gid), "the temp file, to the owner and group it replaces"


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no file owners to keep")
def test_an_owner_that_cannot_be_kept_does_not_stop_the_write(tmp_path, monkeypatch):
    """Only root can give a file away. Somebody else who may write here still
    gets their line; the file is theirs afterwards, as any file they made."""
    path = dotenv(tmp_path, "HF_TOKEN=old\n")

    def not_root(file, uid, gid):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "chown", not_root)

    env.write_env("HF_TOKEN", "new", path)

    assert path.read_text(encoding="utf-8") == "HF_TOKEN=new\n"
    assert [p.name for p in tmp_path.iterdir()] == [".env"]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no file owners to keep")
def test_a_file_shared_with_a_group_stays_with_that_group(tmp_path):
    """Nothing stubbed, as far as a user who is not root can show it: the
    0640 somebody chose for a service group is no use without the group."""
    others = [gid for gid in os.getgroups() if gid != os.getegid()]
    if not others:
        pytest.skip("this user is in one group only")
    path = dotenv(tmp_path, "HF_TOKEN=old\n")
    os.chown(path, -1, others[0])
    path.chmod(0o640)

    env.write_env("HF_TOKEN", "new", path)

    assert (path.stat().st_gid, stat.S_IMODE(path.stat().st_mode)) == (others[0], 0o640)


# --- bootstrap: the file, then the tools -----------------------------------------


@pytest.fixture
def repo(tmp_path, monkeypatch, environ):
    """A repository root of this test's own, with no `.env` in it - so the
    developer's real one is never read into a test."""
    monkeypatch.setattr(env, "REPO_DIR", tmp_path)
    monkeypatch.setattr(env, "DEFAULT_PATH", tmp_path / ".env")
    return tmp_path


def test_bootstrap_puts_the_repositorys_tools_first_on_path(repo, environ):
    tools = repo / ".tools" / "bin"
    tools.mkdir(parents=True)
    environ["PATH"] = os.pathsep.join(["/usr/local/bin", "/usr/bin"])

    env.bootstrap()

    assert environ["PATH"].split(os.pathsep) == [str(tools), "/usr/local/bin", "/usr/bin"]


def test_bootstrap_leaves_path_alone_when_there_are_no_tools(repo, environ):
    environ["PATH"] = os.pathsep.join(["/usr/local/bin", "/usr/bin"])

    env.bootstrap()

    assert environ["PATH"] == os.pathsep.join(["/usr/local/bin", "/usr/bin"])


def test_bootstrap_twice_does_not_add_the_tools_twice(repo, environ):
    """Every child inherits PATH, so an entry per call is an entry per
    generation."""
    tools = repo / ".tools" / "bin"
    tools.mkdir(parents=True)
    environ["PATH"] = "/usr/bin"

    env.bootstrap()
    env.bootstrap()

    assert environ["PATH"].split(os.pathsep) == [str(tools), "/usr/bin"]


def test_bootstrap_moves_tools_that_were_further_down_to_the_front(repo, environ):
    tools = repo / ".tools" / "bin"
    tools.mkdir(parents=True)
    environ["PATH"] = os.pathsep.join(["/usr/bin", str(tools)])

    env.bootstrap()

    assert environ["PATH"].split(os.pathsep) == [str(tools), "/usr/bin"]


def test_bootstrap_without_a_path_at_all_makes_one(repo, environ):
    tools = repo / ".tools" / "bin"
    tools.mkdir(parents=True)

    env.bootstrap()

    assert environ["PATH"] == str(tools)


def test_bootstrap_reads_the_file_too(repo, environ):
    (repo / ".env").write_text("SCRIBE_T12_FROM_FILE=yes\n", encoding="utf-8")

    values = env.bootstrap()

    assert values == {"SCRIBE_T12_FROM_FILE": "yes"}
    assert environ["SCRIBE_T12_FROM_FILE"] == "yes"


def test_the_app_bootstraps_before_it_imports_the_app(monkeypatch):
    """scribe.paths fixes DATA_DIR the moment it is imported, and importing
    the app imports it. So `python -m scribe` reads `.env` first - and finds
    its tools in the same call."""
    from scribe import __main__ as main_module

    order: list[str] = []

    class App(types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):  # the import system asks for __path__ first
                raise AttributeError(name)
            order.append(f"from scribe.app import {name}")
            return lambda **kwargs: order.append("create_app")

    class Server:
        def __init__(self, config):
            pass

        def run(self):
            order.append("run")

    monkeypatch.setattr(env, "bootstrap", lambda: order.append("bootstrap") or {}, raising=False)
    monkeypatch.setattr(env, "load_dotenv", lambda *a, **k: order.append("load_dotenv") or {})
    monkeypatch.setitem(sys.modules, "scribe.app", App("scribe.app"))
    monkeypatch.setattr(main_module.uvicorn, "Config", lambda app, **kwargs: None)
    monkeypatch.setattr(main_module.uvicorn, "Server", Server)

    main_module.main(["--no-browser", "--no-supervisor", "--port", "4299"])

    assert order == ["bootstrap", "from scribe.app import create_app", "create_app", "run"]


# --- paths.refresh: the directory `.env` named ------------------------------------


PATH_CONSTANTS = {
    "DATA_DIR": "",
    "DB_PATH": "myscribe.db",
    "MEDIA_DIR": "media",
    "LOGS_DIR": "logs",
    "WORK_DIR": "work",
    "MODELS_DIR": "models",
}


def test_refresh_moves_the_data_directory_and_everything_under_it(tmp_path, monkeypatch):
    """A command has imported scribe.paths before its first line runs, so a
    SCRIBE_DATA_DIR that only `.env` names arrives after DATA_DIR was fixed.
    refresh() is how the command catches up."""
    for name in PATH_CONSTANTS:
        monkeypatch.setattr(paths, name, getattr(paths, name))  # put back afterwards
    moved = tmp_path / "moved library"
    monkeypatch.setenv("SCRIBE_DATA_DIR", str(moved))

    paths.refresh()

    assert {name: getattr(paths, name) for name in PATH_CONSTANTS} == {
        name: moved / leaf for name, leaf in PATH_CONSTANTS.items()
    }
    # A constant added later has to move too, or one corner of a command
    # keeps writing to the old library.
    located = {name: value for name, value in vars(paths).items() if name.isupper() and isinstance(value, Path)}
    assert set(located) == set(PATH_CONSTANTS)

    monkeypatch.delenv("SCRIBE_DATA_DIR")
    paths.refresh()

    assert paths.DATA_DIR == Path(paths.__file__).resolve().parent.parent / "data"
    assert paths.MODELS_DIR == paths.DATA_DIR / "models"
