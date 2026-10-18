"""Where a point or a line on one image falls on the others: the same pixel,
or the same place on the map (across map projections too); spectra read
there with no-data and bad bands left out."""

import math
import warnings

import numpy as np
import pytest
from affine import Affine
from pyproj import CRS, Transformer

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_ENVI_WKT,
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    WGS84,
    makeImage,
)
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.proj_lock import PROJ_LOCK
from varda.workspaces.linked_images.linking import (
    Link,
    bitPlanes,
    highestBit,
    linkWarnings,
    linkedPoints,
    spectrumAt,
)

EQC = CRS.from_user_input("+proj=eqc +R=3396190 +units=m +no_defs")


def _image(transform=None, crs=EQC, shape=(20, 30, 4), name="a", **kwargs):
    data = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    if transform is None:
        return VardaRaster(ArrayDataSource(data, **kwargs), name=name)
    return VardaRaster(
        ArrayDataSource(data, transform=transform, crs=crs, **kwargs), name=name
    )


def test_by_pixel_every_image_gets_the_same_pixel():
    images = [_image(), _image(shape=(10, 10, 2))]
    a, b = linkedPoints(images, 0, [[5.5, 3.5]], Link.PIXEL)
    np.testing.assert_allclose(a, [[5.5, 3.5]])
    np.testing.assert_allclose(b, [[5.5, 3.5]])


def test_by_map_location_finer_and_shifted_grids_agree():
    coarse = _image(Affine(20, 0, 1000, 0, -20, 5000))
    fine = _image(Affine(10, 0, 1100, 0, -10, 4900), name="fine")
    _, there = linkedPoints([coarse, fine], 0, [[10, 10]], Link.MAP)
    # (10, 10) on the coarse grid is (1200, 4800): 10 px right and down on the fine
    np.testing.assert_allclose(there, [[10, 10]])
    _, back = linkedPoints([fine, coarse], 0, [[0, 0]], Link.MAP)
    np.testing.assert_allclose(back, [[5, 5]])


def test_by_map_location_across_map_projections():
    geographic = CRS.from_user_input("+proj=longlat +R=3396190 +no_defs")
    degree = np.pi * 3396190 / 180
    projected = _image(Affine(100, 0, 0, 0, -100, 0))
    lonlat = _image(Affine(0.01, 0, 0, 0, -0.01, 0), crs=geographic, name="ll")
    _, there = linkedPoints([projected, lonlat], 0, [[10, 10]], Link.MAP)
    # 1000 m east and south of (0, 0) is 1000 / degree degrees
    np.testing.assert_allclose(there, [[1000 / degree / 0.01] * 2], rtol=1e-6)


def test_one_point_across_map_projections_raises_no_numpy_deprecation():
    geographic = CRS.from_user_input("+proj=longlat +R=3396190 +no_defs")
    projected = _image(Affine(100, 0, 0, 0, -100, 0))
    lonlat = _image(Affine(0.01, 0, 0, 0, -0.01, 0), crs=geographic, name="ll")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        _, there = linkedPoints([projected, lonlat], 0, [[10, 10]], Link.MAP)
    assert there.shape == (1, 2)


def test_images_without_georeferencing_fall_back_to_the_pixel():
    plain = _image()
    mapped = _image(Affine(20, 0, 1000, 0, -20, 5000))
    _, there = linkedPoints([mapped, plain], 0, [[3, 4]], Link.MAP)
    np.testing.assert_allclose(there, [[3, 4]])


def test_a_spectrum_leaves_out_no_data_and_bad_bands():
    image = _image(
        wavelengths=np.array([1.0, 2.0, 3.0, 4.0]),
        wavelengthUnits="micrometers",
        nodata=5.0,
        extraMetadata={"bbl": [1, 1, 0, 1]},
    )
    spectrum = spectrumAt(image, 1.5, 0.5)  # pixel (1, 0): values 4, 5, 6, 7
    assert spectrum is not None
    np.testing.assert_allclose(spectrum.axis, [1000, 2000, 3000, 4000])
    np.testing.assert_array_equal(np.isnan(spectrum.values), [False, True, True, False])
    assert spectrum.axisLabel == "Wavelength (µm)"
    np.testing.assert_allclose(spectrum.displayAxis, [1, 2, 3, 4])
    assert spectrumAt(image, -1, 3) is None  # off the image
    plain = spectrumAt(_image(), 2, 2)
    assert plain is not None and plain.axisLabel == "Band"


def test_a_spectrum_of_bit_masks_becomes_bits_by_band():
    values = np.array([5.0, 0.0, np.nan, 2.0])
    planes = bitPlanes(values, 3)
    np.testing.assert_array_equal(
        planes[:, [0, 1, 3]], [[1, 0, 0], [0, 0, 1], [1, 0, 0]]
    )
    assert np.isnan(planes[:, 2]).all()
    assert highestBit(values) == 3 and highestBit(np.array([np.nan])) == 1


