# Image processing tools

Varda's everyday image processing, in the **Processing** menu: spatial
filters, texture, color, class map tools, terrain and geometry. Each opens a
dialog with its settings; results join your project as new images.

Most spatial tools work **band by band**: choose **All bands** (a result with
all of the image's bands) or **One band**. While filtering, each no-data
pixel is filled with the value of the **nearest valid pixel**, so no-data
doesn't smear across the image; it's no-data again in the result. Next to a
large gap, the filter sees a flat copy of the gap's edge, so smoothing,
edges, texture and slopes within a window's width of a gap are biased. Treat
those pixels with care. Image edges are mirrored, unless a tool says
otherwise.

Results are stored as 32-bit floats (about 7 significant digits).

If you need something that isn't here, you can describe it and have your AI
build it: see [Kindle a Feature](kindle-a-feature.md).

## Filtering

Filters are under **Filtering → Spatial** (across the image, band by band)
and **Filtering → Spectral** (along each pixel's spectrum).

### Spatial

| Tool | Methods |
|---|---|
| **Smoothing** | Mean (box), Gaussian, Median (removes spikes and speckle, keeps edges), Bilateral and Total variation (edge-preserving) |
| **Sharpening** | Unsharp mask and Laplacian sharpen add detail back; High pass and Gaussian high pass return only the detail |
| **Edge Detection** | Sobel, Prewitt, Scharr, Roberts (gradient magnitude, band units per pixel), Laplacian, Laplacian of Gaussian, Canny (an edge map of 1s and 0s), Directional (the gradient toward a direction measured clockwise from the image's up, not from map north) |
| **Morphology** | Dilate, Erode, Opening, Closing, White and Black top-hat, Morphological gradient, with a square or disk structuring element |
| **Speckle Filters** | Lee, Kuan (set by the number of looks) and Frost (set by damping): smooth uniform areas, keep edges and texture |
| **Frequency Filter (FFT)** | Low, high or band pass in the Fourier domain, with Gaussian, Butterworth or ideal cutoffs (fractions of the Nyquist frequency) |
| **Custom Kernel** | Your own kernel, written as rows separated by `;`, e.g. `0 -1 0; -1 5 -1; 0 -1 0`. It's applied as written (correlation: the kernel isn't flipped) and, by default, divided by the sum of its weights |

### Spectral

The Iterative Outlier Voting Filter (IOVF; Phillips et al. 2024) removes
isolated spikes, judging each value against its neighbors in both space and
wavelength. It subtracts a smooth reference from each spectrum, runs a
Grubbs outlier test on every small window (rows × columns × bands) of what's
left, and replaces the values that enough windows flag, round after round.
A replaced value is the distance-weighted (Gaussian) average of its
neighbors that weren't flagged, with the weights renormalized over those
neighbors; with none left, it takes the reference value. Bands marked bad
keep their input values. Spectral filters added by plugins (a spectral smoothing filter, say) go here
too.

| Setting | Meaning | Range | Default |
|---|---|---|---|
| **Window Rows**, **Window Columns**, **Window Bands** | Size of the test window (odd) | 3–15 | 7, 5, 5 |
| **Stop Below** | Stop once a round replaces fewer values than this share of round 1's (0: run until nothing is flagged) | 0–50 % | 1 % |
| **Replaced Count Image** | Also add an image of how many bands were replaced at each pixel | On / off | On |

**Outputs:** the filtered cube (the input's bands and units) and, with
**Replaced Count Image** on, a one-band image named "*image* IOVF replaced"
whose band "Bands replaced" counts, at each pixel, the bands replaced in any
round (0 to the band count; NaN where the pixel is unusable). Both carry the
same `iovf` metadata (saved with the file):

| Key | Meaning |
|---|---|
| `replacedPerRound` | Values replaced in each round |
| `replacedTotal` | Their sum; a value replaced in two rounds counts twice |
| `voxelsReplaced` | Distinct values replaced (the sum of the count image) |
| `pixelsWithReplacements` | Pixels with at least one replaced value |
| `rounds` | Rounds run |
| `stopReason` | Why it stopped: "nothing flagged", "below stop fraction", "stalled", "counts cycling" or "round limit" (200 rounds) |

The filtered cube's description repeats the totals, the per-round counts
and the stop reason. When **Save to** is set in the dialog, the count image
is saved beside the result, with `_replaced` added to its file name.

## Spatial gradient (Derivatives)

**Spatial Gradient** (Processing → Derivatives) gives the magnitude of one
band's gradient (Sobel), in band units per pixel: bright where the band
changes quickly, such as at unit boundaries. It's the same as Edge
Detection's Sobel on one band, except that no-data isn't filled: a pixel
next to no-data is no-data too, and the image's edges are extended rather
than mirrored. Distances are in pixels, not meters; for slopes of a DTM, use
[Topography](#topography).

## Convolve to sensor bands (Spectral Parameters)

**Convolve to Sensor Bands** turns a cube with wavelengths into another
sensor's bands: each band is the mean of the cube's bands weighted by that
band's spectral response. Bad bands and no-data values are left out. A band
the cube covers less than 90% of (HiRISE BG below CRISM's first band, say)
is skipped, and the result's description lists it. A pixel missing more
than 10% of a band's response (from no-data) is no-data in that band.

Each band's **coverage**, the share of its response (area) that the cube's
good bands cover, is in the result's description (e.g. "coverage BG 93%,
RED 100%, NIR 100%") and its metadata (`sensorBands`: the sensor, the
coverage of every sensor band, used or skipped, as a fraction 0–1, the
90% threshold and the skipped bands). The metadata is saved with the
file.

Built in:

- **Landsat 8 OLI, Landsat 9 OLI-2, Sentinel-2A, -2B and -2C MSI:** the
  measured spectral responses published by NASA/USGS and ESA (via the
  pyspectral collection; see `resources/sensor_bands/README.md`).
- **MRO CTX:** the measured system passband (filter, optics and CCD) of
  Bell et al. (2013): 50% of peak at 515.5 and 704.7 nm, 1% at 498.2 and
  754.7 nm, effective wavelength 611 nm. That paper shows the curve as a
  figure rather than a table, so Varda builds it from those edges.
- **MRO HiRISE (BG, RED, NIR):** approximations from the half-maximum band
  edges of McEwen et al. (2007): flat bands from 400–580, 570–830 and
  790–1000 nm, with 20 nm ramps at the edges, as the measured curves are
  published only as figures.
- **Mars Odyssey THEMIS VIS:** approximations from the band centers of
  Christensen et al. (2004): Gaussians at 425, 540, 654, 749 and 860 nm,
  each 50 nm wide (FWHM).

The result's description says which kind each is. For another sensor, or a
curve of your own, choose **Custom bands** and give a response file: a CSV
with a header row, then wavelength (`wavelength_um` or `wavelength_nm` in
the header names the unit) and one column of response per band (lines
starting with `#` are notes). Custom bands can also be typed as center/FWHM
in nm: `Red 650/40; NIR 860/30` (Gaussian responses).

A use: **aligning a hyperspectral product to a camera.** A CRISM parameter
map has little texture to match against CTX, but CRISM reflectance
convolved to the CTX band looks like CTX. In a 3D Terrain workspace, add the
parameter map as an overlay, set **Match using** to the CTX-like scene and
click **Auto Align**: the alignment found for the scene moves the parameter
map (see [3D Terrain](3d-terrain.md)). Over Jezero, matching CRISM's own
best band against the CTX orthoimage lands 15 km off; the CTX-like scene
lands within about 50 m of the known offset (160 m west, 340 m north),
though with few tie points, so check the alignment and adjust it by hand
if needed. CTX's band (515-705 nm at half power) lies in CRISM's VNIR
detector, so use joined or VNIR data rather than an IR-only cube; note
that CRISM's MTRDR has no bands from 631 to 710 nm, so the scene misses
part of the CTX band. Varda keeps a band if at least 90% of its response
is covered, and the result's description gives how much was (e.g. "CTX
93%").

## Image math and masks

**Band Math** (Processing → Spectral Parameters) makes one band from an
expression: `R2300 / R2200` (the bands nearest 2300 and 2200 nm),
`(b10 - b4) / (b10 + b4)` (band numbers), comparisons and `where()`. It can
use other images on the same pixel grid by their place in the Image List:
`i3_b1` is band 1 of the 3rd image, so `b1 * (i3_b1 > 0)` keeps band 1
where image 3 is positive. The dialog lists which images those are, and
which band each name picked.

`R<nm>` picks the band (not a bad band) whose center is nearest, and is an
error if none is within 3 times the median band spacing or 25 nm, whichever
is larger. A pixel with no data in any band the expression uses is no data
in the result, as is any result that isn't a finite number (division by
zero, the log of a negative).

**Processing → Image Math:**

- **Image Arithmetic** combines the image with another image on the same
  grid, band by band, or with a number: add, subtract, multiply, divide,
  minimum or maximum. The second image has the same bands, or one band
  applied to every band. No data in either (and division by zero) is no
  data in the result.
- **Threshold to Mask** makes a mask from one band: 1 where its value is
  inside (or outside) a range, 0 elsewhere, no data where the band has
  none. Both ends of the range count as inside. The range starts at the
  band's minimum and maximum.
- **Apply Mask** applies a mask: keep the pixels where it's set (nonzero)
  and make the rest no data, keep where it **isn't** set (the inverse), or
  **multiply** by it (for a weight, or a 0/1 mask that should leave zeros).
  Where the mask has no data, so does the result. A mask with any number
  of bands is offered: one with the image's band count is applied band by
  band, any other's **first band** to every band (so a Shadow Mask, whose
  later bands are its Brightness and cos(i), is applied by its mask band).
  The mask may be on another pixel grid: any georeferenced mask that
  overlaps the image is resampled onto the image's grid by **nearest
  neighbor** (see [Images on other grids](#images-on-other-grids)); the
  description and the `maskResampling` metadata record it.

- **ROI to Mask** makes a mask from the workspace's ROIs: 1 inside the ROIs
  of the classes you tick, 0 elsewhere.

Masks combine with Image Arithmetic: **minimum** of two 0/1 masks is
"and", **maximum** is "or", and `1 - mask` in Band Math inverts one. Band
Math makes masks too (`b3 > 0.2` is 1 or 0).

### Images on other grids

A dropdown of images lists only the images that input can use. Under it, a
muted note names each image left out and why, for example
"Not listed: ctx_dem (different pixel grid: 6.0 m vs 18.0 m pixels; this
needs the same grid)", "(not georeferenced, can't be placed on this
image)", "(doesn't overlap this image)" or "(3 bands; this needs 1 or 30)".

**Resampled automatically** (any georeferenced image on the same body
that overlaps the image in map space, in any map projection; an image on
the same grid is used exactly as it is, with no resampling):

| Input | Resampling onto the image's grid |
| --- | --- |
| Apply Mask's **Mask**, a FATT map's **Exclude**, the ROI Manager's **From Threshold or Mask…** mask | Nearest neighbor (values stay as they are, e.g. 0 and 1) |
| Shadow Mask's **DEM** | **Average** when the DEM's pixels are finer than the image's (every DEM pixel under an image pixel counts equally); **bilinear** when they are coarser |

Varda reprojects and resamples with GDAL's warper
(`rasterio.warp.reproject`), reading only the part of the source under the
image. The source's no-data (its no-data value or masked pixels) is left
out of the resampling; image pixels outside the source, or with no source
data under them, are no data. The result's description says what was done
("ctx_dem resampled from 6.0 m to 18.0 m pixels (average)", "reprojected
and resampled" across map projections), and its metadata keeps the source,
the method, both pixel sizes and both coordinate systems. If you need
another method (cubic, a mode filter, a different target grid), resample
the input yourself first (e.g. GDAL's `gdalwarp`, or Varda's Resample) and
choose that image: an image on the same grid is never resampled again.

Two coordinate systems are the same when their geometry matches (the
projection and its parameters, the body's radii, the prime meridian and the
axes), whatever they're named, so the same grid written by GDAL and by the
Ames Stereo Pipeline is one grid. An image on a different body (a mask in
Earth's degrees over a Mars image, say) is never placed: the list says
"Earth and Mars are different bodies". When a body is unknown and the radii
differ, Varda can't tell and doesn't place it either. In degrees,
longitudes 0–360 and −180–180 name the same ground: an image at 257°E
overlaps one at −103°E, and is placed there by shifting its longitudes by
360°, with no resampling beyond what the grids need.

**Same grid only** (the same size, map projection and pixel positions):
Image Arithmetic's second image, Band Math's other images (`i3_b1`), PCA/
MNF weights, ROI zonal-statistic columns, overlays in the image workspaces
and drapes in 3D Terrain. These combine or compare values pixel for pixel,
and silently resampling science data first would change them. Resample an
image yourself when you mean to.

### Shadow Mask

**Processing → Image Math → Shadow Mask** marks where an image is in
shadow: 1 for shadow, 0 for lit ground, no data where it can't tell. Use it
as the **Exclude** mask of a [FATT detection map](fatt.md#shadows-and-dim-pixels),
with Apply Mask, or anywhere a 0/1 mask goes. **Method** chooses how:

- **Brightness** (the default): a pixel is shadow when its brightness is
  below a threshold. Brightness is the mean of the image's good bands
  between **Brightness From** and **Brightness To** (nm), each pixel's
  no-data bands left out. The range starts at 1280–1380 nm, the window of
  CRISM's IR albedo parameter R1330 (Viviano-Beck et al. 2014), a continuum
  region away from strong mineral absorptions, when the image has a good
  band there; otherwise at the image's whole wavelength range (every good
  band). For one band, set a range around it with no other band inside:
  when no band falls in the range, the band nearest its center is used.
  Without wavelengths, every good band is used. **Threshold** is a
  **Percentile of the scene** (**Percentile**, default 5%: the darkest 5%
  of the valid pixels) or an **Absolute value** in the image's data units
  (e.g. I/F; **Absolute Threshold**, default 0). A pixel is shadow when its
  brightness is strictly below the threshold.
- **Topographic (needs a DEM)**: shadow from the terrain and the sun.
  - **DEM**: a **single-band** elevation image **co-registered** with the
    image (multi-band images aren't offered; save the elevation band as its
    own image first). It may be on another grid, in any map projection, as
    long as it is georeferenced and overlaps the image: a 6 m CTX DEM under
    an 18 m CRISM image, say. Varda resamples it onto the image's grid
    first and computes everything there: **averaged** when the DEM's pixels
    are finer than the image's, **bilinear** when they are coarser (see
    [Images on other grids](#images-on-other-grids)); a DEM on the image's
    grid is used as it is. Averaging a finer DEM smooths it, so slopes
    (and self-shadow) are those of the image's pixels, gentler than the
    DEM's own steepest slopes, and relief narrower than an image pixel
    can't cast a shadow; this matches what one image pixel sees. If you need
    another resampling, reproject the DEM yourself onto the image's grid
    (e.g. `gdalwarp` with the image's extent and pixel size) and choose
    that. The image itself counts too when it has one band. Check the fit,
    e.g. by draping the image over the DEM in
    [3D Terrain](3d-terrain.md#overlays), whose **Auto Align** matches an
    image to a DEM. A misregistered DEM puts shadows in the wrong place.
  - **Z Factor** (default 1) multiplies elevations to put them in the pixel
    size's units (1000 for kilometers of elevation over meters of pixel
    size), as in Terrain. Since the DEM is used on the image's grid, an
    image in degrees (a geographic coordinate system) is refused; a DEM and
    image without georeferencing (on the same grid) count each pixel as one
    unit of elevation (the description says so).
  - **Sun Azimuth** (degrees clockwise from north) and **Sun Elevation**
    (degrees above the horizon, 90° − the incidence angle). Varda fills
    them in when it knows them: from the image's metadata (keys named
    `sun_azimuth` or `solar_azimuth`, `sun_elevation` or `solar_elevation`,
    or `incidence_angle`, in any case and with spaces or underscores), or,
    for the elevation of a CRISM product, 90° minus the mean "INA at
    areoid" over the image's pixels (the incidence angle to the reference
    surface; the DDR has no solar azimuth) from its DDR/DE file: found
    beside the image or beside the file it was made from (an IOVF result
    finds its I/F cube's DDR; see [CRISM ratio cubes: the geometry
    file](crism-ratio.md#the-geometry-file)), or chosen in **CRISM
    Geometry File** (empty means automatic). A chosen file comes before the
    metadata and is kept with the image (`crismIncidenceFile`) and the
    session; it must have an "INA at areoid" band and the image's rows ×
    columns (or the same map grid, covering the image), or the dialog says
    why it can't be used. A DDR found automatically that doesn't cover the
    image's pixels is skipped (the incidence is averaged over the image's
    own pixels, not the whole DDR). The elevation's source, recorded as
    `sunElevationSource`, names the DDR, the incidence range across the
    image and how the file was found (`override`, `beside the file`, `from
    source <path>`). Otherwise you must enter them, from the product's
    label or geometry; Run stays unavailable until you do. Check a metadata
    azimuth's convention: Varda takes it as clockwise from north.
  - **Self-shadow** where the local illumination cosine is at or below
    **Self-Shadow cos(i) ≤** (default 0, slopes facing away from the sun):
    cos(i) = cos(s) sin(e) + sin(s) cos(e) cos(azimuth − aspect), with
    slope s and aspect from the DEM by Horn's method (as in Terrain) and
    sun elevation e. Raise the threshold (say 0.1) to drop grazing-lit
    slopes too.
  - **Cast shadow** where other terrain hides the sun: from each pixel,
    Varda steps toward the sun one pixel at a time along the larger of the
    row and column directions (interpolating linearly across the other),
    and marks the pixel when the DEM rises strictly above the straight
    line to the sun. Distances come from the image's pixel size (the grid
    the DEM is resampled onto). Terrain beyond the image's edge, or the
    DEM's, can't cast a shadow, even when the DEM extends farther.
- **Both (union)**: shadow by either method.

**Buffer** (default 0, 0–100 px) grows the shadow by that many pixels in
every direction (a square). For a FATT map, a buffer of half the window,
3 px, also drops the windows that straddle a shadow's edge.

The result's bands: **Shadow mask** first, then **Brightness** (the mean
used, in data units) when brightness is used, then **cos(i)** (unitless,
−1 to 1) when the terrain is. cos(i) shows weakly lit slopes even where
they aren't shadowed; its no-data matches the DEM's. Where a method has no
data (no brightness, a gap in the DEM), the mask has none either, unless
the other method finds shadow there. The `shadowMask` metadata (saved with
the file) and the description record the method, the bands used, the
threshold actually used (a percentile's value in data units too), the DEM,
the sun angles and where each came from, the cos(i) threshold, the pixel
size and Z Factor, the DEM's resampling (`demResampling`: source, method,
both pixel sizes and coordinate systems; empty when it was on the grid), the buffer, and the pixels found by each rule, added by
the buffer, in shadow and with data.

### ROIs from a threshold or a mask

In the ROI Manager, **From Threshold or Mask…** makes a new ROI class
from the pixels where a band is inside (or outside) a range, as ENVI's
band threshold to ROI does, or where a mask image is set (its **first
band** nonzero, so a Shadow Mask's mask band). A mask on another grid is
offered when it is georeferenced and overlaps the image, and resampled
onto the image's grid by nearest neighbor (the dialog says so under the
mask; see [Images on other grids](#images-on-other-grids)); images it
can't use are named under the dropdown.
The range starts at the band's minimum and maximum, and the dialog counts
the pixels chosen as you change it. Each connected region becomes an ROI
of the class; regions smaller than **Smallest region** (4 pixels by
default) are left out, so a noisy threshold doesn't make thousands of
ROIs. Varda's ROIs are simple shapes, so a hole inside a region is filled;
to leave holes out, apply the mask itself (Apply Mask) instead.

## Bit masks (Statistics)

**Decode Bit Mask** reads images whose values are sets of bits, such as a
pipeline's per-frame exclusions (bit *i* set where frame *i* of a stack was
left out) or a quality band, band by band: the **number of bits set**, **one
bit** (1 where it's set), or **any of the bits** (1 where any is set; bits
written like `0, 3-5`). No-data stays no-data. It needs whole-number data.
Signed integers are read as their bits at the image's width: a 16-bit −1
is 16 bits set (bits 0–15).

For MISE's example products, the number of bits set in EXCLUDE reproduces
COUNT exactly, value for value: a check that the pipeline's two products
agree. One bit (frame 0, say) shows where that frame was left out.

## Texture and entropy (Statistics)

- **Local Statistics (Texture):** the mean, standard deviation, variance,
  minimum, maximum, data range or skewness in a window around each pixel.
- **Co-occurrence Texture (GLCM):** contrast, dissimilarity, homogeneity,
  second moment, entropy, correlation, mean or variance of the gray-level
  co-occurrence matrix in a window (Haralick et al. 1973), in one direction
  or averaged over four. The band is first quantized into **Gray Levels**
  (32 by default) spread over its 2–98% range.
- **Spatial Entropy:** the Shannon entropy (bits) of one band's values in a
  window around each pixel, after sorting the band into equal-width bins
  between its minimum and maximum. Textured areas score high, uniform areas
  low. The **Window** (pixels, 3–99, default 7) takes odd sizes only, so the
  recorded size is the one used.
- **Spectral Entropy:** the Shannon entropy (bits) of each pixel's spectrum,
  treated as a distribution over its bands (each band's share of the
  spectrum's sum), over its good bands (bad bands are left out). A flat
  spectrum scores the most, log₂ of the number of good bands; a spectrum
  dominated by a few bands scores low. Negative values
  count as zero, so use it on data that can't be negative, such as
  reflectance or radiance.

## Color (Transforms)

These work on three bands, the image's display bands by default.

- **Color Transform:** RGB to HSV or HLS, and back. Going from RGB, each
  band is first stretched to 0–1 over its 2–98% range, as the display does;
  going back expects bands in 0–1, such as the forward transform's output.
- **Decorrelation Stretch:** exaggerates the color differences between
  correlated bands (Gillespie et al. 1986); each band keeps its mean and
  spread.
- **Saturation Stretch:** boosts color saturation: the three bands are
  scaled together to 0–1, the saturation is histogram-equalized, and the
  result is converted back to RGB (0–1). **Stretch Brightness Too** also
  stretches the brightness (2–98%).

These change values for the sake of color: use their results for figures
and for finding areas to look at, not for measurements.

## Class maps (Classification)

- **Density Slice:** turns one band into classes of value ranges, either
  equal intervals (between **Low** and **High**, or the band's 2–98% range
  if High isn't above Low; values beyond go to the end classes) or
  quantiles (equal pixel counts). The class names give each range.
- **Majority/Minority Filter:** replaces each pixel with the most (or least)
  common class around it, cleaning speckle out of a classification.
  Unclassified pixels stay so, and a tie keeps the pixel's own class.
- **Sieve Classes:** removes groups smaller than a minimum size, leaving
  them unclassified or giving them the class of the nearest classified
  pixel.
- **Clump Classes:** fills holes and specks inside class regions without
  eating solid regions of other classes.

These work on any class map: a classification result or a density slice.

## Topography

**Terrain** works from an elevation band (a DTM). Its outputs are slope
(degrees), aspect (the compass direction a slope faces, downhill, in degrees
clockwise from north), hillshade (0–1), curvature (positive is convex) and
roughness (the mean absolute height difference to the 8 neighbors).
Distances come from the image's pixel size, so elevations and pixel size
must be in the same units. Images in degrees (a geographic coordinate
system) are refused; use a projected one (such as meters per pixel). An
image with no georeferencing is treated as having a pixel size of 1, so its
slopes are per pixel, not per meter; the result's description and units say
so (slope in "degrees (rise per pixel)", curvature per pixel²). Use the
**Z Factor** when elevations are in other units (for example, 1000 for
kilometers of elevation over meters of pixel size); roughness is in
elevation units times the Z Factor, and its units say so (e.g. "elevation
units × 1000 (Z Factor)").

## Geometry

- **Resample (Resize):** changes the number of pixels by a scale factor,
  with nearest neighbor (use it for class maps), bilinear or cubic
  interpolation. Each new pixel is sampled at its center. When shrinking,
  bilinear and cubic first smooth each band with a Gaussian of σ = (old
  size / new size − 1) / 2 old pixels along each shrunk side (the
  anti-aliasing of scikit-image's `resize`), so fine detail doesn't alias;
  nearest neighbor never smooths (it picks one old value, so it can alias).
  The pixel counts are rounded, so the actual factors along rows and
  columns can differ slightly from the one you typed; the result's
  description records them (and the σ used).
- **Rotate / Flip:** quarter turns and mirrors.
- **Subset:** keeps a range of rows and columns and a list of bands. Its
  preview shows the image with a box: drag it over the part to keep, or
  resize it by its corners. Below, the box's mean spectrum shows the bands
  to keep as shaded ranges: drag their edges, **Add Range** for another,
  double-click one to remove it, **Keep All Bands** to start over. The
  rows, columns and bands (`1-100, 150-200`) can also be typed, and box
  and ranges follow.

- **Crop to ROI:** crops to the ROIs of the classes you tick. Pixels
  outside them can become no-data, so later processing sees only the ROIs.

These keep the georeferencing right, so pixels stay at their place on the
map, and carry the wavelengths, bad bands and FWHM along. Rotate / Flip,
Subset and Crop to ROI copy values unchanged.

### Working on part of an image

To run anything, an analysis or a whole workspace such as the Spectral
Hourglass, on part of an image, crop it first:

- **Crop tool:** turn on **Crop** in an image's toolbar and drag a box. A
  cropped copy is added to the image list.
- **Crop to ROI:** draw an ROI, then run **Processing → Geometry → Crop to
  ROI**.

Then choose the crop in the analysis dialog, or open a new workspace for it.

## Reporting your results

What to state in a methods section, what the outputs mean, and what to
cite. See also [Reporting results from Varda](reporting-results.md) for what
applies to every process.

### What to state

- The tool and method, and every setting. Each result records them (see
  [Reporting results from Varda](reporting-results.md)); the tables below
  give the defaults and the details the dialogs don't show.
- The band or bands it ran on, and for chains (a mask, then a filter, then
  a ratio), every step in order.
- That no-data was filled with the nearest valid value while filtering, if
  your image had gaps, and how you treated the pixels near them.

**Spatial filters** (band by band; edges mirrored; no-data filled with the
nearest valid value; output in the band's units unless noted):

| Tool / method | As implemented | Defaults |
|---|---|---|
| Smoothing: Mean, Median | Box mean or median over a Size × Size square | Method Gaussian; Size 3 px |
| Smoothing: Gaussian | Gaussian of standard deviation Sigma, cut off at 4 Sigma | Sigma 1 px |
| Smoothing: Bilateral | scikit-image `denoise_bilateral` on the band scaled to mean 0, standard deviation 1: spatial σ = Sigma, value σ = 1 standard deviation of the band, window max(5, 2⌈3 Sigma⌉ + 1) px | Sigma 1 px |
| Smoothing: Total variation | Chambolle's algorithm (scikit-image `denoise_tv_chambolle`) on the scaled band, weight = Strength | Strength 0.3 |
| Sharpening | Unsharp mask: x + Amount (x − Gaussian); Laplacian sharpen: x − Amount ∇²x (5-point Laplacian); High pass: x − box mean; Gaussian high pass: x − Gaussian | Unsharp mask; Sigma 1 px; Size 3 px; Amount 1 |
| Edges: Sobel, Prewitt | Gradient magnitude, kernels divided by 8 and 6 | Method Sobel. Output: band units per pixel |
| Edges: Scharr, Roberts | scikit-image `scharr` (divided by √2) and `roberts` | Output: band units per pixel |
| Edges: Laplacian, LoG | 5-point Laplacian; Laplacian of a Gaussian of σ = Sigma | Sigma 1.5 px. Output: band units per pixel² |
| Edges: Canny | scikit-image `canny` on the scaled band (mean 0, standard deviation 1), Gaussian σ = Sigma, scikit-image's default thresholds (0.1 and 0.2) | Sigma 1.5 px. Output: 1 edge, 0 not |
| Edges: Directional | Sobel gradient (÷ 8) toward Direction, clockwise from the image's up | 0°. Output: band units per pixel |
| Morphology | Grayscale (flat) morphology, square or disk of width Size | Opening; Disk; 3 px |
| Speckle: Lee, Kuan | x̂ = m + w (x − m) in the window; w = 1 − Cu²/Ci² (Kuan: divided by 1 + Cu²), clipped to 0–1; Ci² = local variance / m²; Cu² = 1 / Looks | Lee; 5 px; Looks 1 |
| Speckle: Frost | Window mean weighted by exp(−Damping · Ci² · distance in pixels) | 5 px; Damping 1 |
| Frequency (FFT) | Band mirrored to twice its size, then a radial filter; f and the cutoff fc are fractions of Nyquist (1 = 2 pixels per cycle). Gaussian: exp(−½ (f/fc)²); Butterworth (order 2): 1 / (1 + (f/fc)⁴); Ideal: 1 for f ≤ fc. High pass = 1 − low pass; band pass = low pass at Upper Cutoff × high pass at Cutoff | Low pass; Gaussian; Cutoff 0.25; Upper Cutoff 0.5 |
| Custom Kernel | Correlation with the kernel as written; divided by its sum if Normalize is on and the sum isn't 0 | `1 2 1; 2 4 2; 1 2 1`; Normalize on |

Sizes are made odd (a 4 becomes 5).

**Other tools:**

| Tool | As implemented | Defaults | Output |
|---|---|---|---|
| IOVF | Reference per spectrum: 19-band running median, then Savitzky–Golay (9 bands, cubic). Two-sided Grubbs test in every window of the residual, α = 0.125 (0.05 for windows cut below 45 values at the cube's edges); each flag is a vote, and values with at least as many votes as the window's smallest side are replaced by a Gaussian-weighted mean of their unflagged, valid neighbors (weights renormalized over them; the reference value when none is left). Later rounds test only around the last round's replacements | Window 7 rows × 5 columns × 5 bands; Stop Below 1% of round 1 (it also stops when nothing is flagged, when the counts cycle or stall, or after 200 rounds) | Input units |
| Spatial Gradient | Sobel magnitude (÷ 8); edges extended; no-data not filled | Band 1 | Band units per pixel |
| Convolve to Sensor Bands | Each cube band weighted by the response at its center times its share of the wavelength axis; bad bands and no-data left out and the weights renormalized per pixel; a band is kept if the cube covers ≥ 90% of its response (area), a pixel if ≥ 90% is present | Sensor MRO CTX | Input units; each band's wavelength is its response-weighted mean, its FWHM the width of a box of the same area and peak |
| Band Math | Expression in 64-bit floats; `R<nm>` as above | `R2300 / R2200` | As the expression |
| Image Arithmetic | Band by band | Add; Number 1 | As the operation |
| Threshold to Mask | 1 where Low ≤ value ≤ High (or the opposite for outside) | Range: the band's minimum and maximum | 0 / 1 |
| Shadow Mask | Brightness: mean of the good bands in the range (no-data bands left out) < threshold; a percentile is numpy's `nanpercentile` (linear interpolation) over the valid pixels. Topographic: cos(i) ≤ threshold (slope and aspect by Horn's method, edges extended, DEM gaps filled with the nearest value and no data in the result), or cast shadow by a ray march toward the sun in one-pixel steps along the major axis, linear interpolation across it, shadow where the DEM is strictly above the ray. A DEM on another grid is first resampled onto the image's (GDAL: average when finer, bilinear when coarser). Buffer: a (2N + 1)-pixel square maximum filter | Brightness; 1280–1380 nm (else all good bands); 5th percentile; absolute 0; Z Factor 1; cos(i) ≤ 0; buffer 0 | Mask 0 / 1; brightness in data units; cos(i) unitless |
| Decode Bit Mask | Each value's bits at the image's own width; signed integers read as unsigned of the same width (16-bit −1 is 16 bits set) | Number of bits set; Bits `0` | Count, or 0 / 1 |
| Local Statistics | Moments over the window (variance divided by n, not n − 1) | Standard deviation; 7 px; One band | Band units (variance: squared; skewness: none) |
| GLCM | Symmetric co-occurrence of pairs inside the window (windows are cut at the image's edges); gray levels over the band's 2–98% range | Contrast; 7 px; 32 levels; all directions; distance 1 px; One band | Gray levels: contrast, variance in levels²; dissimilarity, mean in levels; entropy in nats (natural log); second moment (Σp², not its square root), homogeneity, correlation unitless |
| Spatial Entropy | Histogram of the window's valid pixels in equal-width bins over the band's whole valid range | Band 1; 7 px; 64 bins | Bits; at most log₂ of the smaller of the bins and the window's pixel count (5.6 bits for 7 × 7) |
| Spectral Entropy | −Σ p log₂ p over the good bands, with p = band value / spectrum sum, negatives as 0 | None | Bits, 0 to log₂(good bands) |
| Color Transform | matplotlib's HSV conversion (HLS from it); forward stretches each band 2–98% to 0–1 | RGB to HSV | 0–1; hue as a fraction of a turn |
| Decorrelation Stretch | Principal components of the three bands' covariance, each scaled to unit variance, rotated back, then scaled to each band's standard deviation and mean | Display bands | Band units |
| Saturation Stretch | Three bands scaled together min–max to 0–1, saturation equalized (scikit-image `equalize_hist`) | Stretch Brightness Too off | 0–1 |
| Density Slice | Class k holds Low_k ≤ value < High_k | 5 classes; equal intervals; Low = High = 0 (the 2–98% range) | Class map |
| Majority/Minority | Class counts in the window (unclassified pixels and pixels past the edge don't count); the center counts Center Weight times | Majority; 3 px; weight 1 | Class map |
| Sieve | Connected regions of a class smaller than Minimum Size removed | 4 px; 8-connected; Fill off | Class map |
| Clump | Each class closed with a square; it can take only unclassified pixels and pixels outside other classes' solid regions (their opening with the same square) | 3 px | Class map |
| Terrain | Gradients by Horn's method (Sobel ÷ 8 pixel size), edges extended; aspect = downhill direction, clockwise from north, none where flat; hillshade = cos Z cos S + sin Z sin S cos(azimuth − aspect), with Z the sun's zenith angle (90° − elevation) and S the slope, clipped to 0–1, no cast shadows; curvature = −(∂²z/∂x² + ∂²z/∂y²) (5-point), none on the image's edge pixels; roughness = mean absolute difference to the 8 neighbors | Slope; band 1; Z Factor 1; sun azimuth 315°, elevation 45° | Slope, aspect in degrees; curvature per pixel-size unit (per meter for meters), not × 100 as some GIS tools give it; roughness in elevation × Z Factor |
| Resample | scipy `zoom`, pixels as areas (corners stay put); nearest, bilinear, or cubic spline; edges extended; when shrinking with bilinear or cubic, a Gaussian prefilter of σ = (old / new pixel count − 1) / 2 per axis (as scikit-image `resize`'s anti-aliasing); the pixel size becomes old × (old pixel count / new) | Factor 0.5; bilinear | Band units |

### Uncertainty and quality

Most of these tools report no uncertainty (IOVF's replacement counts and
the sensor-band coverage are the exceptions, below). What you can say, and
how to get a number:

- **Gaps.** Within a window's width of no-data, filters, texture, slopes and
  resampling see the nearest valid value repeated. Exclude those pixels (for
  instance, ROI to Mask or a buffer around the gap) from anything you
  measure.
- **Filters change the data.** Smoothing, sharpening and speckle filters
  trade noise for resolution; report the filter and its size or sigma, and
  compare a uniform area's standard deviation (ROI statistics) before and
  after if you claim a noise reduction. The Lee and Kuan noise model
  (variance 1 / Looks) is the one for intensity (power) images; amplitude
  images have less speckle per look, so the true number of looks is a poor
  setting for them.
- **Edge units.** Sobel, Prewitt, Scharr, Roberts, Directional and Spatial
  Gradient are in band units per pixel. (Varda 0.9.0 and earlier gave Scharr √2
  times that.) Bilateral, total variation and Canny are
  set relative to the band's standard deviation, so the same setting acts
  differently on bands with different spread.
- **IOVF.** Report the window, the stop setting and the `iovf` metadata
  (values replaced per round and in total, pixels affected, rounds and why
  it stopped). The **Replaced Count Image** maps where values were
  replaced; clusters of replacements in one area or one band range point
  to real spectral features being taken for spikes, so inspect those
  spectra before trusting the filtered values there. To see by how much
  each value changed, subtract the result from the input (Image
  Arithmetic). The window and the stop setting change what's replaced, so
  report both. Bad bands, and a pixel's no-data bands,
  are filled along the spectrum before filtering (so filled values take
  part in the windows); in the result, bad bands get their input values
  back and stay marked bad, while a pixel's filled no-data bands stay
  filled.
- **Convolve to Sensor Bands.** The built-in HiRISE and THEMIS VIS bands are
  approximations (the result's description says so), and CTX is rebuilt
  from published edges: fine for a scene to align, not for comparing
  values with the real instrument. For that, give the instrument's measured
  response file. A band can be kept with up to 10% of its response
  missing; its coverage (in the description and the `sensorBands`
  metadata) says how much of it the cube covers. Report it for any band
  below 100%: the missing part of the response is left out and the rest
  renormalized, so the band is biased toward the cube's side of it when
  the spectrum slopes across it.
- **Band Math and Image Arithmetic** don't propagate errors. For a ratio
  R = A / B with independent noise, (σR / R)² = (σA / A)² + (σB / B)². Take
  σA and σB from the standard deviation of each band over a uniform area
  (ROI statistics); for the mean ratio of an ROI, divide each by √(number of
  pixels). Neighboring bands' noise is often correlated, which this formula
  ignores. Check which band each `R<nm>` picked (the dialog lists them) and
  report those wavelengths, not the ones you typed.
- **Texture and entropy describe the scene, not errors.** A local standard
  deviation is texture plus noise, not a measurement uncertainty. GLCM and
  Spatial Entropy values depend on the gray levels or bins and on each
  band's own range, so compare them only between results made with the
  same settings on the same band. Spectral Entropy depends on the number of
  good bands and changes if you add an offset to the data.
- **Color tools** are visual products (see above): report them as
  enhancements in figure captions.
- **Class map tools** don't report how many pixels they changed. To count
  them, make a Band Math band such as `b1 != i2_b1` on the original map
  (image 2 being the cleaned map): 1 where the class changed, 0 where it
  didn't, no data where either is unclassified. Density slice class names
  round the range edges to 3 significant figures; set **Low** and **High**
  yourself if you need exact edges in your record.
- **Terrain inherits the DEM's error** (see [Reporting results from
  Varda](reporting-results.md)). Slope error grows with the DEM's vertical
  error σz over the pixel size: with independent errors in each pixel,
  Horn's method gives each gradient component an error of about 0.43 σz /
  pixel size (√12 / 8). Curvature amplifies noise more, about 4.5 σz /
  pixel size² (√20) per pixel. On flat ground, noise alone gives a
  roughness of about 1.13 σz. Report the DEM's stated vertical precision
  and pixel size with these products.
- **Shadow Mask is a model, not a measurement.** Brightness confuses dark
  materials (basalt, dark sand) with shadow, and a
  percentile always flags that share of the scene, shadows or not: look
  at the Brightness band and its histogram before trusting it, and prefer
  an absolute threshold you can state. The topographic method is only as
  good as the DEM: a shadow narrower than a few DEM pixels, or cast by
  relief within the DEM's vertical error, is missed or misplaced, and any
  misregistration between the DEM and the image moves every shadow. A DEM
  finer than the image is averaged onto the image's pixels first, which
  smooths slopes; one coarser than the image is interpolated, which adds no
  detail (shadow edges are then only as sharp as the DEM). Cast
  shadow edges are approximate at the scale of a pixel (the ray is sampled
  once per pixel, and a pixel is either shadow or not, though its footprint
  can be part lit). The sun is a point here, so the penumbra (the sun's
  disk is about 0.35° across at Mars, 0.53° at Earth) isn't modeled, and
  shadowed ground isn't dark: diffuse light from the sky and light
  scattered from nearby slopes still reach it. For a margin, buffer the
  mask, and report the method, thresholds, sun angles (and their source),
  DEM and its resampling, buffer and pixel counts from the `shadowMask`
  metadata.
- **Resample** with bilinear or cubic interpolation changes values (cubic
  can overshoot), and when shrinking also smooths them (the anti-aliasing
  Gaussian above: report its σ); nearest neighbor keeps them but moves each
  by up to half an old pixel. The pixel counts are rounded, so the real
  scale can differ slightly from the factor you typed: report the actual
  factors from the result's description, or the new pixel size. Rotate /
  Flip, Subset and Crop to ROI copy values exactly (as 32-bit floats; whole
  numbers beyond 16,777,216 lose precision).

### References

- IOVF: Phillips, M. S., Murchie, S. L., Seelos, F. P., Hancock, K. M.,
  Selby, C., Poffenbarger, R. T., Stephens, D. C., & Kawamura, M. (2024). A
  first look at CRISM hyperspectral mapping mosaicked data: results from
  Mawrth Vallis. *Icarus*, 419, 115712.
  https://doi.org/10.1016/j.icarus.2023.115712 (section 2.2 describes the
  filter, there called an iterative kernel filter).
- Grubbs test: Grubbs, F. E. (1969). Procedures for detecting outlying
  observations in samples. *Technometrics*, 11(1), 1–21.
- Savitzky–Golay smoothing: Savitzky, A., & Golay, M. J. E. (1964).
  Smoothing and differentiation of data by simplified least squares
  procedures. *Analytical Chemistry*, 36(8), 1627–1639.
- Bilateral filter: Tomasi, C., & Manduchi, R. (1998). Bilateral filtering
  for gray and color images. *Proc. ICCV*, 839–846.
- Total variation: Rudin, L. I., Osher, S., & Fatemi, E. (1992). Nonlinear
  total variation based noise removal algorithms. *Physica D*, 60(1–4),
  259–268. Chambolle, A. (2004). An algorithm for total variation
  minimization and applications. *Journal of Mathematical Imaging and
  Vision*, 20(1–2), 89–97.
- Laplacian of Gaussian: Marr, D., & Hildreth, E. (1980). Theory of edge
  detection. *Proceedings of the Royal Society of London B*, 207(1167),
  187–217.
- Canny: Canny, J. (1986). A computational approach to edge detection.
  *IEEE Transactions on Pattern Analysis and Machine Intelligence*, 8(6),
  679–698.
- Lee filter: Lee, J.-S. (1980). Digital image enhancement and noise
  filtering by use of local statistics. *IEEE Transactions on Pattern
  Analysis and Machine Intelligence*, 2(2), 165–168.
- Kuan filter: Kuan, D. T., Sawchuk, A. A., Strand, T. C., & Chavel, P.
  (1985). Adaptive noise smoothing filter for images with signal-dependent
  noise. *IEEE Transactions on Pattern Analysis and Machine Intelligence*,
  7(2), 165–177.
- Frost filter: Frost, V. S., Stiles, J. A., Shanmugan, K. S., & Holtzman,
  J. C. (1982). A model for radar images and its application to adaptive
  digital filtering of multiplicative noise. *IEEE Transactions on Pattern
  Analysis and Machine Intelligence*, 4(2), 157–166.
- CTX passband: Bell, J. F., III, et al. (2013). Calibration and
  performance of the Mars Reconnaissance Orbiter Context Camera (CTX).
  *Mars*, 8, 1–14.
- HiRISE bands: McEwen, A. S., et al. (2007). Mars Reconnaissance
  Orbiter's High Resolution Imaging Science Experiment (HiRISE). *Journal
  of Geophysical Research: Planets*, 112, E05S02.
- THEMIS VIS bands: Christensen, P. R., et al. (2004). The Thermal Emission
  Imaging System (THEMIS) for the Mars 2001 Odyssey mission. *Space Science
  Reviews*, 110(1–2), 85–130.
- GLCM texture: Haralick, R. M., Shanmugam, K., & Dinstein, I. (1973).
  Textural features for image classification. *IEEE Transactions on
  Systems, Man, and Cybernetics*, SMC-3(6), 610–621.
- Entropy: Shannon, C. E. (1948). A mathematical theory of communication.
  *Bell System Technical Journal*, 27(3), 379–423.
- Decorrelation stretch: Gillespie, A. R., Kahle, A. B., & Walker, R. E.
  (1986). Color enhancement of highly correlated images. I. Decorrelation
  and HSI contrast stretches. *Remote Sensing of Environment*, 20(3),
  209–235.
- Slope, aspect and hillshade, and the Shadow Mask's slopes: Horn, B. K.
  P. (1981). Hill shading and the reflectance map. *Proceedings of the
  IEEE*, 69(1), 14–47.
- Shadow Mask's default brightness window (R1330, IR albedo): Viviano-Beck,
  C. E., et al. (2014). Revised CRISM spectral parameters and summary
  products based on the currently detected mineral diversity on Mars.
  *Journal of Geophysical Research: Planets*, 119(6), 1403–1431.
- Filters, morphology and interpolation (scipy) and the scikit-image
  methods: Virtanen, P., et al. (2020). SciPy 1.0: fundamental algorithms
  for scientific computing in Python. *Nature Methods*, 17(3), 261–272.
  van der Walt, S., et al. (2014). scikit-image: image processing in
  Python. *PeerJ*, 2, e453.
