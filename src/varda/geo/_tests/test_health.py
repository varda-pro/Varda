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
from varda.geo.health import (
    CrsHealth,
    Severity,
    checkCrs,
    fixRecord,
    flagged,
    headline,
    recordText,
)
from varda.geo.label_crs import labelCrs
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


@pytest.mark.parametrize(
    "target", ["SKY", "N/A", "CALIBRATION", "cal", "Dark", "NONE", "UNKNOWN"]
)
def test_a_target_that_isnt_a_body_is_ignored(target: str):
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image, hints(target=target))
    assert health.issues == ()
    assert health.body.body is not None and health.body.body.name == "Earth"


def test_an_earth_crs_on_a_body_varda_does_not_know_is_flagged_not_fixed():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = check(image, hints(target="BENNU"))
    assert codes(health) == ["earth-crs-on-body"]
    (issue,) = health.issues
    assert issue.severity is Severity.PROBLEM
    assert issue.message == "Its CRS says Earth, but its label says Bennu."
    assert issue.suggestion == "Set coordinate system…"
    assert issue.fix is None


def test_a_declined_fix_is_checked_on_the_files_crs():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=1 / 256)
    health = checkCrs(
        image.crs, image.transform, image.width, image.height, MARS, applyFix=False
    )
    (issue,) = health.issues
    assert issue.code == "earth-crs-on-body" and issue.severity is Severity.PROBLEM
    assert issue.message == (
        "Its CRS says Earth, but its label says Mars; you chose to keep the file's CRS."
    )
    assert issue.suggestion == (
        "Apply to read it as degrees on the IAU 2015 Mars sphere, or "
        "Set coordinate system…"
    )
    assert issue.fix is not None and not issue.fix.automatic  # offered again
    assert health.body.body is not None and health.body.body.name == "Earth"
    assert fixRecord(health, WGS84) is None


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


def test_the_headline_of_an_automatic_fix_says_what_varda_read():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=0.01)
    health = check(image, MARS)
    assert headline(health) == "Its CRS says Earth; read as Mars (from its label)."
    assert flagged(health)


def test_the_headline_is_the_first_problem_then_the_first_tip():
    image = makeImage(WGS84, centerLonLat=GALE, pixelSize=0.01)
    declined = checkCrs(
        image.crs, image.transform, image.width, image.height, MARS, applyFix=False
    )
    assert headline(declined) == declined.issues[0].message
    unknown = makeImage(
        "+proj=longlat +R=100000 +no_defs", centerLonLat=(0, 0), pixelSize=0.01
    )
    health = check(unknown)
    assert codes(health) == ["unknown-body"] and flagged(health)
    assert headline(health) == health.issues[0].message


def test_info_alone_is_no_headline():
    image = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(200, 0), pixelSize=1 / 128)
    health = check(image)
    assert [i.severity for i in health.issues] == [Severity.INFO]
    assert headline(health) is None and not flagged(health)
    assert headline(None) is None and not flagged(None)


# --- CRSs built from the label (spec §4.8) ---

