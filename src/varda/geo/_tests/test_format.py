from affine import Affine

from varda.geo.describe import describeCrs
from varda.geo.format import formatLonLat, formatMapXY
from varda.geo._tests.fixtures import CRISM_MTRDR_WKT, MOLA_MEGDR_PROJ, WGS84


def test_crism_names_mars_and_its_conventions():
    text = formatLonLat(77.4512, 18.40312, describeCrs(CRISM_MTRDR_WKT))
    assert text == "lat 18.40312° N  lon 77.45120° E  (Mars, planetocentric)"


def test_a_0_360_longitude_is_shown_in_that_domain():
    description = describeCrs(MOLA_MEGDR_PROJ, _affine360(), 10, 10)
    assert description.longitudeDomain == "0..360"
    text = formatLonLat(-102.55, -5.0, description)
    assert "lat 5.00000° S" in text
    assert "lon 257.45000° E (0–360)" in text


def test_a_west_positive_crs_is_planetographic_and_west():
    description = describeCrs("IAU_2015:49901")
    text = formatLonLat(77.0, 10.0, description)
    assert "planetographic" in text
    assert "° W" in text


def test_earth_is_named_earth():
    assert "(Earth, " in formatLonLat(1.0, 2.0, describeCrs(WGS84))


def test_map_xy_has_units():
    description = describeCrs(CRISM_MTRDR_WKT)
    assert formatMapXY(1234.5, -987.0, description) == "xy 1234.50, -987.00 m"


def _affine360():
    return Affine(1.0, 0, 250.0, 0, -1.0, 0.0)
