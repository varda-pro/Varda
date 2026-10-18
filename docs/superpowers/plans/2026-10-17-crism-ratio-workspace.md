# CRISM Ratio Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A CRISM Ratio workspace for fast numerator/denominator assessment (pixel-match and map-link browse images, three stacked plots, blandness checks, column lock that follows the numerator), plus a ratio-cube analysis built on the DE backplane.

**Architecture:** Qt-free cores (`analysis/blandness.py`, `analysis/ratio_cube.py`, `workspaces/crism_ratio/mapping.py`) carry the science and the coordinate mapping, tested without a GUI; the Ratio Explorer tool and controller gain small, shared hooks (follower, click mapper, components signal); the workspace composes existing Varda pieces (viewports, VardaDockWidget, VardaPlotWidget, ROI/Points managers).

**Tech Stack:** Python 3.13, PyQt6, PyQt6Ads 4.5 (auto-hide), pyqtgraph, numpy, rasterio/pyproj, HyPyRameter (optional extra), pytest + pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-17-crism-ratio-workspace-design.md`

## Global Constraints

- CLAUDE.md applies: uv, ruff format/check on touched files only, `uv run ty check` (no new diagnostics; compare with `git stash`), `uv run lint-imports` must stay KEPT and `ignore_imports` must not grow, `uv run pytest` full suite before each PR.
- Qt-free modules created here (`varda.analysis.blandness`, `varda.analysis.ratio_cube`, `varda.workspaces.crism_ratio.mapping`) must be added to the import-linter contract's `source_modules` in `pyproject.toml`.
- camelCase in Qt subclasses; snake_case allowed in Qt-free modules; type hints, never `Any`; American English.
- Every new analysis declares `memoryFactor`/`memoryFixed` and passes `analysis/_tests/test_memory_estimates.py` (it measures every registered analysis).
- Laptop layout: new widgets pass `common/layout_audit.assertNothingClipped` at 1280×800; use `FlowLayout`, `ElidingComboBox`, `VardaDockWidget`.
- Scientific Documentation standard: method, parameters (meaning, units, range, default), outputs, uncertainty/limitations, references, in the same change. Cite Viviano-Beck et al. (2014) for summary parameters (reference text already in `docs/user-guide/optional-features.md`).
- CHANGELOG.md: add to the existing `## Unreleased` → `### Added` / `### Fixed` (exactly one of each heading exists; never add headings).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **I/F cube without a DDR/DE backplane** in the workspace with column lock on: ratioing still works unlocked, and the status bar says column lock is unavailable (Task 7).
2. **Map-link click outside the I/F footprint**: no box is placed; the tool shows "Outside the I/F cube" (Task 5).
3. **Browse and I/F in different CRSs** (e.g. THEMIS in another projection): clicks still land on the right I/F pixel (Task 5).
4. **No column has enough bland pixels** (dark or featureful scene): the ratio cube still completes, every column is flagged with its fallback, and the description says how many fell back (Task 2/3).
5. **NaN bands in a spectrum** (bad bands, no-data) and **HyPyRameter not installed**: the generic check ignores NaN bands; preset checks report "needs HyPyRameter" instead of failing (Task 1, Task 6).

---

### Task 1: Blandness checks (Qt-free)

**Files:**
- Create: `src/varda/analysis/blandness.py`
- Test: `src/varda/analysis/_tests/test_blandness.py` (`varda.analysis` is already in the Qt-free contract)

