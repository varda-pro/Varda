# CRISM Phase 1 validation (Task 23)

Status: run 2026-10-16 on the demo data; V2 and V3 re-run after the final
review's fixes (the slew lines left off the map grid, see V3). Decisions
D9–D13 taken 2026-10-21 (see the end); the artifact basis is now off by
default and limited to the CO2 lines. Design and tolerances:
`../2026-10-16-crism-processing-design.md` ("Validation design", approved
as D4). Decisions per step: `evidence-ledger.md`. Tests:
`src/varda/crism/_tests/test_regression.py` (V2, V3, the artifact basis;
about 10 minutes; it runs only with the demo data and `VARDA_REGRESSION=1`)
and the synthetic V1 tests. Scripts behind the
attribution: `measurements/task23/`. Processing outputs are outside the
repository.

## Summary

- **Met:** the MTRDR grid exactly; geolocation (−0.14, −0.08 px); a CO2
  residual below the MTRDR's on FRT00005850 (0.0067 against 0.0082); V3's
  CO2 residual on all three rim FRSs, with and without the artifact basis;
  the spectral-shape χ² of FRS00031442 against the crater-floor MTRDRs; the
  artifact basis's leave-one-out check under every scan the pipeline
  chooses; four of five parameter biases.
- **Not met (11 xfails, each with its measured cause):**
  - **Pearson r ≥ 0.90 for all five parameters** (0.63–0.90). Not noise:
    the two products share their pixel-scale fluctuations. OLINDEX3 is EGN
    (outside Phase 1). The other four are a cross-track difference inside
    Phase 1, mostly a *smooth* trend across the detector columns that no
    step toggle removes and that is not attributed (suspects ranked below);
    destripe removes only its column-to-column part.
  - **Spectral shape ±1%/±2%.** A 2 µm bowl of up to 3.4% that follows
    CAT's artifact patch, which the MTRDR has and we do not (inside Phase 1,
    ledger A6/A7); beyond 2.2 µm a 1–1.35% offset that follows the phase
    angle (EGN).
  - **D2300 bias** −0.0019 against a repeatability of 0.00152 (the same
    cross-track structure).
  - **The basis's effect on the mineral parameters** exceeds V2's bounds in
    three of nine cases (D2300 and BD1900_2 on FRS0002FE75, BD1900_2 on
    FRS00031260).
  - **FRS00031442's BD2310 offset** from two crater-floor MTRDRs (0.0055,
    0.0051), against an MTRDR–MTRDR maximum of 0.0045 and the spec's 0.004;
    cause unattributed.
- **Two defects found and fixed** (with regression tests):
  - The pipeline masked every element flagged in the BP CDRs. The
    calibration has already interpolated those elements (DPSIS App. L §2.6), and
    on the 2014 FRSs this masked 17% of the samples. 98% of the pixels then
    had a gap in the CO2 window.
  - `learn-artifacts`' leave-one-out table raised on every real observation
    (columns without wavelengths).

## V1: the synthetic cube

The current results of the synthetic tests, all passing in the suite. Where
a test asserts a bound wider than the spec's, the bound is given.

| Check | Tolerance | Measured | Test |
|---|---|---|---|
| δ_x recovered | within 3σ_δ; z std 1.00 ± 0.10 | smoothed δ within 3σ in 99.2–100% of columns; raw δ z std 1.01, 0.88, 0.95 (seeds 0–2; **seed 1 outside ±0.10**; the test asserts 0.8–1.25) | `test_atmosphere::test_recovers_beta_and_shift` |
| β_p recovered | z std 1.00 ± 0.10; H to 3σ | column β z std 1.04–1.08; pixel β z std 0.99–1.00 free, 1.02–1.03 with the prior; H 11.02 ± 0.04 (true 11) | `::test_recovers_beta_and_shift`, `::test_pixel_beta_recovery`, `::test_elevation_model_recovers_H` |
| Artifact | ≤ 1.5× the noise-only residual | 0.0060 against the exact correction's 0.0059 (1.02×) | `::test_artifact_reduces_co2_residual` |
| Destripe | stripe RMS ≤ 0.1× injected; column-aligned unit ≥ 95% | 0.564% → 0.045% (0.08×); unit preserved | `test_destripe::test_removes_injected_stripes`, `::test_preserves_column_aligned_unit` |
| Despike | recall ≥ 95% at ≥ 8σ; false ≤ 1e-5 | recall 0.95–0.98; false 0–1e-6 | `test_despike::test_recall_and_false_rate` |
| Smile | RMS ≤ 0.2σ (smooth spectra) | 0.0034σ | `test_smile::test_resampling_error_and_sigma` |
| Projection | grid exact; round trip < 0.01 px | grid exact on FRT00005850 and HRL000040FF; pyproj agrees to 1e-6 m | `test_projection::test_grid_matches_mtrdr_labels`, `::test_coordinates_match_pyproj` |
| σ calibration | χ² 1.00 ± 0.05 per band group | about each band's mean: 0.98–1.01 in every group; raw on basalt 1.04–1.08 in 1900–2100 nm; **raw on the hydrated units 1.50–1.72** (the A5 β bias; pinned < 1.6 on seed 0) | `test_pipeline::test_sigma_chi2_synthetic` |

