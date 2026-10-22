"""Browse products: three spectral quantities shown as red, green and blue.

Each channel is a reflectance, ``R<nm>`` (the median of the five bands
nearest that wavelength, as HyPyRameter computes its R parameters), or a
HyPyRameter spectral parameter such as ``OLINDEX3``. ``CRISM_STANDARD``
holds the browse products of the CRISM MTRDR Data Product SIS (§3.12.3),
which follow Viviano-Beck et al. (2014).

A product is written ``"MAF = OLINDEX3, LCPINDEX2, HCPINDEX2"`` (``asText``
and ``parseProduct``), so render settings and sessions carry their own
definition, custom products included.
"""

from __future__ import annotations

import re
import warnings

import numpy as np
from attrs import frozen

from varda.analysis.analysis import ProgressCallback
from varda.analysis.hypyrameter_adapter import (
    HYPYRAMETER_AVAILABLE,
    computeParameterCube,
    parameterBounds,
    parameterNames,
    validParameterNames,
)
from varda.analysis.rasters import fillAlongSpectrum
from varda.common.entities import VardaRaster

KERNEL_BANDS = 5  # an R<nm> channel is the median of this many bands
# A band this close (nm), or one band spacing away, stands in for an R<nm>
MIN_TOLERANCE_NM = 10.0
CHUNK_ROWS = 64  # rows read at a time, so a large cube never sits in memory
# Reflectances, slopes and ratios are stretched between these percentiles of
# the scene, as the PDS products are (FRT000047A3's FAL, IRA and TRU span
# its 0.1st to 99.9th percentiles)
SCENE_PERCENTILES = (0.1, 99.9)
# The default ranges of the PDS MTRDR browse products' band depths and
# indices (value = OFFSET + DN × SCALING_FACTOR for DN 0-254): the most
# common in the labels of 40 scenes (in 55-100% of them). The others have a
# higher top: the scene's 99.9th percentile (FRT000047A3's BD2290)
PDS_RANGES = {
    "BD1435": (0.0, 0.01551),
    "BD1500_2": (0.0, 0.0417),
    "BD1900R2": (0.0, 0.04974),
    "BD1900_2": (0.0, 0.04694),
    "BD2100_2": (0.0, 0.0195),
    "BD2165": (0.0, 0.00996),
    "BD2190": (0.0, 0.00996),
    "BD2210_2": (0.005, 0.00942),
    "BD2250": (0.0, 0.0092),
    "BD2290": (0.0, 0.00996),
    "BD2355": (0.005, 0.01317),
    "BD2500_2": (0.0, 0.00404),
    "BD3000": (0.0, 0.87043),
    "BD530_2": (0.0, 0.28169),
    "BD920_2": (0.0, 0.02182),
    "BDI1000VIS": (0.0, 0.03478),
    "D2200": (0.0025, 0.01993),
    "D2300": (0.0, 0.03393),
    "HCPINDEX2": (0.0, 0.02569),
    "LCPINDEX2": (0.0, 0.02022),
    "MIN2250": (0.0, 0.00996),
    "MIN2295_2480": (0.0, 0.00061),
    "MIN2345_2537": (0.0, 0.006),
    "OLINDEX3": (0.0, 0.25898),
    "SINDEX2": (0.005, 0.02938),
}

# Parameters HyPyRameter computes differently from CRISM's definition
DIFFERENT = {
    "BDI1000VIS": "HyPyRameter 0.3's BDI1000VIS integrates R/RPEAK1, not "
    "1 - R/RPEAK1, so it runs opposite to CRISM's",
}

_REFLECTANCE = re.compile(r"R(\d+(?:\.\d+)?)", re.IGNORECASE)
_STARTS_AT_ZERO = re.compile(r"^(BD|BA|D\d|MIN)|INDEX", re.IGNORECASE)


@frozen
class BrowseProduct:
    name: str
    channels: tuple[str, str, str]  # red, green, blue
    title: str = ""
    standard: bool = False  # one of CRISM_STANDARD

    def asText(self) -> str:
        return f"{self.name} = {', '.join(self.channels)}"

    @property
    def label(self) -> str:
        return f"{self.name}  {self.title}" if self.title else self.name


def _crism(name: str, title: str, red: str, green: str, blue: str) -> BrowseProduct:
    return BrowseProduct(name, (red, green, blue), title, standard=True)


