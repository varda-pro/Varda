"""writeRaster saves a VardaRaster to disk with the metadata Varda reads back."""

from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import openDataSource
from varda.image_loading.raster_writer import (
    resolveOutputPath,
    suggestedOutputPath,
    writeRaster,
)
from varda.utilities.debug import generate_random_image


def _spectralImage() -> VardaRaster:
    rng = np.random.default_rng(3)
    cube = rng.random((5, 7, 4)).astype(np.float32)
    cube[0, 0, :] = -9999.0
    source = ArrayDataSource(
        cube,
        wavelengths=np.array([450.0, 550.0, 650.0, 750.0]),
        wavelengthUnits="nm",
        bandNames=["blue", "green", "red", "nir"],
        transform=Affine(10.0, 0.0, 100.0, 0.0, -10.0, 200.0),
        crs=CRS.from_epsg(32612),
        nodata=-9999.0,
        defaultBands=np.array([2, 1, 0]),
        description="a test scene",
    )
    return VardaRaster(source, name="scene")


def test_envi_round_trip_keeps_data_and_spectral_metadata(tmp_path):
    image = _spectralImage()

    written = writeRaster(image, tmp_path / "scene.hdr")

    assert written == tmp_path / "scene.img"
    assert (tmp_path / "scene.hdr").exists()
    loaded = openDataSource(str(written))
    data = loaded.readAllBands()  # nodata pixels come back masked as NaN
    np.testing.assert_array_equal(data[1:], image.getData()[1:])
    assert np.isnan(data[0, 0]).all()
    np.testing.assert_array_equal(loaded.wavelengths, image.wavelengths)
    assert loaded.wavelengthUnits == "nm"
    assert loaded.bandNames == ["blue", "green", "red", "nir"]
    assert list(loaded.defaultBands) == [2, 1, 0]
    assert loaded.nodata == -9999.0
    assert loaded.transform == image.transform
    assert loaded.crs is not None and loaded.crs.to_epsg() == 32612


def _headerDescription(hdr) -> str:
    text = hdr.read_text()
    start = text.index("description = {") + len("description = {")
    return text[start : text.index("}", start)].strip()


def test_envi_header_carries_the_images_description(tmp_path):
    # GDAL's ENVI driver writes the file's path there unless told otherwise
    writeRaster(_spectralImage(), tmp_path / "scene.img")
    assert _headerDescription(tmp_path / "scene.hdr").startswith("a test scene")
    assert str(tmp_path) not in _headerDescription(tmp_path / "scene.hdr")


def test_envi_round_trip_of_a_parameter_image_keeps_its_band_labels(tmp_path):
    names = ["BD1900", "OLINDEX3", "R770"]
    source = ArrayDataSource(
        np.zeros((3, 3, 3), dtype=np.float32),
        wavelengths=np.array(names),
        wavelengthUnits="parameters",
        bandNames=names,
        isParameterImage=True,
    )
    image = VardaRaster(source, name="params")

    written = writeRaster(image, tmp_path / "params")  # no suffix: ENVI by default

    assert written == tmp_path / "params.img"
    loaded = openDataSource(str(written))
    assert loaded.isParameterImage
    assert loaded.bandNames == names
    assert list(loaded.wavelengths) == names


def test_geotiff_round_trip_keeps_data_and_georeferencing(tmp_path):
    image = _spectralImage()

    written = writeRaster(image, tmp_path / "scene.tif")

    assert written == tmp_path / "scene.tif"
    loaded = openDataSource(str(written))
    data = loaded.readAllBands()  # nodata pixels come back masked as NaN
    np.testing.assert_array_equal(data[1:], image.getData()[1:])
    assert np.isnan(data[0, 0]).all()
    assert loaded.nodata == -9999.0
    assert loaded.bandNames == ["blue", "green", "red", "nir"]
    np.testing.assert_array_equal(loaded.wavelengths, image.wavelengths)
    assert loaded.transform == image.transform
    assert loaded.crs is not None and loaded.crs.to_epsg() == 32612


def test_unknown_extension_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="\\.png"):
        writeRaster(_spectralImage(), tmp_path / "scene.png")


def test_envi_export_keeps_fwhm_bad_bands_and_scale_factor(tmp_path):
    cube = np.ones((3, 4, 3), dtype=np.float32)
    source = ArrayDataSource(
        cube,
        wavelengths=np.array([1.0, 1.5, 2.0]),
        wavelengthUnits="Micrometers",
        extraMetadata={
            "fwhm": "{ 0.01 , 0.01 , 0.02 }",
            "bbl": "{ 1 , 0 , 1 }",
            "reflectance_scale_factor": "10000",
        },
    )
    written = writeRaster(VardaRaster(source, name="cube"), tmp_path / "cube.hdr")
    loaded = VardaRaster(openDataSource(str(written)), name="back")
    model = loaded.bandModel
    np.testing.assert_allclose(model.centers, [1000, 1500, 2000])
    np.testing.assert_allclose(model.fwhm, [10, 10, 20])
    assert model.badBands.tolist() == [False, True, False]
    assert model.scaleFactor == 10000


