"""The FATT detection map (DAFA/TT) marks where a library target fits."""

import numpy as np
import pytest

from varda.analysis.analysis import AnalysisContext
from varda.analysis.fatt_map import FattMapAnalysis
from varda.analysis.spectral_library import LibrarySpectrum
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource

WAVELENGTHS = np.linspace(1000.0, 2600.0, 160)


def _spectrum(continuum: float, *bands: tuple[float, float, float]) -> np.ndarray:
    s = continuum + 0.05 * (WAVELENGTHS - 1000.0) / 1600.0
    for center, width, depth in bands:
        s = s * (1 - depth * np.exp(-0.5 * ((WAVELENGTHS - center) / width) ** 2))
    return s


KAOLINITE = _spectrum(0.45, (2165, 12, 0.25), (2205, 14, 0.35))
CALCITE = _spectrum(0.4, (2340, 22, 0.35))


def _scene(dark: tuple[slice, slice] | None = None) -> VardaRaster:
    rng = np.random.default_rng(3)
    background = np.stack([_spectrum(0.35, (2285, 18, 0.3)), _spectrum(0.25)])
    cube = rng.dirichlet(np.ones(2), size=(30, 30)) @ background
    patch = rng.uniform(0.2, 0.6, size=(12, 12, 1))
    cube[:12, :12] = (1 - patch) * cube[:12, :12] + patch * KAOLINITE
    cube += rng.normal(scale=2e-4, size=cube.shape)
    if dark is not None:
        cube[dark] *= 0.01  # a shadow
    cube[29, 29] = -1.0  # no-data
    source = ArrayDataSource(
        cube.astype(np.float32),
        wavelengths=WAVELENGTHS,
        wavelengthUnits="nm",
        nodata=-1.0,
    )
    return VardaRaster(source, name="scene")


def _analysis() -> FattMapAnalysis:
    analysis = FattMapAnalysis(
        [
            LibrarySpectrum("kaolinite", WAVELENGTHS, KAOLINITE),
            LibrarySpectrum("calcite", WAVELENGTHS, CALCITE),
        ]
    )
    analysis.factors.set(4)
    analysis.threshold.set(0.1)  # × 10⁻⁴; the synthetic fits are very clean
    return analysis


def test_the_map_counts_detections_and_keeps_each_targets_fit():
    result = _analysis().run(_scene(), lambda p, m: None)

    assert result.bandNames == [
        "Targets detected",
        "nRMSE: kaolinite",
        "nRMSE: calcite",
        "Mean signal",
    ]
    data = result.getData()
    assert (data[3:9, 3:9, 0] == 1).all()  # kaolinite, inside the patch
    assert (data[20:28, 20:28, 0] == 0).all()  # nothing far from it
    assert np.isnan(data[29, 29]).all()  # no-data stays missing
    assert result.extraMetadata["fatt"]["targets"] == ["kaolinite", "calcite"]


def test_only_the_wavelength_range_is_used():
    analysis = _analysis()
    analysis.minWavelength.set(2100.0)
    analysis.maxWavelength.set(2600.0)
    result = analysis.run(_scene(), lambda p, m: None)
    inRange = int(((WAVELENGTHS >= 2100) & (WAVELENGTHS <= 2600)).sum())
    assert result.extraMetadata["fatt"]["bands"] == [inRange, inRange]


def test_targets_are_needed():
    analysis = FattMapAnalysis()
    assert "target" in analysis.unavailableReason(_scene()).lower()
    with pytest.raises(ValueError):
        analysis.run(_scene(), lambda p, m: None)


def test_each_targets_map_uses_the_bands_it_covers():
    """Ticking a target that covers fewer bands must not change another
    target's map."""
    kaolinite = LibrarySpectrum("kaolinite", WAVELENGTHS, KAOLINITE)
    short = WAVELENGTHS >= 2000
    calcite = LibrarySpectrum("calcite", WAVELENGTHS[short], CALCITE[short])

    alone = FattMapAnalysis([kaolinite])
    both = FattMapAnalysis([kaolinite, calcite])
    for analysis in (alone, both):
        analysis.factors.set(4)
        analysis.threshold.set(0.1)
    image = _scene()
    aloneResult = alone.run(image, lambda p, m: None)
    bothResult = both.run(image, lambda p, m: None)

    np.testing.assert_array_equal(
        aloneResult.getData()[:, :, 1], bothResult.getData()[:, :, 1]
    )
    inRange = (WAVELENGTHS >= 1700) & (WAVELENGTHS <= 2600)  # the default range
    assert bothResult.extraMetadata["fatt"]["bands"] == [
        int(inRange.sum()),
        int((inRange & short).sum()),
    ]


