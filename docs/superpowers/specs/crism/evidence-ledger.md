# CRISM processing: evidence ledger

One entry per algorithmic decision. Each gives the decision, its source, a
confidence rating and the test that pins it down. Tests marked *(planned)*
are written in Phase 1, test-first. Design:
`../2026-10-16-crism-processing-design.md`.

**Confidence:**
- **High:** primary documentation, or our own measurement on the data.
- **Medium:** a secondary source, or one measurement on few scenes.
- **Low:** our inference, to be confirmed by the pinning test.

## Source policy log

- **CAT IDL source:** none read (2026-10-16).
- **CAT behavior:** taken only from the 7.4 and 7.3.1 release notes, the 7.4
  history symbols, the User's Guide (the posted guide is the v6 guide),
  `cat_setup.pdf`, and the aux-zip file listing (its central directory, read
  by HTTP range request).
- **Flagged, pending decision D3:** the PDS *data label* of a CAT aux file,
  `ADR50924300803_28D04_VS00L_9.LBL`, read on a third-party mirror
  (davinci.asu.edu). It gave the VS ADR layout: 3 lines × 640 × 438, which
  are the transmission, the McGuire artifact spectrum and the Pelkey
  artifact spectrum, with interpolation wavelengths 1764 and 2239 nm. It is
  a data description, not code.
- **n2n4m:** the repository is MIT (LICENSE in its initial commit,
  2024-01-29), not unlicensed as the brief assumed. Only the README and
  paper have been read so far.
- **jezero-rim-mapping and pycrism** (MIT), **crism_ml** (Apache-2.0; data
  CC-BY-4.0): read as research. No code copied yet. Adaptations will carry
  attribution in the module docstring and in `THIRD_PARTY_NOTICES`.

## Decisions

### Reading

**R1. Pixels come through GDAL (rasterio). Labels are parsed by Varda.**
- **Source:** M8. Tested on real labels: GDAL's PDS3 driver has explicit
  CRISM `OBJECT = FILE` support (`pdsdataset.cpp:1510-1516`) and the PDS4
  driver gets layout and nodata right. But GDAL reports nodata as -3.4e38
  for PDS3, gives no wavelengths and gives no DDR band names.
- **Confidence:** high.
- **Test:** `src/varda/crism/_tests/test_readers.py::test_trr3_layout_and_nodata` and
  `::test_ddr_band_names`, on a checked-in crop.

**R2. TRR3 layout.**
- **Decision:** BIL (line, band, sample), float32 LE. A ROWNUM table follows
  the image: MSB uint16 & 0x1FF, 438 rows. The L detector is stored with
  wavelength descending, and Varda presents it ascending. The 65.535 µm
  placeholder band is flagged bad.
- **Source:** DPSIS §3.4.1 and App. C2; M3; CAT v6 User's Guide
  ("Wavelength Flipped").
- **Confidence:** high.
- **Test:** `src/varda/crism/_tests/test_readers.py::test_rownum_table` and
  `::test_ascending_wavelengths`.

**R3. Invalid samples.**
- **Decision:** 65535 (DPSIS App. L §2.2); values below 1e-6 (denormals
  such as 1.5e-44 occur, M3); columns outside the WA-valid range (29–632 for
  WA 490947778566, M4); elements flagged in the BP CDR.
- **Confidence:** high for 65535 and WA; medium for the 1e-6 floor.
- **Test:** `src/varda/crism/_tests/test_readers.py::test_trr3_layout_and_nodata` (65535, below 1e-6 and non-finite
  samples; the WA column range and BP flags are applied in later tasks).

**R4. DDR.**
- **Decision:** 14 bands, BSQ, in label order: INA areoid, EMA areoid,
  phase, lat, lon, INA MOLA, EMA MOLA, slope, slope azimuth, elevation (m),
  TI, albedo, LST, spare.
- **Source:** DPSIS §3.2.1 and App. B; the local DDR labels. jezero's
  "band 14 = altitude" is wrong (the label says Spare).
- **Confidence:** high.
- **Test:** `src/varda/crism/_tests/test_readers.py::test_ddr_band_names`.

