"""A result's provenance record and scientific metadata travel with its files:
written in the VARDA metadata domain (inside a GeoTIFF, in the .aux.xml beside
an ENVI file) and read back on open. Files without it open as before."""

import json

import numpy as np
import pytest
import rasterio

from varda.analysis.band_math import BandMathAnalysis
from varda.analysis.provenance import recordProvenance
from varda.analysis.transforms import InverseTransformAnalysis, PcaAnalysis
from varda.common.entities import VardaRaster
from varda.common.provenance import (
    PROVENANCE_KEY,
    SOURCE_SUFFIX,
    VARDA_NAMESPACE,
    makeProvenance,
)
from varda.geo._tests.fixtures import writeWgs84
from varda.geo.equivalence import sameCrs
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import loadRaster, openDataSource
from varda.image_loading.raster_writer import writeRaster


def _image(name="scene", path="/data/scene.img") -> VardaRaster:
    rng = np.random.default_rng(0)
    data = rng.random((6, 7, 5)).astype(np.float32) + 0.1
    source = ArrayDataSource(
        data,
        wavelengths=np.linspace(2000, 2400, 5),
        wavelengthUnits="nm",
        filePath=path,
    )
    return VardaRaster(source, name=name)


def _bandMath(image: VardaRaster, expression: str) -> VardaRaster:
    analysis = BandMathAnalysis()
    analysis.expression.set(expression)
    result = analysis.run(image, lambda p, m: None)
    recordProvenance(result, analysis, image)
    return result


@pytest.mark.parametrize("suffix", [".img", ".tif"])
def test_a_nested_record_round_trips(tmp_path, suffix):
    first = _bandMath(_image(), "b2 - b1")
    second = _bandMath(first, "b1 * 2")
    written = writeRaster(second, tmp_path / f"second{suffix}")

    reopened = openDataSource(str(written))

    record = reopened.extraMetadata[PROVENANCE_KEY]
    assert record == json.loads(json.dumps(second.extraMetadata[PROVENANCE_KEY]))
    inner = record["inputs"][0][PROVENANCE_KEY]
    assert inner["parameters"] == {"Expression": "b2 - b1"}
    assert inner["inputs"][0]["path"] == "/data/scene.img"


def test_envi_keeps_the_record_in_the_aux_xml(tmp_path):
    writeRaster(_bandMath(_image(), "b1"), tmp_path / "r.img")
    aux = tmp_path / "r.img.aux.xml"
    assert aux.exists() and VARDA_NAMESPACE in aux.read_text()


def test_chained_in_memory_results_keep_the_inner_record():
    first = _bandMath(_image(), "b2 - b1")
    second = _bandMath(first, "b1 * 2")
    record = second.extraMetadata[PROVENANCE_KEY]
    assert record["schema"] == 1
    assert record["process"] == "Band Math" and record["processId"] == "band_math"
    assert record["inputs"][0]["name"] == first.name
    assert record["inputs"][0][PROVENANCE_KEY] == first.extraMetadata[PROVENANCE_KEY]


def test_a_reopened_transform_keeps_what_inverse_transform_needs(tmp_path):
    image = _image()
    pca = PcaAnalysis()
    pca.prepareFor(image)
    components = pca.run(image, lambda p, m: None)
    recordProvenance(components, pca, image)
    written = writeRaster(components, tmp_path / "pca.img")

    reopened = loadRaster(str(written))

    assert reopened.extraMetadata["explainedVariance"] == pytest.approx(
        components.extraMetadata["explainedVariance"]
    )
    assert reopened.extraMetadata["transform"]["eigenvalues"] == pytest.approx(
        components.extraMetadata["transform"]["eigenvalues"]
    )
    inverse = InverseTransformAnalysis()
    assert inverse.unavailableReason(reopened) == ""
    inverse.prepareFor(reopened)
    rebuilt = inverse.run(reopened, lambda p, m: None)
    np.testing.assert_allclose(rebuilt.getData(), image.getData(), rtol=1e-3, atol=1e-3)


