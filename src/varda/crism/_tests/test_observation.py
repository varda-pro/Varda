import shutil
from pathlib import Path

import pytest

from varda.crism.observation import CrismObservation

CUBE = "frs00031442_01_if168l_trr3"
DDR = "frs00031442_01_de168l_ddr1"


def _copy(cropDir: Path, root: Path, withDdr: bool = True) -> None:
    (root / "trdr").mkdir(parents=True)
    for suffix in (".img", ".lbl"):
        shutil.copy(cropDir / (CUBE + suffix), root / "trdr")
    if withDdr:
        (root / "ddr").mkdir()
        for suffix in (".img", ".lbl"):
            shutil.copy(cropDir / (DDR + suffix), root / "ddr")


def test_from_folder_crop(cropDir: Path, tmp_path: Path):
    _copy(cropDir, tmp_path)
    obs = CrismObservation.fromFolder(tmp_path)
    assert obs.name == "FRS00031442"
    assert obs.counter == 1
    assert obs.irCube == tmp_path / "trdr" / (CUBE + ".lbl")
    assert obs.irDdr == tmp_path / "ddr" / (DDR + ".lbl")
    assert obs.vnirCube is None and obs.vnirDdr is None
    trr, ddr = obs.readIr()
    assert trr.shape[2] > 0 and ddr is not None
    assert CrismObservation.fromFiles(obs.irCube) == obs


def test_missing_ddr(cropDir: Path, tmp_path: Path):
    _copy(cropDir, tmp_path, withDdr=False)
    with pytest.raises(FileNotFoundError, match="DDR"):
        CrismObservation.fromFolder(tmp_path)


def test_chooses_central_counter(cropDir: Path, tmp_path: Path):
    base = (cropDir / (CUBE + ".lbl")).read_text()
    assert "LINES             = 16" in base
    (tmp_path / "trdr").mkdir()
    (tmp_path / "ddr").mkdir()
    for counter, count in (("03", 16), ("07", 400)):
        text = base.replace("LINES             = 16", f"LINES             = {count}")
        cube = CUBE.replace("_01_", f"_{counter}_")
        (tmp_path / "trdr" / f"{cube}.lbl").write_text(text)
        ddr = DDR.replace("_01_", f"_{counter}_")
        shutil.copy(cropDir / (DDR + ".lbl"), tmp_path / "ddr" / f"{ddr}.lbl")
    assert CrismObservation.fromFolder(tmp_path).counter == 7
    assert CrismObservation.fromFolder(tmp_path, counter=3).counter == 3
    with pytest.raises(FileNotFoundError, match="counter 05"):
        CrismObservation.fromFolder(tmp_path, counter=5)


def test_demo_frt00005850(demoData: Path):
    folder = demoData / "data-for-paper" / "FRT00005850"
    obs = CrismObservation.fromFolder(folder)
    assert obs.counter == 7
    assert obs.irCube.name == "frt00005850_07_if167l_trr3.xml"
    assert obs.irDdr.name.startswith("frt00005850_07_de167l_ddr")
    assert obs.vnirCube is not None and obs.vnirCube.name.startswith(
        "frt00005850_07_if167s"
    )
    assert obs.vnirDdr is not None and obs.vnirDdr.name.startswith(
        "frt00005850_07_de167s_ddr"
    )


def _fake(cropDir: Path, root: Path, observation: str, withDdr: bool = True) -> None:
    (root / "trdr").mkdir(parents=True, exist_ok=True)
    (root / "ddr").mkdir(exist_ok=True)
    shutil.copy(
        cropDir / (CUBE + ".lbl"),
        root / "trdr" / f"{CUBE.replace('frs00031442', observation)}.lbl",
    )
    if withDdr:
        shutil.copy(
            cropDir / (DDR + ".lbl"),
            root / "ddr" / f"{DDR.replace('frs00031442', observation)}.lbl",
        )


def test_several_observations(cropDir: Path, tmp_path: Path):
    _fake(cropDir, tmp_path / "a", "frs00031442")
    _fake(cropDir, tmp_path / "b", "frs0002fe75")
    _fake(cropDir, tmp_path / "c", "frs00031260", withDdr=False)
    with pytest.raises(ValueError, match="FRS0002FE75.*FRS00031260.*FRS00031442"):
        CrismObservation.fromFolder(tmp_path)
    found = CrismObservation.discover(tmp_path)
    assert [o.name for o in found] == ["FRS0002FE75", "FRS00031442"]
    assert CrismObservation.fromFolder(tmp_path / "a").name == "FRS00031442"


def test_from_files_explicit_ddr(cropDir: Path):
    obs = CrismObservation.fromFiles(
        cropDir / (CUBE + ".lbl"), cropDir / (DDR + ".lbl")
    )
    assert obs.irDdr == cropDir / (DDR + ".lbl")
    assert obs.vnirCube is None


def test_demo_discover(demoData: Path):
    names = {o.name for o in CrismObservation.discover(demoData / "data-for-paper")}
    assert {"FRS00031442", "FRS0002FE75", "FRS00031260", "FRT00005850"} <= names