def test_a_target_covering_too_few_bands_is_named():
    tiny = LibrarySpectrum("tiny", WAVELENGTHS[:3], KAOLINITE[:3])
    analysis = FattMapAnalysis(
        [LibrarySpectrum("kaolinite", WAVELENGTHS, KAOLINITE), tiny]
    )
    with pytest.raises(ValueError, match="tiny"):
        analysis.run(_scene(), lambda p, m: None)


QUIET = lambda p, m: None  # noqa: E731
BLOCK = (slice(18, 22), slice(14, 18))


def _band(values: np.ndarray, name: str) -> VardaRaster:
    return VardaRaster(
        ArrayDataSource(values.astype(np.float32)[:, :, None], nodata=None),
        name=name,
    )


def test_excluded_pixels_are_no_data_before_windowing():
    """Pixels set in the Exclude mask give the same map as if they had no
    data: NaN there, and every window touching them is skipped."""
    image = _scene()
    mask = np.zeros((30, 30))
    mask[BLOCK] = 1.0
    mask[0, 29] = np.nan  # no data in the mask has no say
    maskImage = _band(mask, "shadow")

    analysis = _analysis()
    analysis.setContext(AnalysisContext(images=[image, maskImage]))
    analysis.prepareFor(image)
    analysis.exclude.set(maskImage)
    maskedResult = analysis.run(image, QUIET)
    masked = maskedResult.getData()

    reference = np.asarray(image.getData(), dtype=np.float64).copy()
    reference[BLOCK] = -1.0  # the no-data value
    referenceImage = VardaRaster(
        ArrayDataSource(
            reference.astype(np.float32),
            wavelengths=WAVELENGTHS,
            wavelengthUnits="nm",
            nodata=-1.0,
        ),
        name="reference",
    )
    expected = _analysis().run(referenceImage, QUIET).getData()

    assert np.isnan(masked[BLOCK][..., :3]).all()
    np.testing.assert_array_equal(masked[..., :3], expected[..., :3])
    assert np.isfinite(masked[0, 29, :3]).all()
    # a pixel next to the block keeps only windows that don't touch it
    full = _analysis().run(image, QUIET).getData()
    assert masked[17, 15, 1] >= full[17, 15, 1]
    record = maskedResult.extraMetadata["fatt"]
    assert record["excludedByMask"] == 16
    assert record["exclude"] == "shadow"


def test_dim_pixels_are_left_out_and_the_mean_signal_is_kept():
    image = _scene(dark=BLOCK)
    analysis = _analysis()
    analysis.minimumSignal.set(0.05)
    result = analysis.run(image, QUIET)
    data = result.getData()

    assert np.isnan(data[BLOCK][..., :3]).all()
    assert np.isfinite(data[3:9, 3:9, :3]).all()  # bright ground is mapped
    record = result.extraMetadata["fatt"]
    assert record["excludedBySignal"] == 16
    assert record["excludedPixels"] == 16
    assert record["minimumSignal"] == 0.05
    assert "16 px with a mean signal below 0.05" in result.description

    cube = np.asarray(image.getData(), dtype=np.float64)
    fitted = (WAVELENGTHS >= 1700) & (WAVELENGTHS <= 2600)
    expected = cube[:, :, fitted].mean(axis=2)
    np.testing.assert_allclose(data[:28, :28, -1], expected[:28, :28], rtol=1e-5)
    assert np.isnan(data[29, 29, -1])  # no data


def test_nothing_is_left_out_by_default():
    result = _analysis().run(_scene(dark=BLOCK), QUIET)
    record = result.extraMetadata["fatt"]
    assert record["excludedPixels"] == 0 and record["exclude"] is None
    assert np.isfinite(result.getData()[BLOCK][..., 1]).all()