## V2: FRT00005850 against its MTRDR

Default recipe: 06822:AT (relative margin 0.35); H = 14.5 km (accepted); no
artifact basis (none serves 2007). Overlap: 1,032,570 pixels, 603,755 valid
in both. 97.0% of the pixels both fill come from the same source pixel.

| Metric | Tolerance | Measured | Test |
|---|---|---|---|
| Grid | same φ_P, R, s; tie within 0.01 m | identical (15°, 3394839.8133 m, 18 m; tie 4417887.6, 1091944.9) | `test_frt5850_grid_identical` pass |
| Geolocation | median ≤ 0.25 px | −0.14, −0.08 px (whole overlap); tile medians also within | `test_frt5850_geolocation` pass |
| Spectral shape | ±1% over 1.05–2.6 µm; ±2% in 1.94–2.10 µm | max 0.76% in 1.05–1.85 µm; 2.0% (1.85–1.94); **3.4% (1.94–2.10)**; 2.4% (2.10–2.20); 1.35% (2.2–2.6) | `test_frt5850_shape_ratio` **xfail**; `_achieved` pass |
| Brightness | reported, not gated | ours ÷ MTRDR median 1.072 (p5–p95 1.003–1.150); along track 1.00–1.17, r = +0.94 with the emission angle | figure |
| CO2 residual | ours ≤ MTRDR (0.0082) | per pixel 0.00665 against 0.00824; scene 0.0061 against 0.0080 | `test_frt5850_co2_not_worse` pass |
| Pearson D2300 | ≥ 0.90 | **0.785** | xfail (inside Phase 1) |
| Pearson BD2290 | ≥ 0.90 | **0.774** | xfail (inside Phase 1) |
| Pearson BD1900_2 | ≥ 0.90 | **0.627** | xfail (inside Phase 1) |
| Pearson OLINDEX3 | ≥ 0.90 | **0.895** | xfail (EGN) |
| Pearson BD2310 | ≥ 0.90 | **0.883** | xfail (inside Phase 1) |
| Bias D2300 | ≤ repeatability 0.00152 | **−0.0019** | xfail (inside Phase 1) |
| Bias BD2290 | ≤ 0.00103 | +0.00016 | pass |
| Bias BD1900_2 | ≤ 0.0016 | −0.00036 | pass |
| Bias OLINDEX3 | ≤ 0.00595 | −0.0038 | pass |
| Bias BD2310 | ≤ 0.0014 | −0.00023 | pass |

The repeatabilities come from the five crater-floor MTRDRs, with the
prototype's code ported into the test: BD2310 0.00139, D2300 0.00152, BD2290
0.00103, BD1900_2 0.00189, OLINDEX3 0.00595. The port reproduces the
prototype: BD2300 0.00139 against its 0.00140, BD1900 0.00162 against
0.00162, shape χ² 5.7 against 5.6, and its registration residuals exactly.

### Attribution: why r < 0.90

**Not noise-limited.**
- Both products come from the same TRR3 pixels, so their pixel-scale
  fluctuations are shared. Along-track second differences correlate 0.92
  (D2300), 0.82 (BD2290), 0.78 (BD1900_2), 0.97 (OLINDEX3) and 0.99
  (BD2310) between them.
- The ceiling that independent noise would set is meaningless here: it
  would be 0.77–0.98 (along-track noise ≈ 0.0008–0.0022), *below* the
  measured r for BD2290 and BD2310.
