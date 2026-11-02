import shutil
import textwrap
from pathlib import Path

import numpy as np

from varda.api import ArrayDataSource, VardaRaster
from varda.kindling import checks
from varda.kindling.checks import CheckReport, runChecks, scanForWarnings
from varda.kindling.docs import EXAMPLES

MANIFEST = '[plugin]\nname = "under-test"\nversion = "0.1.0"\n'


def _plugin(tmp_path: Path, code: str) -> Path:
    folder = tmp_path / "plugin"
    folder.mkdir()
    (folder / "plugin.toml").write_text(MANIFEST)
    (folder / "plugin.py").write_text(textwrap.dedent(code))
    return folder


def _image() -> VardaRaster:
    wavelengths = np.linspace(1000.0, 2500.0, 40)
    cube = np.random.default_rng(1).uniform(0.1, 0.5, (70, 90, 40))
    return VardaRaster(
        ArrayDataSource(cube, wavelengths=wavelengths, wavelengthUnits="Nanometers"),
        name="scene",
    )


def test_the_example_passes_on_the_users_image(tmp_path):
    folder = tmp_path / "band-depth"
    shutil.copytree(
        EXAMPLES / "band-depth", folder, ignore=shutil.ignore_patterns("__pycache__")
    )
    report = runChecks(folder, _image())
    assert report.passed, report.describe()
    runs = report.analyses[0].runs
    assert [run.image for run in runs] == ["synthetic", "your image"]
    assert runs[1].shape[:2] == (checks.CROP_SIZE, checks.CROP_SIZE)
    assert report.testsRan and report.testsOk


def test_a_plugin_that_fails_to_import_is_reported(tmp_path):
    report = runChecks(
        _plugin(tmp_path, "raise RuntimeError('broken on purpose')\n"), None
    )
    assert not report.passed
    assert "broken on purpose" in report.error


def test_a_wrong_result_shape_fails(tmp_path):
    folder = _plugin(
        tmp_path,
        """
        import numpy as np
        from varda.api import Analysis, ArrayDataSource, VardaRaster, hookimpl

        class Shrinks(Analysis):
            analysisId = "shrinks"
            name = "Shrinks"

            def run(self, image, reportProgress):
                return VardaRaster(ArrayDataSource(np.zeros((2, 2, 1))), name="small")

        @hookimpl
        def registerAnalyses():
            return [Shrinks]
        """,
    )
    report = runChecks(folder, None)
    assert not report.passed
    assert "2×2 pixels" in report.describe()


def test_a_hook_without_hookimpl_fails_as_it_would_when_enabled(tmp_path):
    folder = tmp_path / "band-depth"
    shutil.copytree(
        EXAMPLES / "band-depth", folder, ignore=shutil.ignore_patterns("__pycache__")
    )
    entry = folder / "plugin.py"
    entry.write_text(entry.read_text().replace("@hookimpl\n", ""))
    report = runChecks(folder, None)
    assert not report.passed
    assert "registerAnalyses isn't marked @hookimpl" in report.error


def test_a_plugin_that_hangs_is_stopped(tmp_path):
    folder = _plugin(tmp_path, "while True:\n    pass\n")
    report = runChecks(folder, None, timeout=5)
    assert "longer than 5 s" in report.error


def test_registering_nothing_fails():
    assert not CheckReport().passed
    assert "registered nothing" in CheckReport().describe()


def test_the_scan_warns_about_risky_code(tmp_path):
    folder = _plugin(
        tmp_path,
        """
        import socket
        import subprocess
        from varda.analysis.rasters import readCube
        from varda.api import Analysis
        eval("1")
        open("out.txt", "w")
        open("in.txt")
        """,
    )
    (folder / "tests").mkdir()
    (folder / "tests" / "test_plugin.py").write_text("import subprocess\n")
    warnings = scanForWarnings(folder)
    text = "\n".join(warnings)
    assert "network (socket)" in text and "other programs (subprocess)" in text
    assert "varda.analysis.rasters" in text and "(eval)" in text
    assert "writes a file" in text
    assert sum("open" in w for w in warnings) == 1  # reading is fine
    assert not any(w.startswith("tests") for w in warnings)


def test_the_example_has_no_warnings():
    assert scanForWarnings(EXAMPLES / "band-depth") == []


def test_the_check_flag_matches_main():
    from varda import main  # noqa: PLC0415 (main imports the splash)

    assert main.CHECK_PLUGIN_FLAG == checks.CHECK_FLAG


def test_a_category_duplicating_an_existing_menu_fails():
    report = CheckReport(
        analyses=(
            checks.AnalysisResult(
                "Spectral Smoothing",
                "smooth",
                "Filters",
                (checks.RunResult("synthetic", True),),
            ),
        )
    )
    assert report.passed
    checked = report.withCategoriesChecked(["Spectral Parameters", "Filtering"])
    assert not checked.passed
    assert "set category = 'Filtering'" in checked.describe()
    assert report.withCategoriesChecked(["Filters"]).passed  # exactly an existing one
