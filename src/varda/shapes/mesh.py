"""A plate shape model: a triangle mesh in body-fixed km, and what is
measured on it. No Qt.

Coordinates follow the IAU convention for small bodies (Archinal et al.,
2018): body-fixed Cartesian km about the center of mass, planetocentric
latitude, east longitude 0–360° and radius from the center. Plates wind
counterclockwise seen from outside once ``oriented``: normals point out
and the signed volume is positive.
"""

from __future__ import annotations

import hashlib
from functools import cached_property

import numba
import numpy as np
from attrs import field, frozen


def _vertexArray(values: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.float64).reshape(-1, 3)


def _plateArray(values: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(values, dtype=np.int32).reshape(-1, 3)


@frozen(eq=False, slots=False)
class ShapeModel:
    """Vertices (n, 3) in km and plates (m, 3) indexing them; ``body`` is the
    body's name when known. Derived values are computed once."""

    vertices: np.ndarray = field(converter=_vertexArray)  # (n, 3) float64, km
    plates: np.ndarray = field(converter=_plateArray)  # (m, 3) int32
    name: str = ""
    body: str | None = None

    @property
    def plateCount(self) -> int:
        return len(self.plates)

    @cached_property
    def corners(self) -> np.ndarray:
        """(m, 3, 3): each plate's three corners."""
        return self.vertices[self.plates]

    @cached_property
    def _crosses(self) -> np.ndarray:
        corners = self.corners
        return np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])

    @cached_property
    def centroids(self) -> np.ndarray:
        """(m, 3) km."""
        return self.corners.mean(axis=1)

    @cached_property
    def areas(self) -> np.ndarray:
        """(m,) km²."""
        return 0.5 * np.linalg.norm(self._crosses, axis=1)

    @cached_property
    def normals(self) -> np.ndarray:
        """(m, 3) unit normals by the plates' winding (outward once
        ``oriented``); zero for a degenerate plate."""
        lengths = np.linalg.norm(self._crosses, axis=1, keepdims=True)
        return np.divide(
            self._crosses, lengths, out=np.zeros_like(self._crosses), where=lengths > 0
        )

    @cached_property
    def _edges(self) -> tuple[np.ndarray, np.ndarray]:
        """The undirected edges (k, 2), sorted, and how many plates share each."""
        directed = self.plates[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
        return np.unique(np.sort(directed, axis=1), axis=0, return_counts=True)

    @cached_property
    def meanEdgeKm(self) -> float:
        edges, _ = self._edges
        lengths = np.linalg.norm(
            self.vertices[edges[:, 0]] - self.vertices[edges[:, 1]], axis=1
        )
        return float(lengths.mean()) if len(lengths) else 0.0

    @cached_property
    def isClosed(self) -> bool:
        """Every edge is shared by exactly two plates (a watertight surface)."""
        _, counts = self._edges
        return bool(len(counts)) and bool((counts == 2).all())

    @cached_property
    def volume(self) -> float:
        """Signed volume in km³ (positive when the plates face out). Raises
        ValueError for a model with holes or a patch."""
        if not self.isClosed:
            raise ValueError("Only a closed shape model has a volume.")
        corners = self.corners
        return float(
            np.einsum(
                "ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])
            ).sum()
            / 6.0
        )

    @cached_property
    def contentHash(self) -> str:
        """SHA-256 of the vertices and plates, hex: plate data made for this
        model carries it (the name is not part of it)."""
        digest = hashlib.sha256()
        digest.update(self.vertices.astype("<f8").tobytes())
        digest.update(self.plates.astype("<i4").tobytes())
        return digest.hexdigest()

    @cached_property
    def adjacency(self) -> tuple[np.ndarray, np.ndarray]:
        """Plates sharing an edge, as CSR (indptr (m + 1,), indices)."""
        return _adjacency(self.plates)


def _adjacency(plates: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    count = len(plates)
    directed = plates[:, [0, 1, 1, 2, 2, 0]].reshape(-1, 2)
    keys = np.sort(directed, axis=1)
    owners = np.repeat(np.arange(count), 3)
    order = np.lexsort((keys[:, 1], keys[:, 0]))
    keys, owners = keys[order], owners[order]
    same = (keys[1:] == keys[:-1]).all(axis=1)
    first, second = owners[:-1][same], owners[1:][same]
    pairs = np.concatenate(
        [np.column_stack([first, second]), np.column_stack([second, first])]
    )
    pairs = pairs[np.lexsort((pairs[:, 1], pairs[:, 0]))]
    indptr = np.zeros(count + 1, np.int64)
    np.add.at(indptr, pairs[:, 0] + 1, 1)
    return np.cumsum(indptr), pairs[:, 1].astype(np.int64)


def oriented(model: ShapeModel) -> tuple[ShapeModel, bool]:
    """The model with consistent winding, normals facing out (a positive
    total volume when closed; away from the body's center, the origin, when
    open), and whether any plate was flipped."""
    flip = _consistentFlips(model.plates, *model.adjacency)
    plates = np.where(flip[:, None], model.plates[:, ::-1], model.plates)
    candidate = ShapeModel(model.vertices, plates, model.name, model.body)
    if _facesInward(candidate):
        flip = ~flip
        plates = plates[:, ::-1]
    if not flip.any():
        # the candidate, whose derived values are already computed
        return candidate, False
    return ShapeModel(model.vertices, plates, model.name, model.body), True


def _facesInward(model: ShapeModel) -> bool:
    """A closed model with negative volume, or an open one whose plates face
    the body's center (the origin) more than away from it."""
    corners = model.corners
    if model.isClosed:
        signed = np.einsum(
            "ij,ij->i", corners[:, 0], np.cross(corners[:, 1], corners[:, 2])
        )
        return bool(signed.sum() < 0)
    away = np.einsum("ij,ij->i", model.normals, model.centroids) * model.areas
    return bool(away.sum() < 0)


@numba.njit(cache=True)
def _consistentFlips(
    plates: np.ndarray, indptr: np.ndarray, indices: np.ndarray
) -> np.ndarray:
    """Which plates to reverse so that neighbors traverse their shared edge
    in opposite directions: a breadth-first walk from each part's first
    plate, which keeps its winding."""
    count = len(plates)
    flip = np.zeros(count, np.bool_)
    seen = np.zeros(count, np.bool_)
    queue = np.empty(count, np.int64)
    for start in range(count):
        if seen[start]:
            continue
        seen[start] = True
        head, tail = 0, 1
        queue[0] = start
        while head < tail:
            plate = queue[head]
            head += 1
            for k in range(indptr[plate], indptr[plate + 1]):
                other = indices[k]
                if seen[other]:
                    continue
                same = _sameDirection(plates[plate], plates[other])
                flip[other] = flip[plate] if not same else not flip[plate]
                seen[other] = True
                queue[tail] = other
                tail += 1
    return flip


@numba.njit(cache=True)
def _sameDirection(a: np.ndarray, b: np.ndarray) -> bool:
    """Whether plates ``a`` and ``b`` traverse their shared edge the same way
    (inconsistent winding)."""
    for i in range(3):
        u, v = a[i], a[(i + 1) % 3]
        for j in range(3):
            if b[j] == u and b[(j + 1) % 3] == v:
                return True
    return False


def latLonRadius(points: np.ndarray) -> np.ndarray:
    """(n, 3) body-fixed km → (n, 3): planetocentric latitude (°), east
    longitude 0–360 (°) and radius (km)."""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    radius = np.linalg.norm(points, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        latitude = np.degrees(np.arcsin(np.clip(points[:, 2] / radius, -1, 1)))
    longitude = np.degrees(np.arctan2(points[:, 1], points[:, 0])) % 360.0
    return np.column_stack([np.nan_to_num(latitude), longitude, radius])


def decimated(model: ShapeModel, maxPlates: int) -> ShapeModel:
    """A coarser model of at most ``maxPlates`` plates, by vertex clustering
    on a cubic grid (each cell's vertices become their mean; collapsed and
    duplicate plates go), coarsening the grid until it fits; the model
    itself when it already does."""
    if model.plateCount <= maxPlates:
        return model
    totalArea = float(model.areas.sum())
    cell = np.sqrt(2.0 * totalArea / maxPlates)
    while True:
        coarse = _clustered(model, cell)
        if coarse.plateCount <= maxPlates:
            return coarse
        cell *= 1.15


def _clustered(model: ShapeModel, cell: float) -> ShapeModel:
    cells = np.floor(model.vertices / cell).astype(np.int64)
    cells -= cells.min(axis=0)
    _, cluster, counts = np.unique(
        _packed(cells), return_inverse=True, return_counts=True
    )
    cluster = cluster.ravel()
    sums = np.zeros((len(counts), 3))
    np.add.at(sums, cluster, model.vertices)
    vertices = sums / counts[:, None]
    plates = cluster[model.plates]
    keep = (
        (plates[:, 0] != plates[:, 1])
        & (plates[:, 1] != plates[:, 2])
        & (plates[:, 2] != plates[:, 0])
    )
    plates = plates[keep]
    # a plate and its reverse (a collapsed sliver's two sides) cancel
    _, first, counts = np.unique(
        _packed(np.sort(plates, axis=1)), return_index=True, return_counts=True
    )
    plates = plates[np.sort(first[counts == 1])]
    used, remapped = np.unique(plates, return_inverse=True)
    name = f"{model.name} (decimated)" if model.name else "decimated"
    return ShapeModel(vertices[used], remapped.reshape(-1, 3), name, model.body)


def _packed(rows: np.ndarray) -> np.ndarray:
    """(n, 3) non-negative integers as one int64 each (21 bits a column),
    for fast 1-D uniques."""
    assert rows.max(initial=0) < 1 << 21, "too many cells or vertices to pack"
    rows = rows.astype(np.int64)
    return (rows[:, 0] << 42) | (rows[:, 1] << 21) | rows[:, 2]


_T = (1 + 5**0.5) / 2
_ICOSAHEDRON_VERTICES = [
    (-1, _T, 0), (1, _T, 0), (-1, -_T, 0), (1, -_T, 0),
    (0, -1, _T), (0, 1, _T), (0, -1, -_T), (0, 1, -_T),
    (_T, 0, -1), (_T, 0, 1), (-_T, 0, -1), (-_T, 0, 1),
]  # fmt: skip
_ICOSAHEDRON_PLATES = [
    (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
    (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
    (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
    (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
]  # fmt: skip


def icosphere(
    radiusKm: float = 1.0, subdivisions: int = 3, center=(0.0, 0.0, 0.0)
) -> ShapeModel:
    """A sphere of 20·4^subdivisions plates, vertices on the sphere,
    counterclockwise seen from outside."""
    vertices = [np.array(v, float) / np.linalg.norm(v) for v in _ICOSAHEDRON_VERTICES]
    plates = list(_ICOSAHEDRON_PLATES)
    for _ in range(subdivisions):
        midpoints: dict[tuple[int, int], int] = {}

        def midpoint(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key not in midpoints:
                point = vertices[a] + vertices[b]
                vertices.append(point / np.linalg.norm(point))
                midpoints[key] = len(vertices) - 1
            return midpoints[key]

        finer = []
        for a, b, c in plates:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            finer += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        plates = finer
    points = np.array(vertices) * radiusKm + np.asarray(center, float)
    return ShapeModel(points, np.array(plates, np.int32), f"icosphere {subdivisions}")
