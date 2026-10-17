"""The command line: ``python -m varda.crism`` and ``Varda --crism`` (``crismMain``)."""

import json
import logging
import math
import re
import shutil
import sys
from pathlib import Path

import appdirs

import attrs
import numpy as np
import pytest
import rasterio
from affine import Affine

from varda.crism import calibration, cli
from varda.crism._tests.synthetic import makeSyntheticCube, writeSyntheticObservation
from varda.crism.atmosphere import artifactFileName, loadArtifactBasis
from varda.crism.calibration import CalibrationLibrary
from varda.crism.cli import crismMain
from varda.crism.pipeline import ProcessResult, columnResiduals
from varda.crism.recipe import Recipe
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

_WA = "cdr490947778566_wa0000000l_3"
_AT = "cdr420862848019_at0000000l_7"
_OBSERVATION = ("frs00031442_01_if168l_trr3", "frs00031442_01_de168l_ddr1")
_PARAMETERS = ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, target: Path) -> None:
        raise OSError("offline (test)")

    monkeypatch.setattr(calibration, "downloadTo", refuse)


@pytest.fixture(autouse=True)
def isolatedCache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The tests never read or fill the user's own calibration cache."""
    cache = tmp_path / "default-cache"
    monkeypatch.setattr(cli, "defaultCache", lambda: cache)
    return cache


def _copy(cropDir: Path, folder: Path, names: tuple[str, ...]) -> Path:
    folder.mkdir(parents=True)
    for name in names:
        for suffix in (".lbl", ".img"):
            shutil.copyfile(cropDir / (name + suffix), folder / (name + suffix))
    return folder


