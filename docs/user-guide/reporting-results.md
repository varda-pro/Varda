# Reporting results from Varda

Varda is meant to be described in a paper. Anything you do in it should be
something you can write up accurately, with its uncertainties, from these
guides and from what Varda gives you. This page covers what applies to every
process; each guide ends with a **Reporting your results** section for its
own methods, settings and references.

## Cite Varda, its version and the methods

- **Varda:** **Help → Cite Varda…** gives APA and BibTeX (also GitHub's
  **Cite this repository** button). Give the version: it's in the citation,
  in **Help → About Varda**, and in every result's record (below). The DOI
  in the citation, [10.5281/zenodo.23447208](https://doi.org/10.5281/zenodo.23447208),
  cites every version and resolves to the latest; each version has its own
  DOI on that Zenodo record (minted when the release is published, so the
  app can't show it). Cite the DOI of the version you used.
- **The methods:** cite the papers behind each method you used, listed in
  each guide's references. Varda implements them; it doesn't replace them.
- **Other software Varda ran for you:** Spectral Python, HyPyRameter, ISIS,
  the Ames Stereo Pipeline and PyTorch have their own citations. **Help → Copy System Info**
  copies the versions of Varda, Python, Qt, numpy, GDAL, PROJ, Spectral
  Python (SPy), HyPyRameter and PyTorch, which you can quote or keep with
  your notes. Each result's record also names the libraries that made it
  (below).
- **The data:** cite the instrument teams' data products and spectral
  libraries as their providers ask.

## What Varda records, and what it doesn't

Every result Varda makes carries a **provenance record**: how it was made,
in enough detail to write a methods section from. That covers every
**Processing** menu result and the results made in workspaces: mosaics and
their source maps, displacement fields, crops made with the crop tool, and
the Spectral Hourglass's MNF, PPI, map and MTMF detection images. The
record holds:

- **The process:** its name and identifier (e.g. "Band Math",
  `band_math`).
- **Every setting**, as the dialog or workspace had it. A Processing
  menu result records each setting under the name its dialog shows, a
  choice by the label you picked (e.g. `"Weight Image Holds": "Weights:
  larger counts more (e.g. sample counts)"`), and a range by its two ends
  with the unit they were shown in (`"Wavelengths": {"from": 1000.0, "to":
  2600.0, "units": "nm"}`; no `units` when the dialog showed none, e.g.
  band numbers). Workspace results
  record their own settings: a mosaic its full recipe (order, corrections,
  tie points with their match quality and phase-plane SNR, radiometry with
  its fit quality, seams, blending), output grid and resampling; its source
  map each source's alignment quality (tie points, RMS, median phase SNR);
  a displacement field how Auto Align matched (method asked for and used,
  bands, tile size, refinement); a crop its window (0-based row, column,
  height and width); a Spectral Hourglass result the wavelength window and
  bands kept, IOVF on or off, its settings and what it replaced, the MNF
  noise estimate and components kept, the PPI iterations, threshold, seed and candidate rule,
  every endmember's name, identification and pixels, and the map method
  and its setting.
- **The inputs:** each by name and file path, **with the input's own record**
  when it has one. A chain of results (a ratio of a filtered image of a
  crop, say) keeps every step, in Varda and in files.
- **A corrected coordinate system:** when Varda read an input in a CRS
  other than the one its file declares (the one automatic case: Earth's
  degrees on a planetary image whose label names another body, or a CRS
  you chose in Image Properties), the input carries a `crsFix` record:

  | Key | Meaning |
  |---|---|
  | `from` | The CRS the file declared: its authority code (e.g. `EPSG:4326`), or its WKT when it has none |
  | `to` | The CRS Varda used: its authority code (e.g. `IAU_2015:49900`), or its WKT |
  | `reason` | One sentence, e.g. "Its CRS says Earth (WGS 84) but its label's target is Mars; read as degrees on the IAU 2015 Mars sphere" |
  | `automatic` | `true`: Varda applied it when the image opened; `false`: you applied it (**Apply**, or **Set coordinate system…**, whose `reason` says which) |
  | `undone` | Present and `true` only when you undid the automatic fix: the file's own CRS (`from`) was used, not `to` |

  The image keeps the record in its own metadata too, so an exported copy
  of the image (whose file now declares the corrected CRS) still says it
  was corrected. Report it as
  [Coordinate systems](coordinate-systems.md#reporting-a-corrected-crs)
  shows. Inputs also keep the facts their labels state (`labelHints`:
  target, latitude type, longitude direction and domain, and which label).
- **The software:** the Varda version, the plugin and its version if a
  plugin made it, and the versions of the libraries or tools that did the
  work where they matter: Spectral Python for the detectors, the
  statistical classifiers and FATT's library resampling, HyPyRameter for
  band parameters, PyTorch for the CNN and ViT classifiers (with the device
  the Optional Features runtime was installed for), scikit-image and SciPy
  for spatial filters, and ISIS or the Ames Stereo Pipeline when a mosaic
  uses their results.
- **The time** (UTC).
- **Results measured on the result**, under `results`: a classifier's
  training data (ROI and pixel counts per class, ambiguous pixels left out)
  and hold-out accuracy, and an Accuracy Assessment's confusion matrix and
  measures (see [Classification](classification.md#measuring-accuracy)). A
  result made from a class map thus keeps the map's accuracy in its chain.
- **Where downloaded data came from:** a file downloaded in Find Data has a
  `<file>.varda-source.json` beside it (catalog, collection, product,
  file, URL, size, checksum and what verified the download, the time and
  the Varda version). Opening the file adds this to its record as its
  `source`, so every result made from it names where the data came from.

### Where it lives in files

Exporting a result (or saving it from the analysis dialog, or packaging the
project) writes the record, and the scientific metadata worth keeping, into
the file's `VARDA` metadata domain as JSON:

- **GeoTIFF:** inside the `.tif`.
- **ENVI:** in `<file>.img.aux.xml` beside the `.img` and `.hdr`. Keep the
  three together. A [packaged project](sessions-and-saving.md#sharing-a-project)
  includes it. The `.hdr`'s `description` holds the human-readable
  description and how the image was made, and names the files that must
  travel with it (some analyses add their own, such as a ratio cube's
  `.columns.csv`).

The domain has two keys: `provenance` (the record) and `metadata` (class
names and colors, eigenvalues, explained variance and the inverse transform,
classifier figures such as layer sizes, the final training loss,
the seed, the number of components, the device and PyTorch version, the
training data and hold-out accuracy, Accuracy Assessment reports, the
mosaic recipe, displacement and FATT settings, and the quality figures of
analyses: MNF's noise model, ICA's convergence, the components an Inverse
Transform kept and the count it suggested (`componentSelection`), K-Means
cluster statistics (`kmeans`), unmixing convergence (`unmixing`), what IOVF
replaced (`iovf`) and sensor-band coverage (`sensorBands`)). Varda reads both back when
it opens the file, so a reopened class map keeps its names and colors, and a
reopened PCA, MNF or ICA result still works with **Eigenvalue Plot** and
**Inverse Transform**. Files Varda didn't write open exactly as before.

The file's description (the ENVI header's `description`, or the GeoTIFF's
description tag) also keeps a one-line summary, such as "Made with Varda
0.9.0: Band Math (Expression=…) from /data/scene.img on 2026-10-16T…",
naming the plugin when there is one; a range reads "Wavelengths=1000–2600
nm" there. Long values are shortened there, never in the record.

To read the record outside Varda:

```sh
gdalinfo -mdd VARDA result.tif        # or result.img
```

or in Python: `rasterio.open("result.img").tags(ns="VARDA")["provenance"]`
(JSON text; `json.loads` gives the record).

### What isn't recorded

Note these yourself:

- **ROI geometry.** Analyses trained on ROIs record their settings (such
  as which classes they used), not the polygons. Save the session, which
  keeps the ROIs, with the results.
- **Edits to the input** made in Image Properties (no-data value,
  wavelengths, bad bands) aren't part of a result's record.
- **Runs from Varda 0.9.0 and earlier** of the Pixel Purity Index and the
  CNN and ViT classifiers used random numbers that weren't recorded, so
  they can't be repeated exactly. These processes now have a **Seed**
  setting (0 by default), recorded with their other settings; the CNN and
  ViT can still differ slightly between devices (CPU, CUDA, MPS) and
  PyTorch versions, which their records name (see
  [Classification](classification.md)).
- **Results kept only in memory** aren't in a session: export them, or
  [package the project](sessions-and-saving.md#sharing-a-project).
- **Display stretches** and other view settings aren't part of any result
  (they never change the data).

A [packaged project](sessions-and-saving.md#sharing-a-project) is the most
complete record: it has the session, every image, every in-memory result
(as files, with their records) and a manifest with each file's SHA-256
checksum.

## Precision is not accuracy

Many of Varda's quality numbers (tie-point RMS and phase-plane SNR, a
radiometric balance's fit RMS and overlap agreement, unmixing RMS error,
FATT nRMSE, spectral angles) measure how well a model fits the data you
gave it. They're precision. Accuracy also depends on things Varda can't see:

- **Georeferencing.** Map positions, distances and linked-image locations
  are only as good as each image's own georeferencing. Report its source and
  stated accuracy, and use check points (see
  [Mosaicking](mosaicking.md#reporting-your-results)) when alignment matters.
- **Elevations.** Profiles, slopes and 3D terrain inherit the DEM's
  vertical error. Report the DEM's stated vertical precision (from its
  producer) along with anything you derive from it.
- **Calibration.** Spectra, band parameters and abundances inherit the
  calibration and atmospheric correction of the input. Varda adds none.
- **Libraries.** A library match is only as good as the library spectrum's
  resolution, grain size and measurement conditions.

## Results labeled "(visual)"

A result whose name ends in "(visual)" was made with a step that changes
spectral values for the sake of appearance: bilinear resampling, blending or
histogram matching. Use it for figures, not for measurements.

## Display stretches

Stretches only change how an image looks; they never change the data or the
results of analyses. If you state stretch limits in a figure caption, note
that percentile limits are estimated from a 1,024-bin histogram, so they're
approximate.

## Plugins and Kindle a Feature

A result made by a plugin records the plugin's name and version. A plugin
built with [Kindle a Feature](kindle-a-feature.md) was written by an AI and
checked only to run without errors: it hasn't been validated scientifically.
Before reporting its results, read its code and test it on data with a known
answer, and describe what it does in your own words.
