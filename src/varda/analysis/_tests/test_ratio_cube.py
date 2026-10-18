import numpy as np

from varda.analysis.ratio_cube import (
    applyRatio,
    columnDenominators,
    columnKeys,
    inColumnRunMeans,
)
from varda.image_loading.crism_geometry import ColumnGeometry

ROWS, COLS, BANDS = 20, 6, 8


def _geometry(shift: np.ndarray | None = None) -> ColumnGeometry:
    sample = np.tile(np.arange(COLS, dtype=np.float64), (ROWS, 1))
    if shift is not None:
        sample = sample + shift[:, None]
    return ColumnGeometry(ir_sample=sample)


def _artifactCube(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    artifact = rng.uniform(0.5, 1.5, size=(1, COLS, BANDS))
    scene = rng.uniform(0.8, 1.2, size=(1, 1, BANDS))
    return np.broadcast_to(artifact * scene, (ROWS, COLS, BANDS)).copy()


def test_whole_column_ratio_removes_column_artifacts():
    cube = _artifactCube()
    strip, column = columnKeys(_geometry())
    den = columnDenominators(cube, strip, column, None, minCount=1, radius=0)
    ratio = applyRatio(cube, strip, column, den)
    colMeans = ratio.mean(axis=0)
    assert ratio.dtype == np.float32
    assert colMeans.std(axis=0).max() < 1e-6


def test_keys_follow_the_backplane_not_the_image_column():
    shift = np.where(np.arange(ROWS) % 2 == 1, 1.0, 0.0)
    strip, column = columnKeys(_geometry(shift))
    assert column[0].tolist() == [0, 1, 2, 3, 4, 5]
    assert column[1].tolist() == [1, 2, 3, 4, 5, 6]
    assert (strip == 0).all()


def test_keys_are_minus_one_where_the_backplane_is_missing():
    geometry = _geometry()
    geometry.ir_sample[3, 2] = np.nan
    strip, column = columnKeys(geometry)
    assert strip[3, 2] == -1 and column[3, 2] == -1


def test_strips_are_dense_ids_over_target_and_segment():
    geometry = _geometry()
    geometry.target_id = np.where(np.arange(ROWS) < 10, 7.0, 9.0)[:, None] * np.ones(
        (1, COLS)
    )
    geometry.segment_id = np.ones((ROWS, COLS))
    strip, _ = columnKeys(geometry)
    assert set(np.unique(strip)) == {0, 1}
    assert strip[0, 0] == 0 and strip[15, 0] == 1


def test_run_means_stay_in_column():
    strip, column = columnKeys(_geometry())
    consts = np.arange(COLS, dtype=np.float64)[None, :, None] * np.ones(
        (ROWS, COLS, BANDS)
    )
    np.testing.assert_allclose(inColumnRunMeans(consts, strip, column, 3), consts)
    ramp = np.arange(ROWS, dtype=np.float64)[:, None, None] * np.ones(
        (ROWS, COLS, BANDS)
    )
    out = inColumnRunMeans(ramp, strip, column, 3)
    np.testing.assert_allclose(out[1:-1], ramp[1:-1], atol=1e-6)  # centered mean
    np.testing.assert_allclose(out[0], 1.0, atol=1e-6)  # window rows 0-2
    np.testing.assert_allclose(out[-1], ROWS - 2.0, atol=1e-6)


def test_run_means_ignore_nan():
    strip, column = columnKeys(_geometry())
    cube = np.ones((ROWS, COLS, BANDS))
    cube[5] = np.nan
    out = inColumnRunMeans(cube, strip, column, 3)
    assert np.isfinite(out[5]).all()
    np.testing.assert_allclose(out, 1.0)


def test_bland_only_uses_flagged_pixels():
    cube = np.random.default_rng(1).uniform(size=(ROWS, COLS, BANDS))
    strip, column = columnKeys(_geometry())
    use = np.zeros((ROWS, COLS), dtype=bool)
    use[:10] = True
    den = columnDenominators(cube, strip, column, use, minCount=3, radius=1)
    for c in range(COLS):
        np.testing.assert_allclose(den.spectra[(0, c)], cube[:10, c].mean(axis=0))
        assert den.counts[(0, c)] == 10
    assert den.fallback == {}


def test_short_columns_borrow_from_neighbors_then_whole_column():
    cube = np.random.default_rng(2).uniform(size=(ROWS, COLS, BANDS))
    strip, column = columnKeys(_geometry())
    use = np.zeros((ROWS, COLS), dtype=bool)
    use[:10, [1, 3]] = True
    use[:2, 2] = True
    den = columnDenominators(cube, strip, column, use, minCount=5, radius=1)
    assert den.fallback[(0, 2)] == "neighbors"
    assert den.counts[(0, 2)] >= 5
    den0 = columnDenominators(cube, strip, column, use, minCount=5, radius=0)
    assert den0.fallback[(0, 2)] == "whole column"
    np.testing.assert_allclose(den0.spectra[(0, 2)], cube[:, 2].mean(axis=0))
    assert den0.counts[(0, 2)] == ROWS


def test_no_bland_pixels_anywhere_still_completes():
    cube = _artifactCube()
    strip, column = columnKeys(_geometry())
    use = np.zeros((ROWS, COLS), dtype=bool)
    den = columnDenominators(cube, strip, column, use, minCount=5, radius=2)
    assert set(den.fallback.values()) == {"whole column"}
    assert len(den.fallback) == COLS
    assert np.isfinite(applyRatio(cube, strip, column, den)).all()


def test_apply_ratio_is_nan_without_key_or_denominator():
    cube = _artifactCube()
    geometry = _geometry()
    geometry.ir_sample[0, 0] = np.nan
    strip, column = columnKeys(geometry)
    den = columnDenominators(cube, strip, column, None, minCount=1, radius=0)
    den.spectra[(0, 1)][2] = 0.0
    ratio = applyRatio(cube, strip, column, den)
    assert np.isnan(ratio[0, 0]).all()
    assert np.isnan(ratio[:, 1, 2]).all()
    assert np.isfinite(ratio[:, 1, 3]).all()


def test_run_of_one_is_identity():
    strip, column = columnKeys(_geometry())
    cube = np.random.default_rng(3).uniform(size=(ROWS, COLS, BANDS))
    np.testing.assert_allclose(
        inColumnRunMeans(cube, strip, column, 1), cube, atol=1e-6
    )
