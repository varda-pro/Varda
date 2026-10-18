# Coordinate Systems and Map Projections — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks within a wave touch disjoint files (apart from the hotspots below) and run in parallel, each in its own git worktree.

**Goal:** Reproject Image (a Processing analysis with a projection recommender and a scientific guide), overlays across projections in the image workspaces, CRSs built from planetary label projection groups, and pixel-area zonal sums. Rasters keep their planetocentric positions between a sphere and an ellipsoid everywhere Varda warps them.

**Architecture:** The new logic is Qt-free:

- `varda.geo.recommend`: footprints and the projection recommender.
- `varda.geo.transform.warpOperation`: a PROJ pipeline for GDAL's warper that keeps planetocentric positions.
- `varda.geo.reproject`: the output grid, the warp and the quality bands.
- `varda.geo.label_crs`: a CRS (and transform) from a label's projection group.
- `varda.geo.measure.pixelAreas`.
- `varda.common.display_grid`: display reprojection and its cache.

GUI wiring comes in separate tasks: the overlay bar, the ROI column dialog, and Image Properties. The analysis (`varda.analysis.reproject`) is Qt-free like every analysis.

**Tech Stack:** Python 3.13, pyproj 3.7.2 / PROJ 9.5.1 (the `IAU_2015` authority), rasterio 1.4.3 / GDAL 3.9.3 (`rasterio.warp.reproject` with the `COORDINATE_OPERATION` option), shapely, numpy, attrs, PyQt6, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-18-crs-and-projections-design.md`. Below, "§" refers to it: §4.3 and §4.3.1 (Reproject Image, rasters between surfaces), §4.4.1 (overlays), §4.5 (pixel areas), §4.6 (recommender), §4.8 (label CRSs), §7 decisions 15–21.

## Global Constraints

- **CLAUDE.md applies:**
  - Before each task, run `git status` and confirm the branch is current with `origin/main`.
  - Use uv. Run `uv run ruff format` and `uv run ruff check` on touched files only.
  - `uv run ty check` must add no new diagnostics. Compare against the base with a WIP commit, never a bare `git stash`.
  - `uv run lint-imports` must stay KEPT. **Never grow `ignore_imports`.** Adding a new Qt-free module to `source_modules` is fine (Task 6).
  - Run the full `uv run pytest` before the PR.
- **Style:**
  - camelCase (the repo's convention, also in `varda.geo`).
  - Type hints; never `Any`.
  - No lazy imports.
  - American English.
  - attrs `@frozen` for records.
- **Phase 1 lessons (each was a real bug):**
  - Move coordinates between CRSs only with `geo.transform` (`transformPoints`, `planetocentricLonLat`, `eastLonLat`, `geodeticLonLat`). pyproj's `always_xy` leaves the `IAU_2015` ellipsoid codes latitude first.
  - Between a sphere and an ellipsoid of one body, positions keep their planetocentric longitude and latitude. For points use `transformPoints`; for rasters use `warpOptions` (Task 2). Never let GDAL/PROJ's ballpark operation place a raster.
  - pyproj work (CRS parsing, transformers, pipelines, WKT export) holds `PROJ_LOCK`. GDAL warps (`rasterio.warp.reproject`) run **outside** the lock, so a long warp never blocks the GUI readout. Background work goes through `runInBackground`, which keeps each pool thread's PROJ context alive (`keepProjContext`). Never make a QRunnable or QThread by hand.
  - CRISM map products each use their own scene's local radius (`CRISM_MTRDR_WKT` is 3394839.81 m). Never assume one Mars radius from one file, and never compare radii to a constant.
  - An unknown body is `None`, never Earth.
  - Functions that GUI code calls for a measurement or a check never raise. They return NaN, None or a record with the reason. Only analyses' `run` raises `ValueError` with a user-facing message, which the runner shows.
  - Qt connections use bound methods or `connectWhileAlive`, never lambdas that capture a widget that can be deleted first.
- **Scientific documentation, in the same task as the change.** Every number states its method, its surface and its error terms. Cite as in the spec's References: Snyder (1987), Karney (2013), Archinal et al. (2018), Schowengerdt (2007), Keys (1981), the PDS3 Standards Reference, the ISIS docs, and the PROJ and GDAL docs.
- **CHANGELOG.md:** bullets go under the existing `## Unreleased` → `### Added` / `### Fixed`. Never add headings.
- **Laptop layout:** changed widgets pass `common/layout_audit.assertNothingClipped` at 1280×800.
- **Fixtures:** use `src/varda/geo/_tests/fixtures.py` (`makeImage`, `CRISM_MTRDR_WKT`, `MARS_EQC_PDS3_WKT`, `CTX_DEM_PROJ`, `HIRISE_POLAR_PROJ`, `LRO_WAC_PROJ`, `LRO_WAC_SOUTH_POLAR_PROJ`, `MOLA_MEGDR_PROJ`, `WGS84`, `writeWgs84`). Write label text and GeoTIFFs to `tmp_path`. Add no binary data files.
- **Commits:** end every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Waves

Each wave's tasks start from the merge of every earlier wave. Inside a wave, the tasks touch disjoint files, apart from the hotspots below.

| Task | Name | Wave | Depends on |
|---|---|---|---|
| 1 | Projection recommender (`geo.recommend`) | 1 | — |
| 2 | Planetocentric warps (`warpOperation`; `onGridOf` fix) | 1 | — |
| 3 | CRSs from label projection groups (`geo.label_crs`) | 1 | — |
| 4 | Pixel areas (`geo.measure`, `geo.distortion`) | 1 | — |
| 5 | Transform overrides on images | 1 | — |
| 6 | Display reprojection core (`common.display_grid`) | 1 | — |
| 7 | Reprojection engine (`geo.reproject`) | 2 | 2 |
| 8 | Label CRSs in the health check | 2 | 3 |
| 9 | Overlay bar across projections | 2 | 6 |
| 10 | ROI pixel-area columns | 2 | 4 |
| 11 | Class-map areas (core) | 2 | 4 |
| 12 | Reproject Image analysis and guide | 3 | 1, 7 (8 for `health.py`) |
| 13 | Label CRSs at load, Apply and Undo | 3 | 5, 8 |
| 14 | Class areas in Image Properties | 4 | 11, 13 (shares `image_properties_dialog.py`) |
| 15 | Phase 2 verification | 5 | all |

**Hotspots** (files more than one task in a wave edits). Each task adds only its own section or bullet, and merges keep both sides. Merge a wave's branches in task order, rebasing each on the previous one.

| File | Tasks | Rule |
|---|---|---|
| `CHANGELOG.md` | all but 15 | one bullet per task under the existing `### Added` or `### Fixed` |
| `docs/user-guide/coordinate-systems.md` | 1, 2, 4, 9 (one line), 8, 13 | each task adds its own `###` section, named in its Docs step. Task 13 extends Task 8's section. |
| `docs/user-guide/image-processing.md` | 2, 9, 12 | Task 2 edits "Images on other grids"; Task 9 edits the "Same grid only" sentence; Task 12 adds a bullet to "Geometry" |
| `docs/user-guide/viewing-and-measuring.md` | 9, 10 | Task 9 adds `## Overlays` after "Library overlays"; Task 10 adds rows to "Calculated columns" |
| `docs/user-guide/README.md`, `analysis/registry.py`, `common/provenance.py` | 12 only | — |
| `pyproject.toml` | 6 only | one line in the Qt-free contract's `source_modules` |

