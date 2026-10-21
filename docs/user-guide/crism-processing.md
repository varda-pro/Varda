# CRISM processing

**Processing → CRISM → Process CRISM Observation…** turns a CRISM IR
observation's TRR3 I/F cube into a map-projected, MTRDR-consistent I/F with
a per-band uncertainty (σ) and a record of everything that was done. It
re-implements the corrections of the CAT toolchain in Varda, so you can
reprocess an observation with your own choices (a different atmospheric
scan, destriping on or off, a pixel size) and see exactly what each step did.

Phase 1 covers the **IR detector (S), from a TRR3 cube**. VNIR, the IR/VNIR
join, and the empirical photometric correction (EGN) are not done yet; see
[Limits](#limits).

Varda re-implements the published descriptions of the official processing
(the CRISM Data Product SIS, Murchie et al. 2024, App. P1; Seelos et al.
2024, §5) and of the volcano-scan correction (McGuire et al. 2009). It does
not contain or translate CAT's IDL code. Where Varda departs from CAT on
purpose (the transmission is fitted rather than scaled at two wavelengths,
and σ is carried throughout), [Methods](#methods) says so. How closely the
products agree with CAT's MTRDRs is measured in
[Agreement with CAT's MTRDRs](#agreement-with-cats-mtrdrs).

## What you need

For each observation, a folder with its **TRR3 IR I/F** (`*_if*l_trr3.img`
and `.hdr`/`.lbl`) and its **DDR** (`*_ddr1.img`, the geometry). Varda reads
the PDS labels, so the `.lbl` files must sit beside the images. A crop of an
observation's lines works too; a crop of its columns does not, because the
calibration files (CDRs) cover the whole detector width and must have the
cube's columns.

Processing also needs a few **calibration files**, which are not part of
TRR3: the wavelength table (WA), the bad-pixel maps (BP) and the atmospheric
transmission spectra. Varda looks for them, in this order, in:

1. the observation's folder and its `CRISM_CDR/<kind>/` subfolders (for example
   `CRISM_CDR/wa/`; on the command line, any
   folder you add with `--calibration`);
2. its download cache, `crism` in Varda's app-data folder, shared by the
   dialogs and the command line (`%APPDATA%\Varda\crism` on Windows,
   `~/Library/Application Support/Varda/crism` on macOS,
   `~/.local/share/Varda/crism` on Linux);
3. optionally, CAT's `aux_files` folder (**Get Calibration Files…** or
   `--cat-aux`), which adds the volcano-scan transmissions CAT uses. It is
   needed when the recipe's transmission source is CAT's volcano-scan ADRs.

A missing WA or atmospheric transmission is **fetched from the PDS** into the
cache. Without a network, Varda lists each missing file and where to get it,
**before** any processing starts. The bad-pixel maps are optional and used
only when they are already local: the calibration has already interpolated
the elements they flag, and one with other columns than the cube is skipped
with a note. CAT's files are never copied or redistributed by Varda.
**Processing → CRISM → Get CRISM Calibration Files…** shows what an
observation needs and fetches the rest; only the WA and one atmospheric
transmission are required.

## The dialog

1. **Add Folder…** adds an observation folder (repeat for more). Each row
   shows its status.
2. Choose the **recipe**: one group of settings per step (below). **Load
   Recipe…** and **Save Recipe…** keep a recipe as JSON, which is also what
   the command line takes.
3. Choose the **output folder**, then **Run**. Missing calibration files are
   checked and fetched first; an observation still missing a required file
   is marked failed and skipped, and the others run. **Cancel** stops the
   batch; a canceled or failed observation leaves the output folder as it
   was, and the others still run. A failure's details (with the traceback)
   are in Varda's log.

Each map-projected I/F opens in the project when its observation finishes.

## The command line

Everything the dialog does is available without the GUI:

```
python -m varda.crism process <folder or TRR3 file>... --out crism-products
Varda --crism process <folder>... --recipe my-recipe.json
```

(`Varda --crism` is the same command in the packaged app.) On Windows the
packaged app has no console of its own: `Varda --crism` prints into the
console that started it, but that console does not wait for it to finish
(and does not see its exit code), and when there is no console it writes to
`%LOCALAPPDATA%\Varda\Logs\Varda-crism.log`. For scripts on Windows, use
`python -m varda.crism` from an installed Varda. Other commands:

- `calibration <folder> [--fetch]` lists the calibration files an
  observation needs and where each was found; `--fetch` downloads the
  missing ones.
- `learn-artifacts <folders>... --out <folder>` learns an artifact basis (see
  [Defaults](#defaults)) from several observations and prints its
  leave-one-out check. Each basis is a file named by its transmission and
  the epoch it was learned over (for example
  `061C4_AT_1084995594-1090233682.npz`), so one folder can hold bases for
  several epochs. It warns when a transmission has only one observation.
- `validate <ours.img> <mtrdr.img>` compares a product with an official
  MTRDR and writes a report with figures.

`process` also takes `--sensor-space` (write the unprojected cube), `--cog`
(also write a Cloud Optimized GeoTIFF), `--artifacts <folder>` (a folder of
learned artifact bases, the recipe's **Artifact basis folder**; it also turns
the artifact model on), `--cache`,
`--cat-aux` and `--calibration`. Exit code 0 means everything succeeded, 1 that some
observations failed (the others still ran), 2 a usage error, or missing
calibration files when nothing could be processed.

## Methods

The steps run in this order, on the TRR3's native detector grid until the
smile step (x is the detector column, t the line, λ the wavelength, R the
I/F and σ its 1σ uncertainty):

| Step | History code | In short |
|---|---|---|
| [Read and mask](#read-and-mask) | | Invalid samples and slew lines out |
| [Instrument noise](#instrument-noise) | `NOISE` | σ for every sample |
| [Photometry](#photometry) | `PHT` | ÷ cos(incidence) |
| [Despike](#despike) | `DSK` | Isolated spikes replaced |
| [Atmosphere](#atmosphere) | `ATM` | Fitted CO₂/H₂O transmission divided out |
| [Destripe](#destripe) (off by default) | `RSC` | Column factors divided out |
| [Smile](#smile) | `SMI`, `ESC` | Resampled to the MTRDR wavelengths |
| [Bad bands](#bad-bands) | | `bbl` set |
| [Map projection](#map-projection) | `MAP` | Onto the MTRDR's own grid |

Every product's ENVI header and provenance carry the history string (for
example `PHT(areoid-poly)_DSK(5.0)_ATM(fit;061C4:AT;shift;elev;noresid)_ESC(linear)_MAP(eqc;18m;nn)`),
which names each step and its main choice.

### Read and mask

- **Inputs.** The TRR3 IR I/F and its DDR, read through their PDS labels,
  and the wavelength table (WA CDR) the label names.
- **Invalid samples** are 65535 (DPSIS App. L §2.2), values below 1e-6 or
  not finite. Detector elements without a WA wavelength are masked.
- **Bad-pixel elements are kept.** The calibration has already replaced
  elements flagged in the BP CDRs by the mean of their neighbors along the
  detector row (DPSIS App. L §2.6 and §2.15), and the MTRDRs use them.
  Varda counts them (provenance: `steps.mask.badPixelInterpolated`) and
  does not mask them; neighboring columns' σ are then correlated there,
  which σ does not carry.
- **Slew lines.** A line is a slew line when its along-track step (the
  great-circle distance between consecutive DDR rows, median over columns)
  is more than 4× the scene median. Only the longest run of other lines is
  processed; the rest hold no I/F and are left off the map. (The idea
  comes from the jezero-rim-mapping code.)

### Instrument noise

σ is the random instrument noise, estimated from the cube itself (CAT has
no noise model):

- On the central 64 usable lines, the brightness-normalized along-track
  second difference d = R(t) − L(t)·½[R(t−1)/L(t−1) + R(t+1)/L(t+1)], where
  L is the mean of the pixel's other bands within ±10 bands (never across
  the filter joins at 1656 and 2730 nm). Differences along track cancel
  detector stripes, and normalizing by L removes most of the scene's
  achromatic texture.
- Its robust scale, σ = 1.4826·MAD(d) / √(1.5(1 + 1/m)) (m the bands in L),
  over the 50% of pixels with the smallest local gradient, per band and per
  group of 16 columns.
- A signal-dependent model fitted to those estimates in 8 brightness bins:
  σ² = a(λ, column group) + b(λ)·R, a floor plus a Poisson-like term
  (Kreisch et al. 2017; shift differences after Green et al. 1988). b is
  pooled over ±20 bands. Each sample's σ is then √(a + b·R) at its own R.

The TRR3's IKF filter has already altered the noise statistics, which this
model does not account for; the checks on synthetic cubes test the estimator,
not the filter's effect (see [Uncertainty](#uncertainty)).

### Photometry

R ← R / cos i and σ ← σ / cos i. By default i is a least-squares fit
i = c₀ + c₁x′ + c₂x′² + c₃t′ + c₄t′² to the DDR's areoid incidence (band 1),
where x′ and t′ are the sample and line indices centered and scaled
(provenance `steps.photometry`: `coefficients`, `center` (t₀, x₀), `scale`
(line, sample) and `rmsDeg`),
as the official TER processing does (DPSIS App. P1 §1; Seelos et al. 2024
§5); it removes the stair-steps of the areoid's 16 pixels per degree. The
option uses
the DDR's MOLA surface incidence (band 6) as is. Incidences of 90° or more
are invalid. The geometry is treated as exact.

### Despike

Isolated spikes left after the TRR3 filter are replaced. Spikes replaced in
1850–2150 nm, where the filter can leave false 1.9 and 2.1 µm absorptions
(Leask et al. 2018), are counted separately; Leask's artifacts span several
bands, so the isolation test does not catch them, and the count is only a
pointer to where they may be:

- **Prediction R̂** of each sample: the inverse-variance mean of (a) the
  median of its 8 spatial neighbors, each moved to the pixel's wavelengths
  and scaled by the ratio of whole-spectrum medians, and (b) the median of
  the bands at ±1 and ±2 (the sample's own band excluded, inside its filter
  segment). Its σ_p is widened when the two predictions disagree by more
  than 3σ.
- **A sample is a spike** when all three hold: |R − R̂| > k·√(σ² + σ_p²)
  (k = **Threshold**, default 5); its residual is a strict local extremum in
  λ, with both neighbors' residuals below 0.4 of it (the extremum test of
  Parente 2008); and it is transient along track (it stands out from the same
  detector element 2–4 lines away).
- **Replaced** by R̂, with σ = σ_p. The IN file counts spikes per pixel, and
  separately those in 1850–2150 nm (the "Leask count").

**Limitation:** on synthetic cubes the recall at 8σ is 0.95–0.98 with
0–1e-6 false flags, but on spikes injected into a real TRR3 (FRS00031442)
it is 0.13 at 8σ and 0.57 at 20σ: real spectra make the two predictions
disagree, which raises σ_p. The step removes clear outliers; it is not
validated against real spikes, and the Leask count is a flag to inspect,
not a census.

### Atmosphere

The gas transmission is fitted per pixel and divided out. This is the main
departure from CAT, which scales a volcano-scan transmission by the ratio of
two wavelengths (McGuire et al. 2009, Eq. 4).

**The model.** For a pixel in column x, over the fit window W =
1850–2200 nm, in log space:

    ln R(λ) = Σₖ cₖ Pₖ(λ) + β · ln Tₓ(λ + δₓ) + ε

- Pₖ: Legendre polynomials of degree 0–3 over W (the continuum).
- Tₓ: a volcano-scan transmission, used per detector element (column x of
  the transmission for column x of the scene, as TER does: DPSIS App. P1
  §2). The candidates are the PDS atmospheric transmission (AT) CDRs, or
  with CAT's `aux_files` also CAT's volcano-scan ADRs (**Transmission
  source**).
- β: the path scale (the pixel's CO₂ column relative to the scan's).
- δₓ: a small wavelength shift per column; thermal drift moves the
  transmission by up to about 1 nm (McGuire et al. 2009, §2). ln T is
  interpolated linearly at λ + δ along the column's own wavelengths.
- The fit is weighted by R/σ (1/σ of ln R). The hydration band's core,
  1880–1945 nm, gets zero weight: hydrated minerals put a band there that
  the continuum cannot follow, and it would bias β.

**1. Choose the transmission.** On each column's spectrum (the mean of
ln R over the central 64 lines that are complete in W), every candidate
scan is fitted with (c, β, δ). δ is found by Gauss–Newton: each step fits
(c, β, η = βδ) with the first-order term η·∂ln T/∂λ and moves δ by η/β,
until every column's step is below 1e-4 nm (at most 10 steps; |δ| must stay
below 5 nm). A candidate's score is the median over columns of the
weighted residual RMS over **1947–2066 nm**, the TER window (DPSIS App. P1
§2). The lowest score wins (or name a scan in **Scan**). Choosing *after*
the shift is fitted matters: without it, a different scan wins on the rim
observations. The provenance records every candidate's score, the margin to
the runner-up, and the fraction of columns where the winner is better.

**2. Smooth the shift.** δₓ is smoothed across columns with a cubic
Legendre polynomial in x, fitted robustly (Tukey's biweight, c = 4.685;
Beaton & Tukey 1974), since the drift is a smooth instrumental function of
the column. The chosen transmission is refitted with this δ held fixed.

**3. Fit β per pixel.** With T and δ fixed the model is linear, and each
pixel's (c, β) is a weighted least-squares fit, with its covariance.

**4. Regularize β by elevation** (**Strength per pixel**, default). Varda's
own addition, from physics rather than a published method: the CO₂ column
falls with elevation, so ln β ≈ a − z/H. The pipeline:

- fits a and H robustly to the pixels' (DDR elevation z, ln β);
- accepts the model only if the slope is negative, at least 3σ from zero,
  and H lies within 5–20 km (Mars' scale height is about 11 km); otherwise
  β stays per pixel;
- rescales σ_β to its empirical size (from along-track second differences of
  ln β, because the formal σ assumes independent bands);
- shrinks each pixel's β toward the model in proportion to its uncertainty
  (an empirical-Bayes posterior). The prior's width τ is at least the
  spatially coherent part of the departures from the model, so real
  variation is not smoothed away.

A free β is biased by a hydrated pixel's 1.9 µm band; topography is not.
The provenance (`steps.atmosphere.elevationModel`) records H
(`scaleHeightKm`), its formal σ, `accepted`, `usedAsPrior` and `sigmaRatio`
(the empirical over the formal σ_β; about 0.17 on FRS00031442). The model is
fitted on the central 64 lines only, its formal σ_H is optimistic, and H
depends on the scan (9.0–16.5 km across the candidates on one scene), so
treat H as a sanity check, not a measurement.

**5. Correct.** R′ = R · exp(−β · ln Tₓ(λ + δₓ)) over all bands, and σ′ is
propagated to first order through the fit (σ of β and its covariance with
the data it corrects; see [Uncertainty](#uncertainty)).

**Optional: the artifact model** (**Model instrument artifacts**, off by
default). CAT adds a per-scan artifact spectrum to remove the 2 µm "bowl"
and high-frequency "hash" that one exponent of one empirical transmission
leaves at another path length (Morgan et al. 2011; Wiseman et al. 2016).
CAT's scaling of that patch is not published. Varda instead learns the
residual from several observations processed with the same scan
(`learn-artifacts`): each observation's post-fit column residual is
high-passed (its least-squares cubic over W removed) and scaled to unit
RMS over the score window; the median across observations is smoothed over
31 columns. In a scene it is applied as β·c·a(x, λ), with one scene-level
c (the median over columns of the fitted α/β), and **only over 1940–2100 nm,
the CO₂ lines**. It lowers the CO₂ residual on the rim FRSs by 28–48% but
moves BD1900_2 by up to +0.0039 (see
[Agreement with CAT's MTRDRs](#agreement-with-cats-mtrdrs)), which is why it
is off by default. A basis serves only its own transmission and
observations within 180 days of its training span; the bundled bases were
learned from FRS00031442, FRS0002FE75 and FRS00031260 (2014). The basis
file, c and its σ, and the window are in the provenance.

### Destripe

Off by default, which departs from TER (it applies RSC after the
atmosphere step). TRR3 I/F has already been destriped by the official filter
(DPSIS App. N), and the evidence is mixed: on FRS00031442 a cross-validated
test found no persistent column pattern left (destriping changed the
column-to-column scatter from 0.69% to 0.72%), while on FRT00005850 it
raised the correlation with the MTRDR for the four cross-track parameters
(BD2310 0.883 to 0.902) and lowered the CO₂ residual (0.00665 to 0.00653),
but left the smooth cross-track difference (see
[Agreement with CAT's MTRDRs](#agreement-with-cats-mtrdrs)). Try it when
column structure matters to your result. When on, after the atmosphere
step:

- **Bland pixels:** per column, the 50% with the least continuum-removed
  spectral variance over 1000–2600 nm and no band depth at 1.9, 2.3 or
  2.5 µm more than 3σ above the scene's.
- **Column factors:** ln f(x, λ) = the median over bland lines of ln R less
  a robust (L1) cross-track Legendre fit of degree 5 per line (the TER RSC
  model), less its smooth cross-track part (a 15-column running median
  then a Gaussian of σ = 5 columns), so that the frame holds only
  column-scale structure.
- **Protecting geology:** ln f is split into a part smooth in λ (a cubic
  per filter segment) and the rest. Each is removed only where it is
  significant (the smooth part per column at an RMS of ≥ 3σ, the rest per
  element at > 3σ) and, with **Require stripes in both halves**, the same in
  the first and second halves of the lines (the halves' difference at an RMS
  of ≤ 2σ, or ≤ 3σ per element).
- R′ = R/f and σ′ = σ/f. The frame's own uncertainty (a moving-block
  bootstrap, 50 resamples of 16-line blocks; Künsch 1989) is reported as a
  systematic term, not added to σ.

### Smile

- **Target wavelengths:** the 411-band IR vector shared by every MTRDR
  (detector rows 7–438 less 173–193), with its FWHM, from the MTRDR
  wavelength table. Products are band-for-band compatible with MTRDR IR.
- **Resampling:** each column is interpolated linearly from its own WA
  wavelengths to the target (DPSIS App. P1 §5), never across a filter join
  and never beyond the column's valid wavelengths. At a join, a target with
  only one source band on its side copies that band (up to about one band of
  constant extrapolation). σ propagates exactly, σ² = Σ w²σ². Interpolation
  correlates adjacent bands (ρ median 0.18, up to 0.93), which per-band σ
  does not carry.
- **Empirical shift correction** (**ESC**, on by default, as in TER): per
  band, the continuum-normalized column spectra of the bland pixels are
  binned by the column's wavelength offset from the target (0.125 nm bins;
  0.25 nm for HRL/HRS), a weighted line is fitted, and each column is
  divided by its value relative to zero offset (3 passes). R and σ are
  divided by the same factor.
- **Apply the fitted shift** (off): also adds the atmosphere step's δₓ to
  the WA wavelengths before resampling. δ is relative to the scan, so it is
  off by default.

### Bad bands

`bbl = 0` for bands above 2600 nm (no thermal correction in Phase 1) and for
any band with valid data in fewer than 50% of the footprint's pixels. Bad
bands are still written.

### Map projection

The grid is the MTRDR's (from CRISM's `mtr_map.cat` and the products'
labels):

- **Projection:** equirectangular, x = R·cos φ_P·λ, y = R·φ, central
  meridian 0. φ_P is the equatorward multiple of 5° of the scene's median
  latitude, and R the IAU 2000 Mars ellipsoid's radius at φ_P (Seidelmann
  et al. 2002; a = 3,396,190 m, b = 3,376,200 m). From 65° latitude
  poleward, polar stereographic (this case has not been checked against a
  real MTRDR).
- **Pixels:** 18 m (36 m for binned observations), or **Pixel size**. The
  upper-left corner is (min x, max y) over the DDR pixel centers (a corner
  of their bounding box), rounded to 0.1 m, as in the MTRDRs (not snapped to a global lattice unless
  **Snap to the MTRDR lattice** is on).
- **Resampling:** nearest neighbor by default, the equivalent of a GLT
  (DPSIS App. P1 §12–13): each output pixel takes the nearest DDR pixel
  center within **Empty threshold** × the pixel size, else it is empty. The
  IN file's source sample and line trace every pixel back to the TRR3. With
  **Area weighted**, each source pixel is a quadrilateral and the weights
  are its overlap fractions (sampled at 4 × 4 points), R̄ = Σ wR / Σ w and
  σ = √(Σ w²σ²) / Σ w. The DE backplanes and the source sample and line
  stay nearest neighbor even then.
- On FRT00005850 this reproduces the MTRDR's grid exactly and matches its
  empty-pixel mask on 99.4% of pixels.

### Settings that are not in the recipe

Fixed in this version, recorded in the provenance where they apply:

| Setting | Value |
|---|---|
| Noise: lines, column groups, quiet fraction, brightness bins | central 64 usable lines; 16 columns; 50%; 8 |
| Atmosphere fit window | 1850–2200 nm |
| Hydration band, zero weight | 1880–1945 nm |
| Transmission score window | 1947–2066 nm |
| Continuum | Legendre, degree 3 |
| Lines for choosing the transmission | central 64 usable lines |
| Shift: tolerance, steps, bound | 1e-4 nm; 10; ±5 nm |
| Shift smoothing | cubic Legendre in x, Tukey biweight c = 4.685 |
| Elevation model accepted | slope < 0 at ≥ 3σ, 5 ≤ H ≤ 20 km |
| Artifact basis: window, smoothing, epoch | 1940–2100 nm; 31 columns; ±180 days |
| Despike: neighbor isolation, predictor disagreement, transient test | neighbors' residuals < 0.4×; widened beyond 3σ; 0.85·k against lines t ± 2–4; ≥ 3 spatial neighbors |
| Slew lines | along-track step > 4× the median |
| Destripe: bland fraction, cross-track fit, smoothing, bootstrap | 50%; L1 Legendre degree 5; 15-column running median, then Gaussian σ = 5 columns; 50 × 16-line blocks |
| Destripe: significance, split-half | smooth part RMS ≥ 3σ, element > 3σ; halves' difference RMS ≤ 2σ, element ≤ 3σ |
| ESC bins | 0.125 nm (0.25 nm HRL/HRS), 3 passes |
| Bad bands | > 2600 nm or < 50% valid |
| Polar projection | from 65° latitude |

## Parameters

The recipe (the dialog's settings, or a `.recipe.json` for the command line)
and its defaults. Every product's `.recipe.json` holds the recipe it was made
with.

| Step | Parameter | Meaning | Units, range | Default |
|---|---|---|---|---|
| Photometry | Enabled | Divide by cos i | | on |
| | Incidence angle | Areoid incidence with a polynomial fit, or MOLA surface incidence | | areoid, polynomial |
| Despike | Enabled | Replace isolated spikes | | on |
| | Threshold | How far a sample must stand out (k) | σ, 3–10 | 5 |
| Atmosphere | Enabled | Fit and divide out the transmission | | on |
| | Transmission source | The PDS atmospheric transmission (AT) CDRs, CAT's volcano-scan ADRs, or both | | PDS AT CDRs |
| | Scan | Force one scan, by ID (`061C4`) or key (`061C4:AT`) | | empty: automatic |
| | Fit a spectral shift | Fit δₓ | | on |
| | Strength per pixel | β per pixel, or with the elevation prior | | with the prior |
| | Model instrument artifacts | Apply a learned artifact basis on 1940–2100 nm | | off |
| | Artifact basis folder | Learned bases, considered with the bundled ones (nearest epoch wins; this folder on ties) | | empty |
| Destripe | Enabled | Remove column striping | | off |
| | Require stripes in both halves | Split-half persistence test | | on |
| Smile | Enabled | Per-column resampling from WA (off: from each band's median wavelength) | | on |
| | Empirical shift correction | ESC | | on |
| | Apply the fitted shift | Add δₓ to WA first | | off |
| Projection | Enabled | Map-project (off: sensor space only) | | on |
| | Pixel size | Output pixel size; 0 is automatic | m, 0–200 | 0 (18 m, 36 m binned) |
| | Resampling | Nearest neighbor or area weighted | | nearest |
| | Snap to the MTRDR lattice | Origin at multiples of the pixel size | | off |
| | Empty threshold | Distance beyond which a pixel is empty | pixels, 0.5–4 | 2 |
| Output | Also write sensor space | The `_vter` cube | | off |
| | Cloud Optimized GeoTIFF | Also a COG of the I/F | | off |

## Outputs

Products go in the output folder, named like the MTRDR's (with `_vmtr`):

| File | Contents |
|---|---|
| `frs00031442_01_if168l_vmtr` | The map-projected I/F, 411 bands, ENVI float32 (unitless reflectance factor). Bands above 2600 nm are flagged bad (`bbl = 0`, see [Bad bands](#bad-bands)). |
| `…_vmtr_sigma` | σ for each band and pixel, in I/F (see [Uncertainty](#uncertainty)). |
| `…_de168l_vmtr` | The DDR's 13 backplanes and its spare band, map-projected: incidence and emission (areoid and MOLA), phase, latitude, longitude, slope and azimuth, elevation, thermal inertia, albedo, local solar time. |
| `…_in168l_vmtr` | Backplanes of the processing itself (table below). |
| `…_vter`, `…_vter_sigma` | Optional (sensor space): the same I/F and its σ before map projection; always written when projection is off. |
| `…_vmtr.tif` | Optional: a Cloud Optimized GeoTIFF of the map-projected I/F (needs projection). |
| `.recipe.json`, `.provenance.json`, `.diagnostics.npz` | Sidecars: the recipe, the provenance, and the diagnostics behind it. |
| `<image>.aux.xml` | Beside each image: Varda's provenance record (GDAL's sidecar, in the `VARDA` metadata domain), shown in Image Properties. Varda's CRISM tools follow it back to the TRR3 and DDR. Keep it with the image. |

The IN file's bands:

| Band | Meaning | Units |
|---|---|---|
| Source sample, Source line | The TRR3 pixel under each map pixel (nearest neighbor) | 0-based index |
| Beta | β, the transmission's path scale | none |
| Beta sigma (empirical) | β's σ, rescaled to its empirical size (with the elevation prior, the posterior's) | none |
| ATM residual RMS | The atmosphere fit's σ-weighted residual RMS | ln I/F (≈ fraction of I/F) |
| Artifact amplitude | α = c·β, 0 without the artifact model | ln I/F |
| Spike count, Leask count | Replaced samples; those in 1850–2150 nm | count |
| Effective looks | 1 (nearest neighbor), or (Σw)²/Σw² (area weighted) | count |

With area weighting, Beta through Leask count are area-weighted means, and
the count bands are named "Mean spike count" and "Mean Leask count".

Invalid samples are 65535 in every product; no NaN is ever written. A run
works in its own hidden `.work-…` folder in the output folder and moves its
files into place only when everything succeeded, so a failed run never
leaves a header pointing at a partial image, and rerunning into a folder
replaces the previous products only if the new run succeeds. Two runs can
share an output folder. If a previous product is open in another program
(on Windows it then cannot be replaced), the run stops with an error naming
the file and leaves the previous products as they were: close it and rerun.
A `.work-…` folder left by a run that was killed is removed by a later run
once it is a day old; you can delete one sooner when no run is going.

## Using the products in Varda

The dialog adds the map-projected I/F to the project; the other products
open with **File → Import Image(s)…**. Varda's CRISM tools treat them like an MTRDR:

- **Column lock** in the CRISM Ratio workspace and **Processing → CRISM →
  Ratio Cube** read the detector column from `…_in168l_vmtr` (its "Source
  sample" band: the TRR3 column under each map pixel, in binned columns for
  a binned observation), found beside the I/F. The **Shadow Mask** reads the
  solar incidence from `…_de168l_vmtr`. See
  [the geometry file](crism-ratio.md#the-geometry-file).
- The sensor-space `_vter` I/F finds the TRR3's DDR through its provenance
  (the DDR must still be beside the TRR3), and a whole observation's width is
  ratioed by its image columns.
- The map's coordinate system is the MTRDR's: a sphere of the IAU 2000
  radius at the reference latitude. Varda's coordinate-system check
  identifies it as Mars, and notes the distortion of an equirectangular grid
  away from its standard parallel (about 1.6% at 18° N on a 15° grid), as it
  does for an official MTRDR. Measure lengths and areas on the body (the
  geodesic columns) rather than in map units.

## Uncertainty

Varda keeps random and systematic uncertainty apart, and says what neither
covers.

### σ: the instrument noise

The `_sigma` file holds σ for every band and pixel, in I/F. It starts from
the [noise model](#instrument-noise) and is propagated through every step:

- **Exactly** through photometry (σ/cos i), destriping (σ/f), linear
  interpolation (√Σw²σ²), ESC (σ/c), nearest neighbor (a copy) and area
  weighting (√Σw²σ²/Σw).
- **As an estimate** at replaced spikes: σ = σ_p, the prediction's robust
  error (χ² 0.92–0.96 on synthetic spikes).
- **To first order** through the atmosphere correction:
  σ²_lnR′ = σ²_lnR + t²σ²_β − 2σ²_lnR Σ t_q G_q, where t is the correction
  path, σ²_β the variance of β (with the elevation prior, the posterior's),
  and G the fit's gain (the last term over the fitted bands, where the data
  also set β). With the artifact model α = c·β is not a separate term, and
  c's uncertainty is not propagated (it is the `artifactAmplitude`
  systematic term). No Monte Carlo check is run.
- **Per band only.** Correlations between bands (from interpolation: median
  ρ 0.18) and between pixels (from area weighting) are dropped.

On synthetic cubes with known noise, χ² of the corrected I/F of the bland
basalt unit about each band's mean is 0.98–1.01 in every band group. This
checks the estimator and the propagation; the synthetic cubes are not
IKF-filtered, so the filter's effect on the noise statistics is not
tested. Directly against the true
spectra it is 1.04–1.08 on basalt in 1900–2100 nm, but **1.50–1.72 on
hydrated units**: the atmosphere fit leaves a small bias (below) that σ does
not hold. The header keys `varda sigma file`, `varda sigma kind`
(`instrument-noise`) and `varda sigma steps` (the steps σ went through) say
what a σ file is.

### Systematic terms

Per band, in the provenance (`systematic` in `.provenance.json`, also in
each image's `.aux.xml`), as fractions of I/F (multiply by I/F for
absolute values). Each is the median over detector columns, resampled to the
output wavelengths; null means not defined. Varda does not combine them;
add them in quadrature with σ if you treat them as independent.

| Key | What it is | When it is 0 |
|---|---|---|
| `scanChoice` | \|β ln T(λ+δ) of the chosen scan − that of the runner-up\|, each fitted to the column spectra: how much the correction depends on the scan choice | one candidate |
| `artifactAmplitude` | \|σ_c · β · a(x, λ)\|: the artifact model's scale uncertainty. σ_c is a noise-only lower bound; c differs between scenes about 100 times more | artifact model off (default); outside 1940–2100 nm |
| `atmosphereBetaExcess` | \|ln T\| × the excess of the empirical σ_β over the formal one | when the empirical σ_β is not larger (as on FRS00031442) |
| `destripeFrame` | σ_f/f, the destripe frame's bootstrap uncertainty | destripe off (default) |

`scanChoice` and `atmosphereBetaExcess` scale with |ln T|, so they are near
zero outside the gas bands.

### What neither covers

- **The atmosphere fit's bias.** On synthetic library surfaces the fitted
  shift is +0.17 nm off and β about 1% low, which offsets single CO₂ bands
  by up to 0.8σ. No term holds it.
- **The uncertainty of δ, of the ESC frame and of the artifact scale c**
  (beyond `artifactAmplitude`), of the wavelength calibration, of the
  photometric model (no EGN, below), and of geolocation.
- **The differences from CAT** measured below: they are the best estimate
  of how far a parameter computed from these products may sit from the same
  parameter computed from an MTRDR.

### Provenance

`.provenance.json` records the Varda version and git commit, the Python,
numpy, scipy and rasterio versions, the TRR3 and DDR with the SHA-256 of
each label and image, every calibration file found (path, SHA-256, used or
not, and why), the recipe, the history string, and what each step did: the
mask counts, the noise model, the photometric fit, the spike counts, every
transmission's score and the choice's margin, the shift and β statistics,
the elevation model (a, H, their σ, accepted or not), the artifact basis
used (file, c, σ_c, window), the destripe frame, the ESC factors, the bad
bands, the grid and the wall time of each pass. `.diagnostics.npz` holds the
arrays behind them.

## Agreement with CAT's MTRDRs

Phase 1 was validated against the official products CAT made
(phase1-validation report, below). The numbers a paper using these products
should quote:

**FRT00005850 against its own MTRDR** (603,755 pixels valid in both;
default recipe):

| Measure | Result |
|---|---|
| Grid | identical (reference latitude, radius, pixel size, tie point) |
| Geolocation | −0.14, −0.08 px (median offset) |
| CO₂ residual (RMS over 1940–2100 nm, per pixel, median) | 0.0067, against the MTRDR's 0.0082 |
| Brightness, ours ÷ MTRDR at 1.3–1.7 µm | median 1.072 (5th–95th percentile 1.003–1.150), following the emission angle along track (r = 0.94): no EGN |
| Spectral shape, ours ÷ MTRDR | within 0.76% over 1.05–1.85 µm; a 2 µm "bowl" down to 0.966 at 2080 nm (2.0% at 1.85–1.94 µm, 3.4% at 1.94–2.10 µm, 2.4% at 2.10–2.20 µm); 1.0–1.35% beyond 2.2 µm, following the phase angle |

Summary parameters (Viviano-Beck et al. 2014 definitions; BD2310 as in the
Jezero fusion prototype), ours against the MTRDR's, and the crater-floor
MTRDRs' own between-observation repeatability for scale:

| Parameter | Pearson r | Median bias | RMS ÷ repeatability | Repeatability |
|---|---|---|---|---|
| D2300 | 0.785 | −0.0019 | 1.94 | 0.00152 |
| BD2290 | 0.774 | +0.0002 | 1.07 | 0.00103 |
| BD1900_2 | 0.627 | −0.0004 | 1.47 | 0.00189 |
| OLINDEX3 | 0.895 | −0.0038 | 1.36 | 0.00595 |
| BD2310 | 0.883 | −0.0002 | 0.78 | 0.00139 |

What the differences are:

- **Not noise.** Both products come from the same TRR3 pixels and share
  their pixel-scale fluctuations; averaging 8 × 8 blocks raises r by at most
  0.05.
- **OLINDEX3: EGN.** 92% of the difference is along track and follows the
  phase angle (r = 0.95); removed, r = 0.999.
- **D2300, BD2290, BD1900_2, BD2310: a cross-track difference.** 52–91% of
  the difference is per detector column: a smooth trend across the columns
  that the MTRDR does not have (84% of the column part for D2300, 66% for
  BD1900_2, about half for BD2290 and BD2310), plus column-to-column scatter (D2300 falls by up to about 0.010 at
  the detector's edges). It is not attributed yet; the suspects are a
  residual of the smile correction (D2300, BD2290) and the per-column
  atmosphere fit (BD2310). Removing the per-column part gives r = 0.87–0.99.
  Treat parameter differences across the swath with this in mind.
- **The 2 µm bowl** correlates −0.81 to −0.87 with CAT's artifact spectra:
  it is CAT's artifact patch, which the MTRDR has and these products do not.
  The CO₂ residual divides by a quadratic over 1940–2100 nm and does not see
  it.

**The rim FRSs** (FRS00031442, FRS0002FE75, FRS00031260, default recipe):
the CO₂ residual is 0.0073–0.0077 per pixel (bound 0.0083, the median of the
six local MTRDRs), and 0.0040–0.0053 with the artifact model. Without the
artifact model (the default), FRS00031442's BD2310 sits 0.0057 and 0.0053
above two overlapping crater-floor MTRDRs (FRT00005C5E, FRT000047A3) and
agrees with a third (HRL000040FF, 0.0006);
MTRDR pairs themselves differ by up to 0.0045. The cause is not attributed;
treat it as a per-observation systematic offset.

**The artifact model's effect** (with − without, medians over the
footprint): D2300 and BD2290 within 0.00003, BD1900_2 +0.0014, +0.0016 and
+0.0039 on the three FRSs. The overlapping MTRDRs cannot say which BD1900_2
is right. If you turn it on, report the shift.

## Reporting your results

See [Reporting results from Varda](reporting-results.md) for what applies to
every process (citing Varda and its version, what is recorded).

### What to state

- The observations (product IDs) and the Varda version (both in the
  provenance).
- That the products are Varda's re-processing of TRR3 I/F (not MTRDRs),
  with no EGN, IR only.
- The recipe, or that it is the default; give any change, in particular the
  transmission source, a forced scan, the artifact model (with its basis
  file) and destriping.
- From the provenance: the transmission chosen and its score margin,
  whether the elevation prior was used (H is a sanity check, not a
  measurement: see [Atmosphere](#atmosphere)), and the spike count.
- The uncertainty you used: σ (instrument noise), any systematic terms, and
  how you combined them.
- If you compare with MTRDR-based results: the differences in
  [Agreement with CAT's MTRDRs](#agreement-with-cats-mtrdrs).

### A methods paragraph to adapt

> CRISM TRR3 IR I/F observations (*product IDs*) were processed with Varda
> *version* (*citation*), which re-implements the official TER/MTRDR
> corrections (Murchie et al. 2024; Seelos et al. 2024) with the default
> recipe: I/F was divided by the cosine of the incidence angle from a
> polynomial fit to the areoid incidence; spikes above 5σ were replaced;
> the atmospheric transmission was fitted per pixel in log space over
> 1850–2200 nm with a cubic continuum, a per-column wavelength shift and an
> elevation prior on the path scale, choosing the volcano scan (*scan*)
> with the lowest residual over 1947–2066 nm after the shift fit (after
> McGuire et al. 2009); spectra were resampled to the MTRDR wavelengths with
> the empirical shift correction and map-projected onto the MTRDR grid
> (18 m, nearest neighbor). No empirical geometric normalization (EGN) was
> applied. Per-pixel uncertainties are instrument noise propagated through
> each step. Against the official MTRDR of FRT00005850, these products
> reproduce the grid and geolocation (within 0.15 px) with a lower CO₂
> residual (0.0067 against 0.0082), and summary parameters agree with
> Pearson r = 0.63–0.89 and median biases within the MTRDRs' repeatability
> except D2300 (−0.0019).

### What to cite

McGuire et al. (2009) for the volcano-scan model, Murchie et al. (2024) and
Seelos et al. (2024) for the processing being re-implemented, Murchie et al.
(2007) for the instrument, and Viviano-Beck et al. (2014) if you compute
summary parameters. Add Kreisch et al. (2017) for the noise model, Parente
(2008) for the despike test, and Morgan et al. (2011) and Wiseman et al.
(2016) if you use the artifact model.

## Defaults

- **Destripe is off** for TRR3 input, a departure from TER: TRR3 is
  already filtered, and the evidence for destriping it again is mixed (see
  [Destripe](#destripe)). Turn it on in the recipe to compare.
- **ESC is on**, as in TER.
- **The artifact model is off.** It lowers the CO₂ residual by 28–48% on
  2014 observations, but it moves BD1900_2 by up to +0.0039 (about twice its
  repeatability; beyond the 0.0016 bound on two of three FRSs; see above). Turn it on with **Model instrument artifacts**
  or `--artifacts`. Varda ships bases learned from three 2014 observations
  (FRS00031442, FRS0002FE75, FRS00031260), valid within about 180 days of
  that span; outside it the step is skipped and the reason recorded, and a
  basis is never applied to an epoch it was not learned for.
  `learn-artifacts` learns one for another epoch; put it in a folder and set
  **Artifact basis folder** (or `--artifacts`). Of the bases of a
  transmission in that folder and the bundled ones, those with the cube's
  columns and bands (not, for example, a binned observation's) and within
  180 days are candidates; the one whose epoch holds the observation, else
  the nearest, is used (this folder's on a tie), and the provenance names the
  file. **Note** that the bundled bases
  were learned from the same three FRSs: their leave-one-out check (each FRS
  corrected with a basis learned from the other two) still lowers the CO₂
  residual by 33–64%, so the basis is not fitted to one scene, but quote the
  leave-one-out numbers, not the in-sample ones, if you use it on those
  observations.
- **Atmospheric scan** is chosen automatically by how well it flattens the
  scene's CO₂ band (1947–2066 nm); name one in **Scan** to force it.

## Limits

- **No EGN.** The empirical photometric correction CAT applies is not
  reproduced, so the I/F is brighter than the MTRDR's (median 1.072 on
  FRT00005850, following the emission angle) and its spectral slope beyond
  2.2 µm differs by about 1%, following the phase angle.
- **IR only, no VNIR and no join.** The S detector, from TRR3.
- **Not bit-for-bit CAT.** The 2 µm region differs by up to 3.4% (CAT's
  artifact patch), and summary parameters carry a cross-track difference
  from the MTRDR (above).
- **Validation is on one FRT and three FRSs** near Jezero crater (2007 and
  2014). Other epochs, binned (HRL/HRS) and polar observations run but have
  not been validated against MTRDRs.
- **The despike** is not validated on real spikes (recall 0.13 at 8σ on
  injected ones).

The full validation, with every number, the attribution of each difference
and the decisions taken: the
[Phase 1 validation report](../superpowers/specs/crism/phase1-validation.md).
Design: [the design](../superpowers/specs/2026-10-16-crism-processing-design.md)
(the design, not a description of the code; this guide's Methods is) and
its [evidence ledger](../superpowers/specs/crism/evidence-ledger.md), which
gives the reason and measurement behind each choice.

## References

- Beaton, A. E., & Tukey, J. W. (1974). The fitting of power series,
  meaning polynomials, illustrated on band-spectroscopic data.
  *Technometrics, 16*(2), 147–185. https://doi.org/10.1080/00401706.1974.10489171
- CRISM Science Team (2018). *CRISM Analysis Toolkit (CAT)*, Version 7.4
  [Software]. PDS Geosciences Node.
  https://pds-geosciences.wustl.edu/missions/mro/crism.htm
- Green, A. A., Berman, M., Switzer, P., & Craig, M. D. (1988). A
  transformation for ordering multispectral data in terms of image quality
  with implications for noise removal. *IEEE Transactions on Geoscience and
  Remote Sensing, 26*(1), 65–74. https://doi.org/10.1109/36.3001
- Kreisch, C. D., O'Sullivan, J. A., Arvidson, R. E., Politte, D. V., He,
  L., Stein, N. T., et al. (2017). Regularization of Mars Reconnaissance
  Orbiter CRISM along-track oversampled hyperspectral imaging observations
  of Mars. *Icarus, 282*, 136–151. https://doi.org/10.1016/j.icarus.2016.09.033
- Künsch, H. R. (1989). The jackknife and the bootstrap for general
  stationary observations. *The Annals of Statistics, 17*(3), 1217–1241.
  https://doi.org/10.1214/aos/1176347265
- Leask, E. K., Ehlmann, B. L., Dundar, M. M., Murchie, S. L., & Seelos,
  F. P. (2018). Challenges in the search for perchlorate and other hydrated
  minerals with 2.1-μm absorptions on Mars. *Geophysical Research Letters,
  45*(22), 12,180–12,189. https://doi.org/10.1029/2018GL080077
- McGuire, P. C., Bishop, J. L., Brown, A. J., Fraeman, A. A., Marzo,
  G. A., Morgan, M. F., et al. (2009). An improvement to the volcano-scan
  algorithm for atmospheric correction of CRISM and OMEGA spectral data.
  *Planetary and Space Science, 57*(7), 809–815.
  https://doi.org/10.1016/j.pss.2009.03.007
- Morgan, F., Mustard, J. F., Wiseman, S. M., Seelos, F. P., Murchie, S. L.,
  McGuire, P. C., & the CRISM Team (2011). Improved algorithm for CRISM
  volcano scan atmospheric correction. *42nd Lunar and Planetary Science
  Conference*, Abstract #2453.
- Murchie, S., Arvidson, R., Bedini, P., Beisser, K., Bibring, J.-P.,
  Bishop, J., et al. (2007). Compact Reconnaissance Imaging Spectrometer for
  Mars (CRISM) on Mars Reconnaissance Orbiter (MRO). *Journal of
  Geophysical Research, 112*, E05S03. https://doi.org/10.1029/2006JE002682
- Murchie, S., Stein, T., & Ward, J. (2024). *Mars Reconnaissance Orbiter
  CRISM Data Product Software Interface Specification*, Version 1.3.7.7.
  PDS Geosciences Node.
  https://pds-geosciences.wustl.edu/mro/mro-m-crism-3-rdr-targeted-v1/mrocr_2104/document/crism_dpsis.pdf
- Parente, M. (2008). A new approach to denoising CRISM images. *39th Lunar
  and Planetary Science Conference*, Abstract #2528.
- Seelos, F. P., Seelos, K. D., Murchie, S. L., Novak, M. A. M., Hash,
  C. D., Morgan, M. F., et al. (2024). The CRISM investigation in Mars orbit:
  Overview, history, and delivered data products. *Icarus, 419*, 115612.
  https://doi.org/10.1016/j.icarus.2023.115612
- Seidelmann, P. K., Abalakin, V. K., Bursa, M., Davies, M. E., de Bergh,
  C., Lieske, J. H., et al. (2002). Report of the IAU/IAG Working Group on
  cartographic coordinates and rotational elements of the planets and
  satellites: 2000. *Celestial Mechanics and Dynamical Astronomy, 82*(1),
  83–111. https://doi.org/10.1023/A:1013939327465
- Viviano-Beck, C. E., Seelos, F. P., Murchie, S. L., Kahn, E. G., Seelos,
  K. D., Taylor, H. W., et al. (2014). Revised CRISM spectral parameters and
  summary products based on the currently detected mineral diversity on
  Mars. *Journal of Geophysical Research: Planets, 119*(6), 1403–1431.
  https://doi.org/10.1002/2014JE004627
- Wiseman, S. M., Arvidson, R. E., Wolff, M. J., Smith, M. D., Seelos,
  F. P., Morgan, F., et al. (2016). Characterization of artifacts introduced
  by the empirical volcano-scan atmospheric correction commonly applied to
  CRISM and OMEGA near-infrared spectra. *Icarus, 269*, 111–121.
  https://doi.org/10.1016/j.icarus.2014.10.012
