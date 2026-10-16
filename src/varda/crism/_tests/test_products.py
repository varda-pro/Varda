import pytest

from varda.crism.products import CdrId, ProductId, VsAdrId


def test_parse_trr3():
    p = ProductId.parse("FRS00031442_01_IF168L_TRR3.IMG")
    assert p.classType == "FRS"
    assert p.observationId == 0x31442
    assert p.counter == 1
    assert p.activity == "IF"
    assert p.macro == 168
    assert p.sensor == "L"
    assert p.productType == "TRR"
    assert p.version == "3"
    assert p.stem == "frs00031442_01_if168l_trr3"
    assert p.observationName == "FRS00031442"


def test_parse_ddr_and_mtrdr():
    ddr = ProductId.parse("frt00005850_07_de167l_ddr1.lbl")
    assert (ddr.productType, ddr.version) == ("DDR", "1")
    mtr = ProductId.parse("/data/frt00005850_07_if167j_mtr3")
    assert (mtr.sensor, mtr.productType) == ("J", "MTR")


def test_replace_round_trip():
    p = ProductId.parse("frt00005850_07_if167l_trr3.hdr")
    ddr = p.replace(activity="DE", productType="DDR", version="1")
    assert ddr.stem == "frt00005850_07_de167l_ddr1"


def test_parse_cdr4():
    c = CdrId.parse("CDR420843667218_AT0000000L_7")
    assert (c.level, c.partition, c.sclk) == (4, 2, 843667218)
    assert (c.kind, c.binning, c.wavelengthFilter, c.sensor, c.version) == (
        "AT",
        0,
        0,
        "L",
        7,
    )
    assert (c.frameRate, c.exposure, c.side) == (0, 0, 0)


def test_parse_cdr4_binned():
    c = CdrId.parse("CDR410803692813_WA0100000L_3.IMG")
    assert (c.kind, c.binning, c.sensor, c.version) == ("WA", 1, "L", 3)


def test_parse_cdr6():
    c = CdrId.parse("CDR6_1_0000000000_SW_L_3.TAB")
    assert (c.level, c.partition, c.sclk) == (6, 1, 0)
    assert (c.kind, c.sensor, c.version) == ("SW", "L", 3)
    assert c.binning is None


def test_parse_vs_adr():
    v = VsAdrId.parse("ADR50924300803_28D04_VS00L_9.IMG")
    assert (v.partition, v.sclk) == (5, 924300803)
    assert (v.scanId, v.binning, v.wavelengthFilter, v.sensor, v.version) == (
        "28D04",
        0,
        0,
        "L",
        9,
    )


@pytest.mark.parametrize("cls", [ProductId, CdrId, VsAdrId])
def test_rejects_garbage(cls):
    with pytest.raises(ValueError):
        cls.parse("notcrism.img")
