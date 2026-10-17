# Factor Analysis Target Transformation (FATT)

**Processing → Endmembers → Factor Analysis Target Transformation…** asks
which library spectra could be components of an image. The image's spectra
are treated as mixtures of a few components. Factor analysis finds the space
those mixtures span. A library spectrum that the space reproduces well (its
*target transformation*) is a candidate component (Malinowski 1991;
Bandfield et al. 2000; Thomas & Bandfield 2017; Lin et al. 2021).

FATT finds **candidates, not detections**. Confirm a target, for example
with ratioed spectra, before reporting it.

## Ranking library targets

1. Choose the image and the spectra to analyze:
   - every Nth pixel of every Nth row of the whole image (every 3rd by
     default, 1 to 50);
   - or every pixel of an ROI class in the current workspace.
2. Set the wavelength range. The default is 1.7–2.6 µm, where most
   diagnostic hydrated-mineral features are, when the image has at least 5
   bands there; otherwise it's the image's whole range. The image's bad
   bands are left out.
3. Choose the decomposition:
   - **Covariance (mean removed)** (the default): the eigenvectors of the
     covariance matrix plus the mean spectrum (Bandfield et al. 2000;
     Thomas & Bandfield 2017).
   - **Hysime**: the signal subspace of the (non-centered) correlation
     matrix, with its size chosen by comparing signal and noise power
     (Bioucas-Dias & Nascimento 2008), as Lin et al. (2021) use it.
4. Press **Analyze**. The plot shows Malinowski's indicator function (IND).
   Its minimum (for the covariance method), or Hysime's count, suggests how
   many factors to keep; drag the cutoff to change it. For the covariance
   method, N factors are the mean spectrum and the first N − 1
   eigenvectors. More factors fit the targets better, but they also fit
   noise and processing artifacts, so expect more false positives. 10–15 is
   typical for CRISM.
5. Pick a library: any of Varda's [spectral libraries](spectral-libraries.md)
   (MICA numerators for CRISM, USGS minerals, your own, or one loaded
   anywhere in Varda), or **Load Library…** another, which then appears in
   every library list and is remembered. Every spectrum is resampled to the image's bands and ranked
   by **normalized RMSE** (nRMSE, shown in units of 10⁻⁴): the RMSE after
   dividing both spectra by their sum, so brightness doesn't matter.
   **Angle (°)** is the spectral angle between the library spectrum and its
   target transformation, in degrees. Targets below the threshold are
   shaded green. Select a target to compare it with its target
   transformation.

Library spectra are resampled through each band's response, a Gaussian
with the band's FWHM, when the image has FWHM; otherwise they're linearly
interpolated at the band centers. A band counts only when the library
covers more than half of its response. Spectra covering fewer than 5 of the
bands are skipped, and each one is compared over the bands it covers.

The default threshold, 1.5 × 10⁻⁴, was set by Lin et al. (2021) for
CRISM I/F over 1.7–2.6 µm (about 130 bands). Varda doesn't adjust it. Since
each spectrum is divided by its sum over the bands, nRMSE values get
smaller roughly in proportion as the number of bands grows, so a different
range or instrument needs its own threshold. In principle each mineral
deserves its own too, so treat it as a starting point.

## Mapping where targets fit

Tick targets in the **Map** column and press **Map Ticked Targets…**. This
runs DAFA/TT (Lin et al. 2021):

- FATT runs in small moving windows of about 50 pixels in three shapes
  (7×7, 5×10 and 10×5, rows × columns), stepped one pixel at a time.
- A pixel counts for a target only when every shape has a window containing
  it in which the target fits.

The map uses its own factor spaces: each window's is the first *Factors per
window* singular vectors of its spectra, about the origin (no mean
removed). The **Decomposition** and **Factors** chosen in the FATT window
don't apply to it. The map starts with the FATT window's wavelength range
and threshold. Each target is fitted over the bands it covers, as in the
ranking table, so a target's map doesn't depend on which other targets are
ticked. A target covering fewer than 5 of the image's bands in the range
stops the map, naming it.

The map fits a few hundred thousand windows per shape for a typical CRISM
scene, so it takes a while. Its working memory stays near 1 GiB whatever the
image size (the windows are cut from the image in chunks), so it runs on a
laptop without crowding out other programs.

The result joins the image list. Its first band is the number of targets
detected at each pixel; then there's one band per target with its
normalized RMSE, so you can threshold a target differently later.
*Factors per window* (15 by default, 1 to 49) is the dimension of each
window's factor space.

A phase must add variance to the spectra in a window to be found: a phase
spread evenly across the window, under about half a pixel, or masked in a
mixture can be missed.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

For the ranking:

- The spectra analyzed (every Nth pixel and N, or the ROI class and its
  pixel count; the status line gives the number of spectra and bands), the
  wavelength range and the number of bands.
- The decomposition, the number of factors you used, and the suggested
  number (IND minimum or Hysime count).
