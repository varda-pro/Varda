"""Synthetic test data the personas open, made fresh for every run.

Nothing here is committed: every file is written into the run's own folder.
Real products (``~/Documents/Software/varda-aux``) are used only when present
(``realProducts``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# First: varda drops a foreign PROJ_DATA/GDAL_DATA (e.g. conda's) before
# rasterio loads PROJ, as the app does at launch
import varda  # noqa: F401
import numpy as np
import rasterio
from affine import Affine
from rasterio import CRS

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.raster_writer import writeRaster

ROWS, COLUMNS, BANDS = 48, 64, 40
WAVELENGTHS = np.linspace(1000.0, 2600.0, BANDS)  # nm, CRISM-like
# Folder names a real user's data lives under: spaces, accents, CJK
AWKWARD_FOLDER = "My Data/Ünïcødé 火星 scans"
REAL_DATA = Path.home() / "Documents" / "Software" / "varda-aux" / "ode"


@dataclass(frozen=True)
class QaData:
    folder: Path
    cube: Path  # ENVI, georeferenced (UTM), 40 bands with wavelengths
    geotiff: Path  # the same scene as a GeoTIFF
    awkward: Path  # GeoTIFF under a folder with spaces and unicode
    marsWgs84: Path  # Mars pixels tagged WGS 84, with a PDS3 label saying MARS
    marsLabel: Path  # a PDS3 label for the same pixels, saying TARGET_NAME = MARS
    noCrs: Path  # a GeoTIFF without any georeferencing
    corrupt: Path  # random bytes named .tif
    truncated: Path  # a valid GeoTIFF cut in half
    zeroBand: Path  # an ENVI header that declares no bands
    empty: Path  # a zero-byte .tif
    caseTwin: (
        Path | None
    )  # "Scene.tif" beside "scene.tif" (case-sensitive file systems)


def _spectra() -> np.ndarray:
    """A smooth scene: two materials mixed across the image, with an
    absorption near 1900 nm in one of them, and a little noise."""
    rng = np.random.default_rng(42)
    yy, xx = np.mgrid[0:ROWS, 0:COLUMNS]
    mix = (xx / (COLUMNS - 1))[..., None]
    a = 0.25 + 0.05 * np.sin(WAVELENGTHS / 300.0)
    b = 0.35 - 0.12 * np.exp(-(((WAVELENGTHS - 1900.0) / 60.0) ** 2))
    data = (1 - mix) * a + mix * b + 0.02 * (yy / ROWS)[..., None]
    data += rng.normal(0, 0.003, data.shape)
    return data.astype(np.float32)


def _raster(name: str, crs: CRS | None, transform: Affine | None) -> VardaRaster:
    source = ArrayDataSource(
        _spectra(),
        wavelengths=WAVELENGTHS,
        wavelengthUnits="Nanometers",
        crs=crs,
        transform=transform,
    )
    return VardaRaster(dataSource=source, name=name)


UTM = CRS.from_epsg(32612)
UTM_TRANSFORM = Affine(18.0, 0.0, 500000.0, 0.0, -18.0, 3500000.0)
# Gale crater, Mars, in degrees: as if the file claimed Earth's WGS 84
MARS_TRANSFORM = Affine(0.0003, 0.0, 137.40, 0.0, -0.0003, -4.55)


def _pds3Label(raw: Path) -> str:
    """A minimal PDS3 detached label for a band-sequential float32 cube."""
    return f"""PDS_VERSION_ID = PDS3
RECORD_TYPE = UNDEFINED
^IMAGE = "{raw.name}"
TARGET_NAME = MARS
INSTRUMENT_ID = "QA"
OBJECT = IMAGE
  LINES = {ROWS}
  LINE_SAMPLES = {COLUMNS}
  BANDS = {BANDS}
  BAND_STORAGE_TYPE = BAND_SEQUENTIAL
  SAMPLE_TYPE = PC_REAL
  SAMPLE_BITS = 32
END_OBJECT = IMAGE
OBJECT = IMAGE_MAP_PROJECTION
  MAP_PROJECTION_TYPE = "EQUIRECTANGULAR"
  A_AXIS_RADIUS = 3396.19 <KM>
  B_AXIS_RADIUS = 3396.19 <KM>
  C_AXIS_RADIUS = 3396.19 <KM>
  CENTER_LATITUDE = 0.0 <DEG>
  CENTER_LONGITUDE = 0.0 <DEG>
  MAP_SCALE = 17.78 <METERS/PIXEL>
  MAP_RESOLUTION = 3333.33 <PIX/DEG>
  LINE_PROJECTION_OFFSET = -15166.65 <PIXEL>
  SAMPLE_PROJECTION_OFFSET = 458000.0 <PIXEL>
  POSITIVE_LONGITUDE_DIRECTION = EAST