## Review Focus

1. **Sphere ↔ ellipsoid rasters keep planetocentric positions.** A marker at 45°N on an `IAU_2015:49910` (sphere) image lands within ½ px of `transformPoints`' position on an `IAU_2015:49912` (ellipsoid) grid, in `onGridOf` (Task 2), Reproject Image (Task 7) and display overlays (Task 9). Today it lands 40 rows (20 km) off.
2. **Class maps and masks stay discrete.** A class map reprojected with its defaults keeps only its original class values, its `Class` band name and its `classes`/`classColors`. A display overlay never blends classes (Tasks 6, 12).
3. **0–360 and the antimeridian.** A `MOLA_MEGDR_PROJ` tile at 170–190°E reprojected to `IAU_2015:49910` (lon_0 0) is refused, naming a centered `lon_0`, rather than making a world-wide grid. The same tile overlays a −180..180 base by display reprojection (Tasks 6, 7).
4. **Labels never silently override GDAL.** A label that disagrees with GDAL's CRS is only offered. Hints from Varda provenance never trigger a label issue. A malformed group (`A_AXIS_RADIUS = "N/A"`, an unknown projection, an empty group) never raises (Tasks 3, 8).
5. **Large images fail early and say why.** Reproject Image refuses above `MAX_OUTPUT_PIXELS` before allocating, and its memory estimate bounds a 10× upsample. An overlay over a base above `DISPLAY_MAX_PIXELS` is left out with its size. A stale overlay result (the user picked another meanwhile) is dropped (Tasks 6, 7, 9, 12).

---

### Task 1: Projection recommender

**Wave:** 1 · **Touches:** `src/varda/geo/recommend.py` (new), `src/varda/geo/_tests/test_recommend.py` (new), `coordinate-systems.md` (new `### Recommended projections` section under "Combining images"), CHANGELOG

**Interfaces** (spec §4.6, exactly): `Purpose(LabeledEnum)`, `Footprint`, `Recommendation`, `footprintLonLat`, `isGoodEnough` and `recommendProjection`.
- Consumes: `transform.planetocentricLonLat`, `distortion.distortionAt`, `SCALE_TIP`, `ANGLE_TIP_DEG`, `bodies.identifyBody`, `Body.sphereCrs()`, `Body.referenceCrs()`, `describe.describeCrs`, `equivalence.sameCrs`.
- Produces, for Task 12: `footprintLonLat(crs, transform, width, height) -> Footprint`; `recommendProjection(crs, footprint, purpose, existing=()) -> Recommendation`, whose `wkt` is a PROJ string's WKT.
- `footprintLonLat`:
  - Densify the four edges (21 points each) through `planetocentricLonLat` and drop non-finite points.
  - Unwrap longitudes around the first point (`np.unwrap` in degrees), so `west < east`.
  - `center` = the midpoint.
  - `pole` = "north"/"south" when the pole's map point (`transformPoints(eastLonLat(crs), crs, [0], [±90])`) falls inside the image's pixel bounds. Then `north` (or `south`) = ±90, and west/east = −180/180.
- **Rounding** is half away from zero: `math.copysign(math.floor(abs(x) / step + 0.5) * step, x)`.
- The **CRS strings** use `+R=` (sphere) or `+a= +b=` (ellipsoid), `+units=m +no_defs`. TM gets `+k=1`. Polar uses `+k=1` for a pole true scale, or `+lat_ts` otherwise.

- [ ] **Step 1: Write failing tests** in `test_recommend.py`, using `makeImage` and `footprintLonLat`:
  - `test_mosaic_mid_latitude_is_equirectangular_rounded`: a `CTX_DEM_PROJ` image centered at (77.5, 18.4) with MOSAIC and `existing=()` → `+proj=eqc`, `lat_ts` 18, `lon_0` 78 (read back with `CRS(wkt).to_dict()`).
  - `test_polar_mosaic_true_scale_at_pole` / `test_polar_terrain_true_scale_at_center`: `HIRISE_POLAR_PROJ` at 85°N → stere `lat_0` 90 with `k` 1 (MOSAIC), or `lat_ts` 85.0 (TERRAIN).
  - `test_footprint_contains_south_pole`: an `LRO_WAC_SOUTH_POLAR_PROJ` image centered on the pole → `pole == "south"`, `south == -90`, and the recommendation is polar south.
  - `test_existing_good_crs_is_kept`: CTX DEM, TERRAIN → `keptExisting` and `sameCrs(wkt, CTX_DEM_PROJ)`.
  - `test_distorted_crs_gets_transverse_mercator`: a 300 km-wide `IAU_2015:49910` image at 30°N, TERRAIN → `+proj=tmerc`, and the reason contains "15.5%".
  - `test_wide_scene_gets_laea`: 1000 km wide at 0°N on Mars → `+proj=laea`.
  - `test_geographic_never_kept_for_terrain`: `MOLA_MEGDR_PROJ` → not kept.
  - `test_unknown_body_uses_its_own_sphere`: `+proj=eqc +R=3000000` → the recommendation's `R` is 3000000.
  - `test_all_inputs_on_ellipsoid_keep_ellipsoid`: `existing=["IAU_2015:49912"] * 2` → `+a=3396190 +b=3376200`.
  - `test_rounds_half_away_from_zero`: center lon 77.5 → 78, and −77.5 → −78.
- [ ] **Step 2: Run** `uv run pytest src/varda/geo/_tests/test_recommend.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `recommend.py` per spec §4.6 rules 1–4. The TM test is `max(arcsin(cos φ · |sin Δλ|)) ≤ 0.14` over the densified footprint, with Δλ from the center.
- [ ] **Step 4: Run** → PASS. Run `uv run lint-imports` → KEPT.
- [ ] **Step 5: Docs.** Add `### Recommended projections` to `coordinate-systems.md`: the four rules with their reasons, the 65° HiRISE rule (McEwen et al., 2010), the TM half-width formula (Snyder, 1987, eq. 8-1), and the rounding. CHANGELOG: none (Task 12 announces it).
- [ ] **Step 6: Commit** `git commit -m "geo: recommend a projection for a scene (polar stereographic, equirectangular, transverse Mercator, Lambert azimuthal)"`.

### Task 2: Planetocentric warps, and `onGridOf` fixed with them

**Wave:** 1 · **Touches:** `src/varda/geo/transform.py`, `src/varda/geo/_tests/test_warp_operation.py` (new), `src/varda/common/grid.py`, `src/varda/common/_tests/test_grid.py`, `coordinate-systems.md` (new `### Rasters between surfaces of one body` after "Moving points between surfaces of one body"), `image-processing.md` ("Images on other grids"), CHANGELOG