**R5. MTRDR/TER.**
- **Decision:** read the ENVI `.hdr` (nodata, wavelengths, fwhm, bbl, CRS).
  Its georeference is correct. GDAL's reading of the PDS3 and PDS4 labels is
  one pixel west and north of it.
- **Source:** M1. The MTRDR DE lat/lon agree with the `.hdr` UL-corner
  convention to 0.1–0.2 m (median); the prior-art test reproduces the
  one-pixel label offset.
- **Confidence:** high.
- **Test:**
  `src/varda/image_loading/data_sources/_tests/test_crism_data_source.py::test_hdr_georeference_matches_de_backplanes`
  (data-gated; the median signed offset of 200 DE pixels is within 3 m, the
  per-pixel scatter about 10 m), `::test_mtrdr_label_uses_hdr` and
  `::test_mtrdr_round_trip_metadata`.

### Calibration

**C1. Wavelengths.**
- **Decision:** the per-column center wavelengths come from the WA CDR
  named in `MRO:WAVELENGTH_FILE_NAME`. The band wavelengths shown in Varda
  are the median over IR columns 270–369.
- **Source:** DPSIS Table 3-20 (SW/CT sweet-spot columns); M4.
- **Confidence:** high.
- **Test:** `test_calibration.py::test_finds_local_cdr_folder`.

**C2. AT transmission.**
- **Decision:** AT CDRs are used per detector element (scene column x ↔ AT
  column x), with no wavelength resampling, as TER does with T[x, λ].
- **Source:** DPSIS App. P1 §2. M5: the AT data are bit-identical across
  files that cite different WA references, so they are defined on detector
  elements.
- **Confidence:** high.
- **Test:** `test_atmosphere.py::test_transmission_indexing` *(planned)*.

**C3. Transmission candidates.**
- **Decision:** by default, the 6 distinct PDS AT scans. Optionally, the 17
  CAT VS ADRs (D3). Deduplicate by content hash, since the 20 local files
  hold 6 scans (M5).
- **Confidence:** high.
- **Test:** `test_calibration.py::test_dedupe_transmissions` and
  `::test_cat_aux_vs_adrs` (17 CAT scans).

**C4. Validity period.**
- **Decision:** select the AT by SCLK validity. Past the last period (the
  2014 FRSs), use the latest period and record the extrapolation. Scoring
  (A3) makes the final choice.
- **Source:** AT labels (M5).
- **Confidence:** medium.
- **Test:** `test_calibration.py::test_period_extrapolation_recorded` and
  `::test_period_containing_sclk_first`.

### Photometry

**P1. Lambertian photometry.**
- **Decision:** I/F ÷ cos(i), with i from a least-squares polynomial fit to
  DDR band 1 in sensor space: c0 + c1x + c2x² + c3t + c4t².
- **Source:** DPSIS App. P1 §1; Seelos 2024 §5.
- **Confidence:** high.
- **Test:** `test_photometry.py::test_polynomial_incidence` (recovers a
  synthetic smooth i under 16 ppd quantization).

**P2. Pluggable angles.**
- **Decision:** the incidence and emission arrays are inputs, so DTM angles
  and Lommel–Seeliger plug in later.
- **Source:** design.
- **Test:** `test_photometry.py::test_lommel_seeliger_reference_geometry`
  *(planned, Phase 4)*.

### Noise and σ

**N1. Noise estimator.**
- **Decision:** σ0 comes from along-track second differences (the same
  detector element, so stripes cancel), using MAD, over low-gradient pixels.
  Then a σ² = a + bR fit per band.
- **Source:** Green 1988 shift-difference (standard); Kreisch 2017 App. A
  (var ∝ μ, a Poisson-like model).
- **What we know so far:** a horizontal shift-difference gives an upper
  bound of 0.00083–0.00091 I/F at I/F ≈ 0.1 (M6).
- **Confidence:** medium, because TRR3 IKF filtering may alter the noise
  statistics.
