# Changelog

Notable changes to Varda. Release notes on GitHub are generated from pull
requests; this file records what users need to know, especially changes that
can alter results.

## Unreleased

### Added

- **Varda has a DOI.** Releases are archived on Zenodo:
  10.5281/zenodo.23447208 cites every version, and v0.10.0 is
  10.5281/zenodo.23447209. Help → Cite Varda and CITATION.cff now include
  the DOI.
- **Choose a CRISM geometry file by hand.** Where Varda can't find a cube's
  IN (detector columns) or DDR/DE (incidence) file, **Choose Geometry File…**
  (CRISM Ratio workspace, ROI Manager), **Geometry File** (Ratio Cube) and
  **CRISM Geometry File** (Shadow Mask) point it to one; the file is checked
  (it must open, have the needed band and cover the image's pixels) and
  refused with the reason otherwise. A chosen IN file enables column lock at
  once; the chosen IN and DDR/DE files are kept with the image (one key each,
  `crismColumnsFile` and `crismIncidenceFile`), used by every feature and
  kept by the session. The automatic
  lookup now also follows provenance, so an IOVF-filtered (or cropped) cube
  finds the companions of the I/F cube it was made from. The Ratio Cube
  records the file and how it was found (`geometryRoute`) in its record,
  description and column table; the Shadow Mask's sun elevation now uses the
  incidence over the image's own pixels rather than the whole DDR.

### Changed

- **Open images use much less memory.** Loading an image now peaks at about
  its own size, plus GDAL's shared block cache (a 1.5 GB CRISM MTRDR took
  5.2 GB, now 2.6 GB, and loads in half the time), with the same values and
  no-data. Images in memory aim to take at most 40 % of RAM together; beyond
  that the least recently used are read on demand from their files, with
  the same results (see Installing → Memory).

### Fixed

- **No more errors or crashes after closing a workspace.** Loading an image
  after closing a workspace with an overlay bar, Find Data or 3D Terrain
  tab, the Find Data map moving as its window closed, or a Spectral
  Hourglass step finishing after its workspace was closed could call into
  the deleted window.

## 0.10.0 (2026-10-17)

### Added

- **Collapsing panels documented:** the pin button on any panel's title bar
  collapses it to an edge tab (see Sessions and saving), and a test guards it
  in every workspace.
- **CRISM Ratio workspace** (Workspace → New CRISM Ratio Workspace…): a
  browse image (such as a CRISM summary product) beside the I/F cube,
  linked by pixel or map location. Ctrl/⌘-click a numerator and a
  denominator box to plot the numerator, denominator and ratio spectra
  (always measured on the I/F cube), with a blandness readout for the
  denominator: for Generic or a target preset, every wavelength window's
  deepest continuum-removed dip (how deep, and where), its noise-aware
  limit and its slope, with failing windows shaded and their dips marked
  on the Denominator plot (floor, k, slope and the CO₂ exclusion
  editable). The
  denominator follows the numerator along its sensor column, and says so
  when it can't. S saves the pair as ROIs and P the numerator pixel as a
  point, named with the verdict and described with every check's value and
  limit (edited limits are marked, and kept by the session). The three plots always show the same
  wavelength range, and bad bands are left out of them and of the readout.
  See the CRISM ratio cubes guide.
- **Ratio Explorer numerator and denominator curves:** General Image
  Analysis and the dual-image workspace now plot the numerator and
  denominator box spectra next to the ratio, so you can see which one a
  feature comes from.
- **Ratio Cube (Column Denominators)** (Processing → CRISM): divides a CRISM
  I/F cube by one denominator spectrum per sensor detector column, read
  from the backplane next to the cube (an MTRDR's or TER3's IN file) or, for
  an unprojected cube such as a TRR3, from its own image columns (recorded),
  so the result can replace the
  I/F cube in other software. The denominator is the mean of the whole
  column or of its bland pixels, tested on runs of neighbors within the
  column: no window of the chosen check (Generic or a target preset, after
  Viviano-Beck et al. 2014) may have a continuum-removed dip deeper than
  both a floor and k × the spectrum's own noise, or too steep a slope, with
  the CO₂ residual, the detector join and everything past 2650 nm left out
  (defaults calibrated on a real MTRDR scene and recorded with the cube), with recorded fallbacks to
  neighboring columns or the whole column. It adds a second image of the
  pixels used and counts, and records each column's spectrum and standard
  deviation, per-band valid counts, the distinct sensor pixels and an
  effective sample size that accounts for map-projection oversampling, and
  which neighbor columns a fallback borrowed. The input's no-data is kept:
  missing values are never interpolated, counted or pooled. Saved, it
  carries a methods description in its header and a per-column table
  (`<name>.columns.csv`) beside it. See the new CRISM ratio cubes guide.
