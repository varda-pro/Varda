"""The CRS health check: one test per issue, the single automatic fix, and
the real-style fixtures (spec §4.2, §6)."""

from __future__ import annotations

import math

import pytest
from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_ENVI_WKT,
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    LRO_WAC_SOUTH_POLAR_PROJ,
    MARS_EQC_PDS3_WKT,
    MOLA_MEGDR_PROJ,
    VESTA_IAU,
    WGS84,
    makeImage,
)
from varda.geo.equivalence import sameCrs
from varda.geo.health import CrsHealth, Severity, checkCrs, fixRecord
from varda.geo.labels import LabelHints

GALE = (137.4, -4.6)
MARS = LabelHints("Mars", None, None, None, {}, "PDS3 label x.lbl")
DISTORTION_SUGGESTION = (
    "Measurements are geodesic and unaffected; shapes and slopes on this map "
    "are distorted — reproject (Processing → Reproject Image, coming in a "
    "later version) for figures and terrain"
)


def check(image: VardaRaster, hints: LabelHints = LabelHints.empty()) -> CrsHealth:
    return checkCrs(image.crs, image.transform, image.width, image.height, hints)


def codes(health: CrsHealth) -> list[str]:
    return [issue.code for issue in health.issues]


def hints(**facts: str) -> LabelHints:
    return LabelHints(
        facts.get("target"),
        facts.get("latitudeType"),
        facts.get("longitudeDirection"),
        facts.get("longitudeDomain"),
        {},
        "PDS3 label x.lbl",
    )


def test_severities():
    assert [s.value for s in Severity] == ["info", "tip", "problem"]


# --- one test per issue ---


def test_map_coordinates_without_a_crs():
    health = checkCrs(None, Affine(10, 0, 1000, 0, -10, 2000), 64, 64)
    assert codes(health) == ["no-crs"]
    issue = health.issues[0]
    assert issue.severity is Severity.PROBLEM
    assert issue.message == (
        "Its map coordinates have no coordinate system, so Varda treats it as "
        "unreferenced."
    )
    assert issue.fix is None
    assert health.description.kind == "none"
    assert health.distortion is None


def test_a_label_projection_without_a_crs():
    group = LabelHints(None, None, None, None, {"MAP_PROJECTION_TYPE": "X"}, "y")
    health = checkCrs(None, Affine.identity(), 64, 64, group)
    assert codes(health) == ["no-crs"]
    assert health.issues[0].severity is Severity.PROBLEM


def test_a_plain_unreferenced_image_has_no_issues():
    assert checkCrs(None, Affine.identity(), 64, 64).issues == ()


def test_an_unreadable_crs_kept_as_text():
    health = checkCrs(
        None, Affine(10, 0, 0, 0, -10, 0), 64, 64, rawCrsText='PROJCS["broken'
    )
    assert codes(health) == ["unreadable-crs"]
    assert health.issues[0].severity is Severity.PROBLEM


def test_garbage_is_unreadable():
    health = checkCrs("garbage", Affine(10, 0, 0, 0, -10, 0), 64, 64)
    assert codes(health) == ["unreadable-crs"]
    assert health.description.kind == "unreadable"


def test_earth_degrees_on_mars_are_fixed_automatically():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image, MARS)
    assert codes(health) == ["earth-crs-on-body"]
    (issue,) = health.issues
    assert issue.severity is Severity.PROBLEM
    assert issue.message == "Its CRS says Earth, but its label says Mars."
    assert issue.fix is not None
    assert issue.fix.automatic
    assert issue.fix.reason == (
        "Its CRS says Earth (WGS 84) but its label's target is Mars; read as "
        "degrees on the IAU 2015 Mars sphere"
    )
    assert sameCrs(CRS(issue.fix.wkt), "IAU_2015:49900")


def test_earth_degrees_without_a_label_are_left_alone():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image)
    assert health.issues == ()
    assert health.body.body is not None and health.body.body.name == "Earth"


