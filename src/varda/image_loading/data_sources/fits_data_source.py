"""
FitsDataSource: FITS images and cubes, read with astropy.io.fits.

Varda's GDAL has no FITS driver, so FITS goes through astropy (Astropy
Collaboration 2022). Written from the FITS Standard 4.0 (IAU FITS Working
Group 2018; Pence et al. 2010 for 3.0) and, for the small-body products, the
OSIRIS-REx Altimetry Data Product SIS (UA-SIS-9.4.4-307, §5.2.1–5.2.2) and Map
Format SIS (UA-SIS-9.4.4-324, §4.4.4, §5.2.2).

- The image is the primary HDU when it has pixels, else the first image
  extension. Each plane along NAXIS3 is a band; FITS stores NAXIS1 (x, the
  fastest axis) last in numpy, so the array (planes, NAXIS2, NAXIS1) becomes
  (rows, columns, bands), and FITS row j (0-based) is image row j, shown at
  the top, as the PDS4 labels of ALTWG products display it ("Line: Top to
  Bottom").
- Pixels are read on demand from a memory map of the file (compressed
  images are decompressed whole: they can't be mapped).
- Physical value = BZERO + BSCALE × stored value (Standard §5.3); stored
  BLANK values (integer data) and IEEE NaN are no-data, read as NaN.
- ALTWG/SPC cubes name their planes with ``PLANEn`` (or ``PLANE_nn``) cards:
  the value is the plane's name and the unit is in the comment, in square
  brackets (``PLANE19 = 'Elevation' / [m]``), or, in older products, in
  parentheses in the name (``'Latitude (deg)'``).
- No coordinate system is invented: local DTMs are grids on a reference
  plane with per-pixel body-fixed coordinates, not map projections, so
  ``crs`` is None and pixel coordinates are used. The planes holding
  latitude, longitude, radius and X/Y/Z are recorded in metadata (matched by
  full name, never by position), and so is any FITS WCS (not turned into a
  PROJ CRS).
"""

from __future__ import annotations

import logging
import math
import os
import re
import warnings
from collections.abc import Iterable
from functools import cached_property
from typing import TYPE_CHECKING

import affine as affine_module
import numpy as np
from affine import Affine
from astropy.io import fits
from pyproj import CRS

from varda.common.provenance import fileMetadata
from varda.geo.bodies import bodyNamed, isNonBodyTarget

from .data_source import (
    DataSource,
    fallbackDisplayBands,
    geoToPixelIndex,
    pixelCenterToGeo,
)
from .registry import register_data_source

if TYPE_CHECKING:
    import numpy.typing as npt

logger = logging.getLogger(__name__)

# extraMetadata keys: the primary header as text (Image Properties shows it),
# and what Varda read from the file
FITS_HEADER_KEY = "FITS header"
FITS_KEY = "fits"

# Float values at or below this are treated as fill in ALTWG/SPC cubes, as
# well as NaN (the products' documented no-data, Map Format SIS §4.4.4): no
# quantity in them comes near it, and older DTMs marked missing vertices with
# large negative numbers (e.g. -1e38)
ALTWG_FILL_LIMIT = -1e30

# Plane names (lower case, without units) from the Altimetry SIS's Table 6,
# plus the longer forms the archived products use ("Latitude of vertices").
# Geometry planes, by role:
GEOMETRY_PLANES: dict[str, frozenset[str]] = {
    "latitude": frozenset({"latitude", "latitude of vertices"}),
    "longitude": frozenset({"longitude", "longitude of vertices"}),
    "radius": frozenset({"radius", "radius of vertices"}),
    "x": frozenset({"x coordinate", "x coordinate of vertices"}),
    "y": frozenset({"y coordinate", "y coordinate of vertices"}),
    "z": frozenset({"z coordinate", "z coordinate of vertices"}),
}
UNCERTAINTY_PLANES = frozenset({"sigma"})
KNOWN_PLANES = frozenset().union(
    *GEOMETRY_PLANES.values(),
    UNCERTAINTY_PLANES,
    {
        "height above plane normal",
        "height relative to normal plane",
        "albedo",
        "relative albedo",
        "quality",
        "normal vector x",
        "normal vector y",
        "normal vector z",
        "gravity vector x",
        "gravity vector y",
        "gravity vector z",
        "gravitational magnitude",
        "gravitational potential",
        "elevation",
        "slope",
        "area",
        "shaded relief",
        "facet tilt",
        "facet tilt direction",
        "mean tilt",
        "mean tilt direction",
        "tilt variation",
        "tilt direction variation",
        "relative tilt",
        "relative tilt direction",
        "max relative height",
    },
)
# Shown by default, in this order of preference: the height of the terrain
DISPLAY_PLANES = (
    "elevation",
    "height relative to normal plane",
    "height above plane normal",
    "radius",
    "radius of vertices",
)

