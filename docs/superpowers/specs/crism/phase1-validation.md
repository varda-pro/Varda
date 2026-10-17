# CRISM Phase 1 validation (Task 23)

Status: run 2026-10-16 on the demo data. Design and tolerances:
`../2026-10-16-crism-processing-design.md` ("Validation design", approved
as D4). Decisions per step: `evidence-ledger.md`. Tests:
`src/varda/crism/_tests/test_regression.py` (V2, V3, the artifact basis;
data-gated, about 10 minutes) and the synthetic V1 tests. Scripts behind the
attribution: `measurements/task23/`. Processing outputs are outside the
repository.

## Summary

- **Met:** the MTRDR grid exactly; geolocation (−0.14, −0.08 px); a CO2
  residual below the MTRDR's on FRT00005850 (0.0067 against 0.0082); V3's
  CO2 residual on all three rim FRSs, with and without the artifact basis;
  the spectral-shape χ² of FRS00031442 against the crater-floor MTRDRs; the
  artifact basis's leave-one-out check under every scan the pipeline
  chooses; four of five parameter biases.
- **Not met (10 xfails, each with its measured cause):**
  - **Pearson r ≥ 0.90 for all five parameters** (0.63–0.90). Not noise:
    the two products share their pixel-scale fluctuations. OLINDEX3 is EGN
    (outside Phase 1). The other four are a cross-track, band-structured
    difference inside Phase 1 that no single step toggle removes.
  - **Spectral shape ±1%/±2%.** A 2 µm bowl of up to 3.4% that follows
    CAT's artifact patch, which the MTRDR has and we do not (inside Phase 1,
    ledger A6/A7); beyond 2.2 µm a 1–1.35% offset that follows the phase
    angle (EGN).
  - **D2300 bias** −0.0019 against a repeatability of 0.00152 (the same
    cross-track structure).
  - **The basis's effect on the mineral parameters** exceeds the
    repeatability in two of nine cases (D2300 on FRS0002FE75, BD1900_2 on
    FRS00031260).
  - **FRS00031442's BD2310 offset** from two crater-floor MTRDRs (0.0055,
    0.0051), against an MTRDR–MTRDR maximum of 0.0045 and the spec's 0.004.
- **Two defects found and fixed** (with regression tests):
  - The pipeline masked every element flagged in the BP CDRs. The
    calibration has already interpolated those elements (DPSIS §2.6), and
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
- Block means barely help. r at 2×2, 4×4 and 8×8 is 0.790, 0.800 and
  0.813 for D2300, and the other parameters rise by 0.03 at most.
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

**D2300, BD2290, BD1900_2, BD2310: cross-track, band-structured, inside
Phase 1.**
- Ours has 2–4× the MTRDR's column-to-column scatter: high-passed
  per-column medians of D2300 0.00081 against 0.00039, BD2290 0.00062
  against 0.00023, BD1900_2 0.00111 against 0.00029, BD2310 0.00066
  against 0.00028.
- D2300 also has a smooth trend in ours: 0.5–1% low at 2120 and 2170 nm on
  columns above 480.
- The per-column spectral factor q(x, λ) = median(ours ÷ MTRDR) reproduces
  the column component when it is applied to the MTRDR. It explains 94% of
  it for D2300, 88% for BD2290, 93% for BD2310 and 45% for BD1900_2.
- All of that is in q's band-to-band structure: a smooth cubic in λ per
  column, which is what EGN would be, explains 0–1% (9% for OLINDEX3).
- q varies most across columns in the CO2 window (0.4–0.6% std at
  1975–2040 nm), at the 1.65 µm filter join (0.55%), and at 0.16–0.2%
  elsewhere.
- At 1985 and 2067 nm it anticorrelates with CAT's per-column artifact
  patch (r = −0.5, −0.3), which the MTRDR has. At 2.1–2.4 µm, where the
  patch is zero, it follows neither the patch nor T.

Step toggles (FRT00005850, each from the default recipe; Pearson / median
bias; CO2 per-pixel median; the shape ratio's minimum):

| Run | D2300 | BD2290 | BD1900_2 | OLINDEX3 | BD2310 | CO2 | shape min |
|---|---|---|---|---|---|---|---|
| default | 0.785/−0.0019 | 0.774/+0.0002 | 0.627/−0.0004 | 0.895/−0.0038 | 0.883/−0.0002 | 0.00665 | 0.966 |
| destripe on | 0.795/−0.0019 | 0.801/+0.0002 | 0.663/−0.0005 | 0.895/−0.0038 | **0.902**/−0.0002 | 0.00653 | 0.966 |
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
- A post hoc classic column-ratio destripe of ours (each band ÷ its column
  median over a 15-column running median) does not help: r changes by
  −0.01 to +0.02.
- So the column-to-column scatter is not the whole story. The fitted shift
  and the missing destripe each cost a few hundredths. The rest is
  per-band, per-column structure that the MTRDR's own processing removes
  and that no Phase 1 toggle reproduces: CAT's per-column artifact patch
  explains part of it in the CO2 window.

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

The products of the default recipe, with the BP fix:

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
| FRS00031442 overlap (ruling P4) | ≥ 1000 px at 18 m | 48,488 (FRT00005C5E), 22,627 (FRT000047A3), 46,057 (HRL000040FF) | pass |
| Shape χ², as delivered | ≤ max MTRDR–MTRDR (171.3) | 64.1, 5.6, 12.1 | pass |
| Shape χ², after the gains | ≤ max MTRDR–MTRDR (9.5) | 5.7, 3.6, 5.3 | pass |
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

The bundled files are `resources/artifacts/{061C4,0A93E,08608}_AT.npz`
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

Every leave-one-out reduction is at least 30% (smallest: 33%), so
`artifactModel` stays on by default.

**The basis's effect on the mineral parameters** (median with − without,
over the pixels valid in both; the bound is the crater-floor
repeatability):

