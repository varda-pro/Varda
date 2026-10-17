# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Varda is a PyQt6 desktop application for visualizing and analyzing hyperspectral and multispectral image data. Users can load images, examine ROIs (regions of interest), view pixel spectra, and adjust band parameters. The app is designed to be very modular and extendible.

Right now, the image processing system (`image_processing/`) is incomplete and should generally be ignored for now; analyses live in `analysis/`.

## Scientific Documentation & Reproducibility

Varda is scientific software. **The goal: a user must be able to accurately describe, and report the uncertainties of, any process they run in Varda using only Varda's documentation and its outputs** — enough to write a correct methods section and defend it. Treat this as a definition of "done", not an afterthought.

For every analysis, filter, stretch, mosaicking step, transform, or other process:
- It has a user-guide page under `docs/user-guide/` (linked from `docs/user-guide/README.md` and surfaced via the Help menu, `help_info.py`).
- That page states: what the algorithm does, its **reference/citation**, every **parameter** (meaning, units, valid range, default), the **outputs** (meaning, units), and **how to assess uncertainty/quality** (residuals, RMSE, confidence intervals, error bands, SNR, or clearly stated limitations and assumptions).
- Outputs that carry uncertainty should **expose it** (e.g. residual/quality bands, reported metrics), not hide it.
- When you add or change a process, update its guide **in the same change**. A feature is not complete until a scientist could reproduce and report on it from the docs and outputs alone.

## Dev Tools
uv used for package management. ruff for formatting. ty for static type checking. pytest for unit testing.
When using Astral tools (uv, ty, ruff), invoke the relevant /astral:<skill> to ensure best practices are followed.

### Working across machines
Varda is developed across several machines and Claude Code sessions. **Before making changes, run `git status` and check the current branch** (and whether the tree is behind its remote) so you don't build on a stale checkout. Pull/rebase before starting new work, and don't assume the working tree matches a previous session.

## Commands
- **Install/sync deps:** `uv sync`
- **Run the app:** `uv run varda` (entry point `varda.main:main`)
- **Run all tests:** `uv run pytest`
- **Run one test file / test:** `uv run pytest src/varda/image_rendering/_tests/test_foo.py` or `... -k "test_name"`. Tests are colocated in `_tests/` folders as `test_*.py`.
- **Format:** `uv run ruff format` — **Lint:** `uv run ruff check` (add `--fix` to autofix)
- **Type check:** `uv run ty check`
- **Import contracts:** `uv run lint-imports` (a required CI check, with pytest, the app self-test and the citation file): the Qt-free modules listed in `pyproject.toml`'s import-linter contract (e.g. all of `varda.analysis`) must not reach PyQt6. Put GUI elsewhere; never grow the contract's `ignore_imports`.
- **Releasing:** a release PR bumps `pyproject.toml`, `CITATION.cff` (`version`, `date-released`), `uv.lock` and dates the CHANGELOG's Unreleased section; tag `vX.Y.Z` on the merged main and the build workflow publishes it. Every published release mints a Zenodo DOI, so publish exactly one release per version and no test releases. **After every release, run `/release-qa`** (`.claude/skills/release-qa`): five first-time-user personas use the released app on macOS, Windows and Linux, their problems are ranked, fixed in one pass, and rechecked.
- **Build standalone bundle:** `uv run pyinstaller Varda.spec`; check it with `dist/Varda/Varda --self-test` (`self_test.py`; release builds run it on every platform)
- **Release QA:** `uv run pytest -m qa qa/` drives the whole app as five first-time-user personas (screenshots + `findings.json` in `$VARDA_QA_OUT`); left out of plain `uv run pytest`. CI runs it on all three OSes (`release-qa.yml`). Guide: `docs/developer/release-qa.md`.

Requires Python 3.13. GUI tests use `pytest-qt`.
## Architecture

### Entry Point and Application Bootstrap

`src/varda/main.py` → `main()` → `createVarda()` (in `startup.py`): Performs all Application Initialization, Creates `QApplication`, constructs `VardaApplication` (`app.py`), wires the menu actions from the `_actions/` package, loads plugins, then launches `MainGUI`.

