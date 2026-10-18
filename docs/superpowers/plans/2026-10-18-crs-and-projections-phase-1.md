# Coordinate Systems and Map Projections — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One Qt-free core (`varda.geo`) that knows bodies, describes and compares CRSs, measures on the body and reports distortion. Measurements (line lengths, profiles, ROI areas) become geodesic whatever the projection. Cross-body combinations stop crashing or silently mixing. Every image gets a CRS health check at load, with a quiet badge, an Image Properties section and one recorded automatic fix.

**Architecture:** `src/varda/geo/` (bodies, describe, equivalence, distortion, measure, labels, health) is Qt-free and in the import-linter contract. Its functions take `pyproj.CRS | str`, lock `PROJ_LOCK` internally and return plain values. Existing modules call it instead of their own body, equality and distance code. The GUI shows its results in the image list, Image Properties and the viewport readout. There are no modal dialogs.

**Tech Stack:** Python 3.13, pyproj ≥ 3.7 (PROJ ≥ 9 with the `IAU_2015` authority), rasterio, shapely, geopandas, attrs, PyQt6, pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-18-crs-and-projections-design.md` (§ numbers below refer to it).

## Global Constraints

- **CLAUDE.md applies:**
  - Before starting, run `git status` and confirm the branch is current with `origin/main`.
  - Use uv.
  - Run `uv run ruff format` and `uv run ruff check` on touched files only.
  - `uv run ty check` must add no new diagnostics. Compare against the base with a WIP commit, not a bare `git stash`.
  - `uv run lint-imports` must stay KEPT. **Never grow `ignore_imports`.**
  - Run the full `uv run pytest` before the PR.
- **`varda.geo` is Qt-free.** Add `"varda.geo"` to `source_modules` of the "Logic modules don't use Qt" contract in `pyproject.toml` (Task 1).
- **Threading:** every public `varda.geo` function takes `CRS | str | None`, holds `varda.utilities.proj_lock.PROJ_LOCK` (an RLock) around all PROJ work, and returns plain values or attrs records of plain values. The only exceptions are `Body.sphereCrs()`, `Body.referenceCrs()` and `Body.geod()`, which return fresh objects.
- **Style:**
  - snake_case allowed in `varda.geo`; camelCase in Qt subclasses and in existing camelCase modules.
  - Type hints; never `Any`.
  - No lazy imports.
  - American English.
  - attrs `@frozen` for records.
- **Never return "Earth" for an unknown body.** Unknown means `None`.
- **Scientific documentation, in the same task as the change:**
  - Create `docs/user-guide/coordinate-systems.md` in Task 3 and link it from `docs/user-guide/README.md`.
  - Later tasks add their sections.
  - Every number Varda reports states its method, its surface (sphere or ellipsoid and radii) and its error terms.
  - Cite Snyder (1987), Karney (2013), Archinal et al. (2018) and the PROJ docs as listed in the spec's References.
- **CHANGELOG.md:** add bullets under the existing `## Unreleased` → `### Added` / `### Fixed`. Exactly one of each heading exists; never add headings.
- **Laptop layout:** changed widgets pass `common/layout_audit.assertNothingClipped` at 1280×800.
- **Commits:** end every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Fixtures:** use the real-style CRSs from spec §6 through `src/varda/geo/_tests/fixtures.py` (Task 1). Don't add binary data files.

## Review Focus

1. **Cross-body never crashes and never mixes.** A WGS84-tagged image plus a Mars image must not raise in Linked Images, the CRISM Ratio map link, ROI transfer, point saving, mask/DEM placement or the mosaic. Each gives the user a reason naming both bodies (Tasks 4–6).
2. **Equivalent CRSs are equal everywhere.** A GDAL `D_MARS` WKT and `IAU_2015:49910` / `+proj=eqc +R=3396190` are the same grid. The mosaic accepts them, and nothing resamples between them (Tasks 4–5).
3. **Measurement numbers.**
   - A 10 km east–west line at 60°N in `lat_ts=0` equirectangular measures 10.000 km, not 20.
   - A geographic ROI's area is in km², not degrees².
   - CSV headers state the method, the surface and the body-model difference (Tasks 8–10).
4. **The automatic fix is narrow, visible and recorded.**
   - It applies only to an Earth *geographic* CRS plus a label naming another body.
   - It's shown once, survives a session round trip, and appears in an exported result's provenance.
   - Without label evidence nothing changes (Tasks 12–14).
5. **Body identification regressions:**
   - Vesta's IAU 2015 sphere (255 km) → Vesta.
   - A 3000 km sphere → None.
   - Find Data maps and gazetteer distances are unchanged within tolerance after the move off `catalogs.bodies` (Tasks 1–2).
6. **Thread safety.** `varda.geo` called from `runInBackground` workers while the GUI readout transforms coordinates must not crash. A stress test runs both concurrently (Task 1, `test_thread_safety.py`).

---

### Task 1: `varda.geo` package, the body table and body identification

