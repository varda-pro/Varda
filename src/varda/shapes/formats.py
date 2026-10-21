"""Reading and writing plate shape models. No Qt.

Formats, by suffix:

- Wavefront OBJ (``.obj``): ``v x y z`` and ``f a b c …`` lines, 1-based,
  ``a/t/n`` forms and polygons fanned into triangles (Wavefront Technologies,
  *Advanced Visualizer* manual, App. B1); other lines are ignored.
- Gaskell PLT (``.plt``, ``.tab``): the vertex count, then ``index x y z``
  lines, the plate count, then ``index a b c`` lines (1-based); some
  archives put both counts on the first line (PDS Small Bodies Node
  shape-model descriptions).
- Gaskell ICQ (``.icq``, or ``.txt`` whose first line is one integer): the
  implicitly connected quadrilateral: q, then the 6 (q + 1)² vertices of the
  cube faces' grids, i fastest (Gaskell et al., 2008). Each grid square is
  two plates; vertices repeated along the faces' seams are merged, so a
  global model is closed.
- FITS (``.fits``, ``.fit``): an ALTWG/SPC cube whose ``PLANEn`` cards name X,
  Y and Z coordinate planes (km): a grid square whose four corners all have
  coordinates is two plates (fill values, below ``ALTWG_FILL_LIMIT``, are
  skipped). An open patch.
- NAIF DSK type 2 (``.bds``), through SpiceyPy when the ``spice`` extra is
  installed (NAIF DSK Required Reading).

Every model is ``oriented`` after reading (normals out), and a note says so
when plates were flipped. With the body's name, a model whose mean radius is
about 1000 times the body's is noticed as written in meters.
"""

from __future__ import annotations

import re
import types
from pathlib import Path
from typing import Literal

import numpy as np
from astropy.io import fits
from attrs import frozen

from varda.geo.bodies import bodyNamed
from varda.image_loading.data_sources.fits_data_source import (
    ALTWG_FILL_LIMIT,
    GEOMETRY_PLANES,
    planeCards,
)
from varda.shapes.mesh import ShapeModel, oriented
from varda.terrain.solid import Solid, stlBytes

try:
    import spiceypy as _spiceypy

    spiceypy: types.ModuleType | None = _spiceypy
except ImportError:  # the optional "spice" extra is not installed
    spiceypy = None

SHAPE_SUFFIXES = (".obj", ".plt", ".tab", ".icq", ".txt", ".fits", ".fit", ".bds")
FLIPPED_NOTE = "Normals were flipped to face outward."
METERS_NOTE = "This model's size suggests meters; read as meters?"
# A model this many times larger than its body (in km) is in meters
_METERS_RATIO = (300.0, 3000.0)


class UnsupportedShape(ValueError):
    """A file Varda can't read as a shape model, and why."""


@frozen
class ShapeRead:
    model: ShapeModel
    notes: list[str]  # plain sentences for the user
    suggestedUnit: str | None  # "m" when the model looks like it's in meters


def readShape(
    path: Path,
    body: str | None = None,
    unit: Literal["km", "m"] | None = None,
) -> ShapeRead:
    """The shape model in ``path`` (see the module docstring), on ``body``
    when given, its vertices in km (``unit="m"`` divides them by 1000).
    Raises UnsupportedShape for a format Varda can't read, and ValueError
    for a file it can't parse."""
    path = Path(path)
    vertices, plates = _read(path)
    if unit == "m":
        vertices = vertices / 1000.0
    model, flipped = oriented(ShapeModel(vertices, plates, path.stem, body))
    notes = [FLIPPED_NOTE] if flipped else []
    suggested = None
    if unit is None and _looksLikeMeters(model, body):
        suggested = "m"
        notes.append(METERS_NOTE)
    return ShapeRead(model, notes, suggested)


def _looksLikeMeters(model: ShapeModel, body: str | None) -> bool:
    known = bodyNamed(body) if body else None
    if known is None or model.plateCount == 0:
        return False
    meanKm = float(np.linalg.norm(model.vertices, axis=1).mean())
    ratio = meanKm / (known.meanRadius / 1000.0)
    return _METERS_RATIO[0] < ratio < _METERS_RATIO[1]


def _read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    suffix = path.suffix.lower()
    if suffix == ".obj":
        return _readObj(path)
    if suffix in (".plt", ".tab"):
        return _readPlt(path)
    if suffix == ".icq" or (suffix == ".txt" and _isIcq(path)):
        return _readIcq(path)
    if suffix in (".fits", ".fit"):
        return _readFits(path)
    if suffix == ".bds":
        return _readDsk(path)
    raise UnsupportedShape(
        f"Varda can't read {path.name} as a shape model: use OBJ, Gaskell PLT "
        "or ICQ, FITS X/Y/Z planes, or a NAIF DSK (.bds)."
    )


def _readObj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    vertices: list[list[float]] = []
    plates: list[tuple[int, int, int]] = []
    with path.open("r", encoding="utf-8", errors="replace") as file:
        for line in file:
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "v":
                vertices.append([float(value) for value in parts[1:4]])
            elif parts[0] == "f":
                corners = [_objIndex(part, len(vertices)) for part in parts[1:]]
                plates += [
                    (corners[0], corners[k], corners[k + 1])
                    for k in range(1, len(corners) - 1)
                ]
    if not vertices or not plates:
        raise ValueError(f"{path.name} has no vertices or no faces.")
    return np.array(vertices), np.array(plates)


def _objIndex(token: str, count: int) -> int:
    """A face corner's 0-based vertex index (OBJ's negative indices count
    back from the last vertex read)."""
    index = int(token.split("/")[0])
    return index - 1 if index > 0 else count + index