- **Find Data: basemaps, feature names and Go to, for finding your way
  around.** The map's **Basemap** now offers each body's global imagery
  (Mercury, Venus, the Moon, Mars, Phobos, Ceres, Vesta, Io, Europa,
  Ganymede, Callisto, Enceladus and Titan), chosen by default: a small
  global image bundled with Varda, so it works offline, with sharper tiles
  streamed from NASA's Solar System Treks as you zoom in (Equirectangular
  only; polar views reproject the bundled image). Tiles are fetched in the
  background, only for the view, and kept in a 200 MB cache
  (`basemap-tiles` in Varda's cache folder). **Names** labels features with
  their IAU names from the Gazetteer of Planetary Nomenclature (bundled),
  large ones zoomed out and smaller ones as you zoom in, without overlap.
  **Go to a feature…** completes on names and zooms to a view twice the
  feature's diameter across. The map shows the imagery's and the names'
  credits. Trek's Titan tiles are 180° off IAU longitudes, so Titan uses
  only the bundled image, corrected. See *Finding data*.
- **Spectral plots: pull a marker off the Y axis, and remove curves with
  the keyboard.** Dragging sideways off the Y axis pulls out a vertical
  marker that drops at the wavelength where you release it (released over
  the axis or outside the plot, it's canceled); a drag along the axis still
  moves Y. Backspace or Delete removes the curve you clicked (not while
  you're typing in the plot's sidebar); before, after a click on the plot,
  Backspace undid the last zoom instead.
- **Shadow Mask** (Processing → Image Math): a 0/1 mask of shadow from the
  image's brightness (the mean over a wavelength range, 1280–1380 nm by
  default, below a percentile of the scene or a value), from the terrain
  (a co-registered DEM and the sun: slopes facing away, by the local
  illumination cos(i), and cast shadows traced toward the sun), or both,
  with an optional buffer. It adds the brightness and cos(i) bands and
  records the thresholds used, the sun angles and where they came from
  (the image's metadata or a CRISM DDR's incidence angle, else you enter
  them) and the pixels each rule found.
- **FATT map exclusions:** the FATT detection map can leave out the pixels
  set in a mask (**Exclude**, e.g. a Shadow Mask) and pixels dimmer than a
  **Minimum Signal**, with every window touching them, and adds a **Mean
  signal** band, so you can see whether detections follow brightness. Dark
  and shadowed windows fit almost any target; the FATT guide explains why
  and how to guard against it. The pixels left out by each rule are
  recorded. (The map has one more band, the last.)
- **Spectral libraries: choose them, build your own, load and export them.**
  Every library list in Varda now draws from one set of libraries: the
  bundled CRISM MICA numerators and USGS minerals, your own, and libraries
  loaded from ENVI (.sli), CSV or USGS splib07 folders, which Varda
  remembers (and says so if a remembered file has gone missing). The
  spectral plots' **Library Spectra** overlay, previously USGS-only, picks
  from any libraries you choose, one or several, with a search by name; the
  Spectral Hourglass and FATT list them all, and a library loaded or made
  anywhere appears everywhere. **Add to Library…** saves a plotted pixel,
  box mean or ROI mean, or Spectral Hourglass endmembers, in a library of
  yours, recording its value units, image, pixel or region, pixel count, ±
  1 σ for a mean, the Varda version and date; spectra copied from other
  libraries keep their record. **Processing → Spectral Libraries…** opens a
  manager to browse (with a preview and each library's citation), create,
  rename, delete and export libraries (ENVI .sli or CSV, records included).
  Your libraries are saved as ENVI spectral libraries in Varda's app-data
  `libraries` folder, with a `.varda.json` sidecar for what ENVI headers
  can't hold. See the new guide, Spectral libraries.
- **Memory check before an analysis runs.** Varda estimates the memory a
  Processing-menu analysis may need (measured per analysis, as upper bounds)
  and compares it with the memory free at that moment. If it may not fit, it
  says how much it may need and how much is free, and asks; when it may need
  more than the computer has, the warning says it may freeze the computer and
  Cancel is the default. You can always run it anyway. Plugins can state
  their own needs (`memoryFactor`, `memoryFixed`).
- **IOVF maps what it replaced (adds a result):** by default IOVF also adds
  a one-band image counting the bands replaced at each pixel (**Replaced
  Count Image**; saved beside the result when the dialog saves it). Both
  results record the values replaced in each round, the totals, the pixels
  affected and why it stopped (`iovf` metadata, saved with the file). The
  Spectral Hourglass's Prepare page shows the same totals, and its results'
  records keep them.
- **Unmixing reports convergence:** unmixing results record their solver
  and, for the iterative (ADMM) solver used above ten endmembers, the
  iterations run and the share of pixels that converged (`unmixing`
  metadata, saved with the file); pixels left short are named in the
  description and the log.
- **MNF keeps its noise model:** the result's transform metadata (saved
  with the file) holds the noise covariance MNF whitened by, each band's
  noise σ, the noise estimate and its ROI classes, from the Processing menu
  and the Spectral Hourglass alike. **Inverse Transform** records the
  components kept, the count it suggested and the rule behind it
  (`componentSelection`).
- **ICA reports convergence:** the result's transform metadata records the
  iterations FastICA ran and whether it converged; when it ran out of
  iterations first, the description and the log say so.
- **K-Means records its clusters:** the result's metadata (`kmeans`, saved
  with the file) gives the iterations run, whether it converged, and each
  cluster's center spectrum, pixel count and RMS spread; a run that stops
  before converging says so in its description and the log.
- **Convolve to Sensor Bands records coverage:** each band's coverage (the
  share of its response the cube covers) is in the result's description
  and, for every sensor band, in its saved metadata (`sensorBands`).
- **Spectral Slope reports its uncertainty (changes results):** by default
  the result has two more bands, the slope's standard error (per nm, from
  each pixel's residuals) and the fit's R². Turn off **Fit Statistics** for
  the slope band alone, as before.
- **Documentation for reporting results:** every guide now ends with
  *Reporting your results*: what to state in a methods section (including
  settings the dialogs don't show), what each quality measure means and
  doesn't, and full references. New guides cover classification, target
  and anomaly detection, spectral analysis (continuum removal, derivatives,
  slopes), viewing and measuring (stretches, ROI statistics, the Ratio
  Explorer) and finding data, and *Reporting results from Varda* covers
  citing Varda and what results record about how they were made.
- **Image Properties** (right-click an image in the Image List →
  Properties…): see an image's file, format, size and map projection, and
  edit its name, no-data value (define one the file never declared, e.g.
  -999), wavelength unit, description, and each band's name, wavelength,
  FWHM and good/bad flag. Edits apply at once everywhere and are kept in
  sessions and packages; for ENVI images they can be saved into the header
  (backed up first).
- **Image Math** (Processing → Image Math): **Image Arithmetic** adds,
  subtracts, multiplies, divides, or takes the minimum or maximum of two
  images on the same grid (or an image and a number), band by band;
  **Threshold to Mask** makes a 0/1 mask from a band's range (inside or
  outside); **Apply Mask** keeps where a mask is set, where it isn't
  (inverse), or multiplies by it.
- **Band Math across images:** `i3_b1` is band 1 of the 3rd image in the
  Image List (on the same grid), e.g. `b1 * (i3_b1 > 0)`.
- **ROIs and masks:** **From Threshold or Mask…** in the ROI Manager makes an
  ROI class from a band's range (inside or outside, as ENVI's band threshold
  to ROI) or a mask image, one ROI per connected region, specks left out;
  **ROI to Mask** (Processing → Image Math) makes a 0/1 mask from ROI
  classes.
- **Full provenance in files:** exported results keep their whole record
  (process, every setting, inputs with their own records, Varda and plugin
  versions, the versions of the libraries or tools that did the work, the
  time) as JSON in the file's `VARDA` metadata domain (inside a GeoTIFF, in
  the `.aux.xml` beside an ENVI file; `gdalinfo -mdd VARDA file` shows it),
  with class names and colors, eigenvalues and the inverse transform,
  classifier figures, the mosaic recipe and FATT settings. Varda reads them
  back on open, so a reopened PCA/MNF/ICA result works with Eigenvalue Plot
  and Inverse Transform.
- **Workspace results record how they were made:** mosaics (recipe, grid,
  resampling, ISIS/ASP versions), their source maps, displacement fields
  (Auto Align settings), crop-tool crops (the window), and the Spectral
  Hourglass's MNF, PPI, maps and MTMF detections (wavelength window, IOVF,
  MNF noise estimate and components kept, PPI settings, endmembers and
  their pixels, map method and setting). The hourglass MNF image works with
  Eigenvalue Plot and Inverse Transform.
- **Downloads record their source:** each file downloaded in Find Data gets
  a `<file>.varda-source.json` (catalog, product, URL, size, checksum, time,
  Varda version), and results made from it name it as their source.
- **FATT → Export Table…** saves the ranked targets (nRMSE, angle) as CSV,
  with the settings that made the ranking.
- **Mosaic Auto Align keeps the phase-plane SNR:** each tie point refined
  by the phase-plane fit (Leprince et al. 2007) now carries its SNR (0-1)
  beside its match quality, in a **Phase SNR** column of the tie-point
  table, as each band's median in the Auto Align results, in the recipe
  (sessions, the mosaic's metadata and provenance) and as a fourth band of
  the Displacement Field. Points without refinement (manual, ORB, SIFT,
  LoFTR, ASP) have none and show it blank.
- **Mosaic radiometry reports how well it fit:** after balancing, the
  Radiometry tab shows each image's fit RMS (per band on hover, in data
  units, with the number of samples) and every overlap's mean absolute
  difference before and after; bands left uncorrected because too few
  pixels overlap are named. All of it is saved with the session and the
  recipe in the mosaic's metadata and provenance.
