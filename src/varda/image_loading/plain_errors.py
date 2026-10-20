"""Load and export errors in plain words, with what to do next.

GDAL and rasterio report problems in their own terms ("not recognized as
being in a supported file format", "TIFFReadDirectory: Failed to read
directory at offset …"), which say neither what is wrong with the file nor
what to do. These turn the common ones into a sentence for the user; the
library's text stays as the details, for a bug report.
"""

from __future__ import annotations

import re
from pathlib import Path

from attrs import frozen

FORMATS = (
    "Varda reads ENVI (.img with its .hdr), GeoTIFF, PDS3 and PDS4 labels, "
    "and most other raster formats."
)


@frozen
class PlainError:
    message: str  # what went wrong and what to do next
    details: str  # the library's own text


def _has(text: str, *patterns: str) -> bool:
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def _hasNoBands(path: Path) -> bool:
    """An ENVI header (``path`` or the one beside it) saying ``bands = 0``."""
    header = path if path.suffix.lower() == ".hdr" else path.with_suffix(".hdr")
    try:
        text = header.read_text(errors="replace")
    except OSError:
        return False
    return re.search(r"^\s*bands\s*=\s*0\s*$", text, re.I | re.M) is not None


def explainOpenError(path: str, error: BaseException) -> PlainError:
    """Why ``path`` could not be opened as an image, and what to do."""
    raw = str(error)
    file = Path(path)
    name = file.name or path
    if file.is_dir():
        message = (
            f"{name} is a folder, not an image. Choose the image file inside it "
            "(for ENVI the .img or .hdr, for PDS the .lbl or .xml label)."
        )
    elif not file.exists():
        message = (
            f"{path} doesn't exist: it may have been moved, renamed or deleted. "
            "Choose the file again."
        )
    elif isinstance(error, PermissionError) or _has(raw, r"permission denied"):
        message = (
            f"Varda isn't allowed to read {name}. Check the file's permissions, "
            "or copy it to a folder of your own and open the copy."
        )
    elif file.stat().st_size == 0:
        message = (
            f"{name} is empty (0 bytes): it was probably not fully downloaded or "
            "copied. Download or copy it again."
        )
    elif _hasNoBands(file):
        message = (
            f"The ENVI header of {name} says the image has no bands (bands = 0), "
            "so there is nothing to show. Check the header, or export the image "
            "again from the program that made it."
        )
    elif _has(
        raw,
        r"TIFFRead",
        r"failed to read",
        r"truncat",
        r"read error",
        r"IReadBlock failed",
        r"unexpected end",
    ):
        message = (
            f"{name} seems damaged or incomplete: part of it can't be read. "
            "Download or copy it again."
        )
    elif _has(raw, r"not recognized as (being in )?a supported file format"):
        message = (
            f"{name} isn't an image Varda can read. {FORMATS} Choose a file in "
            "one of these formats, or convert it first."
        )
    else:
        message = (
            f"Varda could not open {name}: {raw.rstrip('.')}. Check that it is a complete "
            "image file; Copy System Info (Help menu) and the log help a bug report."
        )
    return PlainError(message, raw)


def explainWriteError(path: str, error: BaseException) -> PlainError:
    """Why an image could not be written to ``path``, and what to do."""
    raw = str(error)
    target = Path(path)
    if not target.parent.is_dir():
        message = (
            f"The folder {target.parent} doesn't exist. Choose another folder "
            "with Browse…, or create it first."
        )
    elif isinstance(error, PermissionError) or _has(raw, r"permission denied"):
        message = (
            f"Varda isn't allowed to write in {target.parent}. Choose a folder "
            "of your own (e.g. Documents) with Browse…."
        )
    elif _has(raw, r"no space left", r"disk full", r"not enough space"):
        message = (
            f"The disk holding {target.parent} is full. Free some space or "
            "choose another disk."
        )
    else:
        message = (
            f"Varda could not write {target.name}: {raw.rstrip('.')}. Try another folder or "
            "name with Browse…."
        )
    return PlainError(message, raw)
