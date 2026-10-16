# CRISM processing: from TRR3 I/F to MTRDR-consistent products — Design

Status: approved 2026-10-16, with the recommended option for every decision
D1–D8 (see "Decisions for Michael" at the end). FRT00005850's TRR3 + DDR and
CAT 7.4's aux files are downloaded for comparison (D3, D6). The Phase 1 plan
is `../plans/2026-10-16-crism-processing-phase1.md`.

Companion files:
- `crism/evidence-ledger.md`: one entry per algorithmic decision (source,
  confidence, the test that pins it).
- First measurements quoted here as **M1–M11** are in the ledger's
  "Measurements" section, with the scripts that produced them.

## Why

1. **The paper.** The Jezero western rim map fuses overlapping CRISM looks
   (roadmap spec F1–F6). The rim FRS observations (FRS00031442, FRS0002FE75,
   FRS00031260) have only TRR3 I/F plus DDR, no TER or MTRDR. FRS00031442
   holds the only rover ground truth in IR. In the fusion prototype,
   systematic differences between observations dominated the disagreement
   between looks (shape χ² ≈ 5.6, BD offsets up to 0.004). A homemade product
   that is processed differently from the MTRDRs would add another one. So
   the target is **consistency with MTRDR**, not with CAT.
2. **The long term.** Varda owns a modern CRISM pipeline, so nobody needs ENVI
   or IDL to work with CRISM data.

## What the research found (summary)

1. **The official recipe is fully documented.** DPSIS Appendix P1 (and Seelos
   et al. 2024, Icarus 419:115612, §5) gives every TER/MTRDR step with its
   math. The steps are:
   - PHT: I/F ÷ cos i, with i from a polynomial fit to the DDR areoid
     incidence;
   - ATM: a column-specific volcano scan chosen to minimize residuals over
     1947–2066 nm, plus CAT's ≥ 7.1 artifact patch;
   - RSC: column destriping;
   - EGN: geometric normalization, which needs EPFs;
   - ESC: smile correction;
   - XFM: the VNIR/IR join;
   - bad bands, then a GLT map projection.

   The only parts that live solely in CAT's IDL are how the artifact patch
   is scaled per pixel and the IKF kernel details.
2. **TRR3 IR I/F is already filtered** by the official crismhdf filter: RSC,
   then the IKF iterative Grubbs kernel filter, then RSC again (DPSIS
   App. N; the filter parameters are in every TRR3 label, M3). The MTRDRs
   start from this same filtered I/F, so starting there is the
   consistency-preserving choice. Leask et al. (2018) show the filter turns
   some spikes into false 1.9/2.1 µm absorptions: 50–100 pixels per image,
   clustered in certain columns. We detect and flag those; we don't hide
   them.
3. **The MTRDR grid is fully pinned down** (M1, `mtr_map.cat`):
   - a rolling equirectangular projection, x = R cos φ_P · λ, y = R φ;
   - the reference latitude φ_P is the equatorward 5° step (15° at Jezero);
   - R is the IAU 2000 ellipsoid radius at φ_P (3394839.8133 m);
   - pixels are 18 m (36 m for HRL);
   - the upper-left tie point is half a pixel west and north of the
     first-pixel center (WESTERNMOST_LONGITUDE, MAXIMUM_LATITUDE);
   - the origin is not snapped to a global lattice.

   The ENVI `.hdr` is right. GDAL's reading of the PDS3 and PDS4 labels puts
   the origin exactly one pixel off. The MTRDR DE lat/lon backplanes agree
   with the `.hdr` to 0.1–0.2 m (median).
4. **One wavelength vector for every MTRDR.** All six local MTRDRs share the
   same 489-band vector: 78 VNIR and 411 IR, with IR detector rows 7–438
   minus 173–193 (M2). This vector is the smile-correction target.
5. **Only six distinct volcano-scan transmissions are on PDS.** The 20
   local AT CDRs are bit-identical copies of 6 scans: 0A93E, 094B5, 08608,
   061C4, 09E04 and 06822 (M5). CAT's aux files have 17 scans, with artifact
   spectra (VS ADRs, version 9), but VS ADRs are not archived on PDS.
6. **Atmospheric correction can beat the classic scaling.** Measured on the
   three rim FRSs, with the same metric applied to the six local MTRDRs (M7,
   M10):

   | Method | CO2 residual (RMS over 1940–2100 nm) |
   |---|---|
   | Official MTRDRs (per-pixel median) | 0.0042–0.0103 |
   | Classic two-wavelength scaling | 0.0083–0.0089 |
   | Best scan, chosen *after* fitting a linearized wavelength-shift term | **0.0054–0.0060** |

   - Selection has to happen after the fit: the winning scan (08608) scored
     poorly without the shift term.
   - The residual left after fitting is mostly one spectrum shared by all
     three observations (correlation 0.75–0.93). That makes it an
     atmospheric or transmission artifact, not geology. Removing it leaves
     0.0030.
7. **No published Python code does the corrections.**
   - jezero-rim-mapping's hm-TER correction module is not in the public
     repo, and its projection uses the wrong sphere (the 98 px offset it
     papers over).
   - crism_ml and n2n4m are both permissively licensed (Apache-2.0 and MIT).
     The prompt's premise that n2n4m is unlicensed is wrong.
   - GDAL already reads every CRISM file type, but misses nodata (PDS3),
     wavelengths and DDR band names (M8).

## Scope of this spec

- **Phase 1 (P0 for the paper), fully specified:** IR (L) TRR3 I/F,
  1.0–2.6 µm → an MTRDR-consistent IR product, in sensor space and map
  projected, with σ and provenance.