END_OBJECT = IMAGE_MAP_PROJECTION
END
"""


def makeData(folder: Path) -> QaData:
    """Write every test file into ``folder`` (created if needed)."""
    folder.mkdir(parents=True, exist_ok=True)
    cube = writeRaster(_raster("qa cube", UTM, UTM_TRANSFORM), folder / "qa_cube.img")
    geotiff = writeRaster(
        _raster("qa scene", UTM, UTM_TRANSFORM), folder / "qa_scene.tif"
    )
    awkwardFolder = folder / AWKWARD_FOLDER
    awkwardFolder.mkdir(parents=True, exist_ok=True)
    awkward = writeRaster(
        _raster("unicode scene", UTM, UTM_TRANSFORM),
        awkwardFolder / "my scene – ñ 火星.tif",
    )
    marsWgs84 = writeRaster(
        _raster("mars wgs84", CRS.from_epsg(4326), MARS_TRANSFORM),
        folder / "mars_wgs84.tif",
    )
    raw = folder / "mars_pds3.img"
    np.moveaxis(_spectra(), -1, 0).astype("<f4").tofile(raw)  # band sequential
    marsLabel = folder / "mars_pds3.lbl"
    marsLabel.write_text(_pds3Label(raw), encoding="ascii")
    # The same label beside the WGS 84-tagged GeoTIFF: it says MARS
    marsWgs84.with_suffix(".lbl").write_text(_pds3Label(raw), encoding="ascii")
    noCrs = writeRaster(_raster("no crs", None, None), folder / "no_crs.tif")

    corrupt = folder / "corrupt.tif"
    corrupt.write_bytes(np.random.default_rng(1).bytes(4096))
    truncated = folder / "truncated.tif"
    whole = geotiff.read_bytes()
    truncated.write_bytes(whole[: len(whole) // 2])
    zeroBand = folder / "zero_band.hdr"
    zeroBand.write_text(
        "ENVI\nsamples = 10\nlines = 10\nbands = 0\nheader offset = 0\n"
        "file type = ENVI Standard\ndata type = 4\ninterleave = bsq\n"
        "byte order = 0\n",
        encoding="ascii",
    )
    (folder / "zero_band.img").write_bytes(b"")
    empty = folder / "empty.tif"
    empty.write_bytes(b"")

    caseTwin = None
    lower = folder / "case" / "scene.tif"
    upper = folder / "case" / "Scene.tif"
    lower.parent.mkdir(exist_ok=True)
    _writeConstant(lower, 0.2)
    _writeConstant(upper, 0.8)
    if len(list(lower.parent.iterdir())) == 2:  # the file system kept both
        caseTwin = upper
    return QaData(
        folder=folder,
        cube=cube,
        geotiff=geotiff,
        awkward=awkward,
        marsWgs84=marsWgs84,
        marsLabel=marsLabel,
        noCrs=noCrs,
        corrupt=corrupt,
        truncated=truncated,
        zeroBand=zeroBand,
        empty=empty,
        caseTwin=caseTwin,
    )


def _writeConstant(path: Path, value: float) -> None:
    profile = {
        "driver": "GTiff",
        "height": 8,
        "width": 8,
        "count": 3,
        "dtype": "float32",
        "crs": UTM,
        "transform": UTM_TRANSFORM,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.full((3, 8, 8), value, dtype=np.float32))


def manySmall(folder: Path, count: int) -> list[Path]:
    """``count`` small georeferenced GeoTIFFs (the laptop user's dozen images)."""
    folder.mkdir(parents=True, exist_ok=True)
    return [
        writeRaster(
            _raster(f"tile {i}", UTM, UTM_TRANSFORM), folder / f"tile_{i:02}.tif"
        )
        for i in range(count)
    ]


def realProducts() -> list[Path]:
    """Real products to try when the release machine has them (never in CI):
    ENVI headers and PDS labels under ``varda-aux/ode``. Set ``VARDA_QA_REAL=0``
    to skip them."""
    if os.environ.get("VARDA_QA_REAL") == "0" or not REAL_DATA.is_dir():
        return []
    chosen: list[Path] = []
    for pattern in ("*.hdr", "*.xml", "*.lbl"):  # ENVI, PDS4, PDS3
        found = sorted(REAL_DATA.rglob(pattern, case_sensitive=False))
        # the products as downloaded, not results saved beside them ("… PCA.hdr")
        chosen += [path for path in found if " " not in path.name][:2]
    return chosen
