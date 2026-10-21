import struct
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from varda.shapes import formats
from varda.shapes._tests.meshes import icosphere, reversed_
from varda.shapes.formats import UnsupportedShape, readShape, writeObj, writeStl


def writeObjText(path: Path, vertices, plates, scale=1.0) -> Path:
    lines = [f"v {x * scale} {y * scale} {z * scale}" for x, y, z in vertices]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in plates]
    path.write_text("# test\n" + "\n".join(lines) + "\n")
    return path


def test_obj(tmp_path):
    sphere = icosphere(1.0, 2)
    read = readShape(writeObjText(tmp_path / "s.obj", sphere.vertices, sphere.plates))
    assert read.model.plateCount == sphere.plateCount
    assert np.allclose(read.model.vertices, sphere.vertices)
    assert read.model.isClosed and read.suggestedUnit is None


def test_obj_quads_and_slashes_are_triangulated(tmp_path):
    path = tmp_path / "q.obj"
    path.write_text("v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1/1/1 2/2/2 3/3/3 4/4/4\n")
    assert readShape(path).model.plateCount == 2


def test_gaskell_plt(tmp_path):
    sphere = icosphere(1.0, 1)
    lines = [str(len(sphere.vertices))]
    lines += [f"{i + 1} {x} {y} {z}" for i, (x, y, z) in enumerate(sphere.vertices)]
    lines += [str(sphere.plateCount)]
    lines += [
        f"{i + 1} {a + 1} {b + 1} {c + 1}" for i, (a, b, c) in enumerate(sphere.plates)
    ]
    path = tmp_path / "s.plt"
    path.write_text("\n".join(lines) + "\n")
    model = readShape(path).model
    assert np.allclose(model.vertices, sphere.vertices)
    assert np.array_equal(model.plates, sphere.plates)


def test_gaskell_plt_with_both_counts_on_the_first_line(tmp_path):
    sphere = icosphere(1.0, 1)
    lines = [f"{len(sphere.vertices)} {sphere.plateCount}"]
    lines += [f"{i + 1} {x} {y} {z}" for i, (x, y, z) in enumerate(sphere.vertices)]
    lines += [
        f"{i + 1} {a + 1} {b + 1} {c + 1}" for i, (a, b, c) in enumerate(sphere.plates)
    ]
    path = tmp_path / "s.tab"
    path.write_text("\n".join(lines) + "\n")
    assert readShape(path).model.plateCount == sphere.plateCount


def icqText(q: int, radius: float = 1.0) -> str:
    """Gaskell's ICQ: q, then for each of the cube's 6 faces a (q+1)² grid
    of vertices (i fastest), projected onto a sphere."""
    faces = [
        lambda u, v: (u, v, 1.0), lambda u, v: (1.0, u, v), lambda u, v: (-v, 1.0, u),
        lambda u, v: (-1.0, -v, u), lambda u, v: (u, -1.0, -v), lambda u, v: (v, u, -1.0),
    ]  # fmt: skip
    lines = [str(q)]
    for face in faces:
        for j in range(q + 1):
            for i in range(q + 1):
                point = np.array(face(-1 + 2 * i / q, -1 + 2 * j / q))
                point = radius * point / np.linalg.norm(point)
                lines.append(" ".join(f"{c:.12f}" for c in point))
    return "\n".join(lines) + "\n"


def test_gaskell_icq_merges_the_face_seams(tmp_path):
    path = tmp_path / "s.icq"
    path.write_text(icqText(2))
    read = readShape(path)
    assert read.model.plateCount == 2 * 6 * 2 * 2  # 48
    assert len(read.model.vertices) == 6 * 2 * 2 + 2  # 54 written, 26 distinct
    assert read.model.isClosed and read.model.volume > 0


def writeXyzFits(path: Path, fillVertex: tuple[int, int] | None) -> Path:
    y, x = np.mgrid[0:3, 0:3].astype(np.float32)
    z = np.ones((3, 3), np.float32)
    if fillVertex is not None:
        x[fillVertex] = y[fillVertex] = z[fillVertex] = -1e38
    hdu = fits.PrimaryHDU(np.stack([x, y, z]))
    hdu.header["PLANE1"] = ("X coordinate", "[km]")
    hdu.header["PLANE2"] = ("Y coordinate", "[km]")
    hdu.header["PLANE3"] = ("Z coordinate", "[km]")
    hdu.writeto(path)
    return path