- **Mosaic source maps carry each source's alignment quality:** the
  exported source map's metadata and provenance list, per class, the
  image's transform, tie points (enabled and kept), RMS in pixels and
  median phase-plane SNR.
- **3D terrain overlays show what Auto Align matched:** the Overlays panel
  names the image and band matched (its wavelength or name, and its SNR)
  and the tie points' median phase-plane SNR, and the overlay's saved
  alignment records them.
- **3D terrain marks filled gaps:** **Show filled gaps** tints the points
  whose heights Fill gaps invented, in the view, stills and exported web
  pages (whose panel and data also give the count, the method and a mask of
  them). Sessions record the count and method; Export for 3D Printing
  states the count in its dialog, its status line and the STL's header.
- **Classification accuracy.** **Accuracy Assessment** (Processing →
  Classification) compares a class map with ROI classes drawn as ground
  truth, matched by name: confusion matrix, overall accuracy with a 95%
  Wilson interval, kappa, and each class's producer's and user's accuracy
  (with intervals) and omission and commission error. Its result is an
  error map (Correct / Incorrect over the reference pixels). The MLP, CNN,
  ViT, Maximum Likelihood and Mahalanobis Distance classifiers can **Hold
  Out** a fraction of each class's ROIs (whole ROIs, picked with the Seed;
  Maximum Likelihood and Mahalanobis gain a Seed) and report their accuracy
  on them. **Accuracy Report…** shows either report, opens when such a run
  finishes, and saves it as CSV with a header naming the class map, its
  provenance and the reference ROIs. Reports, and every classifier's
  per-class training ROI and pixel counts, are kept with the result, in
  exported files and in its provenance record (under `results`).

### Changed

- **Find Data has its own top-level Data menu** (Data → Find Data…), no
  longer under Workspace: it searches for and downloads data rather than
  opening a view of your images. It opens the same Find Data workspace.
- **Spectral plots remember which libraries you chose.** The libraries you
  turn off under **Libraries (n of m)** are saved with each plot in
  sessions (General Image Analysis, Dual Image and Spectral Hourglass), and
  new plots start with your most recent choice, also after a restart (kept
  in `preferences.json` in the libraries folder). Libraries added later are
  still on, and renaming a library of yours keeps it off. Older sessions
  open with every library on, as before.
- **Scrolling over a spectral plot's Y axis stretches the spectra, and the
  stretch stays**, as in the FATT window. Over an axis the wheel zooms only
  that axis, about the cursor. In **Fit Y To X Range** a Y stretch, or a
  drag on the Y axis, used to be refitted away at once; now it switches the
  plot to **Manual**, keeping the view until **Fit to Data**. The plot's
  hint mentions both.
- Results record their whole chain of inputs, not only the immediate one,
  and the one-line description in exported files names the plugin.
- **Help → Copy System Info** includes Spectral Python's version.
- A `GDAL_DRIVER_PATH` exported by another installation (e.g. a conda base
  environment) is ignored, as `PROJ_DATA` and `GDAL_DATA` already were.

### Fixed

- **ENVI exports keep their description in the header.** GDAL wrote the
  file's path into the `.hdr`'s `description`, so ENVI and other software
  never saw the image's description or how Varda made it (Varda itself read
  it from the `.aux.xml`). The header now has it, and names the files that
  must travel with the image.
- **CRISM companion files are found by their real names:** a TRR3's DDR
  (`…_if166l_trr3` → `…_de166l_ddr1`) and an MTRDR's DDR (`…_de166l_mtr1`)
  are now found, also with ODE's upper-case names on case-sensitive disks,
  so the Shadow Mask reads the incidence angle from them. Column lock (ROI
  Manager, Ratio Explorer) uses the IN file's IR Sample band, or an
  unprojected CRISM cube's image columns, and is no longer offered for a
  companion that has no detector columns.
- **Tool hints wrap in narrow views** instead of running off the right edge,
  including after the view is resized.
- **Map links no longer trip a NumPy deprecation** when one point is mapped
  across map projections (every Map-link click in the CRISM Ratio and Linked
  Images workspaces), which a future NumPy would turn into an error.
- **A session with an unknown link mode** in a Linked Images or CRISM Ratio
  workspace now skips that workspace with a warning instead of failing to
  open.
- **Ratio Explorer, column lock:** placing a new numerator now moves the
  existing denominator into the new numerator's detector column (keeping its
  row and box size), where before only a denominator click was snapped.
- **A Shadow Mask can be applied, and a co-registered DEM chosen.** Apply
  Mask didn't offer a Shadow Mask made from the same image (it has 2 or 3
  bands), and the Shadow Mask's DEM dropdown didn't offer a DEM that was
  co-registered but on another grid (a 6 m CTX DEM under an 18 m CRISM
  image), while it did offer the 2-band Shadow Mask itself. Now Apply Mask,
  a FATT map's Exclude and the ROI Manager's From Threshold or Mask… take a
  mask with any number of bands (its first band; Apply Mask still applies
  one with the image's bands band by band), and the Shadow Mask's DEM is any
  single-band image. These inputs may be on another grid, in any map
  projection, if they are georeferenced and overlap the image: they are
  resampled onto the image's grid (masks by nearest neighbor; a DEM averaged
  when finer than the image, bilinear when coarser), and the result's
  description and metadata record it (e.g. "ctx_dem resampled from 6.0 m to
  18.0 m pixels (average)"). An input on the image's grid is used exactly as
  before. Inputs that combine values pixel for pixel (Image Arithmetic,
  Band Math's other images, PCA/MNF weights, ROI zonal columns, overlays and
  Terrain drapes) still need the same grid.
- **Image dropdowns say which images they leave out, and why**, in a note
  under the dropdown ("Not listed: ctx_dem (different pixel grid: 6.0 m vs
  18.0 m pixels; this needs the same grid)", "doesn't overlap this image",
  "3 bands; this needs 1 or 30"); images used to be left out silently. The
  overlay bar shows a count, with the details in its tooltip, and Band Math
  lists the images it can't use.
- **The FATT window's plot fits each mineral you pick**, also after you've
  zoomed or panned it; a zoom used to turn the fitting off, so the next
  mineral could be drawn off the plot.
- **The FATT detection map no longer runs out of memory.** It copied every
  window of the image into one array before fitting any (about 15 GiB per
  window shape for a CRISM scene), which crashed a 16 GB laptop and the
  programs around it. Windows are now cut out in chunks, keeping the map's
  working memory near 1 GiB whatever the image size. Results are unchanged,
  bit for bit.
