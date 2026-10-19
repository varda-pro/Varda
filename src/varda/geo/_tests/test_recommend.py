"""The projection recommender (spec §4.6): polar stereographic, equirectangular,
transverse Mercator and Lambert azimuthal equal-area, on the right surface."""

from __future__ import annotations

import math
import warnings

from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import (
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    LRO_WAC_SOUTH_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    makeImage,
)
from varda.geo.equivalence import sameCrs
from varda.geo.recommend import (
    Footprint,
    Purpose,
    Recommendation,
    footprintLonLat,
    isGlobal,
    isGoodEnough,
    recommendProjection,
)

MARS_EQC = "IAU_2015:49910"


def _footprint(image: VardaRaster) -> Footprint:
    assert image.crs is not None
    return footprintLonLat(image.crs, image.transform, image.width, image.height)


def _recommend(
    image: VardaRaster, purpose: Purpose, existing: list[str] | tuple[()] = ()
) -> Recommendation:
    assert image.crs is not None
    return recommendProjection(image.crs, _footprint(image), purpose, existing)


def _params(recommendation: Recommendation) -> dict[str, object]:
    with warnings.catch_warnings():
        # "You will likely lose important projection information": only the
        # parameters are read back
        warnings.simplefilter("ignore", UserWarning)
        return CRS.from_wkt(recommendation.wkt).to_dict()


def test_purpose_labels():
    assert [p.value for p in Purpose] == ["Measurement", "Mosaic", "Terrain"]


def test_footprint_of_an_equirectangular_scene():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    footprint = _footprint(image)
    assert footprint.west < 77.5 < footprint.east
    assert footprint.south < 18.4 < footprint.north
    assert abs(footprint.center[0] - 77.5) < 1e-9
    assert abs(footprint.center[1] - 18.4) < 1e-9
    assert footprint.pole is None


def test_mosaic_mid_latitude_is_equirectangular_rounded():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    recommendation = _recommend(image, Purpose.MOSAIC)
    params = _params(recommendation)
    assert params["proj"] == "eqc"
    assert params["lat_ts"] == 18
    assert params["lon_0"] == 78
    assert params["R"] == 3396190
    assert not recommendation.keptExisting


def test_polar_mosaic_true_scale_at_pole():
    image = makeImage(HIRISE_POLAR_PROJ, centerLonLat=(0, 85), pixelSize=1)
    recommendation = _recommend(image, Purpose.MOSAIC)
    params = _params(recommendation)
    assert params["proj"] == "stere"
    assert params["lat_0"] == 90
    assert params["k"] == 1
    assert "85.0°N" in recommendation.reason


def test_polar_terrain_true_scale_at_center():
    image = makeImage(HIRISE_POLAR_PROJ, centerLonLat=(0, 85), pixelSize=1)
    params = _params(_recommend(image, Purpose.TERRAIN))
    assert params["proj"] == "stere"
    assert params["lat_0"] == 90
    assert params["lat_ts"] == 85.0


def test_footprint_contains_south_pole():
    image = makeImage(LRO_WAC_SOUTH_POLAR_PROJ, centerLonLat=(0, -90), pixelSize=100)
    footprint = _footprint(image)
    assert footprint.pole == "south"
    assert footprint.south == -90
    assert (footprint.west, footprint.east) == (-180, 180)
    assert image.crs is not None
    recommendation = recommendProjection(image.crs, footprint, Purpose.MOSAIC)
    params = _params(recommendation)
    assert params["proj"] == "stere"
    assert params["lat_0"] == -90
    assert params["R"] == 1737400
    assert "south pole" in recommendation.reason


def test_existing_good_crs_is_kept():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    footprint = _footprint(image)
    good, _ = isGoodEnough(CTX_DEM_PROJ, footprint)
    assert good
    recommendation = recommendProjection(CTX_DEM_PROJ, footprint, Purpose.TERRAIN)
    assert recommendation.keptExisting
    assert sameCrs(recommendation.wkt, CTX_DEM_PROJ)


def test_distorted_crs_gets_transverse_mercator():
    # 300 km east–west, 2 km north–south, on lat_ts = 0 at 30°N: k = 1/cos 30°
    image = makeImage(
        MARS_EQC, centerLonLat=(0, 30), pixelSize=1000, width=300, height=2
    )
    good, why = isGoodEnough(MARS_EQC, _footprint(image))
    assert not good and "15.5%" in why
    recommendation = _recommend(image, Purpose.TERRAIN)
    params = _params(recommendation)
    assert params["proj"] == "tmerc"
    assert params["k"] == 1
    assert params["lat_0"] == 30
    assert "15.5%" in recommendation.reason
    assert not recommendation.keptExisting


def test_wide_scene_gets_laea():
    # 30° of longitude at the equator, in degrees (never kept for terrain):
    # B = 15° = 0.26 rad, well over transverse Mercator's 0.14 rad
    image = makeImage(
        MOLA_MEGDR_PROJ, centerLonLat=(0, 0), pixelSize=0.25, width=120, height=8
    )
    recommendation = _recommend(image, Purpose.TERRAIN)
    assert _params(recommendation)["proj"] == "laea"
    assert "°" in recommendation.reason


