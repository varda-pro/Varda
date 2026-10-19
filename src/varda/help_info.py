"""What the Help menu shows: system info for bug reports, and how to cite Varda
(from CITATION.cff, the same file GitHub's "Cite this repository" reads)."""

from __future__ import annotations

import platform
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

import numpy as np
import pyproj
import rasterio
import yaml
from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR

from varda.optional_features.torch_runtime import defaultRuntime
from varda.utilities.resources import resource_path
from varda.version import vardaVersion

CITATION_PATH = Path(resource_path("CITATION.cff"))
REPOSITORY_URL = "https://github.com/varda-pro/Varda"
GUIDE_FOLDER = Path(resource_path("docs/user-guide"))
ISSUES_URL = f"{REPOSITORY_URL}/issues/new/choose"


def documentationUrl(version: str | None = None) -> str:
    """The online user guide of this version (its release tag), so the
    methods text matches what is installed; main when the version is unknown."""
    version = vardaVersion() if version is None else version
    ref = "main" if version == "unknown" else f"v{version}"
    return f"{REPOSITORY_URL}/blob/{ref}/docs/user-guide/README.md"


def localGuide() -> Path | None:
    """The user guide shipped with Varda (its README), if present."""
    readme = GUIDE_FOLDER / "README.md"
    return readme if readme.is_file() else None


def _packageVersion(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _torchStatus() -> str:
    version = _packageVersion("torch")
    if version is not None:
        return f"{version} (in Varda's environment)"
    manifest = defaultRuntime().manifest()
    if manifest is not None:
        return f"{manifest['torch']} (Optional Features, {manifest['device']})"
    return "not installed"


def systemInfo() -> str:
    """Plain text to paste into a bug report."""
    frozen = "packaged app" if getattr(sys, "frozen", False) else "from source"
    lines = [
        f"Varda {vardaVersion()} ({frozen})",
        f"Python {platform.python_version()} on {platform.platform()} ({platform.machine()})",
        f"Qt {QT_VERSION_STR}, PyQt6 {PYQT_VERSION_STR}",
        f"numpy {np.__version__}",
        f"rasterio {rasterio.__version__}, GDAL {rasterio.__gdal_version__}",
        f"pyproj {pyproj.__version__}, PROJ {pyproj.proj_version_str}",
        f"Spectral Python (SPy) {_packageVersion('spectral') or 'not installed'}",
        f"HyPyRameter {_packageVersion('hypyrameter') or 'not installed'}",
        f"PyTorch {_torchStatus()}",
    ]
    return "\n".join(lines)


def _citationData() -> dict:
    return yaml.safe_load(CITATION_PATH.read_text())


def citation() -> tuple[str, str]:
    """(APA, BibTeX) for the version that is running."""
    data = _citationData()
    authors = data["authors"]
    version = vardaVersion()
    if version == "unknown":
        version = str(data.get("version", ""))
    initials = [f"{a['family-names']}, {a['given-names'][0]}." for a in authors]
    names = (
        initials[0]
        if len(initials) == 1
        else ", ".join(initials[:-1]) + ", & " + initials[-1]
    )
    doi = data.get("doi")
    link = f"https://doi.org/{doi}" if doi else data["repository-code"]
    apa = f"{names} ({_year(data)}). {data['title']} (Version {version}) [Computer software]. {link}"
    bibtex = (
        "@software{varda,\n"
        f"  author = {{{' and '.join(f'{a["family-names"]}, {a["given-names"]}' for a in authors)}}},\n"
        f"  title = {{{data['title']}}},\n"
        f"  version = {{{version}}},\n"
        f"  year = {{{_year(data)}}},\n"
        f"  url = {{{data['repository-code']}}},\n"
        + (f"  doi = {{{doi}}},\n" if doi else "")
        + f"  license = {{{data['license']}}}\n"
        "}"
    )
    return apa, bibtex


def _year(data: dict) -> str:
    """The release year, or this year for a development version."""
    released = data.get("date-released")
    return str(released)[:4] if released else str(date.today().year)
