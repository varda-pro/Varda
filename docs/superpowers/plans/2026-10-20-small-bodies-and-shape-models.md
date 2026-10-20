# Small Bodies and Shape Models Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Varda knows irregular bodies, reads and shows plate shape models in 3D, and computes per-plate geophysics with density-bound uncertainty.

**Architecture:** A cited body table extends `varda.geo.bodies`; a new Qt-free package `varda.shapes` holds the mesh, readers, ray casting, plate data and gravity; a second three.js page (`resources/terrain/shape_viewer.js`) shows meshes inside the existing 3D Terrain workspace, which gains a Surface switch (DEM / Shape model) and a shape panel.

**Tech Stack:** numpy, numba, astropy (FITS), three.js r170 (vendored), PyQt6 WebEngine; SpiceyPy as the optional extra `spice`.

**Spec:** `docs/superpowers/specs/2026-10-20-small-bodies-and-shape-models-design.md`

## Global Constraints

- **Clean room:** implement only from the papers and format specs the spec cites; never copy or translate SBMT code (`~/Documents/Software/sbmt_re/decompiled/`).
- Every body value (radius, rotation period, density ± σ) carries its own citation and is checked against the paper; none come from memory or SBMT.
- `varda.shapes` and `varda.geo` are Qt-free and listed in the import-linter contract; never grow `ignore_imports`.
- Units: vertices in km; reported lengths m, areas m², accelerations m/s², potential J/kg, angles degrees.
- Coordinates: body-fixed; planetocentric latitude, east longitude 0–360°, radius = distance from the center of mass in km.
- SpiceyPy only via the optional extra `spice`; without it DSK reading reports "Install the spice extra to read DSK files" and nothing else changes.
- Slow work through `runInBackground`; new UI passes `assertNothingClipped` at 1280×800; connections via bound methods or `connectWhileAlive`.
- Docs in the same change: `docs/user-guide/small-bodies.md` (new, linked from the guide README and `help_info.py`), `3d-terrain.md`, `coordinate-systems.md`; CHANGELOG under the single `## Unreleased` headings.
- Real data (Bennu) lives in `~/Documents/Software/varda-aux/`, is never committed, and its tests skip when absent.

## Review Focus

1. **A plate file in meters, not km** (some OBJ exports): the reader compares the mesh's mean radius with the body's and warns "This model's size suggests meters; read as meters?" — test in Task 4.
2. **Inward-facing or mixed winding**: normals must point outward on a closed mesh (signed volume > 0 after orientation); a model with reversed winding is flipped, and the shape panel says so — test in Task 3.
3. **Plate data for another resolution of the same body** (same plate count, different hash, or different count): refused with a message naming both — test in Task 6.
4. **Geophysics on an open patch** (FITS X/Y/Z grid) or a mesh with holes: refused ("gravity needs a closed model") — test in Task 9.
5. **Picking a non-star-shaped body** (a two-lobe or overhanging mesh): the readout comes from the ray hit on the mesh, never from lat/lon → surface — test in Task 5 (two hits along one radial ray resolve to the nearest along the view ray).

---

### Task 1: The small-body table and `Body.shape`

**Files:**
- Create: `src/varda/geo/small_bodies.toml`, `src/varda/geo/_tests/test_small_bodies.py`
- Modify: `src/varda/geo/bodies.py`, `pyproject.toml` (package data, if needed)

**Interfaces:**
- Produces: `class BodyShape(StrEnum): SPHERE_LIKE = "sphere-like"; ELLIPSOID_LIKE = "ellipsoid-like"; IRREGULAR = "irregular"`; `Body.shape: BodyShape`; `Body.rotationPeriodH: float | None`; `Body.density: tuple[float, float] | None` (kg/m³, value and 1σ); `Body.sources: dict[str, str]` (field → citation); `bodyNamed("Bennu")` and `identifyBody(None, labelTarget="BENNU")` return the Bennu `Body`.

- [ ] **Step 1: Write the failing tests**

