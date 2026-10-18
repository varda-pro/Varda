# Coordinate Systems and Map Projections — Design

**Date:** 2026-10-18
**Status:** Design. Phase 1 plan: `docs/superpowers/plans/2026-10-18-crs-and-projections-phase-1.md`.

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
| Common grid | `common/grid.py:34-39` (`sameGrid`: `a.crs == b.crs`), `75-86` (`pixelSizeText`), `139-159` (`placementProblem` via `transform_bounds`), `271-307` (`onGridOf`: GDAL warper), `310-342` (`resamplingText`/`resamplingRecord`) | Masks and DEMs from other grids (commit ed58677): resampled onto the target grid, described, and recorded in provenance. | Good pattern, to be generalized. `placementProblem` misses overlap when two geographic images use 0–360 and −180..180 with the same CRS, because `_footprintIn` returns raw bounds. There's no body check; a cross-body pair fails inside `transform_bounds` and is reported as "can't be reprojected". |
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
| **No CRS** (a transform but no CRS; a label with a map projection group GDAL couldn't turn into a CRS; ENVI `map info` alone) | `transform != identity and crs is None`. The label has `MAP_PROJECTION` (PDS3), `Mapping` (ISIS) or `cart:` (PDS4). | Phase 1: flag it ("Has map coordinates but no coordinate system; Varda treats it as unreferenced"). Phase 2: build the CRS from the label's projection group when every parameter is present (unambiguous, so auto-fix and record it). Otherwise offer "Set coordinate system…". |
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
  bodies.py        Body table (IAU 2015 through PROJ), body identification       (Phase 1)
  describe.py      CrsDescription: a human-readable one-liner and fields         (Phase 1)
  equivalence.py   sameCrs, compatibility (same / equivalent / other projection
                   / other body / unknown)                                        (Phase 1)
  distortion.py    scale factors and angular distortion at points; worst over
                   an extent; thresholds                                          (Phase 1)
  measure.py       geodesic lengths, areas and per-pixel ground spacing for
                   any CRS; Measurement record                                    (Phase 1)
  labels.py        PDS3/PDS4/ISIS label hints (target, latitude type,
                   longitude direction/domain, projection group)                  (Phase 1: hints;
                                                                                   Phase 2: CRS from group)
  health.py        checkCrs: issues and the automatic fix                         (Phase 1)
  recommend.py     recommendProjection, isGoodEnough                              (Phase 3)
  reproject.py     reproject an image to a CRS/grid; common grid of several      (Phase 2)
```

**Thread-safety rule (our call).** Every public `varda.geo` function takes
`pyproj.CRS | str` (WKT, PROJ or authority strings), builds what it needs
inside `PROJ_LOCK`, and returns plain values (floats, numpy arrays, attrs
records of plain values). No `varda.geo` result holds a pyproj object
except `Body.sphereCrs()` / `Body.geod()`, which build a fresh object on
each call. So `varda.geo` is safe from workers. The "keep pyproj on the GUI
thread" and "pass WKT" conventions remain valid for existing code. New code
uses `varda.geo` and needs neither.

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
    westPositiveOgraphic: bool  # IAU convention for planetographic longitude

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
  - the CRISM MTRDR WKT → Mars (evidence "datum D_MARS");
  - the HiRISE 3376.2 km sphere → Mars;
  - LRO WAC → Moon;
  - EPSG:4326 → Earth;
  - `IAU_2015:200000400` (Vesta, 255 km) → Vesta (regression for today's "Earth");
  - a 3000 km sphere → None;
  - the WGS84 GeoTIFF with `labelTarget="MARS"` → Mars, with evidence from the label.
- `sameCrs`:
  - the GDAL `D_MARS` WKT vs `IAU_2015:49910` → True (today False);
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
             hints: LabelHints = LabelHints.empty(), rawCrsText: str | None = None) -> CrsHealth
```

**Automatic fixes (our call).** Only one case is fixed without asking in
Phase 1: an Earth *geographic* CRS on an image whose label names another
body. The fix is that body's `sphereCrs()` with the same axis order and
units. Every other fix is offered with an "Apply" button and applied only
when the user clicks it.

Phase 2 adds a second automatic case: a missing CRS built from a complete
label projection group. Once Phase 2 builds the CRS from the group, "GDAL
built a different projection from the label" (`label-crs-mismatch`) also
becomes fixable. It's offered, not automatic, because GDAL may be right.

**Applying a fix.**

- `VardaRaster` gains a CRS override. `EDITABLE_SINGLE` += `"crs"`, holding
  WKT.
- `VardaRaster.crs` returns the override when set, and the cached
  transformer is cleared.
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
system). Phase 2 adds building a CRS from label projection groups.

