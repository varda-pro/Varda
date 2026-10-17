import json
import math
import re
from pathlib import Path

import numpy as np
import pytest
import rasterio

from varda.crism.projection import IAU2000_A, MapGrid
from varda.crism.smile import standardVector
from varda.crism.writers import (
    EnviBands,
    EnviCubeWriter,
    historyString,
    sha256File,
    writeCog,
    writeSidecars,
)
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

NODATA = 65535.0


def _grid(width: int = 4, height: int = 3) -> MapGrid:
    return MapGrid(
        kind="equirectangular",
        referenceLatDeg=15.0,
        radius=IAU2000_A,
        pixelSize=18.0,
        originX=4417887.6,
        originY=1091944.9,
        width=width,
        height=height,
    )


def _bands() -> EnviBands:
    return EnviBands(
        names=("a", "b"),
        wavelengths=np.array([1000.5, 2000.25]),
        fwhm=np.array([6.5, 6.75]),
        bbl=np.array([1, 0]),
        defaultBands=(2, 1, 1),
    )


def _cube() -> np.ndarray:
    cube = np.arange(24, dtype=np.float32).reshape(3, 4, 2) / 7
    cube[1, 2, 0] = np.nan
    cube[2, 3, 1] = np.inf
    return cube


def _write(path: Path, cube: np.ndarray, **extra: str) -> None:
    with EnviCubeWriter(
        path, 3, 4, _bands(), _grid(), "a test product", extra
    ) as writer:
        writer.write(0, cube[:2])
        writer.write(2, cube[2:])


def _read(path: Path, bands: int = 2) -> np.ndarray:
    return np.fromfile(path, dtype="<f4").reshape(bands, 3, 4)


def _header(path: Path) -> str:
    return path.with_suffix(".hdr").read_text()


def _value(header: str, key: str) -> str:
    match = re.search(rf"^{key}\s*=\s*(.*?)(?=^\w[\w ]*\s=|\Z)", header, re.M | re.S)
    assert match is not None, key
    return match.group(1).strip()