**Interfaces:**
- Consumes: `analysis/continuum.py` `continuumRemovedCube(cube, wavelengthsNm)`; `analysis/hypyrameter_adapter.py` `computeParameterCube(cube, wavelengthsNm, names)`.
- Produces:
  - `DEFAULT_MAX_DEPTH = 0.02` (deepest absorption, 1 − continuum-removed value, unitless), `DEFAULT_MAX_SLOPE = 0.10` (|relative slope| per µm).
  - `@frozen CheckResult(name: str, value: float, limit: float, passed: bool)`
  - `@frozen BlandnessResult(checks: tuple[CheckResult, ...], note: str = "")` with property `passed: bool` (all checks passed; False if no checks).
  - `@frozen ParameterLimit(name: str, maximum: float)`; `@frozen Preset(name: str, limits: tuple[ParameterLimit, ...], slopeWindowNm: tuple[float, float], maxSlope: float = DEFAULT_MAX_SLOPE)`
  - `PRESETS: dict[str, Preset]` keyed by name: "Plagioclase", "Olivine", "Fe/Mg phyllosilicates", "Al phyllosilicates", "Carbonates", "Sulfates", "Hydrated (any)" — parameter lists from the spec; default maxima chosen and justified in the docs (starting points, user-editable).
  - `maxAbsorptionDepth(spectrum: np.ndarray, wavelengthsNm: np.ndarray, window: tuple[float, float] | None = None) -> float`
  - `relativeSlope(spectrum, wavelengthsNm, window=None) -> float` — least-squares slope of spectrum / its mean, per µm.
  - `genericBlandness(spectrum, wavelengthsNm, maxDepth=DEFAULT_MAX_DEPTH, maxSlope=DEFAULT_MAX_SLOPE, window=None) -> BlandnessResult` — checks named "Absorption depth" and "Slope".
  - `hypyrameterValues(spectrum, wavelengthsNm, names: Sequence[str]) -> np.ndarray` — `computeParameterCube` on a (1, 1, bands) cube.
  - `presetBlandness(spectrum, wavelengthsNm, preset: Preset, parameters: Callable[[np.ndarray, np.ndarray, Sequence[str]], np.ndarray] = hypyrameterValues) -> BlandnessResult` — one check per limit plus "Slope" over `slopeWindowNm`; if HyPyRameter is missing (ImportError) returns `BlandnessResult((), note="needs HyPyRameter")`.
  - `genericBlandMask(cube: np.ndarray, wavelengthsNm, maxDepth=..., maxSlope=..., window=None) -> np.ndarray` (rows, cols) bool; `presetBlandMask(cube, wavelengthsNm, preset, parameterCube=computeParameterCube) -> np.ndarray` bool. NaN pixels → False.

- [ ] **Step 1: Write failing tests** (synthetic spectra on 1000–2600 nm, 200 bands):
  - `test_a_flat_spectrum_is_bland`: `genericBlandness(np.full(200, 0.3), wl).passed is True`, depth ≈ 0, slope ≈ 0.
  - `test_an_absorption_fails_the_depth_check`: a 5 % Gaussian dip at 2300 nm → check "Absorption depth" value ≈ 0.05 (±0.005), `passed is False`.
  - `test_a_slope_fails_the_slope_check`: linear 0.30→0.39 over 1.0–2.6 µm → "Slope" value ≈ (0.09 / 1.6) / 0.345 ≈ 0.163 per µm (±0.002); with `maxSlope=0.1` it fails, with `maxSlope=0.2` it passes.
  - `test_nan_bands_are_ignored`: flat spectrum with 10 NaN bands → passed.
  - `test_preset_uses_the_named_parameters`: `presetBlandness(flat, wl, Preset("T", (ParameterLimit("LCPINDEX2", 0.02),), (1000, 1700)), parameters=fake)` where `fake` returns `[0.05]` → check "LCPINDEX2" value 0.05, limit 0.02, failed.
  - `test_preset_without_hypyrameter_says_so`: `parameters` raising ImportError → `checks == ()`, `note == "needs HyPyRameter"`.
  - `test_masks_match_the_per_spectrum_checks`: a 4×5 cube mixing flat and absorbing spectra → `genericBlandMask` equals `[genericBlandness(s).passed for each pixel]`; NaN pixel → False.
  - `test_presets_cover_the_spec_targets`: `set(PRESETS) == {"Plagioclase", "Olivine", "Fe/Mg phyllosilicates", "Al phyllosilicates", "Carbonates", "Sulfates", "Hydrated (any)"}` and Plagioclase's limits include LCPINDEX2 and HCPINDEX2.
- [ ] **Step 2: Run** `uv run pytest src/varda/analysis/_tests/test_blandness.py -q` → FAIL (module missing).
- [ ] **Step 3: Implement** `blandness.py` per Interfaces (depth from `continuumRemovedCube` on the finite bands, window-restricted; slope by `np.polyfit` on finite bands in µm).
- [ ] **Step 4: Run** the test file → PASS; `uv run lint-imports` → KEPT.
- [ ] **Step 5: Commit** `git commit -m "Blandness checks: generic and target presets (Qt-free)"`.

### Task 2: Ratio cube core (Qt-free)

**Files:**
- Create: `src/varda/analysis/ratio_cube.py`
- Test: `src/varda/analysis/_tests/test_ratio_cube.py`

