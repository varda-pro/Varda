"""The separate process that checks a plugin: ``Varda --check-plugin FOLDER
SAMPLE REPORT``. Imports the plugin, collects what it registers, runs each
analysis on the sample images and then the plugin's own tests, and writes
the results as JSON to REPORT (never stdout: the windowed Windows app has
none). ``checks.runChecks`` starts it and reads the report."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

from varda.analysis.analysis import Analysis
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.plugins.manifest import PluginError, readManifest
from varda.plugins.plugin_manager import (
    Contributions,
    collectContributions,
    importPlugin,
    newHookManager,
)

REGISTRATION_HOOKS = ("registerAnalyses", "registerDataSources", "registerCatalogs")


def _sampleImages(sample: Path) -> list[tuple[str, VardaRaster]]:
    data = np.load(sample)
    images = [("synthetic", data["synthetic"], data["synthetic_wavelengths"], None)]
    if "crop" in data:
        wavelengths = data["crop_wavelengths"] if "crop_wavelengths" in data else None
        nodata = float(data["crop_nodata"]) if "crop_nodata" in data else None
        images.append(("your image", data["crop"], wavelengths, nodata))
    return [
        (
            label,
            VardaRaster(
                ArrayDataSource(
                    cube,
                    wavelengths=wavelengths,
                    wavelengthUnits="Nanometers"
                    if wavelengths is not None
                    else "Unknown",
                    nodata=nodata,
                ),
                name=label,
            ),
        )
        for label, cube, wavelengths, nodata in images
    ]


def _load(folder: Path) -> Contributions:
    """What the plugin adds, imported and collected as Varda loads it when
    it's enabled, so a plugin that passes here also works there."""
    manifest = readManifest(folder)
    module = importPlugin(manifest, folder)
    pm = newHookManager()
    pm.register(module, name=manifest.name)
    unmarked = [
        hook
        for hook in REGISTRATION_HOOKS
        if callable(getattr(module, hook, None))
        and pm.parse_hookimpl_opts(module, hook) is None
    ]
    if unmarked:
        raise PluginError(
            f"{', '.join(unmarked)} isn't marked @hookimpl, so Varda won't call it."
        )
    return collectContributions(
        pm, module, f"{manifest.name} {manifest.version}".strip()
    )


def _run(analysisClass: type[Analysis], label: str, image: VardaRaster) -> dict:
    result: dict = {"image": label, "ok": False}
    try:
        analysis = analysisClass()
        analysis.prepareFor(image)
        reason = analysis.unavailableReason(image)
        if reason:
            # Not a failure: the analysis says what it needs (e.g. wavelengths)
            return {**result, "ok": True, "unavailable": reason}
        started = time.perf_counter()
        output = analysis.run(image, lambda percent, message: None)
        result["seconds"] = time.perf_counter() - started
        if not isinstance(output, VardaRaster):
            raise TypeError(f"run returned {type(output).__name__}, not a VardaRaster")
        values = np.ma.filled(np.ma.asarray(output.getData(), dtype=float), np.nan)
        if values.shape[:2] != (image.height, image.width):
            raise ValueError(
                f"the result is {values.shape[0]}×{values.shape[1]} pixels; the input "
                f"is {image.height}×{image.width}"
            )
        return {
            **result,
            "ok": True,
            "shape": list(values.shape),
            "bandNames": list(output.bandNames),
            "nanFraction": float(np.isnan(values).mean()) if values.size else 1.0,
        }
    except Exception:
        return {**result, "error": traceback.format_exc(limit=8)}


def _runTests(folder: Path) -> dict:
    tests = folder / "tests"
    if not tests.is_dir():
        return {"testsOutput": "The plugin has no tests folder."}
    try:
        import pytest  # noqa: PLC0415 (only when Varda runs from source)
    except ImportError:
        return {"testsOutput": "pytest isn't part of the packaged Varda."}
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = pytest.main(
            ["-q", "-p", "no:cacheprovider", "--rootdir", str(folder), str(tests)]
        )
    return {
        "testsRan": True,
        "testsOk": code == 0,
        "testsOutput": output.getvalue()[-4000:],
    }


def check(folder: Path, sample: Path) -> dict:
    sys.dont_write_bytecode = True  # keep the plugin folder clean
    try:
        contributions = _load(folder)
        analyses = contributions.analyses
        formats = [entry[0] for entry in contributions.dataSources]
    except Exception:
        return {"error": traceback.format_exc(limit=8)}
    images = _sampleImages(sample)
    report: dict = {
        "analyses": [
            {
                "name": cls.name,
                "analysisId": cls.analysisId,
                "category": cls.category,
                "runs": [_run(cls, label, image) for label, image in images],
            }
            for cls in analyses
        ],
        "dataSources": formats,
    }
    return {**report, **_runTests(folder)}


def main(arguments: list[str]) -> int:
    folder, sample, reportPath = (Path(a) for a in arguments)
    report = check(folder, sample)
    reportPath.write_text(json.dumps(report), encoding="utf-8")
    return 0
