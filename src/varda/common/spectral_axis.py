"""The spectral axis of a raster.

Unit strings are parsed here, once, and every calculation works in
nanometres. ``BandModel`` carries what a spectral analysis needs about the
bands: centers in nm (or None when the bands aren't a spectral axis, e.g.
band indices, labels or parameter images), FWHM in nm, the bad-band mask
(ENVI ``bbl``), the reflectance scale factor and the no-data value.

When a file declares no unit, ``bandModelFor`` infers one from the values and
flags it (``unitWasInferred``, ``unitNote``: logged once per image by
``VardaRaster``, shown in Image Properties and the image list's tooltip);
nothing else in Varda guesses units.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from enum import Enum

import attrs
import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)


class SpectralUnit(Enum):
    NANOMETRE = "nm"
    MICROMETRE = "µm"
    WAVENUMBER = "cm⁻¹"


_ALIASES = {
    SpectralUnit.NANOMETRE: {
        "nm",
        "nanometer",
        "nanometers",
        "nanometre",
        "nanometres",
    },
    SpectralUnit.MICROMETRE: {
        "um",
        "µm",
        "μm",
        "micron",
        "microns",
        "micrometer",
        "micrometers",
        "micrometre",
        "micrometres",
    },
    SpectralUnit.WAVENUMBER: {
        "wavenumber",
        "wavenumbers",
        "cm-1",
        "cm^-1",
        "1/cm",
        "cm⁻¹",
    },
}
_LOOKUP = {alias: unit for unit, aliases in _ALIASES.items() for alias in aliases}

# Keys under which readers expose ENVI band metadata (GDAL's ENVI domain uses
# underscores); derived rasters carry the same keys in extraMetadata.
FWHM_KEY = "fwhm"
BBL_KEY = "bbl"
SCALE_KEY = "reflectance_scale_factor"
BAND_METADATA_KEYS = (FWHM_KEY, BBL_KEY, SCALE_KEY)

MAX_MICROMETRES = 30.0  # undeclared values below this are taken as µm (to thermal IR)


def parseUnit(text: str | None) -> SpectralUnit | None:
    """The unit named by ``text``, or None (unknown, parameters, indices...)."""
    if not text:
        return None
    return _LOOKUP.get(text.strip().lower())


def unitInText(text: str) -> SpectralUnit | None:
    """A unit named anywhere in ``text``, e.g. ``wavelength_um`` or
    ``Wavelength (nm)`` or ``# units: micrometers``."""
    for token in re.split(r"[^\w µμ⁻^/-]+|[_\s]+", text.lower()):
        unit = parseUnit(token.strip("()[]"))
        if unit is not None:
            return unit
    return None


def nanometresFrom(values: npt.ArrayLike, unit: SpectralUnit | None) -> np.ndarray:
    """Wavelengths in nm; an undeclared unit is inferred from the values (logged)."""
    array = np.asarray(values, dtype=np.float64)
    if unit is None:
        unit = _inferUnit(array)
    return toNanometres(array, unit)


def toNanometres(values: npt.ArrayLike, unit: SpectralUnit) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if unit is SpectralUnit.MICROMETRE:
        return array * 1000.0
    if unit is SpectralUnit.WAVENUMBER:
        with np.errstate(divide="ignore"):
            return 1e7 / array
    return array.copy()


def fromNanometres(values: npt.ArrayLike, unit: SpectralUnit) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if unit is SpectralUnit.MICROMETRE:
        return array / 1000.0
    if unit is SpectralUnit.WAVENUMBER:
        with np.errstate(divide="ignore"):
            return 1e7 / array
    return array.copy()


def unitLabel(unit: SpectralUnit | None, declared: str = "") -> str:
    """Short label for axes and band names: nm, µm or cm⁻¹ (or the declared text)."""
    return unit.value if unit is not None else declared


MAX_WAVELENGTH_DECIMALS = 4


