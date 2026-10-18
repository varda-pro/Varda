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
ones use 1000–2600 nm, the range the 1.9, 2.3 and 2.5 µm parameters span.

**Edit them to suit your scene.** Choosing a preset fills **Preset Limits**
with its maxima and slope limit as text, for example
`OLINDEX3 ≤ 0.02, LCPINDEX2 ≤ 0.02, HCPINDEX2 ≤ 0.02; slope ≤ 0.1 /µm over
1000–1700 nm`. Change the numbers (`≤` or `<=`; the slope window may be
changed too) and the run uses what is written; the text is filled again when
you choose another preset. Every parameter of the preset must appear once and
no others; a malformed entry is refused with a message saying what is wrong.
The limits actually used are recorded in the ratio cube's `thresholds`.
Report any value you change. **Generic Maximum Depth and Slope** do not affect
a preset, and Preset Limits does not affect Generic.

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
  where the pixel was pooled into at least one column's denominator, and 0
  otherwise (NaN with no backplane column). That is the pixel's own column's
  bland pixels; the bland pixels another column borrowed (*neighbors*, so
  they are 1 for that column too); or every pixel of the column in the
  *whole column* fallback, bland or not. A column that borrowed has 0 in its
  own pixels unless they were bland. Band 2 "Column denominator count" is the
  number of pixels pooled for the pixel's column (0 for *no data*); in the
  whole-column fallback it includes pixels of the column that have missing
  data, which the mean ignores.

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
- **Ratio Plots** (right, scrolls when the window is short): the box size, the
  column lock, and three plots on one wavelength axis: **Numerator**,
  **Denominator** (with its blandness readout under it) and **Ratio**. Each new
  pair replaces the curves and refits the Y ranges. The Ratio plot's sidebar
  overlays library spectra.
- **ROI Manager**, **Points Manager** and **ROI Plots** sit folded on the right
  edge, and the render settings and histograms of both views on the bottom
  edge: click their tabs to open them.

The dialog picks a browse image (the one whose name contains `_SR`, else the
first image) and an I/F image (one with a geometry file found, else the next
image); change either.

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
   pixel; its size is **Box Width** and **Box Height** (pixels, 1 to 99,
   default 5 by 5, the same for both boxes).
2. **Ctrl/⌘+right-click** the denominator box (same size).
3. The three plots show the **mean spectrum of each box** measured on the I/F
   cube, and their ratio (numerator mean divided by denominator mean, band by
   band, dimensionless). Plain clicks and drags pan and zoom as usual.
4. Click a new numerator and everything updates: the denominator stays, or
   follows the column (below). **Esc** clears both boxes.
5. **S** saves the pair as two ROIs (rectangles named `Ratio N numerator` and
   `Ratio N denominator`, in the I/F cube's pixels) and makes the denominator
   ROI the ratio reference in the ROI Manager. **P** saves the numerator's
   center pixel as a point in the Points Manager. Both names end with the
   verdict, for example `Ratio 3 numerator (denominator ✓ Generic)`: the mark
   and the check that was selected when you saved.

### Column lock

Sensor artifacts are fixed per detector column, so a denominator is only a good
reference if it lies in the same column as the numerator (see the top of this
page). With **Lock denominator to the numerator's sensor column** on (the
default when a geometry file is found), the right-clicked denominator is put on
the numerator's sensor column in the clicked row, and the denominator then
**follows**: each new numerator moves it into the new numerator's column,
keeping its row. The column comes from the I/F cube's DDR or DE geometry file
(found next to the cube, as for the Ratio Cube analysis) and strips are kept
apart.

Two cases where the lock cannot be met are said, never silent:

