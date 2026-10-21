from pathlib import Path

import numpy as np
import pytest
from pvl.collections import Quantity

from varda.crism.labels import _pointer, readLabel
from varda.crism.readers import readTrr3


def test_trr3_crop_label(cropDir: Path):
    label = readLabel(cropDir / "frs00031442_01_if168l_trr3.lbl")
    image = label.image
    assert (image.lines, image.samples, image.bands) == (16, 120, 438)
    assert image.interleave == "BIL"
    assert image.dtype == np.dtype("<f4")
    assert image.offset == 0
    assert image.rownumOffset == 16 * 438 * 120 * 4
    assert label.get("MRO:WAVELENGTH_FILE_NAME") == "CDR490947778566_WA0000000L_3.IMG"
    assert image.dataFile.name == "frs00031442_01_if168l_trr3.img"
    assert image.memmap().shape == (16, 438, 120)


def test_ddr_band_names(cropDir: Path):
    label = readLabel(cropDir / "frs00031442_01_de168l_ddr1.lbl")
    assert len(label.bandNames) == 14
    assert label.bandNames[0] == "INA at areoid, deg"
    assert label.bandNames[13] == "Spare"
    assert label.image.interleave == "BSQ"
    assert label.image.memmap().shape == (14, 16, 120)


def test_cdr_label(cropDir: Path):
    image = readLabel(cropDir / "cdr490947778566_wa0000000l_3.lbl").image
    assert image.lines == 1
    assert image.bands == 438
    assert image.rownumOffset == 438 * 120 * 4


def test_source_products_list(cropDir: Path):
    label = readLabel(cropDir / "frs00031442_01_if168l_trr3.lbl")
    sources = label.get("SOURCE_PRODUCT_ID")
    assert isinstance(sources, list)
    assert "FRS00031442_01_SC168L_EDR0" in sources


def test_pds4_matches_pds3(demoData: Path):
    folder = demoData / "data-for-paper/FRT00005850/trdr"
    xml = readLabel(folder / "frt00005850_07_if167l_trr3.xml")
    pds3 = readLabel(folder / "frt00005850_07_if167l_trr3.lbl")
    for field in ("lines", "samples", "bands", "interleave", "dtype", "offset"):
        assert getattr(xml.image, field) == getattr(pds3.image, field)
    assert xml.image.rownumOffset == pds3.image.rownumOffset
    assert xml.get("MISSING_CONSTANT") == 65535


def test_pds4_ddr_bands(demoData: Path):
    folder = demoData / "data-for-paper/FRT00005850/ddr"
    xml = readLabel(folder / "frt00005850_07_de167l_ddr1.xml")
    pds3 = readLabel(folder / "frt00005850_07_de167l_ddr1.lbl")
    assert xml.image.bands == pds3.image.bands == len(xml.bandNames)
    assert xml.image.interleave == "BSQ"


def test_unsupported_pointer_raises():
    with pytest.raises(ValueError):
        _pointer(5)
    with pytest.raises(ValueError):
        _pointer(Quantity(5, "BYTES"))


PDS4_KEYS = (
    "MRO:WAVELENGTH_FILE_NAME",
    "INSTRUMENT_ID",
    "PIXEL_AVERAGING_WIDTH",
    "SPACECRAFT_CLOCK_START_COUNT",
    "PRODUCT_ID",
    "START_TIME",
)


def test_pds4_keywords_match_pds3(demoData: Path):
    base = demoData / "data-for-paper/FRT00005850/trdr/frt00005850_07_if167l_trr3"
    if not base.with_suffix(".xml").exists():
        raise pytest.skip.Exception("demo PDS4 TRR3 not found")
    pds3, pds4 = (
        readLabel(base.with_suffix(".lbl")),
        readLabel(base.with_suffix(".xml")),
    )
    for key in PDS4_KEYS:
        assert pds4.get(key) is not None, key
        assert pds4.get(key) == pds3.get(key), key
    a, b = readTrr3(base.with_suffix(".lbl")), readTrr3(base.with_suffix(".xml"))
    assert (a.binning, a.sclk, a.wavelengthFileName) == (
        b.binning,
        b.sclk,
        b.wavelengthFileName,
    )
