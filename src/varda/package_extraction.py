"""Where an opened ``.varda.zip`` is unpacked, and keeping that folder small.

Qt-free. Each package is extracted once into ``<root>/sessions/<name>-<key>``,
where the key identifies the package's content (a hash of its manifest, else of
its path, size and modification time). Reopening the same package reuses the
folder; a marker file written after the last file says the extraction is
complete. ``cleanExtractions`` removes the folders nothing refers to.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import zipfile
from collections.abc import Iterable
from pathlib import Path

from varda.project_package import MANIFEST_NAME, formatBytes
from varda.session import BUNDLE_SESSION_NAME, BUNDLE_SUFFIX, openSessionBundle

logger = logging.getLogger(__name__)

SESSIONS_FOLDER = "sessions"
COMPLETE_MARKER = ".varda-extracted"
MARGIN_BYTES = 256 << 20  # kept free on top of the package's size
MARGIN_FRACTION = 0.05
# Folders Varda made: <name>-<key>, or the old <name>-<YYYYmmdd-HHMMSS>
_OURS = re.compile(r".+-(?:[0-9a-f]{16}|\d{8}-\d{6})$")


def packageKey(bundle: Path) -> str:
    """Identifies the package's content: its manifest's hash if it has one,
    else its absolute path, size and modification time."""
    digest = hashlib.sha256()
    with zipfile.ZipFile(bundle) as archive:
        if MANIFEST_NAME in archive.namelist():
            digest.update(archive.read(MANIFEST_NAME))
            return digest.hexdigest()[:16]
    info = bundle.stat()
    digest.update(f"{bundle.resolve()}|{info.st_size}|{info.st_mtime_ns}".encode())
    return digest.hexdigest()[:16]


def extractionFolder(bundle: Path, root: Path) -> Path:
    name = bundle.name.removesuffix(BUNDLE_SUFFIX)
    return root / SESSIONS_FOLDER / f"{name}-{packageKey(bundle)}"


def _requireRoom(bundle: Path, folder: Path) -> None:
    with zipfile.ZipFile(bundle) as archive:
        needed = sum(info.file_size for info in archive.infolist())
    needed += max(MARGIN_BYTES, int(needed * MARGIN_FRACTION))
    free = shutil.disk_usage(folder.parent).free
    if needed > free:
        raise OSError(
            f"Not enough disk space to open {bundle.name}: it unpacks to about "
            f"{formatBytes(needed)} (with a margin) in {folder.parent}, but only "
            f"{formatBytes(free)} is free. Free some space and try again."
        )


def extractPackage(bundle: str | Path, root: str | Path) -> Path:
    """The session file of ``bundle``, extracted under ``root`` unless an
    earlier, complete extraction of the same package is there."""
    bundle, root = Path(bundle), Path(root)
    folder = extractionFolder(bundle, root)
    if (folder / COMPLETE_MARKER).is_file() and (
        folder / BUNDLE_SESSION_NAME
    ).is_file():
        logger.info("Reusing the extraction of %s in %s", bundle.name, folder)
        return folder / BUNDLE_SESSION_NAME
    folder.parent.mkdir(parents=True, exist_ok=True)
    if folder.is_dir() and not folder.is_symlink():
        shutil.rmtree(folder)  # an interrupted extraction
    _requireRoom(bundle, folder)
    try:
        session = openSessionBundle(bundle, folder)
        (folder / COMPLETE_MARKER).write_text(bundle.name)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return session


def cleanExtractions(
    root: str | Path, autosave: str | Path | None, keep: Iterable[str | Path] = ()
) -> list[Path]:
    """Remove the extracted packages under ``root/sessions`` that the
    ``autosave`` file doesn't mention and none of the ``keep`` paths (the
    folder just opened, the open images' files) is inside. Returns what was
    removed. When in doubt (an unreadable autosave) nothing is removed."""
    sessions = Path(root) / SESSIONS_FOLDER
    if not sessions.is_dir() or sessions.is_symlink():
        return []
    referred = ""
    if autosave is not None and Path(autosave).exists():
        try:
            referred = Path(autosave).read_text()
        except (OSError, UnicodeDecodeError):
            logger.warning("Autosave unreadable; keeping all extracted packages")
            return []
    kept = [Path(p).resolve() for p in keep]
    removed = []
    for folder in sorted(sessions.iterdir()):
        if folder.is_symlink() or not folder.is_dir():
            continue
        if not (_OURS.match(folder.name) or (folder / COMPLETE_MARKER).is_file()):
            continue
        resolved = folder.resolve()
        if resolved.parent != sessions.resolve():
            continue
        if any(resolved == k or resolved in k.parents for k in kept):
            continue
        if str(folder) in referred or str(resolved) in referred:
            continue
        try:
            shutil.rmtree(folder)
        except OSError as error:
            logger.warning("Could not remove %s: %s", folder, error)
            continue
        logger.info("Removed the old extracted package %s", folder)
        removed.append(folder)
    return removed
