"""
ENVIDataSource: extends RasterioDataSource with ENVI-specific metadata parsing.

Reads ENVI namespace tags to extract wavelengths, band names, wavelength units,
default bands, and parameter image detection. Consolidates the metadata parsing
logic that was previously in envi_image_loader.py.
"""

from __future__ import annotations

import logging
from functools import cached_property
from pathlib import Path

import numpy as np

from .data_source import fallbackDisplayBands
from .rasterio_data_source import RasterioDataSource
from .registry import register_data_source

logger = logging.getLogger(__name__)


# Data-file extensions to try beside a header, as Spectral Python does
# (spectral/io/envi.py KNOWN_EXTS), plus the interleave names
DATA_EXTENSIONS = (
    "",
    ".img",
    ".dat",
    ".sli",
    ".hyspex",
    ".raw",
    ".bin",
    ".bsq",
    ".bil",
    ".bip",
)
HEADER_SUFFIX = ".hdr"
# header keys Varda writes for a companion uncertainty (sigma) image, as GDAL
# reports them (lower case, spaces as underscores)
SIGMA_FILE_KEY = "varda_sigma_file"
SIGMA_KIND_KEY = "varda_sigma_kind"
SIGMA_STEPS_KEY = "varda_sigma_steps"
SIGMA_KEYS = (SIGMA_FILE_KEY, SIGMA_KIND_KEY, SIGMA_STEPS_KEY)


def _isHeader(path: Path) -> bool:
    return path.suffix.lower() == HEADER_SUFFIX