def test_a_spectrum_says_what_its_axis_is():
    image = _image(wavelengths=np.array([1.0, 2.0, 3.0, 4.0]), wavelengthUnits="µm")
    spectrum = spectrumAt(image, 0.5, 0.5)
    assert spectrum is not None and spectrum.axisUnit == "µm"
    plain = spectrumAt(_image(), 0.5, 0.5)
    assert plain is not None and plain.axisUnit == "band"


def test_linking_by_map_warns_about_images_without_georeferencing():
    mapped = _image(Affine(20, 0, 1000, 0, -20, 5000), name="mapped")
    plain = _image(name="plain")
    (warning,) = linkWarnings([mapped, plain], Link.MAP)
    assert "plain" in warning and "pixel" in warning
    assert linkWarnings([mapped, mapped], Link.MAP) == []


def test_linking_by_pixel_warns_about_images_on_other_grids():
    a = _image(Affine(20, 0, 1000, 0, -20, 5000), name="a")
    same = _image(Affine(20, 0, 1000, 0, -20, 5000), name="same")
    shifted = _image(Affine(20, 0, 1100, 0, -20, 5000), name="shifted")
    smaller = _image(Affine(20, 0, 1000, 0, -20, 5000), shape=(10, 30, 4), name="small")
    assert linkWarnings([a, same], Link.PIXEL) == []
    (warning,) = linkWarnings([a, same, shifted, smaller], Link.PIXEL)
    assert "a's grid: shifted, small (" in warning


def test_negative_signed_values_show_their_own_bits():
    """int16 -1 is sixteen bits set, read as unsigned of the same width (as
    Bit Mask Decode does), not a 64-bit wraparound."""
    values = np.array([-1.0, -32768.0, 3.0, np.nan])
    planes = bitPlanes(values, 16, np.dtype(np.int16))
    np.testing.assert_array_equal(planes[:, 0], np.ones(16))
    np.testing.assert_array_equal(planes[:, 1], [0] * 15 + [1])
    np.testing.assert_array_equal(planes[:, 2], [1, 1] + [0] * 14)
    assert np.isnan(planes[:, 3]).all()
    assert highestBit(values, np.dtype(np.int16)) == 16
    assert highestBit(np.array([3.0]), np.dtype(np.int16)) == 2


def test_cross_body_link_does_not_raise():
    earth = makeImage(WGS84, centerLonLat=(10.0, 15.0), pixelSize=0.01, name="earth")
    mars = makeImage(
        CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0, name="crism"
    )
    onMars, onEarth = linkedPoints([mars, earth], 0, [[3, 4], [5, 6]], Link.MAP)
    np.testing.assert_allclose(onMars, [[3, 4], [5, 6]])
    assert np.isnan(onEarth).all() and onEarth.shape == (2, 2)
    (warning,) = linkWarnings([mars, earth], Link.MAP)
    assert warning == "earth: on Earth, not Mars; not linked"


def test_equivalent_crs_links_without_transform(monkeypatch):
    pds3 = makeImage(CRISM_MTRDR_WKT, centerLonLat=(10.0, 15.0), pixelSize=18.0)
    envi = makeImage(CRISM_MTRDR_ENVI_WKT, centerLonLat=(10.0, 15.0), pixelSize=9.0)

    def refuse(*args, **kwargs):
        raise AssertionError("an equivalent CRS needs no transform")

    monkeypatch.setattr(Transformer, "from_crs", refuse)
    _, there = linkedPoints([pds3, envi], 0, [[48, 48]], Link.MAP)
    np.testing.assert_allclose(there, [[64, 64]], atol=1e-6)
    assert linkWarnings([pds3, envi], Link.MAP) == []


def test_a_sphere_and_an_ellipsoid_link_at_the_same_planetocentric_place():
    """CTX's sphere and the planetographic, west-positive Mars ellipsoid: the
    linked point is the same planetocentric longitude and latitude (Snyder,
    1987, eq. 3-28), not the same latitude number."""
    ctx = makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 45.0), pixelSize=20.0)
    ographic = makeImage("IAU_2015:49911", centerLonLat=(-77.5, 45.3), pixelSize=20.0)
    _, there = linkedPoints([ctx, ographic], 0, [[32, 32]], Link.MAP)
    with PROJ_LOCK:
        # 49901 lists latitude first
        latG, lonW = Transformer.from_crs("IAU_2015:49911", "IAU_2015:49901").transform(
            *ographic.imageToGeo(there)[0]
        )
    a, c = 3396190.0, 3376200.0
    latC = math.degrees(math.atan((c / a) ** 2 * math.tan(math.radians(latG))))
    assert latC == pytest.approx(45.0, abs=1e-9)
    assert (-lonW) % 360 == pytest.approx(77.5, abs=1e-9)
