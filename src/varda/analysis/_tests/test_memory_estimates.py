"""Each analysis's memory estimate (checked before it runs) is an upper bound
on what it uses: measured here on a synthetic scene, so an estimate that
falls behind a change to its analysis fails."""

import tracemalloc

import numpy as np
import pytest
from shapely.geometry import box

from varda.analysis.analysis import Analysis, AnalysisContext
from varda.analysis.fatt import WINDOW_MEMORY_BUDGET
from varda.analysis.fatt_map import FattMapAnalysis
from varda.analysis.registry import ANALYSES
from varda.common.entities import Color, VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.rois.roi_collection import ROICollection, ROIMode

ROWS, COLS, BANDS = 60, 80, 40
# These run in the PyTorch worker, or need inputs a plain scene doesn't have
# (a class map, a mask, a transform result, a bit mask, sensor bands)
NOT_MEASURED = {
    "Train CNN on ROIs",
    "Train ViT on ROIs",
    "Apply Mask",
    "Convolve to Sensor Bands",
    "Inverse Transform (Keep N Components)",
    "Decode Bit Mask",
    "Majority/Minority Filter",
    "Sieve Classes",
    "Clump Classes",
    "Accuracy Assessment",
}


def _scene(rows: int = ROWS, cols: int = COLS) -> VardaRaster:
    rng = np.random.default_rng(0)
    cube = (rng.random((rows, cols, BANDS)) * 0.3 + 0.1).astype(np.float32)
    source = ArrayDataSource(
        cube, wavelengths=np.linspace(1000, 2600, BANDS), wavelengthUnits="nm"
    )
    return VardaRaster(source, name="scene")


def _rois() -> ROICollection:
    rois = ROICollection()
    rois.addROI(box(2, 2, 12, 12), "A", Color(1, 0, 0, 1), ROIMode.RECTANGLE)
    rois.addROI(box(30, 20, 40, 30), "B", Color(0, 0, 1, 1), ROIMode.RECTANGLE)
    return rois


MEASURED = [c for c in ANALYSES if c.name not in NOT_MEASURED]


@pytest.mark.parametrize("analysisClass", MEASURED, ids=lambda c: c.name)
def test_the_estimate_bounds_what_the_analysis_uses(analysisClass: type[Analysis]):
    if not analysisClass.isAvailable():  # an optional extra isn't installed
        pytest.skip(f"{analysisClass.name} needs {analysisClass.requirement}")
    # A first run also pays one-time costs (imports, compiled kernels, caches:
    # 20 MiB for GLCM) that don't grow with the image: warm up on a small one
    warmUp = _scene(40, 40)
    analysis = analysisClass()
    analysis.setContext(AnalysisContext(rois=_rois()))
    analysis.prepareFor(warmUp)
    analysis.runAll(warmUp, lambda percent, message: None)

    image = _scene()
    analysis.prepareFor(image)
    tracemalloc.start()
    try:
        analysis.runAll(image, lambda percent, message: None)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= analysis.estimateMemory(image)


def test_unmeasured_analyses_still_estimate_conservatively():
    image = _scene()
    cube = ROWS * COLS * BANDS * 8
    for analysisClass in ANALYSES:
        assert analysisClass().estimateMemory(image) >= cube


def test_the_fatt_map_counts_its_window_budget():
    image = _scene()
    assert FattMapAnalysis().estimateMemory(image) > WINDOW_MEMORY_BUDGET