HIRISE_GROUP = {
    "MAP_PROJECTION_TYPE": "POLAR STEREOGRAPHIC",
    "A_AXIS_RADIUS": "3376.2",
    "C_AXIS_RADIUS": "3376.2",
    "COORDINATE_SYSTEM_NAME": "PLANETOCENTRIC",
    "POSITIVE_LONGITUDE_DIRECTION": "EAST",
    "CENTER_LATITUDE": "90.0",
    "CENTER_LONGITUDE": "0.0",
    "MAP_SCALE": "0.001",
    "LINE_PROJECTION_OFFSET": "-120000.5",
    "SAMPLE_PROJECTION_OFFSET": "40000.5",
}
ISIS_LOCAL_RADIUS_GROUP = {
    "PROJECTIONNAME": "Equirectangular",
    "EQUATORIALRADIUS": "3396190.0",
    "POLARRADIUS": "3376200.0",
    "LATITUDETYPE": "Planetocentric",
    "LONGITUDEDIRECTION": "PositiveEast",
    "CENTERLATITUDE": "15.0",
    "CENTERLONGITUDE": "0.0",
    "PIXELRESOLUTION": "18.0",
    "UPPERLEFTCORNERX": "4106872.1",
    "UPPERLEFTCORNERY": "1019091.5",
}
# The spec's GDAL-misread label (§2): WEST, planetographic, a ≠ c
WEST_GROUP = {
    "MAP_PROJECTION_TYPE": "EQUIRECTANGULAR",
    "A_AXIS_RADIUS": "3396.19",
    "C_AXIS_RADIUS": "3376.20",
    "COORDINATE_SYSTEM_NAME": "PLANETOGRAPHIC",
    "POSITIVE_LONGITUDE_DIRECTION": "WEST",
    "CENTER_LATITUDE": "10.0",
    "CENTER_LONGITUDE": "100.0",
    "MAP_SCALE": "0.5",
    "LINE_PROJECTION_OFFSET": "100.5",
    "SAMPLE_PROJECTION_OFFSET": "-20.5",
}
# What GDAL 3.9.3 builds from WEST_GROUP: east, a sphere of A
GDAL_WEST_PROJ = "+proj=eqc +lat_ts=10 +lon_0=100 +R=3396190 +units=m +no_defs"
GDAL_WEST_TRANSFORM = Affine(500, 0, 10_000, 0, -500, 50_500)


def labelled(
    group: dict[str, str], source: str = "PDS3 label x.lbl", **facts: str
) -> LabelHints:
    return LabelHints(
        facts.get("target", "Mars"),
        facts.get("latitudeType"),
        facts.get("longitudeDirection"),
        None,
        group,
        source,
    )


def test_a_complete_label_group_is_used_automatically():
    label = labelled(HIRISE_GROUP, latitudeType="planetocentric")
    health = checkCrs(None, Affine.identity(), 64, 64, label)
    assert codes(health) == ["no-crs"]
    (issue,) = health.issues
    assert issue.severity is Severity.PROBLEM
    assert issue.message == (
        "Its file has no coordinate system; Varda built it from its label's map "
        "projection."
    )
    fix = issue.fix
    assert fix is not None and fix.automatic
    assert sameCrs(fix.wkt, HIRISE_POLAR_PROJ)
    built = labelCrs(label)
    assert built is not None and built.transform is not None
    assert fix.transform == pytest.approx(built.transform)
    assert fix.transform == pytest.approx((1, 0, -40001, 0, -1, -120000))
    assert fix.reason == (
        "Its file has no coordinate system; built from its PDS3 label x.lbl map "
        "projection group (POLAR STEREOGRAPHIC, sphere R = 3376.20 km)"
    )
    assert headline(health) == (
        "No coordinate system in its file; built from its label."
    )
    # the checks are of the CRS Varda uses: a Mars polar map has no problem
    assert health.body.body is not None and health.body.body.name == "Mars"
    record = fixRecord(health, None)
    assert record is not None
    assert record["from"] == "none" and record["source"] == "label"
    assert record["transform"] == pytest.approx(list(built.transform))
    assert record["automatic"] is True and record["reason"] == fix.reason


def test_a_file_transform_is_kept_with_the_label_crs():
    label = labelled(HIRISE_GROUP, latitudeType="planetocentric")
    health = checkCrs(None, Affine(1, 0, 0, 0, -1, 0), 64, 64, label)
    fix = health.issues[0].fix
    assert fix is not None and fix.automatic and fix.transform is None
    record = fixRecord(health, None)
    assert record is not None and "transform" not in record


def test_a_declined_label_crs_is_offered():
    label = labelled(HIRISE_GROUP, latitudeType="planetocentric")
    health = checkCrs(None, Affine.identity(), 64, 64, label, applyFix=False)
    (issue,) = health.issues
    assert issue.fix is not None and not issue.fix.automatic
    assert fixRecord(health, None) is None


def test_a_label_crs_with_an_assumption_is_offered():
    label = labelled(
        ISIS_LOCAL_RADIUS_GROUP, "ISIS cube label", latitudeType="planetocentric"
    )
    health = checkCrs(None, Affine.identity(), 64, 64, label)
    assert codes(health) == ["no-crs"]
    (issue,) = health.issues
    assert issue.severity is Severity.PROBLEM
    assert issue.fix is not None and issue.fix.automatic is False
    assert sameCrs(issue.fix.wkt, CRISM_MTRDR_WKT)
    assert issue.fix.transform == pytest.approx((18, 0, 4106872.1, 0, -18, 1019091.5))
    assert "local radius" in issue.suggestion
    assert issue.suggestion.startswith("Apply to use the CRS built from its label")
    assert fixRecord(health, None) is None