**Interfaces:**
- Consumes: `image_loading/crism_geometry.py` `ColumnGeometry(ir_sample, target_id, segment_id)`.
- Produces:
  - `ColumnKey = tuple[int, int]` (strip id, detector column).
  - `columnKeys(geometry: ColumnGeometry) -> tuple[np.ndarray, np.ndarray]` — (strip, column) int64 arrays (rows, cols); strip = a dense id over (target_id, segment_id) pairs (0 when both absent); column = `rint(ir_sample)`; −1 in both where `ir_sample` is NaN.
  - `inColumnRunMeans(cube: np.ndarray, strip: np.ndarray, column: np.ndarray, run: int) -> np.ndarray` — each pixel's NaN-aware mean over the `run` pixels of its key nearest to it in row order (centered; truncated at the ends), never across keys.
  - `@frozen ColumnDenominators(spectra: dict[ColumnKey, np.ndarray], std: dict[ColumnKey, np.ndarray], counts: dict[ColumnKey, int], fallback: dict[ColumnKey, str])` — fallback values `"neighbors"` or `"whole column"`, absent = none.
  - `columnDenominators(cube, strip, column, use: np.ndarray | None, minCount: int, radius: int) -> ColumnDenominators` — mean (and std) over a key's pixels where `use` is True (all valid pixels when `use` is None); a key with fewer than `minCount` used pixels takes used pixels from columns ±1..±`radius` in the same strip (nearest first, until `minCount`), recorded as `"neighbors"`; if still short, all valid pixels of its own column, `"whole column"`.
  - `applyRatio(cube, strip, column, denominators: ColumnDenominators) -> np.ndarray` — float32; NaN where a pixel has no key or its denominator band is 0/NaN.