_PLANE_KEY = re.compile(r"^PLANE_?(\d+)$")
_UNIT_IN_NAME = re.compile(r"^(.*?)\s*[(\[]([^()\[\]]+)[)\]]\s*$")
_UNIT_IN_COMMENT = re.compile(r"\[([^\]]+)\]")
_LEADING_NUMBER = re.compile(r"^\(?\d+\)?\s+")
# Celestial (Calabretta & Greisen 2002) and planetary ("xyLN"/"xyLT") axes
_CELESTIAL = ("RA", "DEC", "GLON", "GLAT", "ELON", "ELAT", "SLON", "SLAT")
_WCS_KEYS = ("CTYPE", "CUNIT", "CRVAL", "CRPIX", "CDELT")
# FITS WAVE/AWAV units (Greisen et al. 2006) to nm
_TO_NM = {"m": 1e9, "cm": 1e7, "mm": 1e6, "um": 1e3, "nm": 1.0, "angstrom": 0.1}


class FitsError(ValueError):
    """A FITS file Varda can't open, with why in words."""


def splitPlane(value: str, comment: str) -> tuple[str, str]:
    """A ``PLANEn`` card's (name, unit): the unit in parentheses or brackets
    at the end of the value, else in brackets in the comment, else a
    one-word comment (the Map Format SIS's ``/ Degrees``); "" for none."""
    name = value.strip()
    inName = _UNIT_IN_NAME.match(name)
    if inName:
        return inName.group(1).strip(), inName.group(2).strip()
    inComment = _UNIT_IN_COMMENT.search(comment)
    if inComment:
        return name, inComment.group(1).strip()
    comment = comment.strip()
    return name, comment if comment and " " not in comment else ""


def planeCards(header: fits.Header) -> dict[int, tuple[str, str]]:
    """Plane number (1-based) -> (name, unit) from ``PLANEn``/``PLANE_nn``."""
    planes: dict[int, tuple[str, str]] = {}
    for card in header.cards:
        match = _PLANE_KEY.match(card.keyword)
        if match and isinstance(card.value, str):
            planes[int(match.group(1))] = splitPlane(card.value, card.comment)
    return planes


def targetBody(header: fits.Header) -> tuple[str | None, str | None]:
    """(TARGET or OBJECT as written, the body Varda knows it as or None).
    "101955 BENNU" is tried as "BENNU" too; an unknown body stays unknown."""
    for key in ("TARGET", "OBJECT"):
        text = str(header.get(key, "")).strip()
        if not text or isNonBodyTarget(text):
            continue
        for name in (text, _LEADING_NUMBER.sub("", text)):
            body = bodyNamed(name)
            if body is not None:
                return text, body.name
        return text, None
    return None, None


