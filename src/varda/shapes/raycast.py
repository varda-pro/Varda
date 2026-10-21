"""Ray casting and closest points on a shape model. No Qt.

A bounding volume hierarchy over the plates (each node's box split at the
median plate centroid along its longest axis, down to ``LEAF_SIZE`` plates)
prunes the plates a ray or point is tested against. Rays meet plates by the
Möller–Trumbore test (Möller & Trumbore, 1997); the nearest hit along the
ray wins, so on a non-star-shaped body (two lobes, an overhang) a ray finds
the surface it reaches first, never a point looked up by latitude and
longitude. The closest point on a plate is Ericson's (2005, §5.1.5)
region test. Distances are in the model's units (km).
"""

from __future__ import annotations

import numba
import numpy as np
from attrs import frozen

from varda.shapes.mesh import ShapeModel

LEAF_SIZE = 4
# Hits closer than this (km) along the ray are its own origin plate
_EPSILON = 1e-12


@frozen(eq=False)
class Hits:
    plate: np.ndarray  # (n,) int32, -1 for a miss
    distance: np.ndarray  # (n,) km along the ray (or to the point); NaN on a miss
    points: np.ndarray  # (n, 3) km; NaN on a miss


class RayCaster:
    """Intersects rays with, and finds closest points on, ``model``."""

    def __init__(self, model: ShapeModel):
        self.model = model
        self._corners = np.ascontiguousarray(model.corners)
        self._nodes = _build(self._corners)

    def intersect(self, origins: np.ndarray, directions: np.ndarray) -> Hits:
        """The nearest plate each ray (origin, direction; directions needn't
        be unit) meets in front of its origin; ``distance`` is in units of
        the direction's length (km for unit directions)."""
        origins = np.ascontiguousarray(origins, np.float64).reshape(-1, 3)
        directions = np.ascontiguousarray(directions, np.float64).reshape(-1, 3)
        plate, distance = _intersect(self._corners, *self._nodes, origins, directions)
        points = origins + directions * distance[:, None]
        return Hits(plate, distance, points)

    def closestPoint(self, points: np.ndarray) -> Hits:
        """The nearest point on the surface to each point."""
        points = np.ascontiguousarray(points, np.float64).reshape(-1, 3)
        plate, distance, nearest = _closest(self._corners, *self._nodes, points)
        return Hits(plate, distance, nearest)