- **Phases 2–4, outlined:**
  - Phase 2: ratioing and summary parameters with σ.
  - Phase 3: VNIR, the join, ATO and LMLP.
  - Phase 4: joint correction and denoising, learned denoising, EGN, DTM
    photometry, thermal correction.

### Non-goals for Phase 1

- **EGN.** The FRSs have no EPFs. The official late-mission fix is a
  "proxy EGN" built from 2,589 reference scenes (Seelos 2024 §7.7). That's
  Phase 4.
- **The VNIR (S) detector and the VNIR/IR join.** Phase 3.
- **Radiance → I/F from the RA product.** Phase 4 option (D2).
- **Thermal correction above 2.6 µm.** Bands above 2600 nm are written but
  flagged bad (`bbl = 0`).

## Architecture

### Packages

```
src/varda/crism/                 Qt-free core (added to import-linter source_modules)
    products.py                  product IDs: parse/format, companions (DDR, WA, CDR names)
    labels.py                    PDS3 (pvl) and PDS4 (ElementTree) label reading
    readers.py                   TRR3 / DDR / CDR (WA, SW, SB, BP, AT) / VS ADR / MTRDR-TER readers -> arrays + metadata
    calibration.py               CalibrationLibrary: find, select and fetch CDR/ADR files
    observation.py               CrismObservation: the files of one observation, lazily read
    noise.py                     per-band, per-column noise model (sigma0)
    photometry.py                PHT
    despike.py                   spikes against the noise model
    atmosphere.py                ATM: transmission scoring, per-column fit, per-pixel scale, residual (artifact) model
    destripe.py                  column correction that protects geology
    smile.py                     resampling to the MTRDR wavelength vector, ESC-style residual correction
    projection.py                MTRDR grid, nearest-neighbor (GLT) and area-weighted resampling, backplanes
    writers.py                   MTRDR-like ENVI (and optional COG), sigma companion, recipe/provenance sidecar
    recipe.py                    the recipe: one ParameterGroup per step, JSON round trip, versioned
    pipeline.py                  process(observation, recipe, outputFolder, progress) — chunked, multi-pass
    validation.py                comparison metrics (CO2 residual, shape ratios, parameter maps, geolocation)
    cli.py                       `python -m varda.crism`, `Varda --crism ...`
    resources/                   MTRDR standard wavelength table + bad-band list; learned artifact spectra (with provenance)
src/varda/crism_ui/              Qt: the "Process CRISM Observation…" dialog and its menu action
src/varda/image_loading/data_sources/crism_data_source.py   Varda data sources (TRR3, DDR, CDR) registered for .lbl/.xml
```

- **Ownership.** All of these are new files except one change in
  `main.py`/`_actions` (the menu entry and the `--crism` CLI dispatch), the
  import-linter list and `pyproject.toml`. Those are shared with the other
  session and touched once, at the end of the phase (see
  `VARDA_COORDINATION.md`).
- **Dependency.** One new pure-Python dependency, **`pvl`** (BSD-3), for
  PDS3 labels. GDAL is used for the pixels and `ElementTree` for PDS4. It
  will be announced in the coordination file before `uv.lock` changes.

### Data flow (one observation)

A pass is one read through the line chunks. A full FRT IR cube is about
450 × 640 × 438 float32 ≈ 0.5 GB, so it is processed in chunks of 64 lines
from a memory map. Peak memory is about 2 GB.

1. **Pass A: statistics** (a subset of lines plus column medians):
   - the noise model σ0(λ, x);
   - scoring the candidate transmissions;
   - the per-column wavelength shift δ(x);
   - the elevation model for the atmospheric scale β.
2. **Pass B: per pixel, chunked.** PHT → despike → ATM (β, residual model).
   Writes a float32 sensor-space intermediate and its σ, and accumulates the
   destripe statistics.
3. **Pass C: chunked.** Destripe → smile resampling to the MTRDR vector →
   writes the sensor-space product and its σ.
4. **Pass D: projection** to the MTRDR grid (nearest neighbor or area
   weighted), with backplanes. Writes the map-projected product, σ, DE and
   IN.

Every pass reports progress, and the GUI runs the whole job through
`background.runInBackground`.

### Interfaces

```python
from varda.crism import CrismObservation, Recipe, process

obs = CrismObservation.fromFolder(".../FRS00031442")      # or fromFiles(trr3=..., ddr=...)
result = process(obs, Recipe.default(), outputFolder, progress=callback)
result.mapProjected   # Path to the ENVI .img; result.sensorSpace, result.sigma, result.provenance
```

- **CLI:**
  - `python -m varda.crism process <folder|files> [--recipe r.json] [--out dir] [--sensor-space] [--cog]`
  - `... calibration <obs>` lists what is needed and what is local, and
    fetches the missing files.
  - `... validate <ours> <mtrdr>` writes the comparison report.
  - In the bundled app the same commands are `Varda --crism ...`, like
    `--check-plugin`.
- **GUI:** Processing > CRISM > **Process CRISM Observation…**
  - A list of observations (add a folder; batch).
  - One section per step: an on/off switch and its parameters, built with
    `groupWidget` from the recipe's ParameterGroups.
  - An output folder.
  - A run button with per-observation and per-pass progress.
  - Results open as images, with the σ companion attached.
  - Processing > CRISM > **Get Calibration Files…** shows the
    CalibrationLibrary status and fetches from PDS.
- **Readers:** TRR3, DDR and CDR open from `.lbl` or `.xml` in Varda like
  any image:
  - nodata is 65535;
  - wavelengths come from the WA CDR (median of the sweet-spot columns
    270–369), with the per-column map kept in `extraMetadata`;
  - band order is ascending in wavelength (the L detector is stored
    descending), and the 65.535 µm placeholder band is marked bad;
  - DDR band names come from the label;
  - the DDR is found as before (`crism_geometry.resolveGeometryFile`).
