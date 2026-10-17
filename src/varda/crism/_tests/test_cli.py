"""The command line: ``python -m varda.crism`` and ``Varda --crism`` (``crismMain``)."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from varda.crism import calibration, cli
from varda.crism._tests.synthetic import makeSyntheticCube, writeSyntheticObservation
from varda.crism.atmosphere import artifactFileName, loadArtifactBasis
from varda.crism.cli import crismMain
from varda.crism.pipeline import ProcessResult

_WA = "cdr490947778566_wa0000000l_3"
_AT = "cdr420862848019_at0000000l_7"
_OBSERVATION = ("frs00031442_01_if168l_trr3", "frs00031442_01_de168l_ddr1")
_PARAMETERS = ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, target: Path) -> None:
        raise OSError("offline (test)")

    monkeypatch.setattr(calibration, "downloadTo", refuse)


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
    code = crismMain(["process", cube, cube, "--out", str(tmp_path)])
    assert code == 1 and len(calls) == 2
    captured = capsys.readouterr()
    assert "first one is bad" in captured.err and "x.img" in captured.out


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