def test_a_product_that_is_not_a_spectrum_gets_no_wavelength(tmp_path):
    source = ArrayDataSource(
        np.zeros((3, 3, 1), dtype=np.float32),
        wavelengths=np.array(["Slope 1000-2000 nm"]),
        wavelengthUnits="per nm",
        bandNames=["Slope 1000-2000 nm"],
    )
    writeRaster(VardaRaster(source, name="slope"), tmp_path / "slope.hdr")
    header = (tmp_path / "slope.hdr").read_text().lower()
    assert "wavelength =" not in header and "wavelength units" not in header
    assert "slope 1000-2000 nm" in header  # still named, as its band


@pytest.mark.parametrize("name", ["scene.tif", "scene.hdr"])
def test_export_writes_the_corrected_transform(tmp_path, name):
    image = _spectralImage()
    corrected = (5.0, 0.0, 300.0, 0.0, -5.0, 400.0)
    image.setMetadataOverrides({"transform": corrected})

    written = writeRaster(image, tmp_path / name)

    with rasterio.open(written) as dataset:
        assert dataset.transform == Affine(*corrected)


# --- Where Save as text points (release QA #10, #16, #24) -------------------


def test_a_bare_name_goes_into_the_given_folder(tmp_path):
    assert resolveOutputPath("relative name.tif", tmp_path) == tmp_path / (
        "relative name.tif"
    )
    assert resolveOutputPath("🙂", tmp_path) == tmp_path / "🙂"


def test_a_full_path_is_kept(tmp_path):
    target = tmp_path / "out.img"
    assert resolveOutputPath(f"  {target}  ", Path.home()) == target


def test_trailing_dots_and_spaces_are_dropped(tmp_path):
    assert resolveOutputPath("trailing dot.", tmp_path) == tmp_path / "trailing dot"
    assert resolveOutputPath("name. . ", tmp_path) == tmp_path / "name"


def test_writing_a_name_ending_in_a_dot_makes_no_double_dot(tmp_path):
    written = writeRaster(
        generate_random_image((20, 20, 10)),
        resolveOutputPath("trailing dot.", tmp_path),
    )
    assert written.name == "trailing dot.img"


@pytest.mark.parametrize("name", ["bad<>.tif", "a|b.tif", "what?.img", "x*.tif"])
def test_characters_windows_forbids_are_named(tmp_path, name):
    with pytest.raises(ValueError, match="can't be used in a file name"):
        resolveOutputPath(name, tmp_path)


@pytest.mark.parametrize("name", ["CON.tif", "nul", "com1.img", "LPT9.tif"])
def test_names_windows_reserves_are_refused(tmp_path, name):
    with pytest.raises(ValueError, match="won't open on Windows"):
        resolveOutputPath(name, tmp_path)


def test_an_empty_name_is_refused(tmp_path):
    with pytest.raises(ValueError, match="Type a file name"):
        resolveOutputPath(" . ", tmp_path)


def test_a_missing_folder_is_named(tmp_path):
    with pytest.raises(ValueError, match="doesn't exist"):
        resolveOutputPath(str(tmp_path / "no such folder" / "x.tif"), tmp_path)


def test_a_format_varda_cant_write_is_named_before_writing(tmp_path):
    with pytest.raises(ValueError, match=r"\.xyz isn't a format Varda writes"):
        resolveOutputPath("wrong extension.xyz", tmp_path)
    # a dot inside a name without a known suffix is not a format
    assert resolveOutputPath("scene v2.tif", tmp_path).name == "scene v2.tif"


# --- Suggested export names (review of release QA #16) ----------------------


def _named(tmp_path, name: str) -> VardaRaster:
    """A file-backed image renamed as an analysis names its result."""
    source = writeRaster(generate_random_image((20, 20, 10)), tmp_path / "src.img")
    image = VardaRaster(openDataSource(str(source)), name=name)
    return image


@pytest.mark.parametrize(
    "name",
    [
        "qa_cube [b1*2]",  # Band Math
        "qa_cube [b1/b2]",  # Band Math, a ratio
        "qa_cube local RX 3/9",  # RX's inner/outer windows
        'a <b> c:d "e" f|g h?i back\\slash',
        "CON",  # reserved by Windows
        "nul.result",
    ],
)
def test_the_suggested_path_is_one_export_accepts(tmp_path, name):
    image = _named(tmp_path, name)
    suggested = suggestedOutputPath(image)
    assert suggested.parent == tmp_path  # no '/' made a folder
    assert resolveOutputPath(str(suggested), tmp_path) == suggested


def test_suggested_names_keep_what_they_can():
    from_band_math = suggestedOutputPath(
        VardaRaster(ArrayDataSource(np.zeros((2, 2, 1))), name="qa_cube [b1/b2]")
    )
    assert from_band_math.name == "qa_cube [b1_b2].img"