def test_an_earth_projection_on_mars_is_flagged_not_fixed():
    image = makeImage("EPSG:4087", centerLonLat=GALE, pixelSize=100)
    health = check(image, MARS)
    assert codes(health) == ["earth-crs-on-body"]
    issue = health.issues[0]
    assert issue.severity is Severity.PROBLEM
    assert issue.fix is None
    assert "Set coordinate system…" in issue.suggestion


def test_an_earth_crs_with_an_earth_label_is_fine():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    assert check(image, hints(target="Earth")).issues == ()


@pytest.mark.parametrize("target", ["BENNU", "SKY", "N/A", "CALIBRATION"])
def test_a_target_varda_does_not_know_is_ignored(target: str):
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image, hints(target=target))
    assert health.issues == ()
    assert health.body.body is not None and health.body.body.name == "Earth"


def test_the_message_names_the_body_properly():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image, hints(target="MARS"))
    assert health.issues[0].message == "Its CRS says Earth, but its label says Mars."
    fix = health.issues[0].fix
    assert fix is not None and "its label's target is Mars;" in fix.reason


def test_the_reason_names_a_proj_string_crs_by_its_datum():
    crs = "+proj=longlat +datum=WGS84 +no_defs"
    image = makeImage(crs, centerLonLat=GALE, pixelSize=1 / 256)
    fix = check(image, MARS).issues[0].fix
    assert fix is not None
    assert fix.reason.startswith("Its CRS says Earth (World Geodetic System 1984) but")
    assert "unknown" not in fix.reason


def test_after_the_automatic_fix_the_body_is_the_labels():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image, MARS)
    assert health.body.body is not None and health.body.body.name == "Mars"
    # the description stays the CRS as given
    assert health.description.body == "Earth"


def test_without_the_fix_the_body_is_the_crss():
    image = makeImage("EPSG:4087", centerLonLat=GALE, pixelSize=100)
    health = check(image, MARS)
    assert health.body.body is not None and health.body.body.name == "Earth"


def test_fix_record():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image, MARS)
    record = fixRecord(health, WGS84)
    assert record is not None
    assert record["from"] == "EPSG:4326" and record["to"] == "IAU_2015:49900"
    assert record["automatic"] is True
    proj = "+proj=longlat +ellps=WGS84 +pm=10 +no_defs"
    unnamed = makeImage(proj, centerLonLat=GALE, pixelSize=1 / 256)
    other = fixRecord(check(unnamed, MARS), proj)
    assert other is not None and str(other["from"]).startswith("GEOGCRS[")
    fix = health.issues[0].fix
    assert fix is not None and record["reason"] == fix.reason
    assert fixRecord(check(image), WGS84) is None


def test_unknown_body():
    image = makeImage(
        "+proj=longlat +R=100000 +no_defs", centerLonLat=(10, 0), pixelSize=0.01
    )
    health = check(image)
    assert codes(health) == ["unknown-body"]
    assert health.issues[0].severity is Severity.TIP
    assert "100 km" in health.issues[0].message
    assert health.body.body is None


def test_a_label_settles_an_unknown_body():
    image = makeImage(
        "+proj=longlat +R=100000 +no_defs", centerLonLat=(10, 0), pixelSize=0.01
    )
    health = check(image, hints(target="Phobos"))
    assert "unknown-body" not in codes(health)


def test_ambiguous_body():
    image = makeImage(
        "+proj=longlat +R=2425000 +no_defs", centerLonLat=(10, 0), pixelSize=0.01
    )
    health = check(image)
    assert codes(health) == ["ambiguous-body"]
    assert health.issues[0].severity is Severity.TIP
    assert "Mercury, Callisto" in health.issues[0].message


