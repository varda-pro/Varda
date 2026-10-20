"""Processing → Geometry → Reproject Image (spec §4.3)."""

import tracemalloc
import warnings

import numpy as np
import pytest
from pyproj import CRS

from varda.analysis.analysis import AnalysisContext
from varda.analysis.class_tools import classRaster
from varda.analysis.geometry import ResampleAnalysis
from varda.analysis.registry import ANALYSES
from varda.analysis.reproject import (
    ReprojectAnalysis,
    ReprojectExtent,
    ReprojectTarget,
    ResamplingMethod,
    defaultResampling,
)
from varda.common.entities import VardaRaster
from varda.geo._tests.fixtures import (
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    MARS_EQC_PDS3_WKT,
    makeImage,
)
from varda.geo.reproject import sourceResolution
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.data_sources.rasterio_data_source import RasterioDataSource
from varda.image_loading.raster_writer import writeRaster


def _noProgress(percent: int, message: str) -> None:
    pass


def _analysis(image: VardaRaster, *others: VardaRaster) -> ReprojectAnalysis:
    analysis = ReprojectAnalysis()
    analysis.setContext(AnalysisContext(images=[image, *others]))
    analysis.prepareFor(image)
    return analysis


def _params(crs: CRS) -> dict[str, object]:
    with warnings.catch_warnings():
        # "You will likely lose important projection information": only the
        # parameters are read back
        warnings.simplefilter("ignore", UserWarning)
        return crs.to_dict()


def _ctx(**kwargs) -> VardaRaster:
    return makeImage(CTX_DEM_PROJ, centerLonLat=(77.5, 18.4), pixelSize=20.0, **kwargs)


def test_recommended_polar_for_hirise():
    image = makeImage(HIRISE_POLAR_PROJ, centerLonLat=(0, 85), pixelSize=1)
    analysis = _analysis(image)
    assert analysis.target.value is ReprojectTarget.RECOMMENDED
    result, quality = analysis.runAll(image, _noProgress)
    assert result.crs is not None
    params = _params(result.crs)
    assert params["proj"] == "stere"
    assert params["lat_ts"] == 85.0
    assert "Polar stereographic: the scene reaches" in result.description
    assert quality.bandNames == ["Source pixel distance", "Valid"]


def test_match_another_image_grid():
    image = _ctx(name="ctx")
    other = makeImage(
        MARS_EQC_PDS3_WKT, centerLonLat=(77.52, 18.41), pixelSize=30.0, name="other"
    )
    analysis = _analysis(image, other)
    analysis.target.value = ReprojectTarget.IMAGE
    analysis.grid.set(other)
    result = analysis.run(image, _noProgress)
    assert result.transform == other.transform
    assert (result.width, result.height) == (other.width, other.height)
    assert result.crs is not None and result.crs.equals(other.crs)


def test_other_body_refused():
    image = makeImage(MARS_EQC_PDS3_WKT, centerLonLat=(0, 0), pixelSize=100.0)
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "EPSG:4326"
    with pytest.raises(ValueError) as error:
        analysis.run(image, _noProgress)
    assert "Mars" in str(error.value) and "Earth" in str(error.value)


def test_unreadable_crs_refused():
    image = _ctx()
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "not a coordinate system"
    with pytest.raises(ValueError, match="coordinate system"):
        analysis.run(image, _noProgress)


def test_missing_image_refused():
    image = _ctx()
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.IMAGE
    with pytest.raises(ValueError, match="Image"):
        analysis.run(image, _noProgress)


def test_class_map_defaults_to_nearest_and_stays_a_class_map():
    base = _ctx()
    classes = np.random.default_rng(1).integers(0, 3, (64, 64)).astype(np.float32)
    classes[:4] = np.nan
    image = classRaster(
        base,
        classes,
        ["a", "b", "c"],
        ["#ff0000", "#00ff00", "#0000ff"],
        name="classes",
        description="a class map",
    )
    assert defaultResampling(image) is ResamplingMethod.NEAREST
    analysis = _analysis(image)
    assert analysis.resampling.value is ResamplingMethod.NEAREST
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "+proj=eqc +lat_ts=0 +lon_0=77 +R=3396190 +units=m"
    result = analysis.run(image, _noProgress)
    values = result.getData()[:, :, 0]
    assert set(np.unique(values[np.isfinite(values)])) <= {0.0, 1.0, 2.0}
    assert result.bandNames == ["Class"]
    assert result.extraMetadata["classes"] == ["a", "b", "c"]
    assert result.extraMetadata["classColors"] == ["#ff0000", "#00ff00", "#0000ff"]