- **Test:** `test_noise.py::test_recovers_injected_sigma` (synthetic,
  within 3%), `test_noise.py::test_stripes_do_not_inflate_sigma`,
  `test_noise.py::test_shot_noise_slope` (within 10%), and
  `test_noise.py::test_all_nan_band_is_nan_without_warnings`; the end-to-end
  χ² check (V1) *(planned)*.

**N2. Random and systematic uncertainty are kept apart.**
- **Decision:** σ holds random uncertainty only. The systematic terms
  (scan choice, artifact amplitude, destripe frame) are reported per band
  in the provenance.
- **Source:** fusion prototype: instrument-noise χ² understates
  disagreement (shape χ² ≈ 5.6, M9).
- **Confidence:** high, as a design principle.
- **Test:** `test_pipeline.py::test_sigma_chi2_synthetic` *(planned)*.

### Despike

**K1. Spike test.**
- **Decision:** flag a sample when |R − R̂| / √(σ0² + σ_p²) > 5 **and** it
  is a local extremum in λ. Replace it with R̂ and record a mask.
- **Source:** Parente 2008 (the BPS extremum criterion); Plebani 2022 §5.1
  (5σ cascaded medians); Leask 2018 (residual IKF artifacts, column
  clustered).
- **Confidence:** medium.
- **Test:** `test_despike.py::test_recall_and_false_rate` (≥ 95% recall at
  ≥ 8σ; ≤ 1e-5 false flags) *(planned)*.

### Atmospheric correction

**A1. Model.**
- **Decision:** a log-linear Beer–Lambert model over 1850–2200 nm,
  ln R = Σ c_k P_k(λ) + β [ln T_x + δ_x ∂ln T_x/∂λ].
- **Source:** McGuire 2009 Eq. 1 (the model I/F = A cos i T^β); the
  linearized shift is our extension. Wiseman 2016 §3.3 explains why one
  exponent can't fix everything, and the residual model (A6) handles that.
- **Confidence:** medium-high; M10 shows it beats the two-wavelength
  scaling.
- **Test:** `test_atmosphere.py::test_recovers_beta_and_shift` *(planned)*.

**A2. Fitting β across the window, not at two wavelengths.**
- **Decision:** β is a least-squares fit over the window, not the ratio at
  two wavelengths.
- **Source:** McGuire Eq. 4. Propagating errors through it gives σ_β² ≈
  (σ1²/y1² + σ2²/y2²) / ln²(T1/T2), which is noisy for the v2 pair. M7 and
  M10: the classic method leaves 0.0083–0.0089 against 0.0054–0.0060 for
  the fit.
- **Confidence:** high.
- **Test:** `test_atmosphere.py::test_beta_sigma_below_two_wavelength`
  *(planned)*.

**A3. Transmission selection.**
- **Decision:** score every candidate after the shift fit. The score is
  the median over columns of the σ-weighted residual RMS over
  1947–2066 nm. Pick the minimum and record the table and the margin.
- **Source:** DPSIS App. P1 §2 (the TER window); CAT 7.2 release notes (an
  empirical search over all scans on a central subset); M10. Scan 08608
  wins after the shift fit but scores 0.018 without it, so selection must
  be joint and must use the same quantity as the fit.
- **Confidence:** high.
- **Test:** `test_atmosphere.py::test_selects_injected_transmission`
  *(planned)*.

**A4. A smooth per-column shift.**
- **Decision:** fit δ_x per column, then smooth it with a robust low-order
  polynomial in x.
- **Source:** McGuire 2009 §2 (thermal drift ≤ about 1 nm); Seelos 2024
  §5.4 (TER selects "by fitted position of the 2000-nm CO2 triplet"). The
  fitted shifts on the FRSs are small: −0.5 to 0 nm (M7).
- **Confidence:** medium.
- **Test:** `test_atmosphere.py::test_shift_recovery` (±0.8 nm injected,
  within 3σ) *(planned)*.

**A5. Elevation-regularized β (default).**
- **Decision:** fit ln β = a − z/H to the scene, then shrink each pixel's β
  toward the model by its σ. Fall back to per-pixel β if H falls outside
  5–20 km.