- **MTRDR/TER round trip:** the existing ENVI reader keeps 65535 as nodata,
  the wavelengths, `fwhm` and `bbl`. Phase 1 adds tests for this, plus the
  `de`/`in` companion lookup for MTRDR.

### The recipe and provenance

- **The recipe** is one `ParameterGroup` per step (`common/parameter.py`).
  It is serialized to JSON with a `recipeVersion` and written next to the
  output as `<stem>.recipe.json`. Re-running with that file reproduces the
  product.
- **The provenance sidecar** (`<stem>.provenance.json`) holds:
  - the Varda version;
  - every input product ID with its SHA-256;
  - every calibration file ID with its SHA-256;
  - per step, the parameters, the diagnostics and the wall time.

  The diagnostics include the transmission score table, the per-column β,
  δ and fit residual statistics, the destripe factor frame (an `.npz`
  sidecar), spike counts and the noise spectrum.

  Its summary goes into the ENVI `description` under `PROVENANCE_KEY` (the
  existing pattern).
- **A history string** in the ENVI header, in the spirit of CAT's symbols.
  Example: `varda history = PHT(areoid-poly)_DSK(5.0)_ATM(fit;08608;shift;elev;resid1)_RSC(hf)_ESC(linear)_MAP(eqc;18m;nn)`.

### σ (designed to fit roadmap F4)

- **The file.** Every I/F output has a σ companion `<stem>_sigma.img/.hdr`,
  float32, on the same grid and band vector. The main header records
  `varda sigma file`, `varda sigma kind = instrument-noise` and
  `varda sigma steps = ...`.
- **The reader** puts it in `extraMetadata["sigma"]` (path, kind, steps).
  F4 will later give `VardaRaster` a σ API that reads this convention;
  nothing in Phase 1 depends on that API.
- **σ is the random (noise) uncertainty only.** The systematic floor is
  reported separately, per band, in the provenance:
  - the transmission-selection spread (the score margin between the best
    and next-best scan, propagated);
  - the artifact-model amplitude uncertainty;
  - the destripe frame uncertainty.

  Mixing the two would repeat the prototype's finding that
  instrument-noise χ² understates disagreement (shape χ² ≈ 5.6).
- **Propagation.** Through linear steps, σ propagates exactly: photometry,
  despike replacement, the smile interpolation weights and area-weighted
  resampling. Through the nonlinear ATM scale, it propagates linearly via
  the fit covariance and is checked by Monte Carlo on a pixel sample.
  Details are under each step.

## Processing steps (Phase 1)

Notation: x is the detector column, t the line, λ the band; R is I/F, and
σ_R its 1σ.

### S0. Read and mask

- **Reads:**
  - TRR3 I/F (PDS4 `.xml` preferred, PDS3 `.lbl` with nodata forced to
    65535). It is BIL with an appended ROWNUM table (M3).
  - The DDR (BSQ, 14 bands).
  - The WA CDR named by `MRO:WAVELENGTH_FILE_NAME`.
- **Invalid samples:** 65535, values below 1e-6 (denormals occur, M3),
  non-scene columns (outside WA-valid columns 29–632) and bad detector
  elements from the BP CDR. These become a sample mask.
- **Masked rows:** slew and edge rows, using the step-size heuristic from
  jezero-rim-mapping (MIT, attributed).
- **Provenance:** the counts of each mask reason.

### S1. Noise model σ0

- **Estimate.** Robust second differences *along track* (same detector
  element, adjacent lines), so detector stripes cancel:
  d = R(t) − ½[R(t−1) + R(t+1)], with σ = 1.4826·MAD(d)/√1.5.
  The estimate is per band and per group of 16 columns, over pixels whose
  local spatial gradient is in the lowest 50%, which limits texture
  leakage.
- **Shot-noise model.** A signal-dependent fit, σ²(λ, x, R) = a(λ, x) +
  b(λ)·R, which is Kreisch et al. 2017's Poisson-like var ∝ μ plus a floor.
  The result is σ0 per sample.
- **What we know so far.** On FRS00031442 a plain horizontal
  shift-difference gives 0.00083–0.00091 I/F at I/F ≈ 0.1 (SNR 106–115,
  M6). That is an upper bound, because it includes texture. The TRR3 IKF
  filter alters the noise statistics, so σ0 is validated end to end:
  synthetically (χ²), and on real data against along-track split-half
  repeatability.
- **Improvement over CAT:** CAT has no noise model.

### S2. Photometry (PHT)

- **Correction:** R₁ = R₀ / cos i_s.
  - **Default (TER):** i_s is a least-squares 2-D polynomial fit to DDR
    band 1 (INA at areoid): i = c0 + c1 x + c2 x² + c3 t + c4 t². This
    removes the stair-steps of the 16 ppd areoid. The fit residual RMS is
    recorded.
  - **Option:** DDR band 6 (MOLA surface incidence), raw or smoothed.
- **Ready for Phase 4.** `photometry.py` takes an incidence (and emission)
  array from any source, so DTM-derived angles and a Lommel–Seeliger
  option (R·(μ0+μ)/μ0 normalized to a reference geometry) plug in without
  changing the pipeline.
- **σ:** σ₁ = σ₀ / cos i_s. The geometry is treated as exact; its
  uncertainty belongs to the systematic budget.
- **Improvement over CAT:** CAT divides by the raw DDR incidence, which
  carries the areoid stair-steps. We follow TER and record the polynomial.