def test_a_label_crs_without_a_pixel_grid_is_offered():
    group = {
        k: v
        for k, v in HIRISE_GROUP.items()
        if k not in ("LINE_PROJECTION_OFFSET", "SAMPLE_PROJECTION_OFFSET")
    }
    health = checkCrs(None, Affine.identity(), 64, 64, labelled(group))
    fix = health.issues[0].fix
    assert fix is not None and not fix.automatic and fix.transform is None
    assert "LINE_PROJECTION_OFFSET" in health.issues[0].suggestion


def test_a_label_projection_varda_cannot_build():
    group = {"MAP_PROJECTION_TYPE": "OBLIQUE CYLINDRICAL", "A_AXIS_RADIUS": "3396.19"}
    health = checkCrs(None, Affine.identity(), 64, 64, labelled(group))
    assert codes(health) == ["no-crs"]
    (issue,) = health.issues
    assert issue.fix is None
    assert "OBLIQUE CYLINDRICAL" in issue.suggestion
    assert issue.suggestion.endswith("Set coordinate system…")


def test_gdal_misread_label_is_a_mismatch():
    label = labelled(
        WEST_GROUP, latitudeType="planetographic", longitudeDirection="west"
    )
    health = checkCrs(GDAL_WEST_PROJ, GDAL_WEST_TRANSFORM, 64, 64, label)
    assert "label-crs-mismatch" in codes(health)
    assert "lon-direction-mismatch" in codes(health)
    issue = next(i for i in health.issues if i.code == "label-crs-mismatch")
    assert issue.severity is Severity.PROBLEM
    assert issue.message.startswith("Its label describes Mars, ")
    assert ", but its CRS is Mars, sphere R = 3396.19 km" in issue.message
    assert issue.fix is not None and issue.fix.automatic is False
    assert issue.fix.transform is None  # GDAL's transform is kept
    crs = CRS.from_user_input(issue.fix.wkt)
    assert crs.geodetic_crs is not None
    assert any(a.direction == "west" for a in crs.geodetic_crs.axis_info)
    assert fixRecord(health, GDAL_WEST_PROJ) is None


def test_a_matching_label_is_no_mismatch():
    label = labelled(
        ISIS_LOCAL_RADIUS_GROUP, "ISIS cube label", latitudeType="planetocentric"
    )
    image = makeImage(CRISM_MTRDR_WKT, centerLonLat=(77.5, 18.4), pixelSize=18)
    assert "label-crs-mismatch" not in codes(check(image, label))


def test_provenance_hints_never_raise_a_label_issue():
    source = "Varda provenance (inputs' labels)"
    label = labelled(
        WEST_GROUP, source, latitudeType="planetographic", longitudeDirection="west"
    )
    health = checkCrs(GDAL_WEST_PROJ, GDAL_WEST_TRANSFORM, 64, 64, label)
    assert "label-crs-mismatch" not in codes(health)
    alone = checkCrs(None, Affine.identity(), 64, 64, labelled(HIRISE_GROUP, source))
    assert all(issue.fix is None for issue in alone.issues)


def test_a_malformed_label_group_never_raises():
    group = {"MAP_PROJECTION_TYPE": "EQUIRECTANGULAR", "A_AXIS_RADIUS": "N/A"}
    for target in (None, "Mars", "Bennu"):
        label = LabelHints(target, None, None, None, group, "PDS3 label x.lbl")
        for crs in (None, CTX_DEM_PROJ, "garbage"):
            for transform in (Affine.identity(), Affine(20, 0, 0, 0, -20, 0)):
                health = checkCrs(crs, transform, 64, 64, label)
                assert isinstance(health, CrsHealth)