- **Source:** our inference from physics: the CO2 column scales with
  surface pressure, and the Mars scale height is about 11 km. The aim is to
  protect β from the 1.9 µm hydration bands inside the window.
- **Confidence:** low until tested.
- **Test:** `test_atmosphere.py::test_beta_regularization_resists_h2o_band`
  (synthetic, a strong 1.9 µm band unit) *(planned)*, plus the V2/V3 CO2
  residuals.

**A6. Residual (artifact) model.**
- **Decision:** a high-pass, per-transmission artifact basis (M = 1),
  learned across observations and never from the scene alone. A per-pixel
  amplitude is fitted with β, with the prior α ∝ β.
- **Source:**
  - What CAT's patch fixes: the 2 µm bowl and hash (Morgan 2011; Wiseman
    2016 §2.1, CAT 7.2.1).
  - M7: the post-fit residual is shared across three FRSs (r = 0.75–0.93),
    and removing it takes 0.006–0.008 down to 0.003.
- **Confidence:** medium.
- **Tests:** `test_atmosphere.py::test_artifact_basis_leave_one_out` and
  `::test_artifact_model_preserves_mineral_band` *(planned)*. If D3 allows,
  a comparison with the VS ADR artifact spectra.

**A7. A curve-of-growth term is not added.**
- **Decision:** no γ (ln T)² term in Phase 1.
- **Source:** M10: no gain under the selection metric (0.0104–0.0109 with
  it).
- **Confidence:** medium. To be revisited after A6 if a bowl remains.
- **Test:** none. This is a recorded negative result.

### Destripe

**S1. Residual destriping after ATM.**
- **Decision:** destripe after ATM, as residual cleanup, because TRR3 I/F
  is already RSC-filtered.
- **Source:** DPSIS App. N (the TRR3 filter chain) and App. P1 §3 (RSC
  after ATM in TER); M11 (0.43–0.76% residual column scatter, an upper
  bound).
- **Confidence:** high.
- **Test:** `test_destripe.py::test_removes_injected_stripes` *(planned)*.

**S2. Protecting geology.**
- **Decision:** remove the high-frequency-in-λ part of the column factor
  always. Remove the smooth part only if it is consistent across split
  halves. Estimate from bland pixels.
- **Source:** Parente 2008 (robust low-pass in the cross-track–spectral
  plane; masking vertical edges); our inference that detector-element
  factors are independent in λ while geology is smooth in λ.
- **Confidence:** low-medium.
- **Test:** `test_destripe.py::test_preserves_column_aligned_unit` (≥ 95%
  of band depth) *(planned)*.

### Smile

**M1. Target vector.**
- **Decision:** the MTRDR IR standard vector (411 bands: rows 7–438 minus
  173–193), shared by all MTRDRs.
- **Source:** M2. The vector is identical in all six local MTRDRs and
  ≈ min-over-columns WA + 0.2–0.3 nm. DPSIS App. P1 §5 (ESC to the SW
  vector).
- **Confidence:** high.
- **Test:** `test_smile.py::test_target_vector_matches_mtrdr` *(planned)*.

**M2. Resampling.**
- **Decision:** linear interpolation per column. Then the TER-style ESC
  residual step: 0.125 nm bins, a weighted linear fit, normalization to the
  reference wavelength.
- **Source:** DPSIS App. P1 §5; M4 (at most 1.6 bands of smile).
- **Confidence:** medium.
- **Test:** `test_smile.py::test_resampling_error_and_sigma` *(planned)*.

### Projection

**G1. The MTRDR grid.**
- **Decision:** rolling equirectangular; φ_P = the equatorward 5°
  multiple; R = the IAU 2000 ellipsoid radius at φ_P; x = R cos φ_P λ;
  y = R φ; central meridian 0; 18 m (36 m HRL); polar stereographic beyond
  ±65°.
- **Source:** `mtr_map.cat` Table 1.0 and its equations; M1 (the 15° and
  3394839.8133 m values reproduced).