@numba.njit(cache=True)
def _build(
    corners: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """The hierarchy as arrays: box minima and maxima per node, children
    (-1 for a leaf), and each leaf's range in ``order`` (plate indices)."""
    count = corners.shape[0]
    centroids = (corners[:, 0] + corners[:, 1] + corners[:, 2]) / 3.0
    low = np.empty((count, 3))
    high = np.empty((count, 3))
    for p in range(count):
        for axis in range(3):
            low[p, axis] = min(
                corners[p, 0, axis], corners[p, 1, axis], corners[p, 2, axis]
            )
            high[p, axis] = max(
                corners[p, 0, axis], corners[p, 1, axis], corners[p, 2, axis]
            )
    capacity = 2 * max(count, 1)
    boxLow = np.empty((capacity, 3))
    boxHigh = np.empty((capacity, 3))
    children = np.full((capacity, 2), -1, np.int64)
    ranges = np.zeros((capacity, 2), np.int64)
    order = np.arange(count)
    stack = np.empty((capacity, 1), np.int64)
    ranges[0, 0], ranges[0, 1] = 0, count
    nodes = 1
    stack[0, 0] = 0
    depth = 1
    while depth > 0:
        depth -= 1
        node = stack[depth, 0]
        start, end = ranges[node, 0], ranges[node, 1]
        for axis in range(3):
            boxLow[node, axis] = np.inf
            boxHigh[node, axis] = -np.inf
        for k in range(start, end):
            p = order[k]
            for axis in range(3):
                boxLow[node, axis] = min(boxLow[node, axis], low[p, axis])
                boxHigh[node, axis] = max(boxHigh[node, axis], high[p, axis])
        if end - start <= LEAF_SIZE:
            continue
        extent = boxHigh[node] - boxLow[node]
        axis = int(np.argmax(extent))
        segment = order[start:end]
        segment = segment[np.argsort(centroids[segment, axis])]
        order[start:end] = segment
        middle = (start + end) // 2
        left, right = nodes, nodes + 1
        nodes += 2
        ranges[left, 0], ranges[left, 1] = start, middle
        ranges[right, 0], ranges[right, 1] = middle, end
        children[node, 0], children[node, 1] = left, right
        stack[depth, 0] = left
        stack[depth + 1, 0] = right
        depth += 2
    return (
        boxLow[:nodes].copy(),
        boxHigh[:nodes].copy(),
        children[:nodes].copy(),
        ranges[:nodes].copy(),
        order,
    )


@numba.njit(cache=True)
def _slab(
    origin: np.ndarray,
    inverse: np.ndarray,
    low: np.ndarray,
    high: np.ndarray,
    best: float,
) -> bool:
    """Whether the ray meets the box nearer than ``best``."""
    near, far = 0.0, best
    for axis in range(3):
        t1 = (low[axis] - origin[axis]) * inverse[axis]
        t2 = (high[axis] - origin[axis]) * inverse[axis]
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 != t1 or t2 != t2:  # 0 × inf: the ray runs along a face
            continue
        near = max(near, t1)
        far = min(far, t2)
        if near > far:
            return False
    return True


@numba.njit(cache=True)
def _mollerTrumbore(
    origin: np.ndarray, direction: np.ndarray, triangle: np.ndarray
) -> float:
    """The distance along the ray to the triangle (Möller & Trumbore,
    1997), or inf when it misses or lies behind."""
    e1 = triangle[1] - triangle[0]
    e2 = triangle[2] - triangle[0]
    p = np.cross(direction, e2)
    determinant = np.dot(e1, p)
    if abs(determinant) < 1e-300:
        return np.inf
    inverse = 1.0 / determinant
    s = origin - triangle[0]
    u = np.dot(s, p) * inverse
    if u < 0.0 or u > 1.0:
        return np.inf
    q = np.cross(s, e1)
    v = np.dot(direction, q) * inverse
    if v < 0.0 or u + v > 1.0:
        return np.inf
    t = np.dot(e2, q) * inverse
    return t if t > _EPSILON else np.inf


@numba.njit(cache=True, parallel=True)
def _intersect(
    corners: np.ndarray,
    boxLow: np.ndarray,
    boxHigh: np.ndarray,
    children: np.ndarray,
    ranges: np.ndarray,
    order: np.ndarray,
    origins: np.ndarray,
    directions: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    rays = origins.shape[0]
    plates = np.full(rays, -1, np.int32)
    distances = np.full(rays, np.nan)
    for r in numba.prange(rays):  # ty: ignore[not-iterable]
        origin, direction = origins[r], directions[r]
        inverse = np.empty(3)
        for axis in range(3):
            inverse[axis] = 1.0 / direction[axis] if direction[axis] != 0.0 else np.inf
        best = np.inf
        bestPlate = -1
        stack = np.empty(128, np.int64)
        stack[0] = 0
        depth = 1
        while depth > 0:
            depth -= 1
            node = stack[depth]
            if not _slab(origin, inverse, boxLow[node], boxHigh[node], best):
                continue
            if children[node, 0] < 0:
                for k in range(ranges[node, 0], ranges[node, 1]):
                    t = _mollerTrumbore(origin, direction, corners[order[k]])
                    if t < best:
                        best, bestPlate = t, order[k]
            else:
                stack[depth] = children[node, 0]
                stack[depth + 1] = children[node, 1]
                depth += 2
        if bestPlate >= 0:
            plates[r] = bestPlate
            distances[r] = best
    return plates, distances


@numba.njit(cache=True)
def _boxDistance2(point: np.ndarray, low: np.ndarray, high: np.ndarray) -> float:
    total = 0.0
    for axis in range(3):
        if point[axis] < low[axis]:
            total += (low[axis] - point[axis]) ** 2
        elif point[axis] > high[axis]:
            total += (point[axis] - high[axis]) ** 2
    return total


@numba.njit(cache=True)
def _closestOnTriangle(point: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Ericson (2005), Real-Time Collision Detection, §5.1.5."""
    a, b, c = t[0], t[1], t[2]
    ab, ac, ap = b - a, c - a, point - a
    d1, d2 = np.dot(ab, ap), np.dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a
    bp = point - b
    d3, d4 = np.dot(ab, bp), np.dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        return a + ab * (d1 / (d1 - d3))
    cp = point - c
    d5, d6 = np.dot(ab, cp), np.dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        return a + ac * (d2 / (d2 - d6))
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        return b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))
    denominator = 1.0 / (va + vb + vc)
    return a + ab * (vb * denominator) + ac * (vc * denominator)


@numba.njit(cache=True, parallel=True)
def _closest(
    corners: np.ndarray,
    boxLow: np.ndarray,
    boxHigh: np.ndarray,
    children: np.ndarray,
    ranges: np.ndarray,
    order: np.ndarray,
    points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    count = points.shape[0]
    plates = np.full(count, -1, np.int32)
    distances = np.full(count, np.nan)
    nearest = np.full((count, 3), np.nan)
    for i in numba.prange(count):  # ty: ignore[not-iterable]
        point = points[i]
        best2 = np.inf
        stack = np.empty(128, np.int64)
        stack[0] = 0
        depth = 1
        while depth > 0:
            depth -= 1
            node = stack[depth]
            if _boxDistance2(point, boxLow[node], boxHigh[node]) >= best2:
                continue
            if children[node, 0] < 0:
                for k in range(ranges[node, 0], ranges[node, 1]):
                    candidate = _closestOnTriangle(point, corners[order[k]])
                    d2 = np.sum((candidate - point) ** 2)
                    if d2 < best2:
                        best2 = d2
                        plates[i] = order[k]
                        nearest[i] = candidate
            else:
                # the nearer child last, so it's searched first and prunes more
                left, right = children[node, 0], children[node, 1]
                if _boxDistance2(point, boxLow[left], boxHigh[left]) < _boxDistance2(
                    point, boxLow[right], boxHigh[right]
                ):
                    left, right = right, left
                stack[depth] = left
                stack[depth + 1] = right
                depth += 2
        if plates[i] >= 0:
            distances[i] = np.sqrt(best2)
    return plates, distances, nearest
