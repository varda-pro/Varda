"""Writing ROIs, lines and points as GIS vector files that Varda's import
(``vector_import``) reads back."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

EXPORT_FILE_FILTER = "GeoJSON (*.geojson);;GeoPackage (*.gpkg);;Shapefile (*.shp)"
_DRIVERS = {
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".gpkg": "GPKG",
    ".shp": "ESRI Shapefile",
}


def writeVectorFile(
    frame: gpd.GeoDataFrame, path: str | Path, driver: str | None = None
) -> Path:
    """Write ``frame`` in the format ``driver`` names, or else its suffix
    does, with its CRS also as WKT in a ``crs`` column: GeoJSON can record
    only WGS 84, and Varda's import reads the column. ValueError for a suffix
    that isn't a GIS format."""
    target = Path(path)
    if driver is None:
        driver = _DRIVERS.get(target.suffix.lower())
        if driver is None:
            raise ValueError(
                f"Can't write a '{target.suffix}' file: use .geojson, .gpkg or .shp."
            )
    if frame.crs is not None:
        frame = frame.assign(crs=frame.crs.to_wkt())
    frame.to_file(target, driver=driver)
    return target
