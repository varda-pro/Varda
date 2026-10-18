# CRISM Ratio workspace, blandness checks, and ratio cubes — design

**Status:** design, 2026-10-17. Owner: Michael Phillips. Decisions marked
*(Michael)* came from him; *(builder's call)* items are left to the builder;
anything else is a design default proposed during brainstorming.

## Why

CRISM analysts assess a region by **column-matched ratioing**: a numerator
spectrum (the spot of interest) divided by a denominator spectrum from a
spectrally bland spot **in the same detector column**, which cancels the
column-dependent instrument artifacts. Varda's Ratio Explorer does this, but it
lives inside the General Image Analysis and Dual Image workspaces, where:

- only the ratio is plotted, so you can't judge whether the denominator is a
  good one;
- with column lock on, picking a new numerator leaves the old denominator
  behind, out of column;
- the plots don't keep fitting Y automatically, and Replace isn't the default;
- too many other panels compete for space.

The work this serves *(Michael)*: quickly assess what's in a region. You navigate
on a **browse image** (almost always a CRISM SR "summary parameter" product,
but any image that draws the eye) and take spectra from the **I/F cube**. You
click around fast; when a spectrum is good you save it to the ROI manager or the
Points manager and move on to fresh regions. The decision you make most often
is whether the denominator is **bland enough**.

Separately, people have asked Michael for **ratio cubes**: the I/F cube divided,
column by column, by a denominator spectrum per detector column, so that people
without Varda (e.g. in ENVI, where ratioing is slow) can use the ratio cube *in
place of* the I/F cube.

## Goals

1. A **CRISM Ratio workspace** built for the click-assess-save loop (layout
   below).
2. **Blandness checks** for a denominator: a generic default and
   target-specific presets *(Michael: "a default generic blandness metric, and
   then target specific ones")*.
3. **Ratio cube**: a Processing analysis that divides a cube by per-column
   denominators, with whole-column averaging or bland-pixel averaging.
4. **Collapsible panels** (edge tabs you click to pull back out to where they
   were), available in every workspace *(Michael asked for it in this workspace;
   making it general is a design default)*.

## Non-goals

- Replacing or removing the existing Ratio Explorer in General Image Analysis /
  Dual Image. It stays; it gains the two shared fixes (numerator/denominator
  visible, denominator follows the numerator in column) where they apply.
- Atmospheric or photometric correction, or making SR products. The workspace
  uses whatever browse product and I/F cube the user opens.
- Non-CRISM instruments for the column-specific parts. Column lock and the
  ratio cube need CRISM detector-column geometry; the rest of the workspace
  works with any pair of images.

## Anti-goals (a failure even if it works)

- A blandness verdict the user can't see the reasons for. Every check shows
  which feature failed and by how much.
- A ratio cube that can't be described in a methods section: its record must
  say how every column's denominator was made, from how many pixels, and which
  columns fell back.
- Clicking feels slow: plots must refresh and refit on every click without
  noticeable lag on a full FRT.

## The workspace

**Layout** *(Michael chose option C, with "windows re-sizeable and adjustable
like they are in other Varda workspaces")*: all panels are normal Varda docks
(resizable, movable, collapsible to edge tabs).

- **Browse view** and **I/F view** side by side, with two link modes
  *(Michael: "we should probably have a pixel-match mode and a geospatial mode.
  I think we should build in map-link from the get go")*:
  - **Pixel match**: browse and I/F on the same grid (an MTRDR SR product with
    its MTRDR I/F, or a CAT-made SR with its TRR3). Boxes copy across by pixel.
  - **Map link**: the browse image is anything georeferenced over the scene,
    e.g. a THEMIS image co-registered to the CRISM data in the Mosaic
    workspace. A click on either view is converted through map coordinates
    into I/F pixel space; boxes are defined in I/F pixel space (where spectra
    and column lock live) and reprojected for drawing on the browse view.
  Clicks are accepted on **both** views. Column lock always reads the
  **I/F cube's** geometry, whichever view was clicked. Reuse Dual Image and
  Linked Images linking (`workspaces/linked_images/linking.py`).
  The numerator and denominator boxes are drawn on both views.
- Three stacked plots sharing one wavelength axis: **Numerator** (I/F),
  **Denominator** (I/F, with the blandness readout), **Ratio** (with the
  library overlay picker from the spectral-library feature).
- ROI manager, Points manager and Image List start **collapsed** to edge tabs.
- Choosing images: a new-workspace dialog asks for the browse image and the I/F
  cube (defaults: an image whose name/product type looks like an SR product as
  browse, a TRR/TRDR/MTRDR I/F cube as data — *builder's call* on detection).

**The click loop:**

- ⌘/Ctrl-left-click: numerator box. ⌘/Ctrl-right-click: denominator box. Same
  box size control as the Ratio Explorer.
- **Column lock** (on by default here): placing a denominator snaps it to the
  numerator's detector column (existing `computeColumnLockedTranslation`); and
  **every new numerator moves the denominator** to the new numerator's column,
  keeping the denominator's row and size. If the lock can't be satisfied
  (strip doesn't reach that row), keep the denominator where it is and say so
  in the status bar.
- Every click refreshes all three plots in **Replace mode** and **refits Y**
  (all plots default to auto-range; a user zoom/stretch persists until the next
  click — *builder's call* whether a click always refits or only when the user
  hasn't stretched; recommend: always refit on a new click, since the loop is
  "look at a fresh spot").
- **S**: save the pair to the ROI manager (numerator and denominator ROIs, as
  the Ratio Explorer does). **P** (proposed): save the numerator spot to the
  Points manager. Both note the blandness verdict in the saved item's
  description.

## Blandness checks

Shown in the Denominator plot (a compact readout under or beside it) and used by
the ratio cube.

- **Generic (default):** over the analysis range, (a) the deepest absorption in
  the continuum-removed spectrum (upper convex hull, as Continuum Removal does)
  and (b) the continuum's slope. Each has a threshold; either can fail the
  denominator; both numbers are always shown.
- **Target presets** ("Looking for…"), each a list of summary-parameter checks
  computed with HyPyRameter (already in Varda) on the denominator spectrum, plus
  a slope check over a stated window. Starting set (definitions from
  Viviano-Beck et al. 2014, as HyPyRameter implements them):
  - Plagioclase: LCPINDEX2, HCPINDEX2 low (no pyroxene), slope over ~1.0–1.7 µm
    *(Michael: "if I'm looking for plagioclase, I need to make sure there is no
    LCP")*.
  - Olivine: OLINDEX3 low; pyroxene indices low.
  - Fe/Mg phyllosilicates: D2300, MIN2295_2480, BD1900r2 low.
  - Al phyllosilicates: MIN2250, BD1900r2 low.
  - Carbonates: BDCARB, MIN2345_2537 low.
  - Sulfates: SINDEX2, BD1900r2 low.
  - Hydrated (any): BD1900r2 low.
  - Custom: pick parameters and thresholds.
  *(Michael: for alteration targets, no *other* alteration minerals in the
  denominator, and no slopes "messing with where the minimum of a feature of
  interest appears to be".)* The exact parameter lists and default thresholds
  are a *builder's call* to be documented and justified in the user guide;
  thresholds must be editable.
- Readout: each check as ✓/✗ with its value and threshold; an overall verdict.
  Works without HyPyRameter for the generic check; presets say they need it if
  it isn't installed.

## Ratio cube (Processing → CRISM, or alongside the ratio tools)

Inputs: an I/F cube with CRISM column geometry: the DDR/DE backplane that
traces each pixel back to its original sensor-space column, found and read as
column lock already does (`crism_geometry.loadColumnGeometry`). This works on
unprojected TRR3 and on map-projected products (MTRDR) because the backplane
gives the sensor column of every map-projected pixel *(Michael: "without that
type of backplane data I don't know if we can do this on map projected data
... we already have this functionality built into the ROI column lock logic")*.
Refuse with a clear message when no backplane is found. A pixel's column is its
backplane sensor column (rounded to the nearest detector column), within its
strip/segment.

- **Method:**
  - *Whole column*: each detector column's denominator is the mean (or median —
    *builder's call*, document it) of all valid pixels in that column.
  - *Bland pixels*: only pixels that pass the chosen blandness check (generic or
    a preset) count toward their column's denominator. Single pixels are too
    noisy to judge, so each pixel is judged on the **mean of a short run of
    pixels in the same sensor column** (default 9 pixels along the column,
    adjustable), never across columns, which would mix in the column-dependent
    artifacts the ratio is meant to cancel *(Michael)*. In map-projected data,
    "in the same column" means the same backplane sensor column, nearest in
    along-track position. Thresholds for this mode have their own defaults
    (a run mean is noisier than a clicked box mean); the window and thresholds
    are recorded as part of the method.
- **Columns short on bland pixels** (fewer than a minimum count): fall back to
  bland pixels from the nearest neighboring columns within a small radius,
  then to the whole column; every fallback is **recorded and flagged**, never
  silent.
- **VNIR + IR products (joined, e.g. MTRDR):** ratio all bands, but the
  backplane gives the IR (L-detector) column, which only approximately matches
  the VNIR (S-detector) column; flag the VNIR bands as approximately
  column-matched in the record and the user guide *(Michael agreed)*.
- **Outputs:** the ratio cube (same bands, wavelengths, georeferencing, no-data
  and bad bands as the input; ENVI or GeoTIFF via the normal writer), plus a
  companion image: a mask of the pixels used as denominators and a per-pixel
  band giving each pixel's column's denominator pixel count. (Use the existing
  multi-output `Analysis.runAll`.)
- **Record (provenance + metadata, kept in files):** method, blandness check
  and thresholds, per-column denominator spectra, per-column pixel counts,
  fallback columns and how they were filled, and the geometry file used.
- **Uncertainty:** document that a ratio inherits both spectra's noise
  (σ_R/R from the numerator pixel and the column denominator's standard error),
  and output the per-column denominator standard deviation so users can
  propagate it.

## Collapsible panels (every workspace)

Use Qt-Advanced-Docking's auto-hide (PyQt6Ads 4.5 provides
`CDockManager.addAutoHideDockWidget` / `setAutoHideConfigFlag`): each dock gets
a control to collapse it into an edge tab; clicking the tab shows it as an
overlay; restoring puts it back where it was. Collapsed state is saved with the
session where workspaces already save layout. *Builder's call*: the exact
control (title-bar button vs context menu) as long as it's discoverable.

## Constraints

- Follow CLAUDE.md: Qt-free logic (blandness checks and ratio-cube math in
  `varda.analysis` or a new Qt-free module, listed in the import contract; GUI
  elsewhere); `lint-imports` stays KEPT and `ignore_imports` doesn't grow;
  declarative Parameters; `memoryFactor` for the ratio-cube analysis (it's
  measured by `test_memory_estimates.py`); the laptop-layout rule at
  1280×800; docs in the same change (Scientific Documentation standard:
  method, parameters with defaults and units, outputs, uncertainty, references).
- Reuse: Dual Image linking, the Ratio Explorer tool and `crism_geometry`
  column functions, region statistics, Continuum Removal's hull, HyPyRameter
  via the existing adapter, the spectral library overlay, the ROI and Points
  managers.

## Left to the builder

Detection of browse vs I/F images; the minimum bland-pixel count and the
neighbor-column fallback radius; whether a click always refits Y; mean vs
median per column; preset parameter lists and default thresholds (documented
and justified); the collapse control; file/module names; how the workspace is
listed in the Workspace menu.