def wavelengthDecimals(values: npt.ArrayLike) -> int:
    """Decimals that show band centers (in any unit) a step finer than the
    spacing of neighboring bands, so every band reads differently without
    float noise ("2353.8461538461543"): 0–4; 1 for a single band."""
    centers = np.unique(np.asarray(values, dtype=np.float64))
    centers = centers[np.isfinite(centers)]
    spacing = np.diff(centers)
    spacing = spacing[spacing > 0]
    if spacing.size == 0:
        return 1
    decimals = int(np.ceil(-np.log10(spacing.min()))) + 1
    return int(np.clip(decimals, 0, MAX_WAVELENGTH_DECIMALS))


def formatWavelength(value: float, unit: str, decimals: int) -> str:
    """A wavelength for people: rounded, with its unit ("2353.8 nm")."""
    return f"{value:.{decimals}f} {unit}".strip()


def parseEnviList(value) -> list[float] | None:
    """An ENVI ``{ a , b , c }`` list (or a sequence already) as floats."""
    if value is None:
        return None
    if isinstance(value, str):
        items = [item for item in re.split(r"[\s,{}]+", value) if item]
        return [float(item) for item in items]
    return [float(item) for item in value]


def _unitFromValues(values: np.ndarray) -> SpectralUnit:
    return (
        SpectralUnit.MICROMETRE
        if np.nanmax(values) < MAX_MICROMETRES
        else SpectralUnit.NANOMETRE
    )


def assumedUnitNote(unit: SpectralUnit, values: npt.ArrayLike) -> str:
    """What Varda assumed about wavelengths whose unit wasn't declared."""
    array = np.asarray(values, dtype=np.float64)
    return (
        f"Wavelength units not declared; assuming {unit.value} from the values "
        f"({np.nanmin(array):.4g}–{np.nanmax(array):.4g})"
    )


def _inferUnit(values: np.ndarray) -> SpectralUnit:
    unit = _unitFromValues(values)
    logger.warning(assumedUnitNote(unit, values))
    return unit


def _isNumeric(wavelengths: np.ndarray) -> bool:
    return wavelengths.dtype.kind in "fiu" and wavelengths.size > 0


def _isBandIndexList(wavelengths: np.ndarray) -> bool:
    return bool(np.array_equal(wavelengths, np.arange(wavelengths.size)))


@attrs.frozen
class BandModel:
    bandCount: int
    centers: np.ndarray | None = None  # nm; None: not a spectral axis
    unit: SpectralUnit | None = None  # the unit of the source's own values
    declaredUnits: str = ""
    unitWasInferred: bool = False
    fwhm: np.ndarray | None = None  # nm
    badBands: np.ndarray = attrs.field(factory=lambda: np.zeros(0, dtype=bool))
    scaleFactor: float | None = None
    noData: float | None = None

    @classmethod
    def empty(cls, bandCount: int) -> BandModel:
        return cls(bandCount, badBands=np.zeros(bandCount, dtype=bool))

    @property
    def isSpectral(self) -> bool:
        return self.centers is not None

    @property
    def unitNote(self) -> str:
        """What was assumed about the unit when the source declared none
        ("" when it declared one); ``VardaRaster`` logs it once."""
        if not self.unitWasInferred or self.centers is None or self.unit is None:
            return ""
        return assumedUnitNote(self.unit, self.native(self.centers))

    def native(self, nanometres: npt.ArrayLike) -> np.ndarray:
        """Nanometres in the source's own unit (for labels and settings)."""
        if self.unit is None:
            return np.asarray(nanometres, dtype=np.float64)
        return fromNanometres(nanometres, self.unit)

    @property
    def label(self) -> str:
        return unitLabel(self.unit, self.declaredUnits)


