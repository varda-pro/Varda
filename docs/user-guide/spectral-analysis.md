# Spectral analysis tools

Tools that work along each pixel's spectrum: **Continuum Removal**
(**Processing → Spectral Parameters**), and **Spectral Derivative** and
**Spectral Slope** (**Processing → Derivatives**). Each makes a new image
over the same pixels and georeferencing.

What they share:

- **Wavelengths.** Varda converts the image's wavelengths to nm (from µm or
  cm⁻¹) before computing, so every "per nm" below is per nanometer whatever
  unit the image uses. Continuum Removal and Spectral Slope need numeric
  wavelengths with units; Spectral Derivative falls back to band numbers.
  When a file doesn't declare its wavelength unit, Varda assumes µm for
  values below 100 and nm otherwise, and says so in the image's tooltip in
  the image list and under **Wavelength unit** in **Image Properties**
  ("Wavelength units not declared; assuming nm from the values
  (400–2500)"). Set the unit there if the assumption is wrong, and report
  the unit you used.
- **Bad bands.** All three tools leave out the bands marked bad in the
  image (the ENVI `bbl` list, editable in **Image Properties**).
- **Missing values.** No-data values in the good bands are filled by linear
  interpolation along each pixel's spectrum (the nearest value past the
  first or last good band). A pixel missing more than half of its good bands
  is no data in the result.

