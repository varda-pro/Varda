"""The PCA / MNF / ICA analyses produce component images over the source footprint."""

import numpy as np
import pytest
from shapely.geometry import box

from varda.analysis import transforms
from varda.analysis.analysis import AnalysisContext
from varda.analysis.linear_algebra import NoiseEstimate
from varda.analysis.transforms import (
    TRANSFORM_METADATA_KEY,
    IcaAnalysis,
    MnfAnalysis,
    PcaAnalysis,
    WeightKind,
)
from varda.common.vec2 import Vec2
from varda.common.entities import Color, ROIMode, VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.rois.roi_collection import ROICollection
from varda.utilities.debug import generate_random_image


def _image(seed=0, bands=8) -> VardaRaster:
    rng = np.random.default_rng(seed)
    sources = rng.normal(size=(20 * 30, 3))
    mixing = rng.normal(size=(3, bands))
    cube = (sources @ mixing).reshape(20, 30, bands) + 0.01 * rng.normal(
        size=(20, 30, bands)
    )
    cube[0, 0, :] = -1.0  # a nodata pixel
    return VardaRaster(
        ArrayDataSource(
            cube, wavelengths=np.linspace(1000.0, 2600.0, bands), nodata=-1.0
        ),
        name="scene",
    )


def test_pca_image_has_named_components_and_nodata_stays_missing():
    image = _image()
    analysis = PcaAnalysis()
    analysis.components.set(3)

    result = analysis.run(image, lambda p, m: None)

    assert result.name == "scene PCA"
    assert result.bandCount == 3
    assert result.bandNames[0].startswith("PC 1 (") and result.bandNames[0].endswith(
        "%)"
    )
    assert result.width == image.width and result.height == image.height
    assert np.isnan(result.getData()[0, 0]).all()
    assert np.isfinite(result.getData()[5, 5]).all()


def test_mnf_image_is_named_and_ordered():
    image = _image()
    analysis = MnfAnalysis()
    analysis.components.set(2)

    result = analysis.run(image, lambda p, m: None)

    assert result.name == "scene MNF"
    assert result.bandNames == ["MNF 1", "MNF 2"]


def test_ica_image_is_named():
    image = _image()
    analysis = IcaAnalysis()
    analysis.components.set(3)

    result = analysis.run(image, lambda p, m: None)

    assert result.name == "scene ICA"
    assert result.bandNames == ["IC 1", "IC 2", "IC 3"]
    assert result.bandCount == 3


def test_ica_records_convergence_and_warns_when_it_stops_short(caplog):
    """Gaussian sources have no independent directions to find: FastICA runs
    to the iteration cap, and the result says so."""
    analysis = IcaAnalysis()
    analysis.components.set(3)
    analysis.maxIterations.set(10)

    with caplog.at_level("WARNING"):
        result = analysis.run(_image(), lambda p, m: None)

    info = result.extraMetadata[TRANSFORM_METADATA_KEY]
    assert info["iterations"] == 10 and info["maxIterations"] == 10
    assert info["converged"] is False
    assert info["tolerance"] == pytest.approx(1e-5)
    assert "did not converge" in result.dataSource.description
    assert "did not converge" in caplog.text


def test_components_are_capped_at_the_band_count():
    image = _image(bands=4)
    analysis = PcaAnalysis()
    analysis.components.set(50)

    result = analysis.run(image, lambda p, m: None)

    assert result.bandCount == 4


def test_transforms_are_filed_under_transforms():
    for analysis in (PcaAnalysis, MnfAnalysis, IcaAnalysis):
        assert analysis.category == "Transforms"
        assert analysis.isAvailable()


def test_mnf_can_take_its_noise_from_a_homogeneous_roi_class():
    image = generate_random_image((40, 40, 10))
    rois = ROICollection()
    rois.addROI(box(5, 5, 20, 20), "flat area", Color(1, 0, 0, 0.5), ROIMode.RECTANGLE)
    analysis = MnfAnalysis()
    analysis.setContext(AnalysisContext(rois=rois))
    analysis.noiseEstimate.set(NoiseEstimate.HOMOGENEOUS_REGION)
    analysis.prepareFor(image)
    assert analysis.noiseRoi.choices == ["flat area"]
    analysis.noiseRoi.set([])
    assert "ROI class" in analysis.unavailableReason(image)
    analysis.noiseRoi.set(["flat area"])
    assert analysis.unavailableReason(image) == ""
    result = analysis.run(image, lambda p, m: None)
    assert result.bandCount > 0


