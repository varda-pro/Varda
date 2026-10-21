"""The gravity of a constant-density polyhedron. No Qt.

Werner & Scheeres (1997, CMDA 65, 313), after Werner (1994, CMDA 59, 253):
for a closed polyhedron of density ρ with outward plate normals, at a field
point p,

    U = ½Gρ Σ_edges r_e·E_e·r_e L_e − ½Gρ Σ_faces r_f·F_f·r_f ω_f
    ∇U = −Gρ Σ_edges E_e·r_e L_e + Gρ Σ_faces F_f·r_f ω_f

where r_e and r_f run from p to any point of the edge or face, F_f = n_f n_fᵀ
(n_f the face's outward normal), E_e = n_A n_A,eᵀ + n_B n_B,eᵀ for the two
faces A and B sharing the edge (n_A,e the outward normal of the edge in
face A's plane), L_e = ln((r_i + r_j + e)/(r_i + r_j − e)) for the edge's
ends at distances r_i, r_j and its length e, and ω_f the face's signed solid
angle seen from p (van Oosterom & Strackee, 1983). The sums are exact for
the polyhedron, inside and outside it. Werner's U is positive (GM/r far
away); Varda reports the **potential** V = −U (J/kg, negative, −GM/r far
away) and the **acceleration** ∇U (m/s², toward the body).

G is CODATA 2018's 6.67430 × 10⁻¹¹ m³ kg⁻¹ s⁻².
"""

from __future__ import annotations

import math
from collections.abc import Callable

import numba
import numpy as np

from varda.shapes.mesh import ShapeModel

G = 6.67430e-11  # m³ kg⁻¹ s⁻² (CODATA 2018)
CLOSED_ONLY = "Gravity needs a closed shape model; this one has holes or is a patch."
# Plates smaller than this fraction of the mean plate area add nothing
_DEGENERATE_AREA = 1e-12
# Field points per kernel call, so long runs report progress
_CHUNK = 4096


