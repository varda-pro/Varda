"""Build the find-data map's bundled basemaps and feature names.

    uv run python scripts/build_basemaps.py [--images] [--names]

For each layer in ``varda.catalogs.basemaps.LAYERS``, the Trek tiles at the
layer's ``bundledZoom`` are fetched, assembled into one global
equirectangular image (2048 × 1024 at zoom 2, 1024 × 512 at zoom 1) and
saved as ``resources/basemaps/<layer>.jpg``. For each body with a layer, the
WGPSN nomenclature center points are downloaded from USGS and trimmed to
``resources/basemaps/names/<body>.csv.gz``: IAU-adopted names only, with the
longitude converted to -180..180° east. Record the retrieval date in
``resources/basemaps/README.md`` when you rebuild.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import tempfile
import zipfile
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
from PIL import Image

from varda.catalogs.basemaps import LAYERS, assemble, decodeImage, tilesFor
from varda.catalogs.gazetteer import eastLongitude
from varda.catalogs.http import fetchBytes

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "resources" / "basemaps"
WGPSN = "https://asc-planetarynames-data.s3.us-west-2.amazonaws.com"
JPEG_QUALITY = 75
ADOPTED = "Adopted by IAU"


def buildImages() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for layer in LAYERS:
        tiles = tilesFor((-180.0, -90.0, 180.0, 90.0), layer.bundledZoom)
        pixels = {t: decodeImage(fetchBytes(layer.tileUrl(t))) for t in tiles}
        rgba, _ = assemble(pixels)
        alpha = rgba[..., 3:4].astype(np.float32) / 255.0
        rgb = (rgba[..., :3] * alpha).astype(np.uint8)  # transparent → black
        # a column left blank at the edge (a mosaic ending at 179.99°) takes
        # its neighbor's pixels, so no seam shows
        blank = np.flatnonzero(rgb.mean(axis=(0, 2)) < 8)
        valid = np.flatnonzero(rgb.mean(axis=(0, 2)) >= 8)
        for column in blank:
            rgb[:, column] = rgb[:, valid[np.argmin(abs(valid - column))]]
        # back to IAU longitudes (Titan's tiles are 180° off)
        rgb = np.roll(rgb, -round(layer.lonOffset / 360 * rgb.shape[1]), axis=1)
        path = OUT / f"{layer.layerId}.jpg"
        Image.fromarray(rgb).save(path, quality=JPEG_QUALITY, optimize=True)
        print(f"{path.name}: {rgb.shape[1]}×{rgb.shape[0]}, {path.stat().st_size} B")


def buildNames() -> None:
    folder = OUT / "names"
    folder.mkdir(parents=True, exist_ok=True)
    for body in dict.fromkeys(layer.body for layer in LAYERS):
        url = f"{WGPSN}/{body.upper()}_nomenclature_center_pts.zip"
        with tempfile.TemporaryDirectory() as temp:
            zipfile.ZipFile(io.BytesIO(fetchBytes(url, limit=100_000_000))).extractall(
                temp
            )
            frame = gpd.read_file(next(Path(temp).glob("*.shp")))
        frame = frame[frame["approval"] == ADOPTED].sort_values("clean_name")
        # every body's center_lon is 0-360 east (the shapefiles' metadata
        # says "Using a positive East longitude system")
        path = folder / f"{body.lower()}.csv.gz"
        with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
            f.write(f"# {url} retrieved {date.today().isoformat()}\n")
            writer = csv.writer(f)
            writer.writerow(["name", "lat", "lon", "diameter_km", "type", "approval"])
            for row in frame.itertuples():
                writer.writerow(
                    [
                        row.name,
                        f"{row.center_lat:.4f}",
                        f"{eastLongitude(row.center_lon):.4f}",
                        f"{row.diameter:.2f}" if row.diameter > 0 else "",
                        row.type,
                        row.approval,
                    ]
                )
        print(f"{path.name}: {len(frame)} names, {path.stat().st_size} B")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", action="store_true")
    parser.add_argument("--names", action="store_true")
    args = parser.parse_args()
    both = not (args.images or args.names)
    if args.images or both:
        buildImages()
    if args.names or both:
        buildNames()


if __name__ == "__main__":
    main()
