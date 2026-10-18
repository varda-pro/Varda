"""Opened packages are extracted once, and old extractions are cleaned up."""

import json
import os
import shutil
import zipfile
from collections import namedtuple

import pytest

from varda.package_extraction import (
    COMPLETE_MARKER,
    cleanExtractions,
    extractPackage,
)

Usage = namedtuple("Usage", "total used free")


def _package(path, content="a", manifest=True):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("session.varda", "{}")
        archive.writestr("data/image.img", content * 100)
        if manifest:
            archive.writestr("manifest.json", content)
    return path


def _sessions(root):
    return sorted(p.name for p in (root / "sessions").iterdir())


def test_same_package_twice_uses_one_folder(tmp_path):
    root = tmp_path / "appdata"
    package = _package(tmp_path / "p.varda.zip")
    first = extractPackage(package, root)
    (first.parent / "data" / "image.img").write_text("marker")
    second = extractPackage(package, root)
    assert first == second
    assert len(_sessions(root)) == 1
    assert (first.parent / "data" / "image.img").read_text() == "marker"  # reused


def test_package_without_manifest_is_keyed_on_path_size_and_mtime(tmp_path):
    root = tmp_path / "appdata"
    package = _package(tmp_path / "p.varda.zip", manifest=False)
    assert extractPackage(package, root) == extractPackage(package, root)
    _package(package, "bb", manifest=False)
    extractPackage(package, root)
    assert len(_sessions(root)) == 2


def _age(folder, seconds):
    os.utime(folder, (seconds, seconds))


def test_changed_package_gets_a_new_folder_and_the_old_one_is_cleaned(tmp_path):
    root = tmp_path / "appdata"
    package = _package(tmp_path / "p.varda.zip", "a")
    old = extractPackage(package, root).parent
    _package(package, "b")
    new = extractPackage(package, root).parent
    _age(old, 1000)
    assert old != new
    assert cleanExtractions(root, None, keep=[new]) == [old]
    assert _sessions(root) == [new.name]


def test_only_extraction_of_a_package_is_kept(tmp_path):
    root = tmp_path / "appdata"
    folder = extractPackage(_package(tmp_path / "a.varda.zip"), root).parent
    elsewhere = tmp_path / "mine.varda"  # saved by the user, points into it
    elsewhere.write_text(json.dumps({"images": [str(folder / "data" / "image.img")]}))
    assert cleanExtractions(root, tmp_path / "autosave.varda") == []
    assert folder.exists()


def test_duplicates_go_oldest_first_keeping_newest_and_referenced(tmp_path):
    root = tmp_path / "appdata"
    sessions = root / "sessions"
    names = [
        "p-20250101-000000",  # legacy
        "p-0000000000000001",
        "p-0000000000000002",
        "p-0000000000000003",
        "q-0000000000000004",  # another package's only copy
    ]
    for age, name in enumerate(names):
        (sessions / name).mkdir(parents=True)
        _age(sessions / name, 1000 + age)
    autosave = tmp_path / "autosave.varda"
    autosave.write_text(json.dumps({"images": [str(sessions / names[1] / "x.img")]}))
    removed = cleanExtractions(root, autosave)
    assert [f.name for f in removed] == [names[0], names[2]]
    assert _sessions(root) == [names[1], names[3], names[4]]


def test_escaped_paths_in_the_autosave_keep_their_folder(tmp_path):
    root = tmp_path / "appdata"
    sessions = root / "sessions"
    old = sessions / "p\u00e9-0000000000000001"
    new = sessions / "p\u00e9-0000000000000002"
    for folder in (old, new):
        folder.mkdir(parents=True)
    _age(old, 1000)
    # json.dumps escapes backslashes and non-ASCII characters, as on Windows
    autosave = tmp_path / "autosave.varda"
    autosave.write_text(json.dumps({"images": [str(old / "data" / "a.img")]}))
    assert "\\u00e9" in autosave.read_text()
    assert cleanExtractions(root, autosave) == []
    assert old.exists()


def test_folder_referenced_by_the_autosave_is_kept(tmp_path):
    root = tmp_path / "appdata"
    package = _package(tmp_path / "a.varda.zip", "a")
    old = extractPackage(package, root).parent
    _package(package, "b")
    new = extractPackage(package, root).parent
    _age(old, 1000)
    autosave = tmp_path / "autosave.varda"
    autosave.write_text(json.dumps({"images": [str(old / "data" / "image.img")]}))
    assert cleanExtractions(root, autosave) == []
    assert old.exists() and new.exists()


def test_unreadable_autosave_keeps_everything(tmp_path):
    root = tmp_path / "appdata"
    folder = extractPackage(_package(tmp_path / "a.varda.zip"), root).parent
    autosave = tmp_path / "autosave.varda"
    autosave.write_text("{not json")
    assert cleanExtractions(root, autosave) == []
    assert folder.exists()


def test_open_images_keep_their_folder(tmp_path):
    root = tmp_path / "appdata"
    folder = extractPackage(_package(tmp_path / "a.varda.zip"), root).parent
    package = _package(tmp_path / "a.varda.zip", "b")
    new = extractPackage(package, root).parent
    _age(folder, 1000)
    assert cleanExtractions(root, None, keep=[folder / "data" / "image.img"]) == []
    assert new.exists()


def test_incomplete_extraction_is_redone(tmp_path):
    root = tmp_path / "appdata"
    package = _package(tmp_path / "p.varda.zip")
    session = extractPackage(package, root)
    (session.parent / COMPLETE_MARKER).unlink()
    (session.parent / "data" / "image.img").unlink()
    extractPackage(package, root)
    assert (session.parent / "data" / "image.img").is_file()
    assert (session.parent / COMPLETE_MARKER).is_file()


def test_low_disk_space_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "appdata"
    package = _package(tmp_path / "p.varda.zip")
    monkeypatch.setattr(shutil, "disk_usage", lambda _p: Usage(10, 10, 100))
    with pytest.raises(OSError, match="Not enough disk space"):
        extractPackage(package, root)
    assert _sessions(root) == []


def test_nothing_outside_sessions_is_touched(tmp_path):
    root = tmp_path / "appdata"
    package = _package(tmp_path / "p.varda.zip")
    extractPackage(package, root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    (root / "autosave.varda").write_text("{}")
    (root / "logs").mkdir()
    (root / "sessions" / "not-ours").mkdir()  # no marker, odd name
    (root / "sessions" / "link-0123456789abcdef").symlink_to(outside)
    (root / "sessions" / "file-0123456789abcdef").write_text("f")
    cleanExtractions(root, None)
    assert (outside / "keep.txt").read_text() == "x"
    assert (root / "autosave.varda").exists() and (root / "logs").exists()
    assert (root / "sessions" / "not-ours").exists()
    assert (root / "sessions" / "link-0123456789abcdef").is_symlink()
    assert (root / "sessions" / "file-0123456789abcdef").is_file()
    assert package.exists()