- **Find Data's ODE searches with no matches** said `'str' object has no
  attribute 'get'` instead of "Nothing found here.": where nothing matches,
  ODE answers with a message in place of the product list, which Varda
  didn't expect.
- **Odd-only windows record what they use:** the CNN and ViT **Patch Size**
  and the **Spatial Entropy** window silently raised an even value by one,
  so a result's record could say 4 where 5 was used. They now take odd
  values only.
- **Linked Images, bits of negative values:** a signed bit mask's negative
  values showed the wrong bits (they were wrapped to 64 bits). They're now
  read as the unsigned integer of the image's own width (an int16 −1 is
  sixteen bits set), as **Decode Bit Mask** reads them.
- **Pixels inside ROIs of two different classes** are now left out of
  every supervised classifier's training (and validation), and counted in
  the result. Before, the MLP, CNN and ViT trained on them once per class
  with conflicting labels, and Maximum Likelihood and Mahalanobis Distance
  gave them to the later class. **This can change results** where ROIs of
  different classes overlap; without overlaps (and with Hold Out 0) results
  are unchanged.
- **Train ViT on ROIs** says, before running, when Embedding Size isn't a
  multiple of Attention Heads, instead of failing partway.
- **PDS products open through their labels.** After a Find Data download,
  Varda opened a product's raw data file (such as a CaSSIS `.DAT`), which
  has no header and failed to open; it now opens the PDS4 `.xml` or PDS3
  `.lbl` label, and **File → Open** lists labels. For PDS4, values outside
  the label's valid range are no-data, as its special constants already
  were.
- **Edge Detection, Scharr (changes results):** Scharr was √2 times the
  gradient magnitude; it's now in band units per pixel, like Sobel, Prewitt
  and Roberts. Scharr results from earlier versions are √2 (about 1.414)
  times the new ones.
- **Terrain on an image without georeferencing:** distances are in pixels,
  and the result now says so: its description, and slope's unit ("degrees
  (rise per pixel)") and curvature's (per pixel²). Values are unchanged.
- **Terrain roughness units:** labeled with the Z Factor when it isn't 1
  (e.g. "elevation units × 1000 (Z Factor)"), not just "elevation units".
  Values are unchanged.
- **Decode Bit Mask on signed integers (changes results):** negative values
  were widened to 64 bits, so a 16-bit −1 counted 64 bits set and showed
  bits above 15. Each value is now read as the bits of its own width (a
  16-bit −1 is bits 0–15). Unsigned and non-negative images are unchanged.
- **Resample (changes results):** shrinking with bilinear or cubic
  interpolation now anti-aliases first (a Gaussian of σ = (old / new − 1) / 2
  pixels, as scikit-image's resize), so fine detail no longer aliases and
  pixel noise averages down. Nearest neighbor and enlarging are unchanged.
  The description now records the actual factors along rows and columns
  (pixel counts are rounded) and the σ, not the factor typed.
- **3D Terrain height unit (changes the view and STL files):** heights were
  always taken as meters, so the exaggeration, the "vertical exaggeration"
  note and 3D prints were wrong for DEMs in kilometers or feet. A new
  **Height unit** setting (meters, kilometers or feet; saved with the
  session) converts them, and starts from the DEM's vertical unit when its
  CRS declares one. The base depth and color bar show the unit.
- **3D Terrain point positions (changes the view and STL files):** the
  grid's points were spread evenly from edge to edge, misplacing them by up
  to half a block of DEM pixels (more with a partial edge block). Each now
  stands at the center of the block it averages, in the view, web pages
  and STL files, so drapes, overlays and lines sit where they belong. A
  print's width is now between the outermost points.
- **3D Terrain overlays:** the RMS and Shift E, N are labeled with the DEM's
  map unit (° for a geographic DEM) instead of always "m"; values are
  unchanged. Auto Align against the DEM hillshade now shades the band shown,
  not always the first (changes alignments for multi-band DEMs).
- **Profile CSV:** a commented header now records the image (name and
  file), its CRS (name, EPSG code, WKT), the distance unit and how it was
  measured, the band and its unit when known, and the Varda version.
  Distances and map coordinates are written in full; they were rounded to 6
  significant figures (10 m steps past 1,000 km).
- **Linked spectra CSV:** now gives each image's pixel (`col`, `row`) and
  the cursor's map position (`map_x`, `map_y`), and says what `x` is
  (`x_unit`: a wavelength unit, or "band"), with each image's CRS in a
  commented header. The columns changed: scripts reading the old
  `image, x, value` file need updating.
- **Linked Images warnings:** a notice across the top of the workspace now
  names images linked by map location that have no georeferencing (read by
  pixel), and images linked by pixel that aren't on the first image's grid.
These fixes change results; rerun affected analyses before reporting them.

- **Bad bands are left out of analyses that work across bands.** RX
  (global and local), ACE, Matched Filter, Maximum Likelihood, Mahalanobis
  Distance, K-Means, the MLP, CNN and ViT classifiers, Spectral Angle Mapper,
  Linear Spectral Unmixing, MTMF and Pixel Purity Index (Processing menu),
  Spectral Derivative and Spectral Entropy used the image's bad bands as
  values filled from their neighbors. That made covariances nearly singular
  (unstable detector and classifier scores) and counted the neighbors twice.
  They now use only the good bands, as the Spectral Hourglass and the
  transforms already did. **Spectral Derivative** results now have only the
  good bands (with their wavelengths and FWHM).
- **IOVF replacements** were pulled toward the smooth reference spectrum: the
  weights of flagged and no-data neighbors were dropped without renormalizing
  the rest. A replaced value is now the weighted average of the neighbors it
  uses (Phillips et al. 2024), or the reference value when none is left.
  This changes IOVF results, in the Processing menu and in the Spectral
  Hourglass. **Bad bands** in an IOVF result now keep the input's values
  instead of values filled along the spectrum.
- **ACE** gave no data (0/0) at a pixel exactly at the background mean; it
  now scores 0 there.
- **FATT Detection Map:** ticking a target that covers fewer bands shrank the
  bands used for every target, changing their nRMSE. Each target is now
  fitted over the bands it covers, as in the ranking table; the record gives
  each target's band count.
- **MNF with a homogeneous-region noise estimate** used only the first
  ticked noise ROI class; it now pools every ticked class. With a
  neighbor-difference estimate, no noise ROI class is recorded.
- **RX Anomaly (Local):** even window sizes were silently raised by one (and
  the outer window enlarged), so the result's record kept sizes that weren't
  used. The windows now take odd sizes only, with the outer at least two
  pixels larger than the inner, so the recorded sizes are the ones used.
- Spectral Slope's description quoted a 1.8–2.5 µm example that didn't
  match its 1000–2000 nm defaults.
- Inverse Transform's **Output** choices show their full labels
  ("Denoised image (inverse transform to the original bands)", "Only the
  chosen components (as component bands)").
- **Random processes are reproducible.** The Pixel Purity Index (Processing
  menu and Spectral Hourglass) and Train MLP, CNN and ViT on ROIs have a
  **Seed** setting (default 0), recorded with the result: the same seed and
  settings now give the same result. **Results change:** PPI counts and
  CNN/ViT maps differ from earlier versions, and the PPI no longer gives
  different counts on every run (earlier runs used an unrecorded random
  seed and can't be repeated). The MLP gives the same result as before at
  seed 0.
- **PPI counts no longer depend on image size.** The random projections
  depended on how many valid pixels the image had (through the batch size);
  now the seed and the number of iterations alone fix them.
- **Spectral Hourglass Run More** continues the same seeded run, so
  10,000 iterations then 10,000 more equal one run of 20,000, and it is
  turned off once the threshold, seed or number of MNF components changes
  (it used to add counts made with different settings together). The PPI
  image's description and the saved workflow record the seed and total
  iterations.
- **CNN and ViT training** seeds the network's starting weights (they
  weren't seeded, so every run differed) and asks PyTorch for deterministic
  algorithms. Results record the device (cpu, cuda or mps) and PyTorch
  version; GPU results can still differ slightly from CPU ones.
- **No Stretch** draws again (the view failed to draw). Values outside 0–1
  from any stretch now saturate at black or full brightness instead of
  wrapping around to the other end of the 8-bit range.
- **Spectrum plot CSV export** writes each curve's own values: a library
  spectrum scaled and offset to overlay another curve was exported scaled.
  The scale and offset shown are now recorded in comment lines (`#`) at the
  top. Box and ROI means also export their ±1 standard deviation and the
  pixel count n behind each band, which were left out.