def wcsNote(header: fits.Header) -> dict | None:
    """The spatial WCS keywords of axes 1 and 2 and what kind of axes they
    are ("celestial", "planetary" or "other"); None without CTYPE1/2."""
    types = [str(header.get(f"CTYPE{i}", "")).strip() for i in (1, 2)]
    if not any(types):
        return None
    prefixes = [t.split("-")[0].upper() for t in types]
    if all(p in _CELESTIAL for p in prefixes if p):
        kind = "celestial"
    elif all(len(p) == 4 and p[2:] in ("LN", "LT") for p in prefixes if p):
        kind = "planetary"
    else:
        kind = "other"
    keys = {
        f"{key}{i}": header[f"{key}{i}"]
        for key in _WCS_KEYS
        for i in (1, 2)
        if f"{key}{i}" in header
    }
    for key in ("RADESYS", "EQUINOX", "PC1_1", "PC1_2", "PC2_1", "PC2_2"):
        if key in header:
            keys[key] = header[key]
    for key in ("CD1_1", "CD1_2", "CD2_1", "CD2_2"):
        if key in header:
            keys[key] = header[key]
    return {
        "kind": kind,
        "keywords": {
            k: v if isinstance(v, (int, float, str)) else str(v)
            for k, v in keys.items()
        },
        "note": "Recorded only: Varda doesn't turn a FITS WCS into a coordinate system yet.",
    }


def spectralAxis(header: fits.Header, count: int) -> np.ndarray | None:
    """Wavelengths (nm) of the planes from a linear WAVE/AWAV third axis
    (Greisen et al. 2006): CRVAL3 + (k + 1 - CRPIX3) × CDELT3, in CUNIT3
    (meters when absent)."""
    kind = str(header.get("CTYPE3", "")).strip().upper()
    if kind not in ("WAVE", "AWAV") or "CRVAL3" not in header:
        return None
    unit = str(header.get("CUNIT3", "m")).strip().lower()
    scale = _TO_NM.get(unit.replace("µ", "u"))
    step = header.get("CDELT3", header.get("CD3_3"))
    if scale is None or step is None:
        return None
    k = np.arange(count, dtype=np.float64)
    return (header["CRVAL3"] + (k + 1 - header.get("CRPIX3", 1.0)) * step) * scale


def _isMapped(array: np.ndarray) -> bool:
    """Whether ``array`` is a view of a memory-mapped file."""
    base = array
    while isinstance(base, np.ndarray):
        if isinstance(base, np.memmap):
            return True
        base = base.base
    return base is not None and type(base).__name__ == "mmap"


def _imageHdu(
    hdus: fits.HDUList,
) -> tuple[int, fits.PrimaryHDU | fits.ImageHDU | fits.CompImageHDU]:
    """The HDU with the image: the primary when it has pixels, else the first
    image extension with at least two axes."""
    for index, hdu in enumerate(hdus):
        if isinstance(hdu, (fits.PrimaryHDU, fits.ImageHDU, fits.CompImageHDU)):
            shape = hdu.shape or ()
            if len(shape) >= 2 and sum(n > 1 for n in shape[:-2]) <= 1:
                return index, hdu
    tables = [
        hdu.name or f"HDU {i}"
        for i, hdu in enumerate(hdus)
        if isinstance(hdu, (fits.BinTableHDU, fits.TableHDU))
    ]
    if tables:
        raise FitsError(
            "This FITS file holds tables (" + ", ".join(tables) + ") but no image. "
            "Varda can't open FITS tables yet, such as ALTWG plate-ancillary files."
        )
    raise FitsError(
        "This FITS file has no image with two or three axes (axes beyond the "
        "third must have length 1)."
    )