# The MTRDR Data Product SIS, §3.12.3, in its order, with the channels the
# products' labels name (the SIS text has BD600_2 for FEM's green, BD530 and
# BD920 for FM2's red and green, and BD2500H for CAR's green)
CRISM_STANDARD = (
    _crism("TRU", "True Color", "R600", "R530", "R440"),
    _crism("VNA", "VNIR Albedo", "R770", "R770", "R770"),
    _crism("FEM", "Fe Mineralogy", "BD530_2", "SH600_2", "BDI1000VIS"),
    _crism("FM2", "Fe Mineralogy, Version 2", "BD530_2", "BD920_2", "BDI1000VIS"),
    _crism("FAL", "False Color", "R2529", "R1506", "R1080"),
    _crism("IRA", "IR Albedo", "R1330", "R1330", "R1330"),
    _crism("MAF", "Mafic Mineralogy", "OLINDEX3", "LCPINDEX2", "HCPINDEX2"),
    _crism("HYD", "Hydrated Mineralogy", "SINDEX2", "BD2100_2", "BD1900_2"),
    _crism("PHY", "Phyllosilicates", "D2300", "D2200", "BD1900R2"),
    _crism("PFM", "Phyllosilicate with Fe and Mg", "BD2355", "D2300", "BD2290"),
    _crism("PAL", "Phyllosilicate with Al", "BD2210_2", "BD2190", "BD2165"),
    _crism("HYS", "Hydrated Silica", "MIN2250", "BD2250", "BD1900R2"),
    _crism("ICE", "Ices", "BD1900_2", "BD1500_2", "BD1435"),
    _crism("IC2", "Ices, Version 2", "R3920", "BD1500_2", "BD1435"),
    _crism("CHL", "Chloride", "ISLOPE1", "BD3000", "IRR2"),
    _crism("CAR", "Carbonates", "D2300", "BD2500_2", "BD1900_2"),
    _crism("CR2", "Carbonates, Version 2", "MIN2295_2480", "MIN2345_2537", "CINDEX2"),
    _crism("TAN", "Tandem", "R2529", "R1330", "R770"),
)


def parseProduct(text: str) -> BrowseProduct:
    """The product ``asText`` wrote: a standard one when it matches one."""
    name, equals, rest = text.partition("=")
    name = name.strip()
    channels = tuple(c.strip() for c in rest.split(","))
    if not equals or not name or len(channels) != 3 or not all(channels):
        raise ValueError(
            f"{text!r} isn't a browse product; write it as "
            "'NAME = red, green, blue', e.g. 'MAF = OLINDEX3, LCPINDEX2, HCPINDEX2'."
        )
    red, green, blue = channels
    for standard in CRISM_STANDARD:
        if standard.name == name and standard.channels == channels:
            return standard
    return BrowseProduct(name, (red, green, blue))


def reflectanceNm(channel: str) -> float | None:
    """The wavelength of an ``R<nm>`` channel (None for a parameter)."""
    match = _REFLECTANCE.fullmatch(channel.strip())
    return float(match.group(1)) if match else None


def parameterName(channel: str) -> str | None:
    """HyPyRameter's name for the channel (its case may differ: BD1900r2),
    or None when it has none or isn't installed."""
    if not HYPYRAMETER_AVAILABLE:
        return None
    return {n.upper(): n for n in parameterNames()}.get(channel.strip().upper())


def channelProblem(channel: str, centersNm: np.ndarray | None) -> str:
    """Why an image with these band centers (nm) can't show the channel;
    "" when it can."""
    if centersNm is None or np.size(centersNm) == 0:
        return "needs wavelengths"
    nm = reflectanceNm(channel)
    if nm is not None:
        return (
            "" if _nearestBand(centersNm, nm) is not None else f"no band near {nm:g} nm"
        )
    if not HYPYRAMETER_AVAILABLE:
        return f"{channel} needs HyPyRameter, which isn't installed"
    name = parameterName(channel)
    if name is None:
        return f"Varda can't compute {channel} yet"
    if name.upper() in DIFFERENT:
        return DIFFERENT[name.upper()]
    if name not in validParameterNames(centersNm):
        low, high = parameterBounds(name)
        return f"{channel} needs {low:g}-{high:g} nm"
    return ""


def productProblem(product: BrowseProduct, centersNm: np.ndarray | None) -> str:
    """Why the image can't show the product ("" when it can)."""
    problems = [channelProblem(c, centersNm) for c in product.channels]
    return "; ".join(dict.fromkeys(p for p in problems if p))


def channelSource(channel: str, centersNm: np.ndarray) -> str:
    """What the channel reads on this image, for tooltips and provenance."""
    nm = reflectanceNm(channel)
    if nm is None:
        return channel
    used = np.sort(centersNm[_kernel(centersNm, nm)])
    return f"median of the bands at {', '.join(f'{c:.1f}' for c in used)} nm"