```python
def test_bodies_proj_lacks_are_known():
    for name, naif in [("Bennu", 2101955), ("Ryugu", 2162173), ("Didymos", 2065803),
                       ("Dimorphos", 920065803), ("Arrokoth", 2486958)]:
        body = bodyNamed(name)
        assert body is not None and body.naifId == naif
        assert body.shape is BodyShape.IRREGULAR
        assert body.sources["meanRadius"]  # every value is cited

def test_every_value_has_a_citation():
    for body in smallBodies():
        for field in ("meanRadius", "rotationPeriodH", "density"):
            if getattr(body, field) is not None:
                assert body.sources.get(field), (body.name, field)

def test_shape_classes():
    assert bodyNamed("Mars").shape is BodyShape.ELLIPSOID_LIKE
    assert bodyNamed("Moon").shape is BodyShape.SPHERE_LIKE
    assert bodyNamed("Eros").shape is BodyShape.IRREGULAR

def test_label_target_finds_bennu():
    assert identifyBody(None, labelTarget="(101955) BENNU").body.name == "Bennu"
```

- [ ] **Step 2: Run** `uv run pytest src/varda/geo/_tests/test_small_bodies.py -v` — FAIL.
- [ ] **Step 3: Implement.** The TOML holds one table per body: `naifId`, `meanRadius` (m), optional `equatorialRadius`/`polarRadius`, `rotationPeriodH`, `density = [value, sigma]`, `shape`, and `[<body>.sources]` with one citation string per field. Fill Bennu, Ryugu, Didymos, Dimorphos, Arrokoth, and density/rotation for Eros, Itokawa, Phobos, Vesta, Ceres, 67P, from the spec's citations or the mission shape papers; read each number from the paper. Shape class rule: `irregular` when listed in the TOML as irregular or when IAU 2015's three radii differ by more than 10 % of the mean (`(a−c)/R > 0.1`); `sphere-like` when a = c; else `ellipsoid-like`. Load with `tomllib` at import. NAIF IDs above: verify against NAIF's ID list; correct here if they differ.
- [ ] **Step 4: Run** — PASS; also `uv run pytest src/varda/geo -q`.
- [ ] **Step 5: Commit** `git commit -m "Bodies PROJ lacks (Bennu, Ryugu, Didymos, Dimorphos, Arrokoth), each value cited; Body.shape"`

### Task 2: Irregular bodies in health, measurements and recommendations

**Files:**
- Modify: `src/varda/geo/health.py`, `src/varda/geo/measure.py`, `src/varda/geo/recommend.py`, `docs/user-guide/coordinate-systems.md`
- Test: `src/varda/geo/_tests/test_irregular_bodies.py`

**Interfaces:**
- Consumes: `Body.shape` (Task 1).
- Produces: `CrsIssue` id `"irregular-body"` (Severity.WARNING); `Measurement.method` ends with `" — approximate (sphere model): irregular body"` and `bodyModelDifference` is `nan` on irregular bodies; `recommendProjection` returns no recommendation for `Purpose.TERRAIN`/`Purpose.MEASUREMENT` on them, with `note` "Irregular body: no map projection keeps distances or areas; use a shape model (3D Terrain → Shape model)".

- [ ] **Step 1: Failing tests**: an Eros-sphere CRS (`IAU_2015:243300`) gives the `irregular-body` issue; `lineLength` on it has the approximate label and `nan` difference; `recommendProjection(..., Purpose.MEASUREMENT)` on it refuses with the note above; Mars results unchanged (existing tests still pass).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** in the three modules; add "Small and irregular bodies" to `coordinate-systems.md` with the four reasons (spec §5.1, report §2.2) and the IAU convention.
- [ ] **Step 4: Run** `uv run pytest src/varda/geo -q` — PASS.
- [ ] **Step 5: Commit** `"Irregular bodies: a health issue, approximate measurement labels, no terrain or measurement projection"`

### Task 3: `ShapeModel`

**Files:**
- Create: `src/varda/shapes/__init__.py`, `src/varda/shapes/mesh.py`, `src/varda/shapes/_tests/test_mesh.py`, `src/varda/shapes/_tests/meshes.py` (synthetic builders: `icosphere(radiusKm, subdivisions)`, `cube(sideKm)`, `twoLobes()`, `openPatch()`)
- Modify: `pyproject.toml` (import-linter contract: add `varda.shapes`)

**Interfaces:**
- Produces: `@frozen class ShapeModel: vertices: np.ndarray  # (n,3) float64 km; plates: np.ndarray  # (m,3) int32; name: str; body: str | None` with cached properties `centroids`, `normals` (unit, outward), `areas` (km²), `meanEdgeKm`, `isClosed`, `volume` (km³, closed only), `contentHash` (sha256 of vertices and plates bytes, hex), `adjacency` (CSR or list); `oriented(model) -> tuple[ShapeModel, bool]` (flipped?); `latLonRadius(points: np.ndarray) -> np.ndarray` (n,3: planetocentric lat °, east lon 0–360 °, radius km); `decimated(model, maxPlates: int) -> ShapeModel` (vertex clustering; returns the model itself under the cap).