def _numbers(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8", errors="replace") as file:
        return [line.split() for line in file if line.strip()]


def _readPlt(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = _numbers(path)
    try:
        if len(rows[0]) == 2:
            vertexCount, plateCount = int(rows[0][0]), int(rows[0][1])
            vertexRows = rows[1 : 1 + vertexCount]
            plateRows = rows[1 + vertexCount : 1 + vertexCount + plateCount]
        else:
            vertexCount = int(rows[0][0])
            vertexRows = rows[1 : 1 + vertexCount]
            plateCount = int(rows[1 + vertexCount][0])
            start = 2 + vertexCount
            plateRows = rows[start : start + plateCount]
        vertices = np.array([[float(v) for v in row[1:4]] for row in vertexRows])
        plates = np.array([[int(v) for v in row[1:4]] for row in plateRows]) - 1
    except (IndexError, ValueError) as error:
        raise ValueError(f"{path.name} isn't a Gaskell plate file ({error}).") from None
    if len(vertices) != vertexCount or len(plates) != plateCount:
        raise ValueError(f"{path.name} ends before its counts say.")
    return vertices, plates


def _isIcq(path: Path) -> bool:
    with path.open("r", encoding="utf-8", errors="replace") as file:
        return re.fullmatch(r"\s*\d+\s*", file.readline()) is not None


def _readIcq(path: Path) -> tuple[np.ndarray, np.ndarray]:
    rows = _numbers(path)
    q = int(rows[0][0])
    side = q + 1
    expected = 6 * side * side
    if len(rows) - 1 < expected:
        raise ValueError(f"{path.name}: ICQ q = {q} needs {expected} vertices.")
    written = np.array([[float(v) for v in row[:3]] for row in rows[1 : 1 + expected]])
    grid = np.arange(expected).reshape(6, side, side)  # face, j, i
    a, b = grid[:, :-1, :-1], grid[:, :-1, 1:]
    c, d = grid[:, 1:, 1:], grid[:, 1:, :-1]
    plates = np.concatenate(
        [np.stack([a, b, c], -1).reshape(-1, 3), np.stack([a, c, d], -1).reshape(-1, 3)]
    )
    vertices, merged = np.unique(written, axis=0, return_inverse=True)
    return vertices, merged.ravel()[plates]


def _readFits(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with fits.open(path, memmap=False) as hdus:
        header, data = hdus[0].header, hdus[0].data
        if data is None or data.ndim != 3:
            raise UnsupportedShape(f"{path.name} has no cube of coordinate planes.")
        planes = {
            name.lower(): (number, unit)
            for number, (name, unit) in planeCards(header).items()
        }
        axes = []
        for role in ("x", "y", "z"):
            found = next(
                (planes[n] for n in GEOMETRY_PLANES[role] if n in planes), None
            )
            if found is None:
                raise UnsupportedShape(
                    f"{path.name} has no {role.upper()} coordinate plane, so it "
                    "isn't a shape model (its planes: "
                    + ", ".join(sorted(planes))
                    + ")."
                )
            number, unit = found
            scale = 0.001 if unit.strip().lower() in ("m", "meters", "metres") else 1.0
            axes.append(np.asarray(data[number - 1], np.float64) * scale)
    xyz = np.stack(axes, -1)  # rows, cols, 3
    filled = np.any(np.asarray(axes) <= ALTWG_FILL_LIMIT, axis=0)
    valid = np.logical_and(np.all(np.isfinite(xyz), axis=-1), np.logical_not(filled))
    rows, cols = valid.shape
    index = np.arange(rows * cols).reshape(rows, cols)
    a, b = index[:-1, :-1], index[:-1, 1:]
    c, d = index[1:, 1:], index[1:, :-1]
    square = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, 1:] & valid[1:, :-1]
    plates = np.concatenate(
        [
            np.stack([a, b, c], -1)[square],
            np.stack([a, c, d], -1)[square],
        ]
    )
    if len(plates) == 0:
        raise ValueError(f"{path.name} has no grid square with four valid corners.")
    used, remapped = np.unique(plates, return_inverse=True)
    return xyz.reshape(-1, 3)[used], remapped.reshape(-1, 3)


def _readDsk(path: Path) -> tuple[np.ndarray, np.ndarray]:
    if spiceypy is None:
        raise UnsupportedShape(
            "Install the spice extra to read DSK files (uv sync --extra spice)."
        )
    handle = spiceypy.dasopr(str(path))
    try:
        with spiceypy.no_found_check():
            descriptor, found = spiceypy.dlabfs(handle)
        if not found:
            raise ValueError(f"{path.name} has no DSK segment.")
        vertexCount, plateCount = spiceypy.dskz02(handle, descriptor)
        vertices = np.asarray(spiceypy.dskv02(handle, descriptor, 1, vertexCount))
        plates = np.asarray(spiceypy.dskp02(handle, descriptor, 1, plateCount)) - 1
    finally:
        spiceypy.dascls(handle)
    return vertices, plates


def writeObj(model: ShapeModel, path: Path) -> None:
    """The model as Wavefront OBJ, vertices in km to every bit (17
    significant digits), so it reads back with the same content hash and
    its plate data still matches."""
    with Path(path).open("w", encoding="utf-8") as file:
        file.write(f"# {model.name}: {len(model.vertices)} vertices, ")
        file.write(f"{model.plateCount} plates, km; written by Varda\n")
        np.savetxt(file, model.vertices, fmt="v %.17g %.17g %.17g")
        np.savetxt(file, model.plates + 1, fmt="f %d %d %d")


def writeStl(model: ShapeModel, path: Path) -> None:
    """The model as binary STL (float32 corners, km)."""
    solid = Solid(model.vertices, model.plates)
    note = f"{model.name}, km"
    Path(path).write_bytes(stlBytes(solid, note))
