"""CRISM products open in Varda: TRR3/DDR/CDR labels through GDAL with the
bands in ascending wavelength, MTRDR/TER labels through their ENVI header, and
the ENVI reader's sigma companion keys."""

import os
import shutil
from pathlib import Path

import numpy as np
import pytest
import spectral.io.envi as envi
from pyproj import Transformer

from varda.common.spectral_axis import parseEnviList
from varda.crism.readers import DdrBand, readDdr, readTrr3
from varda.image_loading.data_sources.crism_data_source import (
    CrismMapLabelDataSource,
    CrismProductDataSource,
)
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource
from varda.image_loading.image_loading_service import openDataSource

CROPS = Path(__file__).parents[3] / "crism" / "_tests" / "data"
TRR3 = "frs00031442_01_if168l_trr3.lbl"
DDR = "frs00031442_01_de168l_ddr1.lbl"
WA = "cdr490947778566_wa0000000l_3"
MTR_FOLDER = "data-for-paper/FRT00005850/mtrdr"
MTRDR = "frt00005850_07_if167j_mtr3"
DE = "frt00005850_07_de167l_mtr1"


@pytest.fixture
def demoMtrdr() -> Path:
    folder = Path(
        os.environ.get("VARDA_DEMO_DATA", Path(__file__).parents[6] / "varda-demo-data")
    )
    path = folder / MTR_FOLDER / f"{MTRDR}.lbl"
    if not path.exists():
        raise pytest.skip.Exception(f"demo MTRDR not found at {path}")
    return path


@pytest.fixture
def cropFolder(tmp_path: Path) -> Path:
    """The TRR3 and DDR crops, with the WA CDR in a CRISM_CDR/wa folder above."""
    folder = tmp_path / "trdr"
    folder.mkdir()
    for suffix in (".lbl", ".img"):
        for name in (TRR3, DDR):
            shutil.copy(CROPS / name.replace(".lbl", suffix), folder)
    wa = tmp_path / "CRISM_CDR" / "wa"
    wa.mkdir(parents=True)
    for suffix in (".lbl", ".img"):
        shutil.copy(CROPS / f"{WA}{suffix}", wa)
    return folder


def test_opens_trr3_crop(cropFolder: Path) -> None:
    source = openDataSource(str(cropFolder / TRR3))
    assert isinstance(source, CrismProductDataSource)
    assert source.bandCount == 438
    assert source.nodata == 65535
    wavelengths = source.wavelengths
    assert np.all(np.diff(wavelengths) > 0)
    assert wavelengths[0] == pytest.approx(1001, abs=2)
    assert wavelengths[-2] == pytest.approx(3936, abs=2)
    assert wavelengths[-1] == 65535  # the 65.535 um placeholder
    assert source.wavelengthUnits == "Nanometers"
    bbl = parseEnviList(source.extraMetadata["bbl"])
    assert bbl is not None and bbl[-1] == 0 and sum(bbl) == 437
    assert source.extraMetadata["crismColumnWavelengths"].shape == (120, 438)

    expected = readTrr3(cropFolder / TRR3).lines(slice(5, 6))[0, 5]
    spectrum = source.getPixelSpectrum(5, 5)
    assert np.array_equal(np.isnan(spectrum), np.isnan(expected))
    assert np.isnan(spectrum).any()
    np.testing.assert_allclose(spectrum, expected, rtol=1e-6, equal_nan=True)


def test_band_reads_follow_ascending_wavelength(cropFolder: Path) -> None:
    source = openDataSource(str(cropFolder / TRR3))
    cube = readTrr3(cropFolder / TRR3).lines(slice(0, 4))
    np.testing.assert_allclose(
        source.getData(window=(0, 0, 4, 120)), cube, rtol=1e-6, equal_nan=True
    )
    bands = [3, 250, 0]
    np.testing.assert_allclose(
        source.getBands(bands)[:4], cube[..., bands], rtol=1e-6, equal_nan=True
    )
    np.testing.assert_allclose(
        source[0:4, 2:7, 10:12], cube[:, 2:7, 10:12], rtol=1e-6, equal_nan=True
    )
    raw = source.getData(window=(0, 0, 1, 1), masked=False)
    assert raw.shape == (1, 1, 438)
    assert not np.isnan(raw).any()


def test_wavelengths_without_the_wa_file_are_band_numbers(tmp_path: Path) -> None:
    for suffix in (".lbl", ".img"):
        shutil.copy(CROPS / TRR3.replace(".lbl", suffix), tmp_path)
    source = openDataSource(str(tmp_path / TRR3))
    assert isinstance(source, CrismProductDataSource)
    np.testing.assert_array_equal(source.wavelengths, np.arange(1, 439))
    assert source.wavelengthUnits == "Band number"
    assert "crismColumnWavelengths" not in source.extraMetadata


def test_ddr_band_names_in_app(cropFolder: Path) -> None:
    source = openDataSource(str(cropFolder / DDR))
    assert isinstance(source, CrismProductDataSource)
    assert source.bandNames[0] == "INA at areoid, deg"
    latitude = source.getBands([DdrBand.LATITUDE])[..., 0]
    np.testing.assert_allclose(
        latitude, readDdr(cropFolder / DDR).band(DdrBand.LATITUDE), equal_nan=True
    )


def test_cdr_opens_and_other_labels_are_not_claimed(cropFolder: Path) -> None:
    wa = openDataSource(str(cropFolder.parent / "CRISM_CDR" / "wa" / f"{WA}.lbl"))
    assert isinstance(wa, CrismProductDataSource)
    assert not CrismProductDataSource.canOpen(str(CROPS / f"{MTRDR}.lbl"))
    assert not CrismProductDataSource.canOpen(str(cropFolder / "unrelated.lbl"))


