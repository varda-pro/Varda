"""Map projection onto the MTRDR grid (spec S8, ledger G1-G3).

The grid follows ``mtr_map.cat``: a rolling equirectangular projection on a
sphere whose radius is the IAU 2000 ellipsoid radius at the reference latitude
φ_P (the equatorward multiple of 5° of the scene's median latitude), central
meridian 0, x = R cos φ_P λ and y = R φ; poleward of ±65°, a polar
stereographic projection on the pole, on the sphere of the polar radius
(Table 1.0; the catalog's "spherical fallback", true scale at the pole).
Latitudes are planetocentric and longitudes positive east.

The tie point is the UL corner of the bounding box of the DDR pixel centers,
(x_min, y_max), rounded to 0.1 m. The MTRDR labels state it differently, and
equivalently: their WESTERNMOST_LONGITUDE and MAXIMUM_LATITUDE are the first
column and row centers, so tie = x(W) − s/2, y(N) + s/2. FRT00005850's
sensor-space DDR reproduces its MTRDR's tie point to 0.03 m and its size
exactly (M1, ``test_projection.py::test_grid_from_sensor_ddr_matches_mtrdr``).

Longitudes are unwrapped around the scene: a scene crossing ±180° or
0°/360° stays contiguous, centered on its median longitude taken into
(−180°, 180°]. A scene on the antimeridian therefore sits near
x ≈ ±π R cos φ_P, as MTRDR would place it, and its far side continues past
that value instead of jumping sign.

Resampling is nearest neighbor (the GLT, with source-line and source-sample
backplanes) or area weighted with σ. The math here is plain NumPy; pyproj is
used only by the tests, to check the WKT against these coordinates.
"""

import math
from typing import Literal

import numpy as np
from affine import Affine
from attrs import define
from scipy.sparse import csr_matrix
from scipy.spatial import KDTree

IAU2000_A = 3396190.0
IAU2000_B = 3376200.0

MapKind = Literal["equirectangular", "polar"]

_POLAR_LIMIT_DEG = 65.0
_TIE_DECIMALS = 1  # MTRDR tie points are whole decimeters (M1)
_BAND_CHUNK = 32


def referenceLatitude(centerLatDeg: float) -> float:
    """The equatorward multiple of 5° (−18.3 gives −15); ±90 at and beyond ±65°."""
    if abs(centerLatDeg) >= _POLAR_LIMIT_DEG:
        return math.copysign(90.0, centerLatDeg)
    return math.copysign(5.0 * math.floor(abs(centerLatDeg) / 5.0), centerLatDeg) + 0.0


def sphereRadius(latDeg: float) -> float:
    """The IAU 2000 ellipsoid's radius at planetocentric latitude ``latDeg``:
    ab / √((b cos φ)² + (a sin φ)²)."""
    phi = math.radians(latDeg)
    return (
        IAU2000_A
        * IAU2000_B
        / math.sqrt((IAU2000_B * math.cos(phi)) ** 2 + (IAU2000_A * math.sin(phi)) ** 2)
    )


