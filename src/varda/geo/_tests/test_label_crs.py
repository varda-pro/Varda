"""CRSs and transforms built from PDS3, ISIS and PDS4 map projection groups
(spec §4.8). PDS3 labels are written to ``tmp_path`` and read with
``labelHints``; ISIS and PDS4 groups are given as dicts."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import CRS

from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, HIRISE_POLAR_PROJ
from varda.geo.equivalence import sameCrs
from varda.geo.label_crs import LabelCrs, labelCrs
from varda.geo.labels import LabelHints, labelHints
from varda.geo.transform import transformPoints

MARS_A_KM, MARS_C_KM = 3396.19, 3376.20


def _pds3(tmp_path: Path, projection: str, target: str = "MARS") -> LabelHints:
    """The hints of a PDS3 label with this ``IMAGE_MAP_PROJECTION`` body."""
    label = tmp_path / "x.lbl"
    label.write_text(
        f"PDS_VERSION_ID = PDS3\nTARGET_NAME = {target}\n"
        f"OBJECT = IMAGE_MAP_PROJECTION\n{projection}\n"
        "END_OBJECT = IMAGE_MAP_PROJECTION\nEND\n"
    )
    return labelHints(str(label))


def _isis(group: dict[str, str], target: str | None = "Mars") -> LabelHints:
    direction = group.get("LONGITUDEDIRECTION", "PositiveEast")
    latitude = group.get("LATITUDETYPE", "Planetocentric").lower()
    return LabelHints(
        target,
        latitude,
        "west" if direction.endswith("West") else "east",
        None,
        group,
        "ISIS cube label",
    )


def _built(hints: LabelHints) -> LabelCrs:
    result = labelCrs(hints)
    assert result is not None
    return result


def _crs(result: LabelCrs) -> CRS:
    assert result.wkt is not None, result.missing
    return CRS.from_user_input(result.wkt)


def _parameter(crs: CRS, name: str) -> float:
    operation = crs.coordinate_operation
    assert operation is not None
    return float(next(p.value for p in operation.params if p.name == name))


def _longitudeAxis(crs: CRS) -> str:
    geodetic = crs.geodetic_crs
    assert geodetic is not None
    return next(
        a.direction for a in geodetic.axis_info if a.direction in ("east", "west")
    )


# --- The spec's cases ---


def test_isis_equirectangular_local_radius_is_crism() -> None:
    result = _built(
        _isis(
            {
                "PROJECTIONNAME": "Equirectangular",
                "EQUATORIALRADIUS": "3396190.0",
                "POLARRADIUS": "3376200.0",
                "LATITUDETYPE": "Planetocentric",
                "CENTERLATITUDE": "15.0",
                "CENTERLONGITUDE": "0.0",
            }
        )
    )
    assert sameCrs(result.wkt, CRISM_MTRDR_WKT)
    assert not result.complete
    assert any("local radius" in line for line in result.assumed)
    assert result.projection == "Equirectangular"
    assert result.source == "ISIS cube label"


def test_hirise_polar_is_complete(tmp_path: Path) -> None:
    result = _built(
        _pds3(
            tmp_path,
            'MAP_PROJECTION_TYPE = "POLAR STEREOGRAPHIC"\n'
            "A_AXIS_RADIUS = 3376.2 <KM>\nB_AXIS_RADIUS = 3376.2 <KM>\n"
            "C_AXIS_RADIUS = 3376.2 <KM>\nCOORDINATE_SYSTEM_NAME = PLANETOCENTRIC\n"
            "POSITIVE_LONGITUDE_DIRECTION = EAST\n"
            "CENTER_LATITUDE = 90.0 <DEG>\nCENTER_LONGITUDE = 0.0 <DEG>",
        )
    )
    assert sameCrs(result.wkt, HIRISE_POLAR_PROJ)
    assert result.complete
    assert result.projection == "POLAR STEREOGRAPHIC"


def test_west_planetographic_label(tmp_path: Path) -> None:
    result = _built(
        _pds3(
            tmp_path,
            "MAP_PROJECTION_TYPE = EQUIRECTANGULAR\n"
            f"A_AXIS_RADIUS = {MARS_A_KM} <KM>\nC_AXIS_RADIUS = {MARS_C_KM} <KM>\n"
            "COORDINATE_SYSTEM_NAME = PLANETOGRAPHIC\n"
            "POSITIVE_LONGITUDE_DIRECTION = WEST\n"
            "CENTER_LATITUDE = 0.0\nCENTER_LONGITUDE = 100.0",
        )
    )
    crs = _crs(result)
    assert _parameter(crs, "Longitude of natural origin") == pytest.approx(-100)
    assert crs.ellipsoid is not None
    assert crs.ellipsoid.semi_minor_metre == pytest.approx(MARS_C_KM * 1000)
    assert crs.ellipsoid.semi_major_metre == pytest.approx(MARS_A_KM * 1000)
    assert _longitudeAxis(crs) == "west"
    assert result.complete


def _gdalLabel(tmp_path: Path, direction: str) -> Path:
    """The spec §4.8 label, detached, with a 10 × 10 byte image."""
    (tmp_path / "x.img").write_bytes(bytes(100))
    label = tmp_path / "x.lbl"
    label.write_text(
        "PDS_VERSION_ID = PDS3\n"
        "RECORD_TYPE = FIXED_LENGTH\nRECORD_BYTES = 10\nFILE_RECORDS = 10\n"
        '^IMAGE = "x.img"\nTARGET_NAME = MARS\n'
        "OBJECT = IMAGE\n  LINES = 10\n  LINE_SAMPLES = 10\n  BANDS = 1\n"
        "  SAMPLE_TYPE = MSB_UNSIGNED_INTEGER\n  SAMPLE_BITS = 8\n"
        "END_OBJECT = IMAGE\n"
        "OBJECT = IMAGE_MAP_PROJECTION\n"
        "  MAP_PROJECTION_TYPE = EQUIRECTANGULAR\n"
        "  A_AXIS_RADIUS = 3396.19 <KM>\n  C_AXIS_RADIUS = 3396.19 <KM>\n"
        f"  POSITIVE_LONGITUDE_DIRECTION = {direction}\n"
        "  COORDINATE_SYSTEM_NAME = PLANETOCENTRIC\n"
        "  CENTER_LATITUDE = 0.0 <DEG>\n  CENTER_LONGITUDE = 100.0 <DEG>\n"
        "  MAP_SCALE = 0.5 <KM/PIXEL>\n"
        "  LINE_PROJECTION_OFFSET = 100.5 <PIXEL>\n"
        "  SAMPLE_PROJECTION_OFFSET = -20.5 <PIXEL>\n"
        "END_OBJECT = IMAGE_MAP_PROJECTION\nEND\n"
    )
    return label


@pytest.mark.parametrize("direction", ["EAST", "WEST"])
def test_pds3_transform_matches_gdal(tmp_path: Path, direction: str) -> None:
    label = _gdalLabel(tmp_path, direction)
    result = _built(labelHints(str(label)))
    with rasterio.open(label) as src:
        gdal = tuple(src.transform)[:6]
    assert result.transform == pytest.approx(gdal)
    assert result.transform == pytest.approx((500, 0, 10_000, 0, -500, 50_500))


def test_isis_transform() -> None:
    result = _built(
        _isis(
            {
                "PROJECTIONNAME": "SimpleCylindrical",
                "EQUATORIALRADIUS": "3396190.0",
                "CENTERLONGITUDE": "0.0",
                "PIXELRESOLUTION": "18",
                "UPPERLEFTCORNERX": "-1000",
                "UPPERLEFTCORNERY": "5000",
            }
        )
    )
    assert result.transform == (18, 0, -1000, 0, -18, 5000)


def test_unsupported_projection(tmp_path: Path) -> None:
    result = _built(
        _pds3(
            tmp_path,
            'MAP_PROJECTION_TYPE = "OBLIQUE CYLINDRICAL"\nA_AXIS_RADIUS = 3396.19',
        )
    )
    assert result.wkt is None
    assert "OBLIQUE CYLINDRICAL" in result.missing[0]
    assert not result.complete


def test_missing_radii_use_target_sphere(tmp_path: Path) -> None:
    result = _built(
        _pds3(
            tmp_path,
            "MAP_PROJECTION_TYPE = EQUIRECTANGULAR\n"
            "CENTER_LATITUDE = 0.0\nCENTER_LONGITUDE = 0.0",
        )
    )
    crs = _crs(result)
    assert crs.ellipsoid is not None
    assert crs.ellipsoid.semi_major_metre == pytest.approx(3396190)
    assert crs.ellipsoid.inverse_flattening == 0
    assert any("IAU 2015 Mars sphere" in line for line in result.assumed)
    assert not result.complete


def test_malformed_never_raises(tmp_path: Path) -> None:
    result = _built(
        _pds3(
            tmp_path,
            "MAP_PROJECTION_TYPE = EQUIRECTANGULAR\nA_AXIS_RADIUS = N/A\n"
            "CENTER_LATITUDE = 0.0\nCENTER_LONGITUDE = UNK",
            target="UNKNOWN_ROCK",
        )
    )
    assert result.wkt is None
    assert any("radi" in line for line in result.missing)
    assert any("CENTER_LONGITUDE" in line for line in result.missing)
    assert labelCrs(LabelHints.empty()) is None


# --- Surfaces, keys and units ---


def test_planetocentric_polar_ellipsoid_is_the_polar_sphere() -> None:
    result = _built(
        _isis(
            {
                "PROJECTIONNAME": "PolarStereographic",
                "EQUATORIALRADIUS": "3396190",
                "POLARRADIUS": "3376200",
                "CENTERLATITUDE": "-90",
                "CENTERLONGITUDE": "0",
            }
        )
    )
    crs = _crs(result)
    assert crs.ellipsoid is not None
    assert crs.ellipsoid.semi_major_metre == pytest.approx(3376200)
    assert _parameter(crs, "Latitude of natural origin") == -90
    assert result.assumed and not result.complete


def test_polar_true_scale_latitude_is_variant_b() -> None:
    result = _built(
        _isis(
            {
                "PROJECTIONNAME": "PolarStereographic",
                "EQUATORIALRADIUS": "1737400",
                "CENTERLATITUDE": "-71",
                "CENTERLONGITUDE": "0",
            },
            target="Moon",
        )
    )
    crs = _crs(result)
    assert _parameter(crs, "Latitude of standard parallel") == -71
    assert result.complete


def test_planetocentric_other_projection_is_the_equatorial_sphere() -> None:
    result = _built(
        _isis(
            {
                "PROJECTIONNAME": "Sinusoidal",
                "EQUATORIALRADIUS": "3396190",
                "POLARRADIUS": "3376200",
                "CENTERLONGITUDE": "30",
            }
        )
    )
    crs = _crs(result)
    assert crs.ellipsoid is not None
    assert crs.ellipsoid.semi_major_metre == pytest.approx(3396190)
    assert crs.ellipsoid.inverse_flattening == 0
    assert result.assumed


def test_triaxial_label_is_assumed(tmp_path: Path) -> None:
    result = _built(
        _pds3(
            tmp_path,
            "MAP_PROJECTION_TYPE = SINUSOIDAL\nA_AXIS_RADIUS = 13.0\n"
            "B_AXIS_RADIUS = 11.4\nC_AXIS_RADIUS = 13.0\nCENTER_LONGITUDE = 0",
            target="PHOBOS",
        )
    )
    assert result.wkt is not None
    assert any("B_AXIS_RADIUS" in line for line in result.assumed)


def test_meters_per_pixel_map_scale_is_honored(tmp_path: Path) -> None:
    # HiRISE writes MAP_SCALE in meters per pixel
    result = _built(
        _pds3(
            tmp_path,
            "MAP_PROJECTION_TYPE = EQUIRECTANGULAR\nA_AXIS_RADIUS = 3396.19 <KM>\n"
            "CENTER_LATITUDE = 0\nCENTER_LONGITUDE = 0\n"
            "MAP_SCALE = 0.25 <METERS/PIXEL>\n"
            "LINE_PROJECTION_OFFSET = 9.5\nSAMPLE_PROJECTION_OFFSET = -0.5",
        )
    )
    assert result.transform == pytest.approx((0.25, 0, 0, 0, -0.25, 2.5))


@pytest.mark.parametrize(
    ("unit", "meters"), [("km", 3396190.0), ("m", 3396.19), (None, 3396.19)]
)
def test_pds4_radius_units(unit: str | None, meters: float) -> None:
    group = {
        "MAP_PROJECTION_NAME": "Equirectangular",
        "STANDARD_PARALLEL_1": "0",
        "LONGITUDE_OF_CENTRAL_MERIDIAN": "180",
        "A_AXIS_RADIUS": "3396.19",
        "C_AXIS_RADIUS": "3396.19",
    }
    if unit:
        group |= {"A_AXIS_RADIUS_UNIT": unit, "C_AXIS_RADIUS_UNIT": unit}
    crs = _crs(_built(LabelHints("Mars", None, "east", None, group, "PDS4 label")))
    assert crs.ellipsoid is not None
    assert crs.ellipsoid.semi_major_metre == pytest.approx(meters)


def test_pds4_cassis_like_group() -> None:
    # The cart: keys of a real CaSSIS DERSC label (TGO), with their units
    group = {
        "MAP_PROJECTION_NAME": "Equirectangular",
        "LATITUDE_OF_PROJECTION_ORIGIN": "0.0",
        "STANDARD_PARALLEL_1": "15.0",
        "LONGITUDE_OF_CENTRAL_MERIDIAN": "180.0",
        "PIXEL_RESOLUTION_X": "4.0",
        "PIXEL_RESOLUTION_X_UNIT": "m/pixel",
        "PIXEL_RESOLUTION_Y": "4.0",
        "PIXEL_RESOLUTION_Y_UNIT": "m/pixel",
        "UPPERLEFT_CORNER_X": "-6166632.0",
        "UPPERLEFT_CORNER_X_UNIT": "m",
        "UPPERLEFT_CORNER_Y": "1035440.0",
        "UPPERLEFT_CORNER_Y_UNIT": "m",
        "A_AXIS_RADIUS": "3394839.813",
        "A_AXIS_RADIUS_UNIT": "m",
        "C_AXIS_RADIUS": "3394839.813",
        "C_AXIS_RADIUS_UNIT": "m",
    }
    result = _built(
        LabelHints("Mars", "planetocentric", "east", None, group, "PDS4 label")
    )
    crs = _crs(result)
    assert _parameter(crs, "Latitude of 1st standard parallel") == 15
    assert _parameter(crs, "Longitude of natural origin") == 180
    assert result.transform == (4.0, 0, -6166632.0, 0, -4.0, 1035440.0)
    assert result.complete


# --- West-positive labels: map x increases eastward ---
#
# Varda assumes a west-positive label's map x (PDS3 sample, ISIS x) increases
# eastward, as it does in an east-positive label. These tests check that
# reading against the PDS3 equirectangular relation written for west
# longitudes, against GDAL's transform for the same label, and against ISIS's
# PositiveWest convention; and that the other reading (x increasing westward,
# toward larger west longitudes) is not what Varda builds.

R_KM = 3396.19


def _westLongitudes(result: LabelCrs, samples: np.ndarray) -> np.ndarray:
    """West longitudes of the centers of these 1-based samples on line 1."""
    assert result.transform is not None
    a, _, c, _, e, f = result.transform
    xs = c + (samples - 0.5) * a
    ys = np.full_like(xs, f + 0.5 * e)
    crs = _crs(result)
    assert crs.geodetic_crs is not None
    lon, _ = transformPoints(crs, crs.geodetic_crs, xs, ys)
    return lon


def test_west_label_map_x_increases_eastward(tmp_path: Path) -> None:
    label = _gdalLabel(tmp_path, "WEST")
    result = _built(labelHints(str(label)))
    samples = np.arange(1.0, 11.0)
    westLon = _westLongitudes(result, samples)
    # PDS3 equirectangular for a west-positive label: x = (sample −
    # SAMPLE_PROJECTION_OFFSET − 1)·MAP_SCALE eastward, so the west longitude
    # falls by x / (R cos φ1) from CENTER_LONGITUDE
    x = (samples - (-20.5) - 1) * 0.5
    eastward = 100 - np.degrees(x / R_KM)
    westward = 100 + np.degrees(x / R_KM)
    assert westLon == pytest.approx(eastward, abs=1e-9)
    assert not np.allclose(westLon, westward)
    assert np.all(np.diff(westLon) < 0)  # sample 1 is the western edge
    with rasterio.open(label) as src:
        assert src.transform.a > 0  # GDAL reads the samples eastward too


def test_isis_positive_west_x_increases_eastward() -> None:
    # ISIS Equirectangular: x = R·(−(lonW − CenterLongitude))·cos φ1 for
    # PositiveWest, so x grows as the west longitude falls
    result = _built(
        _isis(
            {
                "PROJECTIONNAME": "Equirectangular",
                "EQUATORIALRADIUS": "3396190",
                "CENTERLATITUDE": "0",
                "CENTERLONGITUDE": "100",
                "LONGITUDEDIRECTION": "PositiveWest",
                "PIXELRESOLUTION": "1000",
                "UPPERLEFTCORNERX": "0",
                "UPPERLEFTCORNERY": "0",
            }
        )
    )
    westLon = _westLongitudes(result, np.array([1.0, 1001.0]))
    expected = 100 - np.degrees(np.array([500.0, 1_000_500.0]) / 3_396_190)
    assert westLon == pytest.approx(expected, abs=1e-9)
    assert result.complete


def test_west_center_longitude_normalizes_to_east() -> None:
    result = _built(
        _isis(
            {
                "PROJECTIONNAME": "SimpleCylindrical",
                "EQUATORIALRADIUS": "3396190",
                "CENTERLONGITUDE": "180",
                "LONGITUDEDIRECTION": "PositiveWest",
            }
        )
    )
    crs = _crs(result)
    assert _parameter(crs, "Longitude of natural origin") == 180
    assert math.isclose(_parameter(crs, "Latitude of 1st standard parallel"), 0)