def test_mtrdr_label_uses_hdr(demoMtrdr: Path) -> None:
    source = openDataSource(str(demoMtrdr))
    assert isinstance(source, CrismMapLabelDataSource)
    reference = ENVIDataSource(str(demoMtrdr.with_suffix(".hdr")))
    assert source.transform == reference.transform
    assert (source.transform.c, source.transform.f) == pytest.approx(
        (4417887.6, 1091944.9)
    )
    assert source.crs is not None


def test_map_label_needs_a_sibling_header(tmp_path: Path) -> None:
    label = tmp_path / f"{MTRDR}.lbl"
    shutil.copy(CROPS / label.name, tmp_path)
    assert not CrismMapLabelDataSource.canOpen(str(label))
    shutil.copy(CROPS / f"{MTRDR}.hdr", tmp_path)
    assert CrismMapLabelDataSource.canOpen(str(label))


def test_hdr_georeference_matches_de_backplanes(demoMtrdr: Path) -> None:
    source = ENVIDataSource(str(demoMtrdr.with_suffix(".hdr")))
    de = ENVIDataSource(str(demoMtrdr.with_name(f"{DE}.hdr")))
    lat = de.getData(bandIndices=[3])[..., 0]
    lon = de.getData(bandIndices=[4])[..., 0]
    valid = np.argwhere(np.isfinite(lat) & np.isfinite(lon))
    chosen = valid[np.random.default_rng(0).choice(len(valid), 200, replace=False)]
    assert source.crs is not None
    toMap = Transformer.from_crs(source.crs.geodetic_crs, source.crs, always_xy=True)
    offsets = []
    for row, col in chosen:
        x, y = toMap.transform(lon[row, col], lat[row, col])
        px, py = source.pixelToGeo(int(col), int(row))
        offsets.append((x - px, y - py))
    dx, dy = np.median(offsets, axis=0)
    # the DE pixels scatter by ~10 m (the footprint geometry is not resampled
    # with the cube), so 200 medians land within a few meters of zero; the
    # label's one-pixel offset would bias them by 18 m
    assert abs(dx) < 3.0 and abs(dy) < 3.0
    assert np.percentile(np.abs(offsets), 95) < 18.0


def test_mtrdr_round_trip_metadata(demoMtrdr: Path) -> None:
    source = ENVIDataSource(str(demoMtrdr.with_suffix(".hdr")))
    assert source.nodata == 65535
    assert len(source.wavelengths) == 489
    bbl = parseEnviList(source.extraMetadata["bbl"])
    assert bbl is not None and len(bbl) == 489
    assert "fwhm" in source.extraMetadata


@pytest.mark.filterwarnings("ignore::rasterio.errors.NotGeoreferencedWarning")
def test_sigma_companion_metadata(tmp_path: Path) -> None:
    cube = np.zeros((3, 4, 2), dtype=np.float32)
    header = tmp_path / "x_vmtr.hdr"
    envi.save_image(
        str(header),
        cube,
        dtype=np.float32,
        ext=".img",
        force=True,
        metadata={
            "varda sigma file": "x_sigma.img",
            "varda sigma kind": "instrument-noise",
            "varda sigma steps": ["despike", "atm"],
        },
    )
    source = openDataSource(str(header))
    assert isinstance(source, ENVIDataSource)
    assert source.extraMetadata["sigma"] == {
        "path": str(tmp_path / "x_sigma.img"),
        "kind": "instrument-noise",
        "steps": ["despike", "atm"],
    }
    assert "varda_sigma_file" not in source.extraMetadata
    plain = tmp_path / "plain.hdr"
    envi.save_image(str(plain), cube, dtype=np.float32, ext=".img", force=True)
    assert "sigma" not in openDataSource(str(plain)).extraMetadata


def test_pds4_trr3_gets_wavelengths_from_the_wa_cdr(tmp_path: Path, demoMtrdr: Path):
    demo = demoMtrdr.parents[2]
    trdr = demo / "FRT00005850/trdr"
    waName = "CDR410803692813_WA0000000L_3"
    wa = demo / "CAT_v7_4_aux/aux_files/CDRs/WA"
    if (
        not (trdr / "frt00005850_07_if167l_trr3.xml").exists()
        or not (wa / f"{waName}.IMG").exists()
    ):
        raise pytest.skip.Exception("demo PDS4 TRR3 or its WA CDR not found")
    folder = tmp_path / "trdr"
    folder.mkdir()
    for suffix in (".xml", ".img"):
        (folder / f"frt00005850_07_if167l_trr3{suffix}").symlink_to(
            trdr / f"frt00005850_07_if167l_trr3{suffix}"
        )
    cdr = tmp_path / "CRISM_CDR" / "wa"
    cdr.mkdir(parents=True)
    for suffix in (".LBL", ".IMG"):
        shutil.copy(wa / f"{waName}{suffix}", cdr)
    source = openDataSource(str(folder / "frt00005850_07_if167l_trr3.xml"))
    assert isinstance(source, CrismProductDataSource)
    assert source.wavelengthUnits == "Nanometers"
    assert np.all(np.diff(source.wavelengths) > 0)
    assert source.wavelengths[0] == pytest.approx(1001, abs=5)
    assert "crismColumnWavelengths" in source.extraMetadata


def test_a_corrupt_label_is_not_opened(tmp_path: Path) -> None:
    for name in (
        f"{MTRDR.replace('if167j_mtr3', 'if167j_trr3')}.lbl",
        "frs00031442_01_if168l_trr3.xml",
    ):
        (tmp_path / name).write_text("<<< not a label")
        assert not CrismProductDataSource.canOpen(str(tmp_path / name))