- [ ] **Step 1: Write failing tests** with a synthetic 20×6×8 cube whose column c is multiplied by an artifact factor `a_c` (per band), and a `ColumnGeometry` with `ir_sample = column index`:
  - `test_whole_column_ratio_removes_column_artifacts`: whole-column denominators → `applyRatio` result has no column dependence (std across columns of the per-column mean ratio < 1e-6) for a cube that is `artifact × uniform scene`.
  - `test_keys_follow_the_backplane_not_the_image_column`: map-projected-like geometry where `ir_sample` is shifted by +1 on odd rows → pixels group by `rint(ir_sample)`, verified by `columnKeys`.
  - `test_run_means_stay_in_column`: `inColumnRunMeans` with run=3 on a cube where each column has a distinct constant → output equals input (no mixing); and on a ramp along rows gives the centered 3-row mean.
  - `test_bland_only_uses_flagged_pixels`: `use` True on rows 0–9 only → denominator equals mean of rows 0–9, `counts == 10`.
  - `test_short_columns_borrow_from_neighbors_then_whole_column`: minCount=5, column 2 has 2 used pixels, columns 1/3 have 10 → fallback[(0, 2)] == "neighbors"; with radius=0 → "whole column".
  - `test_no_bland_pixels_anywhere_still_completes` (Review Focus 4): `use` all False → every key has fallback "whole column", result finite.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `ratio_cube.py` per Interfaces (group with `np.unique` over keys; vectorize per key with boolean indexing).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -m "Ratio cube core: column keys from the backplane, in-column runs, denominators with fallbacks"`.

### Task 3: Ratio Cube analysis

**Files:**
- Create: `src/varda/analysis/ratio_cube_analysis.py`
- Modify: `src/varda/analysis/registry.py` (register under a new category `"CRISM"`), `src/varda/common/provenance.py` (`KEPT_METADATA_KEYS` += `"ratioCube"`)
- Create: `docs/user-guide/crism-ratio.md` (Ratio cube section), link in `docs/user-guide/README.md`
- Modify: `CHANGELOG.md`
- Test: `src/varda/analysis/_tests/test_ratio_cube_analysis.py`

**Interfaces:**
- Consumes: Task 1 masks; Task 2 functions; `crism_geometry.loadColumnGeometry(path)`, `resolveGeometryFile(path)`; `analysis/rasters.readGoodBands`; `Analysis.runAll`.
- Produces: `class RatioCubeAnalysis(Analysis)`, `analysisId = "ratio_cube"`, `name = "Ratio Cube (Column Denominators)"`, `category = "CRISM"`; parameters: `method` (`RatioCubeMethod`: `WHOLE_COLUMN = "Whole column"`, `BLAND_PIXELS = "Bland pixels"`, default BLAND_PIXELS), `blandness` (choice "Generic" + `PRESETS` names, default "Generic"), `maxDepth` (0.02), `maxSlope` (0.10/µm), `runLength` (IntParameter 9, range 3–51, step 2), `minimumPixels` (20, 1–10000), `neighborRadius` (2, 0–10). `unavailableReason` says when no backplane is found. `runAll` returns `[ratio cube, companion]`: companion bands "Used as denominator" (0/1) and "Column denominator count". Metadata `ratioCube`: method, blandness name and thresholds, runLength, minimumPixels, neighborRadius, geometry file, VNIR flagged as approximately column-matched, and `columns`: list of `{strip, column, count, fallback, spectrum, std}`.

- [ ] **Step 1: Write failing tests** (monkeypatch `loadColumnGeometry`/`resolveGeometryFile` to return a synthetic geometry): ratio cube shape/bands/wavelengths equal the input's; companion counts match; metadata `columns` length = number of keys and fallbacks reported; description states how many columns fell back; `unavailableReason` non-empty without geometry; export round-trip keeps `ratioCube` (write GeoTIFF with `writeRaster`, reopen, compare).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; measure `memoryFactor` (run `test_memory_estimates.py`; set factor from the measured peak with ~25 % headroom).
- [ ] **Step 4: Run** the new test, `test_memory_estimates.py`, `lint-imports` → PASS/KEPT.
- [ ] **Step 5: Docs** `crism-ratio.md` "Ratio cube": what it is and why (column-dependent artifacts), methods, the in-column run (never across columns), fallbacks, VNIR approximation, outputs, uncertainty (σ_R/R from numerator and column denominator std/√n; the per-column std is in the record), how to report it. CHANGELOG Added.
- [ ] **Step 6: Commit** `git commit -m "Ratio Cube analysis: per-column denominators from the DE backplane"`.

### Task 4: Ratio Explorer shared fixes (follower, components)

**Files:**
- Modify: `src/varda/image_rendering/raster_view/viewport_tools/ratio_explorer_tool.py`, `src/varda/workspaces/ratio_explorer.py`, `src/varda/workspaces/general_image_analysis/general_image_analysis.py`, `src/varda/workspaces/dual_image_workspace/dual_image_workspace.py`
- Modify: `docs/user-guide/viewing-and-measuring.md` (Ratio Explorer), `CHANGELOG.md`
- Test: `src/varda/workspaces/_tests/test_ratio_explorer.py`, `src/varda/image_rendering/raster_view/viewport_tools/_tests/` (existing tool tests location)

**Interfaces:**
- Produces:
  - `DenominatorFollower = Callable[[np.ndarray, np.ndarray], np.ndarray | None]` (new numerator, current denominator) → new denominator or None (keep).
  - `RatioExplorerTool.setDenominatorFollower(follower: DenominatorFollower | None) -> None` — on a numerator click with an existing denominator, the denominator becomes `follower(...)` (or stays when it returns None).
  - `@frozen RatioComponents(image: VardaRaster, wavelengths: np.ndarray, numerator: np.ndarray, denominator: np.ndarray, ratio: np.ndarray, selection: RatioSelection)` in `workspaces/ratio_explorer.py`.
  - `RatioExplorerController.sigComponents = pyqtSignal(object)` emitting `RatioComponents` for each measured image on every complete selection; `bindTool` installs a follower that, when `lockColumn` is on, places a numerator-sized box in the numerator's column at the denominator's center row (`_placeDenominator(image, numerator, row, col)`), else returns None.
  - General Image Analysis and Dual Image add the numerator and denominator as extra curves (labels "Numerator …", "Denominator …") next to the ratio in their pixel plot.

- [ ] **Step 1: Write failing tests**: tool — with a follower returning a shifted box, a second ⌘-left-click moves the denominator; without one, it stays. Controller — with lock on and a synthetic `ColumnGeometry` (monkeypatched `loadColumnGeometry`), a new numerator in another column moves the denominator into that column at its old row; with lock off it stays; `sigComponents` carries numerator/denominator means equal to `computeRegionStatistics` means and ratio = their quotient.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** the Ratio Explorer and workspace tests → PASS.
- [ ] **Step 5: Docs/CHANGELOG** (Fixed: denominator follows the numerator in column; Added: numerator/denominator curves). **Commit** `git commit -m "Ratio Explorer: denominator follows the numerator in column; show both spectra"`.

### Task 5: Map-link click mapping

**Files:**
- Create: `src/varda/workspaces/crism_ratio/__init__.py`, `src/varda/workspaces/crism_ratio/mapping.py` (Qt-free; add to the import contract)
- Modify: `ratio_explorer_tool.py` (click mapper, box projector)
- Test: `src/varda/workspaces/crism_ratio/_tests/test_mapping.py`

**Interfaces:**
- Consumes: `workspaces/linked_images/linking.py` `Link`, `linkedPoints(images, source, points, link)`.
- Produces:
  - `viewToData(view: VardaRaster, data: VardaRaster, link: Link, xy: npt.ArrayLike) -> np.ndarray | None` — (n, 2) data-pixel coords, None if any point falls outside the data image.
  - `dataToView(view, data, link, polygon: np.ndarray) -> np.ndarray` — polygon corners mapped into view pixels.
  - `RatioExplorerTool.setClickMapper(mapper: Callable[[float, float], tuple[float, float] | None] | None)` — clicks are converted before placing boxes; None result → `showText("Outside the I/F cube", timeout=2000)` and no change.
  - `RatioExplorerTool.setBoxProjector(projector: Callable[[np.ndarray], np.ndarray] | None)` — boxes (always in data pixels) are drawn through it.

- [ ] **Step 1: Write failing tests**: two georeferenced synthetic rasters in **different CRSs** (e.g. an equirectangular Mars CRS and a sinusoidal one built with `pyproj.CRS`), where a known ground point maps to known pixels in each → `viewToData` returns the data pixel (±0.01 px) (Review Focus 3); a click outside the data footprint → None (Review Focus 2); `Link.PIXEL` → identity; `dataToView(viewToData(p)) ≈ p`. Tool: with a mapper, a click lands the box at the mapped data pixel; mapper returning None shows the text and leaves the selection.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; `lint-imports` KEPT.
- [ ] **Step 5: Commit** `git commit -m "Ratio Explorer tool: click mapping and box projection for map-linked views"`.

### Task 6: Ratio plots panel and blandness readout

**Files:**
- Create: `src/varda/workspaces/crism_ratio/ratio_plots.py`, `src/varda/workspaces/crism_ratio/blandness_readout.py`
- Test: `src/varda/workspaces/crism_ratio/_tests/test_ratio_plots.py`

**Interfaces:**
- Consumes: Task 1 (`genericBlandness`, `presetBlandness`, `PRESETS`, `BlandnessResult`); Task 4 `RatioComponents`; `plotting/plot.py` `VardaPlotWidget` (`fitToData()`), its library overlay.
- Produces:
  - `class BlandnessReadout(QWidget)`: a check combo ("Generic" + `PRESETS`), a table (Check · Value · Limit · ✓/✗) whose Limit cells are editable, an overall verdict label; `setSpectrum(wavelengthsNm: np.ndarray, spectrum: np.ndarray) -> BlandnessResult`; `sigVerdictChanged = pyqtSignal(object)` (BlandnessResult); `currentCheckName() -> str`.
  - `class RatioPlotsPanel(QWidget)`: three `VardaPlotWidget`s (Numerator, Denominator with the readout under it, Ratio) sharing the X range; `showComponents(c: RatioComponents) -> None` replaces each plot's curve and calls `fitToData()` on all three (every click refits Y, per the design); `numeratorPlot`, `denominatorPlot`, `ratioPlot`, `readout` attributes.

- [ ] **Step 1: Write failing tests**: `showComponents` twice → each plot holds exactly one data curve (Replace) and its Y range covers the new curve; readout on a flat spectrum shows all ✓; editing a Limit cell re-evaluates the verdict; choosing a preset with a fake missing HyPyRameter shows "needs HyPyRameter" (Review Focus 5); `assertNothingClipped(panel)` at 1280×800.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -m "CRISM ratio plots: numerator, denominator with blandness readout, ratio"`.

