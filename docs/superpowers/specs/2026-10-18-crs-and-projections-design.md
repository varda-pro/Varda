# Coordinate Systems and Map Projections — Design

**Date:** 2026-10-18
**Status:** Phase 1 merged (PR #248). Phase 2 planned. Plans:
`docs/superpowers/plans/2026-10-18-crs-and-projections-phase-1.md`,
`docs/superpowers/plans/2026-10-18-crs-and-projections-phase-2.md`.

**Revised 2026-10-18 for Phase 2.** The text now matches the merged Phase 1
code (§3 module list, §4.1 longitude convention, §4.2 `checkCrs`, §6
fixtures). Phase 2 gains overlays across projections (§4.4.1, moved from
Phase 4), the projection recommender (§4.6, moved from Phase 3), the label
CRS rules (§4.8) and the fix for grid resampling between a sphere and an
ellipsoid (§4.3.1).

## Why

Michael's request:

> "We probably also need CRS and map projection transformations built into
> Varda. This is something that causes a lot of confusion and headaches for
> many planetary scientists. So if we could just do as much as possible
> behind the scenes to help users out in terms of getting data from multiple
> different sources, instruments, missions etc. into Varda and 'playing
> nice', i.e. all agreeing in map projections, that would be awesome. Doing
> things like, if a user is trying to make measurements, automatically
> choosing the correct transforms and projections for them (or perhaps
> tipping them off that they ought to be using a different projection or
> something). This will probably matter most for our mosaicking and 3D
> terrain workspaces, but is just good practice to have throughout."

He asked us to start rather than answer design questions. Every decision
he hasn't made is marked **(our call)** and collected in
[Decisions to check](#decisions-to-check).

CLAUDE.md's reproducibility rule shapes the whole design. A user must be
able to write a methods section from Varda's docs and outputs alone. So:

- every coordinate change Varda makes on its own is recorded in provenance
  and shown to the user;
- every measurement states its method, its body model and its expected
  error;
- every new process gets a user-guide page that covers its uncertainty.

Goals, in priority order:

1. **Measurements are right whatever the projection.** Distances, areas,
   profiles and slopes are computed on the body, not on the flat map.
2. **Nothing silently mixes bodies or misreads a CRS.** Problems are
   diagnosed at load. An unambiguous problem is fixed and the fix recorded.
   Anything else is flagged with a one-line explanation and a suggested fix.
3. **Images from different sources work together.** Equivalent CRSs are
   treated as equal. Where images genuinely differ, Varda picks a common
   grid and says what it did.
4. **Mosaic and Terrain choose sensible projections.** Terrain gets true
   meters in x, y and z.

Non-goals:

- Camera models or orthorectification. ISIS's `cam2map` and ASP already do
  this, and Varda's ISIS backend calls `cam2map`.
- Time-dependent reference frames.
- Earth datum shifts. PROJ handles them, and Varda passes them through.

---

## 1. Inventory: what exists today

### 1.1 Where Varda touches CRSs, transforms, reprojection and measurement

| Area | Where | What it does | Notes |
|---|---|---|---|
| Raster model | `common/entities.py:290-307` | `VardaRaster.transform` and `crs` (a pyproj `CRS`) come from the data source. `hasGeospatialData` is true when there's a CRS and a non-identity transform. | A transform without a CRS counts as "not georeferenced", so the transform is silently ignored. |
| Lat/lon | `common/entities.py:186-214` | `pixelToLatLon` goes through `crs.geodetic_crs`. Its comment notes that "converting to an Earth CRS across celestial bodies raises". | This is the one correct, body-aware lat/lon path. |
| Dead code | `common/entities.py:903-993` (`GeoReferencer`), `utilities/image_utils.py:52-75` (`transformPixelToGeoCoord`, used only by `utilities/roi_utils/roi_functions.py:30`, the old ROI code) | Duplicate pixel→lat/lon helpers. | Delete once the old ROI code goes. |
| Loading | `image_loading/data_sources/rasterio_data_source.py:293-312` | CRS = `CRS.from_wkt(rasterio crs WKT)`, built once under `PROJ_LOCK`. On failure it logs and returns `None`. | A CRS that fails to parse silently becomes "no CRS". |
| PDS/ISIS | `image_loading/data_sources/pds_data_source.py:20-46` | GDAL's PDS, PDS4, ISIS2/3 and VICAR drivers read the label and build the CRS. Varda doesn't parse `MAP_PROJECTION` or `Mapping` itself. | When GDAL can't build a CRS (unusual projections, a missing group), the image has none. The label's `TARGET_NAME`, latitude type and longitude direction are never read. |
| ENVI | `envi_data_source.py` (inherits rasterio) | `map info` and `coordinate system string` go through GDAL. | ENVI `map info` without a `coordinate system string` gives a transform with no CRS. |
| CRISM geometry | `image_loading/crism_geometry.py:466-480` | Matches a DDR to an image with `CRS.equals(..., ignore_axis_order=True)`. | The only place that uses PROJ equivalence. Everywhere else uses `==` or string comparison. |
| Writing | `image_loading/raster_writer.py:74` | Writes `image.crs` as WKT. | Faithful. |
| Thread safety | `utilities/proj_lock.py:1-11` | One `RLock` for PROJ. Held in `entities.py`, `rasterio_data_source.py`, `raster_writer.py` and `linking.py`. | There are three different conventions (see 1.3). |
| Body knowledge | `catalogs/bodies.py:12-97` | `RADII_M` (IAU mean radii), `bodyName`, `geographicCrs(body)` (a mean-radius sphere, `+proj=longlat +R=`), `normalizeLongitude`, `BodyChoice`, `bodyOf(crs)` (radius within 1%, otherwise **"Earth"**), and `toLongitudeLatitude` (GeoJSON degrees taken as is). | See 1.2. |
| Footprints | `catalogs/footprints.py:60-65, 78-101` | Shifts 0–360 longitudes to −180..180. The body comes from the file's CRS radius or a user choice. | — |
| ODE / gazetteer | `catalogs/ode.py:6, 74`; `catalogs/gazetteer.py:9-11, 90` | Converts ODE's and WGPSN's 0–360° (east or west positive) longitudes to −180..180 east. Gazetteer latitudes are planetocentric. | Longitude conventions are handled correctly here, but only here. |
| Find Data map | `workspaces/find_data/projection.py:31-56`; `basemap.py:28-38, 80-120` | Equirectangular or polar stereographic on the body's **mean-radius sphere**. Basemaps are reprojected with `rasterio.warp.reproject`. `imageBody` = `bodyOf`. | Display only. Pyproj objects are kept on the GUI thread, and workers get plain values (`basemap.py:80-81`). |
| Common grid | `common/grid.py:34-39` (`sameGrid`: `a.crs == b.crs`), `75-86` (`pixelSizeText`), `139-159` (`placementProblem` via `transform_bounds`), `271-307` (`onGridOf`: GDAL warper), `310-342` (`resamplingText`/`resamplingRecord`) | Masks and DEMs from other grids (commit ed58677): resampled onto the target grid, described, and recorded in provenance. | Good pattern, to be generalized. `placementProblem` misses overlap when two geographic images use 0–360 and −180..180 with the same CRS, because `_footprintIn` returns raw bounds. There's no body check; a cross-body pair fails inside `transform_bounds` and is reported as "can't be reprojected". (Phase 1 fixed both.) **Still open after Phase 1:** `onGridOf` hands both CRSs to GDAL, and between a sphere and an ellipsoid of one body PROJ only has a "ballpark" operation that keeps the geodetic latitude's number. Checked (GDAL 3.9.3, PROJ 9.5.1): a pixel at 45°N on the Mars sphere lands 40 rows (20 km) off on an `IAU_2015:49912` grid. Fixed in Phase 2 (§4.3.1). |
| Linked images | `workspaces/linked_images/linking.py:32-62` | Map links: `Transformer.from_crs(originCrs, crs)` under `PROJ_LOCK`, compared with `!=`. | **A cross-body pair raises an uncaught `ProjError`.** PROJ refuses Mars↔Earth ("Source and target ellipsoid do not belong to the same celestial body"), and nothing in `src/` catches `ProjError` (grep). A WGS84-tagged Mars image linked with a real Mars image crashes the action. |
| CRISM Ratio | `workspaces/crism_ratio/mapping.py:13-33` | Reuses `linkedPoints`. | Inherits the same issue. |
| Profiles | `lines/profile.py:85-105` | Geographic CRS: geodesic (`crs.get_geod()`, falling back to **WGS84**). Projected CRS: **planar** grid distance × the unit factor. | The projected path carries the full scale error. For equirectangular with `lat_ts=0` at 60° latitude, east–west distances are 2× too long (k = cos φ₁/cos φ; Snyder 1987, p. 91). The docs say so (`docs/user-guide/lines-and-profiles.md:93-98`), but the user has to fix it by hand. |
| Line length | `lines/profile.py:131-135`, `lines/line_collection.py:48-52`, `lines/line_manager_widget.py:39-42` | Same as profiles. | — |
| Profile CSV | `plotting/profile_plot.py:64-69, 255-270` | Records the CRS name, EPSG and WKT, and "geodesic" vs "in the map projection's plane". | Good provenance. Reuse it. |
| ROI measures | `rois/roi_calculations.py:32-40, 116-123, 220-227` | Area and perimeter in "map units": planar shapely area. | In a geographic CRS the area is in **degrees²**. In a projected CRS it carries the areal scale error. The docs say so (`viewing-and-measuring.md:237-244, 351-352`). |
| ROI transfer | `rois/roi_collection.py:471-514` (`applyToImage`) | `gdf.to_crs(target)` when `!=`. | There's no body check, so cross-body raises. Equivalent CRSs with different text are needlessly reprojected (harmless, but slow). |
| ROI import | `rois/roi_collection.py:428-445` | Uses the file's CRS as is. | Planetary GeoJSON declares WGS84. |
| Points | `points/point_collection.py:39-67` | The collection's CRS is **the first georeferenced image's**. Later points from any image are stored in their own image's map coordinates. | Points from an image in another CRS are silently mislabeled, for example in the Linked Images workspace. |
| Terrain size | `terrain/scene.py:146-162` (`groundSize`) | Geographic: uses semi-major `cos(center lat)`. Projected: grid size × unit factor. | Projected DEMs carry their scale error into the 3D mesh, and geographic DEMs are a flat rectangle (documented, `3d-terrain.md:238-243`). |
| Terrain heights | `terrain/scene.py:186-196` (`verticalUnit`); `terrain_workspace.py:504-511, 816-820` | Height unit from the CRS's up axis, otherwise meters, with a user-set unit. | **No vertical datum.** Areoid vs sphere vs ellipsoid is never asked or recorded. |
| Terrain overlays | `terrain/overlays.py:133-170` (`toProjection`) | Overlays are reprojected to the DEM's CRS (bilinear, `calculate_default_transform`), passed as WKT for worker threads. Equality is `RasterioCRS ==`. | Not recorded in provenance. |
| Terrain vectors | `terrain/vectors.py:52-69` | Geographic coordinates count as degrees on the DEM's body, through `geographicCrs(bodyOf(demCrs))` (a **mean-radius** sphere). Values beyond ±360/±90 are taken as already in the DEM's projection. | See the inconsistency in 1.2. |
| Terrain bodies | `terrain_workspace.py:678-687, 1222-1232` | Offers drapes and lines only from images where `imageBody(image) == imageBody(dem)`. | The only place that refuses to mix bodies. |
| Terrain threads | `terrain_workspace.py:124-128, 1009, 1511` | "pyproj objects aren't thread-safe". Pass WKT, and compute `groundSize` on the GUI thread. | — |
| Topography analysis | `analysis/topography.py:31-49, 142-144` | Slope, aspect and hillshade from `pixelSize` = the transform's |a|, |e| in map units. **Refuses geographic CRSs.** | Projected-CRS slopes ignore the scale factor. East–west gradients are wrong by k (2× at 60° in `lat_ts=0` equirectangular). Map units aren't converted to meters (a km-unit CRS gives slopes 1000× off) and heights aren't checked against the map unit. |
| Shadow mask | `analysis/shadow_mask.py:313-320, 518-522` | DEM placed with `onGridOf`. Refuses geographic CRSs. | Same pixel-size assumptions. |
| Mosaic | `mosaicking/model.py:170-178` | Refuses inputs whose `str(image.crs)` differ: "reproject them to one first (Varda doesn't yet reproject while mosaicking)". | **Textual** comparison. A GDAL-labeled CRISM MTRDR (`D_MARS` WKT) and the same projection written by ASP (`+proj=eqc +R=3396190`) are refused although they're identical. The mosaic spec (`2026-10-15-mosaicking-workspace-design.md:88-95`) planned on-the-fly reprojection. |
| Mosaic grid | `mosaicking/grid.py:32-67` | Finest pixel size in map units, the first input's CRS. | Fine once the inputs share a CRS. |
| ISIS backend | `mosaicking/backends/isis.py:228` | Writes the grid's CRS. | — |
| Viewport readout | `image_rendering/raster_view/image_viewport.py:493-505` | `px`, projected `xy`, then `lat/lon` from `pixelToLatLon`, always shown as −180..180 east and unlabeled. | Doesn't name the body, latitude type or longitude convention. |
| Image Properties | `all_images_view_list/image_properties_dialog.py:84-90` | "Map projection: <crs.name>", "Pixel size: |a| × |e|" without units. | No body, parameters, units or warnings. CRS can't be edited (`EDITABLE_SINGLE`, `entities.py:43-44`). |
| Vector import (unmerged, `origin/feature/vector-import:src/varda/vector_import.py`) | `:291-299` `_looksLikeDegrees`/`_isEarthDegrees`; `:302-336` `_fileCrs`; `:476-500` `_pixelMapping` | Planetary GeoJSON (WGS84) is read as degrees on the image's body through **`imageCrs.geodetic_crs`**. A real other-body CRS is refused with a clear message. `ProjError` is caught. | The best cross-body handling in the codebase. It should move into the core. |
| Docs | `lines-and-profiles.md:34-37, 79-98`; `viewing-and-measuring.md:234-244, 343-352`; `3d-terrain.md:26-28, 85-88, 213-243`; `linked-images.md:17, 93-94, 118`; `mosaicking.md:4, 283`; `image-processing.md:196-221` | Limitations are honestly documented. Most tell the user to "reproject first", which Varda can't do. | There's no page on coordinate systems. |

### 1.2 Duplication and inconsistency

1. **Body identification is done four ways.**
   - `catalogs.bodies.bodyOf` uses radius within 1% and falls back to **"Earth" when the body is unknown**.
   - `find_data.basemap.imageBody` wraps `bodyOf`.
   - `terrain.vectors` uses `bodyOf(demCrs)`.
   - The vector-import branch uses `bodyOf` plus `_isEarthDegrees`.

   Checked against PROJ's `IAU_2015` database (pyproj 3.7.2, PROJ 9.5.1):
   - The IAU 2015 Vesta sphere is 255.0 km, but `RADII_M` holds 262.7 km (3% apart). **A Vesta CRS from PROJ's own database is identified as Earth.**
   - Mercury (2440.53 km) and Callisto (2410.3 km) are 1.25% apart, close to the 1% tolerance.
   - An unknown radius becomes "Earth". This mislabels the data, and it would also make Varda refuse or permit the wrong transforms.

2. **"Degrees on this body" means two different things.**
   - `geographicCrs(body)` is a mean-radius sphere: 3389.5 km for Mars.
   - The vector-import branch uses the image's own `geodetic_crs`: 3396.19 km for a Mars 2000 sphere image.
   - PROJ treats the two as a ballpark (no-op) transformation, so lat/lon pass through unchanged. But any distance the Find Data map or the polar views compute uses the mean radius, while measurements in profiles use the CRS's own.

3. **CRS equality is done three ways.**
   - `==`: pyproj `CRS.__eq__` → `equals` (`grid.sameGrid`, `linking`, `roi_collection`, `terrain.vectors`).
   - Rasterio `CRS ==` (`terrain.overlays`).
   - `str(crs)` (`mosaicking.model`).

   Checked: `CRS(gdal_mars_wkt).equals(CRS("IAU_2015:49910"))` is **False** although both are equirectangular on the 3396.19 km sphere with `lat_ts=0` and `lon_0=0`. Only the names differ (`D_MARS` vs `Mars (2015) - Sphere`). `to_authority()` also finds no code for GDAL's WKT. Meanwhile `CRS(gdal_mars_wkt) == CRS("+proj=eqc +R=3396190")` is True, but `str()` of the two differs. So the mosaic refuses identical inputs, and other paths reproject needlessly.

4. **Distances are computed three ways.**
   - Geodesic on `crs.get_geod()`, falling back to WGS84 (`profile.py:97`).
   - A semi-major radius times `cos(lat)` (`scene.py:155-160`).
   - Planar × unit factor (`profile.py:103`, `scene.py:161`, `topography.py:37-42`, `grid.py:203-214`).

5. **Thread safety is handled three ways.**
   - Hold `PROJ_LOCK` (`proj_lock.py`).
   - "Keep pyproj on the GUI thread" (`terrain_workspace.py:1511`, `find_data_workspace.py:827`).
   - "Pass WKT to workers" (`overlays.py:133-138`, `terrain_workspace.py:124-128`).

   About 20 modules use pyproj without the lock.

6. **Longitude conventions are handled only in catalogs.** 0–360 and west-positive are normalized for footprints, ODE and the gazetteer, but not for images, ROIs, points or the readout.

7. **Pixel size and units are computed four ways**: `grid.pixelSizeText`, `topography.pixelSize`, `scene.groundSize`/`mapUnit`, `mosaicking.grid.pixelSize`, and Image Properties' unitless `|a| × |e|`.

### 1.3 Pitfalls already handled (keep them)

- GeoJSON's mandatory WGS84 on planetary data: footprints (`bodies.py:56-58`), terrain vectors (`vectors.py:56-63`), vector import.
- 0–360 and west-positive longitudes in catalogs (`ode.py`, `gazetteer.py:90`, `footprints.py:60-65`).
- Lat/lon in the image's own datum, never WGS84 (`entities.py:186-214`).
- PROJ's thread-unsafety (`proj_lock.py`).
- Resampling from other grids is recorded in provenance (`grid.resamplingRecord`).
- Images of different bodies are refused in Terrain (`terrain_workspace.py:678-687`).
- Height units from the CRS's vertical axis (`scene.py:186-196`).

---

## 2. The planetary pitfalls, and what Varda does about each

| Pitfall | How it's detected | What Varda does |
|---|---|---|
| **No CRS** (a transform but no CRS; a label with a map projection group GDAL couldn't turn into a CRS; ENVI `map info` alone) | `transform != identity and crs is None`. The label has `MAP_PROJECTION` (PDS3), `Mapping` (ISIS) or `cart:` (PDS4). | Phase 1: flag it ("Has map coordinates but no coordinate system; Varda treats it as unreferenced"). Phase 2: build the CRS (and, when GDAL gave none, the transform) from the label's projection group (§4.8). It's an automatic fix when the group is complete and needs no convention Varda has to assume; otherwise it's offered. Without a buildable group, offer "Set coordinate system…". |
| **GDAL misreads the label** (checked with GDAL 3.9.3's PDS3 driver: `POSITIVE_LONGITUDE_DIRECTION = WEST` with `CENTER_LONGITUDE = 100` gives the same CRS and transform as EAST, `lon_0 = 100` east, although the label means 100°W; and `COORDINATE_SYSTEM_NAME = PLANETOGRAPHIC` with A ≠ C gives a sphere of radius A) | The CRS Varda builds from the label's projection group (§4.8) isn't `sameCrs` with GDAL's. | Phase 2: `label-crs-mismatch` PROBLEM, with the label's CRS offered by Apply. Never automatic: GDAL may be right, and the label may be the one in error. |
| **CRS that fails to parse** | `rasterio_data_source._readCrs` returns None after an error. | Phase 1: keep the raw WKT and the error on the data source, and flag "Unreadable coordinate system". |
| **Wrong body: Earth CRS on planetary data** | The CRS's body is Earth (EPSG:4326 or an Earth ellipsoid), but the label's `TARGET_NAME`, the file's PDS metadata or the Varda provenance names another body. | If the CRS is geographic (just degrees) and the label names the body, the fix is unambiguous: reinterpret it as degrees on the body's IAU 2015 sphere, apply it at load, and record it. A **projected** Earth CRS (meters on Earth's radius) on Mars isn't fixable automatically, so flag it and suggest "Set coordinate system…". With no evidence of another body, do nothing; Varda can't know. |
| **Unknown body** | The CRS radius matches no body. | Flag "Unknown body (radius 255.0 km)". The body is `None`, **never "Earth"**. Cross-image operations then compare radii instead of names. |
| **0–360 vs −180..180** | Geographic CRS whose extent lies in [180, 360]. Projected with `lon_0 = 180` (e.g. `IAU_2015:49915`). | Not an error. Report the image's convention in its description and readout. Overlap and footprint tests normalize longitudes (fixes `grid.placementProblem`). Never rewrite the image's coordinates. |
| **East vs west positive** | CRS longitude axis direction `west` (e.g. `IAU_2015:49901`, Mars planetographic), or a label `POSITIVE_LONGITUDE_DIRECTION = WEST` disagreeing with the CRS axis. | When the CRS declares it: correct, PROJ handles it. Show "°W" in the readout. When the label and CRS disagree: flag it (GDAL's handling varies by driver and version) and show both readings at a test pixel. |
| **Planetocentric vs planetographic latitude** | The CRS ellipsoid is flattened and the label's `COORDINATE_SYSTEM_NAME` / `LATITUDE_TYPE` is planetographic. A sphere is always planetocentric. | When the CRS is an ellipsoid, PROJ's geodetic latitude *is* planetographic. A label saying planetocentric on an ellipsoid CRS is a mismatch, so flag it. Readouts name the latitude type. Conversion between types is exact through PROJ, or through tan φg = (a/c)² tan φc (Snyder 1987, eq. 3-28) when Varda computes it itself. |
| **Sphere vs ellipsoid radii** (Mars 2000 sphere 3396.19 km, IAU ellipsoid 3396.19/3376.20 km, HiRISE polar products on a 3376.2 km sphere, MOLA's 3396.0 km) | CRS radius vs IAU 2015 values. | Not an error: each dataset's convention is legitimate. Measurements report the difference between the CRS's own surface and the IAU 2015 reference ellipsoid (the "body model" term, §4.5). When images on different spheres are combined, describe the difference. A position shifts by up to (Δr/r) × distance from the projection's origin. |
| **Equirectangular center latitude** | `lat_ts` (standard parallel) vs the image's center latitude. | Distortion check (§4.5): the scale error east–west is cos φ₁/cos φ − 1. Measurements are geodesic anyway. Shapes, slopes and terrain get the tip-off. |
| **Polar data** | |center lat| ≥ 65°, or the extent includes a pole. | The recommender (§4.6) picks polar stereographic. Equirectangular above 65° is flagged (scale error ≥ 137% at 65° for `lat_ts=0`). |
| **Mismatched resolutions** | Pixel size in meters at the center differs between combined images. | Common grid at the finest resolution by default. Describe and record the resampling (`grid.resamplingRecord`). |
| **Equivalent CRSs with different text** | `sameCrs` (§4.1): PROJ equivalence, then geometric equality ignoring names. | Treated as equal everywhere. No reprojection, no refusal. |
| **DEM vertical datum** (heights above the MOLA areoid vs the 3396.19 km sphere vs the ellipsoid) | Label keywords, the product type (MOLA MEGDR and HiRISE DTMs are areoid; ASP's `point2dem` is relative to the sphere or ellipsoid of its `--t_srs` unless `dem_geoid` was run), otherwise unknown. | Phase 3. Terrain asks once, prefilled; it's recorded and stored in the session. Effects are stated in the docs (below). Conversion needs the areoid grid, a Phase 4 download through Optional Features. |

**Vertical datum effects (for the docs).** On Mars the areoid is close to
the IAU ellipsoid. Heights above the 3396.19 km sphere are therefore lower
than areoid heights by about (a − c) sin²φ: up to about 20 km at the poles,
plus the areoid's own undulations of a few km (Smith et al., 2001).

- **Absolute elevations and DEM differencing:** mixing references gives
  kilometer-scale offsets.
- **Slopes:** the difference between the two surfaces tilts by its gradient,
  (a − c) sin 2φ / R. That's at most 19,990 m / 3,389,500 m ≈ 0.0059 rad ≈
  0.34° at 45° latitude, plus the areoid's own gradients. It's negligible
  for local slopes of more than a few degrees, but not for regional tilts.
- **Moon and Mercury:** LOLA and MLA heights are relative to spheres
  (1737.4 km and 2439.4 km) with no geoid convention, so there's nothing to
  convert.

---

## 3. Architecture

A new Qt-free package **`varda.geo`** is added to the import-linter
contract. It owns everything about bodies, CRSs, distortion and
measurement. Other modules call it; nothing else builds `Geod`s, compares
CRSs, or guesses bodies.

```
src/varda/geo/
  __init__.py
  bodies.py        Body table (IAU 2015 through PROJ), body identification,
                   latitudeType, surfaceText                                      (Phase 1)
  describe.py      CrsDescription: a human-readable one-liner and fields         (Phase 1)
  equivalence.py   sameCrs, compatibility (same / equivalent / other projection
                   / other body / unknown), canTransform, bodyLabel               (Phase 1)
  transform.py     transformPoints (planetocentric-preserving between surfaces
                   of one body), planetocentricLonLat, eastLonLat,
                   geodeticLonLat, ographicLatitude                               (Phase 1)
                   warpOperation: the same as a PROJ pipeline for GDAL's warper   (Phase 2)
  distortion.py    scale factors and angular distortion at points; worst over
                   an extent; thresholds                                          (Phase 1)
                   arealScaleAtPixels                                             (Phase 2)
  measure.py       geodesic lengths, areas and per-pixel ground spacing for
                   any CRS; Measurement record                                    (Phase 1)
                   pixelAreas, imagePixelAreas                                    (Phase 2)
  labels.py        PDS3/PDS4/ISIS label hints (target, latitude type,
                   longitude direction/domain, projection group), provenanceHints (Phase 1)
  label_crs.py     a CRS (and transform) built from a label's projection group   (Phase 2)
  health.py        checkCrs: issues and the automatic fix                         (Phase 1)
                   label-crs-mismatch, the label CRS fix                          (Phase 2)
  format.py        formatLonLat, formatMapXY for readouts                         (Phase 1)
  recommend.py     footprintLonLat, recommendProjection, isGoodEnough             (Phase 2)
  reproject.py     TargetGrid, gridFor, reprojectBand, quality bands, record     (Phase 2)
src/varda/image_loading/crs_check.py   the health check on an opened image,
                   applying and undoing fixes (overrides, crsFix records)        (Phase 1)
src/varda/common/display_grid.py       overlays on another grid, for display  (Phase 2)
```

**Thread-safety rule (our call).** Every public `varda.geo` function takes
`pyproj.CRS | str` (WKT, PROJ or authority strings), builds what it needs
inside `PROJ_LOCK`, and returns plain values (floats, numpy arrays, attrs
records of plain values). No `varda.geo` result holds a pyproj object
except `Body.sphereCrs()` / `Body.referenceCrs()` / `Body.geod()`, which
build a fresh object on each call. So `varda.geo` is safe from workers. The
"keep pyproj on the GUI thread" and "pass WKT" conventions remain valid for
existing code. New code uses `varda.geo` and needs neither.

What Phase 1 learned (each was a real bug):

- **Axis order.** pyproj's `always_xy` leaves the `IAU_2015` ellipsoid
  codes (and other spherical/planetocentric coordinate systems) latitude
  first. Points move between CRSs only through `geo.transform`.
- **Surfaces of one body.** PROJ's ballpark between a sphere and an
  ellipsoid keeps the geodetic latitude's number, which misplaces points by
  up to ~20 km on Mars. `transformPoints` keeps the planetocentric longitude
  and latitude instead (Archinal et al., 2018). Phase 2 extends this to
  rasters (§4.3.1).
- **Pool threads.** pyproj frees a Qt pool thread's PROJ context between
  tasks. `runInBackground` calls `keepProjContext()` after each task.
- **GDAL's warper** (rasterio) keeps its own per-thread PROJ context. Warps
  run outside `PROJ_LOCK`, so a long warp never blocks the GUI's readout.
  Only the pyproj work around them (CRS parsing, pipelines) holds the lock.
- **CRISM radii are per scene.** Each CRISM map product is on a sphere of
  Mars's local radius under its own scene (3394.84 km for FRT000107A7 at
  15°N). Varda never assumes one Mars radius from one file.

**CRS input types.** `VardaRaster.crs` is a pyproj `CRS`, but
`mosaicking.grid` and `common.grid` annotate rasterio `CRS`. `varda.geo`
accepts either through `CRS.from_user_input`.

---

## 4. Components

### 4.1 The core (`varda.geo`): bodies, description, equivalence

**What and why.** One place that knows which body a CRS is on, describes
it in words a scientist recognizes, and decides whether two CRSs are the
same. This removes duplication 1–3 in §1.2.

**Body table (our call).** Radii come from PROJ's `IAU_2015` authority, not
typed by hand. It's maintained upstream, and its codes are the OGC/IAU ones
(Hare et al., 2018; Archinal et al., 2018).

- Each `Body` stores its NAIF id. Its `IAU_2015` geographic sphere is code
  `naif × 100`, and its planetocentric ellipsoid is `naif × 100 + 2` where
  one exists (verified for Mercury, Earth, Mars and Ceres).
- The IAU mean radius is kept from today's `RADII_M` for identification,
  since some products use mean-radius spheres (Mars 3389.5 km).
- Identification candidates: a body whose radius interval
  [c·(1 − 1%), a·(1 + 1%)] contains the CRS's semi-major axis.
- Body and datum names are matched first: "Mars", "D_MARS", "Moon_2000",
  "Mars (2015)", an `IAU_2015` authority, and the label's `TARGET_NAME`.
  These beat radius.
- Several candidates by radius alone and no name: **ambiguous**, listed in
  the match.
- No candidate: `None`, never "Earth".

**Interfaces** (exact; the plan's tasks rely on them):

```python
# varda/geo/bodies.py
@frozen
class Body:
    name: str                 # "Mars"
    naifId: int               # 499
    equatorialRadius: float   # m, a (IAU 2015, from PROJ)
    polarRadius: float        # m, c (= a for bodies PROJ models as spheres)
    meanRadius: float         # m (IAU mean radius)
    # IAU convention for planetographic longitude: from the longitude axis of
    # PROJ's IAU_2015:{naif*100+1} when it exists; otherwise east-positive
    # for Earth, the Moon, Venus and Vesta, and west-positive for the rest
    westPositiveOgraphic: bool

    def sphereCrs(self) -> CRS: ...      # IAU_2015:{naif*100}
    def referenceCrs(self) -> CRS: ...   # IAU_2015:{naif*100+2} if it exists, else sphereCrs()
    def geod(self) -> Geod: ...          # on the reference ellipsoid (a, c)

BODIES: dict[str, Body]                  # Mercury, Venus, Earth, Moon, Mars, Phobos, Ceres,
                                         # Vesta, Io, Europa, Ganymede, Callisto, Enceladus, Titan
def bodyNamed(name: str) -> Body | None  # case-insensitive; aliases ("Luna", "MARS", "D_Mars")

@frozen
class BodyMatch:
    body: Body | None
    evidence: str                         # "datum D_MARS", "radius 3396.19 km", "label TARGET_NAME = MARS"
    candidates: tuple[str, ...] = ()      # when ambiguous

def identifyBody(crs: CRS | str | None, labelTarget: str | None = None) -> BodyMatch
```

```python
# varda/geo/describe.py
@frozen
class CrsDescription:
    body: str | None              # "Mars"
    kind: str                     # "geographic" | "projected" | "none"
    projection: str               # "Equirectangular", "Polar Stereographic (north)", "Geographic (lon/lat)"
    surface: str                  # "sphere R = 3396.19 km" | "ellipsoid a = 3396.19, c = 3376.20 km"
    latitudeType: str             # "planetocentric" | "planetographic"
    longitudeDirection: str       # "east" | "west"
    longitudeDomain: str          # "-180..180" | "0..360"
    centerLatitude: float | None  # lat_ts / lat_0, degrees
    centerLongitude: float | None # lon_0, degrees
    units: str                    # "m", "km", "°"
    pixelSizeM: float | None      # ground size of a pixel at the image center, meters
    authority: str | None         # "IAU_2015:49910", "EPSG:4326", or None

    @property
    def text(self) -> str: ...
    # "Mars, sphere R = 3396.19 km, Equirectangular, center lat 0°, 18 m/px"

def describeCrs(crs: CRS | str | None, transform: Affine | None = None,
                width: int = 0, height: int = 0) -> CrsDescription
```

```python
# varda/geo/equivalence.py
class Compatibility(Enum):
    SAME = "same"                         # PROJ-equivalent (names may differ)
    EQUIVALENT = "equivalent"             # same body, surface, method and parameters; names differ
    SAME_BODY = "same body"               # same body, different projection or surface: reproject
    DIFFERENT_BODY = "different body"     # never combine
    UNKNOWN = "unknown"                   # a CRS is missing, or its body is unknown and radii differ

def sameCrs(a: CRS | str | None, b: CRS | str | None) -> bool  # SAME or EQUIVALENT
def compatibility(a: CRS | str | None, b: CRS | str | None) -> Compatibility
def canTransform(a, b) -> str | None      # None, or the reason ("Mars vs Earth")
```

For **EQUIVALENT**, both CRSs are exported to PROJ JSON. Then compare:

- the conversion method name (normalized);
- each conversion parameter by name and value (angles to 1e-9°, lengths to
  1e-6 m);
- the ellipsoid's a and b (to 1e-3 m);
- the prime meridian;
- axis directions and units.

Datum and CRS names are ignored.

**Tests.** These use the real-style fixtures in §6 (as WKT, exactly as GDAL
and ASP write them).

- `identifyBody`:
  - the CRISM MTRDR WKT (its scene's 3394.84 km local-radius sphere) → Mars
    (evidence "datum D_MARS");
  - the HiRISE 3376.2 km sphere → Mars;
  - LRO WAC → Moon;
  - EPSG:4326 → Earth;
  - `IAU_2015:200000400` (Vesta, 255 km) → Vesta (regression for today's "Earth");
  - a 3000 km sphere → None;
  - the WGS84 GeoTIFF with `labelTarget="MARS"` → Mars, with evidence from the label.
- `sameCrs`:
  - the GDAL `D_MARS` WKT of the Mars 2000 sphere (`MARS_EQC_PDS3_WKT`)
    vs `IAU_2015:49910` → True (PROJ's `equals` alone says False);
  - the CRISM MTRDR as GDAL's PDS3 and ENVI drivers read it → True;
  - two CRISM MTRDRs of different scenes (different local radii) → False
    (SAME_BODY);
  - the same with `lat_ts=18.4` vs `lat_ts=0` → False;
  - the same parameters on 3396190 vs 3389500 → False.
- `compatibility`:
  - Mars eqc vs Mars polar stereographic → SAME_BODY;
  - Mars vs EPSG:4326 → DIFFERENT_BODY.
- `describeCrs(...).text` for each fixture matches the expected sentence exactly.

**Docs.** A new `docs/user-guide/coordinate-systems.md` covers:

- how Varda identifies the body;
- what "same" means;
- the conventions (IAU 2015, planetocentric east by default);
- the readout.

It's linked from the guide README.

**Phase.** 1.

### 4.2 CRS health check on load

**What and why.** Diagnose the §2 pitfalls once, when an image opens. Fix
what's unambiguous, record the fix, and tip the user off about the rest. No
dialogs.

**Interfaces.**

```python
# varda/geo/labels.py
@frozen
class LabelHints:
    target: str | None                 # TARGET_NAME / target_identification name / Mapping TargetName
    latitudeType: str | None           # "planetocentric" | "planetographic"
    longitudeDirection: str | None     # "east" | "west"
    longitudeDomain: str | None        # "0..360" | "-180..180"
    projectionGroup: dict[str, str]    # raw MAP_PROJECTION / Mapping / cart keys (upper-case)
    source: str                        # "PDS3 label x.lbl", "PDS4 label", "ISIS cube label"

def labelHints(path: str | None, gdalMetadata: Mapping[str, Mapping[str, str]] = {}) -> LabelHints
```

Sources:

- the `xml:PDS4` and `json:ISIS3` GDAL metadata domains, which GDAL
  exposes;
- the PDS3 `.lbl` / attached label text, read directly (plain ODL, a
  minimal parser over `KEY = VALUE` and `GROUP`/`OBJECT` blocks; no new
  dependency);
- Varda provenance when present: `source.target` from catalogs, or the one
  target its inputs' recorded label facts (`inputs[].labelHints`, written by
  `inputRecord`) name; only the target carries over
  (`labels.provenanceHints`).

```python
# varda/geo/health.py
class Severity(Enum):
    INFO = "info"        # shown in Properties only
    TIP = "tip"          # badge + tooltip
    PROBLEM = "problem"  # badge + tooltip + a status-bar message at load

@frozen
class CrsFix:
    wkt: str             # the CRS to use instead
    reason: str          # one sentence, recorded in provenance
    automatic: bool      # applied at load without asking
    # Phase 2: the pixel-to-map transform to use too (Affine's a, b, c, d,
    # e, f), when the file has none and the label gives one (§4.8)
    transform: tuple[float, float, float, float, float, float] | None = None

@frozen
class CrsIssue:
    code: str            # "no-crs", "unreadable-crs", "earth-crs-on-body", "unknown-body",
                         # "ambiguous-body", "label-crs-mismatch", "lon-direction-mismatch",
                         # "latitude-type-mismatch", "high-distortion", "lon-0-360"
    severity: Severity
    message: str         # one line, plain words
    suggestion: str      # one line: what to do
    fix: CrsFix | None = None

@frozen
class CrsHealth:
    description: CrsDescription
    body: BodyMatch
    issues: tuple[CrsIssue, ...]
    distortion: DistortionSummary | None  # §4.5

def checkCrs(crs: CRS | str | None, transform: Affine, width: int, height: int,
             hints: LabelHints = LabelHints.empty(), rawCrsText: str | None = None,
             applyFix: bool = True) -> CrsHealth
# applyFix=False: the user kept the file's CRS, so the fix is offered
# (automatic=False) and the checks are of the file's CRS. Never raises.
```

As merged, `CrsHealth.body` is the CRS's body, or the label's when the CRS
can't say. `image_loading/crs_check.py` runs the check on an opened image
(`checkImageCrs`), and applies or undoes fixes (`chooseCrs`, `undoCrsFix`),
keeping the `crsFix` record in step. Label targets that name no body (SKY,
CALIBRATION, N/A, …: `NON_BODY_TARGETS`) are no evidence. An image Varda
made takes the target its provenance names (`labels.provenanceHints`), but
only the target: a result's conventions may differ from its inputs'.

**Automatic fixes (our call).** Only one case is fixed without asking in
Phase 1: an Earth *geographic* CRS on an image whose label names another
body. The fix is that body's `sphereCrs()` with the same axis order and
units. Every other fix is offered with an "Apply" button and applied only
when the user clicks it.

Phase 2 adds a second automatic case: a missing CRS built from a complete
label projection group that needs no assumed convention (§4.8). Once Phase 2
builds the CRS from the group, "GDAL built a different projection from the
label" (`label-crs-mismatch`) also becomes fixable. It's offered, not
automatic, because GDAL may be right. Phase 2 also changes the
`high-distortion` suggestion to name Processing → Geometry → Reproject Image
(its Phase 1 text says "coming in a later version").

**Applying a fix.**

- `VardaRaster` gains a CRS override. `EDITABLE_SINGLE` += `"crs"`, holding
  WKT.
- `VardaRaster.crs` returns the override when set, and the cached
  transformer is cleared.
- Phase 2: `EDITABLE_SINGLE` += `"transform"` (Affine's a, b, c, d, e, f),
  and `VardaRaster.transform` returns it when set. It's for label-built
  fixes whose file has no transform (§4.8). It's validated as six finite
  numbers with a nonzero determinant, saved in sessions, written to
  exports, and recorded in `crsFix["transform"]`.
- The session already persists overrides (`session.py:105`).
- Provenance records `crsFix: {from, to, reason, automatic}` in the image's
  metadata, so every result made from it carries the record (inputs embed
  their records).
- Exported files are written with the corrected CRS.

**UI (behind the scenes, our call).**

- **Image list:** a small amber dot on the thumbnail's corner for TIP and
  PROBLEM. The tooltip gains a line, e.g. "Map: its CRS says Earth;
  read as Mars (from its label)".
- **At load:** a single status-bar message for PROBLEM or an automatic fix
  (`general_image_analysis.py:446`'s status bar, or the main window's). No
  modal dialogs.
- **Image Properties:** a "Coordinate system" group with:
  - the description's sentence;
  - the CRS's authority, or "matches IAU_2015:49910" when equivalent;
  - its WKT, collapsed;
  - each issue with its suggestion and an Apply button;
  - "Set coordinate system…", which takes EPSG, IAU_2015, WKT or PROJ
    strings, or another image's CRS.
- **Repeats:** none. An issue is shown once per image per session. The
  badge stays, the message doesn't.

**Tests.**

- The WGS84-tagged Mars GeoTIFF + PDS3 label `TARGET_NAME = MARS` →
  `earth-crs-on-body`, automatic fix to `IAU_2015:49900`.
- The same without a label → no issue (no evidence).
- A transform without a CRS → `no-crs`.
- An unparseable WKT → `unreadable-crs`.
- A MOLA MEGDR-like geographic Mars image with longitudes 0..360 → `lon-0-360`
  INFO, domain "0..360".
- An equirectangular `lat_ts=0` image at 60°N → `high-distortion` TIP.
- The Vesta 255 km sphere → Vesta, no `unknown-body`.
- A fix round-trips through the session and appears in an exported
  result's provenance.

**Docs.** `coordinate-systems.md`: "What Varda checks when an image opens",
listing each issue code, its message and its fix, plus how to report a
corrected CRS in a methods section.

**Phase.** 1 (diagnosis, the single automatic fix, Apply, Set coordinate
system). Phase 2 adds building a CRS from label projection groups (§4.8).

### 4.3 Reproject Image… (Processing analysis)

**What and why.** It's the step the docs keep telling users to do
elsewhere. It's also the engine behind the multi-image agreement features
(§4.4) and the Mosaic/Terrain projections (§4.6). It lives in Processing →
Geometry, after Resample (Resize).

**Parameters.**

| Parameter | Meaning | Units / range | Default |
|---|---|---|---|
| Target | Recommended projection; Another image's grid; Coordinate system | — | Recommended projection |
| Purpose (Recommended) | Measurement; Mosaic; Terrain (§4.6 rules) | — | Measurement |
| Image (Another image's grid, and Another image's extent) | an `ImageParameter` offering `placeableChoices`: georeferenced, same body, overlapping. The others are listed with why. | — | none |
| Coordinate System (Coordinate system) | EPSG, IAU_2015, WKT or PROJ text; parsed and body-checked (`canTransform`) when the analysis runs | — | the recommendation's PROJ string, filled in by `prepareFor` |
| Resampling | Nearest; Bilinear; Cubic; Average; Mode (GDAL's kernels) | — | Nearest for class maps (`class_tools.isClassMap`, or `classes` metadata) and single-band integer images (masks, bit masks); Bilinear otherwise |
| Resolution | ground meters per output pixel at the output's center | 0 (automatic) or 0.001–1e7 m | 0: the source's ground pixel size at its center, √(dx·dy) from `geo.measure.groundSpacing`, so a pixel's area is kept |
| Extent | Whole image; Another image's extent | — | Whole image |
| Source No-Data | one more source value to leave out of the resampling, besides the image's own no-data value and NaN | any number, or empty | empty |
| Quality Bands | also make the companion quality image | on/off | on |

With **Another image's grid**, the output is exactly that image's grid
(CRS, transform and size), so Resolution and Extent don't apply. The
parameter descriptions say which parameters each target uses. The settings
UI has no conditional visibility, and a preview widget would need a Qt
import in `varda.analysis`, which the import contract forbids.

**Output grid (`geo.reproject.gridFor`).**

- **Extent.** The source footprint in the target CRS: the image's edges are
  densified (21 points per edge) and moved with `transformPoints`. If the
  target's pole lies inside the image, the footprint includes it.
- **Pixel size.** Resolution meters at the footprint's center become map
  units through the target's areal scale there: map pixel = resolution ×
  √(areal scale) / (meters per map unit). For a geographic target, square
  degrees: Δφ = resolution / M (M is the meridional radius of curvature at
  the center).
- **Alignment (our call).** Bounds are snapped outward to whole multiples
  of the pixel size, as `gdalwarp -tap` does. Products made at one
  resolution then share pixel edges.
- **Antimeridian.** A footprint that spans more than 180° of longitude in a
  geographic or cylindrical target (it crosses the target's ±180° from
  `lon_0`) is refused, naming a `lon_0` at the scene's center. It never
  becomes a world-wide grid.
- **Size guard.** More than `MAX_OUTPUT_PIXELS` = 400,000,000 output pixels
  is refused before anything is allocated, giving the size and a coarser
  resolution that fits.

**Running.** The analysis reads one band at a time (no-data and Source
No-Data → NaN). It warps each band with `rasterio.warp.reproject` (GDAL's
warper; `src_nodata` and `dst_nodata` NaN) into a float32 cube, outside
`PROJ_LOCK`. Between different surfaces of one body, the warp gets the
planetocentric pipeline (§4.3.1).

**Output.** A new image in the target CRS. It keeps the source's bands,
wavelengths and band metadata, and a class map's `classes`/`classColors`.
No data is NaN (the result is float32). With Quality Bands, a companion
image `<name> reprojection quality` (`runAll`) has two bands:

- **Source pixel distance:** the distance, in source pixels, from each
  output pixel's center to the nearest source pixel center: the
  resampling's interpolation distance. Each output pixel center is moved
  into the source with `transformPoints`, in row chunks. NaN outside the
  source.
- **Valid** (0/1): 1 where every output band has data.

The `reprojection` metadata records:

- `from` and `to`, each with `crs` (WKT) and `description`;
- `method`, `resolutionM`, and `grid` (transform, width, height);
- `coordinateOperation`: the pipeline, or "PROJ";
- `gdal` and `proj` versions;
- `scaleChange` `{max, mean}`: the output's ground pixel size over the
  source's, sampled on a 9 × 9 grid.

**Equivalent target.** When the target is `sameCrs` with the source and
the grid is the source's, the image is copied unchanged. The description
says "already on this grid; nothing resampled".

**Memory.** `estimateMemory` is overridden. The output size comes from
`gridFor`, which is cheap. The estimate is 2 source bands as float64, plus
the float32 output cube, plus 3 float64 output planes for the quality
image. It's tested to bound tracemalloc's peak, including a 10× upsample.

**Uncertainty (docs).**

- Bilinear and cubic resampling smooth the data. The effective resolution
  is about 1.5–2× the output pixel when output ≈ input resolution
  (Schowengerdt, 2007, ch. 7).
- Cubic (GDAL's cubic convolution, Keys 1981, a = −0.5) can overshoot near
  edges and change spectra by a few percent at sharp boundaries.
- Nearest preserves values but shifts positions by up to ½ output pixel.
- Average is the correct choice when coarsening. Mode is the correct choice
  for coarsening class maps.
- Spectral data: resampling mixes neighboring spectra. Use nearest for
  band-ratio work at pixel scale.
- No-data edges: GDAL's warper leaves NaN outside the source. The edge
  pixels' kernels include fewer samples. The Valid band and the source pixel
  distance mark them.
- Position error: moving between CRSs of one body is exact (to 1e-9° for
  `transformPoints`; the warper's approximation is within GDAL's default
  0.125 px error threshold). So position error is the source's
  georeferencing error, which Varda can't know.
- Sphere ↔ ellipsoid: positions keep their planetocentric longitude and
  latitude (§4.3.1). Ground distances on the two surfaces differ by the
  body-model term (§4.5).
- The docs show how to compute the expected change from the scale factors
  (§4.5) and the recorded `scaleChange`.

**Provenance.** Through the analysis runner (`recordProvenance`), plus the
`reprojection` metadata, which is added to `KEPT_METADATA_KEYS`.

**Tests.**

- Round trip: Mars eqc → polar stereographic → eqc on a synthetic checker
  pattern. Positions agree within ½ px (nearest), and values within
  tolerance (bilinear).
- Sphere → ellipsoid: a marker at 45°N lands within ½ px of where
  `transformPoints` puts it.
- Body guard: an Earth target for a Mars image is refused with a message.
- Defaults: Nearest for a class map, which keeps its class values and
  colors.
- Equivalent CRS: the image is returned unchanged with a note, not
  resampled.
- Antimeridian and size guards.
- The memory estimate bounds tracemalloc's peak. The analysis is measured
  in its own test, since the shared memory test's scene has no CRS.

**Docs.** `docs/user-guide/reprojecting.md`, linked from the README and
`image-processing.md`'s Geometry section.

**Phase.** 2.

#### 4.3.1 Rasters between surfaces of one body

GDAL's warper builds its operation with PROJ. Between a sphere and an
ellipsoid of one body, the only operation is the ballpark that misplaces
points (§3), so warps need the same treatment as points.
`geo.transform.warpOperation(src, dst) -> str | None` returns a PROJ
pipeline that does what `transformPoints` does. Its steps:

1. src → longitude east and geodetic latitude in degrees (PROJ's own steps,
   as for `transformPoints`' `_Side.plain`);
2. degrees → radians;
3. `+proj=geoc` on src's ellipsoid (geodetic → planetocentric), left out
   when src's latitude numbers are already planetocentric;
4. `+inv +proj=geoc` on dst's ellipsoid, left out likewise;
5. radians → degrees;
6. dst's own steps.

It returns None when PROJ's own operation is exact (the same surface, or a
real transformation such as Earth's datum shifts). It raises ValueError
with `canTransform`'s reason across bodies. Every warp passes it as GDAL's
`COORDINATE_OPERATION` transformer option, through
`geo.transform.warpOptions(src, dst) -> dict[str, str]`.

Checked (GDAL 3.9.3 through rasterio 1.4.3): with the pipeline, a marker at
45°N on the Mars sphere lands on the pixel `transformPoints` predicts.
Without it, the marker lands 40 rows (20 km) off.

**Users in Phase 2:** `common.grid.onGridOf` (masks, DEMs, display overlays),
Reproject Image, and `resamplingRecord` (which records the operation).
Terrain overlays (`terrain/overlays.toProjection`) and the Find Data
basemaps move to it in Phase 3. The basemaps are display only.

### 4.4 Multi-image agreement

**What and why.** Anywhere images are combined, Varda decides the common
CRS or grid, reprojects (on the fly for display, or properly on request),
and says what it did. It never mixes bodies.

**One policy (our call):**

1. Check `compatibility` across all the inputs.
   - DIFFERENT_BODY: refuse, naming both bodies.
   - UNKNOWN with a missing CRS: link by pixel only, as today, with a
     notice.
2. SAME/EQUIVALENT: treat as one CRS, with no resampling.
3. SAME_BODY: the **reference** is the workspace's primary image (the
   first, the DEM, or the mosaic's top input). Others are reprojected to
   its CRS:
   - For display, on the fly with nearest neighbor.
   - For computation, when the user asks, through Reproject Image or
     `onGridOf`.

   The reprojection is described in a notice and recorded in any result
   (`resamplingRecord`).

**Where it applies.**

| Feature | Today | Change | Phase |
|---|---|---|---|
| Linked images (`linking.py`) | crashes cross-body | `canTransform` guard → `linkWarnings` message; `sameCrs` instead of `!=` | 1 |
| CRISM Ratio map link | same | inherits | 1 |
| Masks/DEMs from other grids (`grid.py`) | `==`, no body check, 0–360 overlap miss | `sameCrs` in `sameGrid`; body guard and longitude-aware overlap in `placementProblem` | 1 |
| Masks/DEMs between a sphere and an ellipsoid (`onGridOf`) | GDAL's ballpark misplaces by up to ~20 km on Mars | the planetocentric pipeline (§4.3.1), recorded in `resamplingRecord` | 2 |
| ROI transfer (`applyToImage`) | no body check | `canTransform` guard; skip `to_crs` when `sameCrs` | 1 |
| Points (`point_collection.py`) | first image's CRS for all | one collection CRS; points from other images converted to it on add (body-checked); the export's `image` column names each point's source (decision 14) | 1 |
| Mosaic (`model.py`) | `str()` comparison, refuses | `sameCrs`; SAME_BODY → reproject inputs to the target CRS (§4.6) | 1 (sameCrs), 3 (reprojection) |
| Terrain drapes/vectors | reprojects drapes unrecorded; mean-radius sphere for degrees | record drape reprojection in exports; degrees mean the DEM's `geodetic_crs` (as vector import) | 3 |
| Raster overlays (`raster_overlay.py`) | same grid only | same body: reprojected for display, on the fly and cached (§4.4.1) | 2 |
| Vector import (branch) | own body logic | uses `identifyBody`/`canTransform` once merged | 1 (if merged first) |

**Tests.**

- Linking a Mars image with a WGS84 image doesn't raise and gives the
  "different bodies" warning.
- `placementProblem` finds the overlap of 0–360 and −180..180 geographic
  images.
- Points from two images in different Mars CRSs export with consistent
  coordinates.
- The mosaic accepts the GDAL-WKT and PROJ-string versions of one CRS.

**Docs.** Updates to `linked-images.md`, `mosaicking.md`,
`image-processing.md` ("Resampled automatically") and
`viewing-and-measuring.md` (points), plus the "combining images" section of
`coordinate-systems.md`.

#### 4.4.1 Overlays across projections (Phase 2)

**What and why.** The overlay bar under an image workspace's views
(`workspaces/raster_overlay.py`: General Image Analysis, Dual Image) lays a
class map or a result over the image. Today it offers only images on the
same pixel grid, so a classification made from a CTX mosaic can't be seen
over a HiRISE image of the same place. Phase 2 lets the bar take any image
on the same body that overlaps the base image. Such an image is
**reprojected for display only**, onto the base image's grid.

**Choices (our call).** The bar offers `displayChoices(images, base)`
(`common/display_grid.py`, Qt-free):

- images on the base's grid (`sameGrid`), shown as today;
- images `placeableChoices` accepts: georeferenced, same body
  (`canTransform`), and overlapping the base, 0–360 and −180..180 alike;
  except
- any image whose display reprojection would exceed
  `DISPLAY_MAX_PIXELS` = 100,000,000 base pixels. It's left out with "too
  large to reproject for display (N Mpx); reproject it with Processing →
  Geometry → Reproject Image".

Images left out are counted in the bar ("(2 can't be overlaid)"), and the
tooltip lists each with its reason, e.g. "Mars and Earth are different
bodies" or "doesn't overlap this image". Other bodies are always refused.

**Reprojection.**

- `reprojectForDisplay(overlay, base, band) -> np.ndarray` is `onGridOf`
  with **nearest neighbor**, as float32. Values are never blended, so a
  class map stays a class map. Positions are within ½ overlay pixel (plus
  ½ base pixel). Sphere ↔ ellipsoid positions keep their planetocentric
  coordinates (§4.3.1).
- Only the band the layer needs is reprojected: the Class band for
  **Classes** (then colored as today), or the chosen band.
- It runs with `runInBackground` (owner: the bar). The bar shows
  "Reprojecting…" until it's done. A result for a choice that has since
  changed is dropped (a generation counter).
- Failures (`onFailed`) leave no overlay. The bar says "couldn't reproject:
  <reason>". Nothing raises into Qt.

**Cache (our call).** `DisplayCache` keeps reprojected arrays, keyed by
(overlay, band, base grid). The overlay is held weakly. The base grid key
is its CRS WKT, transform and size, so a base with a new `crs` override
misses the cache. Eviction is least recently used, within
`DISPLAY_CACHE_BYTES` = 512 MiB. An overlay's entries are dropped when its
metadata changes (`sigMetadataChanged`: no-data, band names, `crs` or
`transform` override). Changing a class's color recolors from the cache
without reprojecting.

**Shown.** When the overlay is reprojected, the bar shows a muted label,
"reprojected for display". Its tooltip gives the overlay's CRS description
→ the base's, "nearest neighbor; for display only: analyses, statistics and
exports use each image as it is". Nothing reprojected for display is
written anywhere. An overlay on the same grid shows no label.

**Not covered.** Overlays are display only. Pixel-for-pixel tools (Band
Math, Image Arithmetic, zonal columns) still need the same grid. Use
Reproject Image with "Another image's grid" to make one.

**Tests.**

- `displayChoices`: same grid; an `IAU_2015:49912` overlay on an
  `IAU_2015:49910` base (offered); a WGS84 overlay on Mars (refused, naming
  both bodies); a non-overlapping image (refused); a too-large base
  (refused with the size).
- `reprojectForDisplay`: a marker at 45°N on the sphere overlay lands
  within ½ px of `transformPoints`' position on an ellipsoid base. A class
  map's values stay integers, with no new values.
- `DisplayCache`: a hit returns the same array object; a base `crs` override
  misses; the byte budget evicts the oldest; a dead overlay frees its
  entries.
- GUI (pytest-qt): choosing a cross-projection overlay shows
  "reprojected for display" and renders after `qtbot.waitUntil`. A stale
  result (choice changed meanwhile) is dropped. Changing a class color
  doesn't reproject again. The bar passes `assertNothingClipped` at
  1280×800.

**Docs.** `viewing-and-measuring.md` gets a new "Overlays" section.
`image-processing.md`'s "Same grid only" list drops overlays and points to
it. `coordinate-systems.md`'s "Combining images" gets one line.

**Phase.** 2 (moved from Phase 4).

### 4.5 Measurements and distortion

**What and why.** Every measurement is made on the body, whatever the
image's projection, and states its method and expected error. The
projection's distortion is reported where it affects what the user sees or
measures by other means (planar map-unit ROI columns, shapes, slopes).

**Method (our call).**

- **Positions.** Map coordinates are converted to longitude east and
  planetocentric latitude on the CRS's own surface
  (`geo.transform.planetocentricLonLat`, under `PROJ_LOCK`), then to that
  surface's geodetic latitude for the geodesics.
- **Lengths.** The sum of geodesic segment lengths (Karney, 2013, through
  `Geod.inv`) on **the CRS's own ellipsoid or sphere**. This is consistent
  with how the data were projected, and with ISIS, ArcGIS and QGIS. Lines
  are densified at about 1 px (as now) so they follow the line as drawn on
  the image.
- **Areas and perimeters.** Polygon vertices are densified at ≤ 1 px along
  each edge, then `Geod.polygon_area_perimeter` (Karney, 2013) is applied.
  Holes are subtracted.
- **Body-model term.** When the CRS's surface differs from the body's IAU
  2015 reference ellipsoid (a sphere on a flattened body, or a nonstandard
  radius), the same measurement is also made on the reference ellipsoid.
  Planetocentric latitudes are converted to planetographic first with
  tan φg = (a/c)² tan φc. The relative difference is reported. It's
  typically ≤ 0.6% on Mars (flattening 0.0059) and 0 on the Moon. With
  HiRISE's 3376.2 km polar sphere it's about 0.005% near the pole (that
  sphere's radius is Mars's polar radius); the Mars 2000 sphere differs by
  about 0.6% at 85°N. (Not converting the latitude would instead compare
  points ~3.5 km apart and give ~1.2%.)
- **Not covered.** Measurements are horizontal, on the reference surface.
  Slope distance over topography (the 3D path length along a DEM) is a
  Phase 3 option for profiles with a DEM.
- **Reported error** (in the CSV header, the docs and tooltips):
  - the geodesic algorithm's error (≈ nm, negligible);
  - the densification (≤ 1 px of path following);
  - the body-model term;
  - "plus the georeferencing error of the image, which Varda can't know"
    (cite the image's own accuracy where its metadata gives it).

**Distortion.**

- `Proj(crs).get_factors(lon, lat)` gives the meridional scale h, the
  parallel scale k, the areal scale, and the maximum angular distortion ω
  (PROJ docs, *Factors*; Snyder 1987, ch. 4).
- Scale error = max(|h − 1|, |k − 1|).
- For a geographic CRS, the image is shown with square pixels, i.e. as an
  equirectangular map in degrees, so the displayed shape depends on the
  pixel aspect: h = 1 and k = (Δφ_px/Δλ_px) · M / (N cos φ), with M and N
  the meridional and prime-vertical radii of curvature at the geodetic
  latitude φ (equal on a sphere; Snyder 1987, eqs. 4-18, 4-20). For
  square-degree pixels on a sphere this is k = 1/cos φ (Snyder's
  equirectangular with φ₁ = 0). A planetocentric latitude φc on a
  flattened ellipsoid is converted with tan φg = (a/c)² tan φc, and M is
  multiplied by dφg/dφc = (a/c)² / (cos² φc + (a/c)⁴ sin² φc).
  sin(ω/2) = |h − k| / (h + k). Measurements are geodesic anyway.
- **Thresholds (our call):** scale error > 1% or ω > 1° → TIP. Scale error
  > 10% or ω > 10° → stronger wording ("shapes and slopes are badly
  distorted here"). The constants are `SCALE_TIP = 0.01`,
  `ANGLE_TIP_DEG = 1.0`, `SCALE_STRONG = 0.10` and `ANGLE_STRONG_DEG = 10.0`.
- **Worst over an extent:** sample a 9 × 9 grid over the image (or over the
  measured geometry's bounding box).

**Where measurements change.**

| Measurement | Today | New | Phase |
|---|---|---|---|
| Line length (Lines manager) | geodesic in geographic CRSs, planar in projected ones | geodesic always; tooltip states method and body-model term | 1 |
| Profile distance + CSV | same | geodesic always; header: method, surface, body-model term, distortion at the line | 1 |
| ROI area/perimeter | planar map units (degrees² in geographic CRSs) | new columns **Area on the body (km²)** and **Perimeter on the body (km)**, geodesic; "map units" columns kept, labeled planar, with a tooltip giving the distortion at the ROI (Phase 1 gives the distortion over the whole image, as the guide says; per ROI is later work) | 1 |
| Pixel area for zonal sums | — | `pixelAreas(crs, transform, rows, cols)`: exact geodesic area per pixel row where area depends on the row only (geographic CRSs and north-up equirectangular, Mercator and cylindrical equal-area), else the map pixel area over the areal scale at the pixel center. Used by ROI columns **Area of pixels with data (km²)**, **Area-weighted mean** and **Sum × pixel area**, and by class-map areas (Image Properties → Classes) | 2 |
| Terrain analysis (slope, aspect, hillshade, curvature) | grid spacing in map units; refuses geographic | per-pixel ground spacing from the scale factors: dx = Δx·u/k, dy = Δy·u/h (u = the unit's meters); geographic CRSs allowed (spacing on the CRS's surface); the description states the method; the z unit is checked against meters | 3 |
| Shadow mask | same as Terrain analysis | same | 3 |
| Terrain mesh (`groundSize`) | center-scale approximation | build in the recommended local projection (§4.6) | 3 |

**Interfaces.**

```python
# varda/geo/distortion.py
SCALE_TIP: float = 0.01
ANGLE_TIP_DEG: float = 1.0
SCALE_STRONG: float = 0.10
ANGLE_STRONG_DEG: float = 10.0

@frozen
class Distortion:
    meridionalScale: float     # h
    parallelScale: float       # k
    arealScale: float
    angularDistortionDeg: float  # ω
    @property
    def scaleError(self) -> float: ...   # max(|h-1|, |k-1|)
    @property
    def level(self) -> str: ...          # "ok" | "tip" | "strong"

def distortionAt(crs, lon: float, lat: float) -> Distortion
def distortionAtPixels(crs, transform: Affine, cols: np.ndarray, rows: np.ndarray) -> list[Distortion]

@frozen
class DistortionSummary:
    worst: Distortion
    where: tuple[float, float]   # lon, lat of the worst sample
    text: str                    # "up to 5.3% scale error (east–west) at 18.4°N"

def distortionOver(crs, transform: Affine, width: int, height: int,
                   window: tuple[int, int, int, int] | None = None) -> DistortionSummary | None
```

```python
# varda/geo/measure.py
@frozen
class Measurement:
    value: float               # meters or square meters
    unit: str                  # "m" | "m²"
    method: str                # "geodesic (Karney 2013) on sphere R = 3396.19 km"
    reference: float | None    # the same on the body's IAU 2015 reference ellipsoid, or None
    bodyModelDifference: float # |value - reference| / reference, 0 when reference is None
    distortion: DistortionSummary | None  # of the image's projection over the measured geometry

def toLonLat(crs, xy: np.ndarray) -> np.ndarray      # (n, 2) map → lon/lat in crs.geodetic_crs
def cumulativeDistance(crs, xy: np.ndarray) -> np.ndarray  # meters along (n, 2) map points
def lineLength(crs, xy: np.ndarray) -> Measurement
def polygonArea(crs, polygon: shapely.Polygon | shapely.MultiPolygon,
                densifyStep: float) -> tuple[Measurement, Measurement]  # area, perimeter
def groundSpacing(crs, transform: Affine, rows: np.ndarray, cols: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray]  # meters per pixel along columns, rows (Phase 3 user)

# Phase 2
def pixelAreas(crs, transform: Affine, rows: np.ndarray, cols: np.ndarray
               ) -> np.ndarray          # m² on the CRS's own surface; NaN where PROJ can't invert
def imagePixelAreas(crs, transform: Affine, width: int, height: int
                    ) -> np.ndarray     # (height, width) m²
AREA_METHOD_ROWS = "exact per row (geodesic polygon, edges densified to 16 points)"
AREA_METHOD_SCALE = "map pixel area over the projection's areal scale at the pixel center"
def pixelAreaMethod(crs, transform: Affine) -> str   # "{AREA_METHOD_…} on {surface}"
```

```python
# varda/geo/distortion.py (Phase 2)
def arealScaleAtPixels(crs, transform: Affine, cols: np.ndarray, rows: np.ndarray
                       ) -> np.ndarray  # vectorized areal scale; NaN where not finite
```

**Pixel areas (Phase 2).**

- **Row-only layouts.** The transform has no rotation, and the CRS is
  geographic or a cylindrical projection whose scale depends on latitude
  only: Equidistant Cylindrical, Mercator, or Lambert Cylindrical Equal
  Area. Then each row's pixels share one area. It's computed once per row,
  as `Geod.polygon_area_perimeter` of the pixel's corners with each edge
  densified to 16 points, so the top and bottom edges follow their
  parallels. The geodesic chords between the 16 points cut inside the
  parallels, so the relative error is ≈ δλ² (cos² φ − 2 sin² φ) / 12 with
  δλ = Δλ/16 (radians), at most ≈ 2.1e-7 · (Δλ/1°)² (2e-9 for 0.1° pixels);
  plus the geodesic algorithm's (nanometers).
- **Otherwise:** the area is |det(transform)| × u² / areal scale at the
  pixel center (u is the map unit's meters). The areal scale is PROJ's
  (`arealScaleAtPixels`). The relative error is of order (pixel / R)², so
  it's negligible for pixels ≪ R.
- **Whole images** (`imagePixelAreas`) larger than 4,000,000 pixels
  outside the row-only case evaluate the pixel areas (map area over areal
  scale) on a grid of every n-th pixel (n = ⌈√(pixels / 4,000,000)⌉, plus
  the last row and column) and interpolate bilinearly, in blocks of rows
  (peak memory ≈ 1× the result). Row-only images return a read-only
  broadcast of the row areas. Scale factors vary smoothly, and the test
  bounds the interpolation error below 1e-6 relative on a 4000 × 4000
  polar stereographic image of 1 km pixels (grid spacing 2 km).
- An ROI's or class's area is the sum of its pixels' areas. That's the area
  of the **pixels** chosen (centers inside), not of the drawn polygon, so it
  differs from **Area on the body** by up to half a pixel along the
  perimeter. The guide says so.

**Tests.**

- On a Mars 3396.19 km sphere, 1° of latitude = π·R/180 = 59,274.70 m
  (exact) in both geographic and equirectangular CRSs.
- A 10 km east–west line at 60°N in `lat_ts=0` equirectangular: today 20
  km by planar, new 10.000 km ± 1 m.
- A 1 km × 1 km square ROI in polar stereographic at 85°N: area 1.000 km²
  ± 0.1%.
- LRO WAC (Moon sphere): `reference is None`, body-model difference 0.
- HiRISE polar sphere (3376.2 km) at 85°N: body-model difference
  ≈ 4.5e-5 (Mars 2000 sphere: ≈ 0.59%).
- `distortionAt` for eqc `lat_ts=0` at 60°: k = 2.000, h = 1.000 (Snyder,
  p. 91).
- Polar stereographic (sphere, k₀ = 1 at the pole) at 65°: k = 2/(1 + sin
  65°) = 1.04915 (Snyder eq. 21-4).
- Transverse Mercator at 300 km from the central meridian on Mars:
  k = 1/√(1 − sin²(x/R)) ≈ 1 + x²/2R² = 1.0039.

**Docs.** `coordinate-systems.md`: "How Varda measures", with formulas,
the body-model term and a worked example. `lines-and-profiles.md` and
`viewing-and-measuring.md` replace their "projected distances include the
projection's scale error" limitations with the new method, and keep the
planar columns' caveat.

### 4.6 Mosaicking and 3D Terrain

**What and why.** These are the two places users most need a projection
chosen for them, and where wrong ones hurt most: seams, tie points, the
mesh's shape, slopes. The recommender is built in Phase 2, for Reproject
Image's "Recommended projection". Mosaic and Terrain use it in Phase 3.

**`recommendProjection(crs, footprint, purpose, existing)` (our call):**

Here `crs` is the reference image's CRS (its body, or its own surface when
the body is unknown), and `footprint` is that image's `footprintLonLat`.
"Center" means the footprint's center in planetocentric degrees.

1. **|center lat| ≥ 65° or the footprint contains a pole** → polar
   stereographic on the surface (rule 4), `lat_0` = ±90, `lon_0` = 0, true
   scale:
   - MOSAIC: at the pole (`lat_ts` = ±90; the HiRISE/PDS convention, so
     products match).
   - TERRAIN/MEASUREMENT: at the center latitude rounded to 0.1° (so k = 1
     where the data are).

   The 65° threshold matches HiRISE's projection rule (McEwen et al.,
   2010; HiRISE RDR SIS).
2. **MOSAIC otherwise** → equirectangular, `lat_ts` = the center latitude
   rounded to 1°, `lon_0` = the center longitude rounded to 1°. Halves
   round away from zero (77.5 → 78), not Python's banker's rounding. This
   is the convention CTX/HiRISE/ISIS `cam2map` products use, so most inputs
   need no resampling. If every `existing` CRS is one same-body CRS
   (`sameCrs`) that `isGoodEnough` over the footprint, keep it.
3. **TERRAIN and MEASUREMENT otherwise:**
   - Keep `crs` itself if it's projected and `isGoodEnough` over the
     footprint: worst scale error ≤ `SCALE_TIP` (1%) and ω ≤
     `ANGLE_TIP_DEG` (1°), on a 9 × 9 lon/lat grid with `distortionAt`.
     A geographic CRS is never kept for these purposes: its pixels aren't
     square on the ground.
   - Otherwise use transverse Mercator on the surface, centered on the
     footprint (`lat_0`, `lon_0` = the center rounded to 0.01°, k₀ = 1).
     It's used when every footprint point lies within 0.14·R of the
     central meridian. That's angular distance B with sin B = cos φ sin Δλ
     ≤ sin(0.14). There the scale error k − 1 = 1/cos B − 1 ≤ 0.99%
     (Snyder, 1987, eq. 8-1 on a sphere): 475 km either side on Mars
     (950 km wide) and 243 km on the Moon.
   - Wider footprints: Lambert azimuthal equal-area centered on the
     footprint. Areas are exact, and the reason states the worst shape
     distortion.
4. **Surface:** the body's IAU 2015 sphere (`Body.sphereCrs()`'s radius),
   unless every `existing` CRS is on the body's IAU reference ellipsoid,
   in which case that ellipsoid. For an unknown body, `crs`'s own surface.

`Recommendation.reason` is one line, e.g. "Polar stereographic: the scene
reaches 82.3°N", "Kept its own projection: under 1% scale error over the
scene", or "Transverse Mercator centered on the scene: its equirectangular
map has 15.5% scale error here".

```python
# varda/geo/recommend.py (Phase 2)
class Purpose(LabeledEnum):
    MEASUREMENT = "Measurement"
    MOSAIC = "Mosaic"
    TERRAIN = "Terrain"

@frozen
class Footprint:
    west: float; south: float; east: float; north: float  # planetocentric, east, degrees;
                                         # west < east, unwrapped around the center
    center: tuple[float, float]          # lon, lat
    pole: str | None                     # "north" | "south" when the image contains it

@frozen
class Recommendation:
    wkt: str
    description: CrsDescription
    reason: str             # "Polar stereographic: the scene reaches 82.3°N"
    keptExisting: bool

def footprintLonLat(crs, transform: Affine, width: int, height: int) -> Footprint
def isGoodEnough(crs, footprint: Footprint) -> tuple[bool, str]
def recommendProjection(crs, footprint: Footprint, purpose: Purpose,
                        existing: Sequence[CRS | str] = ()) -> Recommendation
```

The Phase 1 sketch took a `Body` and a bounds tuple. It now takes the
reference CRS, so unknown bodies still get a recommendation, and a
`Footprint`, so polar images that contain the pole are recognized.

**Mosaic (Phase 3).**

- The target CRS comes from `recommendProjection(..., MOSAIC, existing=inputs)`.
  The Grid step shows it with a "Change…" button (CRS entry, or "same as
  input N").
- Inputs in other CRSs of the same body are reprojected on the fly for
  display, and with the chosen resampling at render or export.
- Tie points are stored in each input's own CRS and converted (the
  mosaic's 3×3 correction is defined in the target CRS).
- The recipe records the target CRS and each input's reprojection.
- Docs: `mosaicking.md`, "Map projection".

**Terrain (Phase 3).**

- The DEM is converted into the recommended TERRAIN projection when
  `isGoodEnough` fails, so x, y and z are true meters and the mesh is
  undistorted. Drapes follow (they already reproject to the DEM's CRS).
- The vertical datum is asked once: "Heights are relative to: Areoid /
  Sphere R = … / Ellipsoid / Unknown". It's prefilled from label keywords
  and product types (MOLA MEGDR → areoid; HiRISE DTM → areoid; an ASP
  `point2dem` product without `dem_geoid` in its history → the CRS's
  surface). It's stored in the session and in exports, and stated in the
  status line.
- Combining DEMs with different datums (a Mosaic of DEMs, DEM
  differencing) is refused until Phase 4's conversion, with the reason.
- Docs: `3d-terrain.md`, "Projection and heights". It replaces the
  "Geographic DEMs are drawn as a flat rectangle" limitation, and adds the
  vertical datum effects from §2.

**Tests.**

- Recommendation (Phase 2):
  - a scene at 18.4°N 77.5°E (MOSAIC) → equirectangular, `lat_ts` 18,
    `lon_0` 78;
  - HiRISE at 85°N → polar stereographic, `lat_ts` 90 (MOSAIC) or 85.0
    (TERRAIN);
  - a south polar LRO WAC image containing the pole → polar stereographic
    south;
  - LRO WAC equatorial → equirectangular;
  - a 300 km-wide scene at 30°N in `lat_ts=0` equirectangular (TERRAIN)
    → transverse Mercator (its own map has 15.5% scale error there);
  - a 1000 km-wide Mars TERRAIN extent → Lambert azimuthal equal-area;
  - a 3000 km-radius unknown-body sphere → the recommendation on that
    sphere.
- `isGoodEnough` is true for the CTX DEM's own CRS (`lat_ts` at its
  center).
- The Terrain mesh of a `lat_ts=0` DEM at 60°N has the correct east–west
  size (±0.5%).
- A mosaic of eqc and polar-stereographic Mars inputs renders with both in
  place (synthetic pattern, ½ px).

**Phase.** 2 for `geo.recommend`. 3 for the Mosaic and Terrain use of it.
Areoid conversion is Phase 4.

### 4.8 CRSs built from label projection groups (Phase 2)

**What and why.** GDAL builds an image's CRS from its PDS3, ISIS or PDS4
label. Sometimes it builds none: a projection or keyword it doesn't
handle, or missing radii. Sometimes it builds the wrong one: the GDAL 3.9.3
behavior in §2. Phase 1 kept the label's raw projection group
(`LabelHints.projectionGroup`). Phase 2 builds a CRS from that group and
compares it with GDAL's.

```python
# varda/geo/label_crs.py
@frozen
class LabelCrs:
    wkt: str | None               # None when the group can't make one
    # Affine's a, b, c, d, e, f from the label, or None when it has no
    # complete pixel-to-map keys
    transform: tuple[float, float, float, float, float, float] | None
    projection: str               # the label's name for it, e.g. "EQUIRECTANGULAR"
    assumed: tuple[str, ...]      # conventions Varda had to assume, in words
    missing: tuple[str, ...]      # keys needed and absent (or unreadable)
    source: str                   # hints.source

    @property
    def complete(self) -> bool: ...   # wkt is not None and nothing was assumed

def labelCrs(hints: LabelHints) -> LabelCrs | None   # None: no projection group; never raises
```

**Projections (our call).** The label's projection names map to PROJ.
PDS3 uses `MAP_PROJECTION_TYPE`, ISIS uses `ProjectionName`, and PDS4 uses
the `cart:` element. Names are matched ignoring case, spaces and
underscores.

| Label | PROJ |
|---|---|
| EQUIRECTANGULAR; Equirectangular | `+proj=eqc +lat_ts=CENTER_LATITUDE +lat_0=0 +lon_0=CENTER_LONGITUDE` |
| SIMPLE CYLINDRICAL; SimpleCylindrical | `+proj=eqc +lat_ts=0 +lat_0=0 +lon_0=CENTER_LONGITUDE` (as GDAL does) |
| POLAR STEREOGRAPHIC; PolarStereographic; Polar_Stereographic | `+proj=stere +lat_0=±90` (the sign of CENTER_LATITUDE) `+lon_0=CENTER_LONGITUDE`, then `+k=1` when \|CENTER_LATITUDE\| = 90 (PROJ's variant A, as `HIRISE_POLAR_PROJ`), else `+lat_ts=CENTER_LATITUDE` (variant B, true scale there). `equivalence` compares method names, so a pole-true-scale CRS is never written as variant B. |
| SINUSOIDAL | `+proj=sinu +lon_0` |
| ORTHOGRAPHIC | `+proj=ortho +lat_0 +lon_0` |
| TRANSVERSE MERCATOR | `+proj=tmerc +lat_0 +lon_0 +k=SCALE_FACTOR (1 when absent)` |
| MERCATOR | `+proj=merc +lat_ts=CENTER_LATITUDE +lon_0` |
| LAMBERT CONFORMAL (CONIC) | `+proj=lcc +lat_1=FIRST_STANDARD_PARALLEL +lat_2=SECOND_STANDARD_PARALLEL +lat_0 +lon_0` |
| LAMBERT AZIMUTHAL EQUAL AREA | `+proj=laea +lat_0 +lon_0` |
| anything else (OBLIQUE CYLINDRICAL, POINT PERSPECTIVE, …) | none: `missing` = ("a projection Varda can build: OBLIQUE CYLINDRICAL",) |

**Keys.** PDS3 uses `A_AXIS_RADIUS` and `C_AXIS_RADIUS` (km),
`CENTER_LATITUDE`, `CENTER_LONGITUDE`, `MAP_SCALE` (km/pixel),
`LINE_PROJECTION_OFFSET` and `SAMPLE_PROJECTION_OFFSET` (pixels). ISIS uses
`EquatorialRadius` and `PolarRadius` (m), `CenterLatitude`,
`CenterLongitude`, `PixelResolution` (m), `UpperLeftCornerX` and
`UpperLeftCornerY` (m). PDS4 uses `cart:a_axis_radius` and
`cart:c_axis_radius` (in their `unit` attribute's unit, which Phase 2 keeps
in `projectionGroup` as `{KEY}_UNIT`; m when absent), the projection element's
center parameters, `cart:pixel_resolution_x`/`_y`, and
`cart:upperleft_corner_x`/`_y`. Units in `<…>` are honored (km or m). A
value that isn't a number counts as missing ("N/A", "UNK").

**Surface (our call).** Here a = the equatorial radius and c = the polar
radius from the label. B_AXIS_RADIUS is ignored, and a triaxial label adds
a line to `assumed`.

| Label | Surface | Assumed? |
|---|---|---|
| a = c (to 1 m), or only a | sphere R = a | no |
| a ≠ c, planetographic (not Equirectangular with φ ≠ 0) | ellipsoid (a, c); geodetic latitude is planetographic | no |
| a ≠ c, planetographic, Equirectangular with center latitude φ ≠ 0 | the ellipsoid scaled to a = r (the local radius below), c × r/a: ISIS's Equirectangular uses r whatever the latitude type (x = r·cos φ·Δλ, y = r·φ_label; `Equirectangular::SetGround`), and PROJ's eqc on that ellipsoid gives the same numbers with planetographic latitude (decided in Task 8 of Phase 2) | yes |
| a ≠ c, planetocentric, Equirectangular with center latitude φ ≠ 0 | sphere of the local radius at φ, r = a·c / √((c cos φ)² + (a sin φ)²): ISIS's Equirectangular convention. With a = 3396.19, c = 3376.20 km and φ = 15° it gives 3394839.8133 m, the CRISM MTRDR's sphere exactly | yes ("ISIS's convention: the sphere of the local radius at the center latitude") |
| a ≠ c, planetocentric, Polar Stereographic | sphere R = c (as GDAL 3.9.3 does) | yes |
| a ≠ c, planetocentric, other projections | sphere R = a (as GDAL 3.9.3 does) | yes |
| no radii | the label target's IAU 2015 sphere (`Body.sphereCrs`) | yes ("radii from the IAU 2015 Mars sphere; the label has none") |
| no radii and no known target | none: `missing` names the radii | — |

**Longitude.** For a `POSITIVE_LONGITUDE_DIRECTION = WEST` (or ISIS
`PositiveWest`) label, the CRS's `lon_0` is the east-positive equivalent,
−CENTER_LONGITUDE normalized to (−180, 180]. Its geodetic CRS's longitude
axis points west, as in `IAU_2015:49901`, so readouts show °W as the label
does. Map x still increases eastward (PDS3 samples and ISIS x both increase
to the east). The label's `LongitudeDomain` doesn't change the CRS.

**Transform.** It's built only when the image's own transform is the
identity (GDAL gave none), and it's an override like the CRS (§4.2):

- PDS3: s = MAP_SCALE in m. Upper-left corner x₀ = −(SAMPLE_PROJECTION_OFFSET
  + 0.5)·s and y₀ = (LINE_PROJECTION_OFFSET + 0.5)·s, with pixels s × −s.
  This is the PDS3 standard's sample/line ↔ x/y relation (1-based pixel
  centers), and it matches what GDAL 3.9.3 builds from the same label
  (offsets −20.5 and 100.5 at 0.5 km → x₀ = 10,000 m, y₀ = 50,500 m).
- ISIS: Affine(PixelResolution, 0, UpperLeftCornerX, 0, −PixelResolution,
  UpperLeftCornerY).
- PDS4: the same from the `cart:` corner and resolution keys.

When GDAL gave a transform, it's kept and not compared (a limitation,
stated in the guide: GDAL's PDS driver has its own offset options).

**Rules in the health check (our call).** They apply only when the hints
come from the image's own label, never from Varda provenance. A result's
grid may differ from its inputs'.

| Situation | Issue | Fix |
|---|---|---|
| No CRS; `labelCrs.complete` | `no-crs` PROBLEM, message "Its file has no coordinate system; Varda built it from its label's map projection." | **Automatic**: the label's CRS, plus its transform when the file has none. Reason: "Its file has no coordinate system; built from its {source} map projection group ({projection}, {surface})". Recorded as `crsFix` with `"source": "label"`. |
| No CRS; a CRS built with assumptions | `no-crs` PROBLEM | Offered (Apply). The suggestion names the assumption. |
| No CRS; no CRS can be built | `no-crs` PROBLEM | none. The suggestion lists `missing`, then "Set coordinate system…". |
| A CRS (after any Earth fix) that isn't `sameCrs` with the label's | `label-crs-mismatch` PROBLEM, message "Its label describes {label CRS text}, but its CRS is {CRS text}." | Offered (Apply), never automatic: GDAL may be right |
| A CRS `sameCrs` with the label's, or a label without a buildable CRS | nothing new | — |

`sameCrs` compares a projected CRS's own axes, not its base CRS's longitude
direction, so a west label and GDAL's east reading with the same `lon_0` are
the same map (no `label-crs-mismatch`); `lon-direction-mismatch` reports the
direction. ISIS's `CenterLatitudeRadius`, when present, is the local radius
used (it's what ISIS projected with; the formula otherwise).

**Map x eastward, per file (decided in Task 8).** `lon-bounds-westward`
PROBLEM, no fix, from the image's own label only: for cylindrical methods
(Equidistant Cylindrical, Mercator variants A/B; not transverse or oblique),
the longitudes of the image's left and right edges at its middle row, read
eastward and mirrored about the central meridian (x′ = 2·x₀ − x), against
the label's range (PDS3 `WESTERNMOST`/`EASTERNMOST_LONGITUDE`, ISIS
`MinimumLongitude`/`MaximumLongitude`, PDS4 `west`/`east_bounding_coordinate`)
as an unordered pair mod 360, within max(3 px, 0.0001°). Raised only when the
mirrored reading fits and the eastward one doesn't; skipped when the edges
span 359° or more. Advisory: if it fails, only it is skipped (logged).

The existing `lon-direction-mismatch` and `latitude-type-mismatch` stay.
They say why the two differ. The `earth-crs-on-body` fix is unchanged: it
runs first, and the label comparison is of the fixed CRS.

**Tests.**

- ISIS `Mapping` (Equirectangular, EquatorialRadius 3396190, PolarRadius
  3376200, Planetocentric, CenterLatitude 15) → `sameCrs` with
  `CRISM_MTRDR_WKT`, with the local-radius convention in `assumed`.
- A HiRISE-style PDS3 group (a = c = 3376.2 km, POLAR STEREOGRAPHIC,
  CENTER_LATITUDE 90) → `sameCrs` with `HIRISE_POLAR_PROJ`, and `complete`.
- The GDAL-wrong label of §2 (WEST, CENTER_LONGITUDE 100, PLANETOGRAPHIC,
  a ≠ c) → `lon_0` −100, an ellipsoid, a west longitude axis. The health
  check of GDAL's CRS against it → `label-crs-mismatch` with an offered
  fix.
- The PDS3 transform matches GDAL's for the same label (written to
  `tmp_path` and opened with rasterio).
- `OBLIQUE CYLINDRICAL` → `wkt` None, and `missing` names it.
- `A_AXIS_RADIUS = "N/A"` without a known target → `missing` names the
  radii. It never raises.
- Hints from provenance only → no label issue.

**Docs.** `coordinate-systems.md` gets "CRSs built from the label": the
tables above in user words, and how a fix built from the label is
reported.

### 4.7 UI conventions

- **Readout (Phase 1).** The viewport's bottom-left readout
  (`image_viewport.py:493-505`) shows the image's own convention and
  names it:
  - `lat 18.40312° N  lon 77.45120° E  (Mars, planetocentric)`;
  - with 0–360 images, `lon 257.45120° E (0–360)`;
  - with west-positive CRSs, `° W`;
  - planetographic latitudes say so.
  
  Projected `xy` gains its unit (`xy 1234.5, -987.0 m`).
- **Status line in workspaces (Phase 4).** An optional readout of
  lat/lon/xy and the local scale error under the cursor. It's off by
  default, under View.
- **Image Properties (Phase 1).** The "Coordinate system" group (§4.2).
  Pixel size gets units and meters at the center: "18.0 × 18.0 m (18.0 m
  on the ground at center)".
- **Defaults (our call):**
  - Planetocentric latitude, east-positive longitude.
  - Longitude domain as the image has it. Catalogs keep −180..180 for their
    maps.
  - Measurements on the CRS's own surface, with the IAU reference as the
    error term.
  
  These follow IAU WGCCRE recommendations for planetocentric coordinates
  (Archinal et al., 2018; Seidelmann et al., 2002).
- **Not nagging:**
  - No modal dialogs for CRS matters anywhere.
  - A badge and tooltip per image, and one status-bar message per PROBLEM
    per session.
  - Distortion tips appear only where they matter: Properties, ROI
    map-unit columns, Terrain analysis results, the Mosaic grid step and
    the Terrain status line.
  - Recommendations are offered with "Use" and never applied silently,
    except where a workspace must choose (the Mosaic target and the
    Terrain mesh). The choice is shown and recorded.

---

## 5. Phases

| Phase | Scope | Why this order |
|---|---|---|
| **1. Core, measurements, health check** | `varda.geo` (bodies, describe, equivalence, distortion, measure, labels, health); consolidate body logic; geodesic line lengths, profiles and ROI areas; cross-body guards and `sameCrs` in linking, grids, ROI transfer, points and the mosaic check; health check with badge, Properties group, the one automatic fix, Apply, and Set coordinate system; readout labels; `coordinate-systems.md`. | The most user value at the lowest risk. It fixes wrong numbers (measurements) and crashes (cross-body), and makes problems visible. It adds no new resampling of science data. |
| **2. Reproject Image, overlays, label-built CRSs** | `geo.recommend` (§4.6, moved from Phase 3); `geo.reproject` and the Reproject Image analysis with its guide (§4.3); planetocentric warps between a sphere and an ellipsoid, also fixing `onGridOf` (§4.3.1); overlays across projections, reprojected for display (§4.4.1, moved from Phase 4); `transform` overrides on images; CRS (and transform) from PDS3/ISIS/PDS4 projection groups with `label-crs-mismatch` (§4.8); pixel areas, ROI area columns and class-map areas (§4.5). | Gives users the tool the docs point to, and the overlay they ask for most. Builds the engine and the recommender for Phase 3. |
| **3. Mosaic and Terrain projections** | Mosaic target CRS from `geo.recommend` with on-the-fly reprojection of inputs; Terrain mesh in a local projection; terrain drapes and Find Data basemaps through `warpOperation`; vertical-datum declaration; scale-correct Terrain analysis and Shadow Mask (geographic allowed); profile 3D path length over a DEM. | Depends on Phases 1–2. Touches the most complex workspaces. |
| **4. Everywhere else** | Areoid download and conversion (Optional Features); workspace status-line readout; a project-level "working CRS" for new workspaces; clean up `GeoReferencer`, `image_utils` and the old ROI paths. | Lower value per effort, or depends on data downloads. |

---

## 6. Test fixtures (real-style CRSs)

`src/varda/geo/_tests/fixtures.py` builds small `ArrayDataSource` images
(no data files), each with a CRS exactly as the producing software writes
it.

| Name | CRS (as written by) | Grid | Purpose |
|---|---|---|---|
| `CRISM_MTRDR` | A real MTRDR (FRT000107A7) as GDAL's PDS3 driver reads it: `PROJCS["EQUIRECTANGULAR MARS", GEOGCS["GCS_MARS", DATUM["D_MARS", SPHEROID["MARS_localRadius",3394839.81331631,0]]…], PROJECTION["Equirectangular"], standard_parallel_1=15, central_meridian=0…]`. The radius is Mars's local radius under this scene (ISIS's equirectangular convention, §4.8), not a Mars constant. `CRISM_MTRDR_ENVI` is the same product as GDAL's ENVI driver reads it; `CRISM_MTRDR_SOUTH` is a constructed second scene (3392.5 km, `lat_ts` −40) | 18 m/px | body by datum; equivalence across drivers; per-scene radii (`SAME_BODY` between scenes); distortion TIP away from 15° |
| `MARS_EQC_PDS3` | GDAL's PDS3 form on the Mars 2000 sphere, `standard_parallel_1=0` (constructed from the CRISM form) | — | equivalence with `IAU_2015:49910` despite the names |
| `CTX_DEM` | ASP: `+proj=eqc +lat_ts=18.4 +lat_0=0 +lon_0=77.5 +x_0=0 +y_0=0 +R=3396190 +units=m +no_defs` | 20 m/px, heights above sphere | `isGoodEnough`; slope spacing |
| `HIRISE_POLAR` | `+proj=stere +lat_0=90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=3376200 +units=m +no_defs` | 1 m/px at 85°N | polar; body by radius; body-model term |
| `LRO_WAC` | `+proj=eqc +lat_ts=0 +lon_0=0 +R=1737400 +units=m +no_defs` (and a south polar stereographic variant) | 100 m/px | Moon; no body-model term |
| `WGS84_MARS` | `EPSG:4326` near Gale crater (`GALE_TRANSFORM`, 1/256°), written by `writeWgs84` with a sibling PDS3 label `TARGET_NAME = "MARS"` (`MARS_LABEL`) | 1/256° | `earth-crs-on-body` and its automatic fix; cross-body guards |
| `MOLA_MEGDR` | `+proj=longlat +R=3396000 +no_defs`, lon 0–360 | 1/128° | 0–360 domain; areoid declaration (Phase 3) |
| `VESTA_IAU` | `IAU_2015:200000400` | — | identification regression |

---

## 7. Decisions to check

Each of these is **(our call)**.

1. **A new package `varda.geo`.** We didn't extend `catalogs/bodies.py`.
   `catalogs.bodies` keeps `BodyChoice` and `toLongitudeLatitude`, which
   call `varda.geo`. `RADII_M`, `bodyOf`, `geographicCrs` and `bodyName` are
   removed after their callers move.
2. **Body radii come from PROJ's `IAU_2015` database**, not a hand-typed
   table. Unknown radii give **no body** instead of "Earth".
3. **Degrees on a body mean that body's IAU 2015 sphere** (`IAU_2015:{naif}00`;
   3396.19 km for Mars). We dropped the mean-radius sphere. When an image is
   involved, degrees mean that image's own `geodetic_crs`, as in the
   vector-import branch. Exception: the Find Data map still draws on the
   IAU mean-radius sphere (`find_data/projection.py`); it is display only,
   and footprints and images keep their longitude and latitude.
4. **Measurements use the CRS's own ellipsoid or sphere**, with the IAU 2015
   reference ellipsoid's result as the reported "body model" difference.
   The alternative was to measure on the IAU ellipsoid by default.
5. **Distortion thresholds:** TIP at 1% scale error or 1° angular
   distortion; strong wording at 10%/10°.
6. **Only one automatic CRS fix in Phase 1:** an Earth *geographic* CRS on
   an image whose label names another body. Everything else is offered with
   an Apply button. Phase 2 adds automatic CRSs from complete label
   projection groups.
7. **Fixes are stored as a `crs` metadata override** (WKT) on the image,
   persisted in sessions and recorded in provenance. The file itself is
   never edited.
8. **UI:** a badge plus tooltip in the image list, a "Coordinate system"
   group in Image Properties, and one status-bar message per problem per
   session. No modal dialogs.
9. **Readout:** shows the image's own longitude domain and direction and
   names the convention, rather than always converting to −180..180 east.
10. **Recommended projections:**
    - polar stereographic at ≥ 65° (HiRISE's rule): true scale at the pole
      for mosaics, at the scene center otherwise;
    - equirectangular with `lat_ts` at the scene center for mosaics;
    - keep the native CRS for Terrain and measurement when it's good
      enough, otherwise transverse Mercator while every point is within
      0.14·R of the central meridian, and Lambert azimuthal equal-area
      beyond. (Phase 2 reads 0.14·R as a half-width, which matches the 1%
      threshold; the first draft said "480 km" wide on Mars, which is
      0.14·R as a full width.)
11. **Mosaic target defaults** to keeping a shared input CRS when it's good
    enough, rather than always the recommendation.
12. **Vertical datum is declared, not converted**, until Phase 4. DEMs of
    different datums aren't combined until then.
13. **Thread safety:** `varda.geo` functions take CRS/WKT and lock
    internally, so they're safe from workers. Existing GUI-thread
    conventions stay valid but aren't required for new code.
14. **Points keep one collection CRS.** Points from other images are
    converted into it on add (body-checked). We didn't store per-point CRSs
    in the export.

Added for Phase 2:

15. **Rasters keep planetocentric positions between surfaces of one body**
    (§4.3.1). Every GDAL warp gets the `warpOperation` pipeline, and
    `onGridOf`'s masks and DEMs are fixed with it, which is a change in
    their results. The alternative was PROJ's ballpark, about 20 km off on
    Mars between a sphere and the ellipsoid.
16. **Overlays across projections are display only:** nearest neighbor,
    cached (512 MiB), refused above 100 Mpx, never written or analyzed.
    Pixel-for-pixel tools still need one grid.
17. **Reproject Image's defaults:** "Recommended projection" for
    Measurement; resolution = the source's area-preserving ground pixel
    size at its center; pixels aligned to whole multiples (`-tap`); float32
    output with NaN for no data; a separate quality image (source pixel
    distance, valid) rather than extra bands, so spectral cubes keep their
    wavelengths.
18. **Label CRS rules** (§4.8): automatic only when the CRS comes from the
    label alone with no assumed convention. The local-radius sphere for a
    planetocentric equirectangular on a flattened body (ISIS's convention)
    and GDAL's sphere choices count as assumptions, so those are offered.
    A label never overrides a CRS GDAL built: a disagreement is offered,
    and hints from Varda provenance never trigger it.
19. **Images can carry a `transform` override** (Affine's six numbers),
    like `crs`, for labels whose transform GDAL couldn't build.
20. **Pixel areas** are exact per row for row-only layouts and use the
    areal scale otherwise. Selected-pixel areas (zonal) are reported
    separately from the polygon's **Area on the body**.
21. **The recommender** takes the reference CRS (not a `Body`), so unknown
    bodies get a recommendation on their own surface. TERRAIN and
    MEASUREMENT never keep a geographic CRS. Centers round half away from
    zero (1° for mosaics, 0.1° for polar `lat_ts`, 0.01° for transverse
    Mercator and Lambert azimuthal centers).

---

## References

- Archinal, B. A., et al. (2018). Report of the IAU Working Group on
  Cartographic Coordinates and Rotational Elements: 2015. *Celestial
  Mechanics and Dynamical Astronomy*, 130, 22.
  https://doi.org/10.1007/s10569-017-9805-5
- Seidelmann, P. K., et al. (2002). Report of the IAU/IAG Working Group on
  cartographic coordinates and rotational elements of the planets and
  satellites: 2000. *Celestial Mechanics and Dynamical Astronomy*, 82,
  83–111. (Planetocentric vs planetographic conventions.)
- Hare, T. M., et al. (2018). Interoperability in planetary research for
  geospatial data analysis. *Planetary and Space Science*, 150, 36–42.
  (The IAU codes for planetary CRSs in OGC services, the basis of PROJ's
  `IAU_2015` authority.)
- Snyder, J. P. (1987). *Map Projections: A Working Manual*. USGS
  Professional Paper 1395. Equirectangular, pp. 90–91; Stereographic,
  ch. 21; Transverse Mercator, ch. 8; distortion, ch. 4; geocentric
  latitude, eq. 3-28.
- Karney, C. F. F. (2013). Algorithms for geodesics. *Journal of Geodesy*,
  87, 43–55. https://doi.org/10.1007/s00190-012-0578-z
- PROJ contributors. PROJ documentation: *Factors* (`proj_factors`), the
  `IAU_2015` authority, and celestial-body checks in
  `proj_create_operations`. https://proj.org
- GDAL documentation: PDS, PDS4 and ISIS3 drivers (how labels become
  CRSs). https://gdal.org/drivers/raster/
- Smith, D. E., et al. (2001). Mars Orbiter Laser Altimeter: Experiment
  summary after the first year of global mapping of Mars. *Journal of
  Geophysical Research*, 106(E10), 23689–23722. (The MOLA areoid and
  reference radius.)
- McEwen, A. S., et al. (2010). The High Resolution Imaging Science
  Experiment (HiRISE) during MRO's Primary Science Phase. *Icarus*, 205,
  2–37; and the HiRISE RDR Software Interface Specification
  (equirectangular below 65°, polar stereographic above).
- Schowengerdt, R. A. (2007). *Remote Sensing: Models and Methods for
  Image Processing* (3rd ed.), ch. 7 (resampling and its effect on
  resolution).
- Keys, R. G. (1981). Cubic convolution interpolation for digital image
  processing. *IEEE Transactions on Acoustics, Speech, and Signal
  Processing*, 29(6), 1153–1160. (GDAL's cubic kernel, a = −0.5.)
- PDS Standards Reference (PDS3), *Cartographic Standards* and the
  `IMAGE_MAP_PROJECTION` object (sample/line ↔ x/y, projection offsets).
- USGS ISIS documentation: the `Mapping` group and map projections
  (Equirectangular's local radius at the center latitude).