### S3. Despike

- **Purpose.** Spikes left after the TRR3 IKF, and the Leask-type artifacts
  it creates.
- **Prediction.** Each sample is predicted from its neighbors: the median of
  the 8 spatial neighbors' spectra (scaled to the pixel's local continuum),
  combined with a 5-band spectral median. The prediction's own σ_p comes
  from neighbor scatter.
- **Test.** A sample is a spike when
  |R − R̂| / √(σ0² + σ_p²) > k (default k = 5, about 6e-7 two-sided under
  Gaussian noise) **and** it is a local extremum in λ (Parente 2008's BPS
  criterion). Requiring both keeps real narrow features.
- **Replacement.** A flagged sample is replaced by R̂, with σ = σ_p. A mask
  marks it: a per-pixel count band in IN, and a sparse (t, x, λ) table in
  the `.npz` sidecar.
- **Leask flag.** A report-only band counts flags in 1.85–2.15 µm, per
  pixel and per column.
- **Improvement over CAT:** CAT's CIRRUS despike smooths with
  data-independent thresholds. This flags against an explicit noise model,
  replaces only what it flags, and keeps a full record.

### S4. Atmospheric correction (ATM): the main improvement

**Model.** For one pixel p in column x, over the fit window
W = 1850–2200 nm in log space:

    ln R_p(λ) = Σ_k c_pk P_k(λ) + β_p ln T_x(λ + δ_x) + ε

- P_k is a smooth continuum basis (Legendre polynomials, degree 3 by
  default).
- T_x(λ) is the candidate transmission for column x. AT CDRs are stored per
  detector element, so scene column x uses AT column x directly, with no
  resampling. This is the TER rule.
- β_p is the per-pixel path scale.
- δ_x is a small per-column wavelength shift (thermal drift ≤ about 1 nm,
  McGuire 2009 §2). ln T_x(λ + δ) is ln T interpolated linearly along the
  column's own wavelengths (`atmosphere.shiftedLogTransmission`).
- δ is fitted by Gauss–Newton: each step is the linear fit of
  (c, β, η = βδ) with the Taylor term η ∂ln T_x/∂λ (central differences)
  about the current shift, which is then moved by η/β and ln T
  re-interpolated, until the step is below 1e-4 nm; a column that does not
  converge in 10 steps is invalid. One linearized step is biased at
  δ ≈ 0.8 nm (on the synthetic, residuals about twice the noise and β a
  median 12σ off).
- The fit is weighted by σ. The hydration-band core 1880–1945 nm gets zero
  weight (`HYDRATION_MASK_NM`): adsorbed water and hydrated minerals put a
  band there that the continuum cannot follow (on the synthetic's library
  surface it biased β by −4 to −5% and let a decoy transmission win). The
  continuum still spans all of W.
- A column's spectrum for Pass A is the mean of ln R over its lines (a
  geometric mean), over the lines complete in W, since the model is linear
  in ln R; a median is not (A1).

**A. Choosing the transmission (Pass A).**
1. For each candidate scan j (6 PDS AT scans, or 17 VS ADRs if available,
   D3) and each column, fit (c, β, η = βδ) to the column's robust mean
   spectrum over a central subset of lines (CAT 7.2's 11-line idea, widened
   to 64 lines for SNR).
2. The score S_j is the median over columns of the σ-weighted residual RMS
   over **1947–2066 nm**, the TER window.
3. Select j* = argmin S_j. Record the full score table (keyed by scan and
   source, `Transmission.key`), the margin to the runner-up, the margin
   relative to S_j*, and the fraction of columns where j* beats the
   runner-up, so a near tie is flagged.
4. Smooth δ_x across columns with a robust low-order polynomial in x, since
   the shift is an instrumental, smooth function of the column.

Joint selection with the shift is essential (M10).

**B. Per-pixel scale (Pass B).** With T_x and δ_x fixed, the model is linear
in (c_p, β_p).
- For each column, precompute H_x = (AᵀWA)⁻¹AᵀW once. Every pixel in the
  column is then one matrix product, which vectorizes over lines.
- σ_β comes from the fit covariance.

**C. Elevation-regularized β (default; per-pixel as an option).**
- Physics: β is the ratio of CO2 column to the scan's, so ln β ≈ a − z/H.
- Fit a and H to (DDR elevation, β_p) over the scene. H should come out
  near 10–11 km; out-of-range values fall back to per-pixel β, and this is
  recorded.
- Shrink each β_p toward the elevation model in proportion to its σ_β
  (empirical Bayes).
- Why: hydrated minerals put a strong 1.9 µm band inside W, which biases a
  free per-pixel β. Topography can't. The H estimate and the β-versus-z
  scatter are physical sanity checks reported in the provenance.

**D. Correction.** R' = R / exp(β_p ln T_x(λ + δ_x)), over all bands, with
ln T_x(λ + δ_x) from `shiftedLogTransmission` and δ_x the smoothed shift
(not the one-step Taylor form). σ′ propagates σ and the covariance of β
(and α) through ln R′ = (I − G) ln R, G the fit's gain, including the
covariance of β with the data it corrects (`atmosphere.correct`).

**E. Residual (artifact) model, in place of the opaque patch.**
- **What CAT's patch fixes** (Morgan 2011; Wiseman 2016 §2.1): the
  2 µm "bowl" (up to about 10% of continuum) and high-frequency "hash".
  Their cause is that CO2 lines saturate (curve of growth), so no single
  exponent of one empirical transmission reproduces another path. CAT
  stores an artifact spectrum per scan, (straight continuum − corrected
  reference) over 1764–2239 nm, and adds it scaled per pixel. The scaling
  is not published.
