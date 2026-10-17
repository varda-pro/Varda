"""Factor analysis target transformation finds the minerals mixed into a
scene, and only where they are."""

import tracemalloc

import numpy as np
import pytest

from varda.analysis.fatt import (
    FactorMethod,
    analyzeFactors,
    fitLibrary,
    hysime,
    indicatorFunction,
    normalizedRmse,
    sampleSpectra,
    targetTransform,
    windowedNrmse,
)
from varda.analysis.spectral_library import LibrarySpectrum

WAVELENGTHS = np.linspace(1700.0, 2600.0, 133)


def _spectrum(continuum: float, *bands: tuple[float, float, float]) -> np.ndarray:
    """A sloped continuum with Gaussian absorptions (center, width, depth)."""
    s = continuum + 0.05 * (WAVELENGTHS - 1700.0) / 900.0
    for center, width, depth in bands:
        s = s * (1 - depth * np.exp(-0.5 * ((WAVELENGTHS - center) / width) ** 2))
    return s


ENDMEMBERS = {
    "kaolinite": _spectrum(0.45, (2165, 12, 0.25), (2205, 14, 0.35)),
    "nontronite": _spectrum(0.35, (2285, 18, 0.3), (2400, 25, 0.15)),
    "olivine": _spectrum(0.25),
}
DECOYS = {
    "calcite": _spectrum(0.4, (2340, 22, 0.35)),
    "gypsum": _spectrum(0.5, (1750, 15, 0.2), (2215, 20, 0.2), (2265, 18, 0.2)),
}


def _library(spectra: dict[str, np.ndarray]) -> list[LibrarySpectrum]:
    return [LibrarySpectrum(name, WAVELENGTHS, s) for name, s in spectra.items()]


def _mixtures(count: int, seed: int = 0, noise: float = 5e-4) -> np.ndarray:
    rng = np.random.default_rng(seed)
    abundances = rng.dirichlet(np.ones(len(ENDMEMBERS)), size=count)
    clean = abundances @ np.stack(list(ENDMEMBERS.values()))
    return clean + rng.normal(scale=noise, size=clean.shape)


def test_target_transform_reproduces_a_component_and_not_a_stranger():
    factors = analyzeFactors(_mixtures(500), FactorMethod.COVARIANCE)
    basis = factors.basis(3)
    inScene = targetTransform(basis, ENDMEMBERS["kaolinite"])
    stranger = targetTransform(basis, DECOYS["calcite"])
    assert normalizedRmse(ENDMEMBERS["kaolinite"], inScene) < 1e-5
    assert normalizedRmse(DECOYS["calcite"], stranger) > 1e-4


def test_target_transform_fits_the_bands_the_target_covers():
    basis = analyzeFactors(_mixtures(500), FactorMethod.COVARIANCE).basis(3)
    target = ENDMEMBERS["nontronite"].copy()
    target[:20] = np.nan  # e.g. a library that starts later
    fit = targetTransform(basis, target)
    assert np.isfinite(fit).all()
    assert normalizedRmse(target, fit) < 1e-5


def test_the_indicator_function_is_smallest_at_the_number_of_components():
    ind = indicatorFunction(_mixtures(2000))
    # ind[n - 1] is IND(n); about the origin the mixtures have rank 3
    assert int(np.argmin(ind)) + 1 == 3


def test_hysime_finds_the_signal_subspace():
    _, _, count = hysime(_mixtures(2000))
    assert count == 3


@pytest.mark.parametrize("method", list(FactorMethod))
def test_library_ranking_puts_the_scene_minerals_first(method):
    factors = analyzeFactors(_mixtures(1000), method)
    fits = fitLibrary(
        factors.basis(factors.suggested), WAVELENGTHS, _library(ENDMEMBERS | DECOYS)
    )
    ranked = [f.spectrum.name for f in fits]
    assert set(ranked[:3]) == set(ENDMEMBERS)
    assert fits[0].nrmse <= fits[-1].nrmse


