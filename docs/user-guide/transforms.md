# PCA, MNF and ICA: choosing how many components to keep

**Processing → Transforms** rotates an image's bands onto new axes, called
components:

- **Principal Component Analysis (PCA)** finds uncorrelated axes ordered by
  variance: the eigenvectors of the bands' covariance matrix (Hotelling
  1933).
- **Minimum Noise Fraction (MNF)** is PCA after the noise has been whitened
  (scaled to unit variance in every direction). Its components are ordered
  by signal-to-noise rather than by variance (Green et al. 1988).
- **Independent Component Analysis (ICA)** looks for statistically
  independent, non-Gaussian sources. Varda runs FastICA on the leading
  principal components (Hyvärinen 1999; Hyvärinen & Oja 2000).

The forward transforms default to every component and attach their
eigenvalues and inverse to the result.

1. Open the result in a workspace and step through its bands to see where the
   components turn into noise.
2. Use **Transforms → Eigenvalue Plot…** to plot and tabulate the eigenvalues
   (with % and cumulative variance), and drag a cutoff to where they flatten
   out. It starts at a suggested count (see below).
3. Its button opens **Transforms → Inverse Transform (Keep N Components)** with
   that N preset. This rebuilds a denoised image in the original bands from
   the chosen components, or keeps just those component bands.

The first N components are ticked by default. Untick any (for example
component 1, to drop albedo) or type a range such as `3-19, 22`.

For ICA the plotted values aren't eigenvalues: they are each component's
variance in the data (the squared length of its mixing vector), and ICA's
components are ordered by it. The plot and table still label them
"Eigenvalue".

## MNF's noise estimate

**Noise Estimate** sets how MNF estimates the noise covariance:

- **Horizontal** (the default), **Vertical** or **Diagonal**: differences
  between each pixel and its right, lower or lower-right neighbor, divided
  by √2. The scene is assumed smooth at the scale of one pixel, so the
  differences are mostly noise. Pick the direction along which the sensor's
  noise varies (for example vertical for along-track striping).
- **Averaged**: the horizontal and vertical differences together.
- **Homogeneous Region**: the covariance of the pixels in a uniform area,
  such as a dark or flat ROI, where all variation is taken to be noise.
  Choose it under **Noise ROI Classes**: the pixels of every ticked class
  are pooled into one region. The classes are offered only for this
  estimate; with a neighbor-difference estimate none is ticked or recorded.

Only pairs of pixels that are both valid count.

