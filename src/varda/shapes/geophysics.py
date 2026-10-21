"""Shape Geophysics: per-plate gravity, slope, geopotential elevation and
tilt of a rotating, constant-density body. No Qt.

At each plate's centroid (body-fixed, rotating about +Z with period P,
ω = 2π/P):

- gravitational acceleration g and potential V from the polyhedron
  (``gravity.polyhedronGravity``, Werner & Scheeres 1997);
- centrifugal acceleration ω²(x, y, 0) and potential −½ω²(x² + y²);
- total acceleration a = g + ω²(x, y, 0), effective potential
  V_eff = V − ½ω²(x² + y²);
- **slope**: the angle between a and the plate's inward normal −n
  (Scheeres et al., 2016): 0° where the total acceleration points straight
  into the surface;
- **geopotential elevation**: (V_eff − V_ref)/|a| in meters (Scheeres et
  al., 2016; Barnouin et al., 2019), V_ref the area-weighted mean of V_eff
  over the plates (as ALTWG products) or its minimum;
- **tilt**: the angle between the surface normal and the radius vector
  (Hergenrother et al., 2014, §2.3.5); the surface normal is the
  area-weighted mean normal of the plates whose centroids lie within the
  tilt radius of the plate's (its own normal at radius 0).

Slope and elevation are not linear in ρ, so they are also given at ρ − σρ
and ρ + σρ (the gravitational terms scale with ρ; the centrifugal ones
don't).

Models of N > ``maxExactPlates`` (L) plates take their gravity from a
coarser closed polyhedron of L²/N plates (so the work stays that of an
L-plate model; at least 5000, at most L), evaluated at every plate's own centroid, its density
scaled by the volume ratio so the mass is kept. The coarse model is the
vertex-clustered model (``mesh.decimated``) when that is closed, else the
outermost surface along the rays of an icosphere from the center (which
fills overhangs). Centrifugal terms, normals, slopes and tilts use every
plate. The provenance records the coarse model ("gravitySource").
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal

import numpy as np
from attrs import asdict, frozen
from scipy.spatial import KDTree

from varda.shapes.gravity import CLOSED_ONLY, G, polyhedronGravity, solid
from varda.shapes.mesh import ShapeModel, decimated, icosphere, oriented
from varda.shapes.raycast import RayCaster
from varda.shapes.plate_data import PlateData, plateData

ProgressCallback = Callable[[int, str], None]
METHOD = (
    "Constant-density polyhedron gravity (Werner & Scheeres 1997) at plate "
    "centroids, plus the centrifugal term; slope and geopotential elevation "
    "after Scheeres et al. (2016); tilt after Hergenrother et al. (2014)"
)
# Vertex clustering is tried this many times before the radial model
_COARSE_TRIES = 4
# Fewer source plates than this bend slopes by tenths of a degree (the guide's
# table); more cost N × this many pair sums
_MIN_COARSE = 5000
# Tilt's neighbor lists stop here (about 30 s and a few hundred MB a chunk)
MAX_TILT_PAIRS = 5e8


@frozen
class GeophysicsSettings:
    density: float  # kg/m³
    densitySigma: float  # kg/m³, 1σ
    rotationPeriodH: float  # hours
    uRef: Literal["area-weighted mean", "minimum"] = "area-weighted mean"
    tiltRadiusM: float = 0.0  # m; 0: each plate's own normal
    maxExactPlates: int = 200_000


def geophysics(
    model: ShapeModel, settings: GeophysicsSettings, progress: ProgressCallback
) -> list[PlateData]:
    """The twelve per-plate datasets for ``model`` (see the module
    docstring). Raises ValueError for an open model or invalid settings."""
    if not model.isClosed:
        raise ValueError(CLOSED_ONLY)
    _check(settings)
    model, _ = oriented(solid(model))
    source, how = _gravitySource(model, settings.maxExactPlates)
    message = f"Gravity at {model.plateCount:,} plates from {source.plateCount:,}…"
    progress(0, message)
    potential, gravity = polyhedronGravity(
        source,
        model.volume / source.volume,  # the coarse model keeps the mass
        model.centroids,
        lambda f: progress(int(90 * f), message),
    )
    centroids = model.centroids * 1000.0  # m
    omega = 2 * math.pi / (settings.rotationPeriodH * 3600.0)
    centrifugal = omega**2 * np.column_stack(
        [centroids[:, 0], centroids[:, 1], np.zeros(len(centroids))]
    )
    spinPotential = -0.5 * omega**2 * (centroids[:, 0] ** 2 + centroids[:, 1] ** 2)
    normals, areas = model.normals, model.areas

    def at(density: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        total = density * gravity + centrifugal
        magnitude = np.linalg.norm(total, axis=1)
        effective = density * potential + spinPotential
        cosine = -np.einsum("ij,ij->i", normals, total) / magnitude
        slope = np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
        if settings.uRef == "minimum":
            reference = effective.min()
        else:
            reference = np.average(effective, weights=areas)
        return total, effective, slope, (effective - reference) / magnitude

    rho, sigma = settings.density, settings.densitySigma
    total, effective, slope, elevation = at(rho)
    _, _, slopeLow, elevationLow = at(rho - sigma)
    _, _, slopeHigh, elevationHigh = at(rho + sigma)
    progress(95, "Tilt…")
    tilt = _tilt(model, settings.tiltRadiusM)
    provenance = {
        "method": METHOD,
        "settings": asdict(settings),
        "G": G,
        "meanEdgeKm": model.meanEdgeKm,
        "evaluatedOn": source.plateCount,
        "gravitySource": {"plates": source.plateCount, "model": how},
        "plates": model.plateCount,
        "shape": model.name,
    }
    outputs = [
        ("area", "m²", areas * 1e6),
        ("gravitational acceleration", "m/s²", np.linalg.norm(rho * gravity, axis=1)),
        ("centrifugal acceleration", "m/s²", np.linalg.norm(centrifugal, axis=1)),
        ("total acceleration", "m/s²", np.linalg.norm(total, axis=1)),
        ("effective potential", "J/kg", effective),
        ("slope", "°", slope),
        ("geopotential elevation", "m", elevation),
        ("tilt", "°", tilt),
        ("slope (ρ−σ)", "°", slopeLow),
        ("slope (ρ+σ)", "°", slopeHigh),
        ("geopotential elevation (ρ−σ)", "m", elevationLow),
        ("geopotential elevation (ρ+σ)", "m", elevationHigh),
    ]
    progress(100, "Done")
    return [
        plateData(model, name, unit, values, provenance=provenance)
        for name, unit, values in outputs
    ]


def _check(settings: GeophysicsSettings) -> None:
    if not settings.density > 0:
        raise ValueError("The density must be positive.")
    if not 0 <= settings.densitySigma < settings.density:
        raise ValueError(
            "The density's σ must be at least 0 and less than the density."
        )
    if not settings.rotationPeriodH > 0:
        raise ValueError("The rotation period must be positive.")
    if settings.tiltRadiusM < 0:
        raise ValueError("The tilt radius can't be negative.")


def _gravitySource(model: ShapeModel, maxExact: int) -> tuple[ShapeModel, str]:
    """The polyhedron gravity comes from, and how it was made."""
    if model.plateCount <= maxExact:
        return model, "the model itself"
    cap = min(maxExact, max(maxExact**2 // model.plateCount, _MIN_COARSE))
    for _ in range(_COARSE_TRIES):
        coarse, _ = oriented(decimated(model, cap))
        if coarse.isClosed:
            return coarse, "vertex clustering (mesh.decimated)"
        cap = int(cap * 0.8)
    return _radialModel(model, cap), "outermost surface along icosphere rays"


def _radialModel(model: ShapeModel, maxPlates: int) -> ShapeModel:
    """An icosphere of at most ``maxPlates`` plates whose vertices are moved
    to the model's outermost surface along their directions from the center:
    closed by construction (overhangs are filled)."""
    subdivisions = max(0, int(math.log(max(maxPlates, 20) / 20, 4)))
    directions = icosphere(1.0, subdivisions)
    far = 2.0 * float(np.linalg.norm(model.vertices, axis=1).max())
    hits = RayCaster(model).intersect(directions.vertices * far, -directions.vertices)
    if (hits.plate < 0).any():
        raise ValueError(
            "Varda couldn't make a coarse model of this shape (the center of its "
            "coordinates is outside it); raise Evaluate exactly up to."
        )
    coarse = ShapeModel(
        hits.points, directions.plates, f"{model.name} (radial)", model.body
    )
    return oriented(coarse)[0]


def _tilt(model: ShapeModel, radiusM: float, chunk: int = 20_000) -> np.ndarray:
    """Degrees between the (radius-averaged) surface normal and the radius
    vector at each plate; neighbors are gathered ``chunk`` plates at a time,
    so memory stays bounded."""
    normals = model.normals
    if radiusM > 0:
        pairs = (
            math.pi * (radiusM / 1000.0) ** 2 / model.areas.mean() * model.plateCount
        )
        if pairs > MAX_TILT_PAIRS:
            raise ValueError(
                f"A {radiusM:g} m tilt radius averages about {pairs:.2g} plate pairs "
                f"on this model (at most {MAX_TILT_PAIRS:.0e}); choose a smaller tilt radius."
            )
        tree = KDTree(model.centroids)
        weighted = normals * model.areas[:, None]
        summed = np.empty_like(weighted)
        for start in range(0, model.plateCount, chunk):
            near = tree.query_ball_point(
                model.centroids[start : start + chunk], radiusM / 1000.0
            )
            counts = np.fromiter((len(n) for n in near), np.int64, len(near))
            flat = np.concatenate([np.asarray(n, np.int64) for n in near])
            offsets = np.concatenate([[0], np.cumsum(counts)[:-1]])
            summed[start : start + len(near)] = np.add.reduceat(weighted[flat], offsets)
        normals = summed / np.linalg.norm(summed, axis=1, keepdims=True)
    radial = model.centroids / np.linalg.norm(model.centroids, axis=1, keepdims=True)
    cosine = np.einsum("ij,ij->i", normals, radial)
    return np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