def test_library_spectra_outside_the_image_bands_are_skipped():
    elsewhere = LibrarySpectrum("visible", np.linspace(400, 700, 50), np.ones(50))
    basis = analyzeFactors(_mixtures(200), FactorMethod.COVARIANCE).basis(3)
    fits = fitLibrary(basis, WAVELENGTHS, [elsewhere, *_library(ENDMEMBERS)])
    assert "visible" not in [f.spectrum.name for f in fits]


def test_windowed_fatt_finds_a_mineral_only_where_it_is():
    """DAFA/TT: kaolinite is mixed in only in the top-left block."""
    rng = np.random.default_rng(1)
    rows, cols = 24, 24
    background = np.stack([ENDMEMBERS["nontronite"], ENDMEMBERS["olivine"]])
    abundances = rng.dirichlet(np.ones(2), size=(rows, cols))
    cube = abundances @ background
    patch = rng.uniform(0.2, 0.6, size=(10, 10, 1))
    cube[:10, :10] = (1 - patch) * cube[:10, :10] + patch * ENDMEMBERS["kaolinite"]
    cube += rng.normal(scale=2e-4, size=cube.shape)

    targets = np.stack([ENDMEMBERS["kaolinite"], DECOYS["calcite"]])
    nrmse = windowedNrmse(cube, targets, factors=4, shapes=[(5, 5), (4, 6), (6, 4)])

    assert nrmse.shape == (2, rows, cols)
    threshold = 1e-5
    kaolinite = nrmse[0] < threshold
    assert kaolinite[2:8, 2:8].all()  # inside the patch
    assert not kaolinite[14:, 14:].any()  # far from it
    assert not (nrmse[1] < threshold).any()  # calcite is nowhere


def test_windowed_fatt_leaves_windows_with_missing_pixels_out():
    cube = _mixtures(144).reshape(12, 12, -1)
    cube[0, 0] = np.nan
    nrmse = windowedNrmse(
        cube, ENDMEMBERS["kaolinite"][None], factors=3, shapes=[(3, 3)]
    )
    assert np.isnan(nrmse[0, 0, 0])  # only windows containing the NaN cover it
    assert np.isfinite(nrmse[0, 6, 6])


def _windowedCase(rows: int, cols: int, bands: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(7)
    cube = rng.random((rows, cols, bands)) + 0.1
    cube[3, 5] = np.nan  # a window with a missing pixel is left out
    return cube, rng.random((2, bands)) + 0.1


def test_windowed_fatt_memory_stays_within_its_budget():
    """The windows were all copied into one array before any was fitted:
    (windows x bands x pixels per window) doubles, about 15 GiB per window
    shape for a CRISM scene, which crashed a 16 GB laptop. Work now goes in
    chunks sized to the budget, whatever the image size."""
    cube, targets = _windowedCase(100, 120, 60)
    budget = 32 * 2**20
    tracemalloc.start()
    windowedNrmse(cube, targets, factors=5, shapes=[(7, 7)], memoryBudget=budget)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    # the budget, plus results and a float64 copy of the cube (under 10 MiB)
    assert peak < budget + 16 * 2**20


def test_windowed_fatt_results_do_not_depend_on_the_budget():
    cube, targets = _windowedCase(30, 40, 20)
    shapes = [(7, 7), (5, 10), (10, 5)]
    roomy = windowedNrmse(cube, targets, factors=5, shapes=shapes)
    tight = windowedNrmse(
        cube, targets, factors=5, shapes=shapes, memoryBudget=256 * 2**10
    )
    np.testing.assert_array_equal(roomy, tight)


def test_spectra_are_sampled_from_usable_pixels_on_a_grid():
    cube = np.arange(6 * 6 * 2, dtype=float).reshape(6, 6, 2)
    usable = np.ones((6, 6), bool)
    usable[0, 0] = False
    cube[2, 2, 1] = np.nan
    spectra = sampleSpectra(cube, usable, stride=2)
    # (0,2) (0,4) (2,0) (2,4) (4,0) (4,2) (4,4): not the unusable or NaN pixel
    assert spectra.shape == (7, 2)
