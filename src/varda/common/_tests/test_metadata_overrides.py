"""Editing an image's metadata in Varda: a no-data value (where the file
has none), wavelengths and their unit, band names, FWHM, bad bands, the
name and the description. The data is read as before, now masked by the
new no-data value; everything that asks the image sees the change."""

import numpy as np
import pytest

from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import WGS84, makeImage
from varda.geo.equivalence import sameCrs
from varda.image_loading.data_sources.array_data_source import ArrayDataSource


MARS_SPHERE = CRS.from_user_input("IAU_2015:49900")


def _image():
    data = np.arange(24, dtype=np.float32).reshape(2, 3, 4)
    data[0, 0, :] = -999.0  # fill the file never declared
    return VardaRaster(
        ArrayDataSource(data, wavelengths=np.array([1.0, 2.0, 3.0, 4.0])), name="cube"
    )


def test_a_no_data_value_masks_reads_and_counts_as_no_data():
    image = _image()
    changes = []
    image.sigMetadataChanged.connect(lambda: changes.append(1))
    assert image.nodata is None
    image.setMetadataOverrides({"nodata": -999.0})
    assert image.nodata == -999.0 and image.bandModel.noData == -999.0
    band = image.getBands([1])
    assert np.isnan(band[0, 0, 0]) and not np.isnan(band[1, 1, 0])
    assert np.isnan(image.getData()[0, 0]).all()
    assert changes == [1]


def test_wavelengths_units_names_fwhm_and_bad_bands():
    image = _image()
    image.setMetadataOverrides(
        {
            "wavelengths": [1000.0, 1100.0, 1200.0, 1300.0],
            "wavelengthUnits": "nm",
            "bandNames": ["a", "b", "c", "d"],
            "fwhm": [10.0, 10.0, 10.0, 10.0],
            "bbl": [1, 1, 0, 1],
        },
        name="renamed",
    )
    model = image.bandModel
    np.testing.assert_allclose(model.centers, [1000, 1100, 1200, 1300])
    np.testing.assert_allclose(model.fwhm, [10, 10, 10, 10])
    np.testing.assert_array_equal(model.badBands, [False, False, True, False])
    assert image.bandNames == ["a", "b", "c", "d"] and image.name == "renamed"
    assert image.metadataOverrides["wavelengthUnits"] == "nm"


def test_overrides_can_be_changed_and_cleared():
    image = _image()
    image.setMetadataOverrides({"nodata": -999.0})
    image.setMetadataOverrides({"nodata": 5.0})
    assert image.nodata == 5.0 and np.isnan(image.getBands([1])[0, 1, 0])  # 5 is there
    assert not np.isnan(image.getBands([1])[0, 0, 0])  # -999 is a value again
    image.setMetadataOverrides({})
    assert image.nodata is None and not np.isnan(image.getBands([0])).any()


def test_a_bad_override_is_refused():
    image = _image()
    with pytest.raises(ValueError, match="4 bands"):
        image.setMetadataOverrides({"wavelengths": [1.0, 2.0]})


def test_a_renderer_redraws_when_the_no_data_value_changes(qtbot):
    from varda.image_rendering.image_renderer import ImageRenderer

    image = _image()
    renderer = ImageRenderer(image=image)
    renderer.transparentNoData = True
    before = renderer.render()
    assert before[0, 0, 3] > 0  # the fill shows
    refreshed = []
    renderer.sigShouldRefresh.connect(lambda: refreshed.append(1))
    image.setMetadataOverrides({"nodata": -999.0})
    assert refreshed
    assert renderer.render()[0, 0, 3] == 0  # now see-through no-data


# --- a corrected coordinate system ---


def _onWgs84() -> VardaRaster:
    return makeImage(WGS84, centerLonLat=(137.4, -4.6), pixelSize=1 / 256)


