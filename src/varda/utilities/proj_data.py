"""Pins GDAL and PROJ to the data bundled with Varda's own rasterio and pyproj.

A shell with another installation active (e.g. conda) exports its own
``PROJ_DATA``/``PROJ_LIB``/``GDAL_DATA``. rasterio's GDAL then reads an older
``proj.db`` that lacks the planetary ``IAU_2015`` codes (or has an incompatible
layout), and creating a CRS fails. ``pinProjData`` runs before rasterio or
pyproj load, and overrides only what is unusable. Qt-free: it uses the
standard library only and does not import rasterio or pyproj.
"""

import importlib.util
import os
import sqlite3
from collections.abc import MutableMapping
from pathlib import Path

OPT_OUT_VARIABLE = "VARDA_KEEP_PROJ_DATA"
PROJ_VARIABLES = ("PROJ_DATA", "PROJ_LIB")
IAU_AUTHORITY = "IAU_2015"


def packageDirectory(name: str) -> Path | None:
    """Where an installed package lives, found without importing it."""
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def bundledProjData() -> Path | None:
    """rasterio's PROJ data (GDAL is the consumer), else pyproj's."""
    for package, folder in (
        ("rasterio", "proj_data"),
        ("pyproj", "proj_dir/share/proj"),
    ):
        root = packageDirectory(package)
        if root is not None and (root / folder / "proj.db").is_file():
            return root / folder
    return None


def bundledGdalData() -> Path | None:
    root = packageDirectory("rasterio")
    if root is not None and (root / "gdal_data").is_dir():
        return root / "gdal_data"
    return None


def databaseLayout(db: Path) -> tuple[int, int] | None:
    """The (major, minor) DATABASE.LAYOUT.VERSION of a proj.db, None if unreadable."""
    try:
        with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
            rows = dict(
                connection.execute(
                    "SELECT key, value FROM metadata WHERE key LIKE 'DATABASE.LAYOUT.VERSION.%'"
                ).fetchall()
            )
        return int(rows["DATABASE.LAYOUT.VERSION.MAJOR"]), int(
            rows["DATABASE.LAYOUT.VERSION.MINOR"]
        )
    except (sqlite3.Error, KeyError, ValueError):
        return None


def hasIauAuthority(db: Path) -> bool:
    try:
        with sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT 1 FROM geodetic_crs WHERE auth_name = ? LIMIT 1",
                (IAU_AUTHORITY,),
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _unusableReason(configured: str, bundled: Path) -> str | None:
    """Why the proj.db found in a PROJ_DATA value can't be used, None if it can."""
    candidates = [Path(p) / "proj.db" for p in configured.split(os.pathsep) if p]
    db = next((c for c in candidates if c.is_file()), None)
    if db is None:
        return "it has no proj.db"
    layout, bundledLayout = databaseLayout(db), databaseLayout(bundled / "proj.db")
    if layout is None:
        return "its proj.db is unreadable"
    if bundledLayout is not None and layout < bundledLayout:
        return f"its proj.db layout {layout[0]}.{layout[1]} is older than the bundled {bundledLayout[0]}.{bundledLayout[1]}"
    if not hasIauAuthority(db):
        return f"its proj.db lacks the {IAU_AUTHORITY} authority (planetary CRSs)"
    return None


def _gdalDataProblem(configured: str, bundled: Path) -> str | None:
    """Why a GDAL_DATA outside rasterio can't serve rasterio's GDAL, else None.
    Conservative: only the schema (.xsd) files GDAL validates against count."""
    folder = Path(configured)
    if not folder.is_dir():
        return "it is not a folder"
    missing = [f.name for f in bundled.glob("*.xsd") if not (folder / f.name).is_file()]
    return f"it lacks {', '.join(sorted(missing))}" if missing else None


def pinProjData(
    environ: MutableMapping[str, str] = os.environ,
    projData: Path | None = None,
    gdalData: Path | None = None,
) -> list[str]:
    """Override an unusable PROJ_DATA/PROJ_LIB/GDAL_DATA in ``environ`` with the
    bundled data. ``VARDA_KEEP_PROJ_DATA=1`` leaves everything alone. Returns a
    message for each override, to log."""
    if environ.get(OPT_OUT_VARIABLE) == "1":
        return []
    projData = projData or bundledProjData()
    gdalData = gdalData or bundledGdalData()
    messages = []
    if projData is not None:
        for variable in PROJ_VARIABLES:
            configured = environ.get(variable)
            reason = _unusableReason(configured, projData) if configured else None
            if reason:
                messages.append(
                    f"{variable}={configured} replaced by the bundled PROJ data ({projData}): {reason}"
                )
                environ["PROJ_DATA"] = str(projData)
                environ.pop("PROJ_LIB", None)
                break
    configured = environ.get("GDAL_DATA")
    if gdalData is not None and configured:
        inside = Path(configured).resolve().is_relative_to(gdalData.parent.resolve())
        reason = None if inside else _gdalDataProblem(configured, gdalData)
        if reason:
            messages.append(
                f"GDAL_DATA={configured} replaced by the bundled GDAL data ({gdalData}): {reason}"
            )
            environ["GDAL_DATA"] = str(gdalData)
    return messages
