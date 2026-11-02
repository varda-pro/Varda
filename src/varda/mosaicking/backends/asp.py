"""The Ames Stereo Pipeline (ASP) as an optional matcher for Auto Align.

ASP is found where people usually have it, or downloaded (the pinned stable
release, checked against GitHub's SHA-256) into Varda's app-data folder. Its
``image_align`` reads only an image's first band, so Varda gives it one band
at a time: each chosen band of the two images' overlap, already resampled
onto one grid. The matches it keeps come back as tie points, which Varda
pools over the bands and fits robustly like its own.

There are no native Windows builds (only WSL), so none is offered there.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import ssl
import struct
import subprocess
import tarfile
import threading
import urllib.request
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path

import certifi
import numpy as np
import rasterio
from attrs import frozen
from rasterio.errors import NotGeoreferencedWarning

ASP_VERSION = "3.7.0"
TOOL = "image_align"
# Conda installations people keep ASP in (envs named asp, or holding ASP)
CONDA_ROOTS = (
    "miniforge3",
    "miniconda3",
    "anaconda3",
    "mambaforge",
    "micromamba",
    "opt/conda",
)
DOWNLOAD_CHUNK = 1 << 20
# Seconds a stalled connection waits for data before the download fails
DOWNLOAD_TIMEOUT_S = 60
VERSION_TIMEOUT_S = 20
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AspError(RuntimeError):
    """ASP couldn't be found, downloaded or run; the message is for the user."""


@frozen
class AspRelease:
    platform: str
    url: str
    sha256: str
    sizeMB: float


_BASE = "https://github.com/NeoGeographyToolkit/StereoPipeline/releases/download/3.7.0/"
RELEASES = {
    ("Darwin", "arm64"): AspRelease(
        "macOS (Apple silicon)",
        _BASE + "StereoPipeline-3.7.0-2026-06-08-arm64-OSX.tar.bz2",
        "54e5e3a3749924520829e43397aa1c238590ef85fd855f4b0e576cacd09b583d",
        261.3,
    ),
    ("Darwin", "x86_64"): AspRelease(
        "macOS (Intel)",
        _BASE + "StereoPipeline-3.7.0-2026-06-08-x86_64-OSX.tar.bz2",
        "94c792c1ed23a114b696d3264631fe244372b07f0e32bdace45fbdf807104124",
        347.4,
    ),
    ("Linux", "x86_64"): AspRelease(
        "Linux (x86-64)",
        _BASE + "StereoPipeline-3.7.0-2026-06-08-x86_64-Linux.tar.bz2",
        "46ac51b282ca1b90e7089f6bb808d7cff548953eeab6c36cb12334089b2706d2",
        649.0,
    ),
    ("Linux", "aarch64"): AspRelease(
        "Linux (ARM)",
        _BASE + "StereoPipeline-3.7.0-2026-06-08-aarch64-Linux.tar.bz2",
        "57b250d3067c55acb3f83a882bde9fa8df2a144c026d713de7e4207c5ff723d7",
        422.8,
    ),
}


def releaseFor(
    system: str | None = None, machine: str | None = None
) -> AspRelease | None:
    """The release for this computer (None on Windows)."""
    system = system or platform.system()
    machine = (machine or platform.machine()).lower()
    machine = {
        "amd64": "x86_64",
        "arm64": "arm64" if system == "Darwin" else "aarch64",
    }.get(machine, machine)
    return RELEASES.get((system, machine))


@frozen
class AspInstall:
    bin: Path  # the folder holding image_align
    version: str
    source: str  # where it was found, for the user

    @property
    def tool(self) -> Path:
        return self.bin / TOOL


def parseVersion(text: str) -> str | None:
    match = re.search(r"Stereo Pipeline\s+(\d+(?:\.\d+)+)", text)
    return match.group(1) if match else None