def test_class_names_and_colors_survive_a_geotiff(tmp_path):
    source = ArrayDataSource(
        np.array([[[0.0], [1.0]]], dtype=np.float32),
        wavelengths=np.array(["Class"]),
        wavelengthUnits="classes",
        bandNames=["Class"],
        extraMetadata={
            "classes": ["rock", "dust"],
            "classColors": ["#ff0000", "#00ff00"],
        },
    )
    written = writeRaster(VardaRaster(source, name="classes"), tmp_path / "c.tif")
    metadata = openDataSource(str(written)).extraMetadata
    assert metadata["classes"] == ["rock", "dust"]
    assert metadata["classColors"] == ["#ff0000", "#00ff00"]


@pytest.mark.parametrize("driver,name", [("GTiff", "plain.tif"), ("ENVI", "plain.img")])
def test_files_without_the_varda_domain_open_unchanged(tmp_path, driver, name):
    path = tmp_path / name
    with rasterio.open(
        path, "w", driver=driver, height=2, width=2, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((1, 2, 2), np.float32))
    metadata = openDataSource(str(path)).extraMetadata
    assert PROVENANCE_KEY not in metadata
    assert not {"classes", "transform", "explainedVariance"} & set(metadata)
    if driver == "GTiff":
        assert metadata == {}


def test_a_download_record_beside_a_file_becomes_its_source(tmp_path):
    path = tmp_path / "product.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=2, width=2, count=1, dtype="float32"
    ) as dst:
        dst.write(np.zeros((1, 2, 2), np.float32))
    source = {"catalog": "PDS ODE", "product": "FRT0001", "url": "https://x/p.tif"}
    (tmp_path / f"product.tif{SOURCE_SUFFIX}").write_text(json.dumps(source))

    image = loadRaster(str(path))
    assert image.extraMetadata[PROVENANCE_KEY]["source"] == source

    result = _bandMath(image, "b1")
    assert (
        result.extraMetadata[PROVENANCE_KEY]["inputs"][0][PROVENANCE_KEY]["source"]
        == source
    )


def test_a_record_names_every_input_and_the_software():
    record = makeProvenance(
        "Test", {"n": np.int64(3)}, [_image()], software={"spectral": "0.24"}
    )
    assert record["parameters"] == {"n": 3}
    assert record["software"] == {"spectral": "0.24"}
    assert record["inputs"] == [{"name": "scene", "path": "/data/scene.img"}]


@pytest.mark.parametrize(
    "key", ["sensorBands", "kmeans", "componentSelection", "unmixing", "iovf"]
)
def test_quality_figures_are_kept_in_files(tmp_path, key):
    """Analyses' quality figures (coverage, convergence, cluster statistics)
    survive export, so a reopened result can still be reported on."""
    figures = {"coverage": {"B1": 0.97}, "converged": False, "iterations": [3, 1]}
    source = ArrayDataSource(
        np.ones((3, 4, 2), np.float32),
        wavelengths=np.array([500.0, 600.0]),
        wavelengthUnits="nm",
        extraMetadata={key: figures},
    )
    written = writeRaster(VardaRaster(source, name="r"), tmp_path / "r.tif")
    assert openDataSource(str(written)).extraMetadata[key] == figures


def test_a_corrected_crs_is_written_and_recorded(tmp_path):
    scene = writeWgs84(tmp_path)  # Earth degrees, with a PDS3 label for Mars
    fixed = loadRaster(str(scene))
    written = writeRaster(_bandMath(fixed, "b2 - b1"), tmp_path / "result.tif")

    reopened = openDataSource(str(written))

    assert reopened.crs is not None and sameCrs(reopened.crs, "IAU_2015:49900")
    entry = reopened.extraMetadata[PROVENANCE_KEY]["inputs"][0]
    assert entry["crsFix"] == fixed.extraMetadata["crsFix"]
    assert entry["crsFix"]["automatic"] is True
    assert entry["labelHints"]["target"] == "Mars"


def test_an_exported_corrected_image_keeps_its_record(tmp_path):
    fixed = loadRaster(str(writeWgs84(tmp_path)))
    written = writeRaster(fixed, tmp_path / "copy.tif")
    reopened = loadRaster(str(written))
    assert reopened.crs is not None and sameCrs(reopened.crs, "IAU_2015:49900")
    assert reopened.extraMetadata["crsFix"] == fixed.extraMetadata["crsFix"]
    assert reopened.metadataOverrides == {}  # nothing left to fix
