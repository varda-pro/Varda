import re
import shutil
from pathlib import Path

import numpy as np
import pytest
from attrs import evolve

from varda.crism.calibration import (
    AtRow,
    PDS_CDR_URL,
    CalibrationLibrary,
    MissingCalibrationError,
    TransmissionSource,
    atTable,
    downloadTo,
)
from varda.crism.products import CdrId
from varda.crism.readers import Trr3, readTrr3

TRR3 = "frs00031442_01_if168l_trr3.lbl"
WA = "cdr490947778566_wa0000000l_3"
AT = "cdr420862848019_at0000000l_7"


def copyCdr(cropDir: Path, name: str, folder: Path, as_: str | None = None) -> None:
    """Copy a crop's .img and .lbl into ``folder``, under another name if given
    (the label's file pointers are renamed with it)."""
    folder.mkdir(parents=True, exist_ok=True)
    target = as_ or name
    shutil.copy(cropDir / (name + ".img"), folder / (target + ".img"))
    label = (cropDir / (name + ".lbl")).read_text()
    label = re.sub(name, target, label, flags=re.IGNORECASE)
    (folder / (target + ".lbl")).write_text(label)


def writeVariant(
    cropDir: Path, name: str, folder: Path, as_: str, value: float
) -> None:
    """A copy of a crop CDR whose first sample is ``value`` (so its content differs)."""
    copyCdr(cropDir, name, folder, as_)
    with open(folder / (as_ + ".img"), "r+b") as file:
        file.write(np.float32(value).astype("<f4").tobytes())


class Recorder:
    """A fake fetcher that records URLs and writes nothing."""

    def __init__(self, error: OSError | None = None):
        self.urls: list[str] = []
        self.error = error

    def __call__(self, url: str, target: Path) -> None:
        self.urls.append(url)
        if self.error is not None:
            raise self.error


def library(tmp_path: Path, fetch: Recorder | None = None) -> CalibrationLibrary:
    return CalibrationLibrary(
        [tmp_path / "cdr"], tmp_path / "cache", fetch=fetch or Recorder()
    )


@pytest.fixture
def trr3(cropDir: Path) -> Trr3:
    return readTrr3(cropDir / TRR3)


def withSclk(trr3: Trr3, sclk: str) -> Trr3:
    keywords = {**trr3.label.keywords, "SPACECRAFT_CLOCK_START_COUNT": sclk}
    return evolve(trr3, label=evolve(trr3.label, keywords=keywords))


def plainRows() -> list[AtRow]:
    """The table's unbinned, unfiltered (``AT0000000L``) rows."""
    return [
        r
        for r in atTable()
        if (c := CdrId.parse(r.name)).binning == 0 and c.wavelengthFilter == 0
    ]


def atNames(lib: CalibrationLibrary, trr3: Trr3) -> list[str]:
    return [f.name for f in lib.status(trr3) if f.kind == "AT"]


def test_finds_local_cdr_folder(cropDir: Path, tmp_path: Path, trr3: Trr3):
    copyCdr(cropDir, WA, tmp_path / "cdr" / "CRISM_CDR" / "wa")
    fetch = Recorder()
    wavelengths = library(tmp_path, fetch).wavelengths(trr3)
    assert wavelengths.shape == (120, 438)
    assert fetch.urls == []


def test_fetches_missing_wa(tmp_path: Path, trr3: Trr3):
    fetch = Recorder()
    with pytest.raises(MissingCalibrationError):
        library(tmp_path, fetch).ensure(trr3)
    stem = PDS_CDR_URL + "wa/" + WA
    assert stem + ".img" in fetch.urls
    assert stem + ".lbl" in fetch.urls


def scanOf(name: str) -> str:
    return next(r.scanId for r in atTable() if r.name == name)


class Selective(Recorder):
    """Fails (OSError) for AT downloads of the given scans; records the rest."""

    def __init__(self, failing: frozenset[str] = frozenset()):
        super().__init__()
        self.failing = failing

    def __call__(self, url: str, target: Path) -> None:
        self.urls.append(url)
        stem = url.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if "/at/" in url and scanOf(stem) in self.failing:
            raise OSError("not found")


def test_missing_files_listed_before_processing(tmp_path: Path, trr3: Trr3):
    lib = library(tmp_path, Recorder(OSError("offline")))
    with pytest.raises(MissingCalibrationError) as caught:
        lib.ensure(trr3)
    listed = {f.name: f for f in caught.value.files}
    assert WA in listed
    assert set(atNames(lib, trr3)) <= set(listed)
    assert len(atNames(lib, trr3)) == 6  # one per scan
    assert all(f.required for f in caught.value.files)
    assert all(f.url and f.localPath is None for f in caught.value.files)
    assert WA in str(caught.value).lower()
    status = lib.status(trr3)
    assert all(f.required for f in status if f.kind in ("WA", "AT"))
    assert not any(f.required for f in status if f.kind == "BP")


