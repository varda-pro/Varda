# CRISM ratio cubes

CRISM spectra carry artifacts that are fixed for each sensor detector column:
striping, a column-dependent gain, and residual calibration structure. Dividing
a spectrum by one from the same detector column cancels them. This page
describes the **Ratio Cube** analysis, which does that for a whole cube, and
the **CRISM Ratio workspace**, which does it for one pair of regions at a time
so you can judge a spot quickly.

## Ratio cube

**Processing → CRISM → Ratio Cube (Column Denominators)…** divides every pixel
of a CRISM I/F cube by a **denominator spectrum for its detector column**. The
result has the same bands, wavelengths and grid as the input, so it can stand
in for the I/F cube in other software.

### What it needs

The detector column of each pixel comes from one of two places, recorded as
`geometry` in the output:

- **A backplane**: a CRISM geometry file whose "IR Sample" band is the
  detector column (see [The geometry file](#the-geometry-file) for how it is
  found, or chosen by hand). This works for map-projected cubes. The names
  Varda looks for (as on the PDS Geosciences Node; upper-case names, as ODE
  downloads them, work too):

  | Cube | Companion with detector columns | Other companion (DDR) |
  | --- | --- | --- |
  | MTRDR `frt000107a7_07_if166j_mtr3.img` (also `_sr`, `_su`) | `frt000107a7_07_in166j_mtr3.img` ("IR Sample", "IR Line") | `frt000107a7_07_de166l_mtr1.img` |
  | TER3 `frt000107a7_07_if166j_ter3.img` | `frt000107a7_07_in166j_ter3.img` | `frt000107a7_07_de166l_ddr1.img` |
  | TRR3 `frt000107a7_07_if166l_trr3.img` (or `…s_trr3`) | none: the cube is in sensor space | `frt000107a7_07_de166l_ddr1.img` (no sample band) |
  | MRDR tile `…_mrral_…` / `…_vrral_…` | `…_mrrde_…` / `…_vrrde_…` | |

  The first companion that has an IR Sample band is used. Where it has
  "Target ID" and "Segment ID" bands (MRDR tiles), columns of different
  source strips are kept apart. A TRR3's DDR is in the DDR volume, not next
  to the cube; it gives the incidence angle but no detector column.
- **The image columns of an unprojected cube.** A cube with no backplane
  that is confidently unprojected (no map projection, and 640, 320, 128 or 64
  columns, a CRISM detector at 1×, 2×, 5× or 10× binning) is in sensor space, so
  image column *c* is binned detector column *c*. This is how a TRR3 (or
  TER3 without its IN file) is ratioed. The output records
  `geometry: image columns (unprojected)` and the `binning` (detector
  columns per image column).

A column is the pair (strip, column). Backplane columns are `rint` of the
sample value, so a value halfway between two columns rounds to the even one.
Without either (for example a map-projected cube without its IN file), or
with a backplane that doesn't cover the image's pixels, the dialog says so
instead of offering Run, and asks for the IN file in **Geometry File**.

### The geometry file

CRISM ships its per-pixel geometry in companion files beside the I/F cube.
Which one a feature needs:

| Companion | Holds | Used by |
| --- | --- | --- |
| **IN** (`…_in…`, e.g. `frt000107a7_07_in166j_mtr3.img`) | "IR Sample" and "IR Line" (the detector column and line of each map pixel) | column lock, the Ratio Cube |
| **DDR/DE** (`…_de…`, e.g. `frt000107a7_07_de166l_mtr1.img`, `…_de166l_ddr1.img`) | "INA at areoid" (solar incidence) and the other angles; **no detector sample** | the Shadow Mask's sun elevation |

**Automatic lookup.** For each need Varda tries, in order, and uses the first
file that has the needed band and covers the image's pixels:

1. **override**: the file chosen by hand for this image and need (below);
2. **beside the file**: the companions named like the image's own file, in its
   folder (the table above);
3. **from source *path***: the companions beside a file the image was made
   from, following its Varda provenance (kept in memory, and in the `VARDA`
   metadata of files Varda writes) back up to 8 steps, e.g. an IOVF result
   → its I/F cube, or a crop → an IOVF result → its I/F cube. A file chosen
   by hand for a source image is used too (its record names it). Crops
   (the Crop tool, Subset) shift the window read from the source's backplane;
   the chain stops at a step that moves pixels (Resample, Rotate/Flip, Crop to
   ROI). Only each step's primary input (the image it ran on) is followed, not
   a second image such as Band Math's. A plugin process is assumed to keep the
   pixel grid: check the recorded route if one warps or flips;
4. for column lock and the Ratio Cube only, **image columns**: the cube's own
   columns, for an unprojected CRISM cube (above).

This is why an IOVF-filtered cube, whose name (`… Iterative Outlier Voting
Filter (IOVF).img`) no longer matches the PDS pattern, still finds the IN file
beside the I/F cube it was filtered from.

**Choosing a file by hand.** When the lookup fails (the source was moved, the
result was renamed, the companion is in another folder), point Varda to the
file: **Choose Geometry File…** beside the column-lock control in the CRISM
Ratio workspace and in the ROI Manager (General Image Analysis, Dual), or
**Geometry File** in the Ratio Cube dialog (empty means automatic). The file
dialog opens in the image's folder (or its source's). Choosing an ENVI `.hdr`
uses its `.img`. The file is checked before it is used, and refused with the
reason when:

- it doesn't open as an image;
- it lacks the needed band: column lock and the Ratio Cube need the IR
  detector sample, so a DDR/DE file is refused with "looks like a DDR/DE file
  … Choose the product's IN file"; the Shadow Mask needs "INA at areoid";
- it doesn't cover the image's pixels: it must have the image's rows ×
  columns, or be on the same map grid (same projection and pixel size, a
  whole-pixel offset, no rotation) and cover the image, in which case the
  image's window is read. Varda never resamples a backplane: detector
  columns can't be interpolated meaningfully.

An accepted file becomes the image's **override** for that need, one key
each in its metadata: `crismColumnsFile` (the IN file, from Choose Geometry
File… or the Ratio Cube's Geometry File) and `crismIncidenceFile` (the
DDR/DE file, from the Shadow Mask's CRISM Geometry File). Every feature uses
it; column lock becomes available in every workspace showing the image (and
turns on in the CRISM Ratio workspace); both are saved and restored with the
session (by path; a packaged project doesn't copy the geometry files, so
keep them beside the data), kept in files Varda writes, and named in the
records of images made from it. Clearing the field goes back to automatic.
Choose Geometry File… asks for the IN file only. In the ROI Manager it is
offered only for an image that looks like CRISM (a CRISM product name on it
or a source, CRISM metadata, or an unprojected 640/320/128/64-column cube
with a CRISM-like spectral axis), or one whose file was chosen by hand.

**What's recorded.** The Ratio Cube's `ratioCube` record has `geometryFile`
(the file read, "" for image columns) and `geometryRoute` (`override`,
`beside the file`, `from source <path>` or `image columns`); its description
says "detector columns from backplane band 'IR Sample' of <file> (found:
<route>)", and every row of the column table has `geometryFile` and
`geometryRoute`. The workspace's column-lock tooltip names the file and
route. The Shadow Mask's sun elevation source names the DDR and its route.

