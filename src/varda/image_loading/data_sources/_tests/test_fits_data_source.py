"""FITS images and cubes, from synthetic files written with astropy.

The ALTWG-like cube follows the OSIRIS-REx Altimetry SIS (UA-SIS-9.4.4-307,
§5.2.1.2): PLANEn names with units in the comment's brackets, TARGET,
lat/lon/radius/X/Y/Z first."""

import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.io.fits.file import _File

from varda.common.di_types import ProjectImages
from varda.image_loading.data_sources.cached_data_source import CachedDataSource
from varda.image_loading.data_sources.fits_data_source import (
    FITS_HEADER_KEY,
    FITS_KEY,
    FitsDataSource,
    FitsError,
    splitPlane,
)
from varda.image_loading.image_loading_service import (
    loadRaster,
    openDataSource,
    wrapForReading,
)
from varda.session import captureSession, readSession, writeSession
from varda.session_restore import SessionRestorer

ROWS, COLUMNS = 4, 5
PLANES = [
    ("Latitude of vertices", "[deg]"),
    ("Longitude of vertices", "[deg]"),
    ("Radius of vertices", "[km]"),
    ("X coordinate of vertices", "[km]"),
    ("Y coordinate of vertices", "[km]"),
    ("Z coordinate of vertices", "[km]"),
    ("Height relative to normal plane", "[km]"),
    ("Sigma", ""),
    ("Elevation", "[m]"),
    ("Boulder count", ""),  # not in the SIS
]


def _cube() -> np.ndarray:
    """(planes, NAXIS2, NAXIS1); plane p, row y, column x holds p*100 + y*10 + x."""
    p, y, x = np.indices((len(PLANES), ROWS, COLUMNS))
    return (p * 100 + y * 10 + x).astype(np.float32)


def writeAltwg(path: Path, target: str = "101955 BENNU") -> Path:
    data = _cube()
    data[8, 0, 0] = np.nan  # documented no-data
    data[8, 0, 1] = -1.0e38  # older fill
    hdu = fits.PrimaryHDU(data)
    hdu.header["TARGET"] = (target, "Target object")
    hdu.header["MAP_NAME"] = "Non-NFT DTM"
    hdu.header["SIGMA"] = (0.0, "Global uncertainty of the data [m]")
    for n, (name, comment) in enumerate(PLANES, start=1):
        hdu.header[f"PLANE{n}"] = (name, comment)
    hdu.writeto(path)
    return path


def test_an_altwg_cube_opens_with_named_unit_bearing_planes(tmp_path):
    source = openDataSource(str(writeAltwg(tmp_path / "l_dtm.fits")))
    assert isinstance(source, FitsDataSource)
    assert (source.height, source.width, source.bandCount) == (ROWS, COLUMNS, 10)
    assert source.bandNames[0] == "Latitude of vertices (deg)"
    assert source.bandNames[8] == "Elevation (m)"
    assert source.bandNames[7] == "Sigma"  # uncertainty stays a named band
    assert source.planeUnits[:3] == ["deg", "deg", "km"]
    assert source.isParameterImage
    assert source.crs is None and source.transform.is_identity
    # the elevation plane is shown by default
    assert list(source.defaultBands) == [8, 8, 8]


def test_axis_order_follows_fits(tmp_path):
    """NAXIS1 is x (columns): pixel (x, y) of plane p is p*100 + y*10 + x."""
    source = FitsDataSource(str(writeAltwg(tmp_path / "a.fits")))
    data = source.readAllBands()
    assert data.shape == (ROWS, COLUMNS, len(PLANES))
    assert data[2, 3, 4] == 4 * 100 + 2 * 10 + 3
    assert source.getPixelSpectrum(3, 2)[4] == 423
    np.testing.assert_array_equal(
        source.getData([1, 2], window=(1, 2, 2, 2))[..., 0], [[112, 113], [122, 123]]
    )
    assert source[2, 3, 4] == 423
    assert source[1:3, 2:4, 1].shape == (2, 2)


def test_altwg_fill_and_nan_are_no_data(tmp_path):
    source = FitsDataSource(str(writeAltwg(tmp_path / "a.fits")))
    elevation = source.getBands([8])[..., 0]
    assert np.isnan(elevation[0, 0]) and np.isnan(elevation[0, 1])
    assert elevation[0, 2] == 802
    assert "ALTWG fill" in source.extraMetadata[FITS_KEY]["noData"]


def test_metadata_records_the_header_body_and_geometry(tmp_path):
    source = FitsDataSource(str(writeAltwg(tmp_path / "a.fits")))
    metadata = source.extraMetadata
    assert "PLANE9  = 'Elevation'" in metadata[FITS_HEADER_KEY]
    record = metadata[FITS_KEY]
    assert record["target"] == "101955 BENNU"
    assert record["body"] is None  # unknown to Varda: never Earth
    assert record["geometryPlanes"] == {
        "latitude": 0, "longitude": 1, "radius": 2, "x": 3, "y": 4, "z": 5,
    }  # fmt: skip
    assert record["uncertaintyPlanes"] == [7]
    assert record["unrecognizedPlanes"] == ["Boulder count"]
    assert record["productKeywords"]["SIGMA"]["value"] == 0.0