For band ratios, band depths and named spectral parameters, use **Band
Math** (see [Image processing](image-processing.md#image-math-and-masks))
and **Band Parameters (HyPyRameter)** (see [Optional
features](optional-features.md#band-parameters-hypyrameter)).

## Continuum Removal

Divides each spectrum by its continuum, so absorption bands show as dips
below 1. The continuum is the **upper convex hull** of the spectrum within a
wavelength window: straight lines (in reflectance against wavelength in nm)
between the spectrum's local high points, from the first band in the window
to the last.

| Setting | Meaning | Default |
|---|---|---|
| **Start Wavelength** | Start of the window, nm | 1000 |
| **End Wavelength** | End of the window, nm | 2500 |

The window is inclusive at both ends, and the two values can be given in
either order. At least three good bands must fall inside it.

**Output:** one band per good band in the window (bands outside it, and bad
bands, are dropped), with the original wavelengths, band names and FWHM.
Values are unitless ratios: 1 on the hull, below 1 inside an absorption.
Each hull point (including the first and last band of the window) is 1 by
construction. A pixel whose continuum is 0 at some band is no data there.

The hull depends on the window: its ends are always anchors, so moving
either end can change the continuum across the whole feature. Choose a
window that starts and ends on the continuum shoulders of the features you
want, and report it.

## Spectral Derivative

The derivative of each spectrum with respect to wavelength: the first
derivative (dR/dλ) shows absorption edges, the second (d²R/dλ²) band
centers and inflections.

| Setting | Meaning | Default | Range |
|---|---|---|---|
| **Order** | 1 = dR/dλ, 2 = d²R/dλ² | 1 | 1–2 |

**Method:** numpy's `gradient` along the spectrum, with each band's center
wavelength (nm) as x. Inside the spectrum this is a central difference that
is second-order accurate, also for uneven band spacing; the first and last
bands use a one-sided difference to their neighbor (first-order accurate).
The second derivative applies the same step twice, so for evenly spaced
bands it is (R₍ᵢ₊₂₎ − 2Rᵢ + R₍ᵢ₋₂₎) / (4h²): it spans five bands (two on
each side), not three, and the first and last two bands are affected by
the edges. Without numeric wavelengths, x is the band number and the result
is per band. Bad bands are left out before differentiating, so across a gap
of bad bands the difference is taken between the good bands on either side
of it (over the wider, uneven spacing).

**Output:** the input's good bands, with their wavelengths and FWHM (bad
bands aren't in the result). Values are the input's units per nm (per nm²
for the second derivative), or per band without wavelengths; the result's
description says which. The derivative isn't smoothed first,
and Varda has no built-in spectral smoothing (the **Iterative Outlier Voting
Filter (IOVF)** removes spikes only), so smooth the data yourself if you
need to and report how.

## Spectral Slope

The least-squares slope of each spectrum against wavelength between two
wavelengths: an ordinary straight-line fit, with every good band in the range
weighted equally.

| Setting | Meaning | Default |
|---|---|---|
| **Start Wavelength** | Start of the fitted range, nm | 1000 |
| **End Wavelength** | End of the fitted range, nm | 2000 |
| **Fit Statistics** | Also output the slope's standard error and the fit's R² (off: the slope band alone) | On |

The range is inclusive at both ends, the values can be given in either
order, and at least two good bands must fall inside it.

**Output:** with **Fit Statistics** on (the default), three bands:

| Band | Meaning | Units |
|---|---|---|
| Slope *start*–*end* nm (per nm) | The fitted slope b in y = a + bλ | Input units per nm (reflectance per nm for reflectance data) |
| Slope standard error (per nm) | √(SSE / (n − 2) / Σ(λᵢ − λ̄)²): the slope's standard error estimated from the pixel's own residuals (SSE, the sum of squared residuals, over the n good bands in the range). NaN when n = 2 (no degrees of freedom left) | Input units per nm |
| R² (unitless) | 1 − SSE / SST, SST the sum of squares of the pixel's values about their mean: the share of the spectrum's variation in the range the line explains. NaN for a flat spectrum | — |

With **Fit Statistics** off, only the slope band, named "Slope
*start*–*end* nm" (units per nm). The intercept is never output.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

- The tool and its settings: the continuum window, the derivative order,
  the slope range, all in nm. Varda records these with each result.
- The bands actually used: list the bad bands you excluded (Image
  Properties shows them), and for a window or range, the first and last
  band centers inside it. The number of bands in a slope fit is the number
  of good bands in its range.
- For **Continuum Removal**, that the continuum is the upper convex hull,
  linear in wavelength (not in wavenumber or log reflectance), anchored at
  the first and last band of the window.
- For **Spectral Derivative**, the finite-difference scheme (central
  differences on the band centers, applied twice for the second
  derivative) and any smoothing you did beforehand.
- For **Spectral Slope**, that it's an unweighted least-squares fit, and
  the standard error and R² of the slopes you report (e.g. their median
  over an ROI).

Settings the tools use that aren't in the dialog:

| Tool | Setting |
|---|---|
| All | A pixel needs at least half of its good bands to be valid; missing bands are filled linearly along the spectrum |
| Continuum Removal | Hull by the monotone chain; points on a straight line between two hull points are not hull points |
| Spectral Derivative | numpy `gradient` with the default edge order (1) |

### Uncertainty and quality

- **Only Spectral Slope outputs an uncertainty** (its standard error and
  R² bands). The others don't propagate the input's noise, and Continuum
  Removal doesn't record which bands it used as hull points for each pixel.
- **Measure the noise yourself.** Draw an ROI over a uniform area and add a
  **Zonal statistic of a raster** column (**std**) in the ROI Manager (**Add
  Column...**) for the result's band of interest. The spread includes real
  variation across the ROI, so it's an upper bound on the noise. Do the same
  on the input bands to get the per-band noise σ used below.
- **Derivatives amplify noise.** For evenly spaced bands h nm apart and
  independent noise σ in each band, the noise of the first derivative is
  σ / (√2 h) inside the spectrum and about √2 σ / h at its ends; of the
  second, √6 σ / (4h²). With hyperspectral spacing (h of a few nm), this is
  often larger than the signal, which is why derivatives are usually taken
  on smoothed spectra.
- **Slope standard error and R².** The standard error band assumes the
  residuals are independent with the same variance in every band; it
  measures how well the line fits that pixel, including real curvature
  (absorption bands) in the range, not only noise. A curved spectrum gives
  a large error and a low R²: report both, or narrow the range. With
  independent noise of known σ in every band, the standard error is
  σ / √(Σ(λᵢ − λ̄)²) instead (λ in nm), which depends only on the band
  centers. Correlated noise between neighboring bands makes the residual
  estimate too small.
- **Continuum-removed depths.** A band depth (1 minus the ratio) carries the
  noise of the band and of the hull points, which are different bands from
  pixel to pixel. Because each hull point is chosen as a local maximum,
  noise biases the continuum upward and the depths slightly deeper,
  especially at low SNR.
- **Gaps of bad bands.** Bad bands are left out, not filled, but a gap
  widens the spacing there: a derivative across it is a coarser difference,
  and a window or range that spans it has fewer bands. Report how many bad
  bands fell inside a window or range, and read derivatives next to a gap
  with care.

### References

- Continuum removal: Clark, R. N., & Roush, T. L. (1984). Reflectance
  spectroscopy: quantitative analysis techniques for remote sensing
  applications. *Journal of Geophysical Research*, 89(B7), 6329–6340.
- Monotone chain convex hull: Andrew, A. M. (1979). Another efficient
  algorithm for convex hulls in two dimensions. *Information Processing
  Letters*, 9(5), 216–219.
- Finite differences: Harris, C. R., et al. (2020). Array programming with
  NumPy. *Nature*, 585, 357–362 (`numpy.gradient`).
