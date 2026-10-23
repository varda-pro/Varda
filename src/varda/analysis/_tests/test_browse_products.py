"""Browse products: CRISM's standard set and custom ones, what an image can
show, and the composites, read a few rows at a time."""

import numpy as np
import pytest

from varda.analysis import browse_products
from varda.analysis.browse_products import (
    CRISM_STANDARD,
    BrowseProduct,
    channelProblem,
    channelRange,
    computeProduct,
    parseProduct,
    productProblem,
)
from varda.analysis.hypyrameter_adapter import computeParameterCube
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

# CRISM's MTRDR spectral sampling, roughly
CRISM_NM = np.arange(436.0, 3920.0, 6.55)


def _image(wavelengths=CRISM_NM, rows=10, cols=7, **kwargs) -> VardaRaster:
    rng = np.random.default_rng(3)
    cube = 0.2 + 0.05 * rng.random((rows, cols, wavelengths.size))
    return VardaRaster(
        ArrayDataSource(cube, wavelengths=wavelengths, wavelengthUnits="nm", **kwargs),
        name="scene",
    )


def test_the_standard_products_are_the_sis_eighteen_in_order():
    names = [p.name for p in CRISM_STANDARD]
    assert (
        names
        == "TRU VNA FEM FM2 FAL IRA MAF HYD PHY PFM PAL HYS ICE IC2 CHL CAR CR2 TAN".split()
    )
    maf = next(p for p in CRISM_STANDARD if p.name == "MAF")
    assert maf.channels == ("OLINDEX3", "LCPINDEX2", "HCPINDEX2")
    assert maf.label == "MAF (Mafic Mineralogy)"


def test_a_product_round_trips_through_text_and_finds_the_standard_one():
    maf = next(p for p in CRISM_STANDARD if p.name == "MAF")
    assert parseProduct(maf.asText()) is maf
    mine = parseProduct(" Mine =R2529, BD1900_2 ,OLINDEX3 ")
    assert mine == BrowseProduct("Mine", ("R2529", "BD1900_2", "OLINDEX3"))
    assert not mine.standard
    with pytest.raises(ValueError, match="NAME = red, green, blue"):
        parseProduct("Mine = R2529, BD1900_2")


def test_reflectance_channels_need_a_band_nearby():
    assert channelProblem("R1330", CRISM_NM) == ""
    assert channelProblem("r1330", CRISM_NM) == ""
    assert channelProblem("R5000", CRISM_NM) == "no band near 5000 nm"
    assert channelProblem("R600", None) == "needs wavelengths"
    # a multispectral image's bands stand in within one band spacing
    assert channelProblem("R600", np.array([450.0, 550.0, 650.0])) == ""


def test_parameters_need_hypyrameter_their_range_and_a_definition(monkeypatch):
    pytest.importorskip("hypyrameter")
    assert channelProblem("OLINDEX3", CRISM_NM) == ""
    assert channelProblem("bd1900r2", CRISM_NM) == ""  # HyPyRameter's BD1900r2
    assert channelProblem("BD1435", CRISM_NM) == "Varda can't compute BD1435 yet"
    assert "needs" in channelProblem("OLINDEX3", np.arange(400.0, 1000.0, 6.55))
    ice = next(p for p in CRISM_STANDARD if p.name == "ICE")
    assert productProblem(ice, CRISM_NM) == (
        "Varda can't compute BD1500_2 yet; Varda can't compute BD1435 yet"
    )
    fm2 = next(p for p in CRISM_STANDARD if p.name == "FM2")
    assert productProblem(fm2, CRISM_NM) == ""  # BDI1000VIS, from HyPyRameter 0.3.1
    fem = next(p for p in CRISM_STANDARD if p.name == "FEM")
    assert fem.channels[1] == "SH600_2"  # as the products' labels name it
    monkeypatch.setattr(browse_products, "HYPYRAMETER_AVAILABLE", False)
    assert channelProblem("OLINDEX3", CRISM_NM) == (
        "OLINDEX3 needs HyPyRameter, which isn't installed"
    )
    assert channelProblem("R770", CRISM_NM) == ""  # reflectance needs nothing