- Block means help little. r rises by at most 0.025 at 4×4 (D2300 +0.014,
  BD2290 +0.024, BD1900_2 +0.024, OLINDEX3 0.000, BD2310 0.000) and by at
  most 0.049 at 8×8 (+0.028, +0.049, +0.045, −0.001, +0.008).
- Same-source pixels only: r changes by less than 0.01.

**Where the difference sits** (ours − MTRDR, grouped by our source column
and line):

| Parameter | per-column share | per-line share | r, column part removed | r, column and line removed | line profile vs phase | var(ours)/var(MTRDR) |
|---|---|---|---|---|---|---|
| D2300 | 0.85 | 0.03 | 0.947 | 0.955 | +0.74 | 1.89 |
| BD2290 | 0.52 | 0.00 | 0.886 | 0.887 | −0.00 | 1.17 |
| BD1900_2 | 0.73 | 0.00 | 0.872 | 0.873 | −0.44 | 1.44 |
| OLINDEX3 | 0.06 | 0.92 | 0.929 | **0.999** | **+0.95** | 1.10 |
| BD2310 | 0.91 | 0.00 | **0.989** | 0.990 | +0.50 | 1.15 |

**OLINDEX3: EGN (outside Phase 1).**
- 92% of the difference is along track.
- Its line profile follows the phase angle (r = 0.95; 33–66° along the
  FRT's gimbal scan).
- The brightness ratio does the same with the emission angle (r = 0.94).
  This is the geometric normalization the MTRDR applies and Phase 1 does
  not.
- Removing it gives r = 0.999.

**D2300, BD2290, BD1900_2, BD2310: cross-track, inside Phase 1, mostly
unattributed.** The per-column difference has two parts
(`figures/v2_column_profiles.png`; `measurements/task23/smooth_columns.py`):
- **A smooth trend** (the 15-column running median of the per-column
  medians): 84% of D2300's column variance (std 0.00173), 66% of
  BD1900_2's (0.00163), 38% of BD2290's and 49% of BD2310's. Our D2300
  falls to about −0.010 below column 100 and toward column 630 while the
  MTRDR's stays flat; it is 0.5–1% low at 2120 and 2170 nm on columns
  above 480.
- **Column-to-column scatter** (the rest): ours has 2.1–3.8× the MTRDR's
  (high-passed per-column medians: D2300 0.00081 against 0.00039, BD2290
  0.00062 against 0.00023, BD1900_2 0.00111 against 0.00029, BD2310 0.00066
  against 0.00028).
- The per-column spectral factor q(x, λ) = median(ours ÷ MTRDR) reproduces
  the column component when it is applied to the MTRDR: 94% for D2300, 88%
  for BD2290 and 93% for BD2310, but only **45% for BD1900_2**, whose cause
  is the least explained (the no-shift and hydration-mask toggles move it,
  which points at the ATM fit).
- All of that is in q's band-to-band structure: a smooth cubic in λ per
  column, which is what EGN would be, explains 0–1% (9% for OLINDEX3).
- q varies most across columns in the CO2 window (0.4–0.6% std at
  1975–2040 nm), at the 1.65 µm filter join (0.55%), and at 0.16–0.2%
  elsewhere.
- At 1985 and 2067 nm it anticorrelates with CAT's per-column artifact
  patch (r = −0.5, −0.3), which the MTRDR has. At 2.1–2.4 µm, where the
  patch is zero, it follows neither the patch nor T.

**The smooth trend against per-column candidates** (correlation of the
15-column running medians; and how much of the trend's variance each toggle
removes, negative where it grows):

| Candidate | D2300 | BD1900_2 | BD2290 | BD2310 |
|---|---|---|---|---|
| smile offset (WA − target) at 2300 nm (1930 nm: the same within 0.02) | −0.55 | +0.14 | −0.47 | +0.15 |
| ESC frame at 2290 nm (2120, 1930, 1985 nm: the same within 0.02 in size) | +0.54 | −0.14 | +0.47 | −0.15 |
| column β | +0.35 | −0.01 | −0.13 | **+0.70** |
| fitted shift δ_x (smoothed) | −0.06 | +0.07 | +0.32 | **−0.58** |
| column fit reduced χ² / residual RMS | −0.20 / +0.07 | −0.20 / −0.11 | +0.05 / +0.10 | +0.20 / +0.19 |
| toggle: destripe on | removes 5% | 5% | 8% | 9% |
| toggle: no fitted shift | 5% | −54% | 43% | −16% |
| toggle: ESC off | −66% | −168% | 16% | −1000% |
| toggle: hydration mask off | −15% | −14% | −4% | −2% |
| toggle: CAT's 11D87:VS | 19% | 20% | 31% | −35% |
| toggle: β per pixel | 0% | 0% | 0% | 0% |

- **The fitted shift's edge extrapolation is not it:** `smoothShift`
  extrapolates only into the 36 columns without wavelengths (0–28,
  633–639), which carry no data. On the valid columns the smoothed δ spans
  −0.92 to −0.58 nm and correlates −0.06 with the D2300 trend.
- **Hydration mask:** turning it off makes every trend 2–15% larger, so it
  does not cause them.
- **ESC removes part of the trend:** with ESC off the D2300 trend is 1.3×
  larger. What remains still follows the smile's parabola (r = −0.55 with
  the offset; the ESC frame is the same parabola, so the two cannot be told
  apart). An ESC model linear in the offset may leave a quadratic residual
  at the edges, where the offset reaches +15 nm. Changing that is an ESC
  design change, not a small fix, and the correlation is moderate.