def bandModelFor(
    wavelengths: npt.ArrayLike,
    wavelengthUnits: str | None,
    noData: float | None,
    extraMetadata: Mapping,
    bandCount: int,
) -> BandModel:
    """The band model of a raster from what every data source exposes."""
    values = np.asarray(wavelengths)
    declared = wavelengthUnits or ""
    unit = parseUnit(declared)
    inferred = False
    centers = None
    if _isNumeric(values) and values.size == bandCount:
        if unit is None and declared.strip().lower() not in ("parameter", "parameters"):
            if not _isBandIndexList(values):
                unit, inferred = _unitFromValues(values.astype(np.float64)), True
        if unit is not None:
            centers = toNanometres(values, unit)

    def bandList(key: str) -> np.ndarray | None:
        try:
            parsed = parseEnviList(extraMetadata.get(key))
        except ValueError:
            parsed = None
        if parsed is None:
            return None
        if len(parsed) != bandCount:
            logger.warning(
                "Ignoring %s: %d values for %d bands", key, len(parsed), bandCount
            )
            return None
        return np.asarray(parsed, dtype=np.float64)

    fwhm = bandList(FWHM_KEY)
    if fwhm is not None and unit is SpectralUnit.WAVENUMBER and centers is not None:
        # a width in cm⁻¹ around a center, in nm
        native = np.asarray(values, dtype=np.float64)
        fwhm = np.abs(
            toNanometres(native - fwhm / 2, unit)
            - toNanometres(native + fwhm / 2, unit)
        )
    elif fwhm is not None and unit is not None:
        fwhm = toNanometres(fwhm, unit)
    bbl = bandList(BBL_KEY)
    scale = extraMetadata.get(SCALE_KEY)
    try:
        scaleFactor = float(scale) if scale not in (None, "") else None
    except (TypeError, ValueError):
        scaleFactor = None
    return BandModel(
        bandCount=bandCount,
        centers=centers,
        unit=unit,
        declaredUnits=declared,
        unitWasInferred=inferred,
        fwhm=fwhm if centers is not None else None,
        badBands=(bbl == 0) if bbl is not None else np.zeros(bandCount, dtype=bool),
        scaleFactor=scaleFactor,
        noData=noData,
    )


TRUE_COLOUR_NM = (640.0, 550.0, 470.0)  # red, green, blue
TRUE_COLOUR_TOLERANCE_NM = 50.0
FALSE_COLOUR_FRACTIONS = (0.85, 0.5, 0.15)  # of the band range: long -> red


def bandWavelengthLabels(model: BandModel) -> list[str] | None:
    """Each band's center in the image's own unit, rounded to tell bands
    apart ("2353.8 nm"); None without a spectral axis."""
    if model.centers is None:
        return None
    native = model.native(model.centers)
    decimals = wavelengthDecimals(native)
    return [formatWavelength(value, model.label, decimals) for value in native]


def displayBands(model: BandModel) -> np.ndarray | None:
    """Default (red, green, blue) band indices for a spectral image: true
    color when it covers the visible, otherwise a false color spread across
    its range with long wavelengths as red. Bad bands are never chosen. None
    when the bands aren't a spectral axis (the caller keeps its own default)."""
    if model.centers is None:
        return None
    good = np.flatnonzero(~model.badBands & np.isfinite(model.centers))
    if good.size < 3:
        return None
    centers = model.centers[good]

    def nearest(target: float) -> int:
        return int(good[np.argmin(np.abs(centers - target))])

    trueColour = [nearest(t) for t in TRUE_COLOUR_NM]
    close = all(
        np.min(np.abs(centers - t)) <= TRUE_COLOUR_TOLERANCE_NM for t in TRUE_COLOUR_NM
    )
    if close and len(set(trueColour)) == 3:  # coarse sampling can't give 3 distinct
        return np.array(trueColour, dtype=np.uint)
    lo, hi = float(np.min(centers)), float(np.max(centers))
    return np.array(
        [nearest(lo + f * (hi - lo)) for f in FALSE_COLOUR_FRACTIONS], dtype=np.uint
    )