def test_a_reflectance_channel_is_the_median_of_five_bands():
    image = _image()
    composite = computeProduct(image, parseProduct("x = R770, R1330, R2529"))
    cube = np.asarray(image.getData())
    for i, nm in enumerate((770, 1330, 2529)):
        nearest = int(np.argmin(np.abs(CRISM_NM - nm)))
        expected = np.median(cube[:, :, nearest - 2 : nearest + 3], axis=2)
        np.testing.assert_allclose(composite[:, :, i], expected, rtol=1e-6)


def test_no_data_and_bad_bands_are_left_out(monkeypatch):
    rows, cols = 6, 4
    cube = np.full((rows, cols, CRISM_NM.size), 0.3)
    nearest = int(np.argmin(np.abs(CRISM_NM - 770)))
    cube[:, :, nearest] = 0.9  # a bad band in the kernel
    cube[0, 0, :] = 65535.0  # a pixel with no data
    bbl = np.ones(CRISM_NM.size)
    bbl[nearest] = 0
    image = VardaRaster(
        ArrayDataSource(
            cube,
            wavelengths=CRISM_NM,
            wavelengthUnits="nm",
            nodata=65535.0,
            extraMetadata={"bbl": bbl.tolist()},
        ),
        name="scene",
    )
    composite = computeProduct(image, parseProduct("x = R770, R770, R770"))
    assert np.isnan(composite[0, 0]).all()
    np.testing.assert_allclose(composite[1:, :, 0], 0.3)
    np.testing.assert_allclose(composite[0, 1:, 0], 0.3)


def test_parameters_match_hypyrameter_on_the_whole_cube_whatever_the_chunk(monkeypatch):
    pytest.importorskip("hypyrameter")
    image = _image(rows=9)
    monkeypatch.setattr(browse_products, "CHUNK_ROWS", 4)  # three chunks
    maf = next(p for p in CRISM_STANDARD if p.name == "MAF")
    reports = []
    composite = computeProduct(image, maf, lambda p, m: reports.append(p))
    whole = computeParameterCube(
        np.asarray(image.getData(), dtype=np.float64), CRISM_NM, list(maf.channels)
    )
    np.testing.assert_allclose(composite, whole, rtol=1e-5, atol=1e-7)
    assert reports == [44, 88, 100]


def test_an_image_that_cant_show_a_product_says_why():
    image = _image(wavelengths=np.arange(400.0, 1000.0, 6.55))
    with pytest.raises(ValueError, match="no band near 2529 nm"):
        computeProduct(image, parseProduct("FAL = R2529, R1506, R1080"))


def test_ranges_follow_the_pds_products_then_the_scene():
    weak = np.linspace(-0.01, 0.005, 1001)  # within the default ranges
    assert channelRange("LCPINDEX2", weak) == (0.0, 0.02022)
    assert channelRange("lcpindex2", weak) == (0.0, 0.02022)
    assert channelRange("D2200", weak) == (0.0025, 0.01993)
    values = np.linspace(-0.01, 0.05, 1001)
    # a scene above the default range raises its top, never its floor
    floor, top = channelRange("D2200", values + 0.1)
    assert floor == 0.0025 and top == pytest.approx(np.percentile(values + 0.1, 99.9))
    # band depths and indices the PDS doesn't show start at 0
    floor, top = channelRange("BD1300", values)
    assert floor == 0.0 and top == pytest.approx(np.percentile(values, 99.9))
    # reflectances, slopes and ratios: the scene's 0.1st to 99.9th percentile
    scene = tuple(np.percentile(values, (0.1, 99.9)))
    assert channelRange("R770", values) == pytest.approx(scene)
    assert channelRange("IRR2", values) == pytest.approx(scene)
    assert channelRange("BD1300", np.full(5, -0.1)) == (0.0, 1e-6)  # nothing detected
    assert channelRange("R770", np.full(5, np.nan)) == (0.0, 1.0)
    assert channelRange("OLINDEX3", np.full(5, np.nan)) == (0.0, 0.25898)


def test_bdi1000vis_is_a_band_depth():
    """HyPyRameter 0.3.0 integrated R/RPEAK1, not 1 - R/RPEAK1: a flat
    spectrum, with no 1 µm band, gave about 0.19 instead of 0."""
    pytest.importorskip("hypyrameter")
    flat = np.full((2, 2, CRISM_NM.size), 0.3)
    value = computeParameterCube(flat, CRISM_NM, ["BDI1000VIS"])
    np.testing.assert_allclose(value, 0.0, atol=1e-6)