def test_one_fetch_per_scan(cropDir: Path, tmp_path: Path, trr3: Trr3):
    copyCdr(cropDir, WA, tmp_path / "cdr")
    fetch = Selective()
    lib = library(tmp_path, fetch)
    with pytest.raises(MissingCalibrationError):  # the fake writes nothing
        lib.ensure(trr3)
    atImages = [u for u in fetch.urls if "/at/" in u and u.endswith(".img")]
    scans = [scanOf(u.rsplit("/", 1)[-1][:-4]) for u in atImages]
    assert len(atImages) == 6 and len(set(scans)) == 6


def test_unavailable_scans_are_noted_not_fatal(
    cropDir: Path, tmp_path: Path, trr3: Trr3
):
    copyCdr(cropDir, WA, tmp_path / "cdr")
    lib = library(tmp_path)
    names = atNames(lib, trr3)
    failing = {scanOf(n) for n in names[:2]}
    for i, name in enumerate(names[2:]):  # four scans are already local
        writeVariant(cropDir, AT, tmp_path / "cdr", name, 0.1 * (i + 1))
    lib = library(tmp_path, Selective(frozenset(failing)))
    status = lib.ensure(trr3)
    assert len(lib.transmissions(trr3, TransmissionSource.PDS_AT)) == 4
    noted = {f.name for f in status if "could not be fetched" in f.note}
    assert noted == set(names[:2])


def test_all_ats_failing_raises(cropDir: Path, tmp_path: Path, trr3: Trr3):
    copyCdr(cropDir, WA, tmp_path / "cdr")
    lib = library(tmp_path, Recorder(OSError("offline")))
    with pytest.raises(MissingCalibrationError) as caught:
        lib.ensure(trr3)
    assert {f.kind for f in caught.value.files} == {"AT"}


def test_other_local_period_satisfies_ensure(cropDir: Path, tmp_path: Path, trr3: Trr3):
    # The crop AT is scan 08608's earlier period, not the one ranked best for
    # the 2014 SCLK; offline, it is still a transmission the cube can use.
    copyCdr(cropDir, WA, tmp_path / "cdr")
    copyCdr(cropDir, AT, tmp_path / "cdr")
    lib = library(tmp_path, Recorder(OSError("offline")))
    entry = next(f for f in lib.status(trr3) if f.name == AT)
    assert entry.localPath is not None and "period" in entry.note
    lib.ensure(trr3)
    found = lib.transmissions(trr3, TransmissionSource.PDS_AT)
    assert [f.sourceName.lower() for f in found] == [AT]


def test_local_file(cropDir: Path, tmp_path: Path):
    copyCdr(cropDir, AT, tmp_path / "cdr" / "CRISM_CDR" / "at")
    lib = library(tmp_path)
    path = lib.localFile("AT", AT.upper())
    assert path == tmp_path / "cdr" / "CRISM_CDR" / "at" / (AT + ".img")
    assert lib.localFile("WA", WA) is None


def test_missing_bad_pixels_do_not_block(cropDir: Path, tmp_path: Path, trr3: Trr3):
    copyCdr(cropDir, WA, tmp_path / "cdr")
    lib = library(tmp_path)
    copyCdr(cropDir, AT, tmp_path / "cdr", atNames(lib, trr3)[0])
    status = lib.ensure(trr3)  # the BPs are absent and the fake writes nothing
    assert [f.localPath for f in status if f.kind == "BP"] == [None, None]
    assert lib.badPixels(trr3) is None


def test_sensor_s_has_no_pds_ats(cropDir: Path, tmp_path: Path, trr3: Trr3):
    s3 = evolve(trr3, id=trr3.id.replace(sensor="S"))
    ats = [f for f in library(tmp_path).status(s3) if f.kind == "AT"]
    assert len(ats) == 1 and "IR only" in ats[0].note


def test_ensure_passes_when_everything_is_local(
    cropDir: Path, tmp_path: Path, trr3: Trr3
):
    copyCdr(cropDir, WA, tmp_path / "cdr")
    for name in atNames(library(tmp_path), trr3) + [
        f.name for f in library(tmp_path).status(trr3) if f.kind == "BP"
    ]:
        copyCdr(cropDir, AT, tmp_path / "cdr", name)
    fetch = Recorder()
    library(tmp_path, fetch).ensure(trr3)
    assert fetch.urls == []


def test_dedupe_transmissions(cropDir: Path, tmp_path: Path, trr3: Trr3):
    other = next(row.name for row in plainRows() if row.name != AT)
    copyCdr(cropDir, AT, tmp_path / "cdr" / "CRISM_CDR" / "at")
    copyCdr(cropDir, AT, tmp_path / "cdr" / "CRISM_CDR" / "at", other)
    found = library(tmp_path).transmissions(trr3, TransmissionSource.PDS_AT)
    assert len(found) == 1
    assert found[0].values.shape == (120, 438)
    assert found[0].artifacts is None


def twoPeriods(cropDir: Path, tmp_path: Path) -> tuple[str, str]:
    rows = sorted(plainRows(), key=lambda r: r.sclkStop)
    early, late = rows[0], rows[-1]
    writeVariant(cropDir, AT, tmp_path / "cdr", early.name, 0.25)
    writeVariant(cropDir, AT, tmp_path / "cdr", late.name, 0.5)
    return early.name, late.name


