# Find and fetch data — Design

Status: draft for review (2026-10-15).

## The idea

Varda should help people find the observations that cover a place and
download them, without becoming a client for every archive. Varda's core
knows nothing about any mission: it shows footprints on a map, filters and
selects them, and downloads files. Everything dataset-specific lives in
**catalogs**: small providers that plug in through the plugin system, as
analyses and file formats do. Varda ships a few catalogs that are
deliberately different; the community adds the rest.

## What the core does (any body, any dataset)

- **Map:** footprints in longitude and latitude on the catalog's body (Mars,
  the Moon, Europa, Earth, …), drawn equirectangular. The map has a basemap
  (any loaded, georeferenced image of the same body, reprojected for
  display; otherwise a graticule) and vector overlays (GeoJSON, shapefiles,
  GeoPackages: a rover traverse, a geologic map).
- **Search:** a catalog, a collection, an area (drawn on the map, the current
  view, or a loaded image's footprint), a date range, and the catalog's own
  filters. Results come in pages.
- **Results:** a table linked to the map (select in either), sortable by any
  property, with a thumbnail when the catalog offers one.
- **Download list:** the chosen products' files, with their total size before
  anything starts. Export the list (CSV, or a shell script of `curl`
  commands) for downloading elsewhere.
- **Downloads:** a queue that resumes interrupted files (HTTP range
  requests), verifies checksums when the catalog gives them, can be canceled,
  and stores each product in its own folder:
  `<download folder>/<catalog>/<collection>/<product>/`. A finished product
  opens in Varda, or goes to a workspace.
- **Credentials:** a catalog that needs a login declares it; the core asks
  once and keeps it in the system keychain (as Kindle a Feature does with API
  keys). Catalogs never handle storage themselves.

## The catalog interface

A catalog is a class, registered by a plugin's `registerCatalogs` hook and
exported from `varda.api` (additive, so the plugin API stays version 1):

```python
class Catalog(ParameterGroup):
    catalogId: ClassVar[str]            # stable, snake_case
    name: ClassVar[str]
    description: ClassVar[str] = ""
    credentials: ClassVar[tuple[str, ...]] = ()   # e.g. ("username", "password")

    # class-level Parameters are the catalog's filters; the core builds the UI

    def collections(self) -> list[Collection]: ...
    def search(self, query: SearchQuery, progress) -> SearchPage: ...
    def files(self, product: Product) -> list[RemoteFile]: ...
```

- `Collection(id, title, body, description="")`: a dataset within the
  catalog (CRISM MTRDR, CTX EDR, a STAC collection). Its `body` picks the
  map.
- `SearchQuery(collection, bbox, start, end, limit, page)`: the bbox is
  (west, south, east, north) in degrees, longitudes −180 to 180, east
  positive. The filters are the catalog's own Parameters, read from `self`.
- `SearchPage(products, nextPage, total)`: `nextPage` is opaque to the core
  (a token, an offset, a URL).
- `Product(id, title, collection, footprint, start, end, properties,
  thumbnail="")`: the footprint is a shapely geometry in longitude and
  latitude; `properties` is a flat dict of displayable values (incidence
  angle, resolution, …), shown and sortable in the table.
- `RemoteFile(name, url, size, checksum="", role="data", essential=True)`: a
  checksum is `"md5:…"` or `"sha256:…"`. Roles are `data`, `label`, `browse`
  and `auxiliary`; only essential files are selected by default, so a
  product's browse images and extras can be added but don't have to be.
- **Opening:** the core opens a finished product's data files with Varda's
  readers (any format a reader or plugin handles). Readers stay their own
  extension point; a catalog doesn't need to know them.

Network work happens in the background; `search` and `files` may block.

## The catalogs Varda ships

Chosen to prove the interface isn't shaped by one archive:

1. **STAC.** The open standard most Earth imagery and a growing share of
   planetary data use. One client, with a configurable list of STAC APIs:
   **USGS Astrogeology** (Mars, the Moon, Europa: HiRISE and CTX DTMs, THEMIS
   mosaics, Kaguya, Galileo, …) and **Earth Search** (Sentinel-2, Landsat,
   …) by default; users add others by URL, without code. Bodies come from
   each collection's `ssys:targets` (Earth otherwise). Assets become files,
   with their roles.
2. **PDS ODE** (the Orbital Data Explorer's REST API): Mars, the Moon and
   Mercury products of many missions, with footprints, viewing geometry and
   file lists (sizes, URLs). Collections are ODE's instrument and product
   types (CRISM MTRDR, TRR3, TER and DDR; CTX; HiRISE; …). Filters: incidence,
   emission and phase angles, solar longitude, and observation type where
   the archive has one.
3. **Footprint file**: any vector file of footprints (an ODE shapefile, a
   GeoJSON someone shared) with a column of URLs or of labels. It works
   offline and covers archives that only publish footprint files.

## Out of scope (for now)

- Readers for every format a catalog can find (a catalog can be useful
  before Varda reads its products; readers are added separately).
- Tiled basemap services (WMTS, XYZ) and globe views.
- Login flows beyond a username and password, or a token (OAuth, for
  example).
- Searching several catalogs at once.

## Architecture

```
src/varda/catalogs/            # no Qt (import contract)
  catalog.py      Catalog, Collection, SearchQuery, SearchPage, Product, RemoteFile
  downloads.py    the download queue: resume, checksums, cancel, layout
  stac.py         the STAC catalog (configurable APIs)
  ode.py          the ODE catalog
  footprints.py   the footprint-file catalog
  bodies.py       body names and radii; longitude conventions
src/varda/workspaces/find_data/  # the GUI: map, filters, results, downloads
```

## Testing

- Each catalog against recorded responses (STAC search pages, ODE product
  and file listings, an ODE shapefile excerpt), plus live tests that skip
  offline.
- Downloads against a local HTTP server: resume after an interrupted file, a
  checksum mismatch refused, cancel, the folder layout.
- The plugin hook: a catalog from a plugin appears and searches.

## Phases

1. **Core:** the catalog interface, the download queue, the plugin hook and
   API names.
2. **Catalogs:** STAC, ODE, footprint file.
3. **The Find Data workspace:** map with basemap and overlays, search,
   results, download list and export, downloads, opening.
4. **Docs:** a user guide page, a section in the plugin authoring guide and
   an example catalog plugin.