**Interfaces** (spec §4.3.1):
- `warpOperation(src: CRS | str, dst: CRS | str) -> str | None` in `geo/transform.py`:
  - Returns None when `sameCrs`, or when `_route(...).direct` is not None.
  - Otherwise returns one flat `+proj=pipeline` string. Its steps:
    1. src's `_Side.plain` steps (split `Transformer.definition` on `" step "`, minus its `proj=pipeline` head);
    2. `+proj=unitconvert +xy_in=deg +xy_out=rad`;
    3. `+proj=geoc +a= +b=` on src's ellipsoid, unless `src.centricNumbers`;
    4. `+inv +proj=geoc` on dst's ellipsoid, unless `dst.centricNumbers`;
    5. `+proj=unitconvert +xy_in=rad +xy_out=deg`;
    6. the steps of `Transformer.from_crs(_plain(dst), dst)`.
  - On a sphere the geoc steps are no-ops and may be kept.
  - Raises `ValueError(canTransform reason)`. Holds `PROJ_LOCK`, and is cached like `_route`.
- `warpOptions(src, dst) -> dict[str, str]`: `{"COORDINATE_OPERATION": op}`, or `{}` when `warpOperation` is None.
- `common/grid.onGridOf` passes `**warpOptions(source.crs, target.crs)` to `reproject` (still outside the lock). `resamplingRecord` adds `"coordinateOperation": op or "PROJ"`.