### Task 7: CRISM Ratio workspace

**Files:**
- Create: `src/varda/workspaces/crism_ratio/crism_ratio_workspace.py` (`CrismRatioWorkspace`, `NewCrismRatioDialog`)
- Modify: `src/varda/workspaces/workspace_kinds.py` (kind `"crism_ratio"` in `describeWorkspace` / `buildWorkspace`), `src/varda/_actions/_workspace_actions.py` (action `varda.workspace.new_crism_ratio`, title "New CRISM Ratio Workspace…")
- Modify: `docs/user-guide/crism-ratio.md` (workspace section), `CHANGELOG.md`
- Test: `src/varda/workspaces/crism_ratio/_tests/test_crism_ratio_workspace.py`

**Interfaces:**
- Consumes: Tasks 4–6; `RatioExplorerController`, `RatioExplorerTool`, `ROIManagerWidget`, `PointManagerWidget`/`PointCollection.addPixel(image, x, y, name=...)`, `VardaDockWidget`, `ads.CDockManager.addAutoHideDockWidget`, `workspaces/plot_libraries.py` session helpers.
- Produces: `CrismRatioWorkspace(browse: VardaRaster, data: VardaRaster, projectImages: MutableSequence[VardaRaster], link: Link)`; `sessionState() -> dict` (keys: `link`, `boxSize`, `lockColumn`, `blandness`, `plotLibrariesOff`); `restoreSessionState(state: dict)`.
  - Docks: Browse view, I/F view (side by side), Ratio plots panel; ROI Manager and Points Manager **auto-hidden** on the right at start (`dockManager.addAutoHideDockWidget(ads.SideBarLocation.SideBarRight, dock)`).
  - The tool is installed on **both** views; on the browse view in map-link mode with `setClickMapper` (Task 5) and `setBoxProjector`; spectra are always measured on `data`; column lock uses `data`'s geometry; lock on by default.
  - Keys: **S** saves the pair as ROIs (existing `saveCurrentBoxes`) with names ending in the verdict (e.g. "Ratio 3 numerator (denominator ✓ Generic)"); **P** saves the numerator center pixel to the Points Manager with the same suffix.
  - The dialog picks a browse image and an I/F image (defaults: a name containing "_SR" or "sr" product type as browse, else the first image; an image with a resolvable DDR as data) and the link (Pixel match / Map link; Map link required when the grids differ).