The result keeps the noise model with its transform metadata (saved with
the file): `noiseEstimate` (the method's name, e.g. `HORIZONTAL`),
`noiseRoiClasses` (the pooled classes, empty for a neighbor-difference
estimate), `noiseCovariance` (the bands × bands covariance MNF whitened by,
in the input's units squared, before the numerical floor below) and
`noiseSigma` (each band's noise standard deviation, the square root of its
diagonal, in the input's units). A Spectral Hourglass MNF image added to the
project keeps the same keys.

## Wavelengths and weights

PCA, MNF and ICA each choose what goes in:

- **Wavelengths** (from, to; in the image's unit, or band numbers when it has
  no wavelengths) keeps the bands whose centers fall in that range, as the
  Spectral Hourglass does; drop noisy ranges such as detector edges or water
  bands. Bands marked bad in the image are always left out. The range is
  shown rounded so neighboring bands read differently, and compared at that
  precision (half the last shown digit either side), so a band at an edge is
  never dropped by the rounding. A range of one wavelength (the same from
  and to) holds only the band centered there, if any; a transform needs at
  least two bands, so the dialog then disables **Run** and says so (it never
  falls back to every band). Above **Run** the dialog names how many bands
  will go in and their range, and the result's provenance and its
  `transform` metadata (`sourceWavelengths`) list the bands that were
  actually used. Inverse Transform then rebuilds just those bands.
- **Weight Image** (optional; none by default) gives each value a weight from
  another image on the same grid: per band (an image with the same bands)
  or per pixel (one band). Weights are never resampled; images that can't
  be used are named under the dropdown, with why. **Weight Image Holds**
  (off until a weight image is chosen) says what that image's values are:
  **Each pixel's weight** (higher counts more), such as the number of frames
  behind each value, or **Each pixel's uncertainty σ** (each value weighted
  by 1/σ²). With one weight band per image band, "pixel" reads "value".

Without weights, a pixel's missing values (no-data or NaN) are filled by
linear interpolation along its spectrum, and a pixel missing more than half
of its good bands is left out (NaN in the result). With weights, nothing is
filled: missing values simply have no weight.

Values with no weight change nothing: they don't bend the components, and
each pixel's scores are fitted to its weighted values only. Make the weights
with Band Math from a pipeline's products, leaving out flagged values by
weighting them 0. For MISE's products, whose COUNT is the number of frames
left out of a stack of 20 and whose FLAG05 marks values with more than 5
left out, that's `(20 - COUNT) * (FLAG05 == 0)`. With equal weights each
transform is the ordinary one.

The weighted covariance is Delchambre's (2015). MNF estimates the noise
from neighbor differences weighted as both of their values are, and keeps
that estimate positive by blending it with its diagonal when needed. ICA
whitens with the weighted PCA. Each pixel's scores are a small fit, so
weighting takes longer with more components: about 2 seconds for 40
components of a 320 × 300 × 421-band cube, a couple of minutes for all of
them.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

- The transform (PCA, MNF or ICA), the wavelength range and the number of
  bands that went in (bad bands are always left out), and the number of
  components computed.
- For MNF, the **Noise Estimate** (direction, or the ROI classes pooled as
  the homogeneous region). It changes the components, so always report it,
  with the noise level it found (`noiseSigma` in the transform metadata,
  e.g. its median over the bands).
- For ICA, **Max Iterations** (200 by default, 10 to 5000), the iterations
  run and whether it converged (the result's transform metadata).
- Any weights: which image, and whether they were weights or σ.
- For an inverse transform, exactly which components you kept (for example
  "MNF components 1–12" or "PCA components 2–19"), and the **Output**:
  **Denoised image (inverse transform to the original bands)**, or **Only
  the chosen components (as component bands)**.

The result's record keeps the settings, and its input's record (the
component image's, with the forward transform's settings and its own
input), in Varda and in exported files (see [Reporting results from
Varda](reporting-results.md#what-varda-records-and-what-it-doesnt)).

Settings that aren't in the dialog:

| Step | Setting |
| --- | --- |
| Covariance | About the mean, divided by n − 1 (n is the number of valid pixels) |
| Explained variance | PCA: each eigenvalue over the total variance of all the bands that went in. MNF: the same, of the noise-whitened data. ICA: each component's variance over the sum for the components computed, so it depends on how many you computed |
| MNF eigenvalues | SNR + 1 if the noise estimate is right: 1 is pure noise |
| Suggested count | MNF: the components with an eigenvalue above 2, but no more than the elbow (the point of the log eigenvalue curve farthest from the line joining its ends). PCA and ICA: enough to reach 99% of the variance |
| FastICA | Parallel (symmetric) updates, log cosh contrast, tolerance 10⁻⁵, random start with a fixed seed (0), so runs are repeatable |
| Numerical safeguards | MNF floors the noise eigenvalues at 10⁻¹⁰ × the largest. Weighted MNF blends the noise covariance with its diagonal until its smallest eigenvalue is at least 10⁻³ × the mean variance. Weighted scores add a 10⁻¹² ridge to each fit |

The suggested count is only a starting point. Report the count you chose
and why (the eigenvalue curve, the component images, or both). An Inverse
Transform result records both in its metadata (`componentSelection`, saved
with the file): `kind` (PCA, MNF or ICA), `suggested` (the count suggested
for the input), `rule` (the rule above, in words) and `kept` (the 1-based
components used).

### Uncertainty and quality

- **Units of the components.** PCA scores are in the input's units, about
  the mean. MNF scores are in units of the estimated noise standard
  deviation, so an MNF component's variance is its eigenvalue. ICA scores
  have unit variance; their scale is in the mixing vectors. The band units
  read "components", not wavelengths.
- **Signs and order.** A component's sign is arbitrary (flipping it changes
  nothing). ICA's order is by variance, not by importance.
- **MNF's SNR reading depends on the noise estimate.** Neighbor
  differences count real scene texture as noise, and miss noise that is
  correlated between neighbors. Striping, spikes or unfiltered artifacts
  make the noise non-white, so the noise floor sits above 1. Compare noise
  estimates if the choice matters: the `noiseSigma` each one recorded shows
  how much noise it found in every band (a neighbor-difference σ well above
  a dark ROI's is scene texture counted as noise).
- **ICA convergence.** FastICA has converged when no unmixing vector turns
  by more than the tolerance (1 − |cos| < 10⁻⁵) in an iteration. The
  result's transform metadata (saved with the file) records `iterations`
  run, `maxIterations`, `converged` and `tolerance`. If it ran out of
  iterations first, the result's description says "did not converge in N
  iterations" and the log has a warning: the components are the last
  estimate and may not be independent. Rerun with a larger **Max
  Iterations**, and report the iterations and convergence.
- **The rebuilt image has no residual band.** To measure what an inverse
  transform removed, subtract the rebuilt image from the original with
  **Processing → Image Math → Image Arithmetic** (see [Image
  processing](image-processing.md)), and report its RMS or look at it for
  structure. Structure in the residual is signal you dropped. The two must
  have the same bands: if the transform left bands out, first cut the
  original to the same bands with **Subset (Crop Rows, Columns, Bands)**.
- **What's saved where.** The band names of a PCA result carry each
  component's share of the variance ("PC 1 (85.2%)"). The eigenvalues,
  explained variance, mean and inverse are kept with the result and in its
  exported files (the `VARDA` metadata domain), so **Eigenvalue Plot…** and
  **Inverse Transform** work on a reopened file. Quote the values you report
  from **Eigenvalue Plot…**'s table.

### References

- PCA: Hotelling, H. (1933). Analysis of a complex of statistical variables
  into principal components. *Journal of Educational Psychology*, 24(6),
  417–441.
- MNF: Green, A. A., Berman, M., Switzer, P., & Craig, M. D. (1988). A
  transformation for ordering multispectral data in terms of image quality
  with implications for noise removal. *IEEE Transactions on Geoscience and
  Remote Sensing*, 26(1), 65–74.
- FastICA: Hyvärinen, A. (1999). Fast and robust fixed-point algorithms for
  independent component analysis. *IEEE Transactions on Neural Networks*,
  10(3), 626–634.
- ICA: Hyvärinen, A., & Oja, E. (2000). Independent component analysis:
  algorithms and applications. *Neural Networks*, 13(4–5), 411–430.
- Weighted PCA: Delchambre, L. (2015). Weighted principal component
  analysis: a weighted covariance eigendecomposition approach. *Monthly
  Notices of the Royal Astronomical Society*, 446(4), 3545–3555.
