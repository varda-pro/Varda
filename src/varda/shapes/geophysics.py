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

Models of more than ``maxExactPlates`` plates are evaluated on a coarser
model (``mesh.decimated``): each plate takes the gravity of the coarse plate
whose centroid is nearest its own, and its potential is carried to its own centroid to
first order (V(p) ≈ V(q) − g·(p − q)); centrifugal terms, normals, slopes and
tilts use the full model. The provenance records the plate count actually
evaluated.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Literal

import numpy as np
from attrs import asdict, frozen
from scipy.spatial import KDTree

from varda.shapes.gravity import CLOSED_ONLY, G, polyhedronGravity
from varda.shapes.mesh import ShapeModel, decimated, oriented
from varda.shapes.plate_data import PlateData, plateData

ProgressCallback = Callable[[int, str], None]
METHOD = (
    "Constant-density polyhedron gravity (Werner & Scheeres 1997) at plate "
    "centroids, plus the centrifugal term; slope and geopotential elevation "
    "after Scheeres et al. (2016); tilt after Hergenrother et al. (2014)"
)
# Coarse models a closed one can't be made from are tried this many times
_COARSE_TRIES = 6


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
    model, _ = oriented(model)
    evaluated = _evaluatedModel(model, settings.maxExactPlates)
    progress(0, f"Gravity at {evaluated.plateCount:,} plates…")
    potential, gravity = polyhedronGravity(
        evaluated,
        1.0,
        evaluated.centroids,
        lambda f: progress(int(90 * f), f"Gravity at {evaluated.plateCount:,} plates…"),
    )
    centroids = model.centroids * 1000.0  # m
    if evaluated is not model:
        progress(90, "Carrying gravity to every plate…")
        _, nearest = KDTree(evaluated.centroids).query(model.centroids)
        offset = centroids - evaluated.centroids[nearest] * 1000.0
        gravity = gravity[nearest]
        potential = potential[nearest] - np.einsum("ij,ij->i", gravity, offset)
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
        "evaluatedOn": evaluated.plateCount,
        "plates": model.plateCount,
        "shape": model.name,
    }
    outputs = [
        ("area", "m²", areas * 1e6),
        ("gravitational acceleration", "m/s²", np.linalg.norm(rho * gravity, axis=1)),
        ("centrifugal acceleration", "m/s²", np.linalg.norm(centrifugal, axis=1)),
        ("total acceleration", "m/s²", np.linalg.norm(total, axis=1)),
        ("potential", "J/kg", effective),
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


def _evaluatedModel(model: ShapeModel, maxPlates: int) -> ShapeModel:
    """The model, or a closed coarser one of at most ``maxPlates`` plates."""
    if model.plateCount <= maxPlates:
        return model
    cap = maxPlates
    for _ in range(_COARSE_TRIES):
        coarse, _ = oriented(decimated(model, cap))
        if coarse.isClosed:
            return coarse
        cap = int(cap * 0.8)
    raise ValueError(
        f"Varda couldn't make a closed model of at most {maxPlates:,} plates from "
        f"this one; raise the plate limit (it has {model.plateCount:,})."
    )


def _tilt(model: ShapeModel, radiusM: float) -> np.ndarray:
    """Degrees between the (radius-averaged) surface normal and the radius
    vector at each plate."""
    normals = model.normals
    if radiusM > 0:
        tree = KDTree(model.centroids)
        pairs = tree.query_pairs(radiusM / 1000.0, output_type="ndarray")
        weighted = normals * model.areas[:, None]
        summed = weighted.copy()
        np.add.at(summed, pairs[:, 0], weighted[pairs[:, 1]])
        np.add.at(summed, pairs[:, 1], weighted[pairs[:, 0]])
        normals = summed / np.linalg.norm(summed, axis=1, keepdims=True)
    radial = model.centroids / np.linalg.norm(model.centroids, axis=1, keepdims=True)
    cosine = np.einsum("ij,ij->i", normals, radial)
    return np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))
