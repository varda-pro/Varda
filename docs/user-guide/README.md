# Varda user guide

Detailed guides to Varda's features. For an overview, see the
[main README](../../README.md).

- [Reporting results from Varda](reporting-results.md): citing Varda and its methods, what results record, precision vs. accuracy
- [Installing Varda](installing.md): downloading a release, running from source, optional extras
- [Network use and privacy](network-and-privacy.md): every connection Varda makes, when, and what it sends; turning off the update check; where AI keys are kept
- [Sessions and saving results](sessions-and-saving.md): saving and restoring your work, autosave, exporting images
- [3D Terrain](3d-terrain.md): DEMs in 3D with draped images, figures and interactive web pages
- [Small bodies and shape models](small-bodies.md): plate shape models of irregular bodies in 3D, picking plates, plate data with uncertainty
- [Linked Images](linked-images.md): any number of images linked by pixel or map location, with every image's spectrum and profile at once
- [Lines and profiles](lines-and-profiles.md): drawing lines and plotting topographic and band profiles along them
- [Viewing and measuring](viewing-and-measuring.md): display stretches, pixel spectra and library overlays, ROI statistics, the Ratio Explorer
- [FITS images and ALTWG/SPC terrain cubes](fits-images.md): what FITS files open, how planes become named, unit-bearing bands, no-data, uncertainty planes, and what isn't supported yet
- [Coordinate systems](coordinate-systems.md): what Varda shows about an image's coordinate system, planetary conventions (IAU 2015, planetocentric and planetographic, east and west, 0–360), and how Varda identifies the body
- [Importing vector data](importing-vector-data.md): ROIs, points and lines from shapefiles, GeoPackages, GeoJSON, KML and CSV; coordinate systems and accuracy
- [Finding data](finding-data.md): searching PDS ODE, STAC and footprint catalogs, and downloading products
- [Image processing tools](image-processing.md): spatial filters, texture, color, class map tools, terrain and geometry
- [Reprojecting images](reprojecting.md): Reproject Image onto a recommended projection, another image's grid or a coordinate system, with quality bands and what to report
- [Spectral libraries](spectral-libraries.md): the bundled libraries, building your own from image spectra, loading, exporting, and what's recorded with each spectrum
- [Spectral analysis](spectral-analysis.md): continuum removal, spectral derivatives and slopes
- [Classification](classification.md): MLP, CNN and ViT classifiers, maximum likelihood, Mahalanobis distance, k-means, hold-out validation and accuracy assessment
- [Target and anomaly detection](target-detection.md): RX, ACE and the matched filter
- [PCA, MNF and ICA](transforms.md): choosing how many components to keep, inverse transforms
- [Spectral Hourglass](spectral-hourglass.md): finding, identifying and mapping endmembers, preparing a spectral library
- [Factor Analysis Target Transformation](fatt.md): which library spectra are components of an image, and where they fit
- [CRISM ratio cubes](crism-ratio.md): dividing a CRISM I/F cube by a bland denominator spectrum per detector column, and the CRISM Ratio workspace for judging regions by clicking numerator and denominator boxes
- [Mosaicking](mosaicking.md): joining overlapping images, aligning them with nudges and tie points, exporting
- [CRISM processing](crism-processing.md): TRR3 I/F to MTRDR-consistent, map-projected products with σ and provenance, from the dialog or the command line; the methods, uncertainty and measured agreement with CAT's MTRDRs, for citing
- [Optional features](optional-features.md): HyPyRameter band parameters, PyTorch classifiers
- [Kindle a Feature](kindle-a-feature.md): describe a feature and have your AI build it as a plugin

Something missing or unclear? [Open an issue](https://github.com/varda-pro/Varda/issues/new/choose).

## Words used in Varda

- **Image**: any raster Varda opens or makes, with one or many bands. Other
  software may call it a raster, a scene or (with many bands) a cube; Varda
  keeps "cube" only in names from the field, such as CRISM *ratio cubes* and
  ISIS *cube* files (`.cub`).
- **Band**: one layer of an image (one wavelength, or one product such as a
  class map). Other software may call it a channel or a layer. In the
  classifiers' guide, a network *layer* and the *channels* of a convolution
  are the networks' own terms.
- **Pixel**: one position in an image; its values across the bands are its
  **spectrum**.

## Typing numbers in settings

A number box takes what you type when you press Return or leave the box,
never partway through (typing 1309 doesn't run with 1, 13 and 130 first).
Text that isn't a number in the box's range, such as `1e309`, `0x10` or a
negative number where only positive ones are allowed, is refused whole: the
box keeps the value it had and says why in a tip under it, and Return
doesn't go on to press **Run**. A setting is only ever the value you see.
Settings that only matter once another is set (such as **Weight Image
Holds** before a **Weight Image** is chosen) are grayed out until then.
