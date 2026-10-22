"""Product previews on the find-data map. A catalog's browse image is drawn
over its footprint when it is a north-up map of the footprint's longitude-
latitude box: simple cylindrical (or equirectangular), as ODE's browse
images of HiRISE, THEMIS VIS, Kaguya TC and MI, HRSC and CRISM's standard
browse products are. Two checks decide it: the
image's width over height matches the box's, and its picture (what is not
black or white fill) covers the footprint. Anything else (a strip in sensor
geometry, a figure with plots around the image) stays in the Preview panel.
No Qt."""

from __future__ import annotations

import math

import numpy as np
import shapely
from rasterio.transform import from_bounds
from rasterio.warp import Resampling, reproject

from varda.workspaces.find_data.basemap import MapFrame
from varda.workspaces.find_data.projection import Bounds

# how far the image's width over height may be from the box's (a fraction)
ASPECT_TOLERANCE = 0.05
# the least intersection over union of the picture and the footprint; measured
# 0.93 to 1.00 for HiRISE, THEMIS VIS, Kaguya, HRSC and CRISM's browse
# products, 0.25 to 0.80 for images that aren't maps of the box (CTX EDR and
# THEMIS IR strips in sensor geometry, CRISM figures)
MIN_OVERLAP = 0.85
FILL_DARK, FILL_LIGHT = 8, 247  # gray levels taken for fill, not picture
CHECK_PIXELS = 512  # the longer side the overlap is measured at
PREVIEW_PIXELS = 2048  # the longer side drawn on the map
MAX_BOX_DEG = 90.0  # a wider box crosses ±180° (or is a global product)
POLE_DEG = 89.5  # a footprint reaching this latitude is around a pole


def footprintBox(footprint: shapely.Geometry) -> Bounds | None:
    """The footprint's longitude-latitude box, or None when a box can't place
    its browse image: an empty footprint, one across ±180° (split in two, so
    its box spans the globe) or one reaching a pole."""
    if footprint.is_empty or footprint.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    west, south, east, north = (float(v) for v in footprint.bounds)
    if not (0 < east - west <= MAX_BOX_DEG and north > south):
        return None
    if max(abs(south), abs(north)) >= POLE_DEG:
        return None
    return west, south, east, north


def _footprintMask(
    footprint: shapely.Geometry, box: Bounds, height: int, width: int
) -> np.ndarray:
    """Which pixels of an image covering ``box`` (rows north to south) have
    their centers in the footprint."""
    west, south, east, north = box
    longitudes = west + (np.arange(width) + 0.5) / width * (east - west)
    latitudes = north - (np.arange(height) + 0.5) / height * (north - south)
    lon, lat = np.meshgrid(longitudes, latitudes)
    shapely.prepare(footprint)
    return shapely.contains_xy(footprint, lon, lat)


def overlap(rgba: np.ndarray, footprint: shapely.Geometry, box: Bounds) -> float:
    """Intersection over union of the image's picture (not black or white
    fill, not clear) and the footprint, the image laid over ``box``."""
    step = max(1, math.ceil(max(rgba.shape[:2]) / CHECK_PIXELS))
    small = rgba[::step, ::step]
    gray = small[..., :3].mean(axis=2)
    picture = (gray > FILL_DARK) & (gray < FILL_LIGHT) & (small[..., 3] > 0)
    inside = _footprintMask(footprint, box, *small.shape[:2])
    union = np.count_nonzero(picture | inside)
    return np.count_nonzero(picture & inside) / union if union else 0.0


def isMapOfFootprint(rgba: np.ndarray, footprint: shapely.Geometry) -> bool:
    """Whether ``rgba`` (rows north to south) is a north-up map of the
    footprint's box: its width over height is the box's, in degrees (simple
    cylindrical) or in ground lengths at a latitude within the box
    (equirectangular), and its picture covers the footprint."""
    box = footprintBox(footprint)
    if box is None:
        return False
    west, south, east, north = box
    widest = (east - west) / (north - south)
    narrowest = widest * math.cos(math.radians(max(abs(south), abs(north))))
    height, width = rgba.shape[:2]
    aspect = width / height
    if not (
        narrowest * (1 - ASPECT_TOLERANCE) <= aspect <= widest * (1 + ASPECT_TOLERANCE)
    ):
        return False
    return overlap(rgba, footprint, box) >= MIN_OVERLAP


def drape(
    rgba: np.ndarray, footprint: shapely.Geometry, frame: MapFrame, bounds: Bounds
) -> np.ndarray:
    """A map-like browse image (``isMapOfFootprint``; RGBA, rows north to
    south) clear outside the footprint, in the map's projection over map
    ``bounds`` (the footprint box's, from ``Projector.mapBounds``): as it is
    for equirectangular, reprojected (nearest neighbor) for a polar view."""
    box = footprintBox(footprint)
    assert box is not None  # isMapOfFootprint checked it
    step = max(1, math.ceil(max(rgba.shape[:2]) / PREVIEW_PIXELS))
    image = np.array(rgba[::step, ::step])
    height, width = image.shape[:2]
    inside = _footprintMask(footprint, box, height, width)
    image[..., 3] = np.where(inside, image[..., 3], 0)
    if not frame.polar:
        return image
    x0, y0, x1, y1 = bounds
    scale = max(height, width) / max(x1 - x0, y1 - y0)
    outWidth = max(1, math.ceil((x1 - x0) * scale))
    outHeight = max(1, math.ceil((y1 - y0) * scale))
    out = np.zeros((4, outHeight, outWidth), np.uint8)
    reproject(
        source=np.ascontiguousarray(image.transpose(2, 0, 1)),
        destination=out,
        src_transform=from_bounds(*box, width, height),
        src_crs=frame.lonLatCrs,
        dst_transform=from_bounds(x0, y0, x1, y1, outWidth, outHeight),
        dst_crs=frame.crs,
        resampling=Resampling.nearest,
    )
    return out.transpose(1, 2, 0)