### What it does

1. **Denominator method** (default Bland pixels):
   - **Whole column**: the mean spectrum of all pixels of the column that have
     a value in every analysis band.
   - **Bland pixels**: the mean spectrum of the column's pixels whose spectrum
     is bland (featureless), so real mineral absorptions in the scene do not
     leak into the denominator and appear, inverted, in every other row of the
     column.
2. **Blandness is judged on a run.** Each pixel's spectrum is first averaged
   with its neighbors **in the same column** (the *run length*, odd, 3–51,
   default 9; centered on the pixel and shifted inward at the ends of a
   column), and the run mean is tested. The run is never taken across columns.
   This reduces the noise the test sees; the denominator itself averages the
   pixels' own spectra. A run mean averages, band by band, only the values
   present.
3. **The blandness check** (Generic or a target preset, see
   [Blandness checks](#blandness-checks)) is applied to the run mean; a run
   mean with a missing value in a band of one of the check's windows fails.
   A pixel is used only if its run mean passes **and** the pixel has
   a value in every analysis band. The image's bad bands are left out of the
   test and the denominators. **Missing values are never filled:** no-data
   (the image's data ignore value, such as CRISM's 65535) and NaN stay
   missing throughout; nothing is interpolated along the spectrum.
4. **Fallbacks.** A column with fewer than **Minimum Pixels** (1–10000,
   default 20) used pixels:
   1. borrows used pixels from columns ±1 to ±**Neighbor Radius** (0–10,
      default 2; 0 never borrows) of the same strip, nearest first, until it
      has enough (recorded as *neighbors*, with `borrowed` listing each
      neighbor column and how many of its pixels were taken);
   2. if still short, uses every pixel of its own column that has data
      (*whole column*), each band averaged over the pixels with a value in
      that band (`validCount` records how many);
   3. if the column has no pixel with data at all, it is flagged *no data* and
      its ratio pixels are NaN. A denominator is never silently NaN.
5. Every pixel is divided, band by band, by its column's denominator. The
   result is NaN where the pixel has no column, where the pixel has no value
   in that band (the input's no-data is kept), or where the denominator is 0
   or NaN in that band. Bad bands stay NaN.

### Blandness checks

A denominator should be **featureless where it matters**: any absorption in
it appears, inverted, at the same wavelength in every ratio made with it,
where it can pass for (or hide) a feature of the numerator. The Ratio Cube
and the workspace's readout use the same checks.

#### Method

A **check** is a set of named **wavelength windows** (table below). In each
window, with the bands in an excluded range left out:

1. The spectrum is smoothed with a running mean over **5 neighboring bands**
   (about 33 nm at CRISM's 6.55 nm spacing, fewer at the ends of a stretch
   of bands), so a single noisy band is not taken for an absorption. The
   mineral bands looked for are 50–200 nm wide, so a band of Gaussian width
   30 nm loses about 5 % of its depth to this smoothing.
2. The smoothed spectrum is divided by its **upper convex hull over the
   window** (continuum removal, Clark & Roush 1984; the same hull as
   [Continuum Removal](spectral-analysis.md)), fitted across any excluded
   gap inside the window.
3. The **deepest dip** is 1 minus the smallest continuum-removed value (a
   fraction of the continuum: 0.03 is a 3 % dip), and **where** it is (nm).
4. The **noise** is the spectrum's own per-band noise in the window: the
   median absolute deviation of the second differences between neighboring
   bands (spectrum divided by its mean, within each unbroken stretch of
   bands), × 1.4826 / √6, the standard deviation of independent per-band
   noise. Second differences ignore slopes and the smooth shape of real
   bands, and the median ignores a few spikes.
5. The **relative slope** is the least-squares slope of the (unsmoothed)
   spectrum against wavelength in µm, divided by its mean (per µm).

A window **passes** when its deepest dip ≤ **max(Depth Floor, k × noise)**
and |slope| ≤ **Maximum Slope**. A spectrum is **bland** for a check when
every window passes. So a dip fails only if it is deeper than both an
absolute floor and k times what the spectrum's own noise could make: a
noisier spectrum (a single pixel, a dark area, a short run) is not failed for
its noise alone, while a quiet one is held to the floor. The other side of
this: the limit has no upper cap, so a very noisy spectrum (a dark or
shadowed area) can pass with a real band hidden in its noise. A pass means
"no dip stands out from this spectrum's noise", not "no band". The readout
shows each window's noise and limit; treat a pass with a limit well above the
floor as weak, and prefer a quieter denominator.

**Why the upper hull, not a line between the window's edges.** The hull does
not depend on which bands are taken as shoulders, is never above the
spectrum (so dips are never negative), and still anchors on both sides of a
gap such as the CO₂ range. Its cost is that it rides on positive noise
excursions, which makes a noisy spectrum's dips slightly deeper; the k ×
noise term absorbs that (on synthetic white noise, k = 3 passes 95–98 % of
featureless spectra at any noise level). Windows reach past their bands'
shoulders on both sides, because a window that stopped inside a band's wing
would anchor the hull there and see only part of the band.

#### Parameters

| Parameter | Meaning | Units | Range | Default (box readout / ratio cube) |
| --- | --- | --- | --- | --- |
| Check (Blandness) | Which windows must be featureless | — | Generic or a target preset | Generic |
| Depth Floor | A dip shallower than this never fails | fraction of the continuum (readout shows %) | 0–1 | 0.03 / 0.03 |
| Noise Multiple (k) | A dip within k × the per-band noise never fails | — | 0–100 | 3 / 3 |
| Maximum Slope | Largest \|relative slope\| in a window | per µm | 0–10 | 0.75 / 0.75 |
| Excluded Ranges | Wavelengths no window looks at | nm | any `low–high` list | `1000–1050, 1950–2100, 2650–4000` |

Fixed, not parameters: the 5-band smoothing, the hull continuum, the
noise estimator, and at least **5 measured bands per window** (fewer: the
window is not measured, fails, and the readout says so; the Ratio Cube
refuses to run). The Ratio Cube's settings are dialog fields; in the
readout, Floor, k and Slope are spin boxes and the CO₂ range a check box
(the other exclusions are fixed there).

The readout and the Ratio Cube have **separate defaults** so either can be
tuned alone, but the calibration below gave no reason for different values:
the noise term already follows each spectrum's own noise; on the calibration
scene, 9-pixel run means and 5 × 5 box means had the same band-to-band noise
(median 0.3 %) and passed at the same rates; and a stricter floor for the
Ratio Cube only sent more columns to the whole-column fallback (floor 0.025:
162 of 640 columns, against 110 at 0.03, of which 72 have fewer than 20
pixels at all).

#### Excluded ranges

| Range (nm) | Why | Source |
| --- | --- | --- |
| 1000–1050 | The VNIR/IR detector join: MTRDR band centers jump from 1010 to 1047 nm here, dropping the IR detector's first bands | Murchie et al. 2007, 2009; MTRDR band list |
| 1950–2100 | The residual of the atmospheric CO₂ triplet at 1.96, 2.01 and 2.06 µm left by the volcano-scan correction (uncheck it, or delete it from Excluded Ranges, for data with a better correction) | McGuire et al. 2009 |
| 2650–4000 | The IR order-sorting filter boundary near 2.7 µm (MTRDR band centers jump from 2655 to 2800 nm), the 2.7 µm CO₂ band, the 3 µm H₂O band and thermal emission; 4000 nm is past CRISM's last band | Murchie et al. 2007, 2009 |

The IR detector's other filter boundary is not identified from the band
centers (MTRDR has no gap there), so no further range is excluded; if a
scene shows a step, add its range. The windows lie within 1050–2650 nm, so
the checks need the IR detector: a VNIR-only cube can be ratioed only with
the Whole column method.

#### Windows

Band centers and shoulders follow the parameters of Viviano-Beck et al.
(2014). A window that crosses an excluded range is fitted on both sides of
it, as BD1900r2 is across the CO₂ range.

| Check | Window | Range (nm) | Why (Viviano-Beck et al. 2014 parameter) |
| --- | --- | --- | --- |
| Generic | 1.05–1.7 µm | 1050–1700 | Broad: the 1 µm mafic region (OLINDEX3, LCPINDEX2/HCPINDEX2 shoulders) and 1.4 µm hydration |
| Generic | 1.7–2.65 µm | 1700–2650 | Broad: the 1.9 µm H₂O band, the pyroxene 2 µm band and all the 2.1–2.6 µm metal-OH, sulfate and carbonate bands |
| Plagioclase | 1 µm mafic | 1050–1700 | Olivine 1.05–1.7 µm band and the pyroxene 1 µm wing (OLINDEX3; LCPINDEX2/HCPINDEX2 shoulder 1.56–1.69 µm) |
| Plagioclase | 2 µm pyroxene | 1700–2550 | LCP 1.81–1.87 µm (LCPINDEX2) and HCP 2.12–2.46 µm (HCPINDEX2) |
| Olivine | 1 µm olivine | 1050–1800 | OLINDEX3 out to its 1.75–1.86 µm shoulder, so the broad band is seen whole |
| Olivine | 2 µm pyroxene | 1700–2550 | Pyroxene, whose bands overlap olivine's |
| Fe/Mg phyllosilicates | 1.9 µm H₂O | 1840–2140 | BD1900r2: band 1908–1941 nm, shoulders 1862–1875 and 2112–2126 nm |
| Fe/Mg phyllosilicates | 2.3 µm Fe/Mg-OH | 2120–2400 | D2300, BD2290: Fe/Mg-OH 2.28–2.33 µm |
| Fe/Mg phyllosilicates | 2.4 µm Fe/Mg-OH | 2340–2520 | The 2.39–2.41 µm combination band and MIN2295_2480's 2.48 µm minimum |
| Al phyllosilicates | 1.9 µm H₂O | 1840–2140 | As above |
| Al phyllosilicates | 2.2 µm Al-OH | 2120–2320 | BD2210_2, BD2190, MIN2250: Al-OH and Si-OH 2.16–2.26 µm |
| Carbonates | 2.3 µm carbonate | 2200–2420 | BDCARB, MIN2345_2537: Mg and Ca carbonate 2.30–2.34 µm |
| Carbonates | 2.5 µm carbonate | 2390–2620 | BDCARB: 2.50–2.54 µm, shoulder 2.60 µm |
| Sulfates | 1.9 µm H₂O | 1840–2140 | As above (most sulfates are hydrated) |
| Sulfates | 2.1 µm sulfate | 2100–2260 | BD2100_2: monohydrated sulfate 2.10–2.13 µm; its short-wavelength wing lies in the CO₂ range, so with the default exclusions its depth is underestimated |
| Sulfates | 2.4 µm sulfate | 2330–2600 | SINDEX2: the polyhydrated sulfate 2.4 µm drop |
| Hydrated (any) | 1.4 µm OH/H₂O | 1300–1560 | BD1400: the 1.4 µm OH/H₂O overtone |
| Hydrated (any) | 1.9 µm H₂O | 1840–2140 | As above |
| Hydrated (any) | 2.2–2.3 µm metal-OH | 2120–2400 | D2300, BD2210_2, MIN2250 |

**Why a preset checks more than its target.** The aim is a denominator free
of every feature that could pass for, or cover, the target in the ratio:
a plagioclase or olivine ratio is spoiled by mafic bands in the denominator
(so both check the 1 µm and 2 µm mafic regions); water in the denominator
produces an inverted 1.9 µm band that reads as a loss of hydration, so every
hydrated target checks 1.9 µm; one hydrated mineral's metal-OH band in the
denominator would read as an absence of another's.

#### Calibration of the defaults

Measured on one scene, MTRDR **FRT000107A7_07_IF166J_MTR3** (columns from its
IN file), so **these are starting points, not validated thresholds**. Spectra:
9-pixel in-column run means through the Ratio Cube's own code
(`inColumnRunMeans`, then the check; a random sample of 40,000 of the
447,479 pixels with data, and the whole scene for the column fallbacks), and
4,000 5 × 5 box means (the readout's input) at random places.

What ordinary terrain looks like in this scene (run means, medians): a
2.6 % dip near 1.22 µm in the 1 µm windows, a 2.7 % dip at 1.92 µm in the
1.9 µm window, 2.3 % near 2.23–2.34 µm, per-band noise 0.25–0.38 %, and
slopes of 0.06–0.5 per µm (the larger ones in narrow windows on a band's
wing). With a floor of 1–2 % (the level of the old limits) Generic passed
0–1 % of pixels and the mafic presets 1–2 %; a floor of 3 % passes about a quarter of the terrain for
Generic while failing the clear features below. Slope limits above 0.5 per
µm changed the rates by at most 13 points (Sulfates); 0.75 leaves the slope
to catch only steep spectra.

Pass rates at the defaults (floor 0.03, k 3, slope 0.75 per µm):

| Check | Run means (9 px) | Box means (5 × 5) | Ratio cube columns falling back to whole column (of 640) |
| --- | --- | --- | --- |
| Generic | 24 % | 27 % | 110 |
| Plagioclase | 25 % | 27 % | 110 |
| Olivine | 25 % | 27 % | 110 |
| Fe/Mg phyllosilicates | 55 % | 55 % | 64 |
| Al phyllosilicates | 56 % | 57 % | 64 |
| Carbonates | 90 % | 91 % | 64 |
| Sulfates | 56 % | 56 % | 64 |
| Hydrated (any) | 55 % | 55 % | 64 |

72 columns have fewer than 20 pixels with data at all, so they fall back
whatever the check.

**Clear features fail.** HyPyRameter (used only as an outside probe) was
run on the sampled run means; of the 1 % with the highest value of each
parameter, the share each check fails:

| Probe parameter (99th percentile value) | Check | Fails |
| --- | --- | --- |
| BD1900r2 (0.061) | Generic, Fe/Mg, Al, Sulfates, Hydrated | 100 % |
| D2300 (0.057) | Generic, Fe/Mg | 100 %, 99 % |
| OLINDEX3 (0.138) | Generic, Olivine, Plagioclase | 98 %, 97 %, 97 % |
| HCPINDEX2 (0.028) | Generic, Plagioclase | 100 %, 100 % |
| BD2210_2 (0.025) | Al phyllosilicates | 91 % |
| BD1400 (0.012) | Hydrated (any) | 68 % |
| BDCARB (0.006), SINDEX2 (0.003), BD2100_2 (0.004) | Carbonates, Sulfates | 65 %, 46 %, 0 % |

The last row's parameters are near zero even at their 99th percentile: the
scene has no clear carbonate or sulfate, so those pixels are not features
and passing them is expected. Box means gave the same picture.

**Recalibrate for your data.** On a different scene (dustier, darker, TRR3
rather than MTRDR, another atmospheric correction) look at the readout on a
few areas you judge bland and a few with known features: the readout gives
each window's dip, noise and limit, which is what to set the floor and k
from. Report the values used.

### VNIR bands

The backplane's column (the IR Sample band), and the image columns of a
joined (J) cube, are the IR (L) detector's. In a cube with bands on both
detectors, the bands below 1000 nm lie on the VNIR (S) detector, whose
columns match only approximately, so a ratio there is only approximately
column-matched. The record sets `vnirApproximate` in that case, and the
description says so. A VNIR-only (S) cube ratioed by its own image columns
is column-matched and not flagged.

### The 1 µm detector join

CRISM measures 362–1053 nm on its VNIR (S) detector and 1001–3920 nm on its
IR (L) detector (Murchie et al. 2007). A joined (J) cube such as an MTRDR
keeps the S bands up to about 1010 nm and the L bands from about 1047 nm,
and the two detectors' calibrations differ slightly, so a spectrum often
**steps at the join**. In FRT00005850's MTRDR the IR side sits a median
1.9 % below the VNIR side (IR/VNIR 0.981; 5th–95th percentile of pixels
0.969–0.996). A ratio divides a step out only as far as numerator and
denominator share it: for pairs of pixels in the same column of that MTRDR
the median step left in the ratio was 0.7 %, and 2 % at the 95th
percentile.

**Scale VNIR to IR at 1 µm** (off by default; in the Ratio Cube dialog and
the workspace's Ratio Controls) removes the step from each spectrum before
it is ratioed. This is Varda's own empirical correction:

1. **The join** is the widest gap between neighboring band centers between
   950 and 1100 nm that is at least 3 times the cube's median band spacing
   (1010.2 to 1047.2 nm in an MTRDR). The VNIR bands are those at or below
   its short end. A cube with no such gap (IR-only, VNIR-only) has no join,
   and the option is unavailable.
2. **Fit.** A least-squares line is fitted to each side: the good bands
   (not marked bad in `bbl`, with a value) within **80 nm** of the gap, at
   least 4 per side (13 and 13 in an MTRDR).
3. **Scale.** Both lines are evaluated at the gap's middle (1028.7 nm in an
   MTRDR), and the VNIR bands are multiplied by the factor *f* = IR / VNIR
   there. The IR bands are unchanged, so every band depth and parameter
   beyond 1.05 µm is unchanged. A spectrum without enough values on a side,
   or with a level that isn't positive, is left as it is.

In the workspace the numerator and the denominator box means are each
scaled by their own factor, then ratioed. The Ratio Cube scales every pixel
by its own factor before anything else, so the bland pixels are chosen and
the column denominators averaged from scaled spectra.

**Uncertainty.** *f*'s 1-sigma comes from the two fits' standard errors at
the gap's middle, (σ_f / f)² = (σ_IR / IR)² + (σ_VNIR / VNIR)². On
synthetic spectra with white noise it matches the spread of repeated
estimates within 15 % (`test_detector_join`). On the MTRDR above it was a
median 0.0064 per pixel and hardly smaller (0.0058) for 5 × 5 box means:
the scatter about the lines is mostly the spectrum's own shape, not noise,
so σ_f states how well a straight line describes each side, and the
correction is good to about half a percent. **Limitation:** the lines bridge
a 37 nm gap. A spectrum that curves there (the short-wavelength side of a
1 µm iron band) is made continuous to the lines, not to the truth, so
interpret band shapes that straddle 1 µm with the factor and its σ in view,
or compare with the option off.

**Where it is recorded.**

- *Workspace:* the factors (IR/VNIR) of the current pair are shown beside the
  check box (numerator / denominator; their 1-sigma in its tooltip), and
  whether or not the option is on, what S and P save carries "1 µm detector
  join (<VNIR end>/<IR start> nm): VNIR scaled to IR" (or "not corrected")
  with both factors ± 1-sigma, in the description. Sessions keep the setting.
- *Ratio Cube:* the record's `joinCorrection` holds `enabled` and, when on,
  `vnirEndNm`, `irStartNm`, `atNm`, `fitNm`, the factor's median and 5th and
  95th percentiles over pixels (`factorMedian`, `factorP5`, `factorP95`),
  `sigmaMedian`, and `unscaledPixels`. The **Denominators** image gains two
  bands, "VNIR join factor (IR/VNIR)" and "VNIR join factor 1-sigma", per
  pixel. The description says it in one sentence.

A methods section can add: "Before ratioing, the VNIR (S detector) bands of
each spectrum were scaled to the IR (L detector) at their join: lines fitted
to the good bands within 80 nm on each side of the 1010–1047 nm gap were
matched at 1029 nm (Varda <version>; median factor <factorMedian>, 5–95 %
<factorP5>–<factorP95>, median 1-sigma <sigmaMedian>)."

### Outputs

- **Ratio cube** (`… ratio cube`): float32, the input's bands, wavelengths and
  grid; dimensionless. Its description states the method, the blandness
  settings, and **how many columns fell back** (to neighbors, to the whole
  column, or had no data). Its `ratioCube` metadata record (see
  [Saving and sharing](#saving-and-sharing)) holds the method, blandness check
  name and `thresholds` (the check's `windows` with their nm ranges,
  `excludedNm`, `depthFloor`, `noiseMultiple`, `maxSlope`, the `continuum`,
  `smoothingBands` and `noise` methods, and `settings`, all of it as one
  sentence), run length, Minimum Pixels, Neighbor Radius, where the columns
  came from (`geometry`, `geometryFile`, `geometryRoute`, `binning`; see
  [The geometry file](#the-geometry-file)), how the oversampling
  was found (`oversamplingFrom`), the good bands used, and for every column:
  - `strip`, `column`;
  - `count`: pixels that went into the denominator (with a value in at least
    one band); `validCount`: per band, the pixels with a value in that band;
  - `sensorPixels`: distinct sensor pixels (detector column, line) among
    them, from the backplane's IR Line band (`null` without one);
    `oversampling`: map pixels per sensor pixel (below); `effectiveCount`:
    `count / oversampling`;
  - `fallback` (`null`, `neighbors`, `whole column` or `no data`) and
    `borrowed` (`[[column, pixels], …]`, for *neighbors*);
  - `spectrum` and `std`: the denominator and its spread, per band.

  And `joinCorrection`: whether the VNIR bands were scaled to the IR, and how
  (see [The 1 µm detector join](#the-1-µm-detector-join)).
- **Denominators** (`… ratio denominators`): band 1 "Used as denominator" is 1
  where the pixel was pooled into at least one column's denominator (with a
  value in at least one band), and 0
  otherwise (NaN with no backplane column). That is the pixel's own column's
  bland pixels; the bland pixels another column borrowed (*neighbors*, so
  they are 1 for that column too); or every pixel of the column in the
  *whole column* fallback, bland or not. A column that borrowed has 0 in its
  own pixels unless they were bland. Band 2 "Column denominator count" is the
  number of pixels pooled for the pixel's column (`count`; 0 for *no data*).
  In the whole-column fallback a pixel with no value in any band is neither
  pooled nor counted. With **Scale VNIR to IR at 1 µm**, bands 3 and 4 are
  each pixel's factor and its 1-sigma (NaN where the pixel was left unscaled).

### Saving and sharing

Saved as ENVI (`ratio.img`), a ratio cube is **four files that must travel
together**:

| File | Holds |
| --- | --- |
| `ratio.img` | the ratio values |
| `ratio.hdr` | the ENVI header; its `description` is the methods description (method, blandness check and limits, run length, Minimum Pixels, Neighbor Radius, how many columns fell back, where the columns came from, how missing values and the standard error are handled) and lists the other files |
| `ratio.img.aux.xml` | the full `ratioCube` record (GDAL's metadata file, which ENVI itself ignores; Varda reads it back) |
| `ratio.columns.csv` | the per-column table, readable anywhere |

Saved as GeoTIFF (`ratio.tif`), the `.tif` carries the description and the
whole record, and `ratio.columns.csv` is written beside it too. The
`… ratio denominators` image is saved as its own file.

`ratio.columns.csv` has one row per column: `strip`, `column`, `count`,
`sensorPixels`, `oversampling`, `effectiveCount`, `fallback`, `borrowed`
(`column:pixels`, `;`-separated), `blandness` (the check's name) and
`blandnessSettings` (its windows, excluded ranges, floor, k and slope, the
same sentence as the description's; both empty for Whole column), then for each good band `B<n>` (the
image's 1-based band number; its wavelength is in the header) the
denominator `mean_B<n>`, its `std_B<n>` and the valid count `n_B<n>`.
Empty cells are missing values.

### Assessing uncertainty and quality

- **`std` is the population standard deviation (ddof = 0)** of the pixels
  pooled for the column's denominator, per band. It is a spread, not a standard
  error.
- **Effective sample size.** On a map-projected cube (MTRDR) one sensor pixel
  is resampled into several map pixels, which are not independent. Per band
  *b*, the effective number of independent samples is
  n_eff,b = `validCount`_b / `oversampling`, where `oversampling` is
  `count / sensorPixels` when the backplane has an IR Line band (an MTRDR's
  IN file does: distinct (IR Sample, IR Line) pairs are counted), and
  otherwise an estimate, (pixels of the column per image row)², which assumes
  the grid oversamples along track as much as across (`oversamplingFrom`
  says which). An unprojected cube's image pixels are sensor pixels
  (`oversampling` 1). On MTRDR FRT000107A7 the IN file gives 1.92 map pixels
  per sensor pixel, where the estimate would give 1.72, so prefer the IN
  file. In the same way, the 9-pixel run that judges blandness spans only
  about 9 / √oversampling sensor lines (about 6.5 there).
- **Standard error of the denominator:** σ_D,b = `std`_b / √n_eff,b. For
  bland and neighbor denominators every pooled pixel has every band, so
  `validCount` equals `count`; in the whole-column fallback it can be
  smaller.
- **Neighbor fallback:** a *neighbors* denominator pools pixels of other
  columns, so its `std` includes the column-to-column gain differences as
  well as the scene: it overstates the spread of one column's pixels, and
  its mean carries the neighbors' gain, which the ratio does not cancel.
- The relative uncertainty of a ratio pixel R = N / D, with numerator
  uncertainty σ_N (for example the cube's noise) and denominator D with
  σ_D as above, is
  σ_R / R ≈ √((σ_N / N)² + (σ_D / D)²), treating the two as independent. The
  denominator term is not independent of the numerator for the pixels that are
  pooled into D; for a column with many pooled pixels it is small.
- A large `std`, a small `count`, or a *neighbors* or *whole column* fallback
  all mean a less trustworthy denominator. A *whole column* denominator, or a
  bland one with a loose limit, can contain real absorptions that then show
  as inverted features in the ratio. Check the "Used as denominator" image for
  where the pooled pixels are (they should not be only one part of the scene)
  and compare the denominators of neighboring columns.
- **How bland were the pooled pixels?** The check passed every pooled run
  mean against max(floor, k × noise) in each window, so a pooled pixel's
  features are at most that deep (in its run mean, after 5-band smoothing).
  The denominator, a mean of many such pixels, has smaller features still,
  but the depths are not reported per column: to see what is left, plot a
  column's `spectrum` (or the denominator box in the workspace) and read the
  readout on it.
- Blandness limits are heuristics calibrated on one scene (see
  [Calibration](#calibration-of-the-defaults)). Limitations: the continuum is
  a hull per window, so a band wider than its window, or one whose shoulder
  is in an excluded range (the 2.1 µm sulfate window), is underestimated;
  residual atmosphere outside the excluded ranges (for example a stronger CO₂
  residual reaching below 1950 nm, or the 1.92 µm dip common in this scene,
  which may be hydration or atmosphere) counts as a feature; and smoothing
  makes a very narrow band slightly shallower. Assumptions: the artifacts are
  constant along a column, and the pooled pixels' real signal averages to a
  featureless spectrum.

### Reporting it

A methods section can say: "Each I/F spectrum was divided by the mean spectrum
of the bland pixels of its detector column (column from the <IN file's IR
sample (<geometryFile>, found <geometryRoute>), or the unprojected cube's image
columns>; blandness: the
<check> check of Varda <version>, in which every window (<windows and nm
ranges>) of the continuum-removed spectrum (upper convex hull per window,
5-band running mean), outside <excluded ranges> nm, had its deepest
absorption no deeper than the larger of <floor> and <k> times the spectrum's
per-band noise, and a relative slope within ±<slope> per µm, judged on
means of <run length> pixels in the column; at least <Minimum Pixels> pixels,
borrowing from columns within <Neighbor Radius> of the same strip, else the
whole column)", then state how many columns fell back, taken from the
description. The standard errors of the denominators (σ_D, above) come from
the per-column table. The `settings` sentence in the record (and the
`blandnessSettings` column) gives the check's part in full. Cite
Viviano-Beck et al. (2014) for the window positions and McGuire et al.
(2009) for the CO₂ exclusion.

## The CRISM Ratio workspace

**Workspace → New CRISM Ratio Workspace…** is for assessing regions fast:
navigate on a browse image, drop a numerator box and a denominator box, look at
the numerator, denominator and ratio spectra, judge whether the denominator is
bland, save the pairs worth keeping, and move on. It does not change any image;
what it saves is ROIs and points.

### Layout

- **Browse view** (left): usually a CRISM summary-parameter (SR) product, or any
  image of the same scene such as a co-registered THEMIS image. It is only for
  navigation: no spectrum is ever read from it.
- **I/F view** (middle): the cube the spectra are measured on. The boxes are
  defined in this cube's pixels and shown on both views.
- **Ratio Controls** (right, top): one row (it wraps when narrow) with the box
  size (**Box** width × height, pixels), **Lock to sensor column** (see
  [Column lock](#column-lock)), **Choose/Change Geometry File…**, **Go to
  Sensor…** (see [Go to a sensor position](#go-to-a-sensor-position)), **Show
  ROIs** (on by default: the saved ROIs, each pair's numerator and
  denominator boxes, outlined in their class colors on both images, the
  browse one through the link; the ROI Manager hides or shows them one by
  one, and selecting one there highlights it), **Scale VNIR to IR at 1 µm**
  with the current pair's factors beside it (see
  [The 1 µm detector join](#the-1-µm-detector-join); unavailable for a cube on
  one detector), and **?**,
  whose tooltip (hover, or click it) lists the clicks and keys of
  [the click loop](#the-click-loop).
- **Spectra** (under the controls, left): the numerator (green) and the
  denominator (magenta) mean spectra overlaid on one plot, with a legend.
  The denominator's failing blandness windows are shaded on it (see
  [Blandness readout](#blandness-readout)). **Separate** in the panel's title
  bar puts them on two plots instead, numerator above denominator, in the
  same panel; click it again to overlay them.
- **Ratio** (beside Spectra): numerator ÷ denominator. Its sidebar overlays
  library spectra; it starts folded, so the plot keeps its width; open it with
  the arrow at the plot's right edge, and drag its left edge to resize it.
- **Denominator Blandness** (under both plots): the check, its limits and the
  CO₂ exclusion on one row, then a one-line verdict. **Details ▸** opens the
  per-window table.
- **ROI Manager**, **Points Manager** and **ROI Plots** sit folded on the right
  edge, and the render settings and histograms of both views on the bottom
  edge: click their tabs to open them. Right-click either view for its
  image's **Render Settings** and **Histogram**, and **Panels** for every
  panel of the workspace: one closed by mistake comes back from there.

Each new pair replaces the curves and refits the Y ranges. The plots show
the whole spectrum, or the wavelength range you zoomed or panned to, which
stays while you click new pairs (Y refits to the data inside it); **Fit to
Data** in a plot's sidebar, or zooming out to the whole spectrum, goes back to
the whole spectrum for each pair. The plots always show the same wavelength
range, so zooming or panning one moves the others. The image's bad bands (ENVI `bbl`) are left out
of the plots and the readout. An image without wavelengths is plotted by band
number, and the readout says the check needs wavelengths.

**Plot shape.** Spectral features read best on a plot that is square or taller
than wide, so the plots start taller than wide on a laptop screen (about 350 ×
650 pixels each at 1440 × 900). **Shape** in each plot panel's title bar keeps
that panel's plots at a shape as you resize: **Free** (default) fills the
panel, **Square** keeps width = height, **Tall (3:4)** keeps width:height at
3:4; the spare space beside or above the plot is left empty. With Separate,
each of the two plots keeps the shape. The shape is of the plot (axes and
labels included), not of the data: it does not lock the wavelength and
reflectance scales to each other, so zooming still changes either freely.

**Moving and folding panels.** Every panel is a dock: drag its title bar to
move it beside, above or below another, or onto another to tab them; drag
the dividers to resize; the title bar's buttons float it as a window or fold
it into a tab on the window's edge (click the tab to open it). The browse and
I/F views and the plot panels can't be closed, only folded. The session keeps
where every panel is, the plot shapes, Separate and whether Details is open.

The dialog picks a browse image (the one whose name contains `_SR`, else the
first image) and an I/F image (one whose detector columns are known, else the
next image); change either.

### Link modes

- **Pixel match**: the same pixel in both views. Needs both images on one
  pixel grid (same size and, if georeferenced, same projection and transform).
- **Map link**: the same map location. Needs both images georeferenced, and is
  required when the grids differ. A click on the browse view is converted to
  map coordinates and then to the I/F pixel it falls in (floor of the
  coordinate); the boxes are always I/F pixel boxes and are drawn on the
  browse view through the same conversion. Panning and zooming the views
  together uses the link too.

The dialog does not offer a link that cannot work: without georeferencing on
both images Map link is unavailable (it would silently fall back to pixels),
and different grids rule out Pixel match; if neither works it will not open.
If a restored session or a link still leaves something unlinked as asked, a
notice across the top says which images and why. A click that falls outside
the I/F cube is refused with a message.

### The click loop

1. **Ctrl/⌘+left-click** a numerator box on either view. It is centered on the
   pixel; its size is **Box** width × height (pixels, 1 to 99, default 5 by
   5, the same for both boxes).
2. **Ctrl/⌘+right-click** the denominator box (same size).
3. The Spectra plot shows the **mean spectrum of each box** measured on the
   I/F cube, and the Ratio plot their ratio (numerator mean divided by denominator mean, band by
   band, dimensionless). Plain clicks and drags pan and zoom as usual.
4. Click a new numerator and everything updates: the denominator stays, or
   follows the column (below). **Esc** clears both boxes.
5. **S** saves the pair as two ROIs (rectangles in the I/F cube's pixels)
   and makes the denominator ROI the ratio reference in the ROI Manager.
   **P** saves the numerator's center pixel as a point in the Points
   Manager. Both first ask for a **class** (see
   [Saving pairs](#saving-pairs-the-class) below; S then Enter saves). The
   name ends with the verdict, for example
   `Ratio 3 (denominator ✓ Generic)` for a pair and
   `Ratio numerator (11, 21) (denominator ✓ Generic)` for a point: the mark
   and the check that was selected when you saved. Their **description**
   (the ROIs' `description` column, and the point's `description` attribute
   when exported) records the check, the limits (and which you changed), the
   excluded ranges, and every window's dip, limit, noise and slope, for
   example `Denominator blandness, Generic check: ✗ not bland. Limits: dip ≤
   max(3.0 %, 3 × noise), |slope| ≤ 0.5 /µm (edited: slope); excluded
   1000–1050, 1950–2100, 2650–4000 nm. 1.05–1.7 µm 1050–1700 nm: dip 0.4 %
   at 1139 nm (limit 3.0 %, noise 0.2 %), slope 0.105 /µm ✓; 1.7–2.65 µm
   1700–2650 nm: dip 4.0 % at 2305 nm (limit 3.0 %, noise 0.2 %), slope
   0.091 /µm ✗.` A point saved without a denominator has no verdict and an
   empty description.

### Saving pairs: the class

**S** opens **Save ratio pair as ROIs**, **P** opens **Save numerator pixel
as a point**. Each has:

- **Class**, editable, with the text selected so you can type over it. It is
  prefilled with the **reference mineral**: the library spectrum overlaid on
  the Ratio plot (with the plot's library picker). With several overlaid, the
  one added last is prefilled and the others are listed first in the
  dropdown, followed by the classes already used in this workspace's ROI
  Manager. With none overlaid, the last class used is prefilled (empty at
  first). What is prefilled for a library spectrum is its metadata's
  `mineral`, `class` or `category` field (the first present, any case) if it
  has one; otherwise the **short form of its name**: the name split into
  words at spaces and underscores, kept up to the first sample-ID-like word,
  one containing a digit or a period (`GDS70.d`, `165um`, `CM9`). A
  composition such as `Fo91` or `An60` (Fo, Fa, En, Fs, Wo, An, Ab or Or and
  a number) is kept. So `Olivine Fo91 GDS70.d 165-250 µm USGS splib07a AREF`
  gives `Olivine Fo91` and `Olivine_GDS70.a_Fo89_165um_BECKb_AREF` gives
  `Olivine`; a name whose first word is an ID is kept whole. The full name
  is listed in the dropdown right after it.
- **Name**, prefilled with the automatic name above. Emptied, the automatic
  name is used.
- A summary: the numerator and denominator center pixels (I/F cube column,
  row) and the denominator's blandness verdict.
- **Don't ask; use the reference mineral**: from then on S and P save at
  once with the class that would have been prefilled (the reference mineral,
  else the last class) and the automatic name. **Shift+S** and **Shift+P**
  always ask, and clear it if you uncheck it there.

**Enter** saves and **Esc** cancels (nothing is saved). The class does not
change any measurement: it is a label that travels with the ROIs and points.

**How the pair is classed.** In Varda an ROI's class is its name (the ROI
Manager's **Class** column): ROIs of one class share a color and are grouped
together. A saved pair is:

| ROI | Class (name) | `pair` | `role` | `target` |
|---|---|---|---|---|
| Numerator | the class entered, e.g. `Olivine` | the Name, e.g. `Ratio 3 (denominator ✓ Generic)` | `numerator` | `Olivine` |
| Denominator | `Denominator` | the same Name | `denominator` | `Olivine` |

So every numerator of one mineral is one class, and every denominator is in
the one `Denominator` class (bland reference regions, whatever they were
used against); `pair` joins a numerator to its denominator and `target` says
what a denominator was the reference for. With the class left empty, the
numerator's class is the Name. An ROI joining a class that already exists
takes the class's color. A new class gets the next color not yet used by a
class from Tableau 10 without its green, purple and gray (blue `#4e79a7`,
orange `#f28e2b`, red `#e15759`, teal `#76b7b2`, yellow `#edc948`, pink
`#ff9da7`, brown `#9c755f`; repeated after seven classes), so it is not
confused with the green numerator box or the magenta denominator box. The
`Denominator` class is always light gray (`#d9d9d9`). ROIs are drawn half
opaque. `pair`, `role`, `target` and
`description` are ordinary ROI columns: they are kept by the session and
written by ROI export (GeoJSON, GeoPackage, Shapefile).

**Points** have a name and a description but no class, so the class opens
the point's description, for example `Class: Olivine. Denominator
blandness, Generic check: ✓ bland. …`, and the Name is the point's name.

The session keeps the last class used and whether to ask.

### Column lock

Sensor artifacts are fixed per detector column, so a denominator is only a good
reference if it lies in the same column as the numerator (see the top of this
page). With **Lock to sensor column** (the denominator to the numerator's) on (the
default when the detector columns are known), the right-clicked denominator is put on
the numerator's sensor column in the clicked row, and the denominator then
**follows**: each new numerator moves it into the new numerator's column,
keeping its row. The column comes from the I/F cube's backplane, or the image
columns of an unprojected CRISM cube, exactly as for the Ratio Cube analysis
(see [What it needs](#what-it-needs) and [The geometry file](#the-geometry-file)),
and strips are kept apart. The status bar says "Column lock on (image columns)"
in the second case; the check box's tooltip names the backplane file and how it
was found.

Two cases where the lock cannot be met are said, never silent:

- **The denominator's row has no pixels in the numerator's column** (the
  column's strip does not reach that row, or the box sits on no-data): the
  denominator is **kept where it is** and the view you clicked shows
  "Denominator left in place: …" for a few seconds. That ratio is not
  column-matched; move the denominator to a row the column reaches.
- **No detector columns for the I/F cube** (no backplane with an IR Sample
  band beside the cube or the file it was made from, and not an unprojected
  CRISM cube): the lock is unavailable (the check box is disabled and
  unchecked) and the status bar says "Column lock unavailable … (choose its
  geometry file)". **Choose Geometry File…** beside the check box points Varda
  to the IN file by hand (see [The geometry file](#the-geometry-file)); once
  accepted, the lock turns on at once. The button then reads **Change
  Geometry File…**.
  Ratioing still works; the denominator is simply where you click, so the
  ratio keeps whatever column-dependent artifacts the two columns differ by.

### Go to a sensor position

**Go to Sensor…** in Ratio Controls places the boxes by detector position
instead of a click: type the numerator's **sample** and **line**, and the
denominator's, and press **Go**. The positions are 0-based, as the I/F cube's
geometry gives them: the IN file's IR Sample and IR Line bands (for Varda's
own processed cubes, the `…_in…_vmtr` file's Source sample and Source line,
the TRR3's sample and line), or an unprojected cube's columns and rows. Each
box goes on the image pixel whose detector position is nearest the one
typed, exactly as a click there would place it: with **Lock to sensor
column** on, the denominator goes on the numerator's column in that row, as
a right-click does. **On the numerator's detector column** (on when the lock
is) fills in the denominator's sample from the numerator's; untick **Place
the denominator too** to move only the numerator. The dialog stays open, so
positions can be typed one after another, opens on the current boxes'
positions, and says where each box landed, with how many detector pixels
away the nearest map pixel was when no pixel saw the position typed (a map
pixel sees one detector position, so in a map-projected cube some positions
fall between pixels). It is unavailable when the geometry has no detector
lines (a backplane with IR Sample but no IR Line).

### Blandness readout

The **Denominator Blandness** strip under the plots tests the **denominator box's mean
spectrum**: is it featureless enough to divide by? Any feature in it appears
inverted at the same wavelength in the ratio, where it could be mistaken for
(or hide) a feature of the numerator.

It runs the checks described in [Blandness checks](#blandness-checks) on
the same bands (bad bands left out), with the readout's own defaults.

- **Check**: Generic (default) or a target preset (Plagioclase, Olivine,
  Fe/Mg phyllosilicates, Al phyllosilicates, Carbonates, Sulfates, Hydrated
  (any)); the windows of each are in the table above. No optional feature is
  needed.
- **The verdict** (one line): **✓ Bland enough to divide by** only if every
  window passes; otherwise **✗ Not bland:** and, for each failing window, why:
  its deepest dip and the limit it broke, and/or its slope and the slope
  limit, for example `✗ Not bland: 1.7–2.65 µm, 3.9 % dip at 1948 nm (limit
  3.0 %)`. A preset may be ✗ where Generic is ✓ or the other way round,
  because each looks at different windows.
- **Details ▸** (folded by default) opens **one row per window**: its name,
  its range (nm), the **deepest dip** (depth in % of the continuum, and the
  wavelength where it is, e.g. `2.7 % at 1920 nm`), the **limit** that dip is
  held to (the larger of the floor and k × the window's noise; hover over it
  to see both), the **slope** (per µm) and **✓** or **✗**. The session keeps
  whether it is open.
- **On the Spectra plot**, every failing window is shaded, and its deepest
  dip has a marker on the denominator's curve with a label (window name and
  depth), so a ✗ points at something you can see in the spectrum. With
  Separate, they are on the denominator's plot.
- **Limits are editable**: **Floor** (%), **k** and **Slope ≤** (per µm)
  beside the check, and **Leave out CO₂ (1950–2100 nm)** (the CO₂ residual),
  on by default. The limits are shared by all checks, so changing the check keeps
  them; **Defaults** puts them back. The session keeps them and the CO₂
  choice, and each saved ROI's and point's description records the limits
  it was judged with; still, **report any limit you changed**. The limits
  are starting points calibrated on one scene (see
  [Calibration](#calibration-of-the-defaults)).

### Assessing uncertainty and quality

- The plots show means of the boxes and carry **no error bars**. A box mean
  averages Box Width × Box Height pixels, so its noise falls roughly with the
  square root of that count, while a bigger box risks mixing materials: use the
  saved ROIs' statistics (mean and standard deviation per band, in the ROI
  Manager) for a spread, and compare against neighboring boxes.
- A ratio is trustworthy where the denominator is bland **and** in the
  numerator's column. Read both the verdict and the column-lock state (✗, a
  "left in place" message, or an unavailable lock all mean a less reliable
  ratio).
- With a Map link, the box position is only as good as the browse image's
  registration to the I/F cube, and one browse pixel may cover several I/F
  pixels; the spectra are not affected, only where the box lands. Check the box
  on the I/F view.
- The blandness check is a heuristic on one spectrum, not a statistical test.
  Read the dip against its limit: a dip just under the limit is not much
  blander than one just over it. The noise column tells you how much of the
  dip the spectrum's own noise could explain; a dip several times deeper
  than its noise and above the floor is a real feature of the denominator
  (or residual atmosphere), and will appear inverted in the ratio at the
  marked wavelength. The limitations of the method are listed in the Ratio
  Cube's [uncertainty section](#assessing-uncertainty-and-quality).

### Reporting it

A methods section can say: "Spectra were the means of <width> × <height> pixel
boxes of the <I/F cube>, ratioed band by band to a denominator box in the same
sensor column (column from the <IN backplane / unprojected image columns>;
lock on / off), whose
spectrum was judged bland by the <check> check of Varda <version>: in each
window (<windows and nm ranges>), outside <excluded ranges> nm, the deepest
absorption of the continuum-removed spectrum (upper convex hull per window,
5-band running mean) was no deeper than the larger of <floor> and <k> times
the spectrum's per-band noise, and the relative slope within ±<slope> per
µm; boxes were chosen on <browse image> linked by <pixel / map location>."
Copy the windows and limits from the saved item's description. Cite
Viviano-Beck et al. (2014) for the window positions and McGuire et al.
(2009) for the CO₂ exclusion. The session
keeps the link, box size, lock, chosen check and edited limits; the saved ROIs
and points record where each pair was, the class you gave it (the ROIs'
class and `target` column, the point's description; a label you chose, not a
detection) and, in their description, the verdict with its values and
limits. Points are not yet kept by the session: export
them from the Points Manager before closing.

## References

- Clark, R. N., & Roush, T. L. (1984). Reflectance spectroscopy:
  Quantitative analysis techniques for remote sensing applications.
  *Journal of Geophysical Research*, 89(B7), 6329–6340.
- McGuire, P. C., et al. (2009). An improvement to the volcano-scan
  algorithm for atmospheric correction of CRISM and OMEGA spectral data.
  *Planetary and Space Science*, 57, 809–815.
- Murchie, S., et al. (2007). Compact Reconnaissance Imaging Spectrometer
  for Mars (CRISM) on Mars Reconnaissance Orbiter (MRO). *Journal of
  Geophysical Research*, 112, E05S03.
- Murchie, S. L., et al. (2009). Compact Reconnaissance Imaging Spectrometer
  for Mars investigation and data set from the Mars Reconnaissance
  Orbiter's primary science phase. *Journal of Geophysical Research*, 114,
  E00D07.

- Viviano-Beck, C. E., et al. (2014). Revised CRISM spectral parameters and
  summary products based on the currently detected mineral diversity on Mars.
  *Journal of Geophysical Research: Planets*, 119, 1403–1431.
