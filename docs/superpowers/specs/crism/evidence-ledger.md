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
  detector element, so stripes cancel) of brightness-normalized spectra (so
  achromatic surface texture cancels; Task 9b), using MAD, over low-gradient
  pixels. Then a σ² = a + bR fit per band. On FRS00031442 the plain
  differences were 92-97% one achromatic component; σ fell to 0.20 of the
  plain estimate.
- **Source:** Green 1988 shift-difference (standard); Kreisch 2017 App. A
  (var ∝ μ, a Poisson-like model).
- **What we know so far:** a horizontal shift-difference gives an upper
  bound of 0.00083–0.00091 I/F at I/F ≈ 0.1 (M6).
- **Confidence:** medium, because TRR3 IKF filtering may alter the noise
  statistics.
- **Test:** `test_noise.py::test_recovers_injected_sigma` (synthetic,
  within 3%), `test_noise.py::test_stripes_do_not_inflate_sigma`,
  `test_noise.py::test_shot_noise_slope` (within 10%), and
  `test_noise.py::test_all_nan_band_is_nan_without_warnings`,
  `test_noise.py::test_texture_does_not_inflate_sigma` and
  `test_noise.py::test_real_sigma_is_the_chromatic_noise` (data-gated); the end-to-end
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
- **Decision:** a sample is replaced and recorded when all of these hold
  (k = 5):
  1. **Prediction.** R̂ is the inverse-variance combination of two
     estimators: the median of the 8 spatial neighbors (scaled by the ratio of
     medians over the whole spectrum, and moved to the pixel's column
     wavelengths along each neighbor's own slope) and the median of the 4
     other bands of a 5-band window (center excluded). Each error is its
     1.4826·MAD scatter, floored at the sample's σ.
  2. **σ_p** is the combined error, widened by the part of the gap between
     the two estimators beyond 3 combined errors (when they disagree, one is
     wrong: a unit boundary, a spectral cliff).
  3. **Score.** |R − R̂| / √(σ² + σ_p²) > k.
  4. **Extremum.** The residual R − R̂ (not R, so a spike on a ramp counts) is
     a strict local extremum in λ, and each band neighbor's residual is under
     0.4 of its own (a 3-band feature or a prediction wrong everywhere is not
     a spike).
  5. **Transient (persistence).** In units of the pixel's brightness L (the
     mean of its other bands within ±10 bands, inside the filter segment,
     `brightness.py`), the element's deviation from its own spectral median,
     (R − M) / L, must stand out from the median of the same element on the
     lines 2 to 4 before and after (not the adjacent lines, which a spike train
     shares) by 0.85 k errors, and by 12× the part of the element's spread over
     those lines that white noise (4σ²) does not explain. A multiplicative
     per-element pattern (a fixed share of the signal) and a slow along-track
     swing are therefore persistent, not spikes; a train of 1 to 3 lines is
     transient. Chunks need `ALONG_TRACK_HALO = 4` halo lines.
  - **Filter boundaries** (`FILTER_BOUNDARIES_NM`: the 1.65 µm and 2.7 µm
    joins, where spectra step per column): the spectral median never
    straddles one (the two bands either side take the spatial predictor only)
    and the extremum test does not compare across it.
  - The replacement is R̂ with σ = σ_p; a report holds per-pixel counts, a
    sparse (line, sample, band) table with absolute lines, and the Leask
    count (1850–2150 nm). With `halo` = 4 lines (`haloBelow` at scene edges)
    chunks give identical results.
- **Source:** Parente 2008 (the BPS extremum criterion); Plebani 2022 §5.1
  (5σ cascaded medians); Leask 2018 (residual IKF artifacts, column
  clustered). The combination, the disagreement widening, the isolation and
  transient tests and the filter boundaries are ours, found by measuring (the
  plain average and raw-R extremum gave ~10³ false flags on synthetic units
  and 2e-4 on FRS00031442; see the Task 11 report).
- **Confidence:** medium. On the synthetic cube, recall at ≥ 8σ is
  0.95–0.98 and false flags 0–1e-6; 1–3 line trains of 10–20σ are caught at
  0.87–0.98 per sample; on FRS00031442 lines 0–63 the overall flag rate is
  2.7e-4 with the instrument-only σ (Task 9b; 1.7e-3 before this fix, from
  per-element multiplicative band patterns of about 1%: bands 12, 16, 46, 48
  one-signed, 27 and 116 of both signs, against σ ≈ 0.09%). Not validated against known real spikes.
- **Expected on FRS00031442 lines 0–63:** three bands exceed 10× the overall
  rate, all clustered detector-element defects that the despiker should
  replace (not structure being eaten, which would spread over many columns):
  band 27 (1180 nm, 28 flags in 15 columns, mostly 58–64), band 210 (2386 nm,
  26 flags in 14 columns, 248–250 and 281–287) and band 189 (2247 nm, 24 flags
  in 7 columns, 10 in column 116). `test_real_chunk_has_no_hot_bands` fails a
  band only above 10× overall with flags in 20 or more columns.
- **Known limits:** the isolation test rejects a spike hitting two adjacent
  bands; single-spike recall inside the CO₂ band (1950–2080 nm) is about 0.94;
  the onset and end of a long element offset look like a train and are
  flagged; the two bands either side of a filter join rely on the spatial
  predictor alone (a one-sided spectral window was tried and flagged bands 99
  and 100 80 and 50 times in 64 lines).