- [ ] **Step 1: Write failing tests:**
  - `test_pipeline_matches_transformPoints`: on a 10 × 10 lon/lat grid over Mars, `Transformer.from_pipeline(warpOperation(a, b)).transform` equals `transformPoints(a, b, …)` within 1e-6 m, for these pairs:
    - `IAU_2015:49910` → `IAU_2015:49912`;
    - `IAU_2015:49911` → `IAU_2015:49910`;
    - `CRISM_MTRDR_WKT` → `IAU_2015:49912`;
    - `HIRISE_POLAR_PROJ` → `IAU_2015:49901` (west, ographic, latitude first).
  - `test_no_pipeline_on_one_surface`: `CTX_DEM_PROJ` → `IAU_2015:49910` → None. The same for `EPSG:4326` → `EPSG:3857`.
  - `test_other_body_raises`: Mars → `WGS84` raises `ValueError` naming both bodies.
  - In `test_grid.py`, `test_onGridOf_keeps_planetocentric_latitude`:
    - The source is a 100 × 100 geographic `+proj=longlat +R=3396190` image (0.01° pixels) with one marker pixel at 44.995°N.
    - The target is a 500 m `IAU_2015:49912` grid built around `transformPoints`' position of that marker.
    - `onGridOf(..., nearest)` puts the marker within ½ px of the expected pixel. (It's 40 rows off today; the spec §1.1 records the check.)
  - `test_resampling_record_names_the_operation`: the record has a "geoc" pipeline for that pair, and "PROJ" for a same-surface pair.
- [ ] **Step 2: Run** `uv run pytest src/varda/geo/_tests/test_warp_operation.py src/varda/common/_tests/test_grid.py -q` → FAIL.
- [ ] **Step 3: Implement** `warpOperation`/`warpOptions`, then the `onGridOf` and `resamplingRecord` changes.
- [ ] **Step 4: Run** → PASS, then `uv run pytest src/varda/common src/varda/geo src/varda/analysis/_tests/test_image_choices.py -q` → PASS.
- [ ] **Step 5: Docs.**
  - `### Rasters between surfaces of one body`: the ballpark problem, the pipeline's steps, the 20 km example, and that masks, DEMs, overlays and Reproject Image all use it.
  - `image-processing.md` "Images on other grids": one sentence saying positions keep their planetocentric coordinates between a sphere and an ellipsoid, and that the record names the operation.
  - CHANGELOG Fixed: "Masks and DEMs resampled from a sphere onto an ellipsoid grid of the same body (or back) were misplaced by up to ~20 km on Mars (GDAL used PROJ's ballpark); they now keep their planetocentric positions."
- [ ] **Step 6: Commit** `git commit -m "Warps between a sphere and an ellipsoid keep planetocentric positions (onGridOf fixed)"`.

### Task 3: CRSs from label projection groups

**Wave:** 1 · **Touches:** `src/varda/geo/label_crs.py` (new), `src/varda/geo/_tests/test_label_crs.py` (new), `src/varda/geo/labels.py` (`_pds4Hints` keeps units), `src/varda/geo/_tests/test_labels.py`

**Interfaces** (spec §4.8, exactly): `LabelCrs` (with `complete`) and `labelCrs(hints: LabelHints) -> LabelCrs | None`.
- Consumes: `LabelHints.projectionGroup`, `latitudeType`, `longitudeDirection`, `target`, `source` (Phase 1 Task 11), `bodies.bodyNamed`, and `Body.sphereCrs()`.
- Produces: `wkt` from a PROJ string (built under `PROJ_LOCK`; a west label's geodetic longitude axis is set west through PROJ JSON, as `transform.eastLonLat` edits axes), `transform` as a 6-tuple, `assumed` and `missing` as plain sentences.
- Key lookup: try the PDS3, ISIS and PDS4 names of each value (the spec's Keys paragraph).
- Units: `labels.py` strips them from `projectionGroup`.
  - PDS3 radii and `MAP_SCALE` are km, by the PDS3 standard.
  - ISIS `Mapping` lengths are m.
  - PDS4: `_pds4Hints` now also stores each `cart:` element's `unit` attribute as `"{KEY}_UNIT"` (e.g. `A_AXIS_RADIUS_UNIT = "km"`). `labelCrs` converts km to m, and treats a missing unit as m. Add a `test_labels.py` case for it.

- [ ] **Step 1: Write failing tests** (groups as dicts in `LabelHints`):
  - `test_isis_equirectangular_local_radius_is_crism`: `{"PROJECTIONNAME": "Equirectangular", "EQUATORIALRADIUS": "3396190.0", "POLARRADIUS": "3376200.0", "LATITUDETYPE": "Planetocentric", "CENTERLATITUDE": "15.0", "CENTERLONGITUDE": "0.0"}` → `sameCrs(wkt, CRISM_MTRDR_WKT)`, `not complete`, and `assumed` mentions "local radius".
  - `test_hirise_polar_is_complete`: PDS3 `POLAR STEREOGRAPHIC`, A = C = 3376.2 km, `CENTER_LATITUDE = 90` → `sameCrs(wkt, HIRISE_POLAR_PROJ)` and `complete`.
  - `test_west_planetographic_label`: PDS3 EQUIRECTANGULAR, A 3396.19, C 3376.20, PLANETOGRAPHIC, WEST, `CENTER_LONGITUDE 100` → the CRS's `lon_0` is −100, its ellipsoid is flattened, its geodetic longitude axis is west, and it's `complete`.
  - `test_pds3_transform_matches_gdal`: write the spec §4.8 label (offsets −20.5 / 100.5, MAP_SCALE 0.5) with a 10 × 10 `.img` to `tmp_path` and open it with rasterio. `labelCrs(...).transform` equals `tuple(src.transform)[:6]`.
  - `test_isis_transform`: `PixelResolution 18`, `UpperLeftCornerX -1000`, `UpperLeftCornerY 5000` → `(18, 0, -1000, 0, -18, 5000)`.
  - `test_unsupported_projection`: `OBLIQUE CYLINDRICAL` → `wkt is None`, and `missing[0]` names it.
  - `test_missing_radii_use_target_sphere`: no radii, target Mars → R 3396190, and `assumed` names the IAU 2015 Mars sphere.
  - `test_malformed_never_raises`: `A_AXIS_RADIUS = "N/A"` with no target → `missing` names the radii. An empty group → `labelCrs(...)` is None.
- [ ] **Step 2: Run** `uv run pytest src/varda/geo/_tests/test_label_crs.py -q` → FAIL.
- [ ] **Step 3: Implement** with the spec's projection and surface tables. The local radius is r = a·c / √((c cos φ)² + (a sin φ)²).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -m "geo: build a CRS and transform from PDS3, ISIS and PDS4 map projection groups"`. Docs come with Task 8.

### Task 4: Pixel areas

**Wave:** 1 · **Touches:** `src/varda/geo/measure.py`, `src/varda/geo/distortion.py`, `src/varda/geo/_tests/test_pixel_areas.py` (new), `coordinate-systems.md` (new `### Pixel areas` under "How Varda measures")

**Interfaces** (spec §4.5 Phase 2, exactly): `pixelAreas`, `imagePixelAreas`, `AREA_METHOD_ROWS`, `AREA_METHOD_SCALE`, `pixelAreaMethod` (measure.py), and `arealScaleAtPixels` (distortion.py; vectorized `_factors` returning the areal column as an array).
- Row-only test: `transform.b == transform.d == 0`, and the CRS is geographic or its conversion method is one of "Equidistant Cylindrical", "Equidistant Cylindrical (Spherical)", "Mercator (variant A)", "Mercator (variant B)", "Popular Visualisation Pseudo Mercator" or "Lambert Cylindrical Equal Area" (plus the `(Spherical)` forms).
- Row areas: `Geod(a, c).polygon_area_perimeter` on the pixel's corners. Densify each edge to 16 points through `planetocentricLonLat`, then `ographicLatitude`.
- Return NaN where PROJ can't invert. Never raise for a CRS on a body. Raise `ValueError` only for a CRS on no body, as `cumulativeDistance` does.

- [ ] **Step 1: Write failing tests:**
  - `test_geographic_rows_match_the_sphere_formula`: `+proj=longlat +R=3396190`, 0.1° pixels at 60°N → R²·Δλ·(sin φ₂ − sin φ₁) within 1e-9 relative.
  - `test_equirectangular_rows`: `IAU_2015:49910` 1 km pixels at 60°N → 0.5e6 m² ± 1e-4 relative (k = 2).
  - `test_polar_stereographic_uses_areal_scale`: `HIRISE_POLAR_PROJ`, 1 m pixels at 85°N → 1 / areal scale, matching `polygonArea` of the pixel square within 1e-6.
  - `test_whole_image_interpolation_error`: a 4000 × 4000 `HIRISE_POLAR_PROJ` image at 85°N → `imagePixelAreas` within 1e-6 relative of `pixelAreas` at 100 random pixels.
  - `test_method_text`: `pixelAreaMethod(CRISM_MTRDR_WKT, …)` starts with `AREA_METHOD_ROWS` and ends with "sphere R = 3394.84 km".
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest src/varda/geo -q` → PASS.
- [ ] **Step 5: Docs.** `### Pixel areas`: both methods, the 4 Mpx interpolation and its bound, and that a selection's area counts whole pixels (it differs from **Area on the body** by up to half a pixel along the perimeter).
- [ ] **Step 6: Commit** `git commit -m "geo: ground area of each pixel on the CRS's own surface"`.

### Task 5: Transform overrides on images

**Wave:** 1 · **Touches:** `src/varda/common/entities.py`, `src/varda/common/_tests/test_metadata_overrides.py`, `src/varda/_tests/test_session.py`, `src/varda/image_loading/_tests/test_raster_writer.py`, `docs/user-guide/sessions-and-saving.md`

**Interfaces:**
- `EDITABLE_SINGLE` += `"transform"`. The value is six floats `(a, b, c, d, e, f)`, a list or a tuple (JSON gives lists).
- `setMetadataOverrides` validates the value (six finite numbers, `a·e − b·d ≠ 0`; otherwise `ValueError("A transform needs six finite numbers with a nonzero determinant")`). `None` removes it.
- `VardaRaster.transform` returns `Affine(*override)` when set, else the data source's. `hasGeospatialData` follows.
- Clear the same caches the `crs` override clears.

- [ ] **Step 1: Write failing tests:**
  - An identity-transform `ArrayDataSource` with a `crs` override and a `transform` override → `hasGeospatialData`, and `pixelToLatLon` uses the new transform.
  - Garbage (five numbers, a zero determinant, NaN) → `ValueError`, nothing changed.
  - `None` restores the file's transform.
  - A session save/restore keeps the override (`test_session.py`).
  - `writeRaster` writes the overridden transform (`test_raster_writer.py`, reopened with rasterio).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest src/varda/common src/varda/_tests/test_session.py src/varda/image_loading -q` → PASS.
- [ ] **Step 5: Docs.** In `sessions-and-saving.md`, one sentence: a corrected transform (from a label) is saved with the session like a corrected CRS, and the file isn't changed.
- [ ] **Step 6: Commit** `git commit -m "Images can carry a corrected pixel-to-map transform, saved in sessions and exports"`.

### Task 6: Display reprojection core

**Wave:** 1 · **Touches:** `src/varda/common/display_grid.py` (new), `src/varda/common/_tests/test_display_grid.py` (new), `pyproject.toml` (add `"varda.common.display_grid"` to the Qt-free contract's `source_modules`)

**Interfaces** (spec §4.4.1):
- `DISPLAY_MAX_PIXELS = 100_000_000` and `DISPLAY_CACHE_BYTES = 512 * 2**20`.
- `displayChoices(images: Sequence[VardaRaster], base: VardaRaster) -> ImageChoices` is `grid.placeableChoices(images, base)`. In addition, an image needs a reprojection (`not sameGrid`) and `base.width * base.height > DISPLAY_MAX_PIXELS` moves to `excluded` with "too large to reproject for display ({n:.0f} Mpx); reproject it with Processing → Geometry → Reproject Image".
- `needsReprojection(image, base) -> bool` is `not sameGrid(image, base)`.
- `reprojectForDisplay(overlay, base, band: int) -> np.ndarray`: `onGridOf(overlay, base, Resampling.nearest, [band])[:, :, 0].astype(np.float32)`. It inherits Task 2's fix once merged; Task 9's integration test covers the combination.
- `displayNote(overlay, base) -> str`: `"Reprojected for display from {describeCrs(overlay.crs).text} to {describeCrs(base.crs).text}; nearest neighbor; for display only: analyses, statistics and exports use each image as it is."`
- `class DisplayCache`:
  - `__init__(self, budget: int = DISPLAY_CACHE_BYTES)`;
  - `get(overlay, base, band) -> np.ndarray | None`;
  - `put(overlay, base, band, array) -> None`;
  - `forget(overlay) -> None`.
  - Keys are `(band, gridKey(base))`, stored per overlay in a `weakref.WeakKeyDictionary`. `gridKey(base) = (describeCrs(base.crs).authority or WKT text, tuple(base.transform)[:6], base.width, base.height)`, built under `PROJ_LOCK`.
  - Eviction is least recently used across overlays, by `array.nbytes`.

- [ ] **Step 1: Write failing tests:**
  - `test_choices_offer_same_body_other_grid`: an `IAU_2015:49912` overlay on an `IAU_2015:49910` base is offered. A `WGS84` overlay is excluded with "different bodies". A non-overlapping one is excluded with "doesn't overlap".
  - `test_too_large_base_is_excluded_with_its_size`: monkeypatch `DISPLAY_MAX_PIXELS` to 100 → excluded with "Mpx".
  - `test_class_values_never_blend`: a class map (0, 1, 2 blocks) reprojected eqc → polar stereographic → the set of finite values ⊆ {0, 1, 2}.
  - `test_0_360_overlay_on_minus180_base`: a `MOLA_MEGDR_PROJ` overlay at lon 257–258 on an `IAU_2015:49910` base centered at −102.5°E is offered, and `reprojectForDisplay` fills the base (no all-NaN result).
  - `test_cache_hit_returns_same_object`, `test_crs_override_on_base_misses`, `test_budget_evicts_oldest`, and `test_dead_overlay_frees_entries` (`del` the overlay, `gc.collect()`, the cache is empty).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Add the contract line.
- [ ] **Step 4: Run** → PASS. Run `uv run lint-imports` → KEPT, with `ignore_imports` unchanged.
- [ ] **Step 5: Commit** `git commit -m "Display reprojection of overlays onto another image's grid, with a bounded cache"`.

### Task 7: Reprojection engine

**Wave:** 2 · **Touches:** `src/varda/geo/reproject.py` (new), `src/varda/geo/_tests/test_reproject.py` (new)

**Interfaces** (spec §4.3):
- `MAX_OUTPUT_PIXELS = 400_000_000`.
- `@frozen TargetGrid`: `crs: str` (WKT), `transform: tuple[float, float, float, float, float, float]`, `width: int`, `height: int`; and `affine` (property → `Affine`).
- `gridFor(srcCrs, srcTransform: Affine, width: int, height: int, dstCrs, *, resolution: float | None = None, extent: tuple[float, float, float, float] | None = None) -> TargetGrid`. `extent` is (west, south, east, north) in dst map units. The function implements the spec's Output grid rules: extent, pixel size, `-tap` alignment, the antimeridian refusal and the size guard. It raises `ValueError` with the user-facing message.
- `sourceResolution(srcCrs, srcTransform, width, height) -> float`: √(dx·dy) from `groundSpacing` at the center pixel.
- `reprojectBand(band: np.ndarray, srcCrs, srcTransform: Affine, grid: TargetGrid, method: Resampling) -> np.ndarray`:
  - `band` is float64 (rows, cols), NaN for no data; the result is float32 (grid.height, grid.width).
  - It calls `rasterio.warp.reproject` with NaN no-data and `**warpOptions(srcCrs, grid.crs)`, outside `PROJ_LOCK`.
  - For a geographic source, it shifts the source transform by 0, +360 or −360° (the first that puts the source's footprint over the grid), as `onGridOf` does, so 0–360 sources land on −180..180 grids.
- `sourcePixelDistance(srcCrs, srcTransform: Affine, srcWidth: int, srcHeight: int, grid: TargetGrid, chunkRows: int = 256) -> np.ndarray`: float32. It moves each output pixel center into the source with `transformPoints`, then takes `~srcTransform`; the distance is `hypot(col − (floor(col) + 0.5), row − (floor(row) + 0.5))`. NaN outside [0, srcWidth) × [0, srcHeight).
- `scaleChange(srcCrs, srcTransform, srcWidth, srcHeight, grid) -> tuple[float, float]`: (max, mean) of the output's √(dx·dy) over the source's, on 9 × 9 output pixels mapped back to the source.
- `reprojectionRecord(srcCrs, grid, method: Resampling, resolutionM: float) -> dict`: `from`, `to`, `method`, `resolutionM`, `grid`, `coordinateOperation`, `gdal`, `proj` and `scaleChange`, as in the spec (plain JSON values).

- [ ] **Step 1: Write failing tests:**
  - `test_default_resolution_preserves_pixel_area`: a `CTX_DEM_PROJ` 20 m image centered at 70°N → `IAU_2015:49930` (north polar). The output pixel's ground area at the center (`pixelAreas`) is within 1% of the source center pixel's (also `pixelAreas`, about 20 × 55.5 m there, since `lat_ts` 18.4 is far from 70°).
  - `test_grid_is_aligned`: `grid.transform` c and f are integer multiples of the pixel size.
  - `test_round_trip_checker`: a 64 × 64 checker on `IAU_2015:49910` at 60°N → stere → back. Nearest positions agree within ½ px, and bilinear values agree within 0.05 away from the edges.
  - `test_sphere_to_ellipsoid_marker`: as Task 2's grid test, through `gridFor` + `reprojectBand`.
  - `test_antimeridian_refused`: a `MOLA_MEGDR_PROJ` image spanning lon 170–190 → `IAU_2015:49910` raises `ValueError`, whose message contains "lon_0" and "180".
  - `test_size_guard`: 1 m resolution on a 1000 km scene raises before allocating, with the size and a coarser resolution in the message.
  - `test_source_pixel_distance`: on the source's own grid the distance is 0 everywhere. On that grid shifted by half a source pixel in x only, it's 0.5 (± 1e-6) wherever the source covers it.
  - `test_record_is_json`: `json.dumps(reprojectionRecord(...))` works and contains "geoc" for sphere → ellipsoid.
- [ ] **Step 2: Run** `uv run pytest src/varda/geo/_tests/test_reproject.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -m "geo: reprojection engine (aligned target grid, planetocentric warps, quality measures, record)"`.

### Task 8: Label CRSs in the health check

**Wave:** 2 · **Touches:** `src/varda/geo/health.py`, `src/varda/geo/_tests/test_health.py`, `coordinate-systems.md` (new `### CRSs built from the label` under "What Varda checks when an image opens"; also a `label-crs-mismatch` row in "The checks")

**Interfaces** (spec §4.2, §4.8):
- `CrsFix` gains `transform: tuple[float, float, float, float, float, float] | None = None`.
- `_missing` and a new `_labelMismatch` use `labelCrs(hints)`. They run only when `hints.source` is the file's own label (`not hints.source.startswith("Varda provenance")`).
- Issue codes and wording are exactly the spec §4.8 rules table.
- The automatic fix's `transform` is the label's only when `transform.is_identity`.
- `fixRecord` adds `"source": "label"` and `"transform": [...]` when the fix has them.
- `headline` returns "No coordinate system in its file; built from its label." for that automatic fix.
- Don't touch `DISTORTION_SUGGESTION` (Task 12 owns it).

- [ ] **Step 1: Write failing tests:**
  - No CRS, an identity transform, and the HiRISE-style complete group → `no-crs` with an automatic fix whose `transform` matches the label's.
  - No CRS and the ISIS local-radius group → `no-crs` with a fix, `automatic is False`, and the suggestion mentions "local radius".
  - No CRS and `OBLIQUE CYLINDRICAL` → `no-crs`, no fix, and the suggestion names the projection.
  - GDAL's CRS for the spec's WEST label (`+proj=eqc +lat_ts=10 +lon_0=100 +R=3396190`) with that label's hints → `label-crs-mismatch` PROBLEM, `fix.automatic is False`; `lon-direction-mismatch` is also present.
  - `CRISM_MTRDR_WKT` with the matching ISIS group → no `label-crs-mismatch`.
  - The same mismatch with `source="Varda provenance (…)"` → no label issue.
  - `checkCrs` never raises on `projectionGroup={"MAP_PROJECTION_TYPE": "EQUIRECTANGULAR", "A_AXIS_RADIUS": "N/A"}`.
- [ ] **Step 2: Run** `uv run pytest src/varda/geo/_tests/test_health.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Docs.** Add `### CRSs built from the label`: the supported projections, the surface table with what counts as assumed, the transform formulas, the automatic and offered rules, and the limitation that GDAL's transform is kept unchecked. Add the `label-crs-mismatch` row to "The checks".
- [ ] **Step 6: Commit** `git commit -m "geo: CRS health check builds a CRS from the label when the file has none, and flags labels GDAL misread"`.

### Task 9: Overlay bar across projections

**Wave:** 2 · **Touches:** `src/varda/workspaces/raster_overlay.py`, `src/varda/workspaces/_tests/test_raster_overlay.py`, `viewing-and-measuring.md` (new `## Overlays`), `image-processing.md` ("Same grid only" sentence), `coordinate-systems.md` (one line in "Combining images"), CHANGELOG

**Interfaces:** consumes Task 6.
- `OverlayBar`:
  - `_refreshImages` uses `displayChoices`; the count reads "({n} can't be overlaid)".
  - A module-level `DisplayCache` is shared by all bars.
  - New widget `self.reprojectedLabel` (`MutedLabel`, hidden unless reprojected), with text "Reprojecting…", "reprojected for display" or "couldn't reproject: {reason}", and `displayNote` as its tooltip.
  - `self._generation: int`.
- `_apply` for an overlay that `needsReprojection`:
  - Get the band (`Class` for **Classes**, else the layer's index) from the cache, or run `reprojectForDisplay` with `runInBackground(owner=self)`.
  - On done, if the generation still matches, `put` the result, wrap it with `displayRaster(overlay, base, array, layer)`, and render as today.
  - `displayRaster` is a module function: a `VardaRaster` on the base's grid whose band is named as the layer, with `classes`/`classColors` copied for **Classes**, so `overlayRaster` colors it.
- `_onImageEdited` calls `cache.forget(edited)` before re-applying.
- Class color changes re-render from the cache.

- [ ] **Step 1: Write failing tests (pytest-qt):**
  - A base on `IAU_2015:49910` and a class map on `IAU_2015:49912` → the class map is listed. Choosing it shows "Reprojecting…", then (`qtbot.waitUntil`) "reprojected for display", and `viewports[0].hasOverlayImage()`.
  - A WGS84 image isn't listed, and the tooltip names both bodies.
  - `test_display_marker_keeps_planetocentric_latitude` (Qt-free, in this file since it needs Task 2 merged): `reprojectForDisplay` of Task 2's 45°N sphere marker onto an `IAU_2015:49912` base lands within ½ px of `transformPoints`' position.
  - Choosing an overlay, then at once another, renders only the second (the stale result is dropped).
  - `setClassColor` on the reprojected overlay doesn't call `reprojectForDisplay` again (monkeypatch a counter).
  - A same-grid overlay shows no label (today's behavior is unchanged).
  - `assertNothingClipped` at 1280×800 with the label visible.
- [ ] **Step 2: Run** `uv run pytest src/varda/workspaces/_tests/test_raster_overlay.py src/varda/workspaces/_tests/test_workspace_overlays.py -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS. Then do a manual check: `uv run varda`, open two Mars images on different projections, overlay one on the other at a 1280×800 window.
- [ ] **Step 5: Docs.**
  - `## Overlays` in `viewing-and-measuring.md`: what can be overlaid, "reprojected for display" (nearest; positions within ½ overlay pixel + ½ base pixel; display only), the size limit, and other bodies refused.
  - `image-processing.md`: drop overlays from "Same grid only", with a link.
  - CHANGELOG Added: "Overlays across map projections…".
- [ ] **Step 6: Commit** `git commit -m "Workspace overlays accept images on the same body in another projection, reprojected for display"`.

### Task 10: ROI pixel-area columns

**Wave:** 2 · **Touches:** `src/varda/rois/roi_calculations.py`, `src/varda/rois/add_column_dialog.py`, `src/varda/rois/_tests/test_roi_calculations.py`, `src/varda/rois/_tests/test_add_column_dialog.py`, `viewing-and-measuring.md` ("Calculated columns"), CHANGELOG

**Interfaces:** consumes Task 4.
- `AREA_STATISTICS: dict[str, str]` = `{"area_km2": "area of pixels with data (km²)", "area_weighted_mean": "area-weighted mean", "sum_times_area_km2": "sum × pixel area (value·km²)"}`.
- `ZonalStatistic.statistic` may be any key of `STATISTICS | AREA_STATISTICS`.
- `_zonal` computes them from the mask's `(rows, cols)` (`np.nonzero`) and `pixelAreas(image.crs, image.transform, rows, cols)` on the finite values only.
- Without georeferencing the value is NaN, and `describe` gives `NO_MAP_COORDINATES`.
- `describe` appends `pixelAreaMethod(...)` for area statistics. The dialog's statistic box lists both dicts (labels for the area ones).
- `suggestName` → `"{band}_area_km2"` and so on.

- [ ] **Step 1: Write failing tests:**
  - A 10 × 10 px ROI on a `LRO_WAC_PROJ` image at 0°N → `area_km2` = 100 × (0.1 km)² ± 1e-6 relative.
  - On `IAU_2015:49910` at 60°N, 1 km pixels → half the pixel count.
  - `area_weighted_mean` equals the plain mean for uniform areas, and differs for a ROI spanning latitudes (a hand computation in the test).
  - Without a CRS → NaN, with the reason in `describe`.
  - The dialog offers "area of pixels with data (km²)".
- [ ] **Step 2: Run** `uv run pytest src/varda/rois -q` → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS. Check the dialog at 1280×800 (`assertNothingClipped`).
- [ ] **Step 5: Docs.** Add the three statistics to "Calculated columns": the formula, the method sentence, whole pixels vs the polygon (vs **Area on the body**), and the uncertainty (georeferencing, the pixel-selection rule). CHANGELOG Added.
- [ ] **Step 6: Commit** `git commit -m "ROI columns: ground area of the pixels with data, area-weighted mean and sum × area"`.

### Task 11: Class-map areas (core)

**Wave:** 2 · **Touches:** `src/varda/analysis/class_tools.py`, `src/varda/analysis/_tests/test_class_tools.py`

**Interfaces:** consumes Task 4. Produces, for Task 14:
- `@frozen ClassArea`: `index: int`, `name: str`, `pixels: int`, `areaKm2: float` (NaN unreferenced), `fraction: float` (of classified pixels).
- `@frozen ClassAreas`: `rows: tuple[ClassArea, ...]`, `method: str` (`pixelAreaMethod`, or "pixel counts only: the image has no map coordinates"), `totalKm2: float`.
- `classAreas(image: VardaRaster) -> ClassAreas`: `imagePixelAreas` once, then `np.bincount(classes, weights=areas)` over finite class values. It never raises for a class map.
- `classAreasCsv(areas: ClassAreas, imageName: str) -> str`: `#` header lines (image, method, Varda version), then `class,name,pixels,area_km2,fraction`.

- [ ] **Step 1: Write failing tests:**
  - A 3-class map on `IAU_2015:49910` at 0°N → areas from `pixelAreas` summed, and the fractions sum to 1.
  - An unreferenced class map → NaN areas with the pixel counts right.
  - The CSV has the header lines and one row per class.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest src/varda/analysis/_tests/test_class_tools.py -q` → PASS.
- [ ] **Step 5: Commit** `git commit -m "Class maps: per-class ground areas from pixel areas"`.

### Task 12: Reproject Image analysis and guide

**Wave:** 3 · **Touches:** `src/varda/analysis/reproject.py` (new), `src/varda/analysis/_tests/test_reproject_analysis.py` (new), `src/varda/analysis/registry.py`, `src/varda/analysis/_tests/test_memory_estimates.py`, `src/varda/common/provenance.py` (`KEPT_METADATA_KEYS` += `"reprojection"`), `src/varda/geo/health.py` (`DISTORTION_SUGGESTION` only), `src/varda/analysis/topography.py` (`unavailableReason` text only), `docs/user-guide/reprojecting.md` (new), `docs/user-guide/README.md`, `image-processing.md` (Geometry), CHANGELOG

**Interfaces** (spec §4.3, parameters exactly as its table):
- `class ReprojectTarget(LabeledEnum)`: `RECOMMENDED = "Recommended projection"`, `IMAGE = "Another image's grid"`, `CRS = "Coordinate system"`.
- `class ReprojectExtent(LabeledEnum)`: `WHOLE = "Whole image"`, `OTHER = "Another image's extent"`.
- `class ResamplingMethod(LabeledEnum)`: `NEAREST = "Nearest neighbor"`, `BILINEAR = "Bilinear"`, `CUBIC = "Cubic"`, `AVERAGE = "Average"`, `MODE = "Mode"`, mapped to `rasterio.enums.Resampling`.
- `defaultResampling(image) -> ResamplingMethod`: NEAREST for `isClassMap(image)`, for `"classes" in image.extraMetadata`, or for `bandCount == 1 and image.dtype.kind in "iub"`; else BILINEAR.
- `ReprojectAnalysis(Analysis)`:
  - `analysisId = "reproject"`, `name = "Reproject Image"`, `category = "Geometry"`, `software = ("rasterio", "pyproj")`.
  - Parameters `target`, `purpose`, `grid` (ImageParameter "Image", optional), `crsText` (StringParameter "Coordinate System"), `resampling`, `resolution` (FloatParameter "Resolution", 0.0, range (0, 1e7), units "m"), `extent`, `sourceNoData` (StringParameter "Source No-Data"), `qualityBands` (BoolParameter "Quality Bands", True).
  - `prepareFor`: `grid.setChoices(lambda: placeableChoices(self.context.images, image))`, the default resampling, and `crsText` = the MEASUREMENT recommendation's PROJ string.
  - `unavailableReason`: "It has no map coordinates, so it can't be reprojected; set its coordinate system in Image Properties first." when `not image.hasGeospatialData`.
  - `estimateMemory`: per the spec, from `gridFor` (if `gridFor` raises, return the source cube × 6 so the run reports the real error).
  - `runAll` → `[result, quality]` when `qualityBands`, else `[result]`. `run` raises `ValueError` with the message for an unreadable CRS, another body (`canTransform`), a missing Image, or `gridFor`'s refusals.
- Description: "{name} reprojected to {describeCrs(grid.crs).text} at {resolution} m/px ({method}); {recommendation.reason}". Metadata: `{"reprojection": reprojectionRecord(...)}`, plus `classes`/`classColors` for class maps.
- `DISTORTION_SUGGESTION` → "Measurements are geodesic and unaffected; shapes and slopes on this map are distorted — reproject it (Processing → Geometry → Reproject Image) for figures and terrain".
- `topography.unavailableReason` appends "; reproject it first (Processing → Geometry → Reproject Image)".

- [ ] **Step 1: Write failing tests** (`test_reproject_analysis.py`; set `analysis.context` with `AnalysisContext(images=[...])`):
  - `test_recommended_polar_for_hirise`: `HIRISE_POLAR_PROJ` at 85°N → the result's CRS is stere with `lat_ts` 85.0, and the description contains the reason.
  - `test_match_another_image_grid`: the result's transform, size and CRS equal the chosen image's.
  - `test_other_body_refused`: CRS text `EPSG:4326` on a Mars image → `ValueError` containing "Mars" and "Earth".
  - `test_class_map_defaults_to_nearest_and_stays_a_class_map`: values ⊆ originals, band "Class", `classes` and `classColors` kept.
  - `test_equivalent_target_copies`: CRS text `IAU_2015:49910` on a `MARS_EQC_PDS3_WKT` image with resolution 0 → identical data, and "nothing resampled" in the description.
  - `test_quality_image`: two bands named "Source pixel distance" and "Valid"; Valid is 0 exactly where the result is NaN.
  - `test_provenance_keeps_reprojection`: `writeRaster` and reopen → `reprojection` in the metadata (`KEPT_METADATA_KEYS`).
  - `test_memory_estimate_bounds_peak`: tracemalloc's peak ≤ `estimateMemory` for a 1:1 run and for a 10× upsample (resolution = source / 10). Add `"Reproject Image"` to `test_memory_estimates.NOT_MEASURED` with the comment "needs a georeferenced image: measured in test_reproject_analysis.py".
  - `test_registered_after_resample`: `ANALYSES.index(ReprojectAnalysis) == ANALYSES.index(ResampleAnalysis) + 1`.
- [ ] **Step 2: Run** `uv run pytest src/varda/analysis/_tests/test_reproject_analysis.py -q` → FAIL.
- [ ] **Step 3: Implement**, then register the analysis and make the two text changes.
- [ ] **Step 4: Run** `uv run pytest src/varda/analysis src/varda/geo -q` → PASS. Run `uv run lint-imports` → KEPT (no Qt in `analysis/reproject.py`, no preview widget).
- [ ] **Step 5: Docs.** Create `reprojecting.md` with these sections:
  - **What it does.**
  - **Targets:** the recommendation rules, summarized and linked to `coordinate-systems.md#recommended-projections`.
  - **Parameters:** a table with meaning, units, range and default, for every parameter.
  - **Outputs:** the bands, the quality image, the `reprojection` record and its fields.
  - **How it works:** GDAL's warper, the planetocentric pipeline, the aligned grid, the extent and the antimeridian, the size guard.
  - **Uncertainty and quality:** every bullet of spec §4.3's "Uncertainty (docs)", plus how to read the Valid and Source pixel distance bands and `scaleChange`.
  - **How to report:** a methods-sentence template naming the source and target CRS (authority or WKT), the resampling, the resolution, and the GDAL and PROJ versions.
  - **References.**

  Link the page from `README.md` (after Image processing tools) and from `image-processing.md`'s Geometry section. CHANGELOG Added: "**Reproject Image**…".
- [ ] **Step 6: Commit** `git commit -m "Processing → Geometry → Reproject Image: recommended, matched or chosen projections, with quality bands and a guide"`.

### Task 13: Label CRSs at load, Apply and Undo

**Wave:** 3 · **Touches:** `src/varda/image_loading/crs_check.py`, `src/varda/all_images_view_list/image_properties_dialog.py` (`applyFix` only), `src/varda/image_loading/_tests/test_crs_health_on_load.py`, `src/varda/all_images_view_list/_tests/test_image_properties_dialog.py`, `coordinate-systems.md` (extends Task 8's section with "Reporting a CRS built from the label"), CHANGELOG

**Interfaces:** consumes Tasks 5 and 8.
- `checkImageCrs`: an automatic fix with a `transform` sets both overrides: `{"crs": wkt, "transform": list(t)}`. `_isFix` also compares the transform override.
- `chooseCrs(image, crs, reason, transform: tuple[float, float, float, float, float, float] | None = None)` sets the transform override when given, and records `"transform"` in `crsFix`.
- New `applyCrsFix(image, fix: CrsFix) -> CrsHealth` = `chooseCrs(image, fix.wkt, fix.reason, fix.transform)`.
- `undoCrsFix` also removes the `transform` override.
- The dialog's `applyFix` calls `applyCrsFix(self.image, fix)`.

- [ ] **Step 1: Write failing tests:**
  - Write a 10 × 10 PDS3 `.img` + `.lbl` with the HiRISE-style group to `tmp_path`. Monkeypatch `RasterioDataSource.crs` to return None and `.transform` to the identity: GDAL 3.9.3 builds this one itself, so this stands in for a product GDAL can't read. Then `loadRaster`:
    - `image.hasGeospatialData`;
    - `sameCrs(image.crs, HIRISE_POLAR_PROJ)`;
    - `extraMetadata["crsFix"]["source"] == "label"`, with its `transform`.
  - Session save/restore keeps both overrides, and the health check is rerun.
  - The WEST label opened through GDAL → `label-crs-mismatch` offered. In the dialog, **Apply** sets the label's CRS, and **Undo** restores GDAL's.
  - `assertNothingClipped` at 1280×800 for the dialog with the new issue.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest src/varda/image_loading src/varda/all_images_view_list src/varda/_tests/test_session.py -q` → PASS.
- [ ] **Step 5: Docs.** "Reporting a CRS built from the label" (the `crsFix` record's `source` and `transform`). CHANGELOG Added: "CRSs from planetary labels…".
- [ ] **Step 6: Commit** `git commit -m "Images whose file has no CRS get one from their label; label/GDAL disagreements can be applied and undone"`.

### Task 14: Class areas in Image Properties

**Wave:** 4 · **Touches:** `src/varda/all_images_view_list/image_properties_dialog.py`, `src/varda/all_images_view_list/_tests/test_image_properties_dialog.py`, `docs/user-guide/classification.md` (a "Class areas" section), CHANGELOG

**Interfaces:** consumes Task 11.
- For a class map, the dialog gains a "Classes" group (`isClassMap`):
  - a `QTableWidget` (class, name, pixels, area km², %) with the method sentence under it;
  - **Copy as CSV** (`classAreasCsv` to the clipboard) and **Export CSV…**.
- `classAreas` runs through `runInBackground(owner=self)`. The table shows "Computing…" until then.
- The group sits in the dialog's scroll area and never widens it.

- [ ] **Step 1: Write failing tests (pytest-qt):**
  - A 3-class map on `IAU_2015:49910` → after `qtbot.waitUntil` the table has 3 rows, and its areas equal `classAreas`.
  - Copy as CSV puts the header line on the clipboard.
  - An unreferenced class map shows pixel counts and "no map coordinates".
  - `assertNothingClipped` at 1280×800.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** `uv run pytest src/varda/all_images_view_list -q` → PASS.
- [ ] **Step 5: Docs.** In `classification.md`'s "Class areas": the method, its surface, whole pixels, and the uncertainty (classification accuracy dominates, so see Accuracy Assessment; plus georeferencing). CHANGELOG Added.
- [ ] **Step 6: Commit** `git commit -m "Image Properties shows each class's ground area for class maps, with CSV export"`.

### Task 15: Phase 2 verification

**Wave:** 5 · **Touches:** none new. Fixes go in the task they belong to.

- [ ] **Step 1:** `uv run pytest` (full suite) → PASS.
- [ ] **Step 2:** `uv run lint-imports` → KEPT. `git diff origin/main -- pyproject.toml` shows only the `display_grid` line.
- [ ] **Step 3:** `uv run ty check` → no new diagnostics compared with `origin/main`.
- [ ] **Step 4:** Run `uv run ruff format --check` and `uv run ruff check` on the touched files.
- [ ] **Step 5:** `grep -rn "rasterio.warp import reproject\|warp.reproject(" src/varda` → every call site either passes `warpOptions` or is listed in spec §4.3.1 as Phase 3 (`terrain/overlays.py`, `find_data/basemap.py`).
- [ ] **Step 6: Docs review.**
  - `reprojecting.md` covers every parameter (meaning, units, range, default), every output and every uncertainty bullet.
  - `coordinate-systems.md` has Tasks 1, 2, 4 and 8 sections.
  - The README links the new page.
  - The CHANGELOG has one bullet per user-visible task.
- [ ] **Step 7: Build check.** Run `uv run pyinstaller Varda.spec`, then `dist/Varda/Varda --self-test`. `self_test.py` gains a 4 × 4 `reprojectBand` of `IAU_2015:49910` → `IAU_2015:49912`, which proves GDAL's `COORDINATE_OPERATION` and PROJ's geoc work in the bundle.
- [ ] **Step 8:** Commit any fixes. Open the PR with `gh pr create --repo varda-pro/Varda --head <branch>`, per the gh-account memory.

---

## Later phases (headings only)

### Phase 3: Mosaic and 3D Terrain projections (using `geo.recommend` and `geo.reproject`), terrain drapes and basemaps through `warpOperation`, vertical datum, scale-correct terrain analysis
### Phase 4: Areoid conversion, workspace coordinate status line, project working CRS, removal of legacy georeferencing code
