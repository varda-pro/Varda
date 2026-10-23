# Optional features

## Band parameters (HyPyRameter)
**Processing → Spectral Parameters → Band Parameters (HyPyRameter)** computes
CRISM-style spectral parameters with
[HyPyRameter](https://github.com/Michael-S-Phillips/HyPyRameter); the dialog
lists every parameter the image's wavelength range supports (hover a name for
its definition) and lets you pick which to compute. It is optional: without it
Varda runs normally and lists that analysis as unavailable. The downloadable
app includes it; from source, the `hypyrameter` extra installs it from PyPI:
```bash
uv sync --extra hypyrameter
# or, with pip:  pip install "varda[hypyrameter]"
```

To describe and cite it in a paper, see [Reporting your
results](#reporting-your-results).

## CNN and ViT classifiers, and learned matchers (PyTorch)

The same environment also runs the Mosaic workspace's LoFTR and SIFT
matchers (Auto Align): it includes kornia and OpenCV for them.

**Processing → Classification → Train CNN on ROIs / Train ViT on ROIs** train
small neural networks on spatial-spectral patches around your ROI pixels. They
need PyTorch, which is too large to ship in every download. (The MLP
classifier is pure numpy and always available.)

**In the app.** The first time you pick one of these classifiers, Varda offers
to install PyTorch. You can also open **Processing → Optional Features…** at
any time. Varda picks the build for your hardware: Apple GPU on Apple silicon,
CUDA 13 for a recent NVIDIA GPU and driver, otherwise CPU. You can override
that choice. The CPU and Apple builds are a few hundred MB; the CUDA build is
about 3 GB.

PyTorch goes into its own Python environment in Varda's data folder (shown in
the dialog), so nothing else on your computer changes. Varda installs exactly
the versions pinned for its release, checked against their hashes. The
classifiers then run in a separate process using that environment.
**Uninstall** deletes the folder. Intel Macs and ARM Linux are not supported.

**From source.** Install the `ml` extra to run PyTorch inside Varda's own
environment instead:
```bash
uv sync --extra ml
# or, with pip:  pip install "varda[ml]"
```
Training runs on CUDA or Apple MPS when available, otherwise on the CPU.
The classifiers' **Seed** setting seeds the starting weights and the
shuffling, and Varda asks PyTorch for deterministic algorithms, so the same
seed and settings repeat on the same device and PyTorch version. GPU
arithmetic isn't identical to CPU arithmetic, so results can differ slightly
between CPU, CUDA and Apple (MPS) runs; each result records the device and
PyTorch version. What the classifiers do and how to report them:
[Classification](classification.md).
Maintainers: `scripts/compile_torch_locks.py` regenerates the pinned locks in
`resources/optional_features/torch/`.

## Ames Stereo Pipeline (mosaicking)

Auto Align in the [Mosaic workspace](mosaicking.md) can match images with the
Ames Stereo Pipeline's `image_align` instead of Varda's own matchers. It's
optional: Varda's tools cover everything Auto Align needs.

**Processing → Optional Features…** shows whether Varda found ASP and where.
It looks, in order, in a folder you choose (**Choose Folder…**), on your
PATH, in conda environments named `asp` (or holding ASP) under miniforge,
miniconda, anaconda and mambaforge, in `~/StereoPipeline-*`, and in its own
download. **Re-detect** looks again.