| FRS (scan) | D2300 (≤ 0.00152) | BD2290 (≤ 0.00103) | BD1900_2 (≤ 0.00189) | BD2310 | OLINDEX3 |
|---|---|---|---|---|---|
| FRS00031442 (061C4) | −0.00071 | −0.00001 | +0.00122 | −0.00013 | −0.00001 |
| FRS0002FE75 (0A93E) | **−0.00177** | −0.00007 | +0.00176 | −0.00117 | −0.00001 |
| FRS00031260 (08608) | −0.00094 | −0.00000 | **+0.00351** | −0.00063 | +0.00001 |

- A leave-one-out basis gives nearly the same shifts (−0.00168 and +0.00313
  for the two that fail), so this is the basis itself, not in-sample
  learning.
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
   - DPSIS §2.6: the a priori bad-pixel mask "is applied to an image by
     replacing each bad pixel with the average of the two pixels in
     adjacent columns". §2.15 does the same after the fact.
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

## Decisions for Michael

Each is a tolerance or design decision. The tests mark the shortfalls
`xfail(strict=True)`, so they turn red when the cause is fixed and the
marker should go.

- **D9. V2 Pearson ≥ 0.90.**
  - OLINDEX3 (0.895) is EGN. Proposed: accept it until proxy EGN
    (Phase 4), or gate OLINDEX3 on r after removing the along-track
    component (0.999).
  - D2300, BD2290, BD1900_2 and BD2310 (0.63–0.88) are inside Phase 1.
    The shortfall is the MTRDR's cross-track, band-structured processing
    (CAT's per-column artifact patch, its RSC) that ours does not
    reproduce. No toggle reaches 0.90. Destripe on and no fitted shift
    together give 0.804, 0.837, 0.734 and 0.908, but no shift doubles the
    CO2 residual (0.0123, failing V2's CO2 criterion).
  - Options:
    - (a) Accept the tolerance as not met for Phase 1 and gate on the
      parameter biases, which pass except D2300.
    - (b) Reconsider destripe's default: it helps every parameter here
      (+0.01–0.04, CO2 0.00665 → 0.00653), against Task 15's slight
      cross-validated harm on FRS00031442.
    - (c) A Phase 1.5 task to apply CAT's VS ADR artifact spectra when the
      aux files are present (D3).
  - The fitted shift trades r against the CO2 residual. Keep it?
- **D10. The 2 µm bowl** (shape ±1/±2%): up to 3.4%, following CAT's patch.
  - (a) Accept as a known difference from CAT (A6/A7).
  - (b) Add the VS ADR artifact spectra as an optional low-pass patch (aux
    data, not PDS).
  - (c) A learned low-pass basis. It is degenerate with β (A6), so it
    would need the MTRDRs as its truth.
- **D11. D2300 bias** −0.0019 against 0.00152: the same cross-track cause as
  D9.
- **D12. The basis and the mineral parameters.** It halves the FRS CO2
  residual (0.0074–0.0077 → 0.0040–0.0052 per pixel), but moves D2300 on
  FRS0002FE75 (−0.0018) and BD1900_2 on FRS00031260 (+0.0035) beyond the
  repeatability. V3 passes without it.
  - (a) Keep it on, and treat BD1900_2 and D2300 on the rim with the shift
    as a systematic term.
  - (b) Turn `artifactModel` off by default.
  - (c) Mask the basis outside the CO2 lines, for example 1940–2100 nm.
    That needs a re-test.
- **D13. V3 BD2310 offset** (0.0055 against 0.0045/0.004): between
  observations, not processing. The MTRDR pairs already exceed 0.004.
  Proposed: compare against the MTRDR–MTRDR maximum only, measured, and
  read the offset as a per-observation term for fusion (F2).
- **V1 note.** The δ z-score std on seed 1 is 0.875, outside the spec's
  1.00 ± 0.10; the test asserts 0.8–1.25. Tighten the test or accept the
  spread?

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