Right now, there is not a strongly defined "Project" data model; Because it's not yet clear what data will need to be stored in a project. So it's kept lightweight; just a list of opened images that `VardaApplication` stores. A user's open images, workspaces and ROIs are persisted to a JSON `.varda` session file (`session.py`, with `session_autosave.py` / `session_restore.py`); only file-backed images are saved (paths, not pixels).

We also try to do as much wiring as possible at this initialization stage, to reduce dependencies in the GUI code. (Eg. we define actions for creating new workspaces, including the code that sends new workspaces to the MainGUI to create tabs for them, so MainGUI doesn't need to link those things itself, just manage the tabs).

### Source Layout (`src/varda/`)

## Important

- **`image_loading/`** — Pluggable image loaders behind `ImageLoaderProtocol`. `ImageLoadingService` runs loading in background threads.
- **`image_rendering/`** — Systems related to visualization of images. `image_renderer.py` calculates an rgb image from based on settings. `stretch_algorithms.py` contains band stretch implementations (min-max, linear percentile, etc). `raster_view/` has the widgets to display the results from the image renderer, and other supporting classes for viewport tools, linked viewports, etc.
- **`rois/`** — ROI graphics item (`VardaROI`), management widgets, table model/view, property editor, statistics dialog. **Much of this is outdated and should be ignored. We will be rewriting it.**
- **`common/`** — Shared code that is used in many places: `entities.py` (important domain objects such as VardaRaster), `parameter.py` (typed settings as plain psygnal state, no Qt; groups inherit their bases' parameters; a `LabeledEnum`'s values are its UI labels) and `parameter_widgets.py` (the widgets that edit them: `parameterWidget`, `groupWidget`; register new parameter widgets with `@widgetFor`), `ui.py` (useful widgets and builders).
- **`terrain/`** + **`workspaces/terrain/`** — the 3D Terrain workspace: `terrain/scene.py` (Qt-free: DEM → decimated, 16-bit quantized height grid in meters; `pageHtml` builds one self-contained page with three.js inlined via an import map of data URLs, used both in the app and as the web export), `resources/terrain/` (vendored three.js r170 + OrbitControls, `viewer.js`, `viewer.html`), `terrain_view.py` (QWebEngineView; the page reports `varda:ready`/`varda:error` on its console; settings, camera and stills via runJavaScript). WebEngine must be imported before the QApplication (main.py, root conftest), its minor version must match PyQt6's, and pyproj objects must stay on the GUI thread (compute `groundSize` there).
- **`lines/`** — Lines (straight, polyline, freehand) and profiles: `profile.py` (Qt-free sampling along a line, distances in meters, geodesic for geographic CRSs), `line_collection.py` (Qt-free, image pixel coordinates, sessions, GIS export), `line_manager_widget.py`. The Draw Line tool is `image_rendering/raster_view/viewport_tools/line_tools.py`, the plot `plotting/profile_plot.py`, the overlays `workspaces/line_display.py`. Plan for 3D views and overlays: `docs/superpowers/specs/2026-10-15-3d-views-and-profiles-design.md`.
- **`analysis/`** — the Processing menu's analyses, registered in `registry.py` (menu order). Spatial tools that work band by band subclass `bandwise.BandwiseAnalysis` (All bands / One band, no-data filled while filtering); class map tools read and write the `Class` band + `classes`/`classColors` metadata (`class_tools.classRaster`).
- **`workspaces/`** — Workspace views: `dual_image_workspace/` (side-by-side comparison), `general_image_analysis/` (standard analysis view), `mosaic/` (the Mosaic workspace, the GUI over `mosaicking/`), `linked_images/` (N images linked by pixel or map location: one cursor's spectra and one line's profiles from every image; `linking.py` is the Qt-free mapping between images).
- **`mosaicking/`** — Qt-free mosaic core: `MosaicProject` (a non-destructive recipe: priority order, a 3×3 map correction per input, tie points), transform fitting (plain and RANSAC), the output grid, tiled rendering with a source-index band, and automatic alignment: `bands.py` (SNR band choice), `matching.py` (phase-correlation tiles, the Leprince 2007 phase-plane refinement re-implemented from the paper, never from COSI-Corr's code, an ORB fallback, displacement fields), `radiometry.py`, `seams.py`, `recipe.py` (the dependency-free recipe parts), and `backends/` (external tools: `asp.py` finds, downloads and runs the Ames Stereo Pipeline's image_align; `isis.py` finds ISIS and runs cam2map and equalizer; LoFTR and SIFT run in the PyTorch runtime's worker, `varda_torch_worker/matching.py`, injected as `MatchSettings.externalMatcher`). Design: `docs/superpowers/specs/2026-10-15-mosaicking-workspace-design.md`.
- **`crism/`** + **`crism_ui/`** — CRISM processing (TRR3 IR I/F → MTRDR-consistent, map-projected I/F with σ, backplanes and provenance). `crism/` is Qt-free: `readers.py`/`labels.py`/`products.py` (PDS products), `calibration.py` (finding WA/BP/AT files; fetching WA/AT), `pipeline.py` (`process`, four passes over the lines, atomic outputs) with its steps `photometry`, `despike`, `atmosphere` (+ learned artifact bases in `resources/artifacts`, epoch-gated), `destripe`, `smile`, `projection`, `noise` (σ), `recipe.py` (one `ParameterGroup` per step, JSON round trip), `writers.py`, `validation*.py`, and `cli.py` (`python -m varda.crism`, `Varda --crism`). `crism_ui/` is the Process and Calibration dialogs. Guide: `docs/user-guide/crism-processing.md`; design, evidence ledger and validation: `docs/superpowers/specs/2026-10-16-crism-processing-design.md`, `docs/superpowers/specs/crism/`.
- **`utilities/`** — Helpers: background work, signal utils, debug tools, etc. Run anything slow through `background.runInBackground` (results and failures come back on the GUI thread; wrap progress callbacks with `onMainThread`), never a hand-made QRunnable or QThread.
- **`plugins/`** + **`api.py`** — Plugin system (pluggy). Plugins are folders with `plugin.toml` installed into the app-data `plugins` folder (Plugins menu: Manage, Install from Folder/Zip); they import only `varda.api` (the stable, versioned plugin API) and add analyses (`registerAnalyses`) or file formats (`registerDataSources`). `plugin_manager.py` loads/enables/removes them; `app_sink.py` puts their contributions into the menus. Guide: `docs/plugins/authoring.md`. Design incl. AI-built plugins (Kindle a Feature): `docs/superpowers/specs/2026-10-14-ai-plugins-design.md`.
- **`kindling/`** — Kindle a Feature: `providers.py` (the AI behind a small Conversation Protocol: Anthropic, and OpenAI or any OpenAI-compatible server such as Ollama or LM Studio), `agent.py` (the tool-use loop and system prompt), `tools.py` (the model's tools, confined to the plugin's folder), `docs.py` (the authoring guide, a `varda.api` reference generated from the module, the examples; bundled from `docs/plugins`), `checks.py` + `check_worker.py` (the plugin runs in a separate Varda process, `Varda --check-plugin`, on a synthetic cube and a crop of the image; plus an advisory static scan), `ai_settings.py` (the provider, and a key per provider in the OS keychain via `keyring`), and the two dialogs. Tests use a scripted provider (`_tests/fakes.py`) and an in-memory keyring.
- **`maingui.py`** — `MainGUI` (QMainWindow): tabbed workspaces, child window management, docking.
- **`_actions/`** — Menu actions grouped by area (`_file_actions`, `_view_actions`, `_workspace_actions`, `_processing_actions`, `_plugin_actions`, `_help_actions`, `_debug_actions`), plus `_menu_ids.py` (`MENUBAR` layout). `startup.py` assembles these into the menu bar at bootstrap, keeping wiring out of the GUI.
- **`plotting/`** — Plot widgets (pyqtgraph): pixel spectra (`pixel_spectra_plot.py`), line profiles (`profile_plot.py`), library spectra and spectrum matching, plot export.
- **`points/`** — Saved pixel points (`point_collection.py`, Qt-free): which pixel of which image and its ground location, exportable to GIS; `point_manager_widget.py` is the GUI.
- **`catalogs/`** — Find-and-fetch: a mission-agnostic catalog interface (STAC, PDS ODE, footprint files) that lists/searches collections and downloads products. Catalogs register via plugins' `registerCatalogs`. Design: `docs/superpowers/specs/2026-10-15-find-and-fetch-design.md` (not yet on `main`: it's on the unmerged `docs/find-and-fetch-spec` branch).
- **`metadata_management/`** — `metadata_editor.py`: dialog for editing image metadata after loading.
- **`optional_features/`** — Processing > Optional Features: install/update/remove the PyTorch runtime (`torch_runtime.py`) and set up external backends (Ames Stereo Pipeline, ISIS).
- **`all_images_view_list/`** — The project-wide image list (view model, list view, per-image actions, image properties dialog).

## Incomplete/On-Hold

- **`image_processing/`** — Processing algorithms (decorrelation stretch, normalization). `ImageProcessRegistry` registers available algorithms. `process_controls/` provides UI controls.
    - image processing is on-hold for now, but will be important later.


## Key Patterns

- **Brand assets and rules:** `resources/brand/README.md`. Use the files there as-is, and load static SVGs with QSvgRenderer, never the CSS-animated ones.
- **Simplicity/Conciseness** is always preferred, and an emphasis on high code quality and maintainability. Declarative patterns are also preferred for this purpose.
- **Application-level Actions** are defined in the `_actions/` package and assembled into the MenuBar (`MENUBAR` in `_actions/_menu_ids.py`) by `startup.py` at bootstrap.
- **Qt signals/slots** are used throughout for reactive UI updates. psygnal can also be used to create signals and slots in code that doesn't directly depend on Qt.
- **Cross-platform & laptop layouts:** Varda runs on macOS, Windows and Linux, often on laptop screens (~1440×900 and smaller). UI must degrade gracefully at small sizes: set sensible minimum sizes, let panels and dialogs **resize, wrap or scroll rather than clip**, and prefer layouts over fixed geometry. Label text, combo boxes and buttons must never be cut off (e.g. docked panels in the Find Data workspace). Verify new/changed UI at laptop resolution before considering it done. Use the pieces in `common/ui.py`: `FlowLayout` for button/field rows (they wrap instead of clipping), `ElidingComboBox` for long names (full text in the tooltip), and `PanelScrollArea` for tall panels (scrolls down, never sideways); every `VardaDockWidget` already wraps its panel in one. Test layouts with `common/layout_audit.py` (`assertNothingClipped`) at 1280×800.
- **Numba** (`@njit`) may be used in performance-critical paths like stretch algorithms.

### Similar Projects

The design goals of Varda are similar to **ENVI Classic**, and **Napari**. Consider using them as references.

## Code Style and Conventions

- **American English** in all prose: docs, the changelog, UI text, comments and docstrings (color, center, gray, analyze, normalize, canceled). Data values that must match external tools stay as they are (e.g. PROJ's unit name "metre").
- **Use type hints** as much as possible. but NEVER USE "Any" as a type. If you cannot provide a type hint for some reason then don't put anything.
- **Avoid Lazy Importing**, as it worsens readability.
- **Variable/Function Naming:** When subclassing classes from Qt, use camelCase for variables and functions. Modules that are completely independent of Qt may use snake_case.
- **Tests** can be colocated with the modules they're testing in a subfolder called /\_tests, with filenames test\_\*.py
- **Package manager:** `uv`

## Note on Testing

Varda currently lacks much unit testing, due to GUI code being difficult to test.
Moving forward we should try to better seperate GUI and logic, and write unit tests for the logic code.
