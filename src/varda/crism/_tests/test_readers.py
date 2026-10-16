import warnings
from pathlib import Path

import numpy as np
import pytest

from varda.crism.labels import ArrayLayout, ProductLabel
from varda.crism.products import ProductId
from varda.crism.readers import (
    Ddr,
    DdrBand,
    DetectorFrame,
    readDdr,
    readDetectorFrame,
    readTrr3,
    readVsAdr,
    usableLines,
)

TRR3 = "frs00031442_01_if168l_trr3.lbl"
DDR = "frs00031442_01_de168l_ddr1.lbl"
WA = "cdr490947778566_wa0000000l_3.lbl"
VS_ADR = "data-for-paper/CAT_v7_4_aux/aux_files/ADR/VS/ADR50924300803_28D04_VS00L_9.IMG"


def test_trr3_layout_and_nodata(cropDir: Path) -> None:
    trr3 = readTrr3(cropDir / TRR3)
    cube = trr3.lines(slice(0, 16))
    assert cube.shape == (16, 120, 438)
    assert cube.dtype == np.float64
    assert trr3.shape == (16, 120, 438)
    assert (trr3.binning, trr3.sclk) == (1, 1090233681.08006)
    assert trr3.wavelengthFileName == "CDR490947778566_WA0000000L_3.IMG"
    raw = np.fromfile(cropDir / "frs00031442_01_if168l_trr3.img", dtype="<f4")
    raw = raw[: 16 * 438 * 120]
    bad = (raw == 65535) | (raw < 1e-6) | ~np.isfinite(raw)
    assert bad.any()
    assert np.isnan(cube).mean() == pytest.approx(bad.mean())
    # a partial slice reads only those rows, in the same order
    np.testing.assert_array_equal(trr3.lines(slice(3, 5)), cube[3:5])


def test_rownum_table(cropDir: Path) -> None:
    rows = readTrr3(cropDir / TRR3).detectorRows
    assert rows.shape == (438,)
    assert np.all(np.diff(rows) < 0)
    assert rows.max() <= 445


def test_ascending_wavelengths(cropDir: Path) -> None:
    trr3 = readTrr3(cropDir / TRR3)
    wavelengths = readDetectorFrame(cropDir / WA).alignedTo(trr3.detectorRows)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # the all-NaN band
        medians = np.nanmedian(wavelengths, axis=0)
    valid = medians[np.isfinite(medians)]
    assert np.all(np.diff(valid) > 0)
    assert valid[0] == pytest.approx(1001, abs=2)
    assert valid[-1] == pytest.approx(3936, abs=2)
    assert np.isnan(medians).sum() == 1  # the 65.535 um placeholder band


def test_ddr_band_names(cropDir: Path) -> None:
    ddr = readDdr(cropDir / DDR)
    incidence = ddr.band(DdrBand.INCIDENCE_AREOID)
    assert incidence.shape == (16, 120)
    assert np.nanmin(incidence) > 0
    assert np.nanmax(incidence) < 90
    assert ddr.label.bandNames[DdrBand.SPARE] == "Spare"
    assert ddr.band(DdrBand.LATITUDE, slice(2, 4)).shape == (2, 120)


def test_detector_frame_alignment() -> None:
    frame = DetectorFrame("x", np.arange(6.0).reshape(2, 3), np.array([5, 7, 6]))
    np.testing.assert_array_equal(
        frame.alignedTo(np.array([6, 5])), [[2.0, 0.0], [5.0, 3.0]]
    )
    with pytest.raises(ValueError, match="detector row 9"):
        frame.alignedTo(np.array([5, 9]))


def _syntheticDdr(tmp_path: Path, steps: np.ndarray) -> Ddr:
    lines = len(steps) + 1
    cube = np.zeros((14, lines, 4), dtype="<f4")
    cube[DdrBand.LATITUDE] = np.concatenate(([0.0], np.cumsum(steps)))[:, None]
    cube[DdrBand.LONGITUDE] = np.arange(4) * 0.001
    path = tmp_path / "synthetic.img"
    cube.tofile(path)
    layout = ArrayLayout(path, 0, lines, 4, 14, "BSQ", np.dtype("<f4"))
    label = ProductLabel(tmp_path / "synthetic.lbl", {}, layout)
    return Ddr(ProductId.parse("frs00031442_01_de168l_ddr1"), label)


def test_usable_lines_drops_slew(tmp_path: Path) -> None:
    steps = np.full(40, 0.001)  # 41 rows
    steps[:4] = 0.01  # rows 1-4 step 10x farther; row 0 copies row 1
    ddr = _syntheticDdr(tmp_path, steps)
    usable = usableLines(ddr)
    assert usable.shape == (41,)
    assert not usable[:5].any()
    assert usable[5:].all()


def test_vs_adr_layout(demoData: Path, cropDir: Path) -> None:
    path = demoData / VS_ADR
    if not path.exists():
        raise pytest.skip.Exception(f"{path.name} not found")
    transmission, mcguire, pelkey = readVsAdr(path)
    for frame in (transmission, mcguire, pelkey):
        assert frame.values.shape == (640, 438)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        wavelengths = np.nanmedian(
            readDetectorFrame(cropDir / WA).alignedTo(transmission.detectorRows),
            axis=0,
        )
    band = int(np.nanargmin(np.abs(wavelengths - 2007)))
    assert np.nanmedian(transmission.values[:, band]) < 0.8