def test_a_west_label_crs_is_recorded_with_its_west_axis():
    # at the equator the local radius is a: the label's ellipsoid, nothing assumed
    group = {**WEST_GROUP, "CENTER_LATITUDE": "0.0"}
    label = labelled(group, latitudeType="planetographic", longitudeDirection="west")
    health = checkCrs(None, Affine.identity(), 64, 64, label)
    fix = health.issues[0].fix
    assert fix is not None and fix.automatic
    record = fixRecord(health, None)
    assert record is not None
    to = CRS.from_user_input(str(record["to"]))
    assert to.geodetic_crs is not None
    assert any(a.direction == "west" for a in to.geodetic_crs.axis_info)
    assert sameCrs(str(record["to"]), fix.wkt)
    assert recordText(CTX_DEM_PROJ).startswith("PROJCRS[")
    assert recordText("IAU_2015:49910") == "IAU_2015:49910"


# --- Does map x increase eastward? (the label's longitude range) ---


def bounded(west: str, east: str, direction: str = "west") -> LabelHints:
    group = {
        **WEST_GROUP,
        "COORDINATE_SYSTEM_NAME": "PLANETOCENTRIC",
        "C_AXIS_RADIUS": "3396.19",
        "CENTER_LATITUDE": "0.0",
        "POSITIVE_LONGITUDE_DIRECTION": direction.upper(),
        "WESTERNMOST_LONGITUDE": west,
        "EASTERNMOST_LONGITUDE": east,
    }
    return labelled(group, latitudeType="planetocentric", longitudeDirection=direction)


# The west label above, as Varda builds it: lon_0 −100 (100°W), a sphere,
# its longitude axis west, map x eastward
WEST_SPHERE = (
    '{"type":"ProjectedCRS","name":"x","base_crs":{"name":"x","datum":{"type":'
    '"GeodeticReferenceFrame","name":"x","ellipsoid":{"name":"x","radius":3396190}},'
    '"coordinate_system":{"subtype":"ellipsoidal","axis":[{"name":"Longitude",'
    '"abbreviation":"lon","direction":"west","unit":"degree"},{"name":"Latitude",'
    '"abbreviation":"lat","direction":"north","unit":"degree"}]}},"conversion":'
    '{"name":"x","method":{"name":"Equidistant Cylindrical","id":{"authority":'
    '"EPSG","code":1028}},"parameters":[{"name":"Latitude of 1st standard '
    'parallel","value":0,"unit":"degree"},{"name":"Longitude of natural origin",'
    '"value":-100,"unit":"degree"},{"name":"False easting","value":0,"unit":"metre"},'
    '{"name":"False northing","value":0,"unit":"metre"}]},"coordinate_system":'
    '{"subtype":"Cartesian","axis":[{"name":"Easting","abbreviation":"E",'
    '"direction":"east","unit":"metre"},{"name":"Northing","abbreviation":"N",'
    '"direction":"north","unit":"metre"}]}}'
)


def test_label_bounds_that_agree_with_eastward_x_are_fine():
    # x 10 to 42 km east of 100°W: 99.831°W to 99.292°W
    label = bounded("99.831", "99.292")
    health = checkCrs(WEST_SPHERE, GDAL_WEST_TRANSFORM, 64, 64, label)
    assert "lon-bounds-westward" not in codes(health)


def test_label_bounds_that_fit_westward_x_are_flagged():
    label = bounded("100.708", "100.169")
    health = checkCrs(WEST_SPHERE, GDAL_WEST_TRANSFORM, 64, 64, label)
    issue = next(i for i in health.issues if i.code == "lon-bounds-westward")
    assert issue.severity is Severity.PROBLEM
    assert "westward" in issue.message
    assert issue.fix is None


def test_label_bounds_that_fit_neither_are_left_alone():
    label = bounded("120", "110")
    health = checkCrs(WEST_SPHERE, GDAL_WEST_TRANSFORM, 64, 64, label)
    assert "lon-bounds-westward" not in codes(health)


def test_the_bounds_check_runs_on_a_label_crs_varda_applied():
    # the west label with no CRS from GDAL: Varda's CRS and the label's grid
    group = {
        **bounded("100.708", "100.169").projectionGroup,
    }
    label = labelled(group, latitudeType="planetocentric", longitudeDirection="west")
    health = checkCrs(None, Affine.identity(), 64, 64, label)
    assert codes(health) == ["no-crs", "lon-bounds-westward"]