def test_a_known_body_is_named(tmp_path):
    source = FitsDataSource(str(writeAltwg(tmp_path / "a.fits", target="4 VESTA")))
    assert source.extraMetadata[FITS_KEY]["body"] == "Vesta"


@pytest.mark.parametrize(
    ("value", "comment", "expected"),
    [
        ("Elevation", "[m]", ("Elevation", "m")),
        ("Latitude (deg)", "", ("Latitude", "deg")),
        ("Latitude", "Degrees", ("Latitude", "Degrees")),  # Map Format SIS
        ("Hazard Mask", "", ("Hazard Mask", "")),
        ("Slope", "Data plane description", ("Slope", "")),
    ],
)
def test_plane_units_come_from_the_name_or_comment(value, comment, expected):
    assert splitPlane(value, comment) == expected


def test_plane_underscore_cards(tmp_path):
    hdu = fits.PrimaryHDU(np.zeros((2, 3, 3), dtype=np.float32))
    hdu.header["PLANE_01"] = ("Hazard Mask", "")
    hdu.header["PLANE_02"] = ("Latitude", "Degrees")
    hdu.writeto(tmp_path / "m.fits")
    source = FitsDataSource(str(tmp_path / "m.fits"))
    assert source.bandNames == ["Hazard Mask", "Latitude (Degrees)"]


def test_bscale_bzero_and_blank(tmp_path):
    stored = np.array([[0, 1, 2], [3, -32768, 5]], dtype=np.int16)
    hdu = fits.PrimaryHDU(stored)
    hdu.header["BSCALE"] = 0.5
    hdu.header["BZERO"] = 100.0
    hdu.header["BLANK"] = -32768
    hdu.header["BUNIT"] = "K"
    hdu.writeto(tmp_path / "s.fits")
    source = FitsDataSource(str(tmp_path / "s.fits"))
    assert source.dtype == np.float32
    values = source.readAllBands()[..., 0]
    np.testing.assert_array_equal(values[0], [100.0, 100.5, 101.0])
    assert np.isnan(values[1, 1]) and values[1, 2] == 102.5
    assert source.nodata == -32768 * 0.5 + 100.0
    raw = source.getData(masked=False)[..., 0]
    assert raw[1, 1] == source.nodata
    assert source.bandNames == ["Plane 1 (K)"]


def test_unsigned_16_bit_convention(tmp_path):
    """uint16 is stored as int16 with BZERO = 32768 (Standard §5.3)."""
    fits.PrimaryHDU(np.array([[0, 65535]], dtype=np.uint16)).writeto(
        tmp_path / "u.fits"
    )
    values = FitsDataSource(str(tmp_path / "u.fits")).readAllBands()[..., 0]
    np.testing.assert_array_equal(values, [[0, 65535]])


def test_an_image_extension_is_read_when_the_primary_is_empty(tmp_path):
    image = fits.ImageHDU(np.arange(12, dtype=np.float64).reshape(3, 4), name="SCI")
    fits.HDUList([fits.PrimaryHDU(), image]).writeto(tmp_path / "e.fits")
    source = FitsDataSource(str(tmp_path / "e.fits"))
    assert (source.height, source.width, source.bandCount) == (3, 4, 1)
    assert source.bandNames == ["SCI"]
    assert source.readAllBands()[2, 3, 0] == 11
    assert source.extraMetadata[FITS_KEY]["hdu"] == 1


def test_a_plain_astronomical_image_opens_and_its_wcs_is_noted(tmp_path):
    hdu = fits.PrimaryHDU(np.ones((8, 6), dtype=np.float32))
    hdu.header["OBJECT"] = "M31"
    hdu.header["CTYPE1"] = "RA---TAN"
    hdu.header["CTYPE2"] = "DEC--TAN"
    hdu.header["CRVAL1"] = 10.68
    hdu.header["CRVAL2"] = 41.27
    hdu.writeto(tmp_path / "m31.fits")
    image = loadRaster(str(tmp_path / "m31.fits"))
    assert image.shape == (8, 6, 1) and image.crs is None
    record = image.extraMetadata[FITS_KEY]
    assert record["wcs"]["kind"] == "celestial"
    assert record["wcs"]["keywords"]["CTYPE1"] == "RA---TAN"
    assert record["target"] == "M31" and record["body"] is None


def test_a_spectral_cube_gets_wavelengths(tmp_path):
    hdu = fits.PrimaryHDU(np.zeros((5, 2, 2), dtype=np.float32))
    hdu.header["CTYPE3"] = "WAVE"
    hdu.header["CUNIT3"] = "um"
    hdu.header["CRVAL3"] = 1.0
    hdu.header["CRPIX3"] = 1.0
    hdu.header["CDELT3"] = 0.5
    hdu.writeto(tmp_path / "c.fits")
    source = FitsDataSource(str(tmp_path / "c.fits"))
    np.testing.assert_allclose(source.wavelengths, [1000, 1500, 2000, 2500, 3000])
    assert source.wavelengthUnits == "nm"


