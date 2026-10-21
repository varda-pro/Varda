import numpy as np
import pytest
from astropy.io import fits

from varda.shapes._tests.meshes import icosphere
from varda.shapes.plate_data import (
    AmbiguousColumns,
    PlateData,
    attach,
    plateData,
    readAltwgPlateTable,
    readPlateCsv,
    writePlateCsv,
)


def test_csv_round_trip_with_sigma(tmp_path):
    model = icosphere(1.0, 2)
    rng = np.random.default_rng(0)
    slope = plateData(model, "slope", "deg", rng.uniform(0, 40, model.plateCount),
                      rng.uniform(0, 2, model.plateCount), {"method": "test"})  # fmt: skip
    area = plateData(model, "area", "m²", model.areas * 1e6)
    path = tmp_path / "plates.csv"
    writePlateCsv(path, model, [slope, area])
    header = path.read_text("utf-8").splitlines()[:5]
    assert header[0] == f"# shape: {model.name}"
    assert header[1] == f"# shapeHash: {model.contentHash}"
    assert header[2] == f"# plates: {model.plateCount}"
    assert header[4] == "plate,slope [deg],slope sigma,area [m²]"
    back = readPlateCsv(path, model)
    assert [d.name for d in back] == ["slope", "area"]
    assert back[0].unit == "deg" and back[1].unit == "m²"
    assert np.allclose(back[0].values, slope.values)
    assert back[0].sigma is not None and slope.sigma is not None
    assert np.allclose(back[0].sigma, slope.sigma)
    assert back[1].sigma is None
    assert back[0].provenance == {"method": "test"}
    assert back[0].shapeHash == model.contentHash


def test_same_count_other_hash_is_refused_naming_both(tmp_path):
    model = icosphere(1.0, 2)
    moved = icosphere(1.01, 2)  # another resolution's twin: same plates, other hash
    data = plateData(model, "slope", "deg", np.zeros(model.plateCount))
    with pytest.raises(ValueError) as raised:
        attach(moved, data)
    message = str(raised.value)
    assert message.startswith("slope was made for another shape model")
    assert model.contentHash[:8] in message and moved.contentHash[:8] in message
    assert f"{model.plateCount} plates" in message


def test_other_count_is_refused(tmp_path):
    model = icosphere(1.0, 2)
    data = plateData(model, "slope", "deg", np.zeros(model.plateCount))
    path = tmp_path / "plates.csv"
    writePlateCsv(path, model, [data])
    with pytest.raises(ValueError, match="another shape model"):
        readPlateCsv(path, icosphere(1.0, 3))


def test_matching_data_attaches():
    model = icosphere(1.0, 1)
    attach(model, plateData(model, "x", "", np.ones(model.plateCount)))


def test_wrong_length_is_refused():
    model = icosphere(1.0, 1)
    with pytest.raises(ValueError, match="80 plates"):
        plateData(model, "x", "", np.ones(5))


def test_altwg_style_table(tmp_path):
    model = icosphere(1.0, 2)
    values = np.linspace(0, 30, model.plateCount)
    sigma = np.full(model.plateCount, 0.5)
    table = fits.BinTableHDU.from_columns(
        [
            fits.Column(
                name="FACET", format="J", array=np.arange(1, model.plateCount + 1)
            ),
            fits.Column(
                name="LATITUDE",
                format="D",
                unit="deg",
                array=np.zeros(model.plateCount),
            ),
            fits.Column(name="SLOPE", format="D", unit="deg", array=values),
            fits.Column(name="SIGMA", format="D", unit="deg", array=sigma),
        ]
    )
    path = tmp_path / "slope.fits"
    fits.HDUList([fits.PrimaryHDU(), table]).writeto(path)
    data = readAltwgPlateTable(path, model)
    assert data.name == "SLOPE" and data.unit == "deg"
    assert np.allclose(data.values, values)
    assert data.sigma is not None and np.allclose(data.sigma, sigma)
    assert data.shapeHash == model.contentHash


def test_altwg_table_of_another_count_is_refused(tmp_path):
    model = icosphere(1.0, 2)
    table = fits.BinTableHDU.from_columns(
        [fits.Column(name="SLOPE", format="D", array=np.zeros(7))]
    )
    path = tmp_path / "slope.fits"
    fits.HDUList([fits.PrimaryHDU(), table]).writeto(path)
    with pytest.raises(ValueError, match="7 plates"):
        readAltwgPlateTable(path, model)


def test_plate_data_is_frozen():
    model = icosphere(1.0, 1)
    data = plateData(model, "x", "", np.ones(model.plateCount))
    assert isinstance(data, PlateData) and data.plateCount == model.plateCount


def test_an_altwg_table_with_several_value_columns_asks_which(tmp_path):
    model = icosphere(1.0, 2)
    n = model.plateCount
    table = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="QUALITY", format="D", array=np.ones(n)),
            fits.Column(name="SLOPE", format="D", unit="deg", array=np.arange(n)),
        ]
    )
    path = tmp_path / "two.fits"
    fits.HDUList([fits.PrimaryHDU(), table]).writeto(path)
    with pytest.raises(AmbiguousColumns) as raised:
        readAltwgPlateTable(path, model)
    assert raised.value.columns == ["QUALITY", "SLOPE"]
    chosen = readAltwgPlateTable(path, model, column="SLOPE")
    assert chosen.name == "SLOPE" and chosen.values[5] == 5


def test_a_truncated_csv_is_refused(tmp_path):
    model = icosphere(1.0, 1)
    path = tmp_path / "plates.csv"
    writePlateCsv(path, model, [plateData(model, "x", "", np.ones(model.plateCount))])
    lines = path.read_text("utf-8").splitlines()
    path.write_text("\n".join(lines[:-3]) + "\n", "utf-8")
    with pytest.raises(ValueError, match="77 rows"):
        readPlateCsv(path, model)