def test_transverse_mercator_half_width_is_checked_on_the_equator():
    # just over 0.14 rad either side of the center, but only within 0.4° of
    # the equator: the 21 grid latitudes (about 1° apart, centered on 0.5°N)
    # miss φ = 0, where B is largest
    image = makeImage(
        MOLA_MEGDR_PROJ,
        centerLonLat=(0, 0.5),
        pixelSize=math.degrees(0.280002) / 100,
        width=100,
        height=125,
    )
    assert _params(_recommend(image, Purpose.TERRAIN))["proj"] == "laea"


def test_global_scene_is_equirectangular_not_polar():
    image = makeImage(
        MOLA_MEGDR_PROJ, centerLonLat=(180, 0), pixelSize=1, width=360, height=180
    )
    footprint = _footprint(image)
    assert footprint.pole == "both"
    assert isGlobal(footprint)
    assert image.crs is not None
    mosaic = recommendProjection(image.crs, footprint, Purpose.MOSAIC)
    params = _params(mosaic)
    assert params["proj"] == "eqc"
    assert params["lat_ts"] == 0
    assert "global" in mosaic.reason
    terrain = recommendProjection(image.crs, footprint, Purpose.TERRAIN)
    assert _params(terrain)["proj"] == "eqc"
    assert "no single projection" in terrain.reason
    assert "geodesic" in terrain.reason


def test_global_scene_keeps_its_own_equirectangular():
    image = makeImage(
        MARS_EQC, centerLonLat=(0, 0), pixelSize=100_000, width=213, height=106
    )
    recommendation = _recommend(image, Purpose.MEASUREMENT)
    assert recommendation.keptExisting
    assert sameCrs(recommendation.wkt, MARS_EQC)


def test_inputs_on_another_unknown_body_are_not_kept():
    reference = "+proj=eqc +R=3000000 +units=m +no_defs"
    inputs = "+proj=eqc +lat_ts=10 +R=2000000 +units=m +no_defs"
    image = makeImage(reference, centerLonLat=(10, 10), pixelSize=100)
    recommendation = _recommend(image, Purpose.MOSAIC, existing=[inputs, inputs])
    assert not recommendation.keptExisting
    assert _params(recommendation)["R"] == 3000000


def test_geographic_never_kept_for_terrain():
    image = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(137.4, -4.6), pixelSize=1 / 128)
    recommendation = _recommend(image, Purpose.TERRAIN)
    assert not recommendation.keptExisting
    assert _params(recommendation)["proj"] == "tmerc"


def test_unknown_body_uses_its_own_sphere():
    crs = "+proj=eqc +R=3000000 +units=m +no_defs"
    image = makeImage(crs, centerLonLat=(10, 10), pixelSize=100)
    params = _params(_recommend(image, Purpose.MOSAIC))
    assert params["R"] == 3000000
    assert params["proj"] == "eqc"


def test_all_inputs_on_ellipsoid_keep_ellipsoid():
    image = makeImage(MARS_EQC, centerLonLat=(20, 40), pixelSize=100)
    recommendation = _recommend(image, Purpose.MOSAIC, existing=["IAU_2015:49912"] * 2)
    params = _params(recommendation)
    assert params["proj"] == "eqc"
    ellipsoid = CRS.from_wkt(recommendation.wkt).ellipsoid
    assert ellipsoid is not None
    assert (ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre) == (
        3396190,
        3376200,
    )
    assert not recommendation.keptExisting


def test_inputs_in_one_good_crs_are_kept_for_mosaic():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    recommendation = _recommend(
        image, Purpose.MOSAIC, existing=[CTX_DEM_PROJ, CTX_DEM_PROJ]
    )
    assert recommendation.keptExisting
    assert sameCrs(recommendation.wkt, CTX_DEM_PROJ)


def test_rounds_half_away_from_zero():
    for lon, expected in ((77.5, 78), (-77.5, -78)):
        image = makeImage(CTX_DEM_PROJ, centerLonLat=(lon, 18.4), pixelSize=20)
        assert _params(_recommend(image, Purpose.MOSAIC))["lon_0"] == expected


def test_unknown_oblate_body_converts_latitudes_to_planetographic():
    # an unknown ellipsoid: PROJ reads lat_ts as geodetic, so the scene's
    # planetocentric center (about 40.07°) is converted back to about 42°
    crs = "+proj=eqc +a=3000000 +b=2900000 +units=m +no_defs"
    image = makeImage(crs, centerLonLat=(10, 42), pixelSize=100)
    assert abs(_footprint(image).center[1] - 42) > 1
    recommendation = _recommend(image, Purpose.MOSAIC)
    ellipsoid = CRS.from_wkt(recommendation.wkt).ellipsoid
    assert ellipsoid is not None
    assert (ellipsoid.semi_major_metre, ellipsoid.semi_minor_metre) == (
        3000000,
        2900000,
    )
    assert _params(recommendation)["lat_ts"] == 42