@define(frozen=True)
class MapGrid:
    """An MTRDR-style map grid; (originX, originY) is the UL corner of pixel (0, 0)."""

    kind: MapKind
    referenceLatDeg: float
    radius: float
    pixelSize: float
    originX: float
    originY: float
    width: int
    height: int

    def transform(self) -> Affine:
        """Pixel (column, row) edges to map (x, y) meters."""
        s = self.pixelSize
        return Affine(s, 0.0, self.originX, 0.0, -s, self.originY)

    def centerLongitude(self) -> float:
        """The longitude of the grid's center column (equirectangular); the
        longitudes of ``mapCoordinates`` are unwrapped around it."""
        if self.kind == "polar":
            return 0.0
        x = self.originX + self.width * self.pixelSize / 2
        return math.degrees(x / self._parallelRadius())

    def _parallelRadius(self) -> float:
        return self.radius * math.cos(math.radians(self.referenceLatDeg))

    def _projectionName(self) -> str:
        kind = (
            "Equirectangular"
            if self.kind == "equirectangular"
            else "Polar Stereographic"
        )
        hemisphere = "S" if self.referenceLatDeg < 0 else "N"
        return f"MRO Mars {kind} [IAU 2000] [{abs(self.referenceLatDeg):.2f}{hemisphere}; 0.00E]"

    def _sphereName(self) -> str:
        return f"Mars_2000_IAU_IAG_custom_sphere_lat{self.referenceLatDeg:.0f}"

    def crsWkt(self) -> str:
        """The ESRI WKT as MTRDR headers write it (``coordinate system string``)."""
        sphere = self._sphereName()
        if self.kind == "equirectangular":
            projection = "Equidistant_Cylindrical"
        else:
            projection = (
                "Stereographic_North_Pole"
                if self.referenceLatDeg > 0
                else "Stereographic_South_Pole"
            )
        return (
            f'PROJCS["{self._projectionName()}",'
            f'GEOGCS["GCS_{sphere}",DATUM["D_{sphere}",'
            f'SPHEROID["{sphere}",{self.radius!r},0.0]],'
            'PRIMEM["Reference_Meridian",0.0],UNIT["Degree",0.0174532925199433]],'
            f'PROJECTION["{projection}"],'
            'PARAMETER["False_Easting",0.0],PARAMETER["False_Northing",0.0],'
            'PARAMETER["Central_Meridian",0.0],'
            f'PARAMETER["Standard_Parallel_1",{float(self.referenceLatDeg)!r}],'
            'UNIT["Meter",1.0]]'
        )

    def enviMapInfo(self) -> str:
        """The ENVI ``map info`` value as MTRDR writes it, without the braces."""
        s = self.pixelSize
        return (
            f"{self._projectionName()}, 1.0000, 1.0000, "
            f"{self.originX:.4f}, {self.originY:.4f}, {s:.10e}, {s:.10e}, "
            f"D_{self._sphereName()}, units=Meters"
        )


def _unwrap(lonDeg: np.ndarray, centerDeg: float) -> np.ndarray:
    """Longitudes taken into (center − 180°, center + 180°]."""
    offset = np.mod(lonDeg - centerDeg, 360.0)
    return centerDeg + np.where(offset > 180.0, offset - 360.0, offset)


def _sceneLongitude(lonDeg: np.ndarray) -> float:
    """The scene's median longitude, unwrapped around its circular mean and
    taken into (−180°, 180°]."""
    radians = np.radians(lonDeg)
    mean = math.degrees(math.atan2(np.mean(np.sin(radians)), np.mean(np.cos(radians))))
    median = float(np.median(_unwrap(lonDeg, mean)))
    return float(_unwrap(np.array(median), 0.0))


def _project(
    kind: MapKind,
    referenceLatDeg: float,
    radius: float,
    latDeg: np.ndarray,
    lonDeg: np.ndarray,
    centerLonDeg: float,
) -> tuple[np.ndarray, np.ndarray]:
    lat = np.radians(np.asarray(latDeg, dtype=np.float64))
    lon = np.asarray(lonDeg, dtype=np.float64)
    if kind == "equirectangular":
        k = math.cos(math.radians(referenceLatDeg))
        return radius * k * np.radians(_unwrap(lon, centerLonDeg)), radius * lat
    lon = np.radians(lon)
    if referenceLatDeg > 0:  # longitude 0 points down, 90° E right
        rho = 2 * radius * np.tan(np.pi / 4 - lat / 2)
        return rho * np.sin(lon), -rho * np.cos(lon)
    rho = 2 * radius * np.tan(np.pi / 4 + lat / 2)  # longitude 0 points up
    return rho * np.sin(lon), rho * np.cos(lon)