### 4.3 Reproject Image… (Processing analysis)

**What and why.** It's the step the docs keep telling users to do
elsewhere. It's also the engine behind the multi-image agreement features
(§4.4) and the Mosaic/Terrain projections (§4.6).

**Parameters.**

| Parameter | Meaning | Units / range | Default |
|---|---|---|---|
| Target | Recommended projection; Another image's grid; Coordinate system | — | Recommended |
| Purpose (Recommended) | Measurement; Mosaic; Terrain | — | Measurement |
| Image (Another image's grid) | an `ImageParameter`: same body only (`compatibility` filter, `ImageChoices` notes) | — | — |
| CRS (Coordinate system) | EPSG / IAU_2015 / WKT / PROJ string; validated, body-checked | — | the recommendation's |
| Resampling | Nearest; Bilinear; Cubic; Average; Mode | — | Nearest for class maps and masks (integer images with `classes` metadata or 1-band 0/1); Bilinear otherwise |
| Resolution | ground meters per pixel | > 0 | the source's pixel size at its center in the new CRS, so area is preserved (`calculate_default_transform`) |
| Extent | Whole image; Another image's extent | — | Whole image |

**Output.** A new image in the target CRS. Optional companion bands:

- "Source pixel distance": distance in source pixels from each output
  pixel's center to the nearest source pixel center. It's the resampling's
  interpolation distance.
- "Valid" (0/1).

Metadata `reprojection` records: source and target CRS (WKT and
description), method, resolution, extent, GDAL version, and the maximum and
mean scale change.

**Uncertainty (docs).**

- Bilinear and cubic resampling smooth the data. The effective resolution
  is about 1.5–2× the output pixel when output ≈ input resolution (Schowengerdt,
  2007, ch. 7).
- Cubic can overshoot near edges and change spectra by a few percent at
  sharp boundaries.
- Nearest preserves values but shifts positions by up to ½ pixel.
- Average is the correct choice when coarsening.
- Spectral data: resampling mixes neighboring spectra. Use nearest for
  band-ratio work at pixel scale.
- No-data edges: GDAL's warper leaves NaN outside the source. The edge
  pixels' kernels include fewer samples, and the Valid band marks them.
- Position error: PROJ's transformation is exact between CRSs of one body
  (no datum shift), so position error is the source georeferencing's.
- The docs show how to compute the expected change from the scale factors
  (§4.5).

**Provenance.** Through the analysis runner (`recordProvenance`), plus the
`reprojection` metadata, which is added to `KEPT_METADATA_KEYS`.

**Tests.**

- Round trip: Mars eqc → polar stereographic → eqc on a synthetic checker
  pattern. Positions agree within ½ px (nearest) and values within
  tolerance (bilinear).
- Body guard: an Earth target for a Mars image is refused with a message.
- Defaults: Nearest for a class map.
- Equivalent CRS: the image is returned unchanged with a note, not
  resampled.
- The memory estimate passes `test_memory_estimates.py`.

**Docs.** `docs/user-guide/reprojecting.md`, linked from the README and
`image-processing.md`.

**Phase.** 2.

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
| ROI transfer (`applyToImage`) | no body check | `canTransform` guard; skip `to_crs` when `sameCrs` | 1 |
| Points (`point_collection.py`) | first image's CRS for all | store each point's own CRS and convert to the collection's on add (body-checked); export notes it | 1 |
| Mosaic (`model.py`) | `str()` comparison, refuses | `sameCrs`; SAME_BODY → reproject inputs to the target CRS (§4.6) | 1 (sameCrs), 3 (reprojection) |
| Terrain drapes/vectors | reprojects drapes unrecorded; mean-radius sphere for degrees | record drape reprojection in exports; degrees mean the DEM's `geodetic_crs` (as vector import) | 3 |
| Raster overlays (`raster_overlay.py`) | same grid only | same body: reprojected on the fly | 4 |
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

### 4.5 Measurements and distortion

**What and why.** Every measurement is made on the body, whatever the
image's projection, and states its method and expected error. The
projection's distortion is reported where it affects what the user sees or
measures by other means (planar map-unit ROI columns, shapes, slopes).

**Method (our call).**

- **Positions.** Map coordinates are converted to longitude and latitude
  in the CRS's own geodetic CRS (`crs.geodetic_crs`), under `PROJ_LOCK`.
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
| ROI area/perimeter | planar map units (degrees² in geographic CRSs) | new columns **Area on the body (km²)** and **Perimeter on the body (km)**, geodesic; "map units" columns kept, labeled planar, with a tooltip giving the distortion at the ROI | 1 |
| Pixel area for zonal sums | — | `pixelAreas(crs, transform, rows, cols)` (geodesic per pixel row for cylindrical projections, through the areal scale otherwise) for "Area of selected pixels (km²)" | 2 |
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
```

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
mesh's shape, slopes.

**`recommendProjection(body, boundsLonLat, purpose)` (our call):**

1. **|center lat| ≥ 65° or the extent contains a pole** → polar
   stereographic on the body's sphere, `lon_0` = 0, true scale:
   - MOSAIC: at the pole (`lat_ts` = ±90; the HiRISE/PDS convention, so
     products match).
   - TERRAIN/MEASUREMENT: at the scene's center latitude (so k = 1 where
     the data are).

   The 65° threshold matches HiRISE's projection rule (McEwen et al.,
   2010; HiRISE RDR SIS).
2. **MOSAIC otherwise** → equirectangular, `lat_ts` = the scene center
   latitude rounded to 1°, `lon_0` = the center longitude rounded to 1°. This
   is the convention CTX/HiRISE/ISIS `cam2map` products use, so most
   inputs need no resampling. If every input already shares one same-body
   CRS whose worst distortion over the union is below the thresholds, keep
   it.
3. **TERRAIN and MEASUREMENT otherwise:**
   - Keep the DEM's or image's own CRS if `isGoodEnough` (worst scale error
     ≤ 1% and ω ≤ 1° over the extent).
   - Otherwise use a conformal local projection on the body's sphere:
     transverse Mercator centered on the scene for extents up to
     0.14·R east–west (scale error ≤ 1%; Snyder eq. 8-1); 480 km on Mars
     and 245 km on the Moon.
   - Larger extents: Lambert azimuthal equal-area centered on the scene
     (areas exact, shapes stated), with a notice.
4. Always on the body's IAU sphere unless every input uses the IAU
   ellipsoid, in which case keep it.

```python
# varda/geo/recommend.py
class Purpose(LabeledEnum):
    MEASUREMENT = "Measurement"
    MOSAIC = "Mosaic"
    TERRAIN = "Terrain"

@frozen
class Recommendation:
    wkt: str
    description: CrsDescription
    reason: str             # "Polar stereographic: the scene reaches 82°N"
    keptExisting: bool

def isGoodEnough(crs, boundsLonLat: tuple[float, float, float, float]) -> tuple[bool, str]
def recommendProjection(body: Body, boundsLonLat, purpose: Purpose,
                        existing: Sequence[CRS | str] = ()) -> Recommendation
```

**Mosaic.**

- The target CRS comes from `recommendProjection(..., MOSAIC, existing=inputs)`.
  The Grid step shows it with a "Change…" button (CRS entry, or "same as
  input N").
- Inputs in other CRSs of the same body are reprojected on the fly for
  display, and with the chosen resampling at render or export.
- Tie points are stored in each input's own CRS and converted (the
  mosaic's 3×3 correction is defined in the target CRS).
- The recipe records the target CRS and each input's reprojection.
- Docs: `mosaicking.md`, "Map projection".

**Terrain.**

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

- Recommendation for each fixture:
  - CRISM MTRDR at 18°N → equirectangular, `lat_ts` 18 (MOSAIC);
  - HiRISE at 85°N → polar stereographic;
  - LRO WAC equatorial → equirectangular;
  - a 1000 km-wide Mars TERRAIN extent → Lambert azimuthal equal-area.
- `isGoodEnough` is true for the CTX DEM's own CRS (`lat_ts` at its
  center).
- The Terrain mesh of a `lat_ts=0` DEM at 60°N has the correct east–west
  size (±0.5%).
- A mosaic of eqc and polar-stereographic Mars inputs renders with both in
  place (synthetic pattern, ½ px).

**Phase.** 3. Areoid conversion is Phase 4.

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
| **2. Reproject Image and label-built CRSs** | `geo.reproject` and the Reproject Image analysis with its guide; CRS from PDS3/ISIS/PDS4 projection groups (an automatic fix when complete); pixel-area zonal sums. | Gives users the tool the docs point to. Builds the engine for Phase 3. |
| **3. Mosaic and Terrain projections** | `geo.recommend`; Mosaic target CRS with on-the-fly reprojection of inputs; Terrain mesh in a local projection; vertical-datum declaration; scale-correct Terrain analysis and Shadow Mask (geographic allowed); profile 3D path length over a DEM. | Depends on Phases 1–2. Touches the most complex workspaces. |
| **4. Everywhere else** | Areoid download and conversion (Optional Features); raster overlays across CRSs; workspace status-line readout; a project-level "working CRS" for new workspaces; clean up `GeoReferencer`, `image_utils` and the old ROI paths. | Lower value per effort, or depends on data downloads. |

---

## 6. Test fixtures (real-style CRSs)

`src/varda/geo/_tests/fixtures.py` builds small `ArrayDataSource` images
(no data files), each with a CRS exactly as the producing software writes
it.

| Name | CRS (as written by) | Grid | Purpose |
|---|---|---|---|
| `CRISM_MTRDR` | GDAL PDS3 WKT: `PROJCS["EQUIRECTANGULAR MARS", GEOGCS["GCS_MARS", DATUM["D_MARS", SPHEROID["MARS",3396190,0]]…], PROJECTION["Equirectangular"], standard_parallel_1=0, central_meridian=0…]` | 18 m/px, centered at 18.4°N 77.5°E (Jezero) | body by datum; equivalence with `IAU_2015:49910`; distortion TIP (k = 1.054 at 18.4°) |
| `CTX_DEM` | ASP: `+proj=eqc +lat_ts=18.4 +lat_0=0 +lon_0=77.5 +x_0=0 +y_0=0 +R=3396190 +units=m +no_defs` | 20 m/px, heights above sphere | `isGoodEnough`; slope spacing |
| `HIRISE_POLAR` | `+proj=stere +lat_0=90 +lon_0=0 +k=1 +x_0=0 +y_0=0 +R=3376200 +units=m +no_defs` | 1 m/px at 85°N | polar; body by radius; body-model term |
| `LRO_WAC` | `+proj=eqc +lat_ts=0 +lon_0=0 +R=1737400 +units=m +no_defs` (and a south polar stereographic variant) | 100 m/px | Moon; no body-model term |
| `WGS84_MARS` | `EPSG:4326`, lon 77–78, lat 18–19, plus a PDS3 label with `TARGET_NAME = MARS` | 0.0003° | `earth-crs-on-body` and its automatic fix; cross-body guards |
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
   vector-import branch.
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
      enough, otherwise transverse Mercator up to 0.14·R and Lambert
      azimuthal equal-area beyond.
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