- **What we do:**
  1. Post-fit residuals are high-pass filtered in λ (orthogonal to the
     continuum basis), so smooth mineral shape is never touched.
  2. They are decomposed into a per-transmission artifact basis
     a_j,m(λ, x), m = 1…M (default M = 1).
  3. The basis is estimated across many observations processed with scan j,
     never from one scene alone. M7 shows it is shared between scenes
     (r = 0.75–0.93) and that removing it takes the residual from about
     0.006–0.008 to 0.003.
  4. Per pixel, the amplitude α_p,m is fitted jointly with β_p (it is
     linear), with the physically motivated prior α ∝ β (path-scaled, as
     CAT scales its patch).
- **Where the basis comes from:**
  - Phase 1 ships a basis learned from the observations at hand: the three
    FRSs, FRT00005850 and HRL000040FF TRR3s, plus any others processed. It
    is recorded as a calibration resource with its own provenance.
  - Leave-one-observation-out estimates test that it is not scene-specific.
  - If CAT's VS ADR artifact spectra are available (D3), they are a
    comparison: we show our basis matches what they fix.
- **Diagnostics** (IN bands): the fit residual RMS in the TER window,
  per pixel; β_p and σ_β; the artifact amplitude; and an "ATM spectral
  shift artifact" analog of the MTRDR IN band, for direct comparison.
- **σ.**
  - For fixed T, δ and the artifact basis, the corrected log spectrum is a
    linear function of the data, ln R' = (I − G_x) ln R, so its covariance
    is propagated exactly per column. It is diagonal-approximated in the
    output, and the dropped covariance is recorded.
  - σ_R' = R' · σ_lnR'.
  - The uncertainty from δ, the scan choice and the basis amplitude is
    estimated by Monte Carlo on 1,000 sample pixels and reported per band
    as a systematic term.