If you don't have ASP, **Download ASP 3.7.0** fetches the official release
for your computer (260–650 MB) from GitHub into Varda's data folder, checks
it against its published SHA-256 checksum and unpacks it. **Remove** deletes
that copy (Varda never removes an ASP it didn't download).

ASP has no Windows version (it runs in WSL), so the download isn't offered
there.

**Citing it.** If you used ASP for Auto Align, cite Beyer, Alexandrov &
McMichael (2018) (see [Mosaicking](mosaicking.md#references)) and give the
ASP version, which **Optional Features** shows (read from `image_align
--version`). Varda doesn't record it with the result, so note it yourself.

## ISIS (mosaicking)

If you have [ISIS](https://github.com/DOI-USGS/ISIS3) installed, Varda finds
it through `ISISROOT`, or as a conda environment named `isis` (or one holding
ISIS, as ASP's environment often does). Varda never downloads ISIS: its
mission data runs to tens of gigabytes. **Optional Features** shows what was
found, and whether `ISISDATA` is set.

With ISIS, Varda can:

- **Map-project raw camera cubes:** **Processing → Geometry → Map-Project with
  ISIS (cam2map)…** projects a spiceinit-ed level 1 cube (to ISIS's default
  sinusoidal projection, to match a map-projected cube, or with a map
  template) so it can be mosaicked. Varda uses nearest-neighbor
  interpolation by default so the projected spectra are measured ones; the
  result opens in Varda. cam2map needs `ISISDATA` and the mission's SPICE
  kernels.
- **Cross-check mosaic radiometry:** in the Mosaic workspace's Radiometry
  tab, **Compare with ISIS equalizer** solves the same balance with ISIS
  equalizer (same held images and fit) and shows its gains and offsets beside
  Varda's; **Use ISIS's Corrections** applies them.

**Citing it.** Cite the ISIS release you used, as ISIS's documentation asks,
and name the programs (`cam2map`, `equalizer`) and their settings. **Optional
Features** shows the ISIS version when it can read it from a conda install
(otherwise it just says "ISIS found"); Varda doesn't record it with the
result, so note it yourself. A map-projected cube opens in Varda as an
ordinary file, without a Varda record of how it was made.

## Reporting your results

What to state when you use **Band Parameters (HyPyRameter)**, what limits
its values, and what to cite. See also [Reporting results from
Varda](reporting-results.md) for what applies to every process. For the
PyTorch classifiers see [Classification](classification.md); for ASP and
ISIS, the notes above.

### What to state

- The parameters you computed, by their HyPyRameter names (e.g. `BD1900_2`,
  `D2300`), and the **HyPyRameter version**. Each parameter's definition
  (its bands, kernel widths and formula) is fixed by the HyPyRameter
  version; Varda's record of the result lists the parameters but not the
  HyPyRameter version or the exact bands each parameter used. **Help → Copy
  System Info** gives the version.
- The input: its calibration and atmospheric correction, and its units.
  HyPyRameter expects reflectance.
- Whether **Clamp reflectance to [-1, 1]** was on (it is by default).

What Varda does before HyPyRameter sees the data:

| Step | What happens |
| --- | --- |
| Missing data | No-data pixels and the image's bad bands become missing, then each pixel's missing bands are filled by linear interpolation between the neighboring finite bands (the nearest value past the first or last). Pixels with fewer than half their good bands are left missing |
| Scale factor | If the image has a scale factor (e.g. reflectance stored as integers × 10,000), the data are divided by it |
| Clamp | With **Clamp reflectance to [-1, 1]** on, any value with \|R\| > 1 becomes missing (after the filling above, so it isn't filled) |
| Wavelengths | Passed to HyPyRameter in nm |
| Which parameters are offered | Those whose wavelength range lies within the image's, with 5 nm of slack at either end |

Within HyPyRameter, each reflectance used is taken at the band nearest the
nominal wavelength, usually as the median over a few bands around it (a
number of bands, not nm; missing values are skipped). Where a parameter
still needs a value that is missing, it comes out missing for that pixel:
missing data in, missing data out.

### Uncertainty and quality

- **Outputs** are one band per parameter, unitless (band depths, ratios,
  slopes and indices of reflectance), stored as 32-bit floats. Missing
  pixels are NaN.
- **No per-pixel error.** Varda and HyPyRameter give no uncertainty for a
  parameter value. Each value inherits the noise of the bands it uses, and
  band depths of a few percent can be at the noise level. To put a number
  on it, report ROI statistics (mean and standard deviation) for the areas
  you discuss, and compare with a spectrally featureless area of the same
  image: its spread is a practical noise floor for each parameter.
- **Calibration and correction** errors pass straight into the parameters
  (see [Reporting results from Varda](reporting-results.md#precision-is-not-accuracy)).
  A positive band depth is a candidate, not an identification: confirm it
  with spectra (e.g. ratioed spectra).
- **Agreement with CRISM's own parameters.** Against the PDS browse
  products of the CRISM MTRDR FRT000047A3, HyPyRameter's OLINDEX3, D2300,
  D2200, BD1900R2, HCPINDEX2 and BDI1000VIS track CAT's closely (fitted
  slopes 0.95–1.00, r = 0.90–1.00); see [Browse
  products](viewing-and-measuring.md#agreement-with-the-pds-products). One
  scene is not a validation of every parameter: compare with the MTRDR
  summary products where they exist.
- **BDI1000VIS from HyPyRameter 0.3.0 or earlier is wrong.** Those versions
  integrated R/RPEAK1 over 833–989 nm where Viviano-Beck et al. (2014)
  integrate 1 − R/RPEAK1 over 833–1023 nm, so the values ran opposite to
  CRISM's (r = −0.97 on FRT000047A3). HyPyRameter 0.3.1 fixes it, and Varda
  requires 0.3.1; compute BDI1000VIS again if you made it with an earlier
  Varda (0.12.2 or before). **Help → Copy System Info** gives the
  HyPyRameter version, and a result's provenance gives the Varda version.
- **Interpolated bands.** Where bad bands or missing values were filled, a
  parameter that uses them is computed from interpolated values. Check the
  bad bands list in **Image Properties** and say if any fall in a parameter's
  window.
- **Sparse or uneven band sampling.** A parameter is offered whenever its
  wavelength range falls within the image's; HyPyRameter doesn't check that
  a band lies near each wavelength it uses. On multispectral images, or
  across gaps in the bands, the nearest band can be far from the nominal
  one, and the median kernel spans more nm where bands are wider apart.
  The definitions assume CRISM-like sampling: check the band centers behind
  any parameter you report.
- **Clamping.** If the image isn't reflectance (radiance, or reflectance in
  percent), the clamp makes almost everything missing; turn it off only if
  you know why the values exceed 1.

### References

- Parameter definitions: Viviano-Beck, C. E., Seelos, F. P., Murchie, S. L.,
  et al. (2014). Revised CRISM spectral parameters and summary products
  based on the currently detected mineral diversity on Mars. *Journal of
  Geophysical Research: Planets*, 119(6), 1403–1431.
- HyPyRameter: Phillips, M. S., Udovicic, C. T., Moersch, J. E., Basu, U.,
  & Hamilton, C. W. (2024). HyPyRameter: a Python toolbox to calculate
  spectral parameters from hyperspectral reflectance data. *The Planetary
  Science Journal*, 5, 258. https://doi.org/10.3847/PSJ/ad81f8. The software
  is archived on Zenodo (doi:10.5281/zenodo.10801541): give the version you
  used. Each
  parameter's definition, including those beyond the CRISM set, is in its
  description (hover its name in the dialog) and HyPyRameter's
  documentation.