def test_cli_process_crop(
    cropDir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = crismMain(["process", str(cropDir), "--out", str(tmp_path / "out")])
    assert code == 0
    assert (tmp_path / "out" / "frs00031442_01_if168l_vmtr.img").exists()
    lines = capsys.readouterr().out.splitlines()
    assert any(line.endswith("frs00031442_01_if168l_vmtr.img") for line in lines)


def test_cli_process_with_recipe_and_options(cropDir: Path, tmp_path: Path) -> None:
    recipe = tmp_path / "r.json"
    recipe.write_text(json.dumps({"recipeVersion": 2, "despike": {"enabled": False}}))
    code = crismMain(
        [
            "process",
            str(cropDir / (_OBSERVATION[0] + ".lbl")),
            "--recipe",
            str(recipe),
            "--out",
            str(tmp_path / "out"),
            "--sensor-space",
            "--cache",
            str(tmp_path / "cache"),
        ]
    )
    assert code == 0
    assert (tmp_path / "out" / "frs00031442_01_if168l_vter.img").exists()
    saved = json.loads(
        (tmp_path / "out/frs00031442_01_if168l_vmtr.recipe.json").read_text()
    )
    assert saved["despike"]["enabled"] is False


def test_cli_calibration_lists_missing(
    cropDir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    obs = _copy(cropDir, tmp_path / "obs", _OBSERVATION)
    cache = str(tmp_path / "cache")
    assert crismMain(["calibration", str(obs), "--cache", cache]) == 2
    out = capsys.readouterr().out
    assert "missing" in out and _WA in out
    assert crismMain(["calibration", str(obs), "--cache", cache, "--fetch"]) == 2
    assert "missing" in capsys.readouterr().out


def test_cli_calibration_found(
    cropDir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert crismMain(["calibration", str(cropDir)]) == 0
    out = capsys.readouterr().out
    assert _WA in out and "found" in out


def test_cli_process_missing_calibration(
    cropDir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    obs = _copy(cropDir, tmp_path / "obs", _OBSERVATION)
    code = crismMain(
        [
            "process",
            str(obs),
            "--out",
            str(tmp_path / "o"),
            "--cache",
            str(tmp_path / "c"),
        ]
    )
    assert code == 2
    assert _WA in capsys.readouterr().err


def test_cli_bad_recipe(
    cropDir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    recipe = tmp_path / "bad.json"
    recipe.write_text(json.dumps({"recipeVersion": 2, "despike": {"bogusKey": 1}}))
    code = crismMain(
        ["process", str(cropDir), "--recipe", str(recipe), "--out", str(tmp_path)]
    )
    assert code == 2
    assert "bogusKey" in capsys.readouterr().err
    recipe.write_text("{not json")
    assert crismMain(["process", str(cropDir), "--recipe", str(recipe)]) == 2


def test_cli_one_failure_does_not_stop_the_rest(
    cropDir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []

    def fake(observation, recipe, folder, **kwargs) -> ProcessResult:
        calls.append(observation.name)
        if len(calls) == 1:
            raise ValueError("first one is bad")
        path = Path(folder) / "x.img"
        return ProcessResult(path, None, None, None, None, path, path, path)

    monkeypatch.setattr(cli, "process", fake)
    cube = str(cropDir / (_OBSERVATION[0] + ".lbl"))
    with caplog.at_level(logging.ERROR, logger="varda.crism.cli"):
        code = crismMain(["process", cube, cube, "--out", str(tmp_path)])
    assert code == 1 and len(calls) == 2
    captured = capsys.readouterr()
    assert "first one is bad" in captured.err and "x.img" in captured.out
    # the traceback goes to the log (final review, Important 7)
    (record,) = [r for r in caplog.records if r.exc_info is not None]
    assert "first one is bad" in caplog.text and "Traceback" in caplog.text


def test_cli_learn_artifacts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folders = []
    for seed in (1, 2, 3):
        cube, truth = makeSyntheticCube(lines=64, seed=seed)
        writeSyntheticObservation(tmp_path / f"obs{seed}", cube, truth)
        folders.append(str(tmp_path / f"obs{seed}"))
    out = tmp_path / "bases"
    code = crismMain(["learn-artifacts", *folders, "--out", str(out)])
    assert code == 0
    files = list(out.glob("*.npz"))
    assert len(files) == 1
    basis = loadArtifactBasis(files[0])
    assert files[0].name == artifactFileName(basis.transmissionKey)
    assert len(basis.sourceObservations) == 3
    table = capsys.readouterr().out
    assert basis.transmissionKey in table and "leave-one-out" in table.lower()
    assert table.count("FRS00031442") == 3


def test_leave_one_out_skips_columns_without_wavelengths(tmp_path: Path) -> None:
    """A real WA leaves the edge columns without wavelengths (FRS: 29–632
    valid); the leave-one-out table must score the other columns, not raise
    (it did on every real observation)."""
    cube, truth = makeSyntheticCube(lines=64, seed=1)
    observation = writeSyntheticObservation(tmp_path / "obs", cube, truth)
    library = CalibrationLibrary([tmp_path / "obs"], tmp_path / "cache")
    column = columnResiduals(observation, Recipe(), library)
    wavelengths = column.wavelengths.copy()
    wavelengths[:3] = np.nan
    edged = attrs.evolve(column, wavelengths=wavelengths)
    assert math.isfinite(cli.columnCo2Median(edged, None))
    assert cli.columnCo2Median(edged, None) == pytest.approx(
        cli.columnCo2Median(column, None), rel=0.05
    )


def test_cli_validate_against_itself(
    cropDir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert crismMain(["process", str(cropDir), "--out", str(tmp_path / "out")]) == 0
    product = str(tmp_path / "out" / "frs00031442_01_if168l_vmtr.img")
    report = tmp_path / "report"
    assert crismMain(["validate", product, product, "--report", str(report)]) == 0
    data = json.loads((report / "report.json").read_text())
    assert set(data["parameters"]) == set(_PARAMETERS)
    for name in _PARAMETERS:
        entry = data["parameters"][name]
        assert entry["n"] > 0
        assert entry["medianBias"] == pytest.approx(0.0, abs=1e-12)
    assert data["overlap"]["pixels"] > 0
    assert data["co2Residual"]["ours"]["median"] == pytest.approx(
        data["co2Residual"]["mtrdr"]["median"]
    )
    for name in (*_PARAMETERS, "spectra", "co2_by_column"):
        assert (report / f"{name}.png").stat().st_size > 0, name
    assert np.isfinite(data["shapeRatio"]["median"])


def _joinedReference(oursPath: Path, target: Path) -> int:
    """A stand-in for the joined VNIR + IR MTRDR: our bands plus 78 extra VNIR
    ones in front, on a grid shifted by (2, 1) pixels; returns the overlap size."""
    ours = ENVIDataSource(str(oursPath))
    cube = ours.readAllBands()[1:, 2:]  # (rows, cols, bands), NaN = no data
    extra = np.full((*cube.shape[:2], 78), 0.5)
    stacked = np.concatenate([extra, cube], axis=-1)
    wavelengths = [436.0 + 7.0 * i for i in range(78)] + [
        float(w) for w in ours.wavelengths
    ]
    data = np.moveaxis(np.where(np.isnan(stacked), 65535.0, stacked), -1, 0)
    transform = ours.transform * Affine.translation(2, 1)
    with rasterio.open(
        target,
        "w",
        driver="ENVI",
        width=data.shape[2],
        height=data.shape[1],
        count=data.shape[0],
        dtype="float32",
        crs=ours.crs,
        transform=transform,
        nodata=65535.0,
    ) as out:
        out.write(data.astype("float32"))
        out.update_tags(
            ns="ENVI", wavelength="{" + ", ".join(map(str, wavelengths)) + "}"
        )
    return cube.shape[0] * cube.shape[1]


def test_cli_validate_joined_reference_with_other_extent(
    cropDir: Path, tmp_path: Path
) -> None:
    assert crismMain(["process", str(cropDir), "--out", str(tmp_path / "out")]) == 0
    product = tmp_path / "out" / "frs00031442_01_if168l_vmtr.img"
    reference = tmp_path / "joined.img"
    size = _joinedReference(product, reference)
    report = tmp_path / "report"
    assert (
        crismMain(["validate", str(product), str(reference), "--report", str(report)])
        == 0
    )
    data = json.loads((report / "report.json").read_text())
    assert data["overlap"]["pixels"] == size
    assert data["parameters"]["D2300"]["medianBias"] == pytest.approx(0.0, abs=1e-6)
    assert max(data["shapeRatio"]["wavelengthsNm"]) <= 2600  # bbl = 0 bands left out
    assert data["shapeRatio"]["median"] == pytest.approx(1.0, abs=1e-6)


def test_cli_validate_unmatched_bands_is_an_error(
    cropDir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert crismMain(["process", str(cropDir), "--out", str(tmp_path / "out")]) == 0
    product = tmp_path / "out" / "frs00031442_01_if168l_vmtr.img"
    reference = tmp_path / "joined.img"
    _joinedReference(product, reference)
    other = ENVIDataSource(str(reference))
    assert other.bandCount == 489
    del other
    # a reference holding only the extra VNIR bands has none of ours
    shutil.copyfile(reference, tmp_path / "vnir.img")
    header = reference.with_suffix(".hdr").read_text()
    wavelengths = ", ".join(str(436.0 + 7.0 * i) for i in range(489))
    (tmp_path / "vnir.hdr").write_text(
        re.sub(
            r"wavelength\s*=\s*\{[^}]*\}", "wavelength = {" + wavelengths + "}", header
        )
    )
    code = crismMain(
        [
            "validate",
            str(product),
            str(tmp_path / "vnir.img"),
            "--report",
            str(tmp_path / "r"),
        ]
    )
    assert code == 2 and "no MTRDR band" in capsys.readouterr().err


def test_program_name() -> None:
    assert cli._parser().prog == "python -m varda.crism"


def test_windowed_app_output_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Final review, Important 8: the windowed Windows build has no console.
    ``Varda --crism`` attaches to the parent's console, and when there is
    none, writes to a log file; elsewhere nothing changes."""
    attached: list[bool] = []

    def attach(result: bool):
        def call() -> bool:
            attached.append(result)
            return result

        return call

    log = tmp_path / "Logs" / cli.CRISM_LOG_NAME
    stdout = sys.stdout
    assert cli.consoleForWindowedApp(windowed=False, attach=attach(False)) is None
    assert attached == [] and sys.stdout is stdout
    assert cli.consoleForWindowedApp(windowed=True, attach=attach(True)) is None
    assert attached == [True] and sys.stdout is stdout

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    found = cli.consoleForWindowedApp(
        windowed=True, attach=attach(False), logFile=lambda: log
    )
    assert found == log and attached == [True, False]
    print("to the log")
    print("errors too", file=sys.stderr)
    sys.stdout.flush()
    text = log.read_text()
    assert "Varda --crism" in text and "to the log" in text and "errors too" in text
    sys.stdout.close()


def test_windowed_app_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "stdout", None)
    assert not cli.isWindowedWindowsApp()
    monkeypatch.setattr(sys, "platform", "win32")
    assert not cli.isWindowedWindowsApp()  # not frozen: run from Python
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert cli.isWindowedWindowsApp()


def test_shared_app_data_locations(monkeypatch: pytest.MonkeyPatch) -> None:
    """One cache for the CLI and the GUI; the log beside the GUI's logs. On
    macOS and Linux the folders are where they were."""
    monkeypatch.undo()  # the real defaultCache, not the isolated one
    assert cli.defaultCache() == calibration.defaultCacheFolder()
    if sys.platform != "win32":
        assert calibration.defaultCacheFolder() == (
            Path(appdirs.user_data_dir("Varda")) / "crism"
        )
        assert (
            cli.crismLogFile().parent == Path(appdirs.user_data_dir("Varda")) / "Logs"
        )