- **Verdict: unattributed; suspects ranked by correlation.**
  - D2300 and BD2290: the residual smile/ESC (|r| 0.47–0.55).
  - BD2310: the per-column ATM fit (β +0.70, δ −0.58).
  - BD1900_2: none above |r| = 0.20. CAT's scan removes 20%, and the ATM
    toggles (no shift, ESC off) enlarge it.

Step toggles (FRT00005850, each from the default recipe; Pearson / median
bias; CO2 per-pixel median; the shape ratio's minimum):

| Run | D2300 | BD2290 | BD1900_2 | OLINDEX3 | BD2310 | CO2 | shape min |
|---|---|---|---|---|---|---|---|
| default | 0.785/−0.0019 | 0.774/+0.0002 | 0.627/−0.0004 | 0.895/−0.0038 | 0.883/−0.0002 | 0.00665 | 0.966 |
| destripe on | 0.795/−0.0019 | 0.801/+0.0002 | 0.663/−0.0005 | 0.895/−0.0038 | **0.902**/−0.0002 | 0.00653 | 0.966 |
| apply the fitted shift to the wavelengths (`smile.applyFittedShift`; the review's run, Pearson only) | 0.789 | 0.782 | 0.638 | 0.896 | 0.883 | 0.00631 | |
| no fitted shift | 0.799/−0.0038 | 0.826/−0.0000 | 0.709/−0.0057 | 0.893/−0.0034 | 0.899/−0.0012 | **0.01226** | 0.957 |
| destripe on, no shift | 0.804/−0.0038 | 0.837/−0.0000 | 0.734/−0.0058 | 0.893/−0.0035 | 0.908/−0.0013 | 0.01223 | 0.957 |
| ESC off | 0.731/−0.0029 | 0.793/+0.0006 | 0.522/+0.0010 | 0.902/−0.0026 | 0.748/−0.0019 | 0.00576 | 0.967 |
| hydration mask off | 0.771/−0.0017 | 0.769/+0.0003 | 0.606/+0.0013 | 0.895/−0.0042 | 0.880/+0.0000 | 0.00839 | 0.969 |
| CAT's scan 11D87:VS | 0.799/−0.0025 | 0.756/−0.0002 | 0.648/−0.0009 | 0.892/−0.0028 | 0.859/−0.0011 | 0.00783 | 0.965 |
| β per pixel (no prior) | 0.785/−0.0019 | 0.774/+0.0002 | 0.628/−0.0004 | 0.895/−0.0038 | 0.883/−0.0002 | 0.00665 | 0.966 |
| despike off | identical to the default | | | | | | |
| MOLA incidence | identical (every metric here is invariant to a per-pixel scalar) | | | | | | |
| no atmosphere | 0.753/−0.0213 | 0.846/−0.0046 | 0.498/−0.0568 | 0.904/+0.0178 | 0.936/−0.0129 | 0.16702 | 0.416 |

- The artifact model is off for this observation anyway: no basis serves
  2007.
- **What destripe on does.** It removes only the high-frequency part:
  ours' column-to-column scatter falls from 2.1–3.8× the MTRDR's to
  1.5–2.2×, while the smooth trends stay (D2300 0.00173 → 0.00169).
- **The post hoc classic column-ratio destripe agrees.** It divides each
  band by its column median over a 15-column running median,
  unconditionally, and changes r by −0.01 to +0.02. It also divides out
  real sub-15-column structure that the MTRDR keeps. Both say the
  high-frequency scatter is worth at most about 0.04 of r; the smooth trend
  is the larger part.
- **The fitted shift applied to the wavelengths is not the cause** (the
  review's run: r within +0.01).
- **So the shortfall is mostly the smooth cross-track trend:**
  unattributed, with the suspects ranked above. CAT's per-column artifact
  patch explains part of it in the CO2 window.

### Attribution: the spectral shape and the brightness

**A 2 µm bowl** (`figures/v2_shape_ratio.png`):
- Ours ÷ MTRDR falls to 0.966 at 2080–2086 nm and stays 1–2.4% below 1
  from 1.875 to 2.21 µm.
- ln(shape ratio) correlates −0.81 and −0.87 over 1850–2250 nm with CAT's
  11D87 VS ADR artifact spectra (McGuire, Pelkey); the curves overlay in
  the figure. With 06822's VS ADR artifact spectra it is −0.78 and −0.83.
- The bowl is the same on every line (0.961–0.971 at 2080 nm; r = −0.30
  with the phase angle): not EGN.
- It is the artifact patch the MTRDR has (CAT ≥ 7.1). Our high-passed
  learned basis cannot hold it by design (A6, S4.E.1), and FRT00005850 has
  no basis at all.
- The CO2 metric divides by a quadratic over 1940–2100 nm, so it is blind
  to the bowl. Our CO2 residual is the lower one, the MTRDR's shape the
  flatter one.

**Beyond 2.2 µm, a 1.0–1.35% offset.** Per line, the shape ratio at 2400 nm
spans 0.980–1.002 and follows the phase angle (r = −0.72; −0.78 at
2550 nm): EGN's spectral slope.

**Brightness.** The ratio ours ÷ MTRDR at 1.3–1.7 µm is 1.00–1.17 along
track, following the emission angle (r = +0.94), and 1.06–1.09 across track
(`figures/v2_brightness_ratio.png`). EGN is absent (spec non-goal); the
spec's 15–20% expectation is met.

## V3: the rim FRSs

The products of the default recipe as it was then (artifact basis on over
the whole fit window; off by default since D12), with the BP fix. Since the final review
the map grid, GLT and area weights take only the usable lines (ledger G2):
the slew lines hold no I/F but their positions spread the grid, and left a
strip of DE and IN without I/F. The FRS grids shrink from 756 × 903 to
702 × 295 (FRS00031442, 13 slew lines), 750 × 836 to 699 × 244
(FRS0002FE75, 13) and 748 × 617 to 730 × 248 (FRS00031260, 8);
FRT00005850 has none, so V2 is unchanged. On the re-run (27 passed, 11
xfailed, as before) the CO2 residuals and the BD2310 offsets are the same to
the digits given; the grid origins move by a fraction of a pixel, so the
nearest-neighbor maps, and with them the shape χ² and the basis's effect,
change in the last digits (before: shape χ² 64.1, 5.6, 12.1 against 171.3;
after the gains 5.7, 3.6, 5.3 against 9.5; the MTRDR–MTRDR values move too,
because the joint gains and the noise model include the FRS).

| FRS | Scan (relative margin) | H | Artifact basis (c) |
|---|---|---|---|
| FRS00031442 | 061C4:AT (0.005) | 8.6 km | 061C4 (0.0058) |
| FRS0002FE75 | 0A93E:AT (0.039) | 9.5 km | 0A93E (0.0066) |
| FRS00031260 | 08608:AT (0.033) | 4.6 km, **rejected** (outside 5–20 km): β per pixel | 08608 (0.0049) |

| Metric | Tolerance | Measured | Test |
|---|---|---|---|
| CO2 per pixel, FRS00031442 | ≤ 0.0083 | 0.00397 (no basis 0.00770; leave-one-out basis 0.00439) | pass |
| CO2 scene RMS, FRS00031442 | ≤ 0.0080 | 0.00193 (0.00659; 0.00258) | pass |
| CO2 per pixel, FRS0002FE75 | ≤ 0.0083 | 0.00517 (0.00741; 0.00537) | pass |
| CO2 scene RMS, FRS0002FE75 | ≤ 0.0080 | 0.00362 (0.00619; 0.00386) | pass |
| CO2 per pixel, FRS00031260 | ≤ 0.0083 | 0.00420 (0.00731; 0.00457) | pass |
| CO2 scene RMS, FRS00031260 | ≤ 0.0080 | 0.00203 (0.00597; 0.00257) | pass |
| FRS00031442 overlap (ruling P4) | ≥ 1000 px at 18 m | 48,488 (FRT00005C5E), 22,627 (FRT000047A3), 46,057 (HRL000040FF), measured before the slew-line fix, which does not change the I/F footprint; the test's own count (36 m pixels valid in both, ×4) on the re-run: 85,576, 35,260, 80,792 | pass |
| Shape χ², as delivered | ≤ max MTRDR–MTRDR (188.7) | 66.8, 5.6, 12.8 | pass |
| Shape χ², after the gains | ≤ max MTRDR–MTRDR (10.7) | 6.1, 3.6, 5.3 | pass |
| BD2310 median offset | ≤ max MTRDR–MTRDR (0.0045) and ≤ 0.004 | **0.0055, 0.0051**, 0.0004 | `test_frs31442_overlap_bd2310_offset` **xfail**; `_achieved` pass |

- Every pixel in the footprints is scored: ≥ 99% is asserted, 100% is
  measured. Before the BP fix only 1.8–3.0% were.
- The bundled bases were learned from these three FRSs, so the test also
  requires the products without a basis to pass, and they do.

**BD2310 offset.**
- FRS00031442's BD2310 is 0.0055 and 0.0051 above FRT00005C5E's and
  FRT000047A3's in their overlaps, and agrees with HRL000040FF's (0.0004).
- The offset is the same without the basis (0.0057, 0.0053, 0.0006) and
  with a leave-one-out basis (0.0055, 0.0052, 0.0004).
- Our pipeline's BD2310 bias against the MTRDR on FRT00005850 is −0.0002,
  with per-column structure up to ±0.001.
- The offsets do not add up around the loop FRT000047A3–HRL000040FF–FRS:
  −0.0010 and −0.0004 imply −0.0014 against the −0.0051 measured. Medians
  over different overlap areas mix geology.
- The MTRDR pairs themselves reach 0.0045 (FRT00005C5E–HRL000040FF),
  already above the spec's 0.004 (the prototype's "up to about 0.004").
- Not attributed to a Phase 1 step.

## The artifact basis (A6)

`learn-artifacts` with the pipeline's own scan choice puts each FRS under a
different key (FRS00031442 061C4, FRS0002FE75 0A93E, FRS00031260 08608;
FRT00005850 06822). Each key's group then holds one observation, which
allows no leave-one-out and would bundle a basis learned from a scene
alone. So each chosen key is learned from all three FRSs under that scan
(`--scan`).

The bundled files are
`resources/artifacts/{061C4,0A93E,08608}_AT_1084995594-1090233682.npz`
(0.21 MB each, compressed). Their training span is SCLK
1084995594–1090233681 (2014-05-19 to 2014-07-19), so the ±180-day gate
serves 2014 only. FRT00005850 is excluded: a 2007 basis would rest on one
observation, and the FRSs' basis makes it worse (Task 14).

Column CO2 residual (median over columns; CLI table, BP elements kept): no
basis → leave-one-out → in-sample.

| Scan | FRS00031442 | FRS0002FE75 | FRS00031260 |
|---|---|---|---|
| 061C4:AT | 0.00630 → 0.00341 (−46%) → 0.00276 | 0.00846 → 0.00501 (−41%) → 0.00434 | 0.00634 → 0.00306 (−52%) → 0.00263 |
| 0A93E:AT | 0.01022 → 0.00369 (−64%) → 0.00302 | 0.00665 → 0.00405 (−39%) → 0.00373 | 0.00933 → 0.00357 (−62%) → 0.00302 |
| 08608:AT | 0.00613 → 0.00360 (−41%) → 0.00288 | 0.00752 → 0.00507 (−33%) → 0.00448 | 0.00588 → 0.00329 (−44%) → 0.00279 |

Every leave-one-out reduction is at least 30% (smallest: 33%). That kept
`artifactModel` on by default until D12, which turned it off for its effect
on BD1900_2 (below and in Decisions).

**The basis's effect on the mineral parameters** (median with − without,
over the pixels valid in both; the bounds are V2's: the crater-floor
repeatability, and the spec's 0.0016 for BD1900_2):

| FRS (scan) | D2300 (≤ 0.00152) | BD2290 (≤ 0.00103) | BD1900_2 (≤ 0.0016) | BD2310 | OLINDEX3 |
|---|---|---|---|---|---|
| FRS00031442 (061C4) | −0.00072 | −0.00001 | +0.00121 | −0.00013 | −0.00001 |
| FRS0002FE75 (0A93E) | **−0.00176** | −0.00007 | **+0.00176** | −0.00117 | −0.00001 |
| FRS00031260 (08608) | −0.00094 | −0.00000 | **+0.00350** | −0.00063 | +0.00001 |

- A leave-one-out basis gives nearly the same shifts (−0.00168, +0.00171 and
  +0.00313 for the three that fail), so this is the basis itself, not in-sample
  learning. (D2300, BD2290 and BD1900_2 are from the re-run after the
  slew-line fix; BD2310, OLINDEX3 and the leave-one-out shifts from before
  it, not re-measured.)
- Against the overlapping MTRDRs, the basis moves FRS00031260's BD1900_2
  by about 0.0033 toward FRT000047A3 and HRL000040FF and away from
  FRT00005C5E. It moves FRS00031442's by about 0.0015 away from FRT00005C5E
  and FRT000047A3 and toward HRL000040FF.
- The overlaps cannot say which is right.

## CAT comparison: scan choices

CAT 7.4's `obs_info/crism_obs_info.txt` (both scan columns agree for these
observations) against our scores: the TER-window score after the shift
fit, central 64 lines, BP elements kept.

| Observation | CAT 7.4 | Ours, PDS ATs (default) | Ours, PDS + CAT VS ADRs | CAT's scan in our score |
|---|---|---|---|---|
| FRT00005850 | 11D87 | 06822:AT 0.00649 | 06822:VS 0.00649 (tie with the AT) | 0.00759, rank 3 of 23 |
| FRS00031442 | 1B815 | 061C4:AT 0.00623 | 2815C:VS 0.00549 | 0.00593, rank 7 of 23 |
| FRS0002FE75 | 0A93E | **0A93E:AT 0.00716 (same as CAT)** | 28D04:VS 0.00693 | 0.00716, rank 3 of 23 |
| FRS00031260 | 1B815 | 08608:AT 0.00600 | 2815C:VS 0.00530 | 0.00562, rank 5 of 23 |

- 11D87 and 1B815 are not among the six PDS scans, so with PDS only ours
  can agree with CAT only on FRS0002FE75, and it does.
- With CAT's VS ADRs, CAT's choice is always in our top 7 but never first.
- Forcing 11D87:VS on FRT00005850 raises our CO2 residual (0.00665 →
  0.00783) and does not raise r (table above). We apply its transmission
  without CAT's patch.

## Fixes made in this task

1. **BP-flagged elements are no longer masked** (`pipeline._Run._read`).
   - DPSIS App. L §2.6: the a priori bad-pixel mask "is applied to an image by
     replacing each bad pixel with the average of the two pixels in
     adjacent columns". App. L §2.15 does the same after the fact.
   - So the TRR3 holds interpolated I/F there, which the MTRDRs use.
   - The 2014 BPs flag 13.7% and 24.1% of the 280,320 elements; their union
     was masked. This masked 9.3 M (17%) of FRS00031442's samples and left 98% of
     its pixels with a gap in the CO2 window.
   - The samples are now counted (`badPixelInterpolated` in the
     provenance's mask record) and kept.
   - Test: `test_pipeline.py::test_bad_pixel_elements_are_kept`. Spec S0 and
     ledger R3 are updated.
   - The bases above were relearned after the fix.
2. **The leave-one-out table of `learn-artifacts`** raised "the CO2
   residual needs bands inside 1940–2100 nm" on every real observation,
   because the WA's edge columns have no wavelengths. It now scores the
   columns that have them. Test:
   `test_cli.py::test_leave_one_out_skips_columns_without_wavelengths`.
3. `saveArtifactBasis` writes a compressed `.npz`: 0.21 MB instead of
   2.2 MB, since the basis is 0 outside the fit window.

## Decisions (2026-10-21)

Michael took the recommendations. The tests keep every shortfall as
`xfail(strict=True)` naming its decision, so a fix turns it red and the
marker goes.

- **D9, the V2 Pearson r ≥ 0.90: (a) accepted as not met for Phase 1, plus
  (c) a follow-up diagnosis.**
  - Phase 1 is gated on the biases (all pass but D2300, D11) and, beside r,
    reports a scale-aware measure: the RMS of ours − MTRDR over the
    crater-floor repeatability. `compareMaps` and `validate`'s
    `report.json` (`rmsDifference`) give it;
    `test_frt5850_parameter_rms_achieved` pins it (D2300 1.94, BD2290 1.07,
    BD1900_2 1.47, OLINDEX3 1.36, BD2310 0.78: regression bounds, not
    tolerances).
  - OLINDEX3 waits for proxy EGN (Phase 4).
  - Destripe stays off by default ((b) not taken): it lifts BD2310 to
    0.902 but leaves the smooth trend and the other three short.
  - The follow-up diagnosis of the smooth cross-track trend is not started.
    Candidates, in order: an ESC residual quadratic in the smile offset
    (D2300, BD2290), the per-column ATM fit (BD2310), CAT's per-column patch
    from the aux files in the CO2 window (BD1900_2).
- **D10, the 2 µm bowl: (a) accepted as a known difference from CAT**
  (ledger A6/A7). The user guide states it with its size.
- **D11, the D2300 bias** (−0.0019 against 0.00152): accepted with D9, the
  same cross-track cause.
- **D12, the basis and the mineral parameters: (c) tried, then (b).**
  - (c): the basis now corrects 1940–2100 nm only
    (`atmosphere.ARTIFACT_WINDOW_NM`, `appliedArtifact`; the provenance's
    `artifact.windowNm`). The window holds the score window, so the basis's
    unit RMS and the leave-one-out metric are unchanged.
  - Re-test (the three FRSs, the bundled bases, with − without):

    | FRS (scan) | CO2 per pixel with / without | D2300 shift | BD2290 | BD1900_2 (≤ 0.0016) |
    |---|---|---|---|---|
    | FRS00031442 (061C4) | 0.00398 / 0.00770 | 0.00000 | +0.00003 | +0.00143 |
    | FRS0002FE75 (0A93E) | 0.00530 / 0.00741 | +0.00001 | −0.00002 | **+0.00164** |
    | FRS00031260 (08608) | 0.00426 / 0.00731 | −0.00002 | 0.00000 | **+0.00388** |

    The window keeps nearly all of the CO2 gain (before: 0.00397, 0.00517,
    0.00420) and no longer moves D2300 (before: up to −0.0018). BD1900_2
    still moves beyond the bound, as expected: its 1985 nm band and its
    2067 nm continuum anchor (`validation.summaryParameter`) lie among the
    CO2 lines.
    The overlaps with the crater-floor MTRDRs cannot say which BD1900_2 is
    right (see above).
  - (b): so `artifactModel` is **off by default**. V3 passes without the
    basis. `--artifacts <folder>` (or the dialog's checkbox) turns it on;
    the window applies whenever it is on.
- **D13, FRS00031442's BD2310 offset** (0.0055, 0.0051 against 0.0045):
  **(a) accepted as a per-observation systematic term**, for the fusion
  stage's offset correction (F2) to absorb. The user guide states it.
- **V1 note** (δ z-score std 0.875 on seed 1): not decided; the test's
  0.8–1.25 stays.

## Figures

- `figures/v2_spectra_before_after.png`: TRR3 ÷ cos i, ours and the MTRDR
  at a hydrated, an olivine-bearing and a bland pixel of FRT00005850.
- `figures/co2_by_column.png`: the CO2 residual by detector column.
  FRT00005850 ours and the MTRDR; the FRSs with and without the basis.
- `figures/v2_map_{d2300,bd2290,bd1900_2,olindex3,bd2310}.png`: ours, the
  MTRDR and their difference.
- `figures/v2_brightness_ratio.png`: the brightness ratio map and its
  along-track profile against the emission angle.
- `figures/v2_shape_ratio.png`: the shape ratio against the tolerance and
  CAT's artifact spectra.
- `figures/v2_column_profiles.png`: each parameter's per-column median in
  ours and in the MTRDR.

## Not done

- **HRL000040FF has no TRR3 in the demo data**, so it is not in V2.
- **FRS0002FE75 is not in the overlap study:** only 3 registration tiles
  against FRT00005C5E; the prototype's chain needs 8.
- **V4 (CAT outputs):** none were produced.