- **Improvement over CAT and TER:**
  - an objective, stated selection metric, applied after fitting the shift;
  - a continuous per-column shift instead of a pick from a list;
  - a per-pixel least-squares scale over the whole window instead of two
    wavelengths (lower σ_β than McGuire's v2 pair);
  - a physics-regularized β;
  - an explicit, cross-validated residual model in place of an opaque
    patch;
  - per-pixel uncertainties.

### S5. Destripe (RSC-like, after ATM as in TER)

- **What's left to fix.** TRR3 I/F is already RSC-filtered, so this
  removes residual striping. FRS column-median ratios against a 15-column
  running median scatter by 0.43–0.76% (M11); that is an upper bound,
  because it includes geology.
- **Bland pixels.** Low continuum-removed spectral variance and no
  significant band depths in 1.0–2.6 µm; Plebani's blandness score in
  Phase 2.
- **Column factors.** In continuum-normalized space, the factor is
  f(x, λ) = median over bland lines of R(x, t, λ) / R̃(x, t, λ). Here R̃
  is a cross-track LAD Legendre fit (degree 5), the TER RSC model, with
  ±1-column ratio accumulation in both directions, averaged.
- **Protecting geology.** Split ln f into a part that is smooth in λ, s,
  fitted per column with a low-order polynomial, and a high-frequency part,
  h.
  - **h** is detector-element-specific (pixel-to-pixel independent in λ).
    It is always removed.
  - **s** is removed only where it passes a split-half test: the same
    column offset is present, within its σ, in both the first and second
    halves of the lines. Real geology aligned with columns rarely spans the
    whole strip with the same spectral shape. That's checked
    synthetically with an injected column-aligned unit.
- **Records and σ.** The factor frame is written to the sidecar. The frame's
  bootstrap σ (over lines) is added in quadrature to σ, multiplicatively.
- **Improvement over CAT:** CAT's RST and CIRRUS destripe remove column
  means indiscriminately (the 2008 destriper subtracted column median −
  global median). Ours separates detector from geology, uses only bland
  pixels and reports what it removed.

### S6. Smile (ESC-style)

- **Target vector:** the MTRDR IR standard vector, 411 bands, shipped in
  `crism/resources/` from the wv table. It lies 0.2–0.3 nm above each band's
  minimum WA wavelength across columns (M2), which is where the sweet spot
  should be; Phase 1 checks it against the SW CDR. Matching it makes the
  output index-compatible with MTRDR IR bands.
- **Resampling.** Each column is resampled from WA(x, λ) to the target by
  **linear interpolation**. The shift is at most 1.6 bands (M4). Linear
  interpolation has exact two-tap σ propagation, σ² = Σ w²σ², and no
  ringing. A cubic spline is an option.
  - The resulting column-dependent variance reduction is carried in σ, and
    the band-to-band correlation it introduces is documented.
- **ESC residual step** (TER, optional, on by default):
  1. Per band, bin continuum-normalized spectra by the column's actual
     wavelength (0.125 nm bins for FRT/FRS, 0.25 nm for HRL).
  2. Fit a weighted line in λ.
  3. Correct to the reference λ.
- **Diagnostic:** the correlation of residual spectra with each column's
  wavelength offset (the "smile residual"), before and after.
- **Optional:** applying the ATM-fitted δ_x to WA before resampling. It is
  off in Phase 1 because δ is relative to the scan. The validation decides
  whether to turn it on.
- **Improvement over CAT:** CAT applies no smile correction to its outputs.
  Ours resamples to the MTRDR vector with propagated σ.

### S7. Bad bands

- The output band vector is the MTRDR IR vector.
- `bbl = 0` for the MTRDR bad-band rows (already excluded from the vector),
  for bands above 2600 nm (no thermal correction in Phase 1), and for any
  band whose valid-sample fraction is below 50%.
- `fwhm` comes from the MTRDR wv table, the target vector's own values,
  so the output carries the same FWHM as MTRDR.

### S8. Map projection

- **Grid (MTRDR rule, M1):**
  - **Projection:** rolling equirectangular, φ_P = the equatorward 5°
    multiple of the observation's center latitude, R = R(φ_P) on the
    IAU 2000 ellipsoid, central meridian 0. Beyond ±65°, polar stereographic
    on the pole, as in `mtr_map.cat`.
  - **Grid size and origin:** 18 m pixels (36 m for HRL/HRS, D5). The tie
    point is x(W) − s/2, y(N) + s/2, where W and N are the extreme DDR
    pixel centers, rounded to 0.01 m.
  - **Lat/lon source:** the DDR lat/lon is the IR geometry of the row
    nearest 2300 nm.
  - **Option:** "snap to a global lattice" (an origin at integer multiples
    of s), so that different observations share pixel edges for fusion.
    Off by default, to match MTRDR.
- **Resampling:**
  - **Default:** nearest neighbor, equivalent to the GLT. For each output
    pixel center, take the nearest DDR pixel center (a KD-tree in projected
    coordinates), and leave the pixel empty beyond 0.75·s·√2.
    Source-sample and source-line backplanes are written, so every pixel
    traces back to sensor space.
  - **Option: area weighted.** Each source pixel is a quadrilateral from
    its neighbors' midpoints, and weights are overlap fractions.
    σ² = Σw²σ² / (Σw)². A look-count band is added.
- **CRS** is written exactly as MTRDR writes it: the
  `D_Mars_2000_IAU_IAG_custom_sphere_latNN` WKT and the ENVI `map info`.
- **Improvement over CAT:** CAT 7.4 already matches MTRDR. Ours adds
  traceability backplanes, an area-weighted option with σ, and optional
  lattice snapping.

### S9. Outputs

All outputs are ENVI, BSQ, float32, with 65535 as nodata and MTRDR-style
headers (`map info`, `coordinate system string`, `wavelength`, `fwhm`,
`bbl`, `data ignore value`, `default bands`). Names avoid any official
product type:

| File | Content |
|---|---|
| `<obs>_<ctr>_if<mac>l_vmtr.img/.hdr` | Map-projected corrected I/F, 411 IR bands on the MTRDR vector |
| `..._vmtr_sigma.img` | 1σ, same grid and bands |
| `..._de<mac>l_vmtr.img` | The 14 DDR backplanes, projected (like MTRDR DE) |
| `..._in<mac>l_vmtr.img` | Source sample, source line, β, σ_β, ATM residual RMS, artifact amplitude, spike count, Leask flag, look count |
| `..._if<mac>l_vter.img` (+ `_sigma`) | Optional sensor-space product (TER-like), with the DDR as geometry |
| `.recipe.json`, `.provenance.json`, `.diagnostics.npz` | Recipe, provenance, frames and tables |

- COG export is optional, through rasterio's COG driver.
- The fusion prototype reads MTRDR IR by wavelength (1047–2596 nm), so it
  ingests `vmtr` unchanged.

## Calibration files

| File | Needed for | Source (default first) |
|---|---|---|
| WA CDR (L) | wavelengths, smile | PDS EDR volume `mrocr_0001/cdr/wa/`; already local for the TRR3s' WA 490947778566 |
| AT CDR (L) | ATM | PDS `mrocr_0001/cdr/at/` (6 distinct scans; all local) |
| SW CDR | target vector check | PDS `cdr/sw` |
| BP CDR | bad elements | PDS `cdr/bp` (named in the TRR3 SOURCE_PRODUCT_ID) |
| VS ADRs (17 scans + artifact spectra) | optional ATM candidates, comparison | CAT 7.4 aux zip (513 MB; not on PDS) — **D3** |
| CAT `obs_info` (precomputed VS choices) | comparison only | CAT aux zip — **D3** |

- **Search order.** `CalibrationLibrary` searches, in order:
  1. a user folder;
  2. `CRISM_CDR/` next to the data (this is how the local data is laid
     out);
  3. Varda's app-data cache;
  4. a CAT `aux_files` folder, if the user points Varda at one.
- **Fetching.** Missing PDS files are fetched over HTTP with the existing
  `catalogs/http`, and their SHA-256 is recorded.
- **Selection rules.** Sensor, binning, wavelength filter and the SCLK
  validity period. For an observation after the last period (all 2014 FRSs
  postdate the 2014-01-09 end of the last AT period, M5), the latest period
  is used and the extrapolation is recorded. Transmission scoring (S4.A)
  makes the choice robust anyway.

## Validation design (Phase 1 definition of done)

Tolerances come from the first measurements (M1–M11) or from each
estimator's own predicted uncertainty, never from round numbers. All of them
are **proposals for D4**.

### V1. Synthetic cube (fast; in `crism/_tests/`)

The cube is 64 lines × 640 columns × 438 bands, built as follows:
- surface spectra (USGS splib07 minerals plus a bland basalt) on a map of
  units, including one column-aligned unit that spans all lines;
- × T_x(λ + δ_x)^β_p, using a real AT CDR, a smooth δ_x of ±0.8 nm and β
  following a synthetic elevation (H = 11 km) plus scatter;
- × an injected artifact spectrum;
- × stripes f(x, λ): high-frequency at 0.5% plus a smooth part;
- + spikes at 5–20 σ, 1e-4 per sample;
- + Gaussian noise with σ² = a + bR, at SNR 100 (M6);
- per-column wavelengths from the real WA CDR.

| Check | Tolerance |
|---|---|
| δ_x recovered | within 3 σ_δ (predicted); the z-score std is 1.00 ± 0.10 |
| β_p recovered | z-score std 1.00 ± 0.10; H recovered to 3σ |
| Artifact | residual after the model at or below 1.5× the noise-only residual over the TER window |
| Destripe | stripe RMS after correction ≤ 0.1× injected; the column-aligned unit's band depths preserved to ≥ 95% |
| Despike | recall ≥ 95% for spikes ≥ 8σ; false-flag rate ≤ 1e-5 per sample |
| Smile | RMS error against truth on the target vector ≤ 0.2 σ for smooth spectra |
| Projection | grid parameters exact; synthetic lat/lon backplane round trip < 0.01 px |
| σ calibration | reduced χ² against truth 1.00 ± 0.05, end to end and per step, also within band groups (1.0–1.8, 1.8–2.2, 2.2–2.6 µm) |

### V2. Regression against official products (data in `varda-demo-data`; skipped when absent)

FRT00005850 needs its TRR3 + DDR (counter 07, about 1 GB) downloaded (D6).
HRL000040FF follows if time allows.

| Metric | Tolerance |
|---|---|
| Grid parameters | identical to the MTRDR (same φ_P, R, s; tie point within 0.01 m) |
| Geolocation offset | phase correlation of a 1.3–1.7 µm albedo image: median ≤ 0.25 px |
| Spectral shape | each spectrum ÷ its 1.3–1.7 µm mean; median ratio (ours / MTRDR) per band within ±1% over 1.05–2.6 µm, and ±2% inside 1.94–2.10 µm |
| Brightness | ratio map reported, not gated; EGN is absent, so cross-track gradients up to 15–20% near 1.1 µm are expected (Seelos 2024 §5.6) |
| CO2 residual (M7 metric, same code) | ours ≤ MTRDR on the same scene (MTRDR FRT00005850: 0.0082 per-pixel median) |
| Parameter maps (D2300, BD2290, BD1900_2, OLINDEX3, and the prototype's BD2310*) | Pearson r ≥ 0.90; median bias ≤ MTRDR between-observation repeatability (prototype BD2310: 0.0014; BD1900: 0.0016; the others measured by the same code on the crater-floor set at the start of Phase 1) |
| Attribution | each difference attributed by toggling steps (EGN absent, ESC, ATM variant, destripe) and reported |

\* The prompt and prototype's "BD2300" is not a Viviano-Beck parameter. The
prototype computes 1 − R2310 / continuum(2160, 2400) on 5-band means. It is
kept under that definition for continuity, named BD2310 here (D7).

### V3. The paper's use case

- **CO2 residual.** Process FRS00031442, FRS0002FE75 and FRS00031260. Each
  must have a per-pixel median CO2 residual ≤ 0.0083 and a scene-median
  residual RMS ≤ 0.0080, which are the medians over the six local MTRDRs
  measured by the same code. Quick fits already reach 0.0054–0.0060 at the
  column level (M10).
- **Spectral-shape offsets.** Where FRS00031442 overlaps the crater-floor
  MTRDRs, its offsets from them must be no larger than the MTRDRs' offsets
  from each other. This uses the prototype's gain and repeatability metrics:
  the shape χ² and the BD2310 median offset (≤ 0.004), computed with its
  `fusion_core.py` code (ported into the test).
- **Compatibility.** Outputs stay compatible with
  `ground_truth/rim_supercam_targets.csv` (lat/lon lookup on the projected
  grid, 1.3–2.6 µm bands on the MTRDR vector).

### V4. Optional: CAT

If Michael produces CAT outputs (ATCv2 with an explicit scan and AUTO) for
the same observations, compare CO2 residuals and parameter maps.

### Test data

- Small crops go in `src/varda/crism/_tests/data`:
  - a TRR3 crop of 16 lines × 120 columns × 438 bands (about 3.4 MB as
    float32) and the matching DDR crop;
  - one AT and one WA;
  - the MTRDR label and header text.
- Full-scene regressions read `varda-demo-data` and are skipped without it,
  using the existing `pytest.skip` pattern.

## Prior art: reuse, wrap or not

| Item | License | Verdict | Why |
|---|---|---|---|
| GDAL PDS3 / PDS4 / ENVI (rasterio) | MIT/X | **Reuse** | Already a dependency. Layout is correct for every CRISM type. Force 65535 on PDS3. The `.hdr` georeference is right, not the labels' (M1, M8). |
| pvl | BSD-3 | **Reuse** (new dependency) | A real ODL parser; regex parsing breaks on `OBJECT = FILE` nesting. |
| jezero-rim-mapping / pycrism (Valantinas) | MIT | **Adapt small pieces with attribution** | WA smile handling, the descending-L flip, slew and edge-row masks, band-depth significance. Not its projection (wrong sphere). Its TRR3 → TER code is not published. |
| crism_ml (Plebani 2022) | Apache-2.0; data CC-BY-4.0 | **Reimplement, cross-checked (Phase 2)** | About 150 lines: Student-t bland model, per-column top-3 within ±50 rows, cascaded despike. Ship fitted parameters (< 1 MB `.npz`) with attribution instead of a 469 MB download. |
| n2n4m (Platt 2024) | **MIT** (not unlicensed) | **Phase 4, in the PyTorch worker** | A per-spectrum 1-D U-Net denoiser trained on MarsSI-corrected data. Adapting it with attribution is allowed; the code is unread so far. |
| MarsSI | n/a (service) | **Reference recipe only** | cos i → CAT volcano scan → column median ratio: the community baseline. |
| Itoh & Parente 2021 | no code | **Phase 4, from the paper** | Joint atmosphere and denoising by L1 ADMM, about 1.5–2 h per image, initialized from VS ADR transmissions. |
| planetaryimage | BSD | Ignore | No BIL, no `FILE` wrapper. |

## Phases 2–4 (outline; each gets its own short spec before its plan)

### Phase 2: ratioing and parameters

- Plebani bland-pixel column ratioing in the Ratio Explorer and as a
  pipeline option.
- HyPyRameter summary parameters at hyperspectral sampling, with σ by
  linear propagation (Monte Carlo for MIN/ratio parameters).
- Report HyPyRameter against the CAT 7.4 fixes (BD3400, BD3400_2, ICER2_2)
  and against the PDS 2026 summary-parameter reference. Known differences
  are OLINDEX3 (11-term versus CAT's 5-term), BD1900r2 anchors and the
  BD1900_2 kernel (the R2046 typo should be R2067). This is a report only;
  HyPyRameter is not edited.

### Phase 3: VNIR and ATO

- S detector (no ATM; PHT, despike, destripe, ESC empirical only).
- The VNIR → IR sensor-space join by nearest neighbor (XFM), with the IN
  continuity residuals.
- The ATO/ATU along-track oversampling.
- PDS's 12 m LMLP I/F products as readers (Kreisch 2017; Seelos 2024 §7.2).
  The prompt's "LMLP" is Kreisch's log-maximum-likelihood with penalty.

### Phase 4: beyond CAT

- Itoh & Parente joint correction and denoising.
- n2n4m denoising in the PyTorch worker.
- EGN for EPF observations, and proxy EGN.
- DTM photometry (Lommel–Seeliger, Hapke) through `photometry.py`.
- Radiance (RA) → I/F without the TRR3 filter (D2).
- Thermal correction above 2.6 µm.

## Risks

- **TRR3 filtering.** The noise model may misestimate σ0. Mitigation: end-to-end
  χ² on synthetic data, and split-half repeatability on real data. A
  radiance start is a Phase 4 option.
- **Artifact basis learned from a few observations.** With only 5 TRR3s at
  first it could overfit. Mitigation: leave-one-out tests, the high-pass
  restriction, M = 1, and a comparison with the VS ADR artifact spectra if
  D3 allows.
- **No EGN.** FRS brightness gradients stay in Phase 1 products. The fusion
  stage's per-observation gain and shape correction (F2) absorbs part of
  this. V3 measures what remains.
- **Using an AT period past its end** for 2014 data. Scoring picks the best
  scan, but none may be ideal.
- **Shared files** with the other session (`pyproject.toml`, `uv.lock`,
  `main.py`, CHANGELOG, CLAUDE.md). Touched once per phase, after a rebase,
  and announced in the coordination file.

## Decisions for Michael

- **D1. Phase 1 scope.**
  - **In:** IR, 1.0–2.6 µm, from TRR3 I/F, with S0–S9, both products, the
    GUI and the CLI.
  - **Out:** EGN, VNIR, the join and radiance input.
  - **Recommended:** approve as written.
- **D2. Starting product.** Filtered TRR3 I/F, which is what MTRDR uses, is
  recommended for consistency, with Leask artifacts detected and flagged.
  Starting from unfiltered RA radiance (Itoh & Parente's choice) would mean
  RA and SF downloads and would break consistency with MTRDR. Proposed for
  Phase 4.
- **D3. CAT aux files (513 MB).** They hold the 17 VS ADRs with artifact
  spectra and CAT's precomputed per-observation scan choices.
  - These are calibration data, not code, but they are not on PDS and the
    CAT distribution states no license.
  - **Recommendation:** download them for *comparison* (V4 and the artifact
    check), but keep Varda's default path PDS-only. Varda never
    redistributes them; it reads them only if the user points it at an aux
    folder.
  - Also: the research read one VS ADR *data label* on a third-party mirror
    (davinci.asu.edu) to learn the file layout. It's a data description,
    not code, and it's logged in the ledger. OK to keep?
- **D4. Validation tolerances.** The V1–V3 tables above. Each is tied to a
  measurement or to an estimator's predicted σ.
- **D5. HRL/HRS pixel size.** 36 m, as in HRL000040FF's MTRDR, versus
  Seelos 2024's "optimized to each observation". It doesn't matter for the
  FRS paper targets (18 m), so the HRL MTRDR is followed.
- **D6. Downloads to `varda-demo-data`.** TRR3 + DDR for FRT00005850 (about
  1 GB) for V2, then HRL000040FF (about 0.5 GB), from PDS.
- **D7. "BD2300".** It isn't a Viviano-Beck parameter. Keep the prototype's
  definition as BD2310 for continuity, and add the official D2300 and
  BD2290.
- **D8. Licensing notes.** n2n4m is MIT, so it can be adapted with
  attribution (Phase 4). crism_ml's fitted bland parameters can ship as a
  small `.npz` derived from the CC-BY data, with attribution (Phase 2).
  jezero pieces are MIT, with attribution. Any objection?

## Roadmap update (for the untracked roadmap spec)

The roadmap spec is untracked in the main checkout, so this branch doesn't
edit it. Suggested replacement for its F6 "Readers" and "Photometry with a
DTM" bullets:

> **F6. CRISM data access and processing** — superseded by
> `2026-10-16-crism-processing-design.md`: readers (TRR3, DDR, CDR, MTRDR/TER;
> LMLP in Phase 3), an MTRDR-consistent TRR3 → corrected I/F pipeline with σ
> and provenance (Phase 1), ratioing and parameters with σ (Phase 2), and DTM
> photometry (Phase 4). The footprint browser is done (Find Data).