- [ ] **Step 1: Failing tests**: icosphere r=1 km, 5 subdivisions → `areas.sum()` within 0.5 % of 4π, `volume` within 0.5 % of 4/3 π, both below the analytic values; `isClosed`; `openPatch().isClosed is False`; a reversed-winding sphere → `oriented` flips it and returns True, then `volume > 0`; `latLonRadius([[0,-1,0]])` → `(0, 270, 1)`; `decimated(sphere, 1000).plates.shape[0] <= 1000` and still closed; hash changes when one vertex moves.
- [ ] **Step 2–4: Run, implement (numpy; numba only if a test is slow), run** — PASS; `uv run lint-imports` kept.
- [ ] **Step 5: Commit** `"varda.shapes: ShapeModel with areas, normals, volume, orientation, lat/lon/radius, decimation"`

### Task 4: Readers and writers

**Files:**
- Create: `src/varda/shapes/formats.py`, `src/varda/shapes/_tests/test_formats.py`
- Modify: `pyproject.toml` (`[project.optional-dependencies] spice = ["spiceypy>=6"]`)

**Interfaces:**
- Consumes: `ShapeModel` (Task 3); the FITS reader's plane names (`image_loading` FITS source, v0.11.0).
- Produces: `readShape(path: Path, body: str | None = None, unit: Literal["km","m"] | None = None) -> ShapeRead` with `@frozen class ShapeRead: model: ShapeModel; notes: list[str]; suggestedUnit: str | None`; formats by suffix: `.obj`, `.plt`/`.tab` (Gaskell PLT), `.icq`/`.txt` with ICQ header, `.fits`/`.fit` (X/Y/Z planes, fill skipped, open patch), `.bds` (DSK type 2, SpiceyPy); `UnsupportedShape(ValueError)`; `writeObj(model, path)`, `writeStl(model, path)` (reuse `terrain.solid.stlBytes` via a `Solid`).

- [ ] **Step 1: Failing tests** (write each format with a tiny writer in the test from the format spec): OBJ, PLT, ICQ q=2 (6·9 = 54 vertices, 2·6·4 = 48 plates), FITS X/Y/Z 3×3 with one fill cell → 4 fewer plates; round trip OBJ and STL keeps vertices to 1e-9 km; a 1000-scale OBJ with `body="Bennu"` → `suggestedUnit == "m"` and a note; `.bds` without SpiceyPy → `UnsupportedShape` whose message names the `spice` extra (monkeypatch import); unknown suffix → `UnsupportedShape`.
- [ ] **Step 2–4** — PASS.
- [ ] **Step 5: Commit** `"Shape readers: OBJ, Gaskell PLT and ICQ, FITS X/Y/Z, DSK (spice extra); OBJ and STL export"`

### Task 5: Ray casting

**Files:**
- Create: `src/varda/shapes/raycast.py`, `src/varda/shapes/_tests/test_raycast.py`

**Interfaces:**
- Consumes: `ShapeModel`.
- Produces: `class RayCaster: __init__(model: ShapeModel)`; `intersect(origins: np.ndarray, directions: np.ndarray) -> Hits` with `@frozen class Hits: plate: np.ndarray  # int32, -1 for miss; distance: np.ndarray; points: np.ndarray`; `closestPoint(points) -> Hits`. numba BVH (median split), Möller–Trumbore (Möller & Trumbore 1997).

- [ ] **Step 1: Failing tests**: 1000 random rays from outside an icosphere r=1 match the analytic sphere hit distance to the mesh's polygonal error (≤ 1 − cos(edge/2)); rays that miss return −1; `twoLobes()` along the x axis returns the nearest hit (Review Focus 5); 1M-plate sphere builds and casts 10k rays in under 5 s (mark `slow`).
- [ ] **Step 2–4** — PASS.
- [ ] **Step 5: Commit** `"Ray casting on shape models: numba BVH and Möller–Trumbore"`

### Task 6: Plate data

**Files:**
- Create: `src/varda/shapes/plate_data.py`, `src/varda/shapes/_tests/test_plate_data.py`