def test_header_matches_mtrdr_style(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    _write(path, _cube())
    header = _header(path)
    grid = _grid()
    assert header.startswith("ENVI")
    assert float(_value(header, "data ignore value")) == NODATA
    assert _value(header, "interleave") == "bsq"
    assert _value(header, "data type") == "4"
    assert _value(header, "byte order") == "0"
    assert _value(header, "header offset") == "0"
    assert _value(header, "file type") == "ENVI Standard"
    assert _value(header, "samples") == "4"
    assert _value(header, "lines") == "3"
    assert _value(header, "bands") == "2"
    assert _value(header, "map info") == "{" + grid.enviMapInfo() + "}"
    assert _value(header, "coordinate system string") == "{" + grid.crsWkt() + "}"
    assert _value(header, "wavelength units") == "Nanometers"
    assert _value(header, "default bands") == "{2,1,1}"
    for key, expected in (
        ("wavelength", [1000.5, 2000.25]),
        ("fwhm", [6.5, 6.75]),
        ("bbl", [1, 0]),
    ):
        text = _value(header, key).strip("{}")
        assert [float(v) for v in text.split(",")] == expected
    assert [n.strip() for n in _value(header, "band names").strip("{}").split(",")] == [
        "a",
        "b",
    ]
    assert "a test product" in _value(header, "description")
    assert not path.with_name(path.name + ".part").exists()


def test_extra_header_keys(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    _write(path, _cube(), **{"varda history": "PHT(a)_MAP(b)"})
    assert _value(_header(path), "varda history") == "{PHT(a)_MAP(b)}"


def test_nan_written_as_nodata(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    cube = _cube()
    _write(path, cube)
    data = _read(path)
    assert data[0, 1, 2] == NODATA
    assert data[1, 2, 3] == NODATA
    assert np.isfinite(data).all()
    expected = np.where(np.isfinite(cube), cube, NODATA).transpose(2, 0, 1)
    np.testing.assert_array_equal(data, expected.astype(np.float32))


def test_write_band_blocks(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    cube = _cube()
    with EnviCubeWriter(path, 3, 4, _bands(), _grid(), "", {}) as writer:
        writer.writeBands(1, cube[:, :, 1:])
        writer.writeBands(0, cube[:, :, :1])
    expected = np.where(np.isfinite(cube), cube, NODATA).transpose(2, 0, 1)
    np.testing.assert_array_equal(_read(path), expected.astype(np.float32))


def test_unwritten_lines_are_nodata(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    with EnviCubeWriter(path, 3, 4, _bands(), None, "", {}) as writer:
        writer.write(1, _cube()[1:2])
    data = _read(path)
    assert (data[:, 0] == NODATA).all() and (data[:, 2] == NODATA).all()
    assert "map info" not in _header(path)


def test_failed_write_leaves_previous_product(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    _write(path, _cube())
    before = (path.read_bytes(), path.with_suffix(".hdr").read_bytes())
    with pytest.raises(RuntimeError, match="boom"):
        with EnviCubeWriter(path, 3, 4, _bands(), _grid(), "new", {}) as writer:
            writer.write(0, np.ones((1, 4, 2), dtype=np.float32))
            raise RuntimeError("boom")
    assert (path.read_bytes(), path.with_suffix(".hdr").read_bytes()) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x_vmtr.hdr", "x_vmtr.img"]


def test_failed_first_write_leaves_nothing(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    with pytest.raises(RuntimeError):
        with EnviCubeWriter(path, 3, 4, _bands(), _grid(), "", {}):
            raise RuntimeError("boom")
    assert list(tmp_path.iterdir()) == []


def test_opens_in_varda(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    cube = _cube()
    _write(
        path,
        cube,
        **{
            "varda sigma file": "x_vmtr_sigma.hdr",
            "varda sigma kind": "instrument-noise",
            "varda sigma steps": "PHT, MAP",
        },
    )
    source = ENVIDataSource(str(path))
    np.testing.assert_allclose(source.wavelengths, [1000.5, 2000.25])
    assert source.nodata == NODATA
    assert tuple(source.transform)[:6] == tuple(_grid().transform())[:6]
    data = source.readAllBands()
    valid = np.isfinite(cube)
    np.testing.assert_allclose(data[valid], cube[valid])
    assert np.isnan(data[~valid]).all()
    sigma = source.extraMetadata["sigma"]
    assert sigma["path"] == str(tmp_path / "x_vmtr_sigma.hdr")
    assert sigma["kind"] == "instrument-noise"
    assert sigma["steps"] == ["PHT", "MAP"]


def test_bands_from_standard_vector() -> None:
    vector = standardVector()
    bands = EnviBands.fromStandardVector(vector, defaultBands=(3, 2, 1))
    assert len(bands.names) == 0
    np.testing.assert_array_equal(bands.wavelengths, vector.wavelengths)
    np.testing.assert_array_equal(bands.bbl, vector.bbl)
    assert bands.defaultBands == (3, 2, 1)


def test_history_string() -> None:
    steps = ["PHT(areoid-poly)", "DSK(5.0)", "MAP(eqc;18m;nn)"]
    assert historyString(steps) == "PHT(areoid-poly)_DSK(5.0)_MAP(eqc;18m;nn)"


def test_sidecars_json(tmp_path: Path) -> None:
    stem = tmp_path / "x_vmtr"
    recipe = {
        "steps": ["a"],
        "k": np.float32(5.0),
        "n": np.int64(3),
        "arr": np.arange(2),
    }
    provenance = {
        "score": math.inf,
        "nan": float("nan"),
        "nested": {"v": np.float64(2.5)},
    }
    diagnostics = {"gain": np.arange(6.0).reshape(2, 3)}
    files = writeSidecars(stem, recipe, provenance, diagnostics)
    assert [f.name for f in files] == [
        "x_vmtr.recipe.json",
        "x_vmtr.provenance.json",
        "x_vmtr.diagnostics.npz",
    ]
    loaded = json.loads(files[0].read_text())
    assert loaded == {"steps": ["a"], "k": 5.0, "n": 3, "arr": [0, 1]}
    prov = json.loads(files[1].read_text())
    assert prov["score"] is None and prov["nan"] is None
    assert prov["nested"] == {"v": 2.5}
    with np.load(files[2]) as npz:
        np.testing.assert_array_equal(npz["gain"], diagnostics["gain"])
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(f.name for f in files)


def test_sha256_file(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"abc")
    assert (
        sha256File(path)
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


def test_cog_export(tmp_path: Path) -> None:
    path = tmp_path / "x_vmtr.img"
    cube = _cube()
    _write(path, cube)
    target = writeCog(path, tmp_path / "x_vmtr.tif")
    with rasterio.open(target) as src:
        assert src.count == 2 and src.nodata == NODATA
        assert src.transform.a == 18.0
        np.testing.assert_allclose(src.read(1)[0, 0], cube[0, 0, 0])
    assert not list(tmp_path.glob("*.part"))