- **ROI and box statistics** with no data (an empty footprint, or a band
  that's no-data in every pixel) are now missing (NaN) instead of 0, so an
  empty ROI or box no longer gives a ratio of 0. The per-band pixel count n
  is now available.
- **Find Data's To date** now includes that whole day (to 23:59:59 UTC) for
  STAC catalogs and footprint files, as it already did for ODE. It used to
  stop at the start of the day, leaving the day out.
- **Laptop screens:** panels no longer cut off their text at small sizes.
  Docked panels used to scroll sideways, out of sight, when they were wider
  than their dock: in **Find Data** the catalog description, the Collection
  box, the dates, the Preview, and the Results and Downloads buttons were cut
  off. Now every dock is at least as wide as its panel can get, panels scroll
  down rather than sideways, button rows wrap onto a second line, long names
  in combo boxes end in "…" with the full name in the tooltip, and
  descriptions wrap. Find Data, General Image Analysis, Dual Image, Linked
  Images, Mosaic, Spectral Hourglass and 3D Terrain all fit a 1280 × 800
  screen; analysis dialogs and Optional Features scroll when they're taller
  than the screen; number fields in analysis settings are wide enough for
  their values.

## 0.9.0 (2026-10-16)

### Added

- **3D Terrain workspace** (Workspace → New 3D Terrain Workspace): a DEM as
  a lit surface with soft shadows, draped with shaded relief, elevation
  colors or an image on its grid (e.g. a DTM's orthoimage); vertical
  exaggeration, sun azimuth and elevation, saved camera views, stills up to
  16384 px (transparent background optional), and **Export Web Page**: a
  self-contained interactive HTML file for any browser. Built on three.js in
  Qt WebEngine, which Varda now includes.
  - **Overlays:** drape images that aren't on the DEM's grid (e.g. a CRISM
    parameter composite over a CTX DEM), each with its display settings and
    opacity, aligned by **Auto Align** against the DEM's orthoimage or a
    hillshade (the Mosaic workspace's matching; translation by default, up to
    polynomial and thin-plate-spline warps) and a manual shift in meters.
    Saved with the session and included in stills and web pages.
  - **Block diagrams and 3D printing:** **Sides** stands the surface on walls
    down to a base; **Export for 3D Printing** saves a watertight STL in
    millimeters (width, exaggeration, base and detail chosen, the printed
    size shown). **Fill gaps** fills no-data voids inside a DEM for both.
    **Edges** makes the outline smooth (the default: no more staircase
    sides on tilted footprints), stepped, or trimmed to a rectangle, and
    **Side color** chooses the walls' color.
  - **Figures:** a north arrow, a scale bar (at the view's center, with the
    exaggeration) and a color bar for elevation colors, on the view and in
    stills; lines from GIS files draped on the surface; and **Export
    Animation**, a flyover as an animated WebP, a GIF or PNG frames.
  - **Overlay Display** panel: the selected overlay's mode, bands (any three
    parameters of a cube by name), stretch and color map, with histograms
    whose stretch ranges can be dragged, in a dockable panel; these
    settings are now saved with the session.
  - **Lines from other workspaces:** **Add Lines** lists the lines drawn in
    open General Image Analysis workspaces (on any georeferenced image of the
    DEM's body), to drape all or one at a time, with no file in between; a
    **Profiles** panel plots the DEM along the lines.
  - **Alignment transfer:** **Match using** runs Auto Align on another image
    with the overlay's georeferencing (e.g. a CTX-like scene simulated from
    the same CRISM observation) and moves the overlay (e.g. its parameter
    map) by the result; **Copy** and **Paste** give another overlay the
    same alignment.
- **Linked Images workspace** (Workspace → New Linked Images Workspace…):
  any number of images side by side, linked by pixel or by map location,
  panning and zooming together. Ctrl+click or Ctrl+drag puts one cursor on
  every view and plots every image's spectrum there, each in its own plot
  (a product, its counts, its flags); a line drawn on any view gives every
  image's profile. Made for checking pipeline products, such as MISE's
  synthetic data, against each other. Every view and panel is a dock to
  drag, tab or float, and the Display panel shows each image's histograms.
- **Bit masks:** **Decode Bit Mask** (Processing → Statistics) turns a
  mask (a pipeline's per-frame exclusions, a QA band) into the number of
  bits set, one bit, or any of several, band by band; in Linked Images a
  mask shows as bits by wavelength at the cursor. On MISE's example
  products, EXCLUDE's set bits reproduce COUNT exactly.
- **Wavelengths and weights for PCA, MNF and ICA** (Processing →
  Transforms): each takes a wavelength range (as the Spectral Hourglass
  does; bands marked bad are always left out) and, optionally, weights: an
  image on the same grid giving each value a weight (frames used, or
  uncertainties as 1/σ²). Values with no weight (flagged, missing) move
  neither the components nor a pixel's scores: Delchambre's (2015)
  weighted covariance, with each pixel's scores a weighted least-squares
  fit (MNF's noise from weighted neighbor differences).
- **Convolve to Sensor Bands** (Processing → Spectral Parameters): a cube
  with wavelengths in another sensor's bands, each weighted by its spectral
  response (bad bands and no-data left out, uncovered bands skipped):
  the measured responses of Landsat 8 OLI, Landsat 9 OLI-2 and
  Sentinel-2A/B/C MSI (NASA/USGS and ESA, bundled), CTX's measured passband
  (Bell et al. 2013), approximate HiRISE and THEMIS VIS bands, a response
  file, or typed center/FWHM bands. CRISM reflectance as a CTX scene aligns
  to a CTX orthoimage near the known offset, where the cube's own bands
  land kilometers off.
- **Package Project** (File → Package Project…, replacing Save Session
  (Self-Contained)): one `.varda.zip` with everything a project needs to
  open on another computer: the session, every image with its companion
  files (ENVI headers, PDS labels, `.aux.xml`, world files), files
  workspaces use, results held in memory, and a manifest of every file's
  size and SHA-256 with the Varda version, for replicability. Packages over
  1 GB ask first; it's written in the background.
- **Subset with a preview** (Processing → Geometry → Subset): drag a box
  on the image to choose the part to keep, and drag ranges on the box's
  mean spectrum to choose the bands; both stay in step with the typed rows,
  columns and bands.
- **Workspace tabs work like a browser's:** drag them into order (sessions
  keep it), drag one out of the tab bar to open it in its own window, and
  middle-click to close.
- **Lines and profiles** (General Image Analysis): draw straight lines,
  polylines or freehand lines with **Draw Line**, listed in the new **Lines**
  tab (rename, recolor, delete, export to GIS files) and saved with the
  session. The **Profiles** tab plots a band along them, e.g. a topographic
  profile of a DEM: distance in meters (geodesic for longitude-latitude
  images), the pointer's spot marked on the image, the vertical
  exaggeration, and export as CSV, PNG, SVG or PDF.
- **Overlay colors:** a single-band overlay's **Colors** menu chooses its
  color map (Viridis, Magma, Inferno, Plasma, Cividis, Turbo, Gray or a
  diverging Blue-White-Red), reverses it, and stretches it over the full
  range or 2-98 % or 5-95 %. Clicking a class's swatch in the legend changes
  its color; the new color is saved with the result and every view showing it
  follows.
- **Stretch From** (General Image Analysis, Render Settings): the stretch and
  the histogram can take their statistics from the full image, the main view
  or the zoom view, as in ENVI; the stretch then applies to the whole image.
  It follows the views as you pan and zoom.
- **Kindle a Feature checks before it builds.** If Varda already does what
  you describe (a menu item, an installed plugin, a file format or catalog),
  the AI says what and where instead of building; **Build Anyway** builds it
  regardless. Requests a plugin can't do (changing the interface or built-in
  features, reworking the app, reaching the network or other programs) are
  declined with the reason and the closest thing it could build.
- **Find and Fetch Data** (Workspace → Find and Fetch Data…): search catalogs
  for observations of a place and see their footprints on a map, over a
  loaded image and vector overlays (a rover traverse, a geologic map), with
  the results in a sortable table and a preview. Varda ships three catalogs:
  STAC (USGS Astrogeology for Mars, the Moon and Europa, and Earth Search, by
  default), the PDS Orbital Data Explorer, and footprint files (offline);
  plugins can add more with a `registerCatalogs` hook.
  - **Downloads:** add checked products to the download list, choose their
    files (the essential ones come checked) and download them into one folder
    per product, with progress, Cancel and resuming; finished data opens in
    Varda. The list also exports as a CSV or a script of resumable `curl`
    commands.
  - The download list's columns can be dragged wider to show full names.
    **Check** checks all files, only the essential ones, none, or every file
    of a type (data, labels, browse images); **Select** picks products by
    status (e.g. the failed ones). Check, Download and Remove act on the
    selected products, or on all when none are selected.
  - Catalogs that need a login ask for it once and keep it in the system
    keychain; the STAC APIs searched can be edited (STAC APIs…).
  - **Map projections:** equirectangular, or north or south polar
    stereographic, with a graticule that gets finer as you zoom in and the
    pointer's latitude and longitude below the map. Basemaps, overlays and
    the search box follow the projection; a view around a pole searches
    every longitude.
  - **Finding a collection:** the Collection menu opens a search over the
    collections, grouped by body and then by mission and instrument with
    counts; every word typed must match (e.g. "mars crism", "mars express",
    "kaguya dtm"). ODE's mission abbreviations are spelled out for searching.
  - **Selecting on the map:** click selects the footprints under the
    pointer, Shift-click adds, Cmd-click (Ctrl-click) removes, and
    Shift-drag adds every footprint a box touches. **Show only selected**
    narrows the results table to the selection.

### Fixed

- **Stretches on whole-number images** (DN products such as MISE's int16
  aggregate): dragging a histogram's stretch range, and the Linear,
  Square Root and Logarithmic Percentile stretches, turned the image
  black, as the stretched fractions were stored as integers (all 0).
- A product file that can't be downloaded (e.g. a catalog's broken link,
  404) no longer stops the product's other files: they download, the product
  is marked failed with the reason, and each missing file says "not
  downloaded". The 404 message now says the archive doesn't have the file.