def _withBadBands(bands=8) -> VardaRaster:
    image = _image(bands=bands)
    data = np.asarray(image.getData())
    return VardaRaster(
        ArrayDataSource(
            data,
            wavelengths=np.linspace(1000.0, 2600.0, bands),
            wavelengthUnits="nm",
            nodata=-1.0,
            extraMetadata={"bbl": [0, 1, 1, 1, 1, 1, 1, 1][:bands]},
        ),
        name="scene",
    )


@pytest.mark.parametrize("analysisType", [PcaAnalysis, MnfAnalysis, IcaAnalysis])
def test_a_wavelength_range_and_no_bad_bands_go_in(analysisType):
    image = _withBadBands()  # bands at 1000, 1229, ..., 2600 nm; the first is bad
    analysis = analysisType()
    analysis.prepareFor(image)
    assert analysis.wavelengths.get().x == pytest.approx(1000)
    assert analysis.wavelengths.get().y == pytest.approx(2600)
    analysis.wavelengths.set(Vec2(1100, 2200))  # bands 2-6 of 8
    analysis.components.set(3)
    result = analysis.run(image, lambda *_: None)
    info = result.extraMetadata[TRANSFORM_METADATA_KEY]
    np.testing.assert_allclose(
        info["sourceWavelengths"], np.linspace(1000.0, 2600.0, 8)[1:6]
    )
    assert np.asarray(info["inverse"]).shape == (3, 5)


def _structured(bands=8) -> VardaRaster:
    """Smooth, non-Gaussian scene structure under a little noise, with a
    no-data pixel: what MNF and ICA can tell apart from noise and each other."""
    rng = np.random.default_rng(0)
    yy, xx = np.mgrid[0:40, 0:50]
    sources = np.stack(
        [np.tanh(3 * np.sin(xx / 6.0)), np.sin(yy / 7.0) ** 3, np.cos((xx + yy) / 9.0)],
        axis=2,
    ).reshape(-1, 3)
    cube = sources @ rng.normal(size=(3, bands)) + 0.02 * rng.normal(size=(2000, bands))
    cube = cube.reshape(40, 50, bands) + 2.0
    cube[0, 0, :] = -1.0
    return VardaRaster(
        ArrayDataSource(
            cube, wavelengths=np.linspace(1000.0, 2600.0, bands), nodata=-1.0
        ),
        name="scene",
    )


@pytest.mark.parametrize("analysisType", [PcaAnalysis, MnfAnalysis, IcaAnalysis])
def test_every_transform_can_weight_its_values(analysisType):
    image = _structured()
    data = np.asarray(image.getData())
    counts = np.ones(data.shape, np.float32) * 5
    weights = VardaRaster(ArrayDataSource(counts), name="FRAMES USED")
    perPixel = VardaRaster(ArrayDataSource(counts[:, :, :1]), name="ONE")
    other = VardaRaster(ArrayDataSource(counts[:5]), name="SMALL")
    analysis = analysisType()
    analysis.setContext(AnalysisContext(images=[image, weights, perPixel, other]))
    analysis.prepareFor(image)
    assert analysis.weights.get() is None  # unweighted unless chosen
    assert [i.name for i in analysis.weights.imageProvider()] == ["FRAMES USED", "ONE"]
    analysis.components.set(3)
    plain = analysis.run(image, lambda *_: None).getData()
    analysis.weights.set(weights)
    weighted = analysis.run(image, lambda *_: None).getData()
    assert np.isnan(weighted[0, 0]).all()  # the no-data pixel
    good = np.isfinite(plain[..., 0])
    for c in range(3):  # equal weights: the same components, up to sign
        r = [
            abs(np.corrcoef(plain[..., c][good], weighted[..., d][good])[0, 1])
            for d in range(3)
        ]
        assert max(r) > 0.99
    analysis.weights.set(perPixel)
    analysis.weightKind.set(WeightKind.INVERSE_VARIANCE)
    assert analysis.run(image, lambda *_: None).bandCount == 3


def test_there_is_no_separate_weighted_pca():
    from varda.analysis.registry import ANALYSES

    assert "weighted_pca" not in {a.analysisId for a in ANALYSES}


def _twoFlatAreas() -> ROICollection:
    rois = ROICollection()
    rois.addROI(box(2, 2, 10, 10), "dark", Color(1, 0, 0, 0.5), ROIMode.RECTANGLE)
    rois.addROI(box(25, 25, 35, 35), "flat", Color(0, 0, 1, 0.5), ROIMode.RECTANGLE)
    return rois