@register_data_source("FITS", (".fits", ".fit", ".fts", ".fz"))
class FitsDataSource(DataSource):
    """A FITS image or cube (see the module docstring for the rules)."""

    def __init__(self, filePath: str):
        self._filePath = filePath
        # astropy's warnings (a cut-short file, nonstandard cards) are logged:
        # a truncated file gets a clearer error below
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                self._hdus = fits.open(
                    filePath,
                    memmap=True,
                    do_not_scale_image_data=True,  # scaled here, a window at a time
                    lazy_load_hdus=False,
                )
            except (OSError, ValueError, TypeError) as error:
                raise FitsError(
                    f"{os.path.basename(filePath)} isn't a readable FITS file: {error}"
                ) from error
        for warning in caught:
            logger.info("%s: %s", filePath, warning.message)
        try:
            self._hduIndex, hdu = _imageHdu(self._hdus)
            self._checkComplete(hdu)
            self._header: fits.Header = hdu.header
            raw = hdu.data
            if raw is None:
                raise FitsError("Its image has no pixels.")
        except Exception:
            self._hdus.close()
            raise
        planes = math.prod(raw.shape[:-2])  # NAXIS3 (and any axes of 1 beyond)
        raw = raw.reshape(planes, *raw.shape[-2:])
        self._raw: np.ndarray = raw  # (planes, NAXIS2, NAXIS1), as stored
        self._closed = False
        header = self._header
        self._scale = float(header.get("BSCALE", 1.0))
        self._zero = float(header.get("BZERO", 0.0))
        blank = header.get("BLANK")
        self._blank: int | None = (
            int(blank) if blank is not None and raw.dtype.kind in "iu" else None
        )
        self._planes = planeCards(self._primaryOrImageHeader())
        self._isAltwg = bool(self._planes)
        self._scaled = self._scale != 1.0 or self._zero != 0.0
        if raw.dtype.kind == "f":
            self._dtype = raw.dtype.newbyteorder("=")
        elif self._scaled or self._blank is not None:
            self._dtype = np.dtype(
                np.float32 if raw.dtype.itemsize <= 2 else np.float64
            )
        else:
            self._dtype = raw.dtype.newbyteorder("=")

    def _primaryOrImageHeader(self) -> fits.Header:
        """Where PLANEn cards are: the image's header, else the primary's."""
        if any(_PLANE_KEY.match(k) for k in self._header.keys()):
            return self._header
        return self._hdus[0].header

    def _checkComplete(
        self, hdu: fits.PrimaryHDU | fits.ImageHDU | fits.CompImageHDU
    ) -> None:
        """A clear error for a file cut short (an interrupted download)."""
        info = hdu.fileinfo()
        if info is None or isinstance(hdu, fits.CompImageHDU):
            return
        bitpix = abs(int(hdu.header.get("BITPIX", 8)))
        needed = info["datLoc"] + math.prod(hdu.shape or (0,)) * bitpix // 8
        size = os.path.getsize(self._filePath)
        if size < needed:
            raise FitsError(
                f"{os.path.basename(self._filePath)} is truncated: its header "
                f"describes {needed:,} bytes but the file has {size:,}. "
                "Download it again."
            )

    # --- Data access ---

    def _toValues(self, raw: np.ndarray, masked: bool) -> np.ndarray:
        """Stored values (planes, h, w) as physical values in native byte
        order; with ``masked``, BLANK and ALTWG fill as NaN."""
        if self._dtype.kind != "f":  # plain integers: nothing to scale or mask
            return np.array(raw, dtype=self._dtype)
        values = np.array(raw, dtype=self._dtype)  # a copy, native order
        if masked and self._blank is not None:
            np.putmask(values, raw == self._blank, np.nan)
        if self._scaled:
            values *= self._scale
            values += self._zero
        if masked and self._isAltwg:
            with np.errstate(invalid="ignore"):
                np.putmask(values, values <= ALTWG_FILL_LIMIT, np.nan)
        return values

    def getBands(self, bandIndices: npt.ArrayLike) -> np.ndarray:
        return self.getData(bandIndices=bandIndices)

    def getPixelSpectrum(self, x: int, y: int) -> np.ndarray:
        if x < 0 or x >= self.width or y < 0 or y >= self.height:
            raise IndexError(
                f"Pixel ({x}, {y}) out of bounds for image of size ({self.width}, {self.height})"
            )
        return self.getData(window=(y, x, 1, 1))[0, 0, :]

    def getData(
        self,
        bandIndices: npt.ArrayLike | None = None,
        window: tuple[int, int, int, int] | None = None,
        masked=True,
    ) -> np.ndarray:
        """Pixels as (h, w, bands), read from the file's memory map; the
        window is clipped to the image."""
        rowOff, colOff, height, width = window or (0, 0, self.height, self.width)
        rows = slice(max(rowOff, 0), max(min(rowOff + height, self.height), 0))
        cols = slice(max(colOff, 0), max(min(colOff + width, self.width), 0))
        if bandIndices is None:
            raw = self._raw[:, rows, cols]
        else:
            planes = [int(i) for i in np.atleast_1d(np.asarray(bandIndices))]
            raw = self._raw[planes, rows, cols]
        return np.moveaxis(self._toValues(raw, masked), 0, -1)

    def readAllBands(self) -> np.ndarray:
        return self.getData()

    def __getitem__(self, key) -> np.ndarray:
        if not isinstance(key, tuple):
            key = (key,)
        key = key + (slice(None),) * (3 - len(key))
        rowKey, colKey, bandKey = key
        bands = np.arange(self.bandCount)[bandKey]
        view = self._raw[:, rowKey, colKey]  # a view of the map: nothing read yet
        values = self._toValues(np.asarray(view[np.atleast_1d(bands)]), True)
        if np.ndim(bands) == 0:
            return values[0]
        return np.moveaxis(values, 0, -1)

    # --- File-intrinsic properties ---

    @property
    def filePath(self) -> str:
        return self._filePath

    @property
    def width(self) -> int:
        return self._raw.shape[2]

    @property
    def height(self) -> int:
        return self._raw.shape[1]

    @property
    def bandCount(self) -> int:
        return self._raw.shape[0]

    @property
    def dtype(self) -> np.dtype:
        return self._dtype

    @property
    def nodata(self) -> float | None:
        """The physical value of BLANK (reads without masking give it)."""
        if self._blank is None:
            return None
        return self._blank * self._scale + self._zero

    @property
    def isMemoryMapped(self) -> bool:
        """Whether pixels are read on demand from a memory map of the file."""
        return _isMapped(self._raw)

    @cached_property
    def _spectral(self) -> np.ndarray | None:
        return None if self._planes else spectralAxis(self._header, self.bandCount)

    @cached_property
    def planeUnits(self) -> list[str]:
        """Each plane's unit as the file states it ("" for none)."""
        if self._planes:
            return [self._planes.get(i + 1, ("", ""))[1] for i in range(self.bandCount)]
        unit = str(self._header.get("BUNIT", "")).strip()
        return [unit] * self.bandCount

    @cached_property
    def planeNames(self) -> list[str]:
        """Each plane's name without its unit."""
        if self._planes:
            return [
                self._planes.get(i + 1, (f"Plane {i + 1}", ""))[0] or f"Plane {i + 1}"
                for i in range(self.bandCount)
            ]
        if self.bandCount == 1:
            name = str(self._header.get("EXTNAME", "")).strip()
            return [name or "Plane 1"]
        return [f"Plane {i + 1}" for i in range(self.bandCount)]

    @cached_property
    def wavelengths(self) -> np.ndarray:
        if self._spectral is not None:
            return self._spectral
        if self._planes:
            return np.array(self.bandNames)
        return np.arange(self.bandCount)

    @cached_property
    def wavelengthsType(self) -> type:
        kind = self.wavelengths.dtype.kind
        return float if kind == "f" else int if kind in "iu" else str

    @cached_property
    def wavelengthUnits(self) -> str:
        return "nm" if self._spectral is not None else "Unknown"

    @cached_property
    def bandNames(self) -> list[str]:
        return [
            f"{name} ({unit})" if unit else name
            for name, unit in zip(self.planeNames, self.planeUnits)
        ]

    @property
    def transform(self) -> Affine:
        return affine_module.identity

    @property
    def crs(self) -> CRS | None:
        return None  # never invented: see the module docstring

    @property
    def driver(self) -> str:
        return "FITS"

    @cached_property
    def defaultBands(self) -> np.ndarray:
        if self._planes:
            names = [name.lower() for name in self.planeNames]
            chosen = next((names.index(n) for n in DISPLAY_PLANES if n in names), 0)
            return np.array([chosen] * 3, dtype=np.uint)
        return fallbackDisplayBands(
            self.wavelengths,
            self.wavelengthUnits,
            self.nodata,
            self.extraMetadata,
            self.bandCount,
        )

    @cached_property
    def isParameterImage(self) -> bool:
        return bool(self._planes)

    @cached_property
    def extraMetadata(self) -> dict:
        return {
            **fileMetadata({}, (self.filePath,)),
            FITS_HEADER_KEY: self._hdus[0].header.tostring(sep="\n", padding=False),
            FITS_KEY: self._fitsRecord(),
        }

    def _fitsRecord(self) -> dict:
        """What Varda read and how: the HDU, planes and units, no-data rule,
        target body, geometry and uncertainty planes and WCS."""
        names = [name.lower() for name in self.planeNames]
        target, body = targetBody(
            self._header if "TARGET" in self._header else self._hdus[0].header
        )
        record: dict = {
            "hdu": self._hduIndex,
            "hduName": self._hdus[self._hduIndex].name,
            "otherImageHdus": [
                i
                for i, hdu in enumerate(self._hdus)
                if i != self._hduIndex
                and isinstance(hdu, (fits.ImageHDU, fits.CompImageHDU))
                and hdu.shape
            ],
            "planeNames": self.planeNames,
            "planeUnits": self.planeUnits,
            "scaling": {"BSCALE": self._scale, "BZERO": self._zero},
            "noData": self._noDataRule(),
            "rowOrder": "FITS row 1 (the first stored) is the top image row",
            "target": target,
            "body": body,
            "georeferencing": (
                "None: pixel coordinates. Per-pixel body-fixed geometry is in "
                "the planes listed under geometryPlanes."
                if self._isAltwg
                else "None: pixel coordinates."
            ),
        }
        if self._isAltwg:
            record["product"] = "ALTWG/SPC image cube (PLANEn cards)"
            record["geometryPlanes"] = {
                role: names.index(found)
                for role, accepted in GEOMETRY_PLANES.items()
                for found in [next((n for n in names if n in accepted), None)]
                if found is not None
            }
            record["uncertaintyPlanes"] = [
                i for i, name in enumerate(names) if name in UNCERTAINTY_PLANES
            ]
            unknown = [
                self.planeNames[i] for i, n in enumerate(names) if n not in KNOWN_PLANES
            ]
            if unknown:
                logger.warning(
                    "%s: planes not in the ALTWG SIS, loaded as named bands: %s",
                    self.filePath,
                    ", ".join(unknown),
                )
                record["unrecognizedPlanes"] = unknown
            for key in ("SIGMA", "SIG_DEF", "DENSITY", "ROT_RATE", "REF_POT", "GSD"):
                if key in self._header:
                    value = self._header[key]
                    record.setdefault("productKeywords", {})[key] = {
                        "value": value
                        if isinstance(value, (int, float, str))
                        else str(value),
                        "comment": self._header.comments[key],
                    }
        wcs = wcsNote(self._header)
        if wcs is not None:
            record["wcs"] = wcs
        if self._spectral is not None:
            record["spectralAxis"] = (
                "Wavelengths from the CTYPE3/CRVAL3/CDELT3 WCS, in nm"
            )
        return record

    def _noDataRule(self) -> str:
        rules = ["NaN"]
        if self._blank is not None:
            rules.append(f"stored value BLANK = {self._blank}")
        if self._isAltwg:
            rules.append(f"values <= {ALTWG_FILL_LIMIT:g} (ALTWG fill)")
        return "No-data: " + ", ".join(rules)

    @cached_property
    def description(self) -> str:
        header = self._hdus[0].header
        parts: Iterable[str] = (
            str(header.get(key, "")).strip()
            for key in ("MAP_NAME", "PRODNAME", "OBJECT")
        )
        return " · ".join(p for p in parts if p)

    # --- Coordinate transformation ---

    def pixelToGeo(self, col: int, row: int) -> tuple[float, float]:
        return pixelCenterToGeo(self.transform, col, row)

    def geoToPixel(self, x: float, y: float) -> tuple[int, int]:
        return geoToPixelIndex(self.transform, x, y)

    # --- Lifecycle ---

    def close(self) -> None:
        """Release the file (the memory map closes once no array uses it)."""
        if getattr(self, "_closed", True):  # also when __init__ failed
            return
        self._closed = True
        self._hdus.close()

    def __del__(self):
        self.close()

    def __repr__(self) -> str:
        return f"FitsDataSource({self._filePath!r}, {self.width}x{self.height}x{self.bandCount})"