def test_a_truncated_file_is_a_clear_error(tmp_path):
    path = writeAltwg(tmp_path / "t.fits")
    path.write_bytes(path.read_bytes()[: 2880 + 100])  # the header, then a little
    with pytest.raises(FitsError, match="truncated"):
        FitsDataSource(str(path))


def test_a_file_that_isnt_fits_is_a_clear_error(tmp_path):
    path = tmp_path / "x.fits"
    path.write_bytes(b"not a fits file at all" * 200)
    with pytest.raises(FitsError, match="isn't a readable FITS file"):
        FitsDataSource(str(path))


def test_a_table_only_file_says_tables_arent_supported(tmp_path):
    table = fits.BinTableHDU.from_columns(
        [fits.Column(name="FACET_NUM", format="J", array=np.arange(3))]
    )
    fits.HDUList([fits.PrimaryHDU(), table]).writeto(tmp_path / "anc.fits")
    with pytest.raises(FitsError, match="tables"):
        FitsDataSource(str(tmp_path / "anc.fits"))


def test_pixels_are_read_lazily_from_a_memory_map(tmp_path):
    source = FitsDataSource(str(writeAltwg(tmp_path / "a.fits")))
    assert source.isMemoryMapped
    wrapped = wrapForReading(source, limitBytes=0)  # too big: read on demand
    assert isinstance(wrapped, CachedDataSource)
    assert wrapped.getData([4], window=(2, 3, 1, 1))[0, 0, 0] == 423
    source.close()


def test_a_session_round_trip_keeps_the_image_and_its_edits(qtbot, tmp_path):
    path = writeAltwg(tmp_path / "a.fits")
    image = loadRaster(str(path))
    names = list(image.bandNames)
    names[9] = "Boulders per m²"
    image.setMetadataOverrides({"bandNames": names}, name="Site DTM")
    capture = captureSession([image], [])
    state = readSession(writeSession(capture.state, tmp_path / "work"))
    assert state.images == (str(path),)

    again = loadRaster(str(path))
    restorer = SessionRestorer(
        ProjectImages(), None, loadImage=lambda _path, done, _failed: done(again)
    )
    with qtbot.waitSignal(restorer.sigFinished, timeout=10_000):
        restorer.restore(state)
    assert again.name == "Site DTM"
    assert again.bandNames[9] == "Boulders per m²"
    assert again.bandNames[8] == "Elevation (m)"
    assert again.extraMetadata[FITS_KEY]["target"] == "101955 BENNU"


def test_a_tile_compressed_image_opens(tmp_path):
    data = np.arange(64, dtype=np.float32).reshape(8, 8)
    hdu = fits.CompImageHDU(data, compression_type="GZIP_1")
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(tmp_path / "c.fits.fz")
    source = openDataSource(str(tmp_path / "c.fits.fz"))
    assert isinstance(source, FitsDataSource)
    np.testing.assert_array_equal(source.readAllBands()[..., 0], data)


def test_a_dtm_without_a_crs_is_not_flagged(tmp_path):
    image = loadRaster(str(writeAltwg(tmp_path / "a.fits")))
    assert image.crs is None
    assert image.crsHealth is not None and image.crsHealth.issues == ()


def test_close_releases_the_memory_map_and_the_file(tmp_path, monkeypatch):
    """After close() astropy can close the map (no array of ours holds it),
    so the file can be deleted and replaced, on Windows too."""
    closedMaps: list[bool] = []
    original = _File._maybe_close_mmap

    def spy(self, refcount_delta=0):
        had = self._mmap is not None
        original(self, refcount_delta)
        closedMaps.append(had and self._mmap is None)

    monkeypatch.setattr(_File, "_maybe_close_mmap", spy)
    path = writeAltwg(tmp_path / "a.fits")
    source = FitsDataSource(str(path))
    assert source.getData([4], window=(2, 3, 1, 1))[0, 0, 0] == 423
    source.close()
    assert any(closedMaps), "the memory map is still referenced"
    assert not source.isMemoryMapped
    with pytest.raises(FitsError, match="closed"):
        source.getData()
    assert source.width == COLUMNS and source.bandNames[8] == "Elevation (m)"

    os.remove(path)
    fits.PrimaryHDU(np.full((2, 2), 7.0, dtype=np.float32)).writeto(path)
    again = FitsDataSource(str(path))
    assert again.readAllBands()[0, 0, 0] == 7.0
    again.close()


def test_an_extensions_own_header_is_shown(tmp_path):
    image = fits.ImageHDU(np.zeros((3, 3), dtype=np.float32), name="SCI")
    image.header["BUNIT"] = "DN"
    primary = fits.PrimaryHDU()
    primary.header["TELESCOP"] = "Test"
    fits.HDUList([primary, image]).writeto(tmp_path / "e.fits")
    text = FitsDataSource(str(tmp_path / "e.fits")).extraMetadata[FITS_HEADER_KEY]
    assert "TELESCOP= 'Test" in text
    assert "# HDU 1 (SCI), the image" in text and "BUNIT   = 'DN" in text
