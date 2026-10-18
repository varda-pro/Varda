"""Footprint files: any vector file of footprints (an ODE shapefile, a
GeoJSON or GeoPackage someone shared) searched like a catalog, offline.

The body comes from the file's coordinate system (``varda.geo``'s
``identifyBody``: its names, else its radius); projected footprints are
converted to degrees on that body's IAU 2015 sphere. A product's files are
the row's URLs, or, when the row carries ODE's identifiers (as ODE's own
footprint shapefiles do), ODE's file list.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import ClassVar
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd
import shapely

from varda.catalogs.bodies import BodyChoice, toLongitudeLatitude
from varda.catalogs.catalog import (
    Catalog,
    CatalogError,
    CatalogProgress,
    Collection,
    Product,
    RemoteFile,
    SearchPage,
    SearchQuery,
    pageOffset,
)
from varda.catalogs.ode import DATABASES, OdeCatalog
from varda.common.parameter import EnumParameter, StringParameter
from varda.geo.bodies import identifyBody

# Column names tried, in order, for a product's id and observation start
ID_COLUMNS = ("ProductId", "pdsid", "product_id", "id", "obs", "name", "Name")
TIME_COLUMNS = (
    "UTCstart",
    "UTC_start_time",
    "start_datetime",
    "datetime",
    "date",
    "StartTime",
)
# ODE footprint files name the product's instrument host, instrument and type
ODE_COLUMNS = ("InstHostId", "InstId", "ProdType", "Target")
LABELS = (".lbl", ".xml")
# Web pages, not files
PAGES = (".aspx", ".html", ".htm", ".php")


def _utc(moment: datetime) -> pd.Timestamp:
    """A date as a UTC timestamp (a date without a zone is taken as UTC)."""
    stamp = pd.Timestamp(moment)
    assert isinstance(stamp, pd.Timestamp)  # a datetime is never "not a time"
    return stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")


def _shift(geometry: shapely.Geometry) -> shapely.Geometry:
    """Longitudes 0-360 as -180 to 180 (a footprint wholly east of 180)."""
    west, _, east, _ = geometry.bounds
    if west >= 180:
        return shapely.transform(geometry, lambda xy: xy - [360, 0])
    return geometry


class FootprintFileCatalog(Catalog):
    catalogId = "footprint_file"
    name = "Footprint files"
    description = (
        "A vector file of footprints (shapefile, GeoJSON, GeoPackage), such as "
        "ODE's footprint shapefiles; works offline."
    )
    # The files offered; the Find Data workspace adds the user's
    paths: ClassVar[list[str]] = []
    idContains = StringParameter("Product ID contains", "")
    body = EnumParameter(
        "Body",
        BodyChoice,
        BodyChoice.AUTO,
        description="The body the footprints are on, when the file doesn't say "
        "(GeoJSON always declares Earth's coordinates)",
    )

    def __init__(self) -> None:
        super().__init__()
        self._frames: dict[str, gpd.GeoDataFrame] = {}
        self._rows: dict[str, pd.Series] = {}

    def collections(self) -> list[Collection]:
        return [
            Collection(path, Path(path).name, self._body(path), "A footprint file")
            for path in self.paths
        ]

    def _body(self, path: str) -> str:
        chosen = BodyChoice(self.body.get())
        if chosen is not BodyChoice.AUTO:
            return chosen.value
        return self._frame(path).attrs["body"]

    def _frame(self, path: str) -> gpd.GeoDataFrame:
        if path not in self._frames:
            try:
                frame = gpd.read_file(path)
            except Exception as error:  # pyogrio raises many kinds
                raise CatalogError(
                    f"Varda couldn't read {Path(path).name}: {error}"
                ) from error
            source = frame.crs
            try:
                frame = toLongitudeLatitude(frame)
            except ValueError as error:
                raise CatalogError(f"{Path(path).name}: {error}") from error
            frame["geometry"] = frame.geometry.apply(_shift)
            # "" when the coordinate system names no known body
            body = identifyBody(source).body
            frame.attrs["body"] = body.name if body is not None else ""
            self._frames[path] = frame
        return self._frames[path]

    def search(self, query: SearchQuery, progress: CatalogProgress) -> SearchPage:
        frame = self._frame(query.collection)
        if query.bbox is not None:
            frame = frame[frame.intersects(shapely.box(*query.bbox))]
        idColumn = next((c for c in ID_COLUMNS if c in frame.columns), None)
        if text := self.idContains.get().strip().lower():
            ids = frame[idColumn] if idColumn else frame.index.astype(str)
            frame = frame[ids.astype(str).str.lower().str.contains(text, regex=False)]
        timeColumn = next((c for c in TIME_COLUMNS if c in frame.columns), None)
        if timeColumn and (query.start or query.end):
            times = pd.to_datetime(frame[timeColumn], errors="coerce", utc=True)
            keep = times.notna()
            if query.start:
                keep &= times >= _utc(query.start)
            if query.end:
                keep &= times <= _utc(query.end)
            frame = frame[keep]
        offset = pageOffset(query.page)
        page = frame.iloc[offset : offset + query.limit]
        products = []
        for index, row in page.iterrows():
            productId = str(row[idColumn]) if idColumn else str(index)
            self._rows[f"{query.collection}#{productId}"] = row
            start = (
                pd.to_datetime(row[timeColumn], errors="coerce", utc=True)
                if timeColumn
                else None
            )
            properties = {
                column: value
                for column, value in row.items()
                if column != "geometry"
                and isinstance(value, (int, float, str, bool))
                and not (
                    isinstance(value, str)
                    and (value.startswith("http") or len(value) > 80)
                )
                and not (isinstance(value, float) and pd.isna(value))
            }
            products.append(
                Product(
                    productId,
                    productId,
                    query.collection,
                    row.geometry,
                    start.to_pydatetime()
                    if start is not None and not pd.isna(start)
                    else None,
                    None,
                    properties,
                )
            )
        more = offset + len(products) < len(frame)
        return SearchPage(
            tuple(products), offset + len(products) if more else None, len(frame)
        )

    def files(self, product: Product) -> list[RemoteFile]:
        row = self._rows.get(f"{product.collection}#{product.id}")
        if row is None:
            raise CatalogError("Search again: Varda no longer has this product's row.")
        if all(column in row.index and row[column] for column in ODE_COLUMNS):
            target = str(row["Target"]).lower()
            if target in DATABASES.values():
                return OdeCatalog().filesFor(
                    product.id,
                    target,
                    str(row["InstHostId"]),
                    str(row["InstId"]),
                    str(row["ProdType"]),
                )
        files = []
        for column, value in row.items():
            if not (isinstance(value, str) and value.startswith("http")):
                continue
            name = PurePosixPath(urlparse(value).path).name
            if not name or name.lower().endswith(PAGES):
                continue
            role = "label" if name.lower().endswith(LABELS) else "data"
            files.append(RemoteFile(name, value, role=role))
        if not files:
            raise CatalogError(
                f"The footprint file lists no downloadable files for {product.id}."
            )
        return files
