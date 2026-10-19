"""Load and export errors say what went wrong and what to do next, with the
library's own text kept as details (release QA #4)."""

from pathlib import Path

import pytest

from varda.image_loading.plain_errors import explainOpenError, explainWriteError

GDAL_UNKNOWN = "'{path}' not recognized as being in a supported file format."


def _unknown(path: Path) -> Exception:
    return Exception(GDAL_UNKNOWN.format(path=path))


def test_a_folder(tmp_path):
    error = explainOpenError(str(tmp_path), _unknown(tmp_path))
    assert "is a folder" in error.message
    assert "Choose the image file inside it" in error.message
    assert error.details == str(_unknown(tmp_path))


def test_a_folder_on_windows_says_permission_denied(tmp_path):
    error = explainOpenError(str(tmp_path), PermissionError(13, "Permission denied"))
    assert "is a folder" in error.message


def test_a_missing_file(tmp_path):
    path = tmp_path / "gone.tif"
    error = explainOpenError(str(path), Exception(f"{path}: No such file or directory"))
    assert "doesn't exist" in error.message


def test_an_empty_file(tmp_path):
    path = tmp_path / "empty.tif"
    path.write_bytes(b"")
    error = explainOpenError(str(path), _unknown(path))
    assert "is empty" in error.message
    assert "again" in error.message


def test_a_file_in_no_format_varda_reads(tmp_path):
    path = tmp_path / "noise.tif"
    path.write_bytes(b"\x01\x02junk" * 100)
    error = explainOpenError(str(path), _unknown(path))
    assert "isn't an image Varda can read" in error.message
    assert "GeoTIFF" in error.message and "ENVI" in error.message


def test_a_truncated_file(tmp_path):
    path = tmp_path / "truncated.tif"
    path.write_bytes(b"II*\x00" + b"\x00" * 100)
    raw = "truncated.tif: TIFFReadDirectory:Failed to read directory at offset 492418"
    error = explainOpenError(str(path), Exception(raw))
    assert "damaged or incomplete" in error.message
    assert error.details == raw


def test_an_envi_header_with_no_bands(tmp_path):
    header = tmp_path / "zero.hdr"
    header.write_text("ENVI\nsamples = 4\nlines = 4\nbands = 0\ndata type = 4\n")
    (tmp_path / "zero.img").write_bytes(b"")
    error = explainOpenError(str(header), _unknown(tmp_path / "zero.img"))
    assert "no bands" in error.message
    assert "zero.hdr" in error.message


def test_permission_denied_on_a_file(tmp_path):
    path = tmp_path / "locked.tif"
    path.write_bytes(b"II*\x00")
    error = explainOpenError(str(path), PermissionError(13, "Permission denied"))
    assert "isn't allowed to read" in error.message


def test_an_unknown_error_keeps_its_text(tmp_path):
    path = tmp_path / "odd.tif"
    path.write_bytes(b"II*\x00")
    error = explainOpenError(str(path), RuntimeError("something odd"))
    assert "odd.tif" in error.message and "something odd" in error.message


def test_export_to_a_missing_folder(tmp_path):
    path = tmp_path / "nowhere" / "x.tif"
    raw = f"Attempt to create new tiff file '{path}' failed: No such file or directory"
    error = explainWriteError(str(path), Exception(raw))
    assert "The folder" in error.message and "doesn't exist" in error.message
    assert error.details == raw


@pytest.mark.parametrize(
    "raw",
    [
        "Permission denied",
        "Attempt to create new tiff file 'x' failed: Permission denied",
    ],
)
def test_export_without_permission(tmp_path, raw):
    error = explainWriteError(str(tmp_path / "x.tif"), Exception(raw))
    assert "isn't allowed to write" in error.message


def test_export_on_a_full_disk(tmp_path):
    error = explainWriteError(
        str(tmp_path / "x.tif"), OSError(28, "No space left on device")
    )
    assert "full" in error.message
