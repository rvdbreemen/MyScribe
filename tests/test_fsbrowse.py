"""Phase 3 Task 4: the server-side path listing behind "pick a file on this
machine".

Two things matter here. The listing says which entries are worth clicking
(directories, and files whose extension looks like media) without ever
opening one - ffprobe stays the arbiter, inside the job. And the browse panel
only ever walks paths the server handed out: a client-supplied `..` is refused
rather than normalised, and every path is checked against the allowed roots
after symlinks and junctions have been resolved.
"""

import os
import sys
from pathlib import Path

import pytest

from scribe import fsbrowse


@pytest.fixture
def tree(tmp_path):
    """A directory with a media file, a text file, a subfolder and a dotfile."""
    (tmp_path / "talk.wav").write_bytes(b"RIFF" * 100)
    (tmp_path / "notes.txt").write_text("not media", encoding="utf-8")
    (tmp_path / "Archive").mkdir()
    (tmp_path / ".hidden").write_text("", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    return tmp_path


# --- listdir -------------------------------------------------------------------


def test_listdir_marks_media_by_extension_and_lists_directories(tree):
    listing = fsbrowse.listdir(tree)

    assert listing["path"] == str(tree)
    assert listing["parent"] == str(tree.parent)
    assert listing["dirs"] == ["Archive"]
    assert listing["files"] == [
        ("notes.txt", len(b"not media"), False),
        ("talk.wav", 400, True),
    ]


def test_listdir_takes_a_string_and_upper_case_extensions(tree):
    (tree / "LOUD.MP3").write_bytes(b"x")

    listing = fsbrowse.listdir(str(tree))

    assert ("LOUD.MP3", 1, True) in listing["files"]


def test_listdir_skips_dot_entries(tree):
    listing = fsbrowse.listdir(tree)

    assert ".git" not in listing["dirs"]
    assert not any(name.startswith(".") for name, _, _ in listing["files"])


def test_listdir_rejects_parent_traversal(tree):
    with pytest.raises(ValueError):
        fsbrowse.listdir(tree / "..")
    with pytest.raises(ValueError):
        fsbrowse.listdir(str(tree / "Archive" / ".." / ".." / "elsewhere"))


def test_listdir_of_a_missing_path_or_a_file_raises(tree):
    with pytest.raises(FileNotFoundError):
        fsbrowse.listdir(tree / "nope")
    with pytest.raises(NotADirectoryError):
        fsbrowse.listdir(tree / "talk.wav")


def test_listdir_at_a_filesystem_root_has_no_parent(tmp_path):
    root = Path(tmp_path.anchor)

    listing = fsbrowse.listdir(root)

    assert listing["parent"] is None


# --- is_allowed ------------------------------------------------------------------


def test_is_allowed_inside_and_outside_the_roots(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    roots = (inside,)

    assert fsbrowse.is_allowed(inside / "talk.wav", roots)
    assert fsbrowse.is_allowed(inside / "deeper" / "talk.wav", roots)
    assert fsbrowse.is_allowed(inside, roots)
    assert not fsbrowse.is_allowed(tmp_path / "outside" / "talk.wav", roots)
    assert not fsbrowse.is_allowed(tmp_path, roots)
    # A sibling whose name merely starts with the root's name is not inside it.
    assert not fsbrowse.is_allowed(tmp_path / "insidious" / "talk.wav", roots)


def test_is_allowed_refuses_traversal_even_when_it_would_land_inside(tmp_path):
    roots = (tmp_path,)

    assert not fsbrowse.is_allowed(tmp_path / "a" / ".." / "talk.wav", roots)
    assert not fsbrowse.is_allowed(str(tmp_path) + os.sep + "..", roots)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths are case-insensitive")
def test_is_allowed_ignores_case_on_windows(tmp_path):
    roots = (tmp_path,)

    assert fsbrowse.is_allowed(Path(str(tmp_path).upper()) / "talk.wav", roots)
    assert fsbrowse.is_allowed(Path(str(tmp_path).lower()) / "talk.wav", roots)


def link_dir(target: Path, link: Path) -> None:
    """``link`` -> ``target``: a junction on Windows (no privilege needed,
    unlike a symlink there), a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def test_is_allowed_follows_a_junction_out_of_the_root(tmp_path):
    """The documented rule: a link inside a root that points outside it is
    not a way out. Both sides are resolved first, so the target decides."""
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "talk.wav").write_bytes(b"x")
    link = inside / "elsewhere"
    link_dir(outside, link)
    assert (link / "talk.wav").is_file()  # the link works; the guard is what says no

    assert not fsbrowse.is_allowed(link, (inside,))
    assert not fsbrowse.is_allowed(link / "talk.wav", (inside,))

    # And the other way round: a link that lands inside a root is inside it.
    back = outside / "home"
    link_dir(inside, back)
    assert fsbrowse.is_allowed(back / "talk.wav", (inside,))


def test_is_allowed_uses_the_module_roots_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))

    assert fsbrowse.is_allowed(tmp_path / "talk.wav")
    assert not fsbrowse.is_allowed(tmp_path.parent / "talk.wav")


def test_default_roots_cover_the_users_home(tmp_path, monkeypatch):
    """The documented rule, against a made-up home rather than this machine's:
    the whole home drive on Windows, the home directory elsewhere."""
    home = tmp_path / "Users" / "someone"
    home.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    roots = fsbrowse.default_roots()

    assert roots == ((Path(home.anchor),) if os.name == "nt" else (home,))
    assert fsbrowse.is_allowed(home / "Recordings" / "talk.wav", roots)


# --- the setting -----------------------------------------------------------------


def test_roots_from_setting_parse_one_path_per_line(tmp_path):
    text = f"  {tmp_path / 'a'}  \n\n{tmp_path / 'b'}\n"

    assert fsbrowse.roots_from_setting(text) == (tmp_path / "a", tmp_path / "b")


def test_roots_from_an_empty_setting_fall_back_to_the_defaults(monkeypatch, tmp_path):
    monkeypatch.setattr(fsbrowse, "ALLOWED_ROOTS", (tmp_path,))

    assert fsbrowse.roots_from_setting(None) == (tmp_path,)
    assert fsbrowse.roots_from_setting("  \n ") == (tmp_path,)