def _siblings(folder: Path) -> dict[str, Path]:
    """Files in ``folder`` by lower-cased name (names match case-insensitively,
    and the result is the name as it is on disk)."""
    try:
        return {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return {}


def findEnviHeader(dataFile: str | Path) -> Path | None:
    """The header beside an ENVI data file (``foo.hdr`` or ``foo.img.hdr``,
    any case), or None."""
    path = Path(dataFile)
    files = _siblings(path.parent)
    names = (
        [path.stem + HEADER_SUFFIX, path.name + HEADER_SUFFIX]
        if path.suffix
        else [path.name + HEADER_SUFFIX]
    )
    return next((files[n.lower()] for n in names if n.lower() in files), None)


def findEnviDataFile(header: str | Path) -> Path:
    """The data file an ENVI header describes: ``foo.img.hdr`` -> ``foo.img``,
    else ``foo`` plus each known extension (any case). Only the file name is
    looked at, never a folder name."""
    path = Path(header)
    files = _siblings(path.parent)
    stem = path.name[: -len(path.suffix)] if path.suffix else path.name
    if Path(stem).suffix and stem.lower() in files:  # foo.img.hdr
        return files[stem.lower()]
    for extension in DATA_EXTENSIONS:
        name = (stem + extension).lower()
        if name in files and name != path.name.lower():
            return files[name]
    raise FileNotFoundError(f"No ENVI data file found for {path.name}")


@register_data_source("ENVI Image", (HEADER_SUFFIX, *[e for e in DATA_EXTENSIONS if e]))
class ENVIDataSource(RasterioDataSource):
    """DataSource for ENVI format images.

    Opens the data file via rasterio and reads ENVI-specific metadata from the
    ``ENVI`` namespace tags. Accepts the header or the data file.
    """

    @classmethod
    def canOpen(cls, filePath: str) -> bool:
        """An ENVI file is a header, or a data file with a header beside it."""
        path = Path(filePath)
        return _isHeader(path) or findEnviHeader(path) is not None

    def __init__(self, filePath: str):
        path = Path(filePath)
        dataFile = findEnviDataFile(path) if _isHeader(path) else path
        super().__init__(str(dataFile))
        # Keep the original path the user provided (could be .hdr)
        self._userFilePath = filePath
        # ENVI header keys are case-insensitive; GDAL keeps them as written
        self._enviTags: dict[str, str] = {
            key.lower(): value for key, value in self._src.tags(ns="ENVI").items()
        }

    @property
    def filePath(self) -> str:
        return self._userFilePath

    @cached_property
    def wavelengths(self) -> np.ndarray:
        wavelengthUnitsLower = self._enviTags.get("wavelength_units", "").lower()

        if "wavelength" in self._enviTags:
            wlStrings = [
                w.strip() for w in self._enviTags["wavelength"].strip("{}").split(",")
            ]

            # Check if units indicate parameter names
            if wavelengthUnitsLower in ("parameters", "parameter"):
                logger.debug("Using spectral parameter names as wavelengths")
                return np.asarray(wlStrings, dtype="U50")

            # Try numeric parse
            try:
                return np.asarray([float(w) for w in wlStrings])
            except ValueError:
                # Fall back to string labels
                logger.debug("Falling back to parameter names for wavelengths")
                return np.asarray(wlStrings, dtype="U50")

        # Try to extract from band names
        bn = self._rawBandNames
        if bn is not None:
            try:
                return np.asarray([float(name) for name in bn])
            except ValueError:
                return np.asarray(bn, dtype="U50")

        # Last resort: band indices
        logger.debug("No wavelength information found, using band indices")
        return np.arange(self.bandCount)

    @cached_property
    def wavelengthsType(self) -> type:
        wl = self.wavelengths
        if wl.dtype.kind == "f":
            return float
        elif wl.dtype.kind == "i":
            return int
        return str

    @cached_property
    def wavelengthUnits(self) -> str:
        return self._enviTags.get("wavelength_units", "Unknown")

    @cached_property
    def _rawBandNames(self) -> list[str] | None:
        """Raw band names from ENVI tags, or None if not present."""
        if "band_names" in self._enviTags:
            return [
                name.strip()
                for name in self._enviTags["band_names"].strip("{}").split(",")
            ]
        return None

    @cached_property
    def bandNames(self) -> list[str]:
        raw = self._rawBandNames
        if raw is not None:
            return raw
        # Fall back to wavelength strings or generic names
        if "wavelength" in self._enviTags:
            return [
                w.strip() for w in self._enviTags["wavelength"].strip("{}").split(",")
            ]
        return [f"Band {i + 1}" for i in range(self.bandCount)]

    @cached_property
    def isParameterImage(self) -> bool:
        """True if this is a parameter image (band names represent parameters, not wavelengths)."""
        wlUnits = self._enviTags.get("wavelength_units", "").lower()
        if wlUnits in ("parameters", "parameter"):
            return True
        # Also consider it parameterized if band_names present but no numeric wavelengths
        if self._rawBandNames is not None and self.wavelengthsType is str:
            return True
        return False

    @cached_property
    def defaultBands(self) -> np.ndarray:
        """Default RGB band indices for display (zero-based)."""
        defaultBandsStr = self._enviTags.get("default_bands")
        if defaultBandsStr:
            bandTokens = [
                band.strip() for band in defaultBandsStr.strip("{}").split(",")
            ]
            try:
                # Try as numeric indices (ENVI uses 1-based, convert to 0-based)
                indices = [int(band) - 1 for band in bandTokens]
            except ValueError:
                # Try to match by band name
                raw = self._rawBandNames
                if raw:
                    indices = []
                    for name in bandTokens:
                        try:
                            indices.append(raw.index(name))
                        except ValueError:
                            logger.warning(
                                f"Default band '{name}' not found in band names"
                            )
                else:
                    indices = [0, 1, 2] if self.bandCount >= 3 else [0, 0, 0]

            # Ensure at least 3 indices
            while len(indices) < 3:
                indices.append(0)

            # Clamp to valid range
            maxBand = self.bandCount - 1
            indices = [min(max(idx, 0), maxBand) for idx in indices[:3]]
            return np.array(indices, dtype=np.uint)

        # None named in the header: true/false color from the wavelengths
        return fallbackDisplayBands(
            self.wavelengths,
            self.wavelengthUnits,
            self.nodata,
            self.extraMetadata,
            self.bandCount,
        )

    @cached_property
    def extraMetadata(self) -> dict:
        """Extra ENVI metadata not captured by standard properties, and what
        Varda wrote beside the file (its provenance and scientific metadata).
        A Varda sigma companion (``varda sigma file`` and its kind and steps)
        becomes ``extraMetadata["sigma"]`` with an absolute path."""
        skip = {
            *SIGMA_KEYS,
            "description",
            "default_bands",
            "wavelength_units",
            "band_names",
            "wavelength",
            "geospatial_info",
        }
        extra: dict = {
            **{k: v for k, v in self._enviTags.items() if k not in skip},
            **self._vardaMetadata(),
        }
        sigma = self._sigmaCompanion()
        if sigma is not None:
            extra["sigma"] = sigma
        return extra

    def _sigmaCompanion(self) -> dict[str, str | list[str]] | None:
        """The uncertainty image written beside this one, or None."""
        name = self._enviTags.get(SIGMA_FILE_KEY, "").strip().strip("{}").strip()
        if not name:
            return None
        header = Path(self._userFilePath).absolute()
        steps = self._enviTags.get(SIGMA_STEPS_KEY, "").strip().strip("{}")
        return {
            "path": str(header.parent / name),
            "kind": self._enviTags.get(SIGMA_KIND_KEY, "").strip().strip("{}").strip(),
            "steps": [step.strip() for step in steps.split(",") if step.strip()],
        }

    @cached_property
    def description(self) -> str:
        return self._enviTags.get("description", "").strip("{}")

    def __repr__(self) -> str:
        return f"ENVIDataSource({self._userFilePath!r}, {self.width}x{self.height}x{self.bandCount})"