def test_period_extrapolation_recorded(cropDir: Path, tmp_path: Path, trr3: Trr3):
    twoPeriods(cropDir, tmp_path)
    found = library(tmp_path).transmissions(trr3, TransmissionSource.PDS_AT)
    assert len(found) == 2
    latest = max(r.sclkStop for r in atTable())
    assert found[0].sclkStop == latest
    assert found[0].extrapolated and found[1].extrapolated


def test_period_containing_sclk_first(cropDir: Path, tmp_path: Path, trr3: Trr3):
    early, late = twoPeriods(cropDir, tmp_path)
    inEarly = withSclk(trr3, "2/0843700000")
    found = library(tmp_path).transmissions(inEarly, TransmissionSource.PDS_AT)
    assert found[0].sourceName.lower() == early
    assert not found[0].extrapolated
    assert found[1].sourceName.lower() == late


def test_binned_observation_selects_binned_cdrs(
    cropDir: Path, tmp_path: Path, trr3: Trr3
):
    label = trr3.label
    keywords = {**label.keywords, "PIXEL_AVERAGING_WIDTH": 2}
    binned = evolve(
        trr3,
        label=evolve(label, keywords=keywords, image=evolve(label.image, samples=320)),
    )
    lib = library(tmp_path)
    names = atNames(lib, binned)
    assert names
    assert {CdrId.parse(n).binning for n in names} == {1}
    # a CDR whose columns do not match the cube is an error, not a silent fit
    copyCdr(cropDir, AT, tmp_path / "cdr", names[0])
    with pytest.raises(ValueError, match=r"120 columns.*320"):
        lib.transmissions(binned, TransmissionSource.PDS_AT)


def test_bad_pixels_are_combined(cropDir: Path, tmp_path: Path, trr3: Trr3):
    lib = library(tmp_path)
    assert (
        lib.badPixels(
            evolve(
                trr3,
                label=evolve(
                    trr3.label,
                    keywords={
                        **trr3.label.keywords,
                        "SOURCE_PRODUCT_ID": ["CDR451090233643_BK1030100L_3"],
                    },
                ),
            )
        )
        is None
    )
    names = [f.name for f in lib.status(trr3) if f.kind == "BP"]
    assert len(names) == 2
    writeVariant(
        cropDir, AT, tmp_path / "cdr", names[0], 0.0
    )  # first sample 0, rest nonzero
    copyCdr(cropDir, AT, tmp_path / "cdr", names[1])
    bad = lib.badPixels(trr3)
    assert bad is not None
    assert bad.shape == (120, 438) and bad.dtype == bool
    assert bad.all()


def test_cat_aux_vs_adrs(demoData: Path):
    folder = demoData / "data-for-paper"
    trr3 = readTrr3(folder / "FRS00031442/trdr/frs00031442_01_if168l_trr3.lbl")
    lib = CalibrationLibrary(
        [folder / "ATO0004EC45"],
        folder.parent / "missing-cache",
        catAuxFolder=folder / "CAT_v7_4_aux/aux_files",
        fetch=Recorder(),
    )
    found = lib.transmissions(trr3, TransmissionSource.CAT_VS_ADR)
    assert len(found) > 6
    assert all(t.artifacts is not None and t.artifacts.shape[0] == 2 for t in found)
    assert all(t.values.shape == (640, 438) for t in found)
    # with both sources, an AT and a VS ADR of the same scan stay apart
    both = lib.transmissions(trr3, TransmissionSource.BOTH)
    keys = [t.key for t in both]
    assert len(set(keys)) == len(keys)
    shared = {t.scanId for t in both if t.key.endswith(":AT")} & {
        t.scanId for t in both if t.key.endswith(":VS")
    }
    assert shared


def test_download_leaves_no_partial_file(tmp_path: Path):
    source = tmp_path / "source.img"
    source.write_bytes(b"abc")
    target = tmp_path / "cache" / "wa" / "x.img"
    downloadTo(source.as_uri(), target)
    assert target.read_bytes() == b"abc"
    assert [p.name for p in target.parent.iterdir()] == ["x.img"]
    with pytest.raises(OSError):
        downloadTo((tmp_path / "absent").as_uri(), tmp_path / "cache" / "y.img")
    assert not (tmp_path / "cache" / "y.img").exists()
    assert not list((tmp_path / "cache").glob("*.part"))


def test_local_second_period_is_used_and_not_fetched(
    cropDir: Path, tmp_path: Path, trr3: Trr3
):
    copyCdr(cropDir, WA, tmp_path / "cdr")
    copyCdr(cropDir, AT, tmp_path / "cdr")
    row = next(r for r in atTable() if r.name == AT)
    recorder = Recorder(OSError("offline"))
    lib = library(tmp_path, recorder)
    status = lib.ensure(trr3)
    scan = [f for f in status if f.kind == "AT" and f"scan {row.scanId}:" in f.note]
    assert [f.name for f in scan] == [AT] and scan[0].localPath is not None
    assert "period" in scan[0].note and "of" in scan[0].note
    assert not [u for u in recorder.urls if scan[0].name in u]