- Collections with the same title (USGS's HiRISE DTMs and their test copy)
  show their ids, so they can be told apart.
- Ratio Explorer's numerator and denominator boxes now stay on their pixels
  when the view is panned or zoomed, instead of moving with it.
- Closing a Find Data workspace while it was drawing a basemap, listing
  collections, searching or downloading could raise an error when the work
  finished; such results are now dropped.
- Very large images read on demand now serialize every read (windows, pixel
  spectra, whole-image reads), not only band reads, so the display and an
  analysis can't read the same file at once.
- **Find Data basemaps** were drawn mirrored across their diagonal (in the
  right place, but transposed); they are now north up as in the image.
- Images loaded after a Find Data workspace opened (a finished download, for
  instance) now appear in its Basemap and Area image lists.

## 0.8.0 (2026-10-15)

### Added

- **Auto Align in the Mosaic workspace.** Finds tie points between an image
  and the images it overlaps:
  - phase-correlated tiles on the bands with the best SNR, spread across the
    spectrum;
  - refined to about 1/50 px with the phase-plane fit of Leprince et al.
    (2007), the method behind COSI-Corr, re-implemented from the paper;
  - ORB features when rotation or scale differ.

  The points are fitted robustly (RANSAC), with outliers shown. A per-band
  report lets you drop a band that disagrees. The tile shifts can be added
  to the image list as a displacement field (east, north, quality).
- **Polynomial and thin-plate-spline alignment** for mosaic images whose
  distortion varies across them (2nd or 3rd order polynomials, and a
  thin-plate spline for local warps), fitted robustly like the other
  transforms and saved with the session.
- **Radiometric balancing in the Mosaic workspace** (the Radiometry tab), per
  band, from the overlaps: linear to held images (robust regression), a
  global least-squares solve like ISIS equalizer (which refuses images not
  connected to a held one), or histogram matching (labeled visual). None
  remains the default, keeping every spectrum exact.
- **Seams in the Mosaic workspace** (the Seams and Blending tab): split each
  overlap down its middle (Voronoi) or along the path where the images
  differ least, instead of letting the top image win. Seams are outlined on
  the mosaic, can be edited by dragging, are kept in map coordinates and
  saved with the session; spectra stay exact.
- **Blending for visual mosaics:** feathering over a chosen width, or
  multi-band (pyramid) blending, across the seams. Blended mosaics are
  labeled visual; hard seams remain the default.
- **The Ames Stereo Pipeline in Auto Align.** Optional Features finds an ASP
  you already have (PATH, a conda environment named asp,
  ~/StereoPipeline-*) or downloads ASP 3.7.0, verified against its
  published checksum (macOS and Linux). Auto Align can then match each
  chosen band with ASP's image_align and fit the pooled matches like its
  own.
- **ISIS, when installed:** Processing → Geometry → Map-Project with ISIS
  (cam2map) projects raw camera cubes so they can be mosaicked (nearest
  neighbor by default), and the Radiometry tab can compare its balance with
  ISIS equalizer's and use it. Varda finds ISIS through ISISROOT or a conda
  environment, and never downloads it.
- **LoFTR and SIFT matchers in Auto Align**, run in the PyTorch environment
  (Optional Features): LoFTR is a learned matcher that finds matches where
  hand-crafted features struggle. The PyTorch environment now includes
  kornia and OpenCV for them; an existing install offers an Update.
- **Mosaic workspace.** **Workspace → New Mosaic Workspace…** joins
  overlapping images (spectral cubes or grayscale) with the same bands and
  projection. Order the images (the top one wins where they overlap), align
  each by nudging it with the arrow keys or by fitting a translation, rigid,
  similarity, affine or projective transform to tie points, and export. The
  default nearest-neighbor export keeps every spectrum exact; an optional map
  shows which image each pixel came from. See the
  [mosaicking guide](docs/user-guide/mosaicking.md).
- **Factor Analysis Target Transformation (FATT).** **Processing →
  Endmembers → Factor Analysis Target Transformation…** finds the factor
  space of an image's spectra (covariance or Hysime, with Malinowski's
  indicator function to choose the number of factors) and ranks library
  spectra by how well it reproduces them (normalized RMSE, as Lin, Tarnas et
  al. 2021). **Map Ticked Targets…** maps where they fit with DAFA/TT moving
  windows. Results are candidates to confirm. See the
  [FATT guide](docs/user-guide/fatt.md).