- **Test:** `test_despike.py::test_recall_and_false_rate` (≥ 95% recall at
  ≥ 8σ; ≤ 1e-5 false flags), `test_preserves_narrow_real_feature`,
  `test_chunk_halo_equivalence`, `test_leask_count`,
  `test_short_trains_are_spikes`, `test_a_long_element_offset_is_not_a_spike`,
  `test_real_chunk_has_no_hot_bands` (data-gated).

### Atmospheric correction

**A1. Model.**
- **Decision:** a log-linear Beer–Lambert model over 1850–2200 nm,
  ln R = Σ c_k P_k(λ) + β ln T_x(λ + δ_x), with ln T_x(λ + δ_x) ln T
  interpolated along the column's wavelengths
  (`atmosphere.shiftedLogTransmission`) and δ_x fitted by iterated
  Gauss–Newton steps (the Taylor term η ∂ln T_x/∂λ only inside each step).
- **Source:** McGuire 2009 Eq. 1 (the model I/F = A cos i T^β); the
  linearized shift is our extension. Wiseman 2016 §3.3 explains why one
  exponent can't fix everything, and the residual model (A6) handles that.
- **Implementation (Task 12):**
  - **Shift by Gauss–Newton.** The model's atmosphere is ln T_x(λ + δ_x),
    ln T interpolated linearly along the column's wavelengths
    (`atmosphere.shiftedLogTransmission`, also used by the correction). Each
    step is the linear fit with the Taylor term η ∂ln T/∂λ about the current
    shift; the shift moves by η/β and ln T is re-interpolated until the step
    is below 1e-4 nm (a column still moving after 10 steps is invalid). One
    step is not enough: on the synthetic cube at δ = 0.8 nm it leaves
    residuals about twice the noise and the column β a median 12σ off;
    iterated, residuals are at the noise and β and δ have z-scores of mean
    ≈ 0, std ≈ 1.
  - **Hydration mask.** Bands in 1880–1945 nm (`HYDRATION_MASK_NM`) get
    zero weight; the degree-3 continuum still spans 1850–2200 nm. On the
    synthetic's library surface (seeds 0–2) the column β bias goes from
    −4.1…−4.8% to −0.5…−1.5%, and the true AT beats the depth decoy (it
    lost 3/3 without the mask); σ_β grows by about 20%. The FRS00031442
    choice is unchanged.
  - **Column spectra: the mean of ln R**, not the median. The model is
    linear in ln R, so the mean of a fixed set of lines with different β is
    the same model with the mean β, while the median takes each band from a
    different line (residuals 1.4–1.8× the noise, column β a median 5–11σ
    and up to 37σ off, with β varying ±14% down a column). A column averages
    only its lines complete in the fit window; with fewer than 8 such lines,
    the missing fit-window samples are filled from a rank-2 model of the
    column (an EM low-rank fill) before the mean: a plain per-band mean over
    different lines inflates the β z-score std to 1.5–1.6 at 10% scattered
    invalid samples, the fill brings it to 1.0–1.1.
- **Confidence:** medium-high; M10 shows it beats the two-wavelength
  scaling.
- **Test:** `test_atmosphere.py::test_recovers_beta_and_shift` (three
  seeds, smooth ±0.8 nm δ, the synthetic's surface replaced by its own
  continuum over the window so the formal σ can be checked),
  `::test_raw_surface` (seeds 0–2 on the library surface: column β median
  |bias| ≤ 2%, the true AT wins against the decoys, the smoothed δ within 3
  χ²-scaled σ), `::test_scattered_invalid_samples`,
  `::test_column_spectra_use_complete_lines`,
  `::test_unconverged_shift_is_invalid` and
  `::test_shifted_log_transmission_matches_interp`. On the library surface
  the residuals stay about 10× the noise (χ²_ν ≈ 85) and δ is biased by
  +0.16 to +0.18 nm: the residual is broad structure, the wing of the 1.9 µm
  water band past 1945 nm and a 1–2% bump at 2020–2040 nm under the deepest
  CO2 bands, not band-to-band roughness (smoothing the library spectra by a
  one-band Gaussian took their second-difference RMS from 0.013 to 0.005 in
  ln but δ only from +0.19…+0.22 to +0.16…+0.18 nm; even three bands leave
  +0.06 nm). `ColumnFit.reducedChiSquare` reports it. σ · √max(1, χ²_ν)
  is a Birge-ratio scaling (it assumes the misfit acts like extra white
  noise), not an error bar: the bias is systematic.
- **Known limitation:** on library-like surfaces δ is biased by about
  +0.17 nm (`test_raw_surface` pins |median bias| ≤ 0.25 nm); on the M7
  metric that costs about 0.003 of residual. Real surfaces with broad
  structure under the CO2 bands may carry the same bias.

**A2. Fitting β across the window, not at two wavelengths.**
- **Decision:** β is a least-squares fit over the window, not the ratio at
  two wavelengths.
- **Source:** McGuire Eq. 4. Propagating errors through it gives σ_β² ≈
  (σ1²/y1² + σ2²/y2²) / ln²(T1/T2), which is noisy for the v2 pair. M7 and
  M10: the classic method leaves 0.0083–0.0089 against 0.0054–0.0060 for
  the fit.