- **The denominator's row has no pixels in the numerator's column** (the
  column's strip does not reach that row, or the box sits on no-data): the
  denominator is **kept where it is** and the view you clicked shows
  "Denominator left in place: …" for a few seconds. That ratio is not
  column-matched; move the denominator to a row the column reaches.
- **No geometry file for the I/F cube**: the lock is unavailable (the check box
  is disabled and unchecked) and the status bar says "Column lock unavailable".
  Ratioing still works; the denominator is simply where you click, so the
  ratio keeps whatever column-dependent artifacts the two columns differ by.

### Blandness readout

Under the Denominator plot, the readout tests the **denominator box's mean
spectrum**: is it featureless enough to divide by? Any feature in it appears
inverted at the same wavelength in the ratio, where it could be mistaken for
(or hide) a feature of the numerator.

- **Generic** (default): the deepest absorption of the continuum-removed
  spectrum must be at most 0.02 and the absolute relative slope at most 0.10
  per µm. These are the Ratio Cube's Generic checks, described above.
- **A target preset** (Plagioclase, Olivine, Fe/Mg phyllosilicates, Al
  phyllosilicates, Carbonates, Sulfates, Hydrated (any)): every spectral
  parameter of the preset must be at most its maximum, and the slope at most
  0.10 per µm over the preset's window. The parameters, maxima and windows are
  in the preset table above. Presets need **HyPyRameter**, an
  [optional feature](optional-features.md); without it a preset says so in
  the readout and Generic still works.

Each row shows a check, its **value**, its **limit** (≤) and **✓** (within the
limit) or **✗**; the overall verdict is ✓ only if every row is. A preset may be
red where Generic is green, because it asks about the particular bands of its
target.

**Why a preset checks more than its target.** The aim is a denominator free of
every feature that could pass for, or cover, the target in the ratio, not only
the target's own:

- *Plagioclase*: a ratio meant to bring out plagioclase is easily spoiled by
  mafic absorption near 1 µm in the denominator, so it checks both pyroxene
  indices (LCPINDEX2, HCPINDEX2) and the olivine index OLINDEX3.
- *Olivine* checks OLINDEX3 and, in addition, the pyroxene indices, because
  pyroxene bands overlap the 1 µm region: a pyroxene-bearing denominator would
  put an inverted pyroxene feature into an olivine ratio.
- *Fe/Mg phyllosilicates, Al phyllosilicates and Hydrated (any)* check
  **BD1900r2** (the 1.9 µm H₂O band) together with the metal-OH parameters
  (D2300, MIN2250, MIN2295_2480): water in the denominator produces an
  inverted 1.9 µm band that reads as a loss of hydration in the numerator, and
  one hydrated mineral's metal-OH band in the denominator would read as an
  absence of another's.
- *Carbonates* check BDCARB with both metal-OH/carbonate minima
  (MIN2295_2480, MIN2345_2537), since the carbonate bands sit among the
  metal-OH ones.
- *Sulfates* add BD1900r2 to SINDEX2, because many sulfates are hydrated and a
  hydrated denominator would imprint the 1.9 µm band.

**Limits are editable**: double-click a value in the Limit column to change it
(a number, at least 0; an invalid entry is refused and the old value kept).
Choosing a different check, or Generic, puts the defaults back. The verdict
suffix in saved names records only the check's name, so **report any limit you
changed**. The limits are conservative starting points, not validated
thresholds (see the Ratio Cube section).

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

### Reporting it

A methods section can say: "Spectra were the means of <width> × <height> pixel
boxes of the <I/F cube>, ratioed band by band to a denominator box in the same
sensor column (column from the <DDR/DE> backplane; lock on / off), whose
spectrum was judged bland by <Generic or preset> (limits: <list; note any
changes>), with boxes chosen on <browse image> linked by <pixel / map
location>." Cite Viviano-Beck et al. (2014) when a preset is used. The session
keeps the link, box size, lock and chosen check; the saved ROIs and points
record where each pair was.

## References

- Viviano-Beck, C. E., et al. (2014). Revised CRISM spectral parameters and
  summary products based on the currently detected mineral diversity on Mars.
  *Journal of Geophysical Research: Planets*, 119, 1403–1431.