- [ ] **Step 1: Write failing tests**: building the workspace on two same-grid synthetic images (pixel match) and on two differently-georeferenced images (map link); a ⌘-click on the browse view measures spectra on `data` and fills all three plots; with no backplane, column lock reports unavailable in the status bar and ratioing still works (Review Focus 1); **S** adds two ROIs and **P** adds a point, each name carrying the verdict; ROI/Points docks start auto-hidden (`dockWidget.isAutoHide()`); session round-trip through `describeWorkspace`/`buildWorkspace` keeps link, box size, lock and blandness choice; `assertNothingClipped` at 1280×800.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** the workspace tests and the full suite → PASS.
- [ ] **Step 5: Docs** (`crism-ratio.md`: purpose, layout, link modes, the click loop, column lock following, blandness checks and how to read them, saving, reporting) and CHANGELOG Added. **Commit** `git commit -m "CRISM Ratio workspace: browse + I/F, stacked plots, blandness, column lock that follows"`.

### Task 8: Make collapsing panels discoverable

Edge-tab collapsing already exists in every workspace: `common/ui.py` sets `DefaultAutoHideConfig` globally (`AutoHideFeatureEnabled`, `DockAreaHasAutoHideButton`, `AutoHideHasMinimizeButton`), but users don't find the button.

**Files:**
- Modify: `docs/user-guide/sessions-and-saving.md` ("Workspace tabs" → add "Collapsing panels": the pin button on a panel's title bar collapses it to an edge tab; click the tab to show it; pin again to put it back), the in-plot hint in `plotting/plot.py` only if space allows (skip otherwise)
- Test: `src/varda/_tests/test_dock_autohide.py`

- [ ] **Step 1: Write a failing-if-regressed test**: for General Image Analysis, Dual Image, Linked Images, Mosaic, Spectral Hourglass, Find Data and CRISM Ratio, every dock area's title bar has a visible auto-hide button (`dockArea.titleBarButton(ads.TitleBarButton.TitleBarButtonAutoHide).isVisible()` (enum names verified in PyQt6Ads 4.5)), and `dock.toggleAutoHide()` then `dock.isAutoHide()` is True.
- [ ] **Step 2: Run** → PASS (documents existing behavior; it guards against a future config change).
- [ ] **Step 3: Docs + CHANGELOG** (Added: documented). **Commit** `git commit -m "Document collapsing panels to edge tabs; guard it with a test"`.
