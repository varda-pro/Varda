from __future__ import annotations

import math

import pytest

from varda.geo._tests.fixtures import MOLA_MEGDR_PROJ, makeImage
from varda.geo.health import Severity, checkCrs
from varda.geo.measure import lineLength
from varda.geo.recommend import (
    IRREGULAR_NOTE,
    IrregularBodyError,
    Purpose,
    footprintLonLat,
    recommendProjection,
)

EROS_SPHERE = "IAU_2015:200043300"


def erosImage():
    return makeImage(EROS_SPHERE, centerLonLat=(30, 10), pixelSize=0.01)


def test_irregular_body_is_a_health_issue():
    image = erosImage()
    health = checkCrs(image.crs, image.transform, image.width, image.height)
    (issue,) = [i for i in health.issues if i.code == "irregular-body"]
    assert issue.severity is Severity.TIP
    assert issue.message == (
        "Irregular body: distances and areas on a sphere or ellipsoid are "
        "approximate; use a shape model."
    )


def test_mars_has_no_irregular_issue():
    image = makeImage(MOLA_MEGDR_PROJ, centerLonLat=(77.5, 18.4), pixelSize=0.01)
    health = checkCrs(image.crs, image.transform, image.width, image.height)
    assert "irregular-body" not in [i.code for i in health.issues]


def test_lengths_on_an_irregular_body_are_labeled_approximate():
    measured = lineLength(EROS_SPHERE, [[10.0, 0.0], [11.0, 0.0]])
    assert measured.method.endswith(" — approximate (sphere model): irregular body")
    assert math.isnan(measured.bodyModelDifference)
    assert measured.reference is None
    assert measured.value == pytest.approx(math.radians(1) * 17_000, rel=1e-9)


@pytest.mark.parametrize("purpose", [Purpose.MEASUREMENT, Purpose.TERRAIN])
def test_no_measurement_or_terrain_projection_on_an_irregular_body(purpose):
    image = erosImage()
    footprint = footprintLonLat(image.crs, image.transform, image.width, image.height)
    with pytest.raises(IrregularBodyError, match="use a shape model") as raised:
        recommendProjection(image.crs, footprint, purpose)
    assert str(raised.value) == IRREGULAR_NOTE
    assert IRREGULAR_NOTE == (
        "Irregular body: no map projection keeps distances or areas; "
        "use a shape model (3D Terrain → Shape model)"
    )


def test_a_mosaic_projection_is_still_recommended_on_an_irregular_body():
    image = erosImage()
    footprint = footprintLonLat(image.crs, image.transform, image.width, image.height)
    assert recommendProjection(image.crs, footprint, Purpose.MOSAIC).wkt
