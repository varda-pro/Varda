# Changelog

Notable changes to Varda. Release notes on GitHub are generated from pull
requests; this file records what users need to know, especially changes that
can alter results.

## Unreleased

### Added

- **CRISM Ratio: go to a sensor position.** **Go to Sensor…** in Ratio
  Controls takes a detector sample and line for the numerator and the
  denominator (0-based, as the IN file's IR Sample and IR Line, or an
  unprojected cube's columns and rows) and puts the boxes on the nearest
  image pixels, as clicks there would (the column lock applies), saying
  where each landed. See
  [CRISM ratio cubes](docs/user-guide/crism-ratio.md#go-to-a-sensor-position).
- **Drag vector files onto Varda.** A GeoPackage, Shapefile, GeoJSON, KML,
  FlatGeobuf or GPX file dropped on the window goes to the current
  workspace: the import dialog of the manager its geometry suggests
  (polygons as ROIs, points as points, lines as lines), or, in Find Data, a
  map overlay. A GeoPackage of raster tiles still opens as an image. See
  [Importing vector data](docs/user-guide/importing-vector-data.md).
- **CRISM Ratio: saved ROIs on the images.** **Show ROIs** (on by default,
  in Ratio Controls) outlines the saved ROIs in their class colors on the
  I/F and browse views, the browse one through the link; selecting one in
  the ROI Manager highlights it.
- **CRISM: scale the VNIR bands to the IR at the 1 µm detector join.** A
  joined cube's spectra often step where the S and L detectors meet (a
  median 1.9 % in FRT00005850's MTRDR). **Scale VNIR to IR at 1 µm**, in the
  CRISM Ratio workspace's Ratio Controls and in Ratio Cube, fits a line to
  each side of the join and multiplies the VNIR bands so they meet the IR;
  the IR bands are unchanged. The workspace scales numerator and
  denominator each by its own factor (shown beside the toggle, and recorded
  with its 1-sigma in what S and P save); Ratio Cube scales every pixel and
  adds its factor and 1-sigma to the denominators image. Off by default. See
  [CRISM ratio cubes](docs/user-guide/crism-ratio.md#the-1-µm-detector-join).
- **Find Data: sharp previews, and previews on the map.** The Preview panel
  shows the catalog's largest browse image (ODE's large browse image, not
  its 32-pixel thumbnail; a STAC overview when it is a picture) at the
  panel's size and the screen's full resolution, so it is no longer
  blurry. With **Previews** checked, the selected products' browse images
  are drawn over their footprints when they are north-up maps of them
  (CTX, HiRISE, THEMIS VIS); others stay in the panel, which says so. See
  [Finding data](docs/user-guide/finding-data.md#previews).
- **Shape models of small bodies.** 3D Terrain has a **Surface** switch:
  **Shape model** opens a plate model (OBJ, Gaskell PLT and ICQ, FITS X/Y/Z
  planes, NAIF DSK with the new `spice` extra), orients its normals
  outward (and says so), shows it in 3D (decimated above 512,000 plates),
  reads the plate under a click from where the view ray meets the full
  model, colors plates by plate data with its uncertainty (sigma as color,
  or faded uncertain plates), and exports OBJ, STL, plate CSV, web pages
  and stills. Plate data is matched to its model by content hash and plate
  count. Guide: Small bodies and shape models.
- **Shape Geophysics** (Processing → Shape Models, or the shape panel): per
  plate gravity, slope, geopotential elevation and tilt of a closed shape
  model (constant-density polyhedron gravity, Werner & Scheeres 1997, plus
  rotation), with slope and elevation also at ρ ± σ, since they aren't
  linear in density. Models above 200,000 plates are evaluated on a
  decimated model and the result says so.
- **Irregular bodies.** Bennu, Ryugu, Didymos, Dimorphos and Arrokoth are
  known bodies (from a cited table; PROJ lacks them), as are Eros, Itokawa
  and 67P, with densities and rotation periods for Shape Geophysics. On an
  irregular body the coordinate-system check says so, lengths and areas are
  labeled approximate, and Reproject Image recommends no projection for
  measurement or terrain.

### Changed

- Lengths and areas on irregular bodies (Eros, Phobos and the bodies above)
  report "approximate (sphere model): irregular body" and no body-model
  difference.

### Fixed

- **Editing an ROI table cell no longer starts from nothing.** A
  double-click on a description or any added column opened an empty editor,
  so the text was lost; the editor now holds the cell's value (numbers in
  full, and they stay numbers).
- **CRISM Ratio: the plots keep the wavelength range you zoomed to** while
  you click new pairs, instead of going back to the whole spectrum each
  time (Fit to Data goes back).
- **A closed panel can be opened again.** Closing a workspace's Render
  Settings, Histogram or another panel left no way back. Right-click an image
  view: its image's Render Settings and Histogram are at the bottom of the
  menu, and **Panels** lists every panel of the workspace, checked when open.
  In the CRISM Ratio, General Image Analysis and Dual workspaces.

## 0.11.0 (2026-10-20)

### Added

- **Class areas in Image Properties.** For a class map, a Classes group
  lists each class's pixels, ground area (km², from each pixel's area on
  the coordinate system's own surface) and share of the classified pixels,
  with the totals and the method; it's measured in the background. **Copy
  as CSV** and **Export CSV…** write the table with the method, Varda's
  version, and the classified and unclassified pixel counts. Without map
  coordinates it gives pixel counts. The CSV also records the image's
  coordinate system (its description and its code or WKT) and pixel grid
  (affine transform and pixel size), and escapes newlines in the image name
  so the header stays intact. Class areas now read the class map in
  blocks of rows (about half the memory), and any failure of PROJ falls
  back to pixel counts with the reason. See Classification → Class areas.
- **Reproject Image** (Processing → Geometry, after Resample). Puts an
  image on the projection Varda recommends for the scene (by Purpose:
  Measurement, Mosaic or Terrain), exactly on another image's grid, or on a
  coordinate system you type (EPSG, IAU_2015, WKT or PROJ), with GDAL's
  warper (nearest, bilinear, cubic, average or mode; nearest by default for
  class maps and masks). Resolution is ground meters per pixel at the
  output's center (0 keeps the source's pixel area); the extent can be the
  whole image or another image's. Another body, an unreadable CRS, a grid
  across the antimeridian or an output over 400 million pixels is refused
  with the reason; an image already on the target grid is copied, not
  resampled. A companion quality image gives each pixel's source pixel
  distance and a Valid mask, and the `reprojection` record (CRSs, kernel,
  resolution, grid, PROJ pipeline, GDAL and PROJ versions, scale change) is
  kept in saved files. See
  [Reprojecting images](docs/user-guide/reprojecting.md).
- **Overlays across map projections.** The overlay bar in General Image
  Analysis and Dual Image now offers any georeferenced image of the same
  body that overlaps the view's image, in any projection, not only images
  on its pixel grid. Such an overlay is reprojected for display only, onto
  the view image's grid, with nearest-neighbor resampling (a class map
  keeps its classes; positions within ½ overlay pixel plus ½ view pixel,
  planetocentric between a sphere and an ellipsoid), on a worker thread and
  cached. The bar says "reprojected for display", with both coordinate
  systems in its tooltip. Analyses, statistics and exports still use each
  image as it is. Images of other bodies are refused, and the bar counts
  the images it can't overlay, with why in its tooltip. See
  [Viewing and measuring](docs/user-guide/viewing-and-measuring.md#overlays).
- **CRSs built from planetary labels in the coordinate-system check.** When
  an image has no CRS, Varda builds one (and, if the file has none, its
  pixel grid) from its own PDS3, ISIS or PDS4 map projection group: used
  automatically when the label needs no assumed convention, otherwise
  offered with the assumption named (e.g. ISIS's local-radius
  equirectangular). A label whose CRS differs from the one GDAL built (GDAL
  3.9.3 reads west-positive PDS3 labels as east, and planetographic
  ellipsoids as spheres) gets the new `label-crs-mismatch` problem, its
  label's CRS offered, never applied on its own. A label whose longitude
  range fits the image only with samples running westward gets
  `lon-bounds-westward`. See
  [Coordinate systems](docs/user-guide/coordinate-systems.md#crss-built-from-the-label).
- **CRSs from planetary labels are applied at load, and can be applied and
  undone in Image Properties.** An image whose file has no CRS and whose
  label's map projection group needs nothing assumed opens with the
  label's CRS and, when the file has none, its pixel grid (both kept in
  sessions and exports, recorded as `crsFix` with `"source": "label"` and
  the `"transform"`). **Apply** on an offered label CRS (including a
  `label-crs-mismatch` against GDAL's) uses it, and **Undo** returns to the
  file's CRS and grid. A west-positive label's CRS is stored as PROJJSON,
  keeping its west axis. A CRS you chose that differs from the label's is
  now an Info finding worded as your choice, not a problem. Image
  Properties' OK no longer drops a corrected transform. See
  [Coordinate systems](docs/user-guide/coordinate-systems.md#reporting-a-crs-built-from-the-label).
- **CRISM processing** (Processing → CRISM → Process CRISM Observation…, or
  `Varda --crism process`): turns a CRISM IR observation's TRR3 I/F into a
  map-projected, MTRDR-consistent I/F on the MTRDR's own grid, with a σ file
  (instrument noise, per band), the geometry (`_de`) and processing (`_in`)
  backplanes, and a recipe and provenance record. Steps: photometry,
  despike, atmospheric correction (fitted transmission with a shift and an
  elevation prior, and learned instrument-artifact bases for their epoch),
  optional destriping, smile and empirical shift correction, projection.
  Calibration files (WA and atmospheric transmission) are found beside the
  observation, in CAT's `aux_files` if you point to them, or fetched from the
  PDS (bad-pixel maps are used when local), and a missing one is reported
  before anything runs. Phase 1 is IR-only with no EGN; it is not
  bit-for-bit CAT. See the
  [guide](docs/user-guide/crism-processing.md) and the
  [validation report](docs/superpowers/specs/crism/phase1-validation.md).
- **FITS images and ALTWG/SPC terrain cubes open.** `.fits`, `.fit`, `.fts`
  and `.fz` files are read with astropy. The image is the primary HDU or the
  first image extension, and each plane is a band. OSIRIS-REx ALTWG/SPC cubes
  get their band names and units from the `PLANEn` cards, e.g. **Elevation
  (m)**, and show their height by default. BSCALE/BZERO are applied, and
  BLANK and NaN read as no-data. Pixels are read from a memory map. Local
  DTMs get no invented coordinate system: the target body and the
  latitude/longitude/radius/X/Y/Z planes are recorded for later use. Sigma
  planes stay as bands, carried as-is. Image Properties shows the FITS
  header. See [FITS images](docs/user-guide/fits-images.md).
- **ROI areas and perimeters on the body.** Add Column's Geometry measures
  now include **Area on the body (km²)** and **Perimeter on the body (km)**:
  geodesic (Karney, 2013) on the coordinate system's own surface, with the
  ROI's edges densified to one pixel, whatever the projection. The column's
  tooltip names the method and surface, the difference on the body's IAU 2015
  reference ellipsoid, and why any value is blank. The map-unit area and
  perimeter are now labeled planar, with the projection's distortion over the
  image in their tooltip. See
  [Viewing and measuring](docs/user-guide/viewing-and-measuring.md#calculated-columns).
- **ROI columns weighted by ground area.** Add Column's zonal statistics
  now include **area of pixels with data (km²)**, **area-weighted mean**
  and **sum × pixel area (value·km²)**: each pixel with data counts with
  its ground area on the coordinate system's own surface (exact per row, or
  over the projection's areal scale). They count whole pixels, so they're
  reported apart from **Area on the body**, which measures the drawn
  polygon; the column's tooltip names the method and surface. See
  [Viewing and measuring](docs/user-guide/viewing-and-measuring.md#calculated-columns).
- **The pixel readout names the body and conventions.** Hovering over a
  georeferenced image shows, e.g., `lat 18.40312° N  lon 77.45120° E  (Mars,
  planetocentric)`: the body, the latitude type, the longitude direction and,
  for 0..360 images, the longitude domain. See
  [Viewing and measuring](docs/user-guide/viewing-and-measuring.md).
- **Image Properties describes the coordinate system.** A new
  **Coordinate system** group says what the image's CRS is in words, e.g.
  "Mars, sphere R = 3394.84 km, Equirectangular, center lat 15°, 18 m/px",
  with its latitude type (planetocentric or planetographic), longitude
  direction and domain (0..360 or −180..180), its authority code (or the
  `IAU_2015`/EPSG code it matches), the pixel size in the CRS's units and on
  the ground, and the full WKT. An unknown body is reported as unknown,
  never as Earth. A CRS PROJ can't read is shown as the file has it, with
  PROJ's error, and fills **Set coordinate system…** to be corrected.
  Line-profile CSVs name the CRS the same way. See the new
  [Coordinate systems](docs/user-guide/coordinate-systems.md) guide.
- **Planetary images tagged WGS 84 are read on their own body.** When an
  image opens, Varda checks its coordinate system against its label. A
  geographic Earth CRS (e.g. `EPSG:4326`) on an image whose label names
  another body Varda knows (PDS3 `TARGET_NAME = MARS`, say) is read as
  degrees on that body's IAU 2015 sphere (`IAU_2015:49900` for Mars); the
  degrees don't change. This is the only CRS Varda changes on its own: the
  file is left as it is, the corrected CRS is kept with the image (and in
  sessions), files Varda exports are written with it, and the provenance of
  every result made from the image records it as `crsFix` (`from`, `to`,
  `reason`, `automatic`). A body Varda doesn't know (e.g. Bennu) is flagged
  but not fixed; targets that name no body (`SKY`, `CALIBRATION`, `N/A`) are
  ignored. Editing an image's metadata in Image
  Properties keeps the corrected CRS; **Reset to the File's** returns the
  image to how it opened. See
  [Coordinate systems](docs/user-guide/coordinate-systems.md#the-one-automatic-fix-and-why-only-that-one).
- **Coordinate-system findings show where you work, and you can fix them.**
  An image whose CRS has a tip or a problem gets an amber dot on its
  thumbnail in the image list, and its tooltip says the first finding
  (e.g. "Map: Its CRS says Earth; read as Mars (from its label)."). An
  image with a problem is announced once in the status bar when it opens.
  Image Properties lists every finding with what to do, **Undo** for the
  automatic fix, **Apply** for a fix offered, and **Set coordinate
  system…**: any EPSG or IAU_2015 code, WKT or PROJ string, or the same CRS
  as another open image, described in words as you type, with a warning
  (not a refusal) for a CRS on another body. Changes apply at once, also
  to workspaces already open (the image's ROIs and their calculated
  columns, line lengths and saved points follow), and are recorded as
  `crsFix` (`automatic: false`, or `undone: true`). See
  [Coordinate systems](docs/user-guide/coordinate-systems.md#setting-or-correcting-a-coordinate-system).
- **Saved ratio pairs get a class.** In the CRISM Ratio workspace, S (and P)
  now opens a small dialog with the class prefilled with the reference
  mineral overlaid on the Ratio plot (its metadata's mineral or class, else
  the short form of its name, e.g. `Olivine Fo91`; else the last class used), the
  automatic name and the verdict; Enter saves, Esc cancels. The numerator ROI
  takes the class (e.g. `Olivine`), the denominator the `Denominator` class,
  and both record `pair`, `role` and `target` columns; each new class gets
  its own color, and `Denominator` is light gray; a point gets the class
  at the start of its description. "Don't ask" saves at once with the
  suggested class (Shift+S and Shift+P still ask); the session keeps it and
  the last class. Previously the ROIs were named `Ratio N numerator …` and
  `Ratio N denominator …`, one class each.
- **Import ROIs, points and lines from GIS files.** **Import…** in the ROI
  Manager, Points Manager and Lines tab reads shapefiles, GeoPackages (choose
  the layer), GeoJSON, KML, FlatGeobuf, GPX and CSV tables of points,
  reprojects them onto the image (or reads them as pixel coordinates), and
  says what it imported and skipped: other geometry types, features outside
  the image. On a planetary image, GeoJSON's WGS 84 degrees are read as
  **Degrees on Mars** (the image's body), a choice the dialog shows;
  longitudes take the image's convention (0–360 or −180–180); ISIS `sample`
  and `line` columns (1-based pixel centers) are converted. Name and class
  columns are chosen in the dialog; the other columns become ROI table
  columns or point and line properties (now exported with them too). Point
  and line exports now carry a `crs` column (WKT), as ROI exports do, so
  Varda's own exports round-trip, planetary ones included. See
  [Importing vector data](docs/user-guide/importing-vector-data.md).
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

- **Pixel Select takes a plain click.** With Pixel Select on, a click plots
  the pixel's spectrum (a plain drag still pans; Ctrl-drag, ⌘-drag on a Mac,
  follows the pointer), its tool sits first under each view, and the tool
  row wraps instead of hiding tools behind a » button. Turning it on says
  how to use it on the image, and the Pixel Spectra tab comes to the front
  with each selection. Clicking a pixel already plotted highlights its curve
  instead of drawing it again.
- **Import Image(s) is Ctrl/⌘+O**, the standard Open shortcut; Open Session
  moves to Shift+Ctrl/⌘+O. The empty window shows how to start.
- **Key names follow the platform:** hints and tool tips say ⌘ on a Mac
  (including the docking library's), Ctrl elsewhere.
- **A new workspace tab comes to the front.**
- **Help → Documentation opens the user guide shipped with this version**,
  offline, in Varda; its online link points at this version's release tag,
  not the main branch.
- **Wavelengths read as wavelengths:** band lists and labels round band
  centers to tell neighboring bands apart and give the image's unit
  (`2353.8 nm`, not `2353.8461538461543`).
- **Clearer words:** PCA/MNF/ICA's weights are **Weight Image** and **Weight
  Image Holds**; menus and tips say *image* and *band*; Find Data spells out
  STAC and ODE; Kindle a Feature says it builds a plugin with AI; the overlay
  bar says why images can't be overlaid; the status bar says what to do next.
  The guide has a glossary.
- **The Debug menu shows only in development** (`VARDA_DEBUG=1`).

- **A spectral plot's sidebar can be resized.** Drag the divider between a
  plot and its settings sidebar to make the sidebar narrower or wider; the
  width is remembered for each kind of plot and restored when the sidebar is
  reopened. In a narrow sidebar the fields go under their labels and the
  buttons and the View Range boxes wrap instead of being cut off, and the
  plot always keeps at least 200 pixels.
- **CRISM Ratio workspace: the plots side by side, and taller.** The
  numerator and denominator are now overlaid on one **Spectra** plot (green
  and magenta, with a legend; **Separate** in its title bar splits them into
  two plots), with the **Ratio** plot beside it, each about 350 × 650 pixels
  at 1440 × 900 instead of three short strips. The box size, column lock
  (now "Lock to sensor column") and geometry file sit on one row above them,
  with the clicks and keys behind a **?**. The blandness readout is a strip
  below both plots: the check and its limits on one row, a one-line verdict
  naming each failing window's dip (or slope) and limit, and the per-window
  table behind **Details ▸**. Each plot can keep a **Shape** (Free, Square
  or Tall 3:4) as it is resized. Every panel is a dock that can be moved,
  tabbed, floated or folded, and the session keeps where they are, the
  shapes, Separate and Details. The failing-window markers are now on the
  Spectra plot's denominator curve. Results are unchanged.
- **Open images use much less memory.** Loading an image now peaks at about
  its own size, plus GDAL's shared block cache (a 1.5 GB CRISM MTRDR took
  5.2 GB, now 2.6 GB, and loads in half the time), with the same values and
  no-data. Images in memory aim to take at most 40 % of RAM together; beyond
  that the least recently used are read on demand from their files, with
  the same results (see Installing → Memory).

### Fixed

- **Earth datum shifts in reprojection.** Reprojecting between registered
  Earth datums onto a computed grid (NAD27 → WGS 84) dropped the datum
  shift, about 200 m, because the grid's WKT loses WGS 84's datum-ensemble
  id. A datum now counts as registered when it or its geodetic CRS has an
  authority code.
- **Masks and DEMs placed between a sphere and an ellipsoid keep their
  positions.** Masks and DEMs resampled from a sphere onto an ellipsoid grid
  of the same body (or back) were misplaced by up to ~20 km on Mars (GDAL
  used PROJ's ballpark); they now keep their planetocentric positions. This
  changes earlier results of Apply Mask, Band Math and Image Arithmetic
  masks, Shadow Mask's DEM, a FATT map's Exclude and the ROI Manager's From
  Threshold or Mask… whenever the input was on another grid across a sphere
  and an ellipsoid of one body: re-run those. Points (Linked Images, ROIs,
  saved points, lines) between a sphere and a PROJ-string ellipsoid with
  `+lon_wrap` or `+over` were also kept at the latitude's number; they too
  now keep their planetocentric positions. The resampling record names the
  operation (`coordinateOperation`). A west-positive geographic image
  (`IAU_2015:49901`) east of about 135°E at 45°N, or across ±180°, placed
  or reprojected onto any grid came out empty (GDAL lost it); it is now
  placed in full. `IAU_2015:49902` with a `+lon_wrap` or `+over` PROJ
  string raised a PROJ error in points, placement and the overlay bar; it
  now takes Varda's own route. See
  [Coordinate systems](docs/user-guide/coordinate-systems.md#rasters-between-surfaces-of-one-body).
- **AI keys without a keychain.** Where no secure key store exists (Linux without
  gnome-keyring or KWallet), the AI Assistant Settings now say so, a key still
  works for the session, and it isn't saved between sessions. Installing
  gnome-keyring, KWallet or `keyrings.alt` keeps it.

- **PCA, MNF and ICA no longer use every band when "from" and "to" are the
  same wavelength** (a wrong result). Only an unset range means every band;
  a range holding fewer than two bands disables Run and says why, and the
  dialog says which bands will go in. The range is compared at the precision
  it is shown with, so rounding never drops an edge band. Results made with
  an equal from/to in 0.10.0 or earlier used all bands; their provenance
  lists the bands actually used.
- **No network connection on startup.** The File menu's icons were looked up
  on `api.iconify.design` (a Cloudflare address) at every launch, even
  offline; they are bundled now. The new guide page
  [Network use and privacy](docs/user-guide/network-and-privacy.md) lists
  every connection Varda makes.
- **Opening a workspace no longer builds Matplotlib's font cache** (24 s on a
  fresh install, and at every launch of the packaged app).
- **Load and export errors say what to do**, in plain words (a folder, a
  missing, empty, damaged or zero-band file, an unknown format, no
  permission, a missing folder, a full disk); the library's text is under
  Show Details.
- **Export to a bare name** goes into the last export folder or the image's
  folder (not the folder Varda started in), and the status bar gives the full
  path. Names Windows forbids or reserves are refused before writing (Windows
  said "failed: No error"), and a name ending in a dot no longer writes
  `name..img`.
- **Images opened together join the Image List in the order chosen**, and a
  busy indicator shows for every load that takes a moment.
- **A result whose image was removed while the analysis ran is dropped**,
  with a message, instead of joining the project alone.

- **Planetary coordinate systems work when conda (or another GDAL/PROJ
  installation) is active in your shell.** Such a shell exports its own
  `PROJ_DATA`/`PROJ_LIB`/`GDAL_DATA`, and rasterio then read an older PROJ
  database that fails on `IAU_2015` codes. At startup Varda now replaces
  those with its bundled data only when they are unusable (an older database
  or no `IAU_2015` authority), and logs what it changed. Set
  `VARDA_KEEP_PROJ_DATA=1` to opt out. See
  [Installing](docs/user-guide/installing.md#troubleshooting).
- **Line lengths and profile distances in projected images were measured
  on the flat map (e.g. 2× too long east–west at 60° in a 0°-centered
  equirectangular map); they're now geodesic on the body.** In every
  coordinate system, distances are geodesic (Karney, 2013) on the CRS's own
  sphere or ellipsoid. The Lines tab's length tooltip and the profile CSV's
  header give the method and surface, the difference on the body's IAU 2015
  reference ellipsoid, the projection's distortion along the line, and the
  limits (horizontal; plus the image's georeferencing error). Line lengths
  now follow the line at about 1 px, as profiles do. See
  [Lines and profiles](docs/user-guide/lines-and-profiles.md).
- **Images and footprint files of Vesta and other bodies with IAU 2015
  radii were identified as Earth; unknown bodies are now reported as
  unknown.** Varda now identifies a body from a coordinate system's
  `IAU_2015` code, the names in it, else its radius against PROJ's IAU 2015
  radii, in the Find Data map, footprint files, the Terrain workspace and
  vector import. A projected footprint file on no known body is refused with
  a message. Projected footprints are now converted to degrees on the body's
  IAU 2015 sphere (Mars: 3396.19 km, was the 3389.5 km mean radius); latitude
  and longitude on a sphere don't depend on its radius, so footprints don't
  move. Degrees placed on a Terrain DEM are read in the DEM's own geographic
  coordinate system.
- **Mosaic refused images in the same projection written differently (e.g.
  by GDAL and ASP).** Mosaics, masks, DEMs and 3D Terrain overlays now
  treat two coordinate systems as one when their geometry matches (the
  projection, its parameters, the body's radii, the prime meridian and the
  axes), whatever they're named, so such images share a grid and aren't
  resampled. An image on a different body (a mask in Earth's degrees over a
  Mars image, say) is never placed on another, and degree images with
  longitudes 0–360 and −180–180 now overlap where they cover the same
  ground. A mosaic's projection error names both projections.
- **Linking or transferring between images of different bodies (or with an
  Earth CRS on planetary data) no longer crashes; it says why it can't.**
  Linked Images (and the CRISM Ratio workspace's browse view) linked by map
  location leave an image of another body unlinked, with a notice such as
  "EARTH: on Earth, not Mars; not linked"; applying ROIs to such an image
  and saving a point from it are refused with a message naming both bodies.
  Points saved from images on other projections of the same body are now
  converted to the Points Manager's coordinate system (that of the first
  image a point was saved from); before, their coordinates were stored
  unconverted and exported under the wrong CRS. Points from an image
  without georeferencing are no longer mixed with map points (or the
  reverse). Linking, ROI transfer and points between a sphere and a
  flattened ellipsoid of one body keep each point's planetocentric
  latitude; PROJ alone kept the latitude number, up to 0.34° (about 20 km)
  off on Mars. Images whose coordinate
  systems differ only in names are linked and given ROIs without
  reprojection.
- **The pixel readout swapped latitude and longitude for map projections
  on the IAU 2015 ellipsoid codes (e.g. `IAU_2015:49911`, `49912`).** It now
  reads the coordinate system's own axis order, so a pixel near 18.4° N,
  77.5° E reads `lat 18.39916° N  lon -77.49916° W  (Mars, planetographic)` in
  49911 and `… lon 77.50084° E  (Mars, planetocentric)` in 49912.
- **Vector import, terrain lines and Find Data misread degrees for map
  projections on the IAU 2015 ellipsoid codes.** Their geographic axes are
  latitude first (and west-positive for the planetographic ones), so
  imported features and lines draped on a terrain landed in the wrong place
  and Find Data drew such an image's footprint mirrored. Degrees are now
  read longitude east first, in the image's own latitude type, and
  conversions between surfaces of one body keep the planetocentric place
  (as Linked Images and points do). Terrain lines on another body are
  refused with a message rather than "No line crosses the DEM".
- **A box zoom no longer leaves its yellow box on a plot.** In every
  spectral plot, letting go of Cmd, Ctrl or Shift just before the mouse
  button turned the end of a box zoom into a pan, so the box was never
  cleared (and the zoom never applied); with a curve selected, any drag of
  10 px or more anywhere in the plot became dragging that curve away, which
  took the mouse release from the zoom. The keys held when a drag starts now
  decide it, only a plain drag that starts on the selected curve drags the
  curve, and a new drag, a mouse release or **Esc** clears any leftover box.
- **Opening a packaged project no longer fills the disk.** Every open of a
  `.varda.zip` unpacked a new full copy and kept it forever. Varda now unpacks
  a package once and reuses that folder, removes unpacked packages nothing uses
  (at startup and after opening one), and refuses to unpack when the disk lacks
  the room.
- **Varda no longer slows down as more images are open.** The image list
  draws each image from a small thumbnail made once (from a sample of its
  pixels, and again after its metadata is edited) instead of rendering every
  image in full on every repaint and hover. Overlay bars no longer redraw
  their views each time an image is opened, removed or renamed, unless the
  overlay shown is the one removed. Pixel-spectra plots watch only their own
  widgets for clicks, not every event in the application, which made tab
  switches and new workspaces slower with every workspace open. A new
  General Image Analysis workspace no longer flashes up as its own window
  before it's tabbed.
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