def test_fits_xyz_patch(tmp_path):
    full = readShape(writeXyzFits(tmp_path / "a.fits", None)).model
    holed = readShape(writeXyzFits(tmp_path / "b.fits", (0, 1))).model
    assert full.plateCount == 8 and not full.isClosed
    assert holed.plateCount == full.plateCount - 4


def test_obj_round_trip(tmp_path):
    sphere = icosphere(1.0, 2)
    writeObj(sphere, tmp_path / "out.obj")
    model = readShape(tmp_path / "out.obj").model
    assert np.abs(model.vertices - sphere.vertices).max() < 1e-9
    assert np.array_equal(model.plates, sphere.plates)


def test_obj_round_trip_keeps_the_content_hash(tmp_path):
    sphere = icosphere(1.0, 3)
    writeObj(sphere, tmp_path / "out.obj")
    assert readShape(tmp_path / "out.obj").model.contentHash == sphere.contentHash


def test_stl_holds_the_corners_to_float32(tmp_path):
    sphere = icosphere(1.0, 2)
    writeStl(sphere, tmp_path / "out.stl")
    data = (tmp_path / "out.stl").read_bytes()
    (count,) = struct.unpack("<I", data[80:84])
    rows = np.frombuffer(data[84:], np.uint8).reshape(count, 50)
    values = rows[:, :48].copy().view("<f4").reshape(count, 12)
    corners = values[:, 3:].reshape(-1, 3, 3)
    assert count == sphere.plateCount
    assert np.abs(corners - sphere.corners).max() < 1e-6


def test_meters_model_is_noticed(tmp_path):
    sphere = icosphere(0.245, 2)
    path = writeObjText(tmp_path / "m.obj", sphere.vertices, sphere.plates, 1000)
    read = readShape(path, body="Bennu")
    assert read.suggestedUnit == "m"
    assert "This model's size suggests meters; read as meters?" in read.notes
    meters = readShape(path, body="Bennu", unit="m")
    assert np.allclose(meters.model.vertices, sphere.vertices)
    assert meters.suggestedUnit is None
    assert meters.model.body == "Bennu"


def test_reversed_model_is_flipped_and_noted(tmp_path):
    sphere = reversed_(icosphere(1.0, 2))
    read = readShape(writeObjText(tmp_path / "r.obj", sphere.vertices, sphere.plates))
    assert "Normals were flipped to face outward." in read.notes
    assert read.model.volume > 0


def test_dsk_without_spiceypy_names_the_extra(tmp_path, monkeypatch):
    monkeypatch.setattr(formats, "spiceypy", None)
    path = tmp_path / "s.bds"
    path.write_bytes(b"DAS/DSK")
    with pytest.raises(UnsupportedShape, match="spice extra"):
        readShape(path)


def test_unknown_suffix(tmp_path):
    path = tmp_path / "s.xyz"
    path.write_text("1 2 3\n")
    with pytest.raises(UnsupportedShape):
        readShape(path)


def writeDsk(path: Path, model) -> Path:
    """A type 2 DSK segment of ``model`` (NAIF DSK Required Reading:
    dskmi2 builds the spatial index, dskw02 writes the segment)."""
    spice = pytest.importorskip("spiceypy")
    spaixd, spaixi = spice.dskmi2(
        model.vertices, model.plates + 1, 5.0, 4, 1_000_000, 100_000, 200_000,
        True, 2_000_000,
    )  # fmt: skip
    handle = spice.dskopn(str(path), "test", 0)
    radius = float(np.linalg.norm(model.vertices, axis=1).max())
    spice.dskw02(
        handle, 2101955, 1, 1, "IAU_BENNU", 1, np.zeros(10),
        -np.pi, np.pi, -np.pi / 2, np.pi / 2, 0.0, radius * 1.1,
        -1e9, 1e9, model.vertices, model.plates + 1, spaixd, spaixi,
    )  # fmt: skip
    spice.dskcls(handle, True)
    return path


def test_dsk_type_2(tmp_path):
    sphere = icosphere(0.25, 2)
    model = readShape(writeDsk(tmp_path / "s.bds", sphere)).model
    assert np.allclose(model.vertices, sphere.vertices)
    assert np.array_equal(model.plates, sphere.plates)