- **Confidence:** high.
- **Test:** `test_projection.py::test_grid_matches_mtrdr_labels`
  (FRT00005850, HRL000040FF) *(planned)*.

**G2. Tie point.**
- **Decision:** tie = (x(W) − s/2, y(N) + s/2), the UL corner of a pixel
  centered on the extreme DDR pixel centers. The origin is not snapped to a
  global lattice by default.
- **Source:** M1. The residual is 0.02 m for both local MTRDR labels;
  tie/s is fractional (245438.2).
- **Confidence:** high for the rule. Medium for which DDR samples define
  W/N (the 2300 nm geometry row, MOLA intercept).
- **Test:** `test_projection.py::test_tie_point_rule` *(planned)*.

**G3. Resampling.**
- **Decision:** nearest neighbor (GLT-equivalent) by default, with
  source-sample and source-line backplanes.
- **Source:** DPSIS App. P1 §12–13 (GLT); M1 (DE lat/lon scatter of 7–9 m
  std, the nearest-neighbor signature).
- **Confidence:** high.
- **Test:** `test_projection.py::test_backplanes_trace_to_sensor`
  *(planned)*.

### Validation metric

**V1. The CO2 residual metric.**
- **Decision:** spectrum ÷ a quadratic fit over 1940–2100 nm, RMS. Reported
  per pixel (median) and for the scene-median residual spectrum. It is used
  identically for our products and for MTRDRs.
- **Source:** M7 (MTRDR values: 0.0035–0.0103).
- **Confidence:** medium. A template-projection variant, the signed
  coefficient on the high-passed ln T, will be added as a secondary metric.
- **Bands:** each spectrum's own bands inside 1940–2100 nm, with no
  interpolation, so white noise scores the same on any band grid; sensor-space
  columns pass their own wavelengths. An MTRDR scores exactly as M7 measured
  it.
- **Test:** `src/varda/crism/_tests/test_validation.py::test_co2_metric_on_synthetic`;
  `test_validation.py::test_co2_metric_reproduces_m7` (data-gated:
  FRT00005850 MTRDR, 2× subsample, per-pixel median 0.008237, scene-median
  residual 0.008033); `test_validation.py::test_co2_metric_independent_of_band_grid`.

**V2. "BD2300".**
- **Decision:** the prototype's "BD2300" is 1 − R2310 / continuum(2160,
  2400) on 5-band means (`stage2b_diag.py:55-58`). It is not a
  Viviano-Beck parameter. Keep it as BD2310 for continuity, alongside the
  official D2300 and BD2290 (D7).
- **Source:** VB2014 Table 2; the PDS 2026 summary-parameter reference.
- **Confidence:** high.
- **Test:** `src/varda/crism/_tests/test_validation.py::test_bd2310_matches_prototype`;
  the Viviano-Beck parameters (ruling P2) in
  `test_validation.py::test_summary_parameter_depth`.

## Measurements (2026-10-16)

The scripts are in `measurements/` (scratch quality, kept for
reproducibility). The data are in
`varda-demo-data/data-for-paper/`.

### M1. The MTRDR grid

- **Projection.** Rolling equirectangular. The sphere radius is the IAU 2000
  radius at the equatorward 5° (Jezero at 18.3° N gives 15° and
  R = 3394839.8133 m). x = R cos(φ_P) λ and y = R φ, so the y origin is the
  equator. Polar stereographic beyond 65° (`mtr_map.cat` Table 1.0).
- **Tie point.** The ENVI tie point is the UL corner of pixel (1,1).
  WESTERNMOST_LONGITUDE and MAXIMUM_LATITUDE are the first column and first
  row centers: tie_x = x(W) − s/2 and tie_y = y(N) + s/2. The residual is
  0.02 m for FRT00005850 (18 m) and HRL000040FF (36 m). The origin is not
  snapped (tie/s = 245438.2).
- **DE backplanes.** The DE lat/lon agree with the `.hdr` convention to
  0.1–0.2 m (median). Their std of 7–9 m is the nearest-neighbor signature.

### M2. The MTRDR wavelength vector

