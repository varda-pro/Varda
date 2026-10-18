"""A loaded image's bounds as a search area: longitude east, then latitude."""

import pytest

from varda.geo._tests.fixtures import WGS84, makeImage
from varda.workspaces.find_data.basemap import imageBounds


@pytest.mark.parametrize(
    "code, center",
    [
        ("IAU_2015:49911", (-77.5, 18.4)),  # westing; planetographic
        ("IAU_2015:49912", (77.5, 18.4)),  # planetocentric
        (WGS84, (77.5, 18.4)),
    ],
)
def test_bounds_are_east_longitude_and_latitude(code, center):
    image = makeImage(
        code, centerLonLat=center, pixelSize=0.001 if code == WGS84 else 100.0
    )
    west, south, east, north = imageBounds(image)
    assert west < 77.5 < east and east - west < 1
    assert south < 18.4 < north and north - south < 1
