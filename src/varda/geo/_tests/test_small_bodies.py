import pytest

from varda.geo.bodies import BODIES, BodyShape, bodyNamed, identifyBody, smallBodies


@pytest.mark.parametrize(
    "name, naif",
    [
        ("Bennu", 2101955),
        ("Ryugu", 2162173),
        ("Didymos", 920065803),
        ("Dimorphos", 120065803),
        ("Arrokoth", 2486958),
    ],
)
def test_bodies_proj_lacks_are_known(name, naif):
    body = bodyNamed(name)
    assert body is not None and body.naifId == naif
    assert body.shape is BodyShape.IRREGULAR
    assert body.sources["meanRadius"]  # every value is cited


def test_every_value_has_a_citation():
    for body in smallBodies():
        for field in ("meanRadius", "rotationPeriodH", "density"):
            if getattr(body, field) is not None:
                assert body.sources.get(field), (body.name, field)


def test_density_is_value_and_sigma():
    density = BODIES["Bennu"].density
    assert density is not None
    value, sigma = density
    assert 0 < sigma < value


def test_shape_classes():
    assert BODIES["Mars"].shape is BodyShape.ELLIPSOID_LIKE
    assert BODIES["Moon"].shape is BodyShape.SPHERE_LIKE
    assert BODIES["Eros"].shape is BodyShape.IRREGULAR
    assert BODIES["Vesta"].shape is BodyShape.ELLIPSOID_LIKE  # an IAU ellipsoid


def test_label_target_finds_bennu():
    assert identifyBody(None, labelTarget="(101955) BENNU").body == BODIES["Bennu"]


def test_body_proj_lacks_has_a_sphere_crs_named_for_it():
    bennu = BODIES["Bennu"]
    ellipsoid = bennu.sphereCrs().ellipsoid
    assert ellipsoid is not None and ellipsoid.semi_major_metre == bennu.meanRadius
    crs = bennu.sphereCrs()
    assert identifyBody(crs).body == bennu


def test_provisional_designations_and_old_names():
    assert bodyNamed("2014 MU69") == BODIES["Arrokoth"]
    assert bodyNamed("67P") == BODIES["Churyumov-Gerasimenko"]
    assert bodyNamed("433 Eros") == BODIES["Eros"]