- The library (name and version, the file you loaded, or for one of your
  own the records of its spectra; see
  [Spectral libraries](spectral-libraries.md#reporting-and-citing)), the threshold,
  and each reported target's nRMSE and angle.

For the map:

- The targets, the threshold, *Factors per window*, the window shapes
  (7×7, 5×10, 10×5) and the number of bands used.
- How you confirmed each detection (for example ratioed spectra), since
  the map gives candidates.

Settings that aren't in the dialog:

| Step | Setting |
| --- | --- |
| Pixels used | Pixels with no-data in some bands are filled by linear interpolation along the spectrum; a pixel missing more than half of its good bands is left out |
| Covariance | About the mean, divided by n − 1 |
| IND | Malinowski's IND(n) = RE(n) / (c − n)², computed about the origin (no mean removed), with RE(n) = √(Σ of the remaining eigenvalues / (r (c − n))), for r spectra of c bands (swapped when there are fewer spectra than bands) |
| Hysime | Each band's noise is what a least-squares fit from all the other bands leaves over. The inverse of the bands' correlation matrix gets a ridge of 10⁻⁶ × its mean diagonal; the noise correlation gets a floor of 10⁻⁵ × the mean signal power per band. The count is the number of signal directions whose power is more than twice their noise power |
| Map windows | A window with a no-data pixel is skipped; each pixel's value for a shape is the lowest nRMSE among the windows of that shape containing it, and its final value the highest of the three shapes. NaN where a shape has no usable window |
| Map detection | nRMSE strictly below the threshold |

### Uncertainty and quality

- **nRMSE is a fit, not a probability.** It says how well the factor space
  reproduces a library spectrum. It has no confidence interval, and a low
  value can come from a factor space so large that it fits almost
  anything. Report the number of factors with every nRMSE, and check that
  a target's fit holds over a range of factor counts.
- **The map is biased toward detection.** Each pixel takes the best of up
  to about 50 windows per shape, so its nRMSE is lower than a single
  window's and false positives are more likely. The three shapes must
  agree, which works against that. Pixels near the image's edges or near
  no-data lie in fewer windows, so their values are less reliable.
- **Ranking and map values aren't comparable.** The ranking uses one
  factor space for the whole sample; the map uses one per window, with a
  different number of factors. Within the map, targets covering different
  bands are fitted over different bands, so their nRMSE values aren't
  directly comparable (nRMSE scales with the number of bands): report each
  target's band count with the map.
- **Library resampling.** A library spectrum measured at a different
  resolution, grain size or condition fits less well. Without the image's
  FWHM, the library is interpolated at band centers, which keeps narrow
  features the bands themselves would blur.
- **What's saved where.** **Export Table…** saves the ranking as CSV:
  every target's rank, name, nRMSE (unitless; the window shows it ×10⁻⁴),
  spectral angle between the target and its fit (degrees), whether it was
  ticked and whether it fits under the threshold, after commented (`#`)
  lines giving the image, the spectra used (sampling or ROI class, and how
  many), the wavelength window and band count, the decomposition, the
  factor count, the library, the threshold, the Varda version and the time.
  The map's targets, library, threshold, factors, windows and each
  target's band count
  are in its metadata and its provenance record (threshold in units of
  10⁻⁴), in Varda and in exported files; the band names name the targets.

### References

- Factor analysis and IND: Malinowski, E. R. (1991). *Factor Analysis in
  Chemistry* (2nd ed.). Wiley.
- FATT on spectra: Bandfield, J. L., Christensen, P. R., & Smith, M. D.
  (2000). Spectral data set factor analysis and end-member recovery:
  application to analysis of Martian atmospheric particulates. *Journal of
  Geophysical Research: Planets*, 105(E4), 9573–9587.
- FATT on near-infrared (CRISM) data: Thomas, N. H., & Bandfield, J. L.
  (2017). Identification and refinement of martian surface mineralogy using
  factor analysis and target transformation of near-infrared spectroscopic
  data. *Icarus*, 291, 124–135.
- Hysime: Bioucas-Dias, J. M., & Nascimento, J. M. P. (2008). Hyperspectral
  subspace identification. *IEEE Transactions on Geoscience and Remote
  Sensing*, 46(8), 2435–2445.
- DAFA/TT, nRMSE and the default threshold: Lin, H., Tarnas, J. D., Mustard, J. F., Zhang, X., Wei,
  Y., Wan, W., Klein, F., & Kellner, J. R. (2021). Dynamic aperture factor
  analysis/target transformation (DAFA/TT) for Mg-serpentine and
  Mg-carbonate mapping on Mars with CRISM near-infrared data. *Icarus*, 355,
  114168. https://doi.org/10.1016/j.icarus.2020.114168
- Library resampling: Spectral Python (SPy)'s band-resampling matrix. Varda
  uses SPy 0.24 or 0.25; **Help → Copy System Info** doesn't list which.
