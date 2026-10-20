"""Real-style coordinate systems, written exactly as the producing software
writes them (spec §6), and small synthetic images on them (no data files)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.geo.transform import transformPoints
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.proj_lock import PROJ_LOCK

# A real CRISM MTRDR (FRT000107A7_07_IF166J_MTR3) as GDAL's PDS3 driver reads
# its .LBL: a sphere of Mars's local radius under this scene, with standard
# parallel 15°N. The radius is this scene's, not CRISM's: every MTRDR has its own
CRISM_MTRDR_WKT = (
    'PROJCS["EQUIRECTANGULAR MARS",'
    'GEOGCS["GCS_MARS",'
    'DATUM["D_MARS",SPHEROID["MARS_localRadius",3394839.81331631,0]],'
    'PRIMEM["Reference_Meridian",0],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],'
    'PROJECTION["Equirectangular"],'
    'PARAMETER["standard_parallel_1",15],'
    'PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)
# Another CRISM-like MTRDR, constructed (not a real product) in the same form:
# each MTRDR's sphere is Mars's local radius under its own scene, so a
# southern scene has its own radius and standard parallel
CRISM_MTRDR_SOUTH_WKT = (
    'PROJCS["EQUIRECTANGULAR MARS",'
    'GEOGCS["GCS_MARS",'
    'DATUM["D_MARS",SPHEROID["MARS_localRadius",3392500,0]],'
    'PRIMEM["Reference_Meridian",0],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],'
    'PROJECTION["Equirectangular"],'
    'PARAMETER["standard_parallel_1",-40],'
    'PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)
# The same product as GDAL's ENVI driver reads its .HDR
CRISM_MTRDR_ENVI_WKT = (
    'PROJCS["MRO Mars Equirectangular [IAU 2000] [15.00N; 0.00E]",'
    'GEOGCS["GCS_Mars_2000_IAU_IAG_custom_sphere_lat15",'
    'DATUM["D_Mars_2000_IAU_IAG_custom_sphere_lat15",'
    'SPHEROID["Mars_2000_IAU_IAG_custom_sphere_lat15",3394839.81331631,0]],'
    'PRIMEM["Reference_Meridian",0],'
    'UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Equirectangular"],'
    'PARAMETER["standard_parallel_1",15],'
    'PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],'
    'UNIT["Meter",1],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)
# GDAL's PDS3 form (as CRISM_MTRDR_WKT) for a product on the Mars 2000
# sphere with standard parallel 0: IAU_2015:49910 under GDAL's names, which
# PROJ's equals rejects only for the names (constructed from the CRISM form)
MARS_EQC_PDS3_WKT = (
    'PROJCS["EQUIRECTANGULAR MARS",'
    'GEOGCS["GCS_MARS",'
    'DATUM["D_MARS",SPHEROID["MARS",3396190,0]],'
    'PRIMEM["Reference_Meridian",0],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]]],'
    'PROJECTION["Equirectangular"],'
    'PARAMETER["standard_parallel_1",0],'
    'PARAMETER["central_meridian",0],'
    'PARAMETER["false_easting",0],'
    'PARAMETER["false_northing",0],'
    'UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AXIS["Easting",EAST],AXIS["Northing",NORTH]]'
)
# The Ames Stereo Pipeline's point2dem, a CTX DEM (20 m/px)
CTX_DEM_PROJ = (
    "+proj=eqc +lat_ts=18.4 +lat_0=0 +lon_0=77.5 +x_0=0 +y_0=0 "
    "+R=3396190 +units=m +no_defs"
)
# HiRISE polar products, on a 3376.2 km sphere (1 m/px at 85°N)
HIRISE_POLAR_PROJ = (
    "+proj=stere +lat_0=90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=3376200 +units=m +no_defs"
)
# LRO WAC (100 m/px), and its south polar stereographic variant
LRO_WAC_PROJ = "+proj=eqc +lat_ts=0 +lon_0=0 +R=1737400 +units=m +no_defs"
LRO_WAC_SOUTH_POLAR_PROJ = (
    "+proj=stere +lat_0=-90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
)
# Earth's degrees, as a Mars product sometimes (wrongly) declares them
WGS84 = "EPSG:4326"
# MOLA MEGDR, lon 0–360 (1/128°)
MOLA_MEGDR_PROJ = "+proj=longlat +R=3396000 +no_defs"
# A PDS3 label naming Mars, and a 1/256° GeoTIFF transform near Gale crater
MARS_LABEL = 'PDS_VERSION_ID = PDS3\nTARGET_NAME = "MARS"\nEND\n'
GALE_TRANSFORM = Affine(1 / 256, 0, 137.3, 0, -1 / 256, -4.5)
# Vesta's IAU 2015 sphere: the regression for "Earth"
VESTA_IAU = "IAU_2015:200000400"


def makeImage(
    crs: CRS | str,
    *,
    centerLonLat: tuple[float, float],
    pixelSize: float,
    width: int = 64,
    height: int = 64,
    bands: int = 1,
    label: str | None = None,
    name: str = "fixture",
) -> VardaRaster:
    """A north-up image of ``width`` × ``height`` pixels of ``pixelSize``
    (in the CRS's units) centered on ``centerLonLat`` (longitude first, in
    the geodetic CRS's own units, directions and latitude type), with ``label`` as its PDS3 label when given."""
    with PROJ_LOCK:
        crs = CRS.from_user_input(crs)
        geodetic = crs.geodetic_crs if crs.is_projected else crs
        # not a Transformer with always_xy, which leaves the IAU_2015
        # ellipsoids' latitude first
        (centerX,), (centerY,) = transformPoints(
            geodetic, crs, [centerLonLat[0]], [centerLonLat[1]]
        )
    transform = Affine(
        pixelSize,
        0.0,
        centerX - width / 2 * pixelSize,
        0.0,
        -pixelSize,
        centerY + height / 2 * pixelSize,
    )
    data = np.random.default_rng(0).random((height, width, bands)).astype(np.float32)
    source = ArrayDataSource(
        data,
        transform=transform,
        crs=crs,
        extraMetadata={"pds3Label": label} if label is not None else None,
    )
    return VardaRaster(source, name=name)


def writeWgs84(folder: Path, label: str | None = MARS_LABEL, crs="EPSG:4326") -> Path:
    """A small GeoTIFF in Earth's degrees near Gale crater, with ``label`` as
    its PDS3 label beside it."""
    path = folder / "scene.tif"
    data = np.random.default_rng(0).random((2, 16, 16)).astype(np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=16,
        height=16,
        count=2,
        dtype="float32",
        crs=crs,
        transform=GALE_TRANSFORM,
    ) as file:
        file.write(data)
    if label is not None:
        path.with_suffix(".lbl").write_text(label)
    return path


# A PDS3 IMAGE_MAP_PROJECTION group as HiRISE writes one: Mars's north polar
# stereographic on the 3376.2 km sphere, 1 m pixels. Its CRS is
# HIRISE_POLAR_PROJ, its transform HIRISE_LABEL_TRANSFORM.
HIRISE_LABEL_GROUP = {
    "MAP_PROJECTION_TYPE": '"POLAR STEREOGRAPHIC"',
    "A_AXIS_RADIUS": "3376.2 <KM>",
    "C_AXIS_RADIUS": "3376.2 <KM>",
    "COORDINATE_SYSTEM_NAME": "PLANETOCENTRIC",
    "POSITIVE_LONGITUDE_DIRECTION": "EAST",
    "CENTER_LATITUDE": "90.0 <DEG>",
    "CENTER_LONGITUDE": "0.0 <DEG>",
    "MAP_SCALE": "0.001 <KM/PIXEL>",
    "LINE_PROJECTION_OFFSET": "-120000.5 <PIXEL>",
    "SAMPLE_PROJECTION_OFFSET": "40000.5 <PIXEL>",
}
HIRISE_LABEL_TRANSFORM = (1.0, 0.0, -40001.0, 0.0, -1.0, -120000.0)
# The spec's label GDAL 3.9.3 misreads (§2): west-positive, planetographic,
# a ≠ c. GDAL builds GDAL_WEST_PROJ (east, a sphere of A) with
# GDAL_WEST_TRANSFORM.
WEST_LABEL_GROUP = {
    "MAP_PROJECTION_TYPE": "EQUIRECTANGULAR",
    "A_AXIS_RADIUS": "3396.19 <KM>",
    "C_AXIS_RADIUS": "3376.20 <KM>",
    "COORDINATE_SYSTEM_NAME": "PLANETOGRAPHIC",
    "POSITIVE_LONGITUDE_DIRECTION": "WEST",
    "CENTER_LATITUDE": "10.0 <DEG>",
    "CENTER_LONGITUDE": "100.0 <DEG>",
    "MAP_SCALE": "0.5 <KM/PIXEL>",
    "LINE_PROJECTION_OFFSET": "100.5 <PIXEL>",
    "SAMPLE_PROJECTION_OFFSET": "-20.5 <PIXEL>",
}
GDAL_WEST_PROJ = "+proj=eqc +lat_ts=10 +lon_0=100 +R=3396190 +units=m +no_defs"
GDAL_WEST_TRANSFORM = Affine(500, 0, 10_000, 0, -500, 50_500)


def writePds3(folder: Path, group: dict[str, str], name: str = "x") -> Path:
    """A 10 × 10 byte PDS3 image (``name``.img) of Mars and its detached
    label (``name``.lbl, returned) with ``group`` as its
    IMAGE_MAP_PROJECTION object."""
    (folder / f"{name}.img").write_bytes(bytes(range(100)))
    keys = "".join(f"  {key} = {value}\n" for key, value in group.items())
    label = folder / f"{name}.lbl"
    label.write_text(
        "PDS_VERSION_ID = PDS3\n"
        "RECORD_TYPE = FIXED_LENGTH\nRECORD_BYTES = 10\nFILE_RECORDS = 10\n"
        f'^IMAGE = "{name}.img"\nTARGET_NAME = MARS\n'
        "OBJECT = IMAGE\n  LINES = 10\n  LINE_SAMPLES = 10\n  BANDS = 1\n"
        "  SAMPLE_TYPE = MSB_UNSIGNED_INTEGER\n  SAMPLE_BITS = 8\n"
        "END_OBJECT = IMAGE\n"
        f"OBJECT = IMAGE_MAP_PROJECTION\n{keys}END_OBJECT = IMAGE_MAP_PROJECTION\n"
        "END\n"
    )
    return label