- **Kindle a Feature works with OpenAI and local models.** Plugins → AI
  Assistant Settings offers OpenAI and any OpenAI-compatible server,
  including Ollama and LM Studio on your own computer (no key needed), with
  Load Models to list a server's models. Each provider keeps its own key in
  the keychain. See [Kindle a Feature](docs/user-guide/kindle-a-feature.md).

### Fixed

- **Mosaics no longer have gashes where an image misses a few bands.** A
  pixel missing data in some bands (a bad CRISM detector column, say) was
  dropped whole, leaving holes, or the image below, in the export (the
  display-band preview didn't show it). A pixel now counts when at least half
  its bands have data, as analyses treat it, and is copied as measured;
  radiometric balancing, seams and feathering handle missing bands per band.
- **The histogram keeps its zoom when the stretch changes.** Dragging a
  stretch region (or changing the stretch) snapped the histogram back to the
  full range, which also undid a span you'd just zoomed to.

## 0.7.2 (2026-10-15)

### Added

- **Cropping, to work on part of an image.** The **Crop** tool in an
  image's toolbar adds a cropped copy of the box you drag to the image
  list. **Geometry → Crop to ROI** crops to ROI classes, optionally making
  pixels outside them no-data. Run any analysis or workspace (such as the
  Spectral Hourglass) on the crop.
- **Pixel Select windows.** The Pixel Spectra panel has Window Width and
  Height settings (like the Ratio Explorer's boxes): 1×1 selects one pixel;
  larger plots the region's mean spectrum with a ±1 σ band (no-data pixels
  left out), and the window is outlined while you Ctrl-drag.

### Changed

- **Plotted spectra are marked with circles** in their curve's color
  (a region with its outline), and selecting a spectrum on the plot
  highlights its marker on the image. Markers follow a change of color.

## 0.7.1 (2026-10-15)

### Changed

- **Filtering is split into Spatial and Spectral submenus.** The spatial
  filters are under Processing → Filtering → Spatial; IOVF and spectral
  filters from plugins are under Filtering → Spectral. Plugins can nest
  their own submenus with a "/" in their category ("Filtering/Spectral").

## 0.7.0 (2026-10-15)

### Added

- **Image processing tools** (see the user guide's Image processing page):
  - Filtering: Smoothing, Sharpening, Edge Detection, Morphology, Speckle
    Filters (Lee, Kuan, Frost), Frequency Filter (FFT) and Custom Kernel,
    on all bands or one.
  - Statistics: Local Statistics and Co-occurrence (GLCM) texture.
  - Transforms: Color Transform (RGB ↔ HSV/HLS), Decorrelation Stretch and
    Saturation Stretch.
  - Classification: Density Slice, Majority/Minority Filter, Sieve and
    Clump for class maps.
  - Topography (new): Terrain, which computes slope, aspect, hillshade,
    curvature and roughness from a DTM.
  - Geometry (new): Resample, Rotate / Flip and Subset, keeping the
    georeferencing and band metadata.

## 0.6.0 (2026-10-14)

### Added

- **Update check.** A few seconds after starting, Varda asks GitHub whether
  a newer version is out, and offers to open its release page (or skip that
  version). **Help → Check for Updates…** checks at any time, and the check
  at startup can be turned off.
- **Spectral Hourglass: Draw on Image.** Draw shapes on the scene to add the
  pixels inside to the active endmember (Shift removes them), alongside the
  n-D lasso.
- **Kindle a Feature.** Describe a feature in plain language and your own AI
  (Anthropic, with your API key in the system keychain) builds it as a
  plugin. Varda checks it on a synthetic image and a crop of yours in a
  separate process, then shows a summary, where it appears, warnings, the
  checks and the code to Enable, Ask for Changes or Discard. Only the
  description, the plugin docs and the image's metadata are sent, never its
  pixels. Plugins → Kindle a Feature… and AI Assistant Settings….

### Fixed

- **A closed image list can be reopened**, from the new **View → Image
  List**, which is checked while the list is showing.
- **Manage Plugins has a Close button** (on macOS it had no way to close
  except Escape).
- **Spectral plots follow a spectrum taller than the view.** Hovering a point
  in the Spectral Hourglass n-D view didn't grow Y for a pixel brighter than
  the plotted spectra, in Auto or Fit Y To X Range; a pixel with bad (NaN)
  bands froze the range entirely.

## 0.5.2 (2026-10-14)

### Fixed

- **macOS: double-clicking the disk image showed nothing.** The 0.5.1 image
  mounted without opening a window. The image now opens a window with
  **Varda** and the **Applications** shortcut, like other apps' disk images.

## 0.5.1 (2026-10-14)

### Fixed

- **macOS: the download didn't open.** macOS blocked each library inside the
  downloaded folder ("library load disallowed by system policy"), and
  right-click → Open couldn't help. The macOS download is now a disk image
  with **Varda.app**: drag it to Applications and approve it once (see the
  install guide).
- **Windows: no more console window** behind Varda; it's a normal windowed
  app now.
- **Linux:** the download is a `.tar.gz` (it keeps file permissions), with
  an `install.sh` that adds Varda to your applications menu.
- Every release build now unpacks each platform's actual download and
  self-tests that copy before publishing.

## 0.5.0 (2026-10-14)

### Changed

- Varda's home is now **github.com/varda-pro/Varda** (the old address
  redirects), and the Help menu links there.
- On-screen text uses American spelling throughout.

### Added

- **Plugins work.** A new **Plugins** menu installs plugins from a folder or zip
  and manages them (enable, disable, reload, remove, and see why one didn't
  load), in the downloadable app as well as from source. Plugins add
  analyses to the Processing menu or new file formats, using the stable
  `varda.api`. See `docs/plugins/authoring.md` and the band depth example.
- **Remove images from the project:** right-click in the Image List →
  **Remove from Project…**, or press Delete. Workspaces showing them close,
  and Varda asks first when that, or losing an unsaved result, would happen.

### Fixed

- The New Dual Image Workspace and New General Analysis Workspace dialogs
  could vanish a moment after opening.
- The large view now follows the overview's region box to its final
  position; it lagged one change behind.
- Fit Y To X Range now fits ROI spectra that have their ±1σ shading.

## 0.4.0 (2026-10-13)

### Changed: results may differ from earlier versions

- **Wavelength units are read, not guessed.** Varda now reads each file's
  declared wavelength unit (nm, µm or cm⁻¹) and does every calculation in
  nanometres. Before, several analyses guessed the unit from how big the
  numbers were ("below 10 means µm"). Affected:
  - **Spectral Slope** now works on µm images (it found no bands before) and
    gives the same result as on the nm version of the same image.
  - **Spectral Derivative** is per nm whatever the image's unit; its result
    keeps the source's wavelengths.
  - **Spectral libraries**: a thermal-IR library in µm (e.g. 8-14 µm) is no
    longer read as nm. CSV libraries can name their unit in the wavelength
    column (`wavelength_um`) or a `# units:` comment.
  - A file that declares no unit gets one inferred from its values, with a
    warning in the log.
- **Bad bands are honored.** Bands marked bad in an ENVI header (`bbl`) are
  ignored by analyses (filled along each spectrum), left out of Spectral Slope
  and of the Spectral Hourglass's Prepare step.
- **Scaled reflectance.** Band Parameters divides by the header's reflectance
  scale factor first, so integer-scaled products (e.g. × 10000) work.
- **Half-pixel alignment.** Map coordinates of analysis results now line up
  with their source image (they were half a pixel off). Drawn ROIs on
  map-referenced images keep their exact corners instead of snapping to pixel
  centers.

### Added

- **Hide and show ROIs:** untick an ROI in the ROI table to hide it on the
  image, use **Hide All / Show All**, or right-click a row for **Show Only
  This ROI**. Hidden ROIs stay in the table and in saved sessions.
- **Duplicate Workspace** (Workspace menu, or right-click a workspace's tab):
  opens a copy on the same images with its own copies of the ROIs and
  display settings.
- The class legend of an overlaid classification sits over the image instead
  of crowding the overlay toolbar.
- **File → Save Session (Self-Contained)** writes one `.varda.zip` holding
  the session and every result that exists only in memory (as ENVI files);
  **Open Session** reopens it from anywhere. Paths inside a session folder
  are now stored relative to the session file.
- **Provenance**: every analysis result records the analysis, its settings,
  its input, the Varda version and the time; exported files carry the same
  record in their description.
- **Target Detection** (new Processing submenu): RX anomaly (global and
  local window), ACE and matched filter, the last two targeting the mean
  spectrum of ROI classes.
- **Classifiers**: Gaussian maximum likelihood and Mahalanobis distance
  (trained on ROI classes; small classes are handled by reducing to principal
  components instead of being dropped) and k-means clustering.
- **MNF noise estimate** can use vertical, diagonal or averaged neighbor
  differences, or a homogeneous ROI class (horizontal stays the default).
- Library spectra are resampled through each band's response when the image
  header gives band widths (FWHM), instead of being sampled at band centers.
- **Band Math** (Processing → Spectral Parameters): expressions such as
  `R2300 / R2200` (bands nearest 2300 and 2200 nm), `(b10 - b4) / (b10 + b4)`
  or `where(b3 > 0.2, R2300, 0)`. References are checked before anything is
  read, the result size is shown, large images are processed in chunks, and
  expressions can be saved for reuse.
- Images open in **true color** when they cover the visible (bands nearest
  640, 550 and 470 nm), otherwise in a false color spread over their range;
  a file's own default bands still win.
- **Histogram equalization, square-root and logarithmic stretches.**
- **Export spectral plots** as PNG (high resolution), SVG, PDF or CSV (the
  **Export…** button in the plot's View section).
- **Continuum Removal** (Processing → Spectral Parameters): each spectrum
  divided by its upper hull within a wavelength window.
- **Help menu**: About Varda, Cite Varda (APA and BibTeX), Copy System Info
  (for bug reports), Documentation and Report an Issue.
- Varda is licensed under the GNU GPL v3 or later; the repository has a
  citation file, contributing guide, code of conduct and issue templates.
- ENVI export writes `fwhm`, `bbl` and `reflectance scale factor`, and no
  longer writes a `wavelength` list for products that aren't spectra.
- Spectral plots label their x axis with the unit, and library spectra are
  shown in the plotted image's unit.
- The Hourglass endmember export names its wavelength unit, so it loads back
  as a library.

### Deprecated

- `VardaRaster.raster` (deprecated since 0.0.2) now says so with a
  `VardaDeprecationWarning` naming its replacements: `getBands()`,
  `getData()` or `getSpectrum()`. These read only what they need rather than
  the whole image.

### Faster

- **Very large images open quickly.** An image that would take more than a
  quarter of the computer's memory is read from disk on demand (recent band
  reads are cached) instead of being loaded whole; smaller images still load
  into memory as before.
- Changing the stretch no longer re-reads the image, and changing opacity
  no longer re-reads or re-stretches it (about 70 ms to under 25 ms on a
  3000 × 3000 RGB view). Only a new band selection reads bands again.
- Numba's compiled stretch kernels are cached on disk when running from
  source, so the first stretch of a session no longer compiles for seconds.

### Fixed

- The splash appears as soon as Varda starts and animates while it loads; it
  used to flash up only once loading had finished.
- A new splash animation: one continuous pen writes the wordmark, with no jump
  or pause, and finishes smoothly even when Varda is ready early.
- Hints and analysis descriptions were too dark to read in dark mode.
- ENVI files open whatever their data file is called: `.img`, `.dat`, `.raw`,
  `.bin`, `.bsq`/`.bil`/`.bip`, no extension, upper case, or `foo.img.hdr`.
  Opening the data file directly also works when its header sits beside it,
  and an `.img` without a header (e.g. Erdas or PDS) is no longer forced
  through the ENVI reader.
- ENVI headers written with capitalized keys (e.g. `Wavelength Units`) lost
  their wavelengths, units and band metadata.
- Integer ENVI files with a `data ignore value` failed to load.
- The Spectral Hourglass library preview was blank for µm images.
- User plugins in `plugins/user_plugins/` never loaded.
- Closing an image that had been loaded into memory left its file open.
- An analysis could fail with a read error, or read wrong values, if the display
  read the same file at the same time. Each thread now has its own file handle.
- Opening a large file no longer blocks the window while it loads.
- An unexpected error while installing PyTorch left the Optional Features
  dialog busy forever.
- Saved plot curves kept their color but lost its transparency.
- Settings changed after a dialog or panel closed could still reach its
  widgets.
- A session saved by a newer Varda is refused with a clear message.
