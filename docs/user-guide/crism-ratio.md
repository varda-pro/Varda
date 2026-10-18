# CRISM ratio cubes

CRISM spectra carry artifacts that are fixed for each sensor detector column:
striping, a column-dependent gain, and residual calibration structure. Dividing
a spectrum by one from the same detector column cancels them. This page
describes the **Ratio Cube** analysis, which does that for a whole cube.

## Ratio cube

**Processing → CRISM → Ratio Cube (Column Denominators)…** divides every pixel
of a CRISM I/F cube by a **denominator spectrum for its detector column**. The
result has the same bands, wavelengths and grid as the input, so it can stand
in for the I/F cube in other software.

### What it needs

The column of each pixel comes from the cube's DDR or DE geometry file (the
backplane), found next to the cube by name (for example `…_if…_trr3.img` and
`…_de…_trr3.img`). Its "IR Sample" band is the detector column, so this also
works for map-projected cubes. Where the backplane has "Target ID" and "Segment
ID" bands, columns of different source strips are kept apart. A column is the
pair (strip, column). Columns are `rint` of the backplane's sample value, so a
value halfway between two columns rounds to the even one. Without a backplane
(or with one of another size) the dialog says so instead of offering Run.

### What it does

1. **Denominator method** (default Bland pixels):
   - **Whole column**: the mean spectrum of all pixels of the column that have
     data.
   - **Bland pixels**: the mean spectrum of the column's pixels whose spectrum
     is bland (featureless), so real mineral absorptions in the scene do not
     leak into the denominator and appear, inverted, in every other row of the
     column.
2. **Blandness is judged on a run.** Each pixel's spectrum is first averaged
   with its neighbors **in the same column** (the *run length*, odd, 3–51,
   default 9; centered on the pixel and shifted inward at the ends of a
   column), and the run mean is tested. The run is never taken across columns.
   This reduces the noise the test sees; the denominator itself averages the
   pixels' own spectra.
3. **The blandness test** (Generic or a target preset, below) is applied to the
   run mean. A pixel is used only if its run mean passes **and** the pixel has
   a value in every analysis band. The image's bad bands are left out of the
   test and the denominators.
4. **Fallbacks.** A column with fewer than **Minimum Pixels** (1–10000,
   default 20) used pixels:
   1. borrows used pixels from columns ±1 to ±**Neighbor Radius** (0–10,
      default 2; 0 never borrows) of the same strip, nearest first, until it
      has enough (recorded as *neighbors*);
   2. if still short, uses every pixel of its own column (*whole column*);
   3. if the column has no pixel with data at all, it is flagged *no data* and
      its ratio pixels are NaN. A denominator is never silently NaN.
5. Every pixel is divided, band by band, by its column's denominator. The
   result is NaN where the pixel has no backplane column or no data, or the
   denominator is 0 or NaN in that band. Bad bands stay NaN.

### Blandness: Generic and presets

**Generic** (the default): the deepest absorption of the continuum-removed
spectrum (1 minus the minimum; continuum removal as in
[Spectral analysis](spectral-analysis.md)) must be at most **Generic Maximum
Depth** (default 0.02), and the absolute relative slope (least-squares slope of
the spectrum divided by its mean, per micrometer, over all good bands) at most
**Generic Maximum Slope** (default 0.10 per µm). Both settings are in the
dialog. At least three good bands are required.

**Target presets** keep only pixels free of the spectral parameters of one
mineral group (Viviano-Beck et al. 2014, computed by HyPyRameter, an
[optional feature](optional-features.md); a preset says so and cannot run
without it, while Generic works without it). Every parameter must be at most
its maximum, and the relative slope at most 0.10 per µm over the preset's slope
window. The slope window must hold at least three good bands, and the image's
wavelengths must support the parameters.