**Files:**
- Create: `src/varda/geo/__init__.py` (docstring only), `src/varda/geo/bodies.py`
- Create: `src/varda/geo/_tests/__init__.py`, `src/varda/geo/_tests/fixtures.py`, `src/varda/geo/_tests/test_bodies.py`, `src/varda/geo/_tests/test_thread_safety.py`
- Modify: `pyproject.toml` (add `"varda.geo"` to the Qt-free contract's `source_modules`)

**Interfaces** (spec §4.1):
- `@frozen class Body`:
  - fields: `name: str`, `naifId: int`, `equatorialRadius: float` (m), `polarRadius: float` (m), `meanRadius: float` (m), `westPositiveOgraphic: bool`;
  - methods: `sphereCrs() -> CRS` (`IAU_2015:{naifId*100}`), `referenceCrs() -> CRS` (`IAU_2015:{naifId*100+2}` if PROJ has it, else `sphereCrs()`), `geod() -> Geod` (a, c of the reference).
- `BODIES: dict[str, Body]`:
  - Mercury 199, Venus 299, Earth 399, Moon 301, Mars 499, Phobos 401, Ceres 2000001, Vesta 2000004, Io 501, Europa 502, Ganymede 503, Callisto 504, Enceladus 602, Titan 606.
  - `equatorialRadius`/`polarRadius` are read from PROJ at import (the ographic code's ellipsoid if it exists, else the sphere's).
  - `meanRadius` comes from today's `catalogs.bodies.RADII_M` values.
  - `westPositiveOgraphic` is True for every body except Earth, Moon and the Sun (Archinal et al., 2018).
- `bodyNamed(name: str) -> Body | None`: case-insensitive, with aliases `{"luna": "Moon", "d_mars": "Mars", "mars_2000": "Mars", "moon_2000": "Moon"}`, and strips the "(2015)", " - Sphere", "D_" and "GCS_" decorations.
- `@frozen class BodyMatch`: `body: Body | None`, `evidence: str`, `candidates: tuple[str, ...] = ()`.
- `identifyBody(crs: CRS | str | None, labelTarget: str | None = None) -> BodyMatch`, in order:
  1. `labelTarget` through `bodyNamed`;
  2. an `IAU_2015` authority code (`crs.to_authority(auth_name="IAU_2015")` or a `list_authority` match) → naif = code // 100;
  3. EPSG:4326, or a WGS84 or other Earth datum → Earth;
  4. datum, ellipsoid and CRS names through `bodyNamed` on each word group;
  5. radius: bodies whose [c·0.99, a·1.01] or mean·[0.99, 1.01] contains the CRS semi-major axis. Exactly one → it. Several → `body=None`, `candidates=names`. None → `body=None`, evidence "radius X km matches no known body".
- `src/varda/geo/_tests/fixtures.py`: constants `CRISM_MTRDR_WKT`, `CTX_DEM_PROJ`, `HIRISE_POLAR_PROJ`, `LRO_WAC_PROJ`, `LRO_WAC_SOUTH_POLAR_PROJ`, `WGS84`, `MOLA_MEGDR_PROJ`, `VESTA_IAU`, exactly as in spec §6. Also `makeImage(crs, *, centerLonLat, pixelSize, width=64, height=64, bands=1, label: str | None = None) -> VardaRaster` (an `ArrayDataSource` with a north-up transform centered on `centerLonLat` in the CRS's units, computed with pyproj, and `extraMetadata={"pds3Label": label}` when given).

- [ ] **Step 1: Write failing tests** in `test_bodies.py`:
  - `test_crism_mtrdr_is_mars_by_datum`: `identifyBody(CRISM_MTRDR_WKT).body.name == "Mars"` and `"D_MARS" in evidence`.
  - `test_hirise_polar_sphere_is_mars`: 3376.2 km → Mars.
  - `test_lro_wac_is_moon`, `test_wgs84_is_earth`.
  - `test_vesta_iau_sphere_is_vesta`: `IAU_2015:200000400` → Vesta. This is the regression for today's "Earth".
  - `test_unknown_radius_is_no_body`: `+proj=longlat +R=3000000` → `body is None`, "matches no known body" in evidence.
  - `test_label_beats_crs`: `identifyBody(WGS84, labelTarget="MARS").body.name == "Mars"`, with "label" in the evidence.
  - `test_body_radii_come_from_proj`: Mars a = 3396190, c = 3376200; Moon a = c = 1737400.
  - `test_reference_crs`: Mars → `IAU_2015:49902`; Moon → `IAU_2015:30100` (sphere).
  - `test_bodyNamed_aliases`: "D_MARS", "Mars (2015) - Sphere", "luna".
- [ ] **Step 2: Write `test_thread_safety.py`**: 8 threads × 200 iterations call `identifyBody` on the fixtures while the main thread runs `Transformer.from_crs(...).transform` under `PROJ_LOCK`. Assert no exception and consistent answers.
- [ ] **Step 3: Run** `uv run pytest src/varda/geo/_tests -q` → FAIL (module missing).
- [ ] **Step 4: Implement** `bodies.py` and `fixtures.py`. Add `"varda.geo"` to the import-linter contract.
- [ ] **Step 5: Run** the tests → PASS. Run `uv run lint-imports` → KEPT.
- [ ] **Step 6: Commit** `git commit -m "geo: body table from PROJ's IAU_2015 authority and body identification"`.

### Task 2: Move body callers to `varda.geo.bodies`

**Files:**
- Modify:
  - `src/varda/catalogs/bodies.py`: keep `BodyChoice`, `normalizeLongitude` and `toLongitudeLatitude`. Delete `RADII_M`, `bodyName`, `geographicCrs` and `bodyOf`.
  - `src/varda/catalogs/footprints.py`
  - `src/varda/catalogs/gazetteer.py` (radius from `bodyNamed(body).meanRadius`)
  - `src/varda/catalogs/basemaps.py`
  - `src/varda/catalogs/stac.py`
  - `src/varda/workspaces/find_data/projection.py`
  - `src/varda/workspaces/find_data/basemap.py` (`imageBody` returns `str | None` from `identifyBody`)
  - `src/varda/workspaces/find_data/find_data_workspace.py`
  - `src/varda/terrain/vectors.py`
  - `src/varda/workspaces/terrain/terrain_workspace.py`
  - `src/varda/self_test.py`
- Test: `src/varda/catalogs/_tests/test_bodies.py` (update), `src/varda/terrain/_tests/test_animation_and_lines.py` (vectors), `src/varda/catalogs/_tests/test_gazetteer.py`

**Interfaces:**
- Consumes Task 1.
- Produces `catalogs.bodies.toLongitudeLatitude(frame)`, which uses `identifyBody(frame.crs).body.sphereCrs()` for projected frames and raises `ValueError("…matches no known body…")` when the body is unknown.
- **Behavior decisions (spec §7, decision 3):**
  - "Degrees on a body" = `Body.sphereCrs()` (IAU 2015 sphere). Mars changes from 3389.5 km to 3396.19 km.
  - Find Data's `Projector` draws on `Body.meanRadius` (unchanged numbers for maps).
  - `terrain.vectors.toDemCoordinates` uses `demCrs.geodetic_crs` for degrees, as the vector-import branch does.
- **If `feature/vector-import` has merged by then:** replace its `bodyOf`/`geographicCrs` uses with `identifyBody`/`Body.sphereCrs()` in this task.

- [ ] **Step 1: Update tests first:**
  - catalogs `test_bodies.py` expectations: unknown radius → `toLongitudeLatitude` raises with "no known body" (was a silent Earth).
  - Add a Vesta footprint test.
  - Terrain vectors: degrees on a Mars `CTX_DEM_PROJ` DEM land within 0.5 px of the same points through `IAU_2015:49900`.
- [ ] **Step 2: Run** them → FAIL.
- [ ] **Step 3: Implement** the moves. `grep -rn "RADII_M\|bodyOf\|geographicCrs\|bodyName" src/` must return nothing outside `varda/geo`.
- [ ] **Step 4: Run** `uv run pytest src/varda/catalogs src/varda/terrain src/varda/workspaces/find_data -q` → PASS. Run `uv run lint-imports` → KEPT. Run `uv run varda --self-test`, or the self-test test (`src/varda/_tests/test_windowed_self_test.py`) → PASS.
- [ ] **Step 5: CHANGELOG** Fixed: "Images and footprint files of Vesta and other bodies with IAU 2015 radii were identified as Earth; unknown bodies are now reported as unknown."
- [ ] **Step 6: Commit** `git commit -m "Use varda.geo for body identification everywhere; unknown bodies are no longer Earth"`.

### Task 3: CRS descriptions, and the coordinate-systems guide

**Files:**
- Create: `src/varda/geo/describe.py`, `src/varda/geo/_tests/test_describe.py`
- Modify:
  - `src/varda/all_images_view_list/image_properties_dialog.py` (a "Coordinate system" group: the description's `text`, the authority or "matches IAU_2015:…", and the WKT in a collapsed read-only `QPlainTextEdit`; pixel size with units and ground meters)
  - `src/varda/common/grid.py` (`pixelSizeText` delegates units to `describeCrs`)
  - `src/varda/plotting/profile_plot.py` (`crsLabel` returns `describeCrs(crs).text`)
- Create: `docs/user-guide/coordinate-systems.md`. Modify: `docs/user-guide/README.md`.
- Test: `src/varda/all_images_view_list/_tests/test_image_properties_dialog.py`

**Interfaces** (spec §4.1): `CrsDescription` and `describeCrs(crs, transform=None, width=0, height=0)` exactly as in the spec.
- Fields come from the CRS's PROJ JSON:
  - The conversion method maps to projection names: "Equidistant Cylindrical" → "Equirectangular"; "Polar Stereographic (variant A/B)" and stereographic at lat_0 = ±90 → "Polar Stereographic (north|south)".
  - `centerLatitude` = the standard parallel (`lat_ts`) or latitude of origin.
  - The longitude direction comes from the geodetic CRS's longitude axis.
  - The longitude domain is "0..360" when the image's longitudes (the geographic extent, or `lon_0` > 180 for projected CRSs) lie in [180, 360]; otherwise "-180..180".
  - The latitude type is "planetographic" when the geodetic ellipsoid is flattened (inverse flattening ≠ 0), and "planetocentric" for spheres.
  - `pixelSizeM` = the ground size at the image center through `measure` once Task 8 exists. Until then, use the CRS's unit factor (projected) or π·a/180·cos φ (geographic). Task 8 replaces it with the geodesic value.
  - `authority` = `to_authority()`, else the first `IAU_2015`/EPSG code in `list_authority()` whose CRS satisfies `sameCrs`. Task 4 extends this; here, use PROJ `equals` only.
- `text` format: `"{body}, {surface}, {projection}{, center lat X°}{, lon_0 Y°}{, N m/px}"`, e.g. `"Mars, sphere R = 3396.19 km, Equirectangular, center lat 0°, 18 m/px"`.
  - With no CRS: `"No coordinate system (pixel coordinates)"`.
  - With an unknown body: "Unknown body (sphere R = 3000.00 km), …".

- [ ] **Step 1: Write failing tests** (`test_describe.py`): the exact `text` for each fixture.
  - `CRISM_MTRDR_WKT` at 18 m/px → `"Mars, sphere R = 3396.19 km, Equirectangular, center lat 0°, 18 m/px"`.
  - `HIRISE_POLAR_PROJ` → `"Mars, sphere R = 3376.20 km, Polar Stereographic (north), 1 m/px"`.
  - `LRO_WAC_PROJ`; `WGS84` → Earth, ellipsoid, "Geographic (lon/lat)".
  - `MOLA_MEGDR_PROJ` with a 0–360 transform → `longitudeDomain == "0..360"`.
  - `IAU_2015:49901` → `longitudeDirection == "west"`, `latitudeType == "planetographic"`.
  - In the dialog test: the "Coordinate system" group shows the sentence, and `assertNothingClipped` passes at 1280×800.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `describe.py`, the dialog group, and the `pixelSizeText`/`crsLabel` delegation.
- [ ] **Step 4: Run** the geo, grid, profile-plot and dialog tests → PASS.
- [ ] **Step 5: Docs.** Create `coordinate-systems.md` with these sections:
  - "What Varda shows about an image's coordinate system": each field and where it comes from.
  - "Conventions": IAU 2015 bodies and codes; planetocentric/planetographic; east/west; 0–360; cite Archinal et al. (2018) and Seidelmann et al. (2002).
  - "How Varda identifies the body": the order of evidence, and that an unknown body is reported as such.
  
  Add placeholders (headings only) for "Combining images", "How Varda measures" and "What Varda checks when an image opens", which later tasks fill. Link the page from the README. Add a CHANGELOG Added bullet.
- [ ] **Step 6: Commit** `git commit -m "geo: human-readable CRS descriptions; Image Properties shows the coordinate system"`.

### Task 4: CRS equivalence and compatibility

**Files:**
- Create: `src/varda/geo/equivalence.py`, `src/varda/geo/_tests/test_equivalence.py`
- Modify: `src/varda/geo/describe.py` (`authority` uses `sameCrs` against `list_authority()` candidates and the body's `IAU_2015` codes ×100 + {0,1,2,10,11,12,15,16,17,20,30,35,…}, the codes PROJ lists for the body)

**Interfaces** (spec §4.1):
- `class Compatibility(Enum)` with SAME, EQUIVALENT, SAME_BODY, DIFFERENT_BODY, UNKNOWN.
- `sameCrs(a, b) -> bool`: both None → True; one None → False.
- `compatibility(a, b) -> Compatibility`.
- `canTransform(a, b) -> str | None`: the reason is `"{bodyA} and {bodyB} are different bodies"` or `"…has no coordinate system"`.
- EQUIVALENT compares PROJ JSON conversion method and parameters (angles 1e-9°, lengths 1e-6 m), ellipsoid a and b (1e-3 m), prime meridian and axis directions/units, ignoring names.
- Unknown bodies: compare radii (1e-3 m). Equal → SAME_BODY, else DIFFERENT_BODY.

- [ ] **Step 1: Write failing tests:**
  - `test_gdal_wkt_equals_iau_code`: `sameCrs(CRISM_MTRDR_WKT, "IAU_2015:49910")`. PROJ alone says False today; this test documents it with `assert not CRS(CRISM_MTRDR_WKT).equals(CRS("IAU_2015:49910"))`.
  - `test_proj_string_and_wkt`: CRISM WKT vs `+proj=eqc +R=3396190 +units=m`.
  - `test_different_standard_parallel_is_not_same`: CTX `lat_ts=18.4` vs `lat_ts=0` → SAME_BODY.
  - `test_different_radius_is_not_same`: 3396190 vs 3389500, same method → SAME_BODY (not equivalent).
  - `test_mars_vs_wgs84_is_different_body`, and `canTransform` names both bodies.
  - `test_missing_crs_is_unknown`.
  - `test_describe_finds_iau_code`: `describeCrs(CRISM_MTRDR_WKT).authority == "IAU_2015:49910"`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Docs.** Fill "Combining images" in `coordinate-systems.md`: what "same" means, that names don't matter, and that different bodies are never combined.
- [ ] **Step 6: Commit** `git commit -m "geo: CRS equivalence that ignores names, and body-aware compatibility"`.

### Task 5: Grids, the mosaic and terrain overlays use equivalence and body guards

**Files:**
- Modify:
  - `src/varda/common/grid.py`: `sameGrid` uses `sameCrs`. `placementProblem` returns `canTransform`'s reason first. For geographic CRSs, overlap is tested with longitudes normalized (shift the target footprint by ±360 when that makes it overlap). `onGridOf` applies the same shift through the source transform. `gridDifference` says "a different body" for DIFFERENT_BODY.
  - `src/varda/mosaicking/model.py` (`MosaicProject.__init__`: `sameCrs` across the inputs instead of `str()`; the message names the projections with `describeCrs(...).text`)
  - `src/varda/terrain/overlays.py` (`toProjection`: `sameCrs` instead of rasterio `==`)
- Test: `src/varda/common/_tests/test_grid.py`, `src/varda/mosaicking/_tests/test_mosaic_core.py`, `src/varda/terrain/_tests/test_overlays.py`

**Interfaces:** consumes Task 4. No new public functions. `placementProblem` reasons gain `"{a} and {b} are different bodies"`.

- [ ] **Step 1: Write failing tests:**
  - A mosaic of two images with `CRISM_MTRDR_WKT` and `+proj=eqc +R=3396190 +units=m` CRSs (same grid) constructs. Today it raises `MosaicError`.
  - `sameGrid` is True for the same pair.
  - `placementProblem(WGS84 image, Mars image)` → "Earth and Mars are different bodies".
  - Two geographic Mars images, one with longitudes 257–258, the other −103 to −102 → `placementProblem` is None, and `onGridOf` places the values (the checksum equals a direct read).
  - `toProjection` with equivalent WKTs returns the input unchanged (the same array object).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the three test files, plus `src/varda/analysis/_tests/test_image_choices.py` → PASS.
- [ ] **Step 5: Docs.**
  - `mosaicking.md`: "same map projection" now means equivalent (names may differ).
  - `image-processing.md` "Resampled automatically": different bodies are never placed; 0–360 images overlap correctly.
  - CHANGELOG Fixed: "Mosaic refused images in the same projection written differently (e.g. by GDAL and ASP)."
- [ ] **Step 6: Commit** `git commit -m "Equivalent CRSs are one grid in masks, DEMs, mosaics and terrain overlays; different bodies are never placed"`.

### Task 6: Linking, ROI transfer and points never mix bodies

**Files:**
- Modify:
  - `src/varda/workspaces/linked_images/linking.py`: `linkedPoints` uses `sameCrs`. Images whose `canTransform` fails get NaN points, not an exception. `linkWarnings` adds `"{names}: on {body}, not {originBody}; not linked"`.
  - `src/varda/workspaces/crism_ratio/mapping.py`: `viewToData` already treats non-finite points as outside, so verify only.
  - `src/varda/rois/roi_collection.py`: `applyToImage` raises `ValueError(canTransform reason)` before `to_crs`, and skips `to_crs` when `sameCrs`.
  - `src/varda/points/point_collection.py`: `addPixel` converts a point from an image whose CRS isn't `sameCrs` with the collection's to the collection's CRS (a `Transformer` under `PROJ_LOCK`). On another body it raises `ValueError` with the reason. The point manager shows it in its status line.
  - `src/varda/points/point_manager_widget.py`: catch and show that `ValueError`.
- Test: `src/varda/workspaces/linked_images/_tests/test_linking.py`, `src/varda/rois/_tests/test_roi_collection.py`, `src/varda/points/_tests/test_point_collection.py`, `src/varda/workspaces/crism_ratio/_tests/` (mapping test)

**Interfaces:** consumes Task 4. `linkedPoints` keeps its signature; points that can't be mapped are `NaN`.

- [ ] **Step 1: Write failing tests:**
  - `test_cross_body_link_does_not_raise`: a WGS84 image and a CRISM image linked by map location → the WGS84 image's points are NaN, and `linkWarnings` names both bodies.
  - `test_equivalent_crs_links_without_transform`.
  - `test_apply_rois_to_other_body_refused`, with a message naming the bodies.
  - `test_points_from_two_mars_projections_share_one_crs`: a point saved from a `CTX_DEM_PROJ` image and one from a `CRISM_MTRDR_WKT` image at the same ground spot → identical exported coordinates (≤ 1e-6 m).
  - `test_point_from_other_body_refused`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the test files and `src/varda/workspaces/linked_images/_tests/test_linked_images_workspace.py` → PASS.
- [ ] **Step 5: Docs.**
  - `linked-images.md`: different bodies aren't linked, and how that's shown.
  - `viewing-and-measuring.md`: points are stored in the first image's CRS and converted from other images.
  - CHANGELOG Fixed: "Linking or transferring between images of different bodies (or with an Earth CRS on planetary data) no longer crashes; it says why it can't."
- [ ] **Step 6: Commit** `git commit -m "Linked images, ROI transfer and points refuse other bodies instead of crashing or mislabeling"`.

### Task 7: Distortion at a location and over an extent

**Files:**
- Create: `src/varda/geo/distortion.py`, `src/varda/geo/_tests/test_distortion.py`

**Interfaces:** spec §4.5: `SCALE_TIP`, `ANGLE_TIP_DEG`, `SCALE_STRONG`, `ANGLE_STRONG_DEG`, `Distortion`, `distortionAt`, `distortionAtPixels`, `DistortionSummary` and `distortionOver`, exactly as in the spec.
- Projected CRSs use `pyproj.Proj(crs).get_factors(lon, lat)` with lon/lat in `crs.geodetic_crs`.
- Geographic CRSs: h = 1 and k = cos(φ_c)/cos(φ) · (|a|/|e| pixel aspect). The image's pixels are treated as an equirectangular map with standard parallel φ_c.
- `distortionOver` samples a 9 × 9 grid of pixel centers (within `window` when given). It skips points PROJ can't invert, and returns None for an image without a CRS.
- `DistortionSummary.text` looks like `"up to 5.4% scale error (east–west) at 18.4°N"`. The direction is "east–west" when |k − 1| > |h − 1|, else "north–south". Angular distortion is appended when ω > `ANGLE_TIP_DEG`.

- [ ] **Step 1: Write failing tests:**
  - eqc `lat_ts=0` at 60° → k = 2.000 ± 1e-9, h = 1.000.
  - Polar stereographic (sphere, k₀ = 1) at 65° → k = 2/(1 + sin 65°) = 1.04915 ± 1e-5 (Snyder eq. 21-4).
  - TM `+proj=tmerc +lon_0=0 +R=3396190` at x = 300 km (lon = 300000/R rad, lat 0) → k = 1/√(1 − sin²(x/R)) = 1.00391 ± 1e-5.
  - `distortionOver(CRISM_MTRDR at 18.4°N)` → level "tip", text contains "east–west".
  - `distortionOver(CTX_DEM)` → level "ok".
  - Geographic `MOLA_MEGDR` at 60° → k = 2 relative to an equatorial φ_c.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -m "geo: projection distortion (scale factors, angular distortion) at points and over images"`.

### Task 8: Geodesic measurement core

**Files:**
- Create: `src/varda/geo/measure.py`, `src/varda/geo/_tests/test_measure.py`
- Modify: `src/varda/geo/describe.py` (`pixelSizeM` from `cumulativeDistance` across the center pixel)

**Interfaces** (spec §4.5): `Measurement`, `toLonLat`, `cumulativeDistance`, `lineLength`, `polygonArea` and `groundSpacing` (implement now and test; its first user is Phase 3), exactly as in the spec.
- Geodesics on `CRS(crs).geodetic_crs`'s ellipsoid: `Geod(a=…, b=…)`.
- **Reference value:** when the body's reference ellipsoid (`Body.referenceCrs()`) differs from the CRS's surface (a or b differs by > 1 m), the same geometry is measured on it.
  - If the CRS's surface is a sphere and the reference ellipsoid is flattened, latitudes are first converted planetocentric → planetographic with φg = atan((a/c)² tan φc).
  - `bodyModelDifference = |value − reference| / reference`.
- `polygonArea` densifies edges with `shapely.segmentize` at `densifyStep` (map units, the caller passes the pixel size) before converting. Holes are subtracted; MultiPolygons are summed.
- `Measurement.method` looks like `"geodesic (Karney 2013) on sphere R = 3396.19 km"`.
- Every `Measurement` carries `distortion = distortionOver(...)` of the geometry's bounding box when the caller passes a transform (keyword `transform: Affine | None = None`). This is for tips only; values never use it.

- [ ] **Step 1: Write failing tests:**
  - 1° of latitude on `IAU_2015:49900` → π·3396190/180 = 59,274.70 m (± 0.01 m), in both the geographic and the equirectangular CRSs.
  - A 10 km east–west line at 60°N in eqc `lat_ts=0` (map length 20 km) → 10,000 m ± 1 m.
  - A 1 km × 1 km ground square at 85°N in `HIRISE_POLAR_PROJ` (vertices placed through the CRS) → area 1.000e6 m² ± 0.1%.
  - `LRO_WAC_PROJ` → `reference is None`, `bodyModelDifference == 0`.
  - `HIRISE_POLAR_PROJ`, a 1 km north–south line at 85°N → `bodyModelDifference` between 0.011 and 0.013.
  - A polygon with a hole → area = outer − hole (± 0.01%).
  - `groundSpacing` of eqc `lat_ts=0` rows at 60° → east–west spacing = map pixel / 2.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS. Then run `test_describe.py`, updated so `pixelSizeM` is the geodesic value: the CRISM fixture's east–west ground size at 18.4°N is 18/1.054 m, and its `text` says "17 m/px (east–west), 18 m/px (north–south)" when they differ by > 1%.
- [ ] **Step 5: Docs.** Fill "How Varda measures" in `coordinate-systems.md`:
  - positions → lon/lat on the CRS's own surface;
  - Karney geodesics;
  - densification;
  - the body-model term (with the planetocentric→planetographic formula, Snyder eq. 3-28);
  - what isn't included (topography, georeferencing error);
  - a worked example (the 60°N line).
- [ ] **Step 6: Commit** `git commit -m "geo: geodesic lengths and areas for any CRS, with the body-model difference"`.

### Task 9: Line lengths and profiles are geodesic in every CRS

**Files:**
- Modify:
  - `src/varda/lines/profile.py`: `mapDistances` → `geo.measure.cumulativeDistance` for every georeferenced CRS. `lineLength` → `geo.measure.lineLength`, returning `(meters, "m")` as before. Add `lineMeasurement(vertices, transform, crs) -> Measurement | None`.
  - `src/varda/lines/line_collection.py` (`VardaLine.measurement(image)`)
  - `src/varda/lines/line_manager_widget.py`: the length cell's tooltip gives `Measurement.method`, the body-model difference when > 0.1%, and the distortion text when level ≠ "ok".
  - `src/varda/plotting/profile_plot.py`: CSV header `distance unit:` line → `"m (geodesic, Karney 2013, on sphere R = 3396.19 km; IAU 2015 reference ellipsoid differs by 0.2%)"`; add `projection distortion along the line: …`.
- Test: `src/varda/lines/_tests/test_profile.py`, `src/varda/lines/_tests/test_line_collection.py`, the profile plot CSV test (find it with `grep -rln "exportTo" src/varda/plotting/_tests`)

**Interfaces:** consumes Task 8. `Profile.distanceUnits` stays "m"/"px".

- [ ] **Step 1: Write failing tests:**
  - A profile along a 10 km east–west line at 60°N in eqc `lat_ts=0` → last distance 10,000 m ± 1 m (today 20,000).
  - A geographic-CRS profile is unchanged from today's values (± 1e-6 relative).
  - The CSV header contains "geodesic" and "sphere R = 3396.19 km" for the CRISM fixture.
  - The line manager tooltip contains the method.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest src/varda/lines src/varda/plotting -q` → PASS.
- [ ] **Step 5: Docs.**
  - `lines-and-profiles.md`: replace "Projected distance … planar" and the "Projected distances include the projection's scale error" limitation with the geodesic method, the body-model term and the remaining limits (horizontal, not along topography; georeferencing). Update the "How to report" bullet.
  - CHANGELOG Fixed: "Line lengths and profile distances in projected images were measured on the flat map (e.g. 2× too long east–west at 60° in a 0°-centered equirectangular map); they're now geodesic on the body."
- [ ] **Step 6: Commit** `git commit -m "Lines and profiles: geodesic distances in every map projection, stated in exports"`.

### Task 10: ROI areas and perimeters on the body

**Files:**
- Modify:
  - `src/varda/rois/roi_calculations.py`: `GEOMETRY_MEASURES` += `"area_body": ("Area on the body (km²)", "area_km2")` and `"perimeter_body": ("Perimeter on the body (km)", "perimeter_km")`, computed with `geo.measure.polygonArea` at a densify step of one pixel. The two `*_map` labels become "Area (map units², planar)" and "Perimeter (map units, planar)". `MAP_MEASURES` includes the new kinds.
  - `src/varda/rois/add_column_dialog.py` (or wherever measures are offered: `grep -rn GEOMETRY_MEASURES src/varda/rois`): the planar choices' tooltip gives `distortionOver(...).text` for the image.
- Test: `src/varda/rois/_tests/test_roi_calculations.py`, `src/varda/rois/_tests/test_add_column_dialog.py`

**Interfaces:** consumes Task 8. A column's value is a float in km² or km. NaN without georeferencing, with the reason in the column tooltip.

- [ ] **Step 1: Write failing tests:**
  - A 1 km × 1 km ground square ROI on `HIRISE_POLAR_PROJ` at 85°N → `area_body` 1.000 ± 0.001 km².
  - A 0.1° × 0.1° ROI on the geographic `MOLA_MEGDR` at 0°N → π²R²/180² × 0.01 × cos ≈ 35.13 km² ± 0.1% (compute exactly with `Geod` in the test).
  - `suggestName` → `"area_km2"`.
  - Planar columns are unchanged.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest src/varda/rois -q` → PASS.
- [ ] **Step 5: Docs.**
  - `viewing-and-measuring.md`: add the two measures (method, surface, body-model term, densification, limits).
  - Keep the planar caveat for the map-unit columns and point to the new ones.
  - Update "How to report".
  - CHANGELOG Added.
- [ ] **Step 6: Commit** `git commit -m "ROIs: area and perimeter on the body (geodesic), whatever the projection"`.

### Task 11: Label hints (PDS3, PDS4, ISIS)

**Files:**
- Create: `src/varda/geo/labels.py`, `src/varda/geo/_tests/test_labels.py`
- Modify: `src/varda/image_loading/data_sources/rasterio_data_source.py`. Keep the raw CRS text and the parse error when `_readCrs` fails: `rawCrsText: str | None` and `crsError: str | None` properties on `DataSource` (default None in `data_source.py`), passed through by `delegating_data_source.py`. Expose `gdalMetadata() -> dict[str, dict[str, str]]` for the `xml:PDS4` and `json:ISIS3` domains.

**Interfaces** (spec §4.2): `LabelHints` (plus `LabelHints.empty()`), and `labelHints(path, gdalMetadata={})`.
- **PDS3:** read the detached `.lbl`/`.LBL` beside `path`, or the attached label (the file's leading ODL up to `END`, at most 64 KiB). Use a minimal ODL reader: `KEY = VALUE` lines, quoted and multi-line values, `GROUP`/`OBJECT` nesting, and units in `<…>` stripped.
  - `TARGET_NAME` → `target`.
  - `IMAGE_MAP_PROJECTION`/`MAP_PROJECTION`: `COORDINATE_SYSTEM_NAME` → `latitudeType`; `POSITIVE_LONGITUDE_DIRECTION` → `longitudeDirection`; `WESTERNMOST_LONGITUDE`/`EASTERNMOST_LONGITUDE` > 180 → "0..360".
- **PDS4:** `target_identification/name`, and `cart:` `longitude_direction`, `latitude_type`, `bounding_coordinates`.
- **ISIS:** `Mapping` group `TargetName`, `LatitudeType`, `LongitudeDirection` and `LongitudeDomain`.
- `projectionGroup` holds the group's keys, upper-cased. Phase 2 uses them.
- **Never raises:** an unreadable label gives `LabelHints.empty()` with `source` stating why.

- [ ] **Step 1: Write failing tests** with label text written to `tmp_path`:
  - a CRISM MTRDR-style PDS3 label (`TARGET_NAME = MARS`, `MAP_PROJECTION_TYPE = "EQUIRECTANGULAR"`, `COORDINATE_SYSTEM_NAME = PLANETOCENTRIC`, `POSITIVE_LONGITUDE_DIRECTION = EAST`, `A_AXIS_RADIUS = 3396.19 <KM>`);
  - a HiRISE-style label (`POSITIVE_LONGITUDE_DIRECTION = EAST`, `WESTERNMOST_LONGITUDE = 352.1`) → domain "0..360";
  - an ISIS `json:ISIS3`-shaped dict (`{"IsisCube": {"Mapping": {"TargetName": "Moon", "LongitudeDirection": "PositiveWest", …}}}`) → Moon, west;
  - a PDS4 label snippet with `cart:longitude_direction` "Positive West";
  - a binary garbage file → `empty()`.
  - And `RasterioDataSource` on a GeoTIFF with a broken CRS (write one with rasterio and a bogus WKT through GDAL metadata, or monkeypatch `_src.crs`) → `crs is None`, with `crsError` set.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS. Run `uv run pytest src/varda/image_loading -q` → PASS.
- [ ] **Step 5: Commit** `git commit -m "geo: read target, latitude type and longitude conventions from PDS3, PDS4 and ISIS labels"`.

### Task 12: The CRS health check

**Files:**
- Create: `src/varda/geo/health.py`, `src/varda/geo/_tests/test_health.py`

**Interfaces** (spec §4.2): `Severity`, `CrsFix`, `CrsIssue`, `CrsHealth` and `checkCrs(...)`, exactly as in the spec.

| Code | Severity | Condition | Automatic fix? |
|---|---|---|---|
| `no-crs` | PROBLEM | identity-free transform with no CRS, or label `projectionGroup` non-empty and no CRS | none |
| `unreadable-crs` | PROBLEM | `rawCrsText` set, CRS None | none |
| `earth-crs-on-body` | PROBLEM | `identifyBody(crs)` is Earth, and `hints.target` names another body | **geographic CRS only:** `CrsFix(wkt=body.sphereCrs().to_wkt(), reason="Its CRS says Earth (WGS 84) but its label's target is Mars; read as degrees on the IAU 2015 Mars sphere", automatic=True)`. Projected: none, suggestion "Set coordinate system…" |
| `unknown-body` | TIP | body None, no candidates | none |
| `ambiguous-body` | TIP | body None, candidates | none |
| `lon-direction-mismatch` | PROBLEM | `hints.longitudeDirection` ≠ the description's direction | none |
| `latitude-type-mismatch` | TIP | `hints.latitudeType` = planetocentric on a flattened CRS, or planetographic on a sphere | none |
| `high-distortion` | TIP | `distortionOver` level ≠ "ok" | none. Suggestion "Measurements are geodesic and unaffected; shapes and slopes on this map are distorted — reproject (Processing → Reproject Image, coming in a later version) for figures and terrain" |
| `lon-0-360` | INFO | description domain "0..360" | none |

Messages are one line in plain words, for example:
- "Its map coordinates have no coordinate system, so Varda treats it as unreferenced."
- "Its CRS says Earth, but its label says Mars."

- [ ] **Step 1: Write failing tests:** one per row, plus:
  - the WGS84-tagged Mars fixture with `MARS` hints → exactly one PROBLEM, with `fix.automatic` and `CRS(fix.wkt)` `sameCrs` `IAU_2015:49900`;
  - the same without hints → no issues;
  - `CTX_DEM` → no issues;
  - `CRISM_MTRDR` → `high-distortion` only;
  - `checkCrs` never raises on any fixture, or on `crs="garbage"` (→ `unreadable-crs`).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Docs.** Fill "What Varda checks when an image opens" in `coordinate-systems.md`: the table above in user words, what's fixed automatically and why only that, and how to report a corrected CRS.
- [ ] **Step 6: Commit** `git commit -m "geo: CRS health check with one narrow automatic fix"`.

### Task 13: CRS overrides on images, in sessions and in provenance

**Files:**
- Modify:
  - `src/varda/common/entities.py`:
    - `EDITABLE_SINGLE` += `"crs"` (a WKT string; validated with `CRS.from_user_input` under `PROJ_LOCK`; `None` removes it).
    - `VardaRaster.crs` returns the override's CRS when set (built once per override).
    - `setMetadataOverrides` also pops `_geodeticTransformer` from `__dict__`.
    - Add `VardaRaster.crsHealth: CrsHealth | None` (a plain attribute, set by loading).
  - `src/varda/common/provenance.py`: `inputRecord` includes `crsFix` when the image has one. Add `"crsFix"` to `KEPT_METADATA_KEYS`.
  - `src/varda/image_loading/image_loading_service.py`: `loadRaster` runs `checkCrs` (worker thread; `varda.geo` locks internally), applies an automatic fix with `setMetadataOverrides({**overrides, "crs": wkt})`, sets `extraMetadata["crsFix"] = {"from": …, "to": …, "reason": …, "automatic": True}` and stores `crsHealth`.
  - `src/varda/session.py` and `src/varda/session_restore.py`: confirm that overrides already round-trip (`session.py:105`). On restore, an override from the session wins and the health check is rerun on the result.
- Test:
  - `src/varda/common/_tests/test_metadata_overrides.py`
  - `src/varda/_tests/test_session.py`
  - `src/varda/image_loading/_tests/test_provenance_in_files.py`
  - a new `src/varda/image_loading/_tests/test_crs_health_on_load.py`

**Interfaces:**
- `VardaRaster.crsHealth`.
- The override key `"crs"`.
- The `extraMetadata["crsFix"]` dict as above.
- Exported files carry the corrected CRS: `writeRaster` already writes `image.crs`.

- [ ] **Step 1: Write failing tests:**
  - Write a WGS84 GeoTIFF with a sibling PDS3 `.lbl` (`TARGET_NAME = MARS`) to `tmp_path`, and `loadRaster` it:
    - `image.crs` is equivalent to `IAU_2015:49900`;
    - `image.crsHealth.issues[0].code == "earth-crs-on-body"`;
    - `extraMetadata["crsFix"]["automatic"]`.
  - `pixelToLatLon` gives the same numbers as before the fix (degrees unchanged).
  - A session save/restore keeps the override.
  - `writeRaster` of an analysis result made from it → reopened file's CRS is Mars, and the provenance contains `crsFix`.
  - Setting `"crs"` to garbage raises `ValueError`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the four test files, then `uv run pytest src/varda/common src/varda/image_loading src/varda/_tests -q` → PASS.
- [ ] **Step 5: Docs.**
  - `sessions-and-saving.md`: corrected CRSs are saved with the session, and the file isn't changed.
  - `reporting-results.md`: the `crsFix` record.
  - CHANGELOG Added.
- [ ] **Step 6: Commit** `git commit -m "Images can carry a corrected CRS: applied automatically for planetary data tagged WGS 84, saved in sessions, recorded in provenance"`.

### Task 14: Showing CRS health (image list, Image Properties, status bar)

**Files:**
- Modify:
  - `src/varda/all_images_view_list/imageview_list.py`: the delegate paints an amber dot (`common/palette.py`'s warning color; add one if missing) at the thumbnail's top-right for TIP/PROBLEM. The item tooltip gains `"Map: " + first issue message`.
  - `src/varda/all_images_view_list/image_properties_dialog.py`: the "Coordinate system" group from Task 3 gains:
    - the issue list (message + suggestion), with an **Apply** button for issues with a non-automatic `fix`;
    - a note for an applied automatic fix, with **Undo** (removes the `"crs"` override and records `crsFix.undone`);
    - **Set coordinate system…**: a small dialog with a `QLineEdit` that accepts EPSG, IAU_2015, WKT or PROJ; a "Same as" `ElidingComboBox` of the open images; and live `describeCrs(...).text` with `canTransform` feedback.

    Applying sets the override, adds a `crsFix` with `automatic=False`, and reruns `checkCrs`.
  - The main window's or workspace's status bar (`src/varda/maingui.py`, or wherever images join the project; `app.py` `_onImagesChanged`): one `showMessage` per PROBLEM or automatic fix when an image is added, e.g. `"scene.tif: its CRS says Earth; read as Mars (from its label). Details in Image Properties."`. Each image id is shown only once per session (a set on the app).
- Test: `src/varda/all_images_view_list/_tests/test_imageview_list.py`, `test_image_properties_dialog.py`, `src/varda/_tests/test_maingui_image_list.py`

**Interfaces:** consumes Tasks 12–13. Add `SetCrsDialog(QDialog)` in `src/varda/all_images_view_list/set_crs_dialog.py` with `chosenWkt() -> str | None`.

- [ ] **Step 1: Write failing tests (pytest-qt):**
  - The WGS84-Mars image's list item tooltip contains "read as Mars", and the delegate reports a badge (expose `ImageItemDelegate.hasBadge(index) -> bool` for the test).
  - The Properties dialog lists the issue. **Undo** restores the WGS84 CRS and the badge stays (the issue is unresolved).
  - `SetCrsDialog`: entering `IAU_2015:49910` shows "Mars, sphere R = 3396.19 km, Equirectangular…". Entering `EPSG:4326` for a Mars image shows the cross-body warning, but it's allowed (the user may know better).
  - The status message appears once for two `_onImagesChanged` signals.
  - `assertNothingClipped` at 1280×800 for the dialog and the Properties dialog.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the GUI tests → PASS. Then do a manual check: `uv run varda`, open a WGS84-tagged Mars GeoTIFF with a label, and confirm the badge, the tooltip, one status message, and Properties at a 1280×800 window.
- [ ] **Step 5: Docs.** Add "Where you'll see it" and "Setting or correcting a coordinate system" to `coordinate-systems.md`. CHANGELOG Added.
- [ ] **Step 6: Commit** `git commit -m "Image list badge, Image Properties issues with Apply/Undo, and Set Coordinate System"`.

### Task 15: Viewport readout names the body and conventions

**Files:**
- Modify: `src/varda/image_rendering/raster_view/image_viewport.py` (`_buildReadoutText`)
- Create: `src/varda/geo/format.py` with `formatLonLat(lon: float, lat: float, description: CrsDescription) -> str` and `formatMapXY(x: float, y: float, description: CrsDescription) -> str`
- Test: `src/varda/geo/_tests/test_format.py`, `src/varda/image_rendering/raster_view/_tests/test_viewport_readout.py` (new)

**Interfaces:**
- `formatLonLat` gives e.g. `"lat 18.40312° N  lon 77.45120° E  (Mars, planetocentric)"`.
- In the 0..360 domain, longitudes are shown in [0, 360) with " (0–360)". With west direction, the longitude is shown as the CRS gives it, followed by "° W".
- `formatMapXY` gives `"xy 1234.50, -987.00 m"`.
- The viewport caches the image's `CrsDescription` (rebuilt on `sigMetadataChanged`).

- [ ] **Step 1: Write failing tests:**
  - CRISM fixture → `"… E  (Mars, planetocentric)"`;
  - MOLA 0–360 at lon 257.45 → `"lon 257.45000° E (0–360)"`;
  - `IAU_2015:49901` → "planetographic" and "° W";
  - WGS84 → "(Earth, …)";
  - the viewport readout for the CRISM image contains "Mars".
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Docs.** Update the readout description in `viewing-and-measuring.md` and `coordinate-systems.md`. CHANGELOG Added.
- [ ] **Step 6: Commit** `git commit -m "Pixel readout names the body, latitude type and longitude convention"`.

### Task 16: Phase 1 verification

**Files:** none new. Fixes go in the task they belong to.

- [ ] **Step 1:** `uv run pytest` (full suite) → PASS.
- [ ] **Step 2:** `uv run lint-imports` → KEPT, with `ignore_imports` unchanged from `origin/main` (`git diff origin/main -- pyproject.toml` shows only the `varda.geo` line).
- [ ] **Step 3:** `uv run ty check` → no new diagnostics compared with `origin/main`.
- [ ] **Step 4:** `uv run ruff format --check` and `uv run ruff check` on the touched files.
- [ ] **Step 5:** `grep -rn "RADII_M\|bodyOf\|geographicCrs\|str(image.crs)\|Geod(ellps=\"WGS84\")" src/varda` → nothing outside `varda/geo`.
- [ ] **Step 6:** Docs review. In `coordinate-systems.md`, every section is filled. Every number Varda now reports (line length, profile distance, ROI area and perimeter, pixel size) has a method, surface and error term in the guide. The README links the page.
- [ ] **Step 7:** Build check: `uv run pyinstaller Varda.spec` and `dist/Varda/Varda --self-test`. PROJ's `IAU_2015` database must be in the bundle: `self_test.py` gains `identifyBody("IAU_2015:49910").body.name == "Mars"`.
- [ ] **Step 8: Commit** any fixes, then open the PR (`gh pr create --repo varda-pro/Varda --head <branch>`, per the gh-account memory).

---

## Later phases (headings only)

### Phase 2: Reproject Image and CRSs built from labels
### Phase 3: Mosaic and 3D Terrain projections, vertical datum, scale-correct terrain analysis
### Phase 4: Areoid conversion, raster overlays across CRSs, workspace coordinate status line, project working CRS, removal of legacy georeferencing code