def test_vesta_is_vesta():
    image = makeImage(VESTA_IAU, centerLonLat=(10, 0), pixelSize=0.01)
    health = check(image)
    assert health.body.body is not None and health.body.body.name == "Vesta"
    assert "unknown-body" not in codes(health)


def test_longitude_direction_mismatch():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    health = check(image, hints(target="Mars", longitudeDirection="west"))
    assert codes(health) == ["lon-direction-mismatch"]
    assert health.issues[0].severity is Severity.PROBLEM


def test_planetographic_label_on_a_sphere():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    health = check(image, hints(latitudeType="planetographic"))
    assert codes(health) == ["latitude-type-mismatch"]
    assert health.issues[0].severity is Severity.TIP


def test_planetocentric_label_on_a_flattened_crs():
    crs = "+proj=eqc +lat_ts=0 +a=3396190 +b=3376200 +units=m +no_defs"
    image = makeImage(crs, centerLonLat=GALE, pixelSize=100)
    health = check(image, hints(latitudeType="planetocentric"))
    assert codes(health) == ["latitude-type-mismatch"]


def test_matching_label_conventions_are_fine():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    label = hints(
        target="Mars", latitudeType="planetocentric", longitudeDirection="east"
    )
    assert check(image, label).issues == ()


def test_equirectangular_at_60_north_is_strongly_distorted():
    crs = "+proj=eqc +lat_ts=0 +R=3396190 +units=m +no_defs"
    image = makeImage(crs, centerLonLat=(30, 60), pixelSize=100)
    health = check(image)
    assert codes(health) == ["high-distortion"]
    issue = health.issues[0]
    assert issue.severity is Severity.TIP
    assert issue.suggestion == DISTORTION_SUGGESTION
    assert health.distortion is not None and health.distortion.worst.level == "strong"
    assert health.distortion.text in issue.message
    assert "badly distorted" in issue.message


def test_longitudes_0_to_360():
    image = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(200, 0), pixelSize=1 / 128)
    health = check(image)
    assert codes(health) == ["lon-0-360"]
    assert health.issues[0].severity is Severity.INFO
    assert health.description.longitudeDomain == "0..360"


# --- the real-style fixtures ---


def test_ctx_dem_has_no_issues():
    image = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20)
    health = check(image)
    assert health.issues == ()
    assert health.body.body is not None and health.body.body.name == "Mars"
    assert health.distortion is not None


def test_crism_mtrdr_is_only_distorted():
    image = makeImage(CRISM_MTRDR_WKT, centerLonLat=(77.5, 18.4), pixelSize=18)
    health = check(image, hints(target="Mars", latitudeType="planetocentric"))
    assert codes(health) == ["high-distortion"]
    assert health.issues[0].message == (
        "Its projection distorts the map: up to 1.8% scale error (east–west) "
        "at 18.4°N, 1.0° angular distortion."
    )


ALL_FIXTURES = [
    CRISM_MTRDR_WKT,
    CRISM_MTRDR_ENVI_WKT,
    MARS_EQC_PDS3_WKT,
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    LRO_WAC_SOUTH_POLAR_PROJ,
    WGS84,
    MOLA_MEGDR_PROJ,
    VESTA_IAU,
    "garbage",
    'PROJCS["broken',
    "",
    None,
]


@pytest.mark.parametrize("crs", ALL_FIXTURES)
@pytest.mark.parametrize(
    "transform",
    [
        Affine.identity(),
        Affine(20, 0, 0, 0, -20, 0),
        Affine(0, 0, 0, 0, 0, 0),
        Affine(math.nan, 0, math.nan, 0, math.nan, math.nan),
    ],
)
def test_never_raises(crs: str | None, transform: Affine):
    for label in (LabelHints.empty(), MARS, hints(longitudeDirection="west")):
        health = checkCrs(crs, transform, 64, 64, label, rawCrsText="x")
        assert isinstance(health, CrsHealth)
    assert isinstance(checkCrs(crs, transform, 0, 0), CrsHealth)