def test_a_crs_override_takes_the_files_place():
    image = _onWgs84()
    before = image.pixelToLatLon(10, 20)
    image.setMetadataOverrides({"crs": MARS_SPHERE.to_wkt()})
    assert image.crs is not None and sameCrs(image.crs, "IAU_2015:49900")
    assert image.crs is image.crs  # built once per override
    assert image.dataSource.crs is not None and image.dataSource.crs.to_epsg() == 4326
    after = image.pixelToLatLon(10, 20)  # the cached transformer was rebuilt
    assert after == pytest.approx(before)
    image.setMetadataOverrides({})
    assert image.crs is not None and image.crs.to_epsg() == 4326


def test_a_crs_override_of_none_is_the_files_crs():
    image = _onWgs84()
    image.setMetadataOverrides({"crs": None})
    assert image.crs is not None and image.crs.to_epsg() == 4326
    assert image.metadataOverrides == {"crs": None}


def test_a_crs_override_that_isnt_a_crs_is_refused():
    image = _onWgs84()
    image.setMetadataOverrides({"crs": "EPSG:4326"})
    with pytest.raises(ValueError, match="coordinate system"):
        image.setMetadataOverrides({"crs": "garbage"})
    with pytest.raises(ValueError, match="coordinate system"):
        image.setMetadataOverrides({"crs": 4326})
    assert image.metadataOverrides == {"crs": "EPSG:4326"}  # unchanged


def test_an_image_starts_without_a_health_check_or_label_facts():
    image = _image()
    assert image.crsHealth is None and image.labelHints is None


# --- a corrected pixel-to-map transform ---


def _identityOnMars() -> VardaRaster:
    """An image whose file has no transform (the identity), given a CRS."""
    image = VardaRaster(ArrayDataSource(np.zeros((4, 5, 1), dtype=np.float32)))
    image.setMetadataOverrides({"crs": MARS_SPHERE.to_wkt()})
    return image


def test_a_transform_override_makes_the_image_geospatial():
    image = _identityOnMars()
    assert not image.hasGeospatialData
    crs = image.metadataOverrides["crs"]
    image.setMetadataOverrides(
        {"crs": crs, "transform": [0.5, 0, 137.0, 0, -0.5, -4.0]}
    )
    assert image.hasGeospatialData
    assert image.transform == Affine(0.5, 0, 137.0, 0, -0.5, -4.0)
    lat, lon = image.pixelToLatLon(2, 4)  # pixel centers
    assert lon == pytest.approx(137.0 + 2.5 * 0.5)
    assert lat == pytest.approx(-4.0 - 4.5 * 0.5)


def test_a_transform_override_can_be_a_tuple():
    image = _identityOnMars()
    image.setMetadataOverrides({"transform": (2.0, 0.0, 10.0, 0.0, -2.0, 20.0)})
    assert image.transform == Affine(2.0, 0.0, 10.0, 0.0, -2.0, 20.0)


@pytest.mark.parametrize(
    "bad",
    [
        [1.0, 0.0, 0.0, 0.0, -1.0],  # five numbers
        [1.0, 2.0, 0.0, 2.0, 4.0, 0.0],  # a zero determinant
        [1.0, 0.0, float("nan"), 0.0, -1.0, 0.0],
        [1.0, 0.0, float("inf"), 0.0, -1.0, 0.0],
        ["1", 0.0, 0.0, 0.0, -1.0, 0.0],
        "garbage",
        5.0,
    ],
)
def test_a_transform_override_that_isnt_one_is_refused(bad):
    image = _onWgs84()
    before = image.transform
    good = list(before)[:6]
    image.setMetadataOverrides({"transform": good})
    with pytest.raises(ValueError, match="six finite numbers"):
        image.setMetadataOverrides({"transform": bad})
    assert image.metadataOverrides == {"transform": good}  # unchanged
    assert image.transform == before


def test_a_transform_override_of_none_is_the_files_transform():
    image = _onWgs84()
    files = image.transform
    image.setMetadataOverrides({"transform": [1.0, 0.0, 0.0, 0.0, -1.0, 0.0]})
    assert image.transform != files
    image.setMetadataOverrides({"transform": None})
    assert image.transform == files
    assert image.metadataOverrides == {"transform": None}