def mapCoordinates(
    grid: MapGrid, latDeg: np.ndarray, lonDeg: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Map (x, y) in meters of planetocentric (lat, lon) on ``grid``'s projection;
    NaN stays NaN."""
    return _project(
        grid.kind,
        grid.referenceLatDeg,
        grid.radius,
        latDeg,
        lonDeg,
        grid.centerLongitude(),
    )


def mtrdrGrid(
    latDeg: np.ndarray,
    lonDeg: np.ndarray,
    pixelSize: float,
    snapToLattice: bool = False,
) -> MapGrid:
    """The MTRDR grid of the pixel centers ``(latDeg, lonDeg)`` (NaN ignored).

    φ_P comes from the median latitude. The tie point is (x_min, y_max) of the
    centers rounded to 0.1 m; with ``snapToLattice``, x_min is floored and
    y_max ceiled to multiples of the pixel size so that observations share
    pixel edges (G1, G2). The grid reaches the last center:
    width = ⌊(x_max − originX) / s⌋ + 1, and likewise for the height.
    """
    valid = np.isfinite(latDeg) & np.isfinite(lonDeg)
    if not valid.any():
        raise ValueError("no valid latitude and longitude")
    lat, lon = np.asarray(latDeg)[valid], np.asarray(lonDeg)[valid]
    phiP = referenceLatitude(float(np.median(lat)))
    kind: MapKind = "polar" if abs(phiP) == 90.0 else "equirectangular"
    radius = sphereRadius(phiP)
    x, y = _project(kind, phiP, radius, lat, lon, _sceneLongitude(lon))
    xMin, xMax, yMin, yMax = x.min(), x.max(), y.min(), y.max()
    s = float(pixelSize)
    if snapToLattice:
        originX, originY = math.floor(xMin / s) * s, math.ceil(yMax / s) * s
    else:
        originX, originY = round(xMin, _TIE_DECIMALS), round(yMax, _TIE_DECIMALS)
    return MapGrid(
        kind=kind,
        referenceLatDeg=phiP,
        radius=radius,
        pixelSize=s,
        originX=float(originX),
        originY=float(originY),
        width=math.floor((xMax - originX) / s) + 1,
        height=math.floor((originY - yMin) / s) + 1,
    )


@define(frozen=True, eq=False)
class Glt:
    """A geometry lookup table: the source line and sample of each output pixel,
    ``(height, width)`` int32, −1 where the pixel is empty."""

    sourceLine: np.ndarray
    sourceSample: np.ndarray


def _pixelCenters(grid: MapGrid) -> tuple[np.ndarray, np.ndarray]:
    s = grid.pixelSize
    xs = grid.originX + (np.arange(grid.width) + 0.5) * s
    ys = grid.originY - (np.arange(grid.height) + 0.5) * s
    return np.meshgrid(xs, ys)


def nearestNeighborGlt(
    grid: MapGrid,
    latDeg: np.ndarray,
    lonDeg: np.ndarray,
    maxDistanceFactor: float = 0.75 * np.sqrt(2),
) -> Glt:
    """For each output pixel center, the nearest DDR pixel center (a KD-tree on
    the projected centers); empty beyond ``maxDistanceFactor · s`` (G3).

    The threshold is in output pixels. DDR centers coarser than about
    1.5 s apart leave holes inside the footprint at the default (FRT00005850's
    are 23 × 26 m apart for s = 18 m; ledger G3).
    """
    x, y = mapCoordinates(grid, latDeg, lonDeg)
    valid = np.flatnonzero(np.isfinite(x) & np.isfinite(y))
    if valid.size == 0:
        raise ValueError("no valid latitude and longitude")
    tree = KDTree(np.column_stack([x.ravel()[valid], y.ravel()[valid]]))
    centerX, centerY = _pixelCenters(grid)
    _, nearest = tree.query(
        np.column_stack([centerX.ravel(), centerY.ravel()]),
        distance_upper_bound=maxDistanceFactor * grid.pixelSize,
        workers=-1,
    )
    found = nearest < valid.size
    line, sample = np.divmod(valid[np.where(found, nearest, 0)], np.shape(latDeg)[1])
    shape = (grid.height, grid.width)
    return Glt(
        np.where(found, line, -1).astype(np.int32).reshape(shape),
        np.where(found, sample, -1).astype(np.int32).reshape(shape),
    )


def _floatType(values: np.ndarray) -> np.dtype:
    return (
        values.dtype
        if np.issubdtype(values.dtype, np.floating)
        else np.dtype(np.float64)
    )


def applyGlt(cube: np.ndarray, glt: Glt) -> np.ndarray:
    """``(l, s, ...)`` to ``(height, width, ...)``, NaN where empty; floating
    input keeps its dtype. Pass band slices to bound memory."""
    filled = glt.sourceLine >= 0
    result = np.full(
        glt.sourceLine.shape + cube.shape[2:], np.nan, dtype=_floatType(cube)
    )
    result[filled] = cube[glt.sourceLine[filled], glt.sourceSample[filled]]
    return result


@define(frozen=True, eq=False)
class AreaWeights:
    """Overlap weights, ``matrix`` ((height · width) × (l · s)), in units of
    source pixels; ``looks`` (height, width) is the effective number of looks
    (Σw)² / Σw², 0 where nothing overlaps."""

    matrix: csr_matrix
    looks: np.ndarray


def _corners(values: np.ndarray) -> np.ndarray:
    """``(l + 1, s + 1)`` corners at the midpoints of each 2×2 block of
    centers; the edges are extrapolated half a step."""
    padded = np.pad(values, 1, mode="reflect", reflect_type="odd")
    return 0.25 * (
        padded[:-1, :-1] + padded[1:, :-1] + padded[:-1, 1:] + padded[1:, 1:]
    )


def areaWeights(
    grid: MapGrid, latDeg: np.ndarray, lonDeg: np.ndarray, subsamples: int = 4
) -> AreaWeights:
    """Area weights of each source pixel over the output pixels.

    A source pixel is the quadrilateral of its neighbors' midpoints, sampled at
    ``subsamples``² bilinear points; its weight on an output pixel is the
    fraction of its points there. A pixel next to an invalid center has an
    undefined corner and contributes nothing.
    """
    lines, samples = np.shape(latDeg)
    if lines < 2 or samples < 2:
        raise ValueError("area weighting needs at least 2 lines and 2 samples")
    x, y = mapCoordinates(grid, latDeg, lonDeg)
    u = (np.arange(subsamples) + 0.5) / subsamples
    along, across = np.meshgrid(u, u, indexing="ij")  # (line, sample) fractions

    def sample(corners: np.ndarray) -> np.ndarray:
        """``(l, s, n, n)`` bilinear points inside each quad."""
        c00 = corners[:-1, :-1, None, None]
        c01 = corners[:-1, 1:, None, None]
        c10 = corners[1:, :-1, None, None]
        c11 = corners[1:, 1:, None, None]
        return (1 - along) * ((1 - across) * c00 + across * c01) + along * (
            (1 - across) * c10 + across * c11
        )

    pointX = sample(_corners(x))
    pointY = sample(_corners(y))
    column = np.floor((pointX - grid.originX) / grid.pixelSize)
    row = np.floor((grid.originY - pointY) / grid.pixelSize)
    inside = (column >= 0) & (column < grid.width) & (row >= 0) & (row < grid.height)
    source = np.broadcast_to(
        np.arange(lines * samples).reshape(lines, samples, 1, 1), inside.shape
    )
    target = row[inside].astype(np.int64) * grid.width + column[inside].astype(np.int64)
    matrix = csr_matrix(
        (np.full(target.size, 1.0 / subsamples**2), (target, source[inside])),
        shape=(grid.height * grid.width, lines * samples),
    )
    matrix.sum_duplicates()
    total = np.asarray(matrix.sum(axis=1)).ravel()
    squares = np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel()
    looks = np.divide(total**2, squares, out=np.zeros_like(total), where=squares > 0)
    return AreaWeights(matrix, looks.reshape(grid.height, grid.width))


def applyAreaWeights(
    cube: np.ndarray, sigma: np.ndarray, weights: AreaWeights
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted means of ``cube`` ``(l, s, ...)`` and their 1σ from ``sigma``
    (same shape): σ² = Σw²σ² / (Σw)², over the valid (finite) sources only.
    Returns ``(height, width, ...)`` arrays, NaN where nothing valid overlaps."""
    height, width = weights.looks.shape
    rest = cube.shape[2:]
    values = cube.reshape(cube.shape[0] * cube.shape[1], -1)
    sigmas = sigma.reshape(values.shape)
    matrix = weights.matrix
    squared = matrix.multiply(matrix).tocsr()
    dtype = _floatType(cube)
    mean = np.empty((height * width, values.shape[1]), dtype=dtype)
    meanSigma = np.empty_like(mean)
    for start in range(0, values.shape[1], _BAND_CHUNK):
        block = slice(start, start + _BAND_CHUNK)
        v = values[:, block].astype(np.float64)
        sd = sigmas[:, block].astype(np.float64)
        valid = np.isfinite(v) & np.isfinite(sd)
        total = matrix @ valid.astype(np.float64)
        weighted = matrix @ np.where(valid, v, 0.0)
        variance = squared @ np.where(valid, sd**2, 0.0)
        with np.errstate(invalid="ignore", divide="ignore"):
            mean[:, block] = np.where(total > 0, weighted / total, np.nan)
            meanSigma[:, block] = np.where(total > 0, np.sqrt(variance) / total, np.nan)
    shape = (height, width) + rest
    return mean.reshape(shape), meanSigma.reshape(shape)
