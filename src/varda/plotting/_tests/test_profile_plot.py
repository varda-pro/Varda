"""The Profiles plot: one curve per line, distance in meters or kilometers,
vertical exaggeration, the pointer's spot on the image, and CSV export."""

import csv

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, makeImage
from varda.geo.measure import cumulativeDistance
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.lines.line_collection import LineCollection
from varda.plotting.profile_plot import ProfilePlotWidget


def _dem(pixel=20.0) -> VardaRaster:
    rows, cols = 30, 60
    heights = np.tile(np.arange(cols, dtype=np.float32) * 2.0, (rows, 1))[..., None]
    return VardaRaster(
        ArrayDataSource(
            heights,
            bandNames=["Elevation"],
            transform=Affine(pixel, 0, 0.0, 0, -pixel, 0.0),
            crs=CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs"),
        ),
        name="dem",
    )


@pytest.fixture
def plot(qtbot):
    widget = ProfilePlotWidget(_dem())
    qtbot.addWidget(widget)
    widget.resize(600, 400)
    widget.show()
    return widget


def _line(vertices):
    lines = LineCollection()
    lines.addLine(np.asarray(vertices, float))
    return lines.lines()


def test_a_line_becomes_a_curve_in_meters(plot):
    plot.setLines(_line([[0.5, 15], [40.5, 15]]))
    result = next(iter(plot.profiles.values()))
    assert result.distance[-1] == pytest.approx(800)  # 40 px of 20 m
    assert plot.plot.getAxis("bottom").labelText == "Distance (m)"
    assert plot.plot.getAxis("left").labelText == "Elevation"
    assert plot.verticalExaggeration() is not None
    assert "Vertical exaggeration" in plot.exaggeration.text()


def test_long_profiles_are_plotted_in_kilometers(qtbot):
    widget = ProfilePlotWidget(_dem(pixel=200.0))
    qtbot.addWidget(widget)
    widget.setLines(_line([[0.5, 15], [50.5, 15]]))  # 10 km
    assert widget.plot.getAxis("bottom").labelText == "Distance (km)"


def test_csv_has_every_sample_with_its_map_position(plot, tmp_path):
    plot.setLines(_line([[0.5, 15], [10.5, 15]]))
    plot.exportTo(tmp_path / "profile.csv")
    with open(tmp_path / "profile.csv") as f:
        rows = list(csv.reader(line for line in f if not line.startswith("#")))
    assert rows[0] == [
        "line",
        "distance_m",
        "x_px",
        "y_px",
        "map_x",
        "map_y",
        "Elevation",
    ]
    assert len(rows) == 12
    assert float(rows[-1][1]) == pytest.approx(200, rel=1e-5) and float(
        rows[-1][-1]
    ) == pytest.approx(20)


def test_csv_says_where_its_numbers_come_from(qtbot, tmp_path):
    # far enough that 6 significant figures would round the distance
    widget = ProfilePlotWidget(_dem(pixel=20000.0))
    qtbot.addWidget(widget)
    widget.setLines(_line([[0.5, 15], [50.5 + 1e-4, 15]]))
    widget.exportTo(tmp_path / "profile.csv")
    text = (tmp_path / "profile.csv").read_text()
    comments = [line for line in text.splitlines() if line.startswith("#")]
    header = "\n".join(comments)
    assert "Varda" in header and "image: dem" in header
    assert "CRS: Mars, sphere R = 3396.19 km, Equirectangular, center lat 0°" in header
    assert "CRS WKT:" in header
    assert "Equidistant Cylindrical" in header
    assert "distance unit: m (geodesic, Karney 2013, on sphere R = 3396.19 km" in header
    assert "Elevation" in header
    rows = list(csv.reader(line for line in text.splitlines() if line[:1] != "#"))
    assert rows[0][1] == "distance_m"
    last = float(rows[-1][1])
    result = next(iter(widget.profiles.values()))
    mapXY = widget.image.imageToGeo(result.positions)
    crs = widget.image.crs
    assert crs is not None
    expected = cumulativeDistance(crs, mapXY)[-1]
    assert last == pytest.approx(expected, abs=0.01)  # not 1 significant figure
    assert last > 900_000 and last != round(last, -1)


def test_csv_states_the_geodesic_method_and_its_limits(qtbot, tmp_path):
    # the real CRISM MTRDR CRS: a 3394.84 km sphere with lat_ts = 15°
    image = makeImage(CRISM_MTRDR_WKT, centerLonLat=(77.0, 18.0), pixelSize=18.0)
    widget = ProfilePlotWidget(image)
    qtbot.addWidget(widget)
    widget.setLines(_line([[2.5, 30], [60.5, 30]]))
    widget.exportTo(tmp_path / "profile.csv")
    header = "\n".join(
        line
        for line in (tmp_path / "profile.csv").read_text().splitlines()
        if line.startswith("#")
    )
    assert (
        "distance unit: m (geodesic, Karney 2013, on sphere R = 3394.84 km; "
        "IAU 2015 reference ellipsoid differs by " in header
    )
    assert "# projection distortion along the line: up to " in header
    assert "scale error" in header
    assert "georeferencing" in header


def _csv(widget: ProfilePlotWidget, path) -> tuple[str, list[list[str]]]:
    widget.exportTo(path)
    text = path.read_text()
    comments = "\n".join(ln for ln in text.splitlines() if ln.startswith("#"))
    rows = list(csv.reader(ln for ln in text.splitlines() if ln[:1] != "#"))
    return comments, rows


def test_csv_leaves_unmeasurable_distances_empty(qtbot, tmp_path):
    # an orthographic view of Mars: the line runs off the disk
    image = VardaRaster(
        ArrayDataSource(
            np.zeros((5, 20, 1), np.float32),
            transform=Affine(100_000.0, 0, 3_000_000.0, 0, -100_000.0, 0.0),
            crs=CRS.from_user_input("+proj=ortho +lat_0=0 +lon_0=0 +R=3396190"),
        ),
        name="disk",
    )
    widget = ProfilePlotWidget(image)
    qtbot.addWidget(widget)
    widget.setLines(_line([[0.0, 0.5], [10.0, 0.5]]))
    header, rows = _csv(widget, tmp_path / "profile.csv")
    assert "# distance gaps: " in header
    distances = [row[1] for row in rows[1:]]
    assert distances[0] == "0.0" and distances[-1] == ""


def test_csv_on_an_engineering_crs_is_planar(qtbot, tmp_path):
    image = VardaRaster(
        ArrayDataSource(
            np.zeros((10, 10, 1), np.float32),
            transform=Affine(20.0, 0, 0.0, 0, -20.0, 0.0),
            crs=CRS.from_user_input(
                'ENGCRS["Lab",EDATUM["Lab datum"],CS[Cartesian,2],'
                'AXIS["x",east,LENGTHUNIT["metre",1]],'
                'AXIS["y",north,LENGTHUNIT["metre",1]]]'
            ),
        ),
        name="lab",
    )
    widget = ProfilePlotWidget(image)
    qtbot.addWidget(widget)
    widget.setLines(_line([[0.0, 0.0], [3.0, 4.0]]))
    header, rows = _csv(widget, tmp_path / "profile.csv")
    assert (
        "# distance unit: m (planar, in the coordinate system's units "
        "(no body ellipsoid))" in header
    )
    assert float(rows[-1][1]) == pytest.approx(100.0)


def test_an_image_export_writes_a_png(plot, tmp_path):
    plot.setLines(_line([[0.5, 15], [10.5, 15]]))
    plot.exportTo(tmp_path / "profile.png")
    assert (tmp_path / "profile.png").stat().st_size > 1000
