import pytest

from varda.geo.bodies import BODIES, bodyNamed, identifyBody
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_WKT,
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    LRO_WAC_SOUTH_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    VESTA_IAU,
    WGS84,
    makeImage,
)


def test_crism_mtrdr_is_mars_by_datum():
    match = identifyBody(CRISM_MTRDR_WKT)
    assert match.body is not None and match.body.name == "Mars"
    assert "D_MARS" in match.evidence


def test_hirise_polar_sphere_is_mars():
    match = identifyBody(HIRISE_POLAR_PROJ)
    assert match.body is not None and match.body.name == "Mars"
    assert "3376.2 km" in match.evidence


def test_mola_megdr_sphere_is_mars():
    match = identifyBody(MOLA_MEGDR_PROJ)
    assert match.body is not None and match.body.name == "Mars"


@pytest.mark.parametrize("crs", [LRO_WAC_PROJ, LRO_WAC_SOUTH_POLAR_PROJ])
def test_lro_wac_is_moon(crs):
    match = identifyBody(crs)
    assert match.body is not None and match.body.name == "Moon"


def test_wgs84_is_earth():
    match = identifyBody(WGS84)
    assert match.body is not None and match.body.name == "Earth"


def test_vesta_iau_sphere_is_vesta():
    match = identifyBody(VESTA_IAU)
    assert match.body is not None and match.body.name == "Vesta"
    assert "IAU_2015" in match.evidence


def test_iau_ellipsoid_code_is_its_body():
    match = identifyBody("IAU_2015:49910")
    assert match.body is not None and match.body.name == "Mars"


def test_unknown_radius_is_no_body():
    match = identifyBody("+proj=longlat +R=3000000 +no_defs")
    assert match.body is None
    assert "matches no known body" in match.evidence


def test_ambiguous_radius_lists_candidates():
    # 253 km is within 1% of Enceladus (252.1 km) and of Vesta's IAU 2015
    # sphere (255.0 km), and there's no name to decide between them
    match = identifyBody("+proj=longlat +R=253000 +no_defs")
    assert match.body is None
    assert set(match.candidates) == {"Vesta", "Enceladus"}


def test_no_crs_is_no_body():
    assert identifyBody(None).body is None


def test_label_beats_crs():
    match = identifyBody(WGS84, labelTarget="MARS")
    assert match.body is not None and match.body.name == "Mars"
    assert "label" in match.evidence


def test_unknown_label_falls_back_to_crs():
    match = identifyBody(WGS84, labelTarget="ARROKOTH")
    assert match.body is not None and match.body.name == "Earth"


def test_body_radii_come_from_proj():
    mars, moon = BODIES["Mars"], BODIES["Moon"]
    assert (mars.equatorialRadius, mars.polarRadius) == (3396190.0, 3376200.0)
    assert (moon.equatorialRadius, moon.polarRadius) == (1737400.0, 1737400.0)
    assert mars.meanRadius == 3389500.0


def test_reference_crs():
    assert BODIES["Mars"].referenceCrs().to_authority(auth_name="IAU_2015") == (
        "IAU_2015",
        "49902",
    )
    assert BODIES["Moon"].referenceCrs().to_authority(auth_name="IAU_2015") == (
        "IAU_2015",
        "30100",
    )
    assert BODIES["Mars"].sphereCrs().to_authority(auth_name="IAU_2015") == (
        "IAU_2015",
        "49900",
    )


def test_geod_is_reference_ellipsoid():
    geod = BODIES["Mars"].geod()
    assert geod.a == 3396190.0
    assert geod.b == pytest.approx(3376200.0)


def test_west_positive_ographic():
    assert BODIES["Mars"].westPositiveOgraphic
    assert not BODIES["Earth"].westPositiveOgraphic
    assert not BODIES["Moon"].westPositiveOgraphic


def test_all_bodies_present():
    assert list(BODIES) == [
        "Mercury",
        "Venus",
        "Earth",
        "Moon",
        "Mars",
        "Phobos",
        "Ceres",
        "Vesta",
        "Io",
        "Europa",
        "Ganymede",
        "Callisto",
        "Enceladus",
        "Titan",
    ]


@pytest.mark.parametrize(
    "name, expected",
    [
        ("D_MARS", "Mars"),
        ("Mars (2015) - Sphere", "Mars"),
        ("luna", "Moon"),
        ("MARS", "Mars"),
        ("Moon_2000", "Moon"),
        ("GCS_Mars_2000", "Mars"),
        ("mars_2000", "Mars"),
    ],
)
def test_bodyNamed_aliases(name, expected):
    body = bodyNamed(name)
    assert body is not None and body.name == expected


def test_bodyNamed_unknown():
    assert bodyNamed("Arrokoth") is None
    assert bodyNamed("") is None


def test_fixture_image_is_centered():
    image = makeImage(
        CRISM_MTRDR_WKT, centerLonLat=(77.5, 18.4), pixelSize=18.0, label="x"
    )
    assert image.extraMetadata["pds3Label"] == "x"
    assert identifyBody(image.crs).body == BODIES["Mars"]