def test_mnf_noise_comes_from_every_ticked_roi_class(monkeypatch):
    image = generate_random_image((40, 40, 10))
    rois = _twoFlatAreas()
    seen = []

    def capture(cube, valid, method=NoiseEstimate.HORIZONTAL, region=None, w=None):
        seen.append(region)
        return np.eye(cube.shape[2])

    monkeypatch.setattr(transforms, "noiseCovariance", capture)
    analysis = MnfAnalysis()
    analysis.setContext(AnalysisContext(rois=rois))
    analysis.noiseEstimate.set(NoiseEstimate.HOMOGENEOUS_REGION)
    analysis.prepareFor(image)
    analysis.noiseRoi.set(["dark", "flat"])
    analysis.run(image, lambda p, m: None)
    expected = rois.classMask("dark", image) | rois.classMask("flat", image)
    np.testing.assert_array_equal(seen[0], expected)


def test_mnf_records_no_noise_roi_for_a_neighbor_difference_estimate():
    image = generate_random_image((40, 40, 10))
    analysis = MnfAnalysis()
    analysis.setContext(AnalysisContext(rois=_twoFlatAreas()))
    analysis.prepareFor(image)
    assert analysis.noiseEstimate.get() is NoiseEstimate.HORIZONTAL
    assert analysis.noiseRoi.get() == []
    analysis.noiseEstimate.set(NoiseEstimate.HOMOGENEOUS_REGION)
    assert analysis.noiseRoi.get() == ["dark"]  # a class to start from
    analysis.noiseEstimate.set(NoiseEstimate.VERTICAL)
    assert analysis.noiseRoi.get() == []


# --- The wavelength range (release QA #6, #7) --------------------------------


@pytest.mark.parametrize("kind", [PcaAnalysis, MnfAnalysis, IcaAnalysis])
def test_the_same_from_and_to_never_means_every_band(kind):
    """'from 1309 to 1309' ran on all 40 bands, silently."""
    image = _image(bands=40)
    analysis = kind()
    analysis.prepareFor(image)
    analysis.wavelengths.set(Vec2(1309.0, 1309.0))

    reason = analysis.unavailableReason(image)
    assert "at least two" in reason and "1309" in reason
    with pytest.raises(ValueError, match="at least two"):
        analysis.run(image, lambda p, m: None)


def test_a_range_on_one_band_is_refused_too():
    image = _image(bands=40)  # 1000, 1041.0, … nm
    analysis = PcaAnalysis()
    analysis.prepareFor(image)
    analysis.wavelengths.set(Vec2(1041.0, 1041.0))
    assert analysis.keptBands(image).tolist() == [1]
    assert "1 usable band" in analysis.unavailableReason(image)


def test_the_dialog_says_which_bands_will_be_used():
    image = _image(bands=40)
    analysis = PcaAnalysis()
    analysis.prepareFor(image)
    assert analysis.unavailableReason(image) == ""
    assert analysis.inputSummary(image) == "Uses 40 of 40 bands, 1000–2600 nm."
    analysis.wavelengths.set(Vec2(1100.0, 1500.0))
    assert analysis.inputSummary(image) == "Uses 10 of 40 bands, 1123–1492 nm."


def test_the_range_shows_the_images_unit_and_sensible_decimals():
    image = _image(bands=40)
    analysis = PcaAnalysis()
    analysis.prepareFor(image)
    assert analysis.wavelengths.suffix == " nm"
    assert analysis.wavelengths.decimals == 0


def test_rounding_the_range_for_display_never_drops_an_edge_band():
    # Centers that a 1-decimal spin box rounds outwards and inwards
    data = np.random.default_rng(0).normal(size=(10, 10, 4))
    image = VardaRaster(
        ArrayDataSource(data, wavelengths=np.array([500.06, 504.0, 508.0, 512.04])),
        name="edges",
    )
    analysis = PcaAnalysis()
    analysis.prepareFor(image)
    shown = analysis.wavelengths.decimals
    analysis.wavelengths.set(Vec2(round(500.06, shown), round(512.04, shown)))
    assert analysis.keptBands(image).tolist() == [0, 1, 2, 3]


def test_the_weight_settings_say_what_they_do():
    """Release QA #12: 'Weights' and 'Weights Are: Weights (…)' said nothing."""
    analysis = PcaAnalysis()
    assert analysis.weights.name == "Weight Image"
    assert analysis.weightKind.name == "Weight Image Holds"
    assert "counts less" in analysis.weightKind.description
    assert "larger counts more" in WeightKind.WEIGHTS.value