def computeProduct(
    image: VardaRaster,
    product: BrowseProduct,
    reportProgress: ProgressCallback | None = None,
) -> np.ndarray:
    """(rows, cols, 3) float32 of the product's channels, NaN where the image
    has no data. Parameters see the image as Band Parameters does: bad
    bands and no-data filled along each spectrum, scaled by the scale factor,
    and |I/F| > 1 treated as missing."""
    model = image.bandModel
    centers = model.centers
    problem = productProblem(product, centers)
    if problem or centers is None:
        raise ValueError(f"{image.name} can't show {product.name}: {problem}")
    # the bands each channel reads, as positions in the bands read
    kernels = {
        c: _kernel(centers, nm)
        for c in product.channels
        if (nm := reflectanceNm(c)) is not None
    }
    names = list(
        dict.fromkeys(
            n for c in product.channels if c not in kernels and (n := parameterName(c))
        )
    )
    parameterBands = _parameterBands(centers, names)
    bands = sorted(set(parameterBands).union(*kernels.values()))
    where = {band: i for i, band in enumerate(bands)}
    forParameters = [where[b] for b in parameterBands]
    bad = model.badBands[bands] if model.badBands.size == image.bandCount else None

    out = np.empty((image.height, image.width, 3), dtype=np.float32)
    for start in range(0, image.height, CHUNK_ROWS):
        rows = min(CHUNK_ROWS, image.height - start)
        raw = image.getData(bandIndices=bands, window=(start, 0, rows, image.width))
        chunk = np.ma.filled(np.ma.asarray(raw, dtype=np.float64), np.nan)
        if model.noData is not None:
            chunk[chunk == model.noData] = np.nan
        if bad is not None:
            chunk[:, :, bad] = np.nan
        if model.scaleFactor:
            chunk /= model.scaleFactor
        values: dict[str, np.ndarray] = {}
        if names:
            spectra = chunk[:, :, forParameters]
            spectra[np.abs(spectra) > 1.0] = np.nan
            spectra, _ = fillAlongSpectrum(
                spectra, badBands=None if bad is None else bad[forParameters]
            )
            cube = computeParameterCube(spectra, centers[parameterBands], names)
            values = {n: cube[:, :, i] for i, n in enumerate(names)}
        for i, channel in enumerate(product.channels):
            if channel in kernels:
                with warnings.catch_warnings():  # all-NaN pixels stay NaN
                    warnings.simplefilter("ignore", RuntimeWarning)
                    picked = chunk[:, :, [where[b] for b in kernels[channel]]]
                    out[start : start + rows, :, i] = np.nanmedian(picked, axis=2)
            else:
                out[start : start + rows, :, i] = values[str(parameterName(channel))]
        if reportProgress is not None:
            reportProgress(
                int(100 * (start + rows) / image.height),
                f"{product.name}: rows {start + rows}/{image.height}",
            )
    return out


def channelRange(channel: str, values: np.ndarray) -> tuple[float, float]:
    """The values a channel's black and full color stand for. Band depths
    and indices: the PDS products' default range, its top raised to the
    scene's top percentile when that's higher, as the PDS does (those
    without one from 0). Everything else (reflectances, slopes, ratios):
    between the scene's percentiles."""
    key = channel.strip().upper()
    finite = values[np.isfinite(values)]
    low, high = SCENE_PERCENTILES
    if finite.size == 0:
        return PDS_RANGES.get(key, (0.0, 1.0))
    if key in PDS_RANGES or (
        reflectanceNm(channel) is None and _STARTS_AT_ZERO.search(key)
    ):
        floor, top = PDS_RANGES.get(key, (0.0, 0.0))
        top = max(top, float(np.percentile(finite, high)))
        return floor, top if top > floor else floor + 1e-6
    bottom, top = (float(v) for v in np.percentile(finite, (low, high)))
    return bottom, top if top > bottom else bottom + 1e-6


def productRanges(
    product: BrowseProduct, composite: np.ndarray
) -> list[tuple[float, float]]:
    return [channelRange(c, composite[:, :, i]) for i, c in enumerate(product.channels)]


def _nearestBand(centersNm: np.ndarray, nm: float) -> int | None:
    """The band nearest ``nm``, if within the tolerance (the larger of
    ``MIN_TOLERANCE_NM`` and the image's typical band spacing)."""
    centers = np.asarray(centersNm, dtype=np.float64)
    if not np.isfinite(centers).any():
        return None
    nearest = int(np.nanargmin(np.abs(centers - nm)))
    spacing = np.diff(np.sort(centers[np.isfinite(centers)]))
    allowed = max(MIN_TOLERANCE_NM, float(np.median(spacing)) if spacing.size else 0.0)
    return nearest if abs(centers[nearest] - nm) <= allowed else None


def _kernel(centersNm: np.ndarray, nm: float) -> list[int]:
    """The KERNEL_BANDS bands centered on the one nearest ``nm`` (in
    wavelength order, shifted inward at the ends of the spectrum)."""
    order = np.argsort(centersNm)
    nearest = _nearestBand(centersNm, nm)
    assert nearest is not None
    position = int(np.flatnonzero(order == nearest)[0])
    half = KERNEL_BANDS // 2
    first = min(max(position - half, 0), max(len(order) - KERNEL_BANDS, 0))
    return [int(b) for b in order[first : first + KERNEL_BANDS]]


def _parameterBands(centersNm: np.ndarray, names: list[str]) -> list[int]:
    """The bands the parameters read, with a margin of a few bands so their
    5-band medians at each end are whole."""
    if not names:
        return []
    spacing = np.diff(np.sort(centersNm))
    margin = 3 * max(float(np.median(spacing)) if spacing.size else 0.0, 5.0)
    keep = np.zeros(len(centersNm), dtype=bool)
    for name in names:
        low, high = parameterBounds(name)
        keep |= (centersNm >= low - margin) & (centersNm <= high + margin)
    return [int(b) for b in np.flatnonzero(keep)]