**Interfaces:**
- Produces: `@frozen class PlateData: name: str; unit: str; values: np.ndarray; sigma: np.ndarray | None; shapeHash: str; plateCount: int; provenance: dict`; `attach(model, data) -> None` raises `ValueError("<name> was made for another shape model (<count> plates, hash <8>…); this one has …")` on mismatch; `readPlateCsv(path, model)`, `writePlateCsv(path, model, datasets: list[PlateData])` (header `# shape: <name>`, `# shapeHash: <hex>`, `# plates: <n>`, `# provenance: <json>`, then `plate,<name> [<unit>],<name> sigma`); `readAltwgPlateTable(path, model)` (FITS table with a SIGMA column → `sigma`).

- [ ] **Step 1: Failing tests**: CSV round trip with sigma; mismatched hash and same count → error naming both (Review Focus 3); different count → error; an ALTWG-style table built with astropy reads values and sigma.
- [ ] **Step 2–4** — PASS.
- [ ] **Step 5: Commit** `"Plate data matched to its shape model by hash and count; CSV and ALTWG tables"`

### Task 7: The shape page

**Files:**
- Create: `resources/terrain/shape_viewer.js`, `resources/terrain/shape_viewer.html`, `src/varda/terrain/shape_scene.py`, `src/varda/terrain/_tests/test_shape_scene.py`
- Modify: `Varda.spec` only if `resources/terrain` isn't already bundled whole.

**Interfaces:**
- Consumes: `ShapeModel`, `PlateData`, three.js import map from `terrain/scene.py` (reuse its data-URL helpers).
- Produces: `@frozen class ShapeSettings: colorBy: str | None; colorMap: str; rgb: tuple[str,str,str] | None; sigmaView: Literal["off","color","fade"]; fadeAbove: float  # sigma/value; light: Literal["headlight","sun"]; sunBodyFrame: tuple[float,float,float]; maxPlates: int  # 512k default, 200k for web export`; `shapePageHtml(model, settings, plateData: dict[str, PlateData], camera: dict | None = None, panel: bool = False) -> str`; page console messages `varda:ready`, `varda:error <text>`, and `varda:pick {"plate": int, "xyz": [x,y,z]}`; JS functions `setSettings(json)`, `getCamera()`, `setCamera(json)`, `home()`.

- [ ] **Step 1: Failing tests** (Qt-free, on the HTML string): the page embeds quantized vertices and int32 plates as base64; a model above `maxPlates` is decimated and the page says so in its title note; per-plate colors encode the chosen colormap; `panel=False` export holds no `varda:` bridge calls beyond console logs.
- [ ] **Step 2–4** — PASS. Then a pytest-qt smoke test loading the page in `TerrainView` and waiting for `varda:ready` (pattern: existing terrain view tests).
- [ ] **Step 5: Commit** `"Shape page: three.js indexed mesh, plate colors and sigma views, picking"`

### Task 8: Shape mode in the 3D Terrain workspace