| Preset | Parameters (maximum) | Slope window | Why |
| --- | --- | --- | --- |
| Plagioclase | LCPINDEX2, HCPINDEX2, OLINDEX3 (0.02) | 1000–1700 nm | Low- and high-Ca pyroxene and olivine bands near 1 µm |
| Olivine | OLINDEX3, LCPINDEX2, HCPINDEX2 (0.02) | 1000–1700 nm | The olivine 1 µm band; the pyroxene indices exclude mafic confusion |
| Fe/Mg phyllosilicates | BD1900r2, D2300, MIN2295_2480 (0.01) | 1000–2600 nm | 1.9 µm H₂O, the 2.3 µm Fe/Mg-OH drop, 2.29–2.48 µm metal-OH |
| Al phyllosilicates | BD1900r2, MIN2250, D2300 (0.01) | 1000–2600 nm | 1.9 µm H₂O, 2.2–2.25 µm Al-OH, the 2.3 µm drop |
| Carbonates | BDCARB, MIN2295_2480, MIN2345_2537 (0.01) | 1000–2600 nm | Carbonate overtone and metal-OH/carbonate minima near 2.3–2.5 µm |
| Sulfates | SINDEX2, BD1900r2 (0.02) | 1000–2600 nm | The 2.4 µm sulfate drop with the 1.9 µm hydration band |
| Hydrated (any) | BD1900r2, D2300, MIN2250 (0.01) | 1000–2600 nm | Any hydration: 1.9 µm H₂O, the 2.3 µm drop, 2.25 µm Al-OH |

These limits are **conservative starting points, not validated thresholds**.
The parameters are band-depth-like indices near 0 for a featureless spectrum,
so 1 to 2 percent is below what typically marks a detection, and a denominator
under it adds little false structure to a ratio. The mafic presets use the
1000–1700 nm window (the 1 µm region, before hydration features); the hydrated
ones use 1000–2600 nm, the range the 1.9, 2.3 and 2.5 µm parameters span. Edit
them to suit your scene: the Generic limits in the dialog, and the preset
table in `src/varda/analysis/blandness.py`. Report any value you change. The
dialog's Generic limits do not affect a preset.

### VNIR bands

The backplane's column is the IR (L) detector's. Bands below 1000 nm lie on
the VNIR (S) detector, whose columns match only approximately, so a ratio
there is only approximately column-matched. The record sets `vnirApproximate`
when the cube has such bands, and the description says so.

### Outputs

- **Ratio cube** (`… ratio cube`): float32, the input's bands, wavelengths and
  grid; dimensionless. Its description states the method, the blandness
  settings, and **how many columns fell back** (to neighbors, to the whole
  column, or had no data). Its `ratioCube` metadata record is kept when
  exporting to GeoTIFF or ENVI and holds the method, blandness name and
  thresholds, run length, Minimum Pixels, Neighbor Radius, the geometry file,
  the good bands used, and for every column `{strip, column, count, fallback,
  spectrum, std}`.
- **Denominators** (`… ratio denominators`): band 1 "Used as denominator" is 1
  where the pixel was in its column's denominator (0 where not, NaN with no
  backplane column); band 2 "Column denominator count" is the number of pixels
  pooled for the pixel's column (0 for *no data*). In the whole-column
  fallback the count includes pixels of the column that have missing data;
  the mean ignores them.

### Assessing uncertainty and quality

- **`std` is the population standard deviation (ddof = 0)** of the pixels
  pooled for the column's denominator, per band. It is a spread, not a standard
  error: the standard error of the denominator is `std / √count`.
- The relative uncertainty of a ratio pixel R = N / D, with numerator
  uncertainty σ_N (for example the cube's noise) and denominator D with
  σ_D = std / √count, is
  σ_R / R ≈ √((σ_N / N)² + (σ_D / D)²), treating the two as independent. The
  denominator term is not independent of the numerator for the pixels that are
  pooled into D; for a column with many pooled pixels it is small.
- A large `std`, a small `count`, or a *neighbors* or *whole column* fallback
  all mean a less trustworthy denominator. A *whole column* denominator, or a
  bland one with a loose limit, can contain real absorptions that then show
  as inverted features in the ratio. Check the "Used as denominator" image for
  where the pooled pixels are (they should not be only one part of the scene)
  and compare the denominators of neighboring columns.
- Blandness thresholds are heuristics (above). Assumptions: the artifacts are
  constant along a column, and the pooled pixels' real signal averages to a
  featureless spectrum.

### Reporting it

A methods section can say: "Each I/F spectrum was divided by the mean spectrum
of the bland pixels of its detector column (column from the DDR/DE
backplane's IR sample; blandness: <preset or Generic, with limits>, judged on
means of <run length> pixels in the column; at least <Minimum Pixels> pixels,
borrowing from columns within <Neighbor Radius> of the same strip, else the
whole column)", then state how many columns fell back, taken from the
description. Cite Viviano-Beck et al. (2014) when a preset is used.

## References

- Viviano-Beck, C. E., et al. (2014). Revised CRISM spectral parameters and
  summary products based on the currently detected mineral diversity on Mars.
  *Journal of Geophysical Research: Planets*, 119, 1403–1431.