def test_default_resampling():
    assert defaultResampling(_ctx(bands=3)) is ResamplingMethod.BILINEAR
    image = _ctx()
    mask = VardaRaster(
        ArrayDataSource(
            np.zeros((8, 8, 1), np.uint8), transform=image.transform, crs=image.crs
        ),
        name="mask",
    )
    assert defaultResampling(mask) is ResamplingMethod.NEAREST


def test_equivalent_target_copies():
    image = makeImage(MARS_EQC_PDS3_WKT, centerLonLat=(10, 5), pixelSize=200.0)
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "IAU_2015:49910"
    analysis.resolution.value = 0.0
    result = analysis.run(image, _noProgress)
    assert np.array_equal(result.getData(), image.getData())
    assert result.transform == image.transform
    assert "nothing resampled" in result.description
    record = result.extraMetadata["reprojection"]
    assert record["copied"] is True
    assert record["method"] == "none (pixels copied)"
    assert record["coordinateOperation"] is None


def test_quality_image():
    image = _ctx()
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "+proj=stere +lat_0=90 +lon_0=0 +R=3396190 +units=m"
    result, quality = analysis.runAll(image, _noProgress)
    assert quality.name == f"{image.name} reprojection quality"
    assert quality.bandNames == ["Source pixel distance", "Valid"]
    data, cube = quality.getData(), result.getData()
    valid = data[:, :, 1]
    assert set(np.unique(valid)) == {0.0, 1.0}
    assert np.array_equal(valid == 0, np.isnan(cube[:, :, 0]))
    distance = data[:, :, 0]
    inside = np.isfinite(distance)
    assert inside.any() and np.nanmax(distance) <= np.sqrt(2) / 2 + 1e-6


def test_no_quality_bands():
    image = _ctx()
    analysis = _analysis(image)
    analysis.qualityBands.value = False
    assert len(analysis.runAll(image, _noProgress)) == 1


def test_another_images_extent():
    image = _ctx()
    other = _ctx(width=16, height=16, name="small")
    analysis = _analysis(image, other)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "+proj=eqc +lat_ts=0 +lon_0=77 +R=3396190 +units=m"
    analysis.extent.value = ReprojectExtent.OTHER
    analysis.grid.set(other)
    small = analysis.run(image, _noProgress)
    analysis.extent.value = ReprojectExtent.WHOLE
    whole = analysis.run(image, _noProgress)
    assert small.width * small.height < whole.width * whole.height / 4


def test_source_no_data():
    image = _ctx()
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "+proj=eqc +lat_ts=0 +lon_0=77 +R=3396190 +units=m"
    analysis.resampling.value = ResamplingMethod.NEAREST
    analysis.sourceNoData.value = "nonsense"
    with pytest.raises(ValueError, match="Source No-Data"):
        analysis.run(image, _noProgress)


def test_provenance_keeps_reprojection(tmp_path):
    image = _ctx()
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "+proj=eqc +lat_ts=0 +lon_0=77 +R=3396190 +units=m"
    result = analysis.run(image, _noProgress)
    record = result.extraMetadata["reprojection"]
    assert record["method"] == "bilinear" and record["copied"] is False
    path = writeRaster(result, tmp_path / "reprojected.tif")
    reread = VardaRaster(RasterioDataSource(str(path)), name="reprojected")
    assert reread.extraMetadata["reprojection"] == record


def test_unavailable_without_map_coordinates():
    image = VardaRaster(ArrayDataSource(np.zeros((8, 8, 1), np.float32)), name="plain")
    assert "Image Properties" in ReprojectAnalysis().unavailableReason(image)


@pytest.mark.parametrize("upsample", [1.0, 3.0, 10.0])
def test_memory_estimate_bounds_peak(upsample):
    # big enough that the size-dependent terms, not the fixed 8 MiB, dominate
    image = _ctx(width=200, height=200, bands=10)
    assert image.crs is not None
    analysis = _analysis(image)
    analysis.target.value = ReprojectTarget.CRS
    analysis.crsText.value = "+proj=eqc +lat_ts=0 +lon_0=77 +R=3396190 +units=m"
    analysis.resolution.value = (
        sourceResolution(image.crs, image.transform, 200, 200) / upsample
    )
    analysis.runAll(image, _noProgress)  # warm up: imports, PROJ caches
    tracemalloc.start()
    try:
        analysis.runAll(image, _noProgress)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak <= analysis.estimateMemory(image)


def test_registered_after_resample():
    assert ANALYSES.index(ReprojectAnalysis) == ANALYSES.index(ResampleAnalysis) + 1