def _version(folder: Path) -> str | None:
    tool = folder / TOOL
    if not tool.is_file() or not os.access(tool, os.X_OK):
        return None
    try:
        result = subprocess.run(
            [str(tool), "--version"],
            capture_output=True,
            text=True,
            timeout=VERSION_TIMEOUT_S,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return parseVersion(result.stdout + result.stderr)


def _candidates(
    chosen: str | None, downloads: Path | None, env: Mapping[str, str], home: Path
) -> list[tuple[Path, str]]:
    """Folders that might hold image_align, in the order they're tried."""
    found: list[tuple[Path, str]] = []
    if chosen:
        folder = Path(chosen).expanduser()
        found += [(folder, "chosen folder"), (folder / "bin", "chosen folder")]
    found += [(Path(p), "PATH") for p in env.get("PATH", "").split(os.pathsep) if p]
    for root in CONDA_ROOTS:
        envs = (
            (home / root / "envs")
            if not root.startswith("opt")
            else Path("/") / root / "envs"
        )
        if not envs.is_dir():
            continue
        named = [envs / "asp"] if (envs / "asp").is_dir() else []
        holding = sorted(
            e
            for e in envs.iterdir()
            if e.is_dir()
            and e not in named
            and any((e / "conda-meta").glob("stereo-pipeline-*.json"))
        )
        found += [(e / "bin", f"conda environment {e.name}") for e in named + holding]
    found += [
        (p / "bin", p.name) for p in sorted(home.glob("StereoPipeline-*"), reverse=True)
    ]
    if downloads is not None and downloads.is_dir():
        found += [
            (p / "bin", "Varda download")
            for p in sorted(downloads.glob("StereoPipeline-*"))
        ]
    return found


def findAsp(
    chosen: str | None,
    downloads: Path | None = None,
    env: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> AspInstall | None:
    """The first working ASP: a folder the user chose, then PATH, conda
    environments named ``asp`` (or holding ASP), ``~/StereoPipeline-*``, and
    finally Varda's own download."""
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    for folder, source in _candidates(chosen, downloads, env, home):
        version = _version(folder)
        if version is not None:
            return AspInstall(folder, version, source)
    return None


# --- downloading -------------------------------------------------------------------


def downloadAsp(
    release: AspRelease,
    root: Path,
    progress: Callable[[float, str], None],
    cancelled: threading.Event,
) -> AspInstall:
    """Download the release into ``root``, check its SHA-256, unpack it and
    return the install. Raises AspError (also when canceled)."""
    root.mkdir(parents=True, exist_ok=True)
    archive = root / Path(release.url).name
    digest = hashlib.sha256()
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        opener = urllib.request.urlopen(
            release.url,
            context=context if release.url.startswith("https") else None,
            timeout=DOWNLOAD_TIMEOUT_S,
        )
        with opener as response, open(archive, "wb") as out:
            total = int(response.headers.get("Content-Length") or release.sizeMB * 1e6)
            done = 0
            while chunk := response.read(DOWNLOAD_CHUNK):
                if cancelled.is_set():
                    raise AspError("The download was canceled.")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(
                    min(done / total, 1.0) * 0.9, f"downloading ({done / 1e6:.0f} MB)"
                )
        if cancelled.is_set():
            raise AspError("The download was canceled.")
        if digest.hexdigest() != release.sha256:
            raise AspError(
                "The download doesn't match its published checksum (SHA-256); it "
                "may be damaged. Try again."
            )
        progress(0.9, "unpacking")
        with tarfile.open(archive, "r:*") as tar:
            top = {Path(m.name).parts[0] for m in tar.getmembers() if m.name}
            tar.extractall(root, filter="tar")
    except AspError:
        raise
    except (OSError, tarfile.TarError) as error:
        raise AspError(f"The download failed: {error}") from error
    finally:
        archive.unlink(missing_ok=True)
    if platform.system() == "Darwin" and shutil.which("xattr"):
        for name in top:  # files downloaded by other means can carry quarantine
            subprocess.run(
                ["xattr", "-dr", "com.apple.quarantine", str(root / name)],
                capture_output=True,
            )
    for name in sorted(top):
        version = _version(root / name / "bin")
        if version is not None:
            progress(1.0, "installed")
            return AspInstall(root / name / "bin", version, "Varda download")
    raise AspError("The download unpacked, but image_align in it doesn't run.")


def removeDownload(install: AspInstall) -> None:
    """Delete a Varda download (never an install Varda didn't make)."""
    if install.source != "Varda download":
        raise AspError("Varda only removes the copy of ASP it downloaded.")
    shutil.rmtree(install.bin.parent, ignore_errors=True)


# --- running -------------------------------------------------------------------------

# Varda's transform models as image_align's (it has no warps: those are
# screened with a homography and fitted by Varda)
ALIGNMENT = {
    "translation": "translation",
    "rigid": "rigid",
    "similarity": "similarity",
    "affine": "affine",
    "projective": "homography",
}


def readMatches(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """The matched points (N, 2) in each image, in Varda's pixel coordinates
    (ASP counts from pixel centers; Varda from corners). Reads ASP's binary
    ``.match`` files and its plain-text ones (``x1 y1 unc1 x2 y2 unc2``)."""
    if path.suffix == ".txt":
        rows = np.loadtxt(path, ndmin=2)
        if not rows.size:
            return np.zeros((0, 2)), np.zeros((0, 2))
        return rows[:, 0:2] + 0.5, rows[:, 3:5] + 0.5
    data = path.read_bytes()
    first, second = struct.unpack_from("<QQ", data, 0)
    offset, points = 16, []
    for _ in range(first + second):
        # x, y (float), ix, iy (int), orientation, scale, interest (float),
        # polarity (bool), octave, scale level (uint32), descriptor (uint64 + floats)
        x, y = struct.unpack_from("<ff", data, offset)
        offset += 29 + 8
        (length,) = struct.unpack_from("<Q", data, offset)
        offset += 8 + 4 * length
        points.append((x, y))
    array = np.array(points, float).reshape(-1, 2) + 0.5
    return array[:first], array[first:]


def imageAlign(
    install: AspInstall,
    reference: np.ndarray,
    source: np.ndarray,
    transform: str,
    workdir: Path,
) -> tuple[np.ndarray, np.ndarray]:
    """Run image_align on two single-band images (NaN where there's no data)
    and return its kept matches: points in the reference and the same
    features in the source, in pixels."""
    workdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, image in (("reference", reference), ("source", source)):
        path = workdir / f"{name}.tif"
        with warnings.catch_warnings():
            # plain pixel grids: they need no georeferencing
            warnings.simplefilter("ignore", NotGeoreferencedWarning)
            with rasterio.open(
                path,
                "w",
                driver="GTiff",
                width=image.shape[1],
                height=image.shape[0],
                count=1,
                dtype="float32",
                nodata=np.nan,
            ) as dataset:
                dataset.write(np.asarray(image, np.float32), 1)
        paths.append(path)
    prefix = workdir / "run" / "run"
    command = [
        str(install.tool),
        str(paths[0]),
        str(paths[1]),
        "-o",
        str(workdir / "aligned.tif"),
        "--output-prefix",
        str(prefix),
        "--alignment-transform",
        ALIGNMENT.get(transform, "homography"),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            creationflags=_NO_WINDOW,
            cwd=workdir,
            env=_toolEnv(install),
        )
    except OSError as error:
        raise AspError(f"image_align couldn't start: {error}") from error
    candidates = sorted(prefix.parent.glob("run-reference__source*"))
    clean = [p for p in candidates if "-clean" in p.stem] or candidates
    matches = [p for p in clean if p.suffix in (".match", ".txt")]
    if result.returncode != 0 or not matches:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-8:])
        if result.returncode == 0 and not matches:
            return np.zeros((0, 2)), np.zeros((0, 2))  # nothing matched
        raise AspError(f"image_align failed (exit code {result.returncode}):\n{tail}")
    return readMatches(matches[0])


def _toolEnv(install: AspInstall) -> dict[str, str]:
    """The environment to run ASP in: its own bin first on PATH."""
    env = dict(os.environ)
    env["PATH"] = str(install.bin) + os.pathsep + env.get("PATH", "")
    return env