def polyhedronGravity(
    model: ShapeModel,
    densityKgM3: float,
    points: np.ndarray,
    progress: Callable[[float], None] | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """The potential (J/kg, negative) and the acceleration (m/s², (n, 3))
    at ``points`` (km, body-fixed) of ``model`` filled at ``densityKgM3``.
    The model must be closed and wound outward (``mesh.oriented``); raises
    ValueError when it isn't closed."""
    model = solid(model)
    vertices = model.vertices * 1000.0  # m
    edges, edgeDyads = _edgeDyads(vertices, model.plates)
    normals = model.normals
    faceDyads = np.einsum("fi,fj->fij", normals, normals)
    field = np.ascontiguousarray(points, np.float64).reshape(-1, 3) * 1000.0
    potential = np.empty(len(field))
    acceleration = np.empty((len(field), 3))
    for start in range(0, len(field), _CHUNK):
        stop = min(start + _CHUNK, len(field))
        u, g = _werner(
            vertices, edges, edgeDyads, model.plates, faceDyads, field[start:stop]
        )
        potential[start:stop] = u
        acceleration[start:stop] = g
        if progress is not None:
            progress(stop / len(field))
    scale = G * densityKgM3
    return -scale * potential, scale * acceleration


def solid(model: ShapeModel) -> ShapeModel:
    """The model without zero-area plates, which add nothing to the field
    (and have no normal); raises ValueError unless what remains is closed."""
    areas = model.areas
    keep = areas > _DEGENERATE_AREA * areas.mean()
    if not keep.all():
        model = ShapeModel(model.vertices, model.plates[keep], model.name, model.body)
    if not model.isClosed:
        raise ValueError(CLOSED_ONLY)
    return model


def _edgeDyads(
    vertices: np.ndarray, plates: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Each edge once, as (k, 2) vertex indices, and its E_e (k, 3, 3)."""
    corners = vertices[plates]
    crosses = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals = crosses / np.linalg.norm(crosses, axis=1, keepdims=True)
    # each plate's three directed edges (a → b, in its winding)
    starts = plates.ravel()
    ends = plates[:, [1, 2, 0]].ravel()
    owner = np.repeat(np.arange(len(plates)), 3)
    direction = vertices[ends] - vertices[starts]
    direction /= np.linalg.norm(direction, axis=1, keepdims=True)
    faceNormal = normals[owner]
    edgeNormal = np.cross(direction, faceNormal)  # in the face's plane, outward
    dyads = np.einsum("ki,kj->kij", faceNormal, edgeNormal)
    # the two directed copies of an edge (a → b in A, b → a in B) share a key
    keys = np.sort(np.column_stack([starts, ends]), axis=1)
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    summed = np.zeros((len(unique), 3, 3))
    np.add.at(summed, inverse.ravel(), dyads)
    return unique.astype(np.int64), summed


@numba.njit(cache=True, parallel=True)
def _werner(
    vertices: np.ndarray,
    edges: np.ndarray,
    edgeDyads: np.ndarray,
    plates: np.ndarray,
    faceDyads: np.ndarray,
    field: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Werner's U/(Gρ) and ∇U/(Gρ) at each field point (m), in scalar
    arithmetic (no arrays made in the loops)."""
    count = field.shape[0]
    potential = np.zeros(count)
    gradient = np.zeros((count, 3))
    for p in numba.prange(count):  # ty: ignore[not-iterable]
        px, py, pz = field[p, 0], field[p, 1], field[p, 2]
        u = gx = gy = gz = 0.0
        for e in range(edges.shape[0]):
            a, b = edges[e, 0], edges[e, 1]
            ax, ay, az = vertices[a, 0] - px, vertices[a, 1] - py, vertices[a, 2] - pz
            bx, by, bz = vertices[b, 0] - px, vertices[b, 1] - py, vertices[b, 2] - pz
            da = math.sqrt(ax * ax + ay * ay + az * az)
            db = math.sqrt(bx * bx + by * by + bz * bz)
            ex, ey, ez = bx - ax, by - ay, bz - az
            length = math.sqrt(ex * ex + ey * ey + ez * ez)
            if da + db - length <= 1e-12 * length:
                # the point lies on the edge, where E_e·r_e is 0 (r_e runs
                # along the edge), so the term's limit is 0
                continue
            L = math.log((da + db + length) / (da + db - length))
            E = edgeDyads[e]
            rx = E[0, 0] * ax + E[0, 1] * ay + E[0, 2] * az
            ry = E[1, 0] * ax + E[1, 1] * ay + E[1, 2] * az
            rz = E[2, 0] * ax + E[2, 1] * ay + E[2, 2] * az
            u += (ax * rx + ay * ry + az * rz) * L
            gx -= rx * L
            gy -= ry * L
            gz -= rz * L
        for f in range(plates.shape[0]):
            i, j, k = plates[f, 0], plates[f, 1], plates[f, 2]
            x1, y1, z1 = vertices[i, 0] - px, vertices[i, 1] - py, vertices[i, 2] - pz
            x2, y2, z2 = vertices[j, 0] - px, vertices[j, 1] - py, vertices[j, 2] - pz
            x3, y3, z3 = vertices[k, 0] - px, vertices[k, 1] - py, vertices[k, 2] - pz
            d1 = math.sqrt(x1 * x1 + y1 * y1 + z1 * z1)
            d2 = math.sqrt(x2 * x2 + y2 * y2 + z2 * z2)
            d3 = math.sqrt(x3 * x3 + y3 * y3 + z3 * z3)
            numerator = (
                x1 * (y2 * z3 - z2 * y3)
                + y1 * (z2 * x3 - x2 * z3)
                + z1 * (x2 * y3 - y2 * x3)
            )
            denominator = (
                d1 * d2 * d3
                + d1 * (x2 * x3 + y2 * y3 + z2 * z3)
                + d2 * (x3 * x1 + y3 * y1 + z3 * z1)
                + d3 * (x1 * x2 + y1 * y2 + z1 * z2)
            )
            omega = 2.0 * math.atan2(numerator, denominator)
            F = faceDyads[f]
            fx = F[0, 0] * x1 + F[0, 1] * y1 + F[0, 2] * z1
            fy = F[1, 0] * x1 + F[1, 1] * y1 + F[1, 2] * z1
            fz = F[2, 0] * x1 + F[2, 1] * y1 + F[2, 2] * z1
            u -= (x1 * fx + y1 * fy + z1 * fz) * omega
            gx += fx * omega
            gy += fy * omega
            gz += fz * omega
        potential[p] = 0.5 * u
        gradient[p, 0] = gx
        gradient[p, 1] = gy
        gradient[p, 2] = gz
    return potential, gradient
