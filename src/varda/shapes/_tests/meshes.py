"""Synthetic shape models for tests: spheres, cubes, two lobes and an open
patch, all in km."""

from __future__ import annotations

import numpy as np

from varda.shapes.mesh import ShapeModel

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


def cube(sideKm: float = 1.0) -> ShapeModel:
    """An axis-aligned cube centered on the origin, 12 plates."""
    h = sideKm / 2
    corners = np.array(
        [[x, y, z] for x in (-h, h) for y in (-h, h) for z in (-h, h)], float
    )
    plates = [
        (0, 1, 3), (0, 3, 2), (4, 6, 7), (4, 7, 5),  # x = -h, x = +h
        (0, 4, 5), (0, 5, 1), (2, 3, 7), (2, 7, 6),  # y = -h, y = +h
        (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3),  # z = -h, z = +h
    ]  # fmt: skip
    return ShapeModel(corners, np.array(plates, np.int32), "cube")


def twoLobes() -> ShapeModel:
    """Two unit spheres centered at x = ±1.5 km: a radial ray along +x
    crosses the surface twice (at 0.5 and 2.5 km)."""
    a = icosphere(1.0, 3, (1.5, 0.0, 0.0))
    b = icosphere(1.0, 3, (-1.5, 0.0, 0.0))
    vertices = np.vstack([a.vertices, b.vertices])
    plates = np.vstack([a.plates, b.plates + len(a.vertices)])
    return ShapeModel(vertices, plates.astype(np.int32), "two lobes")


def openPatch(cells: int = 4) -> ShapeModel:
    """A gently curved square patch (z = 1 − (x² + y²)/10), facing +z."""
    xs = np.linspace(-1, 1, cells + 1)
    x, y = np.meshgrid(xs, xs)
    vertices = np.column_stack([x.ravel(), y.ravel(), 1 - (x**2 + y**2).ravel() / 10])
    plates = []
    for row in range(cells):
        for col in range(cells):
            i = row * (cells + 1) + col
            plates += [(i, i + 1, i + cells + 2), (i, i + cells + 2, i + cells + 1)]
    return ShapeModel(vertices, np.array(plates, np.int32), "open patch")


def reversed_(model: ShapeModel) -> ShapeModel:
    """The model with every plate's winding reversed."""
    return ShapeModel(model.vertices, model.plates[:, ::-1].copy(), model.name)
