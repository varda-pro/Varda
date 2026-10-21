"""Every analysis result records how it was made, and keeps it on export."""

import json

import numpy as np

from varda.analysis.band_math import BandMathAnalysis
from varda.analysis.detection import RxAnalysis
from varda.analysis.provenance import PROVENANCE_KEY, provenanceText, recordProvenance
from varda.analysis.runner import AnalysisRunner
from varda.analysis.transforms import PcaAnalysis, WeightKind
from varda.common.di_types import ProjectImages
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import openDataSource
from varda.version import vardaVersion


def _image() -> VardaRaster:
    data = np.random.default_rng(0).random((6, 7, 5)).astype(np.float32) + 0.1
    source = ArrayDataSource(
        data,
        wavelengths=np.linspace(2000, 2400, 5),
        wavelengthUnits="nm",
        filePath="/data/scene.img",
    )
    return VardaRaster(source, name="scene")


def test_a_result_records_the_analysis_parameters_inputs_and_version():
    analysis = BandMathAnalysis()
    analysis.expression.set("R2300 / R2200")
    image = _image()
    result = analysis.run(image, lambda p, m: None)
    recordProvenance(result, analysis, image)
    record = result.extraMetadata[PROVENANCE_KEY]
    assert record["process"] == "Band Math"
    assert record["processId"] == "band_math"
    assert record["parameters"] == {"Expression": "R2300 / R2200"}
    assert record["inputs"] == [{"name": "scene", "path": "/data/scene.img"}]
    assert record["vardaVersion"] == vardaVersion()
    json.dumps(record)  # plain data, ready to save


def test_the_runner_records_provenance_before_saving(tmp_path, qtbot):
    analysis = BandMathAnalysis()
    analysis.expression.set("b2 - b1")
    image = _image()
    runner = AnalysisRunner(ProjectImages([image]))
    with qtbot.waitSignal(runner.sigFinished, timeout=10_000) as blocker:
        runner.run(analysis, image, str(tmp_path / "diff.img"))
    assert PROVENANCE_KEY in blocker.args[0].extraMetadata
    description = openDataSource(str(tmp_path / "diff.img")).description
    assert (
        "Band Math" in description
        and "b2 - b1" in description
        and vardaVersion() in description
    )


def test_provenance_text_reads_like_a_sentence():
    analysis = BandMathAnalysis()
    analysis.expression.set("b1")
    image = _image()
    result = analysis.run(image, lambda p, m: None)
    recordProvenance(result, analysis, image)
    text = provenanceText(result)
    assert text.startswith(
        f"Made with Varda {vardaVersion()}: Band Math (Expression=b1) from /data/scene.img"
    )


class _PluginBandMath(BandMathAnalysis):
    plugin = "Ratios 1.2"  # as the plugin manager sets it


def test_provenance_text_names_the_plugin():
    analysis = _PluginBandMath()
    analysis.expression.set("b1")
    image = _image()
    result = analysis.run(image, lambda p, m: None)
    recordProvenance(result, analysis, image)
    assert result.extraMetadata[PROVENANCE_KEY]["plugin"] == "Ratios 1.2"
    assert provenanceText(result).endswith("by the plugin Ratios 1.2")


def test_the_software_that_did_the_work_is_recorded():
    image = _image()
    analysis = RxAnalysis()
    result = analysis.run(image, lambda p, m: None)
    recordProvenance(result, analysis, image)
    software = result.extraMetadata[PROVENANCE_KEY]["software"]
    assert set(software) == {"spectral"}


def test_long_parameter_values_are_shortened_in_the_one_line_text():
    analysis = BandMathAnalysis()
    analysis.expression.set("b1 + " * 40 + "b1")
    image = _image()
    result = analysis.run(image, lambda p, m: None)
    recordProvenance(result, analysis, image)
    assert "…" in provenanceText(result)


def test_a_range_records_its_units_and_an_enum_its_label(tmp_path):
    """Release QA 0.12.0 #6: the exported record said ``"Wavelengths": {"x":
    1000.0, "y": 2600.0}`` and ``"Weight Image Holds": "WEIGHTS"``, which a
    methods section can't quote."""
    image = _image()  # 2000-2400 nm
    analysis = PcaAnalysis()
    analysis.prepareFor(image)
    result = analysis.run(image, lambda p, m: None)
    recordProvenance(result, analysis, image)
    parameters = result.extraMetadata[PROVENANCE_KEY]["parameters"]
    assert parameters["Wavelengths"] == {"from": 2000.0, "to": 2400.0, "units": "nm"}
    assert parameters["Weight Image Holds"] == WeightKind.WEIGHTS.value
    json.dumps(parameters)
    text = provenanceText(result)
    assert "Wavelengths=2000–2400 nm" in text
    # the one-line summary shortens long values; the record keeps them whole
    assert "Weight Image Holds=Each pixel's weight" in text
