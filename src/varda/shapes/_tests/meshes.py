"""Synthetic shape models for tests: spheres (``mesh.icosphere``), cubes,
two lobes and an open patch, all in km."""

from __future__ import annotations

import numpy as np

from varda.shapes.mesh import ShapeModel, icosphere

# icosphere is re-exported for the tests
__all__ = ["cube", "icosphere", "openPatch", "reversed_", "twoLobes", "uvSphere"]


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


def uvSphere(radiusKm: float, rings: int, segments: int) -> ShapeModel:
    """A latitude–longitude sphere of 2·segments·(rings − 1) plates, built
    without Python loops (for large test models)."""
    lat = np.linspace(-np.pi / 2, np.pi / 2, rings + 1)[1:-1]
    lon = np.linspace(0, 2 * np.pi, segments, endpoint=False)
    la, lo = np.meshgrid(lat, lon, indexing="ij")
    ring = np.column_stack(
        [
            (np.cos(la) * np.cos(lo)).ravel(),
            (np.cos(la) * np.sin(lo)).ravel(),
            np.sin(la).ravel(),
        ]
    )
    vertices = np.vstack([[0, 0, -1], ring, [0, 0, 1]]) * radiusKm
    index = 1 + np.arange((rings - 1) * segments).reshape(rings - 1, segments)
    nxt = np.roll(index, -1, axis=1)
    a, b, c, d = index[:-1], nxt[:-1], nxt[1:], index[1:]
    quads = np.concatenate(
        [np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)]
    )
    south = np.column_stack([np.zeros(segments, int), nxt[0], index[0]])
    top = len(vertices) - 1
    north = np.column_stack([np.full(segments, top), index[-1], nxt[-1]])
    plates = np.vstack([quads, south, north]).astype(np.int32)
    return ShapeModel(vertices, plates, "uv sphere")