- IF has 489 bands: 78 VNIR (436–1010 nm) and 411 IR (1047–3897 nm).
- The IR detector rows are 7–438 minus 173–193.
- The wv table is identical in all six local MTRDRs.
- IR FWHM is 9.92–17.48 nm.
- Each standard wavelength is about the minimum over columns of the WA
  wavelength + 0.2–0.3 nm.

### M3. The TRR3 file (FRS00031442 L)

- 190 × 640 × 438, BIL float32, with the ROWNUM table appended.
- 10.2% of samples are 65535, and denormals occur.
- Scene columns are 31–630.
- The label carries crismhdf RSC/IKF parameters, so the I/F is already
  filtered.

### M4. The WA CDR (490947778566)

- Valid columns are 29–632. Band spacing is 6.61 nm.
- At about 2007 nm the wavelength spans 2007.0–2017.5 nm across columns
  (about 1.6 bands of smile).

### M5. The AT CDRs

- The 20 local files are 6 distinct scans, and files from the same scan are
  bit-identical.
- Valid periods end 2014-01-09.
- An aerosol-slope fit was already applied (label text).

### M6. Noise on FRS00031442

- A horizontal shift-difference (MAD/√2, an upper bound) gives σ ≈
  0.00083–0.00091 I/F at I/F ≈ 0.093–0.104 (SNR 106–115) over 1.1–2.6 µm,
  and 0.00042 at 2009 nm.
- Median incidence is 55° (FRS0002FE75: 49°; FRS00031260: 53°).

### M7. The CO2-window metric

The metric is the RMS of the spectrum ÷ a quadratic fit over 1940–2100 nm.

- **Official MTRDRs:**

  | Observation | Per-pixel median | Scene-median residual |
  |---|---|---|
  | FRT00005850 | 0.0082 | 0.0080 |
  | FRT00005C5E | 0.0103 | 0.0099 |
  | FRT000047A3 | 0.0045 | 0.0037 |
  | FRT000066A4 | 0.0089 | 0.0086 |
  | FRT0001FB74 | 0.0084 | 0.0080 |
  | HRL000040FF | 0.0042 | 0.0035 |

  The residual has a classic over-correction shape: dips near 1955–1968 and
  2053–2067 nm, peaks near 2000 and 2020 nm.
- **FRS classic scaling** (1980/2007 nm), per-column medians: 0.0083–0.0089.
  With the best-scored AT, 0.0066–0.0091.
- **After the fit,** the residual common to all columns is 0.0061–0.0084.
  It correlates 0.75–0.93 between the three FRSs. Removing it leaves
  0.0030–0.0032.

### M8. GDAL 3.9.3

- It opens TRR3, DDR, MTRDR, AT and WA from `.lbl`.
- It does not set nodata 65535 on PDS3, and gives no wavelengths and no DDR
  band names.
- The PDS4 `.xml` path gets nodata right (prior-art test).

### M9. Fusion prototype (from its `summary.json`)

- Shape χ² 5.6.
- BD2310 between-observation scatter 0.0014 (36 m) and 0.0012 (144 m);
  median offsets up to about 0.004.
- BD1900 single-look repeatability 0.0016.

### M10. Log-linear fits on per-column medians

- **Setup.** The model is fitted over 1850–2200 nm and scored with the M7
  metric.
- **Without a shift term,** with AT 4909: 0.0101–0.0107.
- **With the linearized shift:** 0.0075–0.0081.
- **With a curve-of-growth term:** no gain.
- **The best of 6 scans × 4 models** is scan 08608 + shift: 0.0057, 0.0060
  and 0.0054 (FRS00031442, FRS0002FE75, FRS00031260). Without the shift,
  scan 08608 scores 0.018.
- The fitted shifts are −0.5 to 0 nm.

### M11. Residual striping in FRS TRR3

- The column-median ÷ 15-column running median has a std of 0.43–0.76%
  across 1.3–2.5 µm. That includes geology, so it is an upper bound.
- A naive 3×3 median spike count was not meaningful: its MAD is too
  small. Spike rates will be measured with the S1 noise model in Phase 1.