- **Confidence:** high.
- **Test:** `test_atmosphere.py::test_beta_sigma_below_two_wavelength`
  (synthetic pixels at SNR 100: σ_β 0.015 for the window fit, as its
  covariance predicts, against 0.038–0.042 for 1980/2007 nm).

**A3. Transmission selection.**
- **Decision:** score every candidate after the shift fit. The score is
  the median over columns of the σ-weighted residual RMS over
  1947–2066 nm. Pick the minimum and record the table and the margin.
- **Source:** DPSIS App. P1 §2 (the TER window); CAT 7.2 release notes (an
  empirical search over all scans on a central subset); M10. Scan 08608
  wins after the shift fit but scores 0.018 without it, so selection must
  be joint and must use the same quantity as the fit.
- **Confidence:** high.
- **Score (Task 12):** per column, √(Σ w r² / Σ w) over the score-window
  bands, w = 1/σ_lnR², the fit's weighted objective restricted to the
  window (in ln R units, comparable to M7), from the fit with a free
  per-column shift; the median over valid columns.
- **Ties.** Selection stays argmin. `SelectionResult` also records the
  relative margin (margin / best score) and the win fraction (the share of
  columns valid for both where the best's own column score beats the
  runner-up's). Scores are keyed by `Transmission.key`, scan and source
  (`061C4:AT`, `061C4:VS`), since an AT and a VS ADR can share a scan.
- **Phase 1 measurement (FRS00031442, 6 PDS scans, mean-of-ln column
  spectra, hydration mask, all 190 lines):** with the shift, 061C4 0.00597,
  08608 0.00606, 094B5 0.00773, 06822 0.00858, 09E04 0.00891, 0A93E
  0.01142; 061C4 is chosen by a margin of 0.00009 (relative 0.015, win
  fraction 0.517: a tie). The central 64 lines give the same order (0.00620
  against 0.00624, relative 0.006). Without the shift, 094B5 wins (0.01078)
  and 08608 and 061C4 are among the worst (0.0188, 0.0196), the M10
  pattern. Under the M7 metric 08608 edges out 061C4 (0.00597 against
  0.00616). Their fitted shifts are −0.83 and −0.91 nm. With both sources
  (23 candidates) a VS ADR wins: 2815C:VS 0.00525 against 32AFC:VS 0.00530
  (relative margin 0.008, win fraction 0.585).
- **Scored without the artifact basis (Task 14 ruling).** Selection fits
  and scores each candidate without A6's basis. With each candidate's basis
  the gap between scans compresses (in the Task 14 review 0A93E went from
  worst to near-best, and FRS00031260 would flip from 08608 to 061C4), so
  the score would mostly measure how well each basis was learned, not the
  transmission.
- **Test:** `test_atmosphere.py::test_selects_injected_transmission` (true
  AT against copies shifted 1.5 nm and scaled in depth, 1 − k(1 − T) with
  k = 0.9, 1.1; a power T^k is no decoy, since β absorbs k exactly) and
  `::test_selection_requires_shift_fit` (a decoy pre-shifted by 0.9 δ_x with
  a 0.4% band pattern wins without the shift term and loses with it).
  `::test_sources_sharing_a_scan_id` and
  `test_calibration.py::test_cat_aux_vs_adrs` (the 23 FRS00031442
  candidates have distinct keys).

**A4. A smooth per-column shift.**
- **Decision:** fit δ_x per column, then smooth it with a robust low-order
  polynomial in x.
- **Source:** McGuire 2009 §2 (thermal drift ≤ about 1 nm); Seelos 2024
  §5.4 (TER selects "by fitted position of the 2000-nm CO2 triplet"). The
  fitted shifts on the FRSs are small: −0.5 to 0 nm (M7).
- **Confidence:** medium.
- **Implementation (Task 12):** Tukey biweight IRLS (c = 4.685, MAD scale),
  degree 3 in the column index. On the synthetic's ±0.8 nm half-sine the
  cubic's own error reaches about 3σ of a column's δ at the edges.
- **Test:** `test_atmosphere.py::test_recovers_beta_and_shift` (±0.8 nm
  injected; the smoothed δ within 3σ in ≥ 99% of columns) and
  `::test_smooth_shift_resists_outliers`. The FRS00031442 shifts depend on
  the scan: −0.11 to −1.05 nm (median per scan), not just −0.5 to 0 nm.
  `smoothShift` also evaluates the polynomial at invalid columns, which at
  the FRS's empty edge columns is an extrapolation.

**A5. Elevation-regularized β (default).**
- **Decision:** fit ln β = a − z/H to the scene, then shrink each pixel's β
  toward the model by its σ. Fall back to per-pixel β if H falls outside
  5–20 km.
- **Source:** our inference from physics: the CO2 column scales with
  surface pressure, and the Mars scale height is about 11 km. The aim is to
  protect β from the 1.9 µm hydration bands inside the window.
- **Implementation (Task 13):**
  - **Per-pixel fit** (`atmosphere.fitPixels`): the column fit's design
    (cubic Legendre, β ln T_x(λ + δ_x) at the smoothed shift, the hydration
    mask, an artifact column when given) with T and δ fixed, so it is
    linear. Each column's solution operator is computed once, weighted by
    the column's mean σ_lnR², and applied to all its complete lines; pixels
    missing bands are solved on their own. The covariance comes from each
    pixel's own σ. A 64 × 120 × 438 chunk takes about 0.01 s, the whole
    FRS00031442 (190 × 640) 0.16 s.
  - **Elevation model** (`fitElevationModel(beta, betaSigma, elevationM)`):
    the pixels' formal σ_β is first rescaled to their empirical σ, from
    robust along-track second differences of ln β per column
    (`ElevationModel.sigmaRatio`, recorded for provenance). Then IRLS on
    ln β against z, weights 1 / (σ²_lnβ + τ²) times a Tukey biweight
    (c = 4.685) of the standardized residual. τ² is re-estimated with the
    same biweights (their weighted mean square that of unit Gaussian
    residuals, 0.828) and floored by the spatially coherent part of the
    residuals (the robust variance of their 7 × 7 means less the noise
    share). The final line and its σ come from the final weights.
    `accepted` needs H in 5–20 km and the slope ≥ 3σ from 0. The formal σ
    of H ignores spatially correlated residuals and is optimistic.
  - **The prior** is Gaussian in β with σ² from τ² and the line's own
    uncertainty (empirical Bayes); the free fit's σ_β it is weighed against
    is the empirical one. The augmented least squares is solved in closed
    form, a scalar update of the free fit, so the column operator is kept.
    The ratio sets only how far β moves: the covariance and gain stay the
    propagation of the data's σ through the updated operator (plus the
    prior's variance), so σ′ stays consistent (fix round 2: scaling the
    covariance by the ratio² made σ′ at 2009 nm 0.59 of the free fit's on
    FRS00031442 while β moved 0.04%; now 0.99). The honest σ of β is
    `PixelFit.betaSigmaEmpirical`.
  - **Why empirical σ (fix round 1):** with the formal σ, τ collapsed to 0
    on FRS00031442 (the ln β scatter about the law, 0.014, is below the
    formal σ_β/β, 0.017) and the prior pinned β to the law to 0.005%, while
    the real departures are coherent at 1–3% (7 × 7 means: std 0.0123
    against 0.0025 from noise). The 5% most deviant pixels' CO2 residual
    then got 18% worse (0.00686 → 0.00812), the 5% most coherently deviant
    17% (0.00585 → 0.00684). The TRR3's along-track noise is 96% common to
    the 53 fit-window bands: an achromatic component (texture or
    common-mode) that the continuum absorbs, so the empirical σ_β is 0.17 of
    the formal one, not half as the residual RMS (0.5 of σ_lnR) suggested.
    The noise model is being revised to exclude it (Task 9b).
  - **σ of the correction** (`correct`): ln R′ = (I − G) ln R for the fit's
    gain G, so σ′ includes the covariance of β with the data it corrects.
    Neglecting it gives a reduced χ² of 0.97–0.98 over the fitted bands and
    0.95–0.96 over the CO2 score window (the free fit); with it, 1.01–1.03
    (stripes on). `correct` peaks at about five copies of its input and runs
    per chunk.
  - **Synthetic results:** H 11.02 ± 0.04 (true 11); 30 km is found and
    rejected; shuffled elevations are rejected. With ln β departing from
    the law by a coherent field of std 0.01 (`betaScatter`, Gaussian of
    4 pixels; 128 lines), τ comes back as 0.0096–0.0097 and the β z-scores
    have std 1.00 free and 1.02–1.03 with the prior (seeds 0–3). With σ overstated 2× the
    ratio is 0.49–0.52, τ unchanged and the prior fit identical. With noise
    correlated across bands (1.5 bands) the formal σ_β is 1.4× too small;
    the ratio finds it (1.39–1.44) and the prior's z std is 0.89–1.19.
    With the hydration mask, a hydrated unit in an otherwise
    model-following scene has a free β bias of −3.6…−3.8% (2.0–2.2σ_β) and
    −0.5…−0.8% with the prior (0.9–1.1 of the prior's own σ_β).
  - **Known limitation (hydrated units on realistic surfaces):** on the
    full library surface the prior leaves the hydrated unit at −1.9…−2.3%,
    2.0–2.1 times the prior fit's own σ_β (the target was under 1): the
    elevation law inherits the scene-wide surface bias (−1% on the basalt)
    and τ (0.010–0.015, the units' coherent departures) keeps the prior
    weak. The prior cuts the bias to 0.49–0.64 of the free one. A6 and the
    continuum, not A5, have to remove a bias every unit shares.
  - **FRS00031442** (TRR3 ÷ cos i, `estimateNoise` σ; despiking changes
    nothing measurable; z from DDR ELEVATION, −2560…−1790 m, most pixels
    within 500 m; 92.8% of pixels valid): H = 10.61 ± 0.04 km with
    061C4:AT (accepted), 9.03 with 08608:AT, 13.98 with 2815C:VS and 16.45
    with 32AFC:VS (no artifact term yet), so H depends on the scan and its
    formal σ is far too small. σ ratio 0.16–0.17; τ = 0.013–0.015, the
    coherent departures, so the prior moves β by about 4% of its departure
    from the law: on this scene it is nearly inert, as it should be. CO2
    residual with 061C4:AT, free / prior: column spectra 0.00616 / 0.00615
    (Task 12's column-level 0.00616, Phase 0's 0.0054–0.0060), per pixel
    0.00746 / 0.00745, scene residual RMS 0.00529 / 0.00528; the 5% most
    deviant pixels 0.00694 / 0.00691, the 5% most coherently deviant
    0.00586 / 0.00582. With 08608:AT 0.00597 / 0.0071 / 0.0048 (column /
    pixel / scene); with 2815C:VS 0.00573 / 0.0069 / 0.0044.
- **Confidence:** medium on the synthetic; low-medium on real scenes until
  the scan dependence of H is understood and more scenes are measured.
- **Test:** `test_atmosphere.py::test_beta_regularization_resists_h2o_band`
  (a strong 1.9 µm band unit), `::test_pixel_beta_recovery`,
  `::test_elevation_model_recovers_H`,
  `::test_elevation_prior_uses_empirical_sigma`,
  `::test_prior_with_spectrally_correlated_noise`, `::test_correct_sigma_chi2`,
  `::test_co2_window_flat_after_correction`,
  `::test_correct_sigma_is_consistent_with_the_prior`,
  `::test_real_prior_spares_deviant_pixels` (data-gated),
  `::test_pixels_with_missing_bands`,
  `::test_rejected_elevation_model_is_not_a_prior`,
  `::test_fit_pixels_is_fast`; plus the V2/V3 CO2 residuals.

**A6. Residual (artifact) model.**
- **Decision:** a high-pass, per-transmission artifact basis (M = 1),
  learned across observations and never from the scene alone. A per-pixel
  amplitude is fitted with β, with the prior α ∝ β.
- **Source:**
  - What CAT's patch fixes: the 2 µm bowl and hash (Morgan 2011; Wiseman
    2016 §2.1, CAT 7.2.1).
  - M7: the post-fit residual is shared across three FRSs (r = 0.75–0.93),
    and removing it takes 0.006–0.008 down to 0.003.
- **Confidence:** medium for the 2014 FRSs; low for other periods until
  more observations per scan are processed (FRT00005850 below).
- **Implementation (Task 14):**
  - **Basis** (`atmosphere.learnArtifactBasis`, `ArtifactBasis`): per
    observation, the column spectra's part-A residual with one transmission
    (`SelectionResult.fit.residuals`), high-passed (`highPass`: less its
    least-squares cubic Legendre over the fit window, so a cubic is removed
    exactly) and scaled to unit RMS over the score window; the median across
    observations; a running median over 31 columns; high-passed again and
    scaled to unit RMS over the score window (one scale for all columns, so
    the variation across columns is kept). 0 outside the fit window and in
    the hydration mask (no data there). One basis per transmission key
    (`ArtifactBasis.transmissionKey`, `"061C4:AT"`; an AT and a VS ADR of
    one scan differ), stored as `resources/artifacts/<scan>_<AT|VS>.npz`
    (`artifactFileName`: ':' becomes '_') with a JSON metadata entry (key,
    source observations, the training SCLK span, Varda version). No basis is
    bundled yet (Task 23).
  - **Epoch gate (fix round 1):** `bundledArtifactBasis(key, samples,
    bands, sclk)` returns a `BasisLookup(basis, reason)`: the basis only
    when the file's key is the transmission's, its columns and bands are the
    cube's, and the SCLK is within the training span ± 180 days
    (`ARTIFACT_EPOCH_MARGIN_SCLK` = 15.55·10⁶ s); otherwise None and the
    reason, for the provenance. The 2014 FRS basis made FRT00005850 (2007)
    worse (below), so a basis does not carry across epochs.
  - **α ∝ β as a constraint:** α = c·β with one scene-level c, so the
    per-pixel atmosphere is β (ln T_x(λ + δ_x) + c a_x), one linear term:
    `AtmosphereModel(artifact=basis.spectra, artifactScale=c)`. c is
    `artifactScale`: the column spectra fitted with α free (T and the
    smoothed shift fixed), the median over columns of α / β, σ from the
    MAD as if the columns were independent, a noise-only lower bound (the
    31-column smoothing and the smoothed shift correlate them; c differs
    between the FRSs, 0.0052–0.0060, about 100× that σ). A NaN c (under 3
    valid columns) is rejected by `AtmosphereModel`: correct without the
    basis. The IN band's "artifact amplitude" is then c·β
    (`PixelFit.artifactAmplitude`), not a fitted α. A free α per pixel would be one more parameter for hydrated or
    noisy pixels to bias. σ′ is propagated as before (a free α keeps its own
    covariance; c's uncertainty is not propagated, a systematic term for the
    S4.E Monte Carlo); with the true artifact tied the correction's reduced
    χ² is 1.01 (fitted bands) and 1.04 (score window), as in A5.
  - **Identifiability (synthetic):** the synthetic artifact, (ln T)²
    high-passed, correlates −0.93 with the high-passed ln T, so the fit
    reads about 93% of it as β (−3% at RMS 0.006, −10% at 0.02) and δ. Only
    the rest, less its σ-weighted projection on the continuum, ln T and its
    shift term, can be learned from residuals; at RMS 0.006 that part is
    0.0016, below the stripes of column spectra. The part-C tests use RMS
    0.02, whose learnable part (0.0055) is what M7 found the FRS fits leave
    in common. The basis from four synthetic observations correlates 0.987
    (median column; worst 0.975) with that part, 0.50 with the whole
    artifact. Correcting with the biased β leaves a smooth 2 µm bowl of
    about −3% that a high-passed basis cannot hold by design (S4.E.1).
  - **Real data** (TRR3 ÷ cos i, instrument-noise σ, column spectra of all
    lines; PDS ATs). Each observation's own choice: FRS00031442 061C4
    (relative margin 0.006), FRS0002FE75 0A93E (0.026), FRS00031260 08608
    (0.037), FRT00005850 06822 (0.42). So the bases are learned under one
    fixed scan, 061C4:AT. The high-passed column-median residuals correlate
    0.86–0.99 between the FRSs and 0.47–0.58 with FRT00005850. CO2 residual
    (V1) with 061C4:AT, none → leave-one-out (basis from the other two FRSs)
    → in-sample (all three), as column spectra / per-pixel median / scene
    residual RMS:
    - FRS00031442: 0.00611 / 0.00744 / 0.00523 → 0.00311 / 0.00476 /
      0.00201 → 0.00243 / 0.00434 / 0.00131 (c = 0.0060, 0.0056);
    - FRS0002FE75: 0.00834 / 0.00962 / 0.00756 → 0.00484 / 0.00622 /
      0.00412 → 0.00420 / 0.00570 / 0.00350;
    - FRS00031260: 0.00622 / 0.00755 / 0.00530 → 0.00267 / 0.00472 /
      0.00160 → 0.00224 / 0.00445 / 0.00123.
    - Adding FRT00005850 to the training set makes each FRS's leave-one-out
      slightly worse (column 0.00336, 0.00500, 0.00328), and the three FRSs'
      basis makes FRT00005850's own residual worse (0.00793 / 0.00810 /
      0.00745 → 0.00848 / 0.00867 / 0.00773; c = 0.0047): a basis does not
      carry across from 2014 to 2007 (another WA, and 061C4 is a poor fit
      for it, 0.00886 against 06822's 0.00623).
  - **Mineral bands:** on the library surface the model moves BD2290 by
    0.00σ and BD1900_2 by 0.29–0.40σ (seeds 1–3; σ the unit's scatter;
    the test requires ≤ 0.5σ), and on a cube without the artifact (scene
    c ≈ −0.001, the basalt's structure) by ≤ 0.15σ (required < 0.25σ). The
    review confirmed the real basis is CO2 hash, not mineralogy. Against the artifact-free run BD2290 is within
    0.04σ, but BD1900_2 is 1.1–1.4σ off with the model and 0.7–1.0σ
    without: the bowl above sits under R1985 and the artifact under the
    hydration mask is never fitted. A learned basis cannot fix either
    without learning the ubiquitous 1.9 µm hydration band too.
  - **Surface leakage into c:** on a scene with no artifact the library
    basalt's structure gives c = −0.0007…−0.0016 (4–10σ), 10–25% of the
    injected 0.0063. c is a scene-level fit, so surface structure that
    projects onto the basis biases it.
- **Tests:** `test_atmosphere.py::test_artifact_basis_recovered`,
  `::test_artifact_reduces_co2_residual` (the M7 column-median metric of
  the atmosphere left, the corrected over the exactly corrected cube:
  0.0049 without the model, 0.0011 with; on the corrected cube itself
  0.0071 → 0.0060, the exact correction's 0.0059),
  `::test_artifact_model_preserves_mineral_band`,
  `::test_high_pass_removes_continuum`, `::test_round_trip_npz`,
  `::test_bundled_basis_lookup`, `::test_learn_rejects_mismatched_inputs`,
  `::test_artifact_scale_must_be_finite`,
  `::test_artifact_basis_leave_one_out` (data-gated: the three FRSs under
  061C4:AT, each held out reduced by ≥ 30%; completed in Task 23). If D3
  allows, a comparison with the VS ADR artifact spectra (Task 23).

**A7. A curve-of-growth term is not added.**
- **Decision:** no γ (ln T)² term in Phase 1.
- **Source:** M10: no gain under the selection metric (0.0104–0.0109 with
  it). Task 14 explains why: high-passed, (ln T)² correlates −0.93 with
  ln T, so a γ term is nearly degenerate with β;
  the learnable part of a curve-of-growth residual is what A6 models.
- **Confidence:** medium. A6 leaves a smooth bowl on the synthetic (about
  −3% at an artifact RMS of 0.02) that neither A6 nor a free γ can
  separate from β and the continuum; revisit if V2/V3 show a 2 µm bowl
  against the MTRDRs.
- **Test:** none. This is a recorded negative result.

### Destripe

**S1. Residual destriping after ATM.**
- **Decision:** destripe after ATM, as residual cleanup, because TRR3 I/F
  is already RSC-filtered. The cross-track model is a per-line LAD Legendre
  fit of degree 5 (IRLS, refitted to the destriped data, outliers beyond 5
  robust spreads set aside); ln f is the median over bland lines of
  ln R − ln R̃, kept only at the column scale (less a Gaussian-smoothed
  15-column running median, the scale M11 measures). The ±1-column ratio
  accumulation (4 profiles) is not used: with medians the profiles are one
  up to constants, and its random walk doubled the synthetic error (0.46%
  against 0.28% at 64 lines, SNR 100).
- **Source:** DPSIS App. N (the TRR3 filter chain) and App. P1 §3 (RSC
  after ATM in TER); M11 (0.43–0.76% residual column scatter, an upper
  bound). Task 15 on FRS00031442 (lines 0–189, PHT + ATM with 061C4:AT):
  the column-scale pattern of the column medians does not persist between
  the two halves of the lines (achromatic: correlation −0.05 to 0.13;
  band-to-band: −0.35 to 0.11), so M11 there is scene, not detector.
  Without the column-scale step the degree-5 model left persistent scene in
  the frame and raised M11 from 0.51% to 0.72%; with it, 0.51% → 0.52%
  (cross-validated, frame from lines 0–94 applied to 95–189: 0.69% → 0.72%).
- **Default:** off for TRR3 I/F input (`DestripeStep.enabled = False`,
  controller ruling after Task 15), available as an option: on FRS00031442
  M11 0.514% → 0.524%, cross-validated 0.693% → 0.720%; the method as first
  specified, 0.51% → 0.75%, cross-validated 0.69% → 1.32%.
- **Confidence:** high for the order; medium for the model (one real scene).
- **Test:** `test_destripe.py::test_removes_injected_stripes` (M11 metric
  against the truth: 0.56% → 0.045%, bar 0.05%).

**S2. Protecting geology.**
- **Decision:** estimate from bland pixels only (the lowest 50% of the
  column's continuum-removed variance, no 1.9/2.3/2.5 µm band depth above
  3σ); a column without 8 bland lines gets no factor. Split ln f into a
  per-column cubic in λ within each filter segment (s) and the rest (h).
  Remove s where present (RMS s/σ_s ≥ 3) and the same in both halves of the
  lines (RMS (s₁ − s₂)/σ ≤ 2); remove h per element where |h| > 3σ_h and
  |h₁ − h₂| ≤ 3σ. σ is a moving-block bootstrap over lines (50 resamples,
  16-line blocks, within each half). The spec's "h always" is gated because
  on FRS00031442 h is not persistent: removing it everywhere raised the
  cross-validated M11 to 0.75%.
- **Source:** Parente 2008 (robust low-pass in the cross-track–spectral
  plane; masking vertical edges); our inference that detector-element
  factors are independent in λ while geology is smooth in λ; Task 15's
  split-half analysis of FRS00031442.
- **Confidence:** low-medium. Known limit: geology that spans the whole
  strip with a width under ~15 columns and no band depth (an achromatic
  albedo streak) looks like a detector pattern; a synthetic 2% bump of σ = 6
  columns is altered by up to 0.55%.
- **Test:** `test_destripe.py::test_preserves_column_aligned_unit` (≥ 95%
  of band depth), `test_destripe.py::test_smooth_part_needs_split_half`.

### Smile

**M1. Target vector.**
- **Decision:** the MTRDR IR standard vector (411 bands: rows 7–438 minus
  173–193), shared by all MTRDRs.
- **Source:** M2. The vector is identical in all six local MTRDRs and
  ≈ min-over-columns WA + 0.2–0.3 nm. DPSIS App. P1 §5 (ESC to the SW
  vector).
- **Check:** the vector equals the IR rows of the SW CDR6 (L, versions 1, 5
  and 9) exactly (max |difference| 0.00 nm).
- **Confidence:** high.
- **Test:** `test_smile.py::test_target_vector_matches_mtrdr`.

**M2. Resampling.**
- **Decision:** linear interpolation per column. Then the TER-style ESC
  residual step: 0.125 nm bins, a weighted linear fit, normalization to the
  reference wavelength.
- **Source:** DPSIS App. P1 §5; M4 (at most 1.6 bands of smile).
- **Detail:** a filter join is a detector-row boundary (rows 346|345 in
  every column; row 346 spans 1651–1662 nm across columns), so segment
  membership is per band (`brightness.segmentIds` of the median wavelength)
  and a target never takes a tap from another segment (a per-column cut at
  1656 nm bridged it in about 9% of near-join cases on the real WA). A
  target outside a column's valid range, or with a NaN tap, is NaN.
  Per-band σ is the variance only; interpolation correlates adjacent bands
  (real WA: adjacent-band ρ median 0.18, p95 0.50, max 0.93), which σ does
  not carry.
- **ESC offset:** the regressor is `escOffsets` = WA(x at the target's
  detector row) − target (real smile −1.2…+15.2 nm, roughly parabolic across
  the columns), not the nearest native band's offset. On FRS00031442 smile
  |corr| falls 0.49 → 0.05 and column scatter by 25%, the same with bland or
  all pixels; ESC frames correlate 0.95–0.98 across scenes (the
  nearest-native offset gave −3%). The fit is repeated three times: one or
  two passes leave more than a tenth of the injected error.
- **Confidence:** medium.
- **Test:** `test_smile.py::test_resampling_error_and_sigma`,
  `test_smile.py::test_esc_removes_column_slope`.

### Projection

**G1. The MTRDR grid.**
- **Decision:** rolling equirectangular; φ_P = the equatorward 5°
  multiple of the median DDR latitude; R = the IAU 2000 ellipsoid radius at
  φ_P; x = R cos φ_P λ; y = R φ; central meridian 0; 18 m (36 m HRL); polar
  stereographic beyond ±65°, on the pole, spherical with R = Rp = 3376200 m
  and true scale at the pole (the catalog's fallback; no local polar MTRDR,
  so its names, `[90.00N; 0.00E]`, `Stereographic_North_Pole`, are built by
  analogy and checked only by pyproj round trips). Longitudes are unwrapped
  around the scene's median longitude (taken into (−180°, 180°]), so a scene
  on the antimeridian stays contiguous near x ≈ ±π R cos φ_P. Southern names
  (`[15.00S; 0.00E]`, `custom_sphere_lat-15`) are also by analogy.
- **Source:** `mtr_map.cat` Table 1.0 and its equations; M1 (the 15° and
  3394839.8133 m values reproduced). The WKT and `map info` strings match
  both local MTRDR headers exactly.
- **Confidence:** high (equirectangular north); medium (southern and polar
  names).
- **Test:** `src/varda/crism/_tests/test_projection.py::test_grid_matches_mtrdr_labels`
  (data-gated; FRT00005850, HRL000040FF), `::test_radius_table`,
  `::test_reference_latitude_and_wrap`, `::test_coordinates_match_pyproj`.

**G2. Tie point.**
- **Decision:** tie = (x_min, y_max) of the DDR pixel centers, the UL corner
  of their bounding box, rounded to 0.1 m: the extreme DDR centers lie on the
  grid's corner, not half a pixel inside it. Width = ⌊(x_max − tie_x)/s⌋ + 1,
  likewise the height. In label terms this is still tie = x(W) − s/2,
  y(N) + s/2, because the label's WESTERNMOST_LONGITUDE and
  MAXIMUM_LATITUDE are the first column and row *centers* (M1), half a pixel
  inside the extreme DDR centers. The origin is not snapped to a global
  lattice by default; `snapToLattice` floors x_min and ceils y_max to
  multiples of s.
- **Source:** M1; Task 17 measurement: FRT00005850's sensor-space DDR
  (`frt00005850_07_de167l_ddr1`, all 510 × 640 centers) gives x_min =
  4417887.627, y_max = 1091944.932 against the header's 4417887.6000,
  1091944.9000 (0.03 m), and 990 × 1043 pixels, the MTRDR size exactly. The
  MTRDR DE extremes give the same values (HRL000040FF: 4422632.234,
  1109935.515 against 4422632.2, 1109935.5). A "tie = x_min − s/2" rule
  would be off by s/2 (9 m). The header values are whole decimeters, so
  0.1 m rounding reproduces `map info` character for character.
- **Confidence:** high (two observations, 18 m and 36 m; 0.1 m rounding
  versus truncation cannot be told apart on them).
- **Test:** `src/varda/crism/_tests/test_projection.py::test_tie_point_rule`,
  `::test_grid_from_sensor_ddr_matches_mtrdr` (data-gated).

**G3. Resampling.**
- **Decision:** nearest neighbor (GLT-equivalent) by default, with
  source-sample and source-line backplanes; a KD-tree on the projected DDR
  centers, empty beyond **2·s** (`maxDistanceFactor = 2.0`, controller
  ruling in Task 17; the plan's 0.75·s·√2 leaves interior holes). Option:
  area weighting, a sparse (H·W) × (l·s) matrix from 4×4 bilinear
  subsamples of each source quad (corners at neighbor midpoints, edges
  extrapolated half a step); σ² = Σw²σ² / (Σw)² over valid sources; the
  look count is the effective number of looks (Σw)² / Σw².
- **Source:** DPSIS App. P1 §12–13 (GLT); M1 (DE lat/lon scatter of 7–9 m
  std, the nearest-neighbor signature). Task 17 measurement on
  FRT00005850, the GLT from its sensor-space DDR against the MTRDR IN
  backplanes "IR Sample"/"IR Line" (0-based; the IN and DE empty masks are
  identical):
  - The DDR centers are 23 m (across) × 26 m (along) apart, coarser than
    the 18 m grid. The MTRDR is not distance-gated: it leaves pixels empty
    whose nearest DDR center is 0.5 s away, and fills some at 1.8 s.
  - Interior holes and agreement with the MTRDR empty mask:
    0.75·√2 (19.1 m): 39,739 holes, 95.64%; 1.5: 1,681 holes, 99.39%;
    1.75: 16 holes, 99.46%; **2.0: 0 holes, 99.37%** (we fill 62.3% of the
    grid, the MTRDR 61.6%: 6,501 pixels only we fill, at the swath edges,
    none only it fills).
  - The same source pixel as the MTRDR on 96.96% of the pixels both fill
    (96.91% at 0.75·√2).
  - Full-FRT cost: 0.1 s for the GLT; 0.14 s and 21 MB (1.43 M non-zeros)
    for the area weights.
- **Confidence:** high.
- **Test:** `src/varda/crism/_tests/test_projection.py::test_backplanes_trace_to_sensor`
  (default and 0.75·√2), `::test_default_threshold_fills_coarse_geometry`,
  `::test_glt_matches_mtrdr_from_sensor_ddr` (data-gated: no interior holes,
  empty mask ≥ 99%, same source ≥ 95%), `::test_glt_skips_invalid_geometry`,
  `::test_area_weights_conserve_mean`,
  `::test_area_weights_memory_for_a_full_frt`.

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