**Files:**
- Create: `src/varda/workspaces/terrain/shape_panel.py`, `src/varda/workspaces/terrain/_tests/test_shape_panel.py`
- Modify: `src/varda/workspaces/terrain/terrain_workspace.py` (a Surface switch, DEM / Shape model, that swaps the side panel and the page), `docs/user-guide/3d-terrain.md`, `docs/user-guide/small-bodies.md` (create: opening shapes, plate data, the readout), `docs/user-guide/README.md`, `src/varda/help_info.py`, `CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 3–7.
- Produces: `ShapePanel(QWidget)` with `openShape(path: Path)`, `addPlateData(data: PlateData)`, signal `pickedPlate(int)`; readout label text `"Plate {id} · X {x:.4f} Y {y:.4f} Z {z:.4f} km · lat {lat:.3f}° lon {lon:.3f}° r {r:.4f} km · {name} {value} ± {sigma} {unit}"`; Export menu: OBJ, STL, plate CSV, web page (capped), still image.

- [ ] **Step 1: Failing tests**: open an icosphere OBJ → plate count and mean edge shown; a reversed model shows "Normals were flipped to face outward" (Review Focus 2); a meters OBJ for Bennu asks to read as meters (auto-accept in test) (Review Focus 1); a simulated `varda:pick` updates the readout; mismatched plate CSV shows the Task 6 message, not a traceback; `assertNothingClipped` at 1280×800 and 1024×700; the reader runs in `runInBackground`.
- [ ] **Step 2–4** — PASS; full `uv run pytest -q`.
- [ ] **Step 5: Commit** `"3D Terrain: Shape model surface with plate coloring, sigma view, picking and export"`

### Task 9: Polyhedron gravity and geophysics core

**Files:**
- Create: `src/varda/shapes/gravity.py`, `src/varda/shapes/geophysics.py`, `src/varda/shapes/_tests/test_gravity.py`, `src/varda/shapes/_tests/test_geophysics.py`

**Interfaces:**
- Consumes: `ShapeModel`, `PlateData`, `Body.density`, `Body.rotationPeriodH`.
- Produces: `polyhedronGravity(model, densityKgM3: float, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]` (potential J/kg, acceleration m/s², Werner & Scheeres 1997 edge and face terms, numba); `@frozen class GeophysicsSettings: density: float; densitySigma: float; rotationPeriodH: float; uRef: Literal["area-weighted mean","minimum"]; tiltRadiusM: float; maxExactPlates: int = 200_000`; `geophysics(model, settings, progress) -> list[PlateData]` named `area`, `gravitational acceleration`, `centrifugal acceleration`, `total acceleration`, `potential`, `slope`, `geopotential elevation`, `tilt`, `slope (ρ−σ)`, `slope (ρ+σ)`, `geopotential elevation (ρ−σ)`, `geopotential elevation (ρ+σ)`; each `provenance` holds the settings, `meanEdgeKm`, and `"evaluatedOn"` (plate count actually used); open models raise `ValueError("Gravity needs a closed shape model; this one has holes or is a patch.")`.

- [ ] **Step 1: Failing tests**: uniform icosphere (r=1 km, ρ=2000) at points 1.5–3 km out → U = −GM/r and |g| = GM/r² within the mesh's volume error; inside the Laplacian is −4πGρ (finite differences); Werner's unit cube matches the analytic cube potential at a corner-diagonal point (Werner 1994) to 1e-10 relative; a spinning sphere's slope is 0 at the poles and the equator; `slope (ρ−σ) >= slope >= slope (ρ+σ)` on a spinning ellipsoid where centrifugal matters; open patch → the ValueError (Review Focus 4); a 300k-plate model reports `evaluatedOn < 300000`.
- [ ] **Step 2–4** — PASS.
- [ ] **Step 5: Commit** `"Shape geophysics: polyhedron gravity (Werner & Scheeres 1997), slope, geopotential elevation, tilt, density bounds"`

### Task 10: Shape Geophysics in the app, the guide and Bennu

**Files:**
- Create: `src/varda/workspaces/terrain/geophysics_dialog.py`, its test
- Modify: `shape_panel.py` (a "Shape Geophysics…" button; results added as plate data), `src/varda/_actions/_processing_actions.py` + `_menu_ids.py` (Processing → Shape Models → Shape Geophysics…, which opens 3D Terrain in Shape mode with the dialog), `docs/user-guide/small-bodies.md` (method, citations, every parameter with units/range/default, outputs, uncertainty: density bounds, constant density, resolution = mean edge length, U_ref, coarse evaluation above 200k plates; "Reporting your results" with a methods-sentence template), `CHANGELOG.md`
- Test: `src/varda/shapes/_tests/test_bennu.py` (skips without `~/Documents/Software/varda-aux/bennu/`)

**Interfaces:**
- Consumes: Tasks 8–9; defaults from `bodyNamed(model.body)`.

- [ ] **Step 1: Failing tests**: the dialog fills density, σ and period from Bennu's table entry and validates (density > 0, σ ≥ 0, period > 0); running on a small sphere adds the twelve plate datasets to the panel in the background; Bennu (when present): the model reads, is closed, its volume and mean radius are within 1 % of the cited paper's values, and geophysics on the low-resolution model gives slopes with mean and maximum recorded in the test log (no assertion on science values beyond 0–90°).
- [ ] **Step 2–4** — PASS; `uv run pytest -q`, `uv run lint-imports`, ruff, ty (no new diagnostics).
- [ ] **Step 5: Commit** `"Shape Geophysics in 3D Terrain and the Processing menu, with its guide"`

### Task 11: Verification

- [ ] Full suite, `lint-imports` (contract lists `varda.shapes`), ruff, ty versus `origin/main`, `uv run varda --self-test` plus a new self-test step "shape model": read a 2-subdivision icosphere OBJ written at test time, cast one ray, compute its volume.
- [ ] Docs: every number in `small-bodies.md` has its method, units, citation and uncertainty; README and Help links work.
- [ ] Manual check at 1280×800 with the Bennu model (when present); note memory use for the largest model tried in `small-bodies.md`.
- [ ] Commit `"Phase 5 (first part) verification: self-test shape step"`
