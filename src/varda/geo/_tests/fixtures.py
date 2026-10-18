"""Real-style coordinate systems, written exactly as the producing software
writes them (spec §6), and small synthetic images on them (no data files)."""

from __future__ import annotations

import numpy as np
from affine import Affine
from pyproj import CRS

from varda.common.entities import VardaRaster
from varda.geo.transform import transformPoints
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.proj_lock import PROJ_LOCK

# A real CRISM MTRDR (FRT000107A7_07_IF166J_MTR3) as GDAL's PDS3 driver reads
# its .LBL: a sphere of Mars's local radius at the standard parallel, 15°N
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
