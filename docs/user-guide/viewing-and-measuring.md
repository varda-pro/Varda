# Viewing and measuring

How Varda turns an image into what you see, and how it measures spectra:
single pixels, boxes, ROIs and ratios. Stretches change only the picture.
Everything else on this page reads the data as stored.

## Display stretches

Each view shows either one band through a color map (**Mono**) or three
bands as red, green and blue (**RGB**), set under **Render Settings**.
Right-click a view for its image's **Render Settings** and **Histogram**,
and **Panels** for every panel of the workspace (checked when open): a panel
closed by mistake comes back from there. A stretch maps each band's
values to 0–1. Varda then clips to 0–1 (so
values outside saturate at black or full brightness), multiplies by 255 and
truncates to 8 bits; in Mono, that 8-bit value picks one of 256 colors from
the color map. No-data pixels are drawn as 0: black in RGB, the color
map's lowest color in Mono.

**Stretch Algorithm** offers:

| Stretch | What it does | Settings (default) |
| --- | --- | --- |
| **Min-Max (Auto Full Range)** (the default) | The band's minimum maps to 0 and its maximum to 1, linearly | None |
| **Min-Max (Manual)** | Linear between limits you type | **Red Stretch**, **Green Stretch**, **Blue Stretch**: min and max (0 and 1). Mono uses the red limits |
| **Linear Percentile** | Linear between two percentiles; values outside are clipped | **Low Percent** (1 %), **High Percent** (99 %), whole numbers 0–100 |
| **Square Root (Percentile)** | √x of the Linear Percentile result: lifts dark areas gently | As Linear Percentile |
| **Logarithmic (Percentile)** | log₁₀(1 + 9x) of the Linear Percentile result: lifts dark areas strongly | As Linear Percentile |
| **Histogram Equalization** | Each value maps to the fraction of pixels at or below it, from a 4,096-bin histogram between the band's minimum and maximum | None |

Each band of an RGB view is stretched on its own statistics. **No Stretch**
shows the values as they are, as if they were already 0–1: right for
reflectance; values below 0 draw as 0 and above 1 as 1.

**Percentiles are approximate.** Varda finds them from a 1,024-bin
histogram, not by sorting. The p-th percentile is the smallest value with
at least p % of the pixels at or below it, and each limit is the lower edge
of the bin that value falls in, so it can be low by up to one bin width: (maximum −
minimum) / 1,024. For RGB, the bins span the range of all three bands
together, so a band with a narrow range gets coarser limits. **High
Percent** at 100 gives the lower edge of the top bin, a little under the
maximum.

**Where the statistics come from.** In the General Image Analysis workspace,
**Stretch From** chooses **Full image** (the default), **Main view** or
**Zoom view**. The view choices recompute the stretch as you pan and zoom, so
the same pixel value can look different in two figures. Other workspaces
always use the full image.

**The Histogram panel** shows each band's values with the stretch limits as
a draggable region. Dragging a region switches to **Min-Max (Manual)**, filled
in with the limits the previous stretch used, so the numbers then show in
**Red Stretch**, **Green Stretch** and **Blue Stretch**. This is the way to
read the exact limits of an automatic stretch.

### Stating the display in a figure caption

A stretch never changes the data, the spectra, or any analysis. But a reader
can't interpret a picture without knowing how it was made. In the caption,
give:

- the bands shown, by wavelength, as R, G, B (or the one band and its color
  map);
- the stretch and its settings (for example "linear, 1–99 % per band") or
  the exact limits in data units;
- for **Main view** or **Zoom view** statistics, that the stretch comes from
  the area shown.

Sessions keep the display settings of Linked Images and 3D Terrain
workspaces, but not those of General Image Analysis, so write them down.

## Overlays

The **Overlay** bar under the main view of General Image Analysis (and under
each side of Dual Image) lays another loaded image over the view: a class
map in its classes' colors, with a legend whose swatches you click to
recolor a class, or one band of any other image through a color map, with
its own stretch (**Colors**) and **Opacity**. Pixels without data stay
see-through. An overlay is for looking only: it changes nothing in either
image.

**What can be overlaid.** The list offers:

- images on the view's pixel grid (the same size, map projection and pixel
  positions), shown pixel for pixel as they are;
- georeferenced images of the **same body** that overlap the view's image,
  in any map projection, on any surface of that body (a sphere or an
  ellipsoid), with longitudes 0–360 or −180–180. These are **reprojected
  for display** (below).

The images left out are counted beside the list, with why and what to do
("1 image can't be overlaid: no map coordinates. Open a map-projected version
of it."; when the view's own image has no map coordinates, only images of its
size can be overlaid: resample one to that size with Processing → Geometry →
Resample), and its tooltip names each with the reason: "Mars and Earth are different
bodies", "doesn't overlap this image", "not georeferenced, can't be placed
on this image". Images of other bodies are always refused. An image that
would be reprojected onto a view image larger than 100 million pixels is
left out too, "too large to reproject for display (N Mpx)": reproject it
yourself with **Processing → Geometry → Reproject Image** onto the view
image's grid and overlay that.

### Reprojected for display

An overlay in another projection is reprojected onto the view image's grid
by GDAL's warper (`rasterio.warp.reproject`) with **nearest neighbor**
resampling, one band at a time: the Class band for **Classes**, or the band
chosen. Values are never blended, so a class map keeps its classes and a
band keeps its values; pixels outside the overlay, or without data in it,
stay see-through. While it runs the bar says **Reprojecting…**, then
**reprojected for display**; hover over that for the two coordinate
systems and the method. If it fails, the bar says "couldn't reproject:"
and why, and no overlay is shown.

- **Where pixels land.** Each view pixel shows the overlay pixel under its
  center, so a feature is drawn within ½ overlay pixel plus ½ view pixel
  of where the two images' georeferencing puts it. Between a sphere and an
  ellipsoid of one body, positions keep their planetocentric longitude and
  latitude (see [Rasters between surfaces of one
  body](coordinate-systems.md#rasters-between-surfaces-of-one-body)). The
  georeferencing's own errors (a misregistered image) are on top of this:
  the overlay shows them, it doesn't correct them.
- **Display only.** Nothing reprojected for display is saved or written.
  Analyses, statistics, ROIs, spectra and exports read each image as it
  is. Tools that combine images pixel for pixel (Image Arithmetic, Band
  Math, zonal-statistic columns) still need the [same
  grid](image-processing.md#images-on-other-grids): make one with Reproject
  Image.
- **Cache.** Reprojected bands are kept (up to 512 MiB, the least recently
  used dropped first), so choosing an overlay again, or recoloring a class,
  is instant. Editing the overlay's metadata (its no-data value, band
  names, coordinate system or transform) reprojects it again.

In a figure caption, state that the overlay was reprojected for display
from its coordinate system to the view image's with nearest-neighbor
resampling (the tooltip's text), and give both images' pixel sizes, which
set the ½ + ½ pixel position error above.

## Pixel readout

Hovering over an image shows, at its bottom left, the pixel's column and row,
the displayed band values and, for a georeferenced image, its position. A
map-projected image shows `xy` in the CRS's units (e.g. `xy 1234.50, -987.00 m`),
and every georeferenced image shows its latitude and longitude with the
conventions named, e.g. `lat 18.40312° N  lon 77.45120° E  (Mars,
planetocentric)`. The body and latitude type (planetocentric or
planetographic) come from the image's CRS, as described in
[Coordinate systems](coordinate-systems.md); an unknown body reads "unknown
body", never Earth. The angles are shown as the CRS gives them: a west-positive
CRS reads "° W", and a 0..360 image shows longitudes in [0, 360) followed by
"(0–360)". The position is that of the pixel's center.

## Pixel spectra

Turn on **Pixel Select** under a view and click a pixel to plot its
spectrum; the **Pixel Spectra** tab comes to the front (the tool's hint over
the view goes once you click, and the status bar names the next step). A plain drag still
pans the view; Ctrl-drag (⌘-drag on a Mac) plots the pixel under the pointer
as it moves. Clicking (or Ctrl/⌘-clicking) a pixel already plotted selects
its curve instead of drawing it again: the curve gets a thick yellow halo,
its legend entry starts with "Selected:", its marker on the image turns yellow,
and its settings appear under **Selected Curve** in the sidebar. Click an
empty part of the plot to deselect it. Under **Pixel Spectra** in the plot's sidebar,
**Window Width** and **Window Height** (1 px each by default, up to 99) set
a box around the click instead. A box plots the mean of its pixels, with a
shaded band of ±1 standard deviation (see [ROI statistics](#roi-statistics)
for how both are computed). An even-sized box extends one pixel further
right and down than left and up.

The y axis names the values when the image's metadata says what they
are: **Value (<unit>)** from its stated unit (an ENVI header's `data units`,
a FITS `BUNIT`, or a `units` entry), **Reflectance** for an ENVI file with
a `reflectance scale factor`, and plain **Value** otherwise, or when the
plotted images disagree. Varda doesn't guess a unit the file doesn't state.
The legend sits in the plot's top-right corner and is translucent so the
curves show through it; drag it to move it, or turn it off with **Show
Legend** under **View**.

**Export…** in the plot's sidebar saves the plot as PNG, SVG or PDF, or the
curves as CSV. Each curve is a column of its own values (not scaled; see
[Library overlays](#library-overlays)). For a box or ROI mean, two more
columns follow it: `<name> std`, the ±1 standard deviation shaded on the
plot, and `<name> n`, the number of pixels with data in that band behind
the mean and standard deviation. When curves don't share wavelengths, the
CSV has one row per sample instead, with columns `curve`, `x`, `y` and, if
any curve is a mean, `std` and `n` (blank for single pixels).

### The plot's sidebar

A spectral plot's settings (**View**, **Markers**, **Library Spectra**,
**Selected Curve**, **Appearance**, and in some workspaces more) sit in a
sidebar on its right. The arrow between the plot and the sidebar folds it
away and back. Drag the divider beside the arrow to make the sidebar
narrower or wider: its fields move under their labels and its buttons wrap,
and it scrolls up and down but never sideways. It can't get narrower than
its widest field (about 230 pixels), and the plot keeps at least 200 pixels.
The width you drag it to is remembered for plots of that kind (e.g. pixel
spectra plots) and used again when the sidebar is reopened, in this and
later sessions of Varda.

### Saved points

**Save Point** (or **Save All Points**) in the plot's sidebar keeps a pixel
spectrum's pixel in the **Points Manager**, with its map position when the
image is georeferenced. **Export…** there writes the points as GeoJSON,
GeoPackage or a shapefile; **Import…** adds points from a GIS file or a CSV of
coordinates (see [Importing vector data](importing-vector-data.md)).

The points share one coordinate system: that of the first georeferenced image
a point was saved from, which **Export…** writes with them. A point saved
from an image on another projection of the same body is converted to it at
the same planetocentric longitude and latitude, with the latitude type of
each CRS respected (a sphere's planetocentric latitude becomes the
planetographic one on a flattened ellipsoid); see [Moving points between
surfaces of one body](coordinate-systems.md#moving-points-between-surfaces-of-one-body).
Report the exported CRS with the points. Images whose coordinate systems
differ only in names need no conversion.

A point isn't saved, and the line under the Points Manager's table says why,
when it comes from an image of another body (or one whose body can't be told
and whose radii differ; the message names both), or when it would mix pixel
and map coordinates: a point from an image without georeferencing among
points with a coordinate system, or the reverse. Points from images without
georeferencing are kept in pixel coordinates (pixel centers).

### Moving around a spectral plot

In the spectral plots (pixel, ROI and ratio spectra, and the Spectral
Hourglass):

- **Drag** pans; **Shift-drag** (or Ctrl/Cmd-drag) zooms to the dragged box;
  **right-drag** stretches the axes. The keys held when a drag starts decide
  which it is, so letting go of Shift or Cmd just before the mouse button
  still zooms. **Esc** over the plot clears a zoom box, should one ever be
  left behind.
- **Scroll** (mouse wheel or trackpad) over the plot zooms both axes about
  the cursor.
- **Scroll over an axis** zooms only that axis, about the cursor. Over the
  Y axis this stretches the spectra vertically, so shallow absorption
  features look deeper; over the X axis it narrows the wavelength range.
- **Drag along an axis** moves only that axis; **right-drag on an axis**
  stretches only that axis.
- **Drag sideways off the Y axis** pulls out a marker (below), like a guide
  off a ruler: it follows the cursor and drops at the wavelength where you
  release. Varda decides after the first few pixels: mostly sideways pulls a
  marker, mostly up or down moves Y. Releasing back over the axis or outside
  the plot area cancels it.
- **Click a curve** to select it, then press **Backspace** or **Delete** to
  remove it (the same as **Remove Curve** under **Selected Curve**). The
  keys act only while the plot has the keyboard focus (clicking the plot
  gives it the focus), never while you are typing in a field of the sidebar.
  A plain drag that starts on the selected curve drags it to another plot;
  any other drag pans or zooms as above.

A Y stretch stays as new spectra arrive until you reset it with **Fit to
Data** under **View** (or by setting **Range** back to **Auto**). Under
**Range**, any pan or zoom turns **Auto** into **Manual**. In **Fit Y To X
Range**, changing X keeps that mode and refits Y to the data in the new
window, but scrolling or dragging on the Y axis switches to **Manual**,
keeping the view you made. **X View Range** and **Y View Range** show the range on
screen, so you can note them for a figure.

**Markers** are vertical reference lines across every curve, for reading
off where a feature sits and comparing it between curves. Add one by
pulling it off the Y axis, or under **Markers** in the sidebar (type a
wavelength and press **Add**; leave it at "view center" to place it in the
middle of the view). Drag a marker to move it; its label and the list under
**Markers** show its position in the X axis's units, to 0.1. **Remove
Selected** and **Clear** remove markers. A marker's position is only as
precise as where you drop it: one screen pixel spans the visible wavelength
range divided by the plot's width in pixels, so zoom in on X (or type the
wavelength) when the exact value matters, and report a feature's position
from the data (the band centers) rather than from a marker.

The Linked Images spectra and the FATT window's fit plot work the same
way when you scroll or drag (without the Shift-drag box zoom, markers or
removing curves with the keyboard). They have no
**View** controls: the small **A** button in a plot's lower-left corner
fits it to the data again.

A stretch changes only the view, never the values: the axis labels stay
in the data's units, and an export writes the curves' own values. In a
figure, give the Y range shown, because a stretched axis makes features
look deeper than they do at full range.

### Library overlays

**Library Spectra** adds spectra from any of Varda's
[spectral libraries](spectral-libraries.md): the bundled CRISM MICA
numerators and USGS minerals (splib07a), your own libraries and libraries
you loaded. **Libraries (n of m)** chooses which libraries to pick from
(one, several or all; each plot keeps its choice in sessions and new plots
start with your most recent one, see
[Which libraries a plot uses](spectral-libraries.md#which-libraries-a-plot-uses)),
**Search by name** filters their spectra, and **Add
to Plot** (or a double-click) plots the selected ones, at the library's own
spectral resolution, not resampled to your sensor. **Manage…** opens the
Spectral Library Manager.
To make it easy to compare shapes, Varda **scales and offsets** the library
curve so that, within the wavelengths on screen, its minimum and maximum
match those of the selected curve (or the most recently added one). The
values plotted are no longer the library's reflectance.

This is cosmetic, but it changes what a reader sees:

- In any published figure, say that library spectra are scaled and offset
  for comparison, or show them unscaled.
- To show the library's true values, click the library curve and, under
  **Selected Curve**, set **Y Scale** to 1 and **Y Offset** to 0. The scaling
  can't be turned off beforehand.
- A CSV export writes every curve's own values (the library's
  reflectance), not as shown. When a curve is shown scaled or offset, the
  CSV starts with comment lines (beginning `#`) giving each such curve's
  **Y Scale** and **Y Offset**: what's plotted is value × scale + offset.
  Tell your CSV reader to skip lines starting with `#` (in pandas,
  `comment="#"`).

### Saving spectra to a library

Click a curve (a pixel spectrum, box mean or ROI mean) and press **Add to
Library…** under **Selected Curve** to save it in one of your spectral
libraries, with the image, the pixel or region, the number of pixels, its
± 1 σ for a mean, and the Varda version. Its own values are saved, not as
scaled. See [Spectral libraries](spectral-libraries.md#adding-spectra-from-your-images).

## ROI statistics

Draw ROIs in the **ROI Manager** with **Draw ROI**. (The ROI tools are due to
be rewritten; this describes them as they are now.) **Plot Spectrum** (in the
ROI's right-click menu, or the button) plots the ROI's mean spectrum in
**ROI Plots**, shaded ±1 standard deviation.

How Varda computes them:

- **Which pixels:** a pixel is in the ROI when its center is inside the
  shape.
- **No-data:** a band's no-data value is left out band by band, so a pixel
  can count at some wavelengths and not others. Pixels that are no-data in
  every band are dropped.
- **Mean and standard deviation** are per band, over the remaining pixels.
  The standard deviation is the population form (divided by n, not n − 1).
  Multiply by √(n / (n − 1)) for the sample form; for more than about 30
  pixels the difference is under 2 %.
- **n per band:** the number of pixels with data in that band. It can be
  smaller than the ROI's pixel count where some pixels are no-data in only
  some bands. A band with no data at all has no mean or standard deviation
  (a gap in the curve, `nan` in the CSV), never 0.
- An ROI with no valid pixels isn't plotted.

The plot doesn't show n; its CSV export has it for every band (`<name> n`,
see [Pixel spectra](#pixel-spectra)). For one band, you can also add a
column (below) with the **count** statistic.

### Calculated columns

Double-click a cell of the ROI table (a class, a description, a value) to
edit what it holds. A number is edited in full, not as the six figures
shown, and stays a number; clearing it leaves it missing. Calculated
columns are read-only.

**Add Column...** adds a column to the ROI table. Its **Kind** is one of:

- **Geometry**, with **Measure**:
  - **Area (pixels)** and **Perimeter (pixels)**: of the shape in pixel
    units. The area is the shape's area, not the number of pixels whose
    centers fall inside it.
  - **Area on the body (km²)** and **Perimeter on the body (km)**: the
    ROI's area and perimeter on the ground, measured geodesically, whatever
    the image's projection (projected or geographic), so the projection's
    scale distortion never enters them. Available only for georeferenced
    images. The method is Varda's (see [How Varda
    measures](coordinate-systems.md#how-varda-measures)):
    - **Densification:** the ROI's edges are densified to one pixel (the
      smaller side of a pixel, in the CRS's units) in map coordinates, so
      they follow the straight edges you see on the image.
    - **Surface:** every vertex is converted to longitude and planetocentric
      latitude on the CRS's own sphere or ellipsoid (the surface the data
      were projected on), and the area and perimeter are Karney's (2013)
      geodesic polygon area and perimeter on that surface (PROJ's `geod`;
      its error is negligible). Holes are subtracted from the area and their
      edges added to the perimeter; a multi-part ROI's parts are summed.
    - **Body-model term:** when the CRS's surface differs from the body's
      IAU 2015 reference ellipsoid (Archinal et al., 2018) by more than 1 m,
      the same points are measured on the ellipsoid too, and the column's
      header tooltip gives the largest relative difference,
      |value − reference| / reference, over its ROIs. On a Mars 2000 sphere
      (R = 3396.19 km) at 85°N it's about 0.59% for a perimeter and 1.2%
      for an area (both directions of the area scale); on the HiRISE polar
      sphere there, about 0.009% for an area. Without one (the
      Moon, or a CRS already on the reference ellipsoid) nothing is shown.
    - **The tooltip** also names the method and surface, e.g. "Geodesic
      (Karney 2013) on sphere R = 3376.20 km, edges densified to 1 pixel".
    - **Blank values:** a value is blank when it can't be measured, and the
      tooltip says for which ROIs and why: the image has no map
      coordinates, or some of the ROI's points are outside where the
      projection can be converted (e.g. off the visible disk of an
      orthographic image).
    - **Limits:** areas are horizontal, on the reference surface, not along
      the terrain (topography isn't included); they're only as good as the
      image's georeferencing, which Varda can't know; and the edges follow
      the map's straight lines, not geodesics, as drawn on the image.
  - **Area (map units², planar)**, **Perimeter (map units, planar)**,
    **Centroid x (map units)** and **Centroid y (map units)**: in the
    image's coordinate system, measured on the flat map (planar, not
    geodesic). They're available only for georeferenced images. In a
    projected system they're in its unit (usually meters), and carry the
    projection's scale distortion away from its true-scale latitude or
    center; the Measure list's tooltip on the planar area and perimeter
    gives the worst distortion over the image (see [Projection
    distortion](coordinate-systems.md#the-distortion-check)). In a
    geographic system (longitude and latitude) they're in degrees and
    degrees², which aren't areas on the ground. For areas and perimeters on the ground use **Area on the
    body** and **Perimeter on the body**.
- **Zonal statistic of a raster**: **mean**, **median**, **std**
  (population), **min**, **max**, **sum** or **count** of one band inside
  each ROI, on the ROIs' image or any image on the same pixel grid (never
  resampled; images on other grids are named under the **Raster** dropdown,
  with why). Same pixel rule as above; **count** is the number of valid (non-no-data) pixels in
  that band.

  Three more statistics weight each pixel by its **ground area** (georeferenced
  images only):
  - **area of pixels with data (km²)**: A = Σ aᵢ, the summed ground area
    of the ROI's pixels with a valid value in the band.
  - **area-weighted mean**: Σ vᵢ aᵢ / Σ aᵢ. On a map whose pixels differ
    in ground area (an equirectangular map away from its true-scale
    latitude, a polar stereographic one), it's the mean per unit of ground,
    where the plain **mean** is per pixel; for pixels of equal area the two
    are the same.
  - **sum × pixel area (value·km²)**: Σ vᵢ aᵢ, e.g. a column density
    times area for a total.

  Here vᵢ is a pixel's value and aᵢ its ground area in km², on the CRS's own
  surface, by the method in [Pixel
  areas](coordinate-systems.md#pixel-areas): exact per row for north-up
  geographic, equirectangular, Mercator and cylindrical equal-area images,
  otherwise the map pixel area over the projection's areal scale at the
  pixel center. The column's header tooltip names the method and surface,
  e.g. "Pixel areas exact per row (geodesic polygon, edges densified to 16
  points) on sphere R = 1737.40 km". Without map coordinates the values are
  blank, and the tooltip says so ("the image has no map coordinates"). An
  ROI with no pixels with data has an **area of pixels with data** of 0 and
  a blank **area-weighted mean** and **sum × pixel area**.
  - **Whole images, computed once, in the background.** Varda computes
    the areas of all of an image's pixels once, in the background, then
    reuses them for every ROI and column until the image's coordinate
    system or transform changes. Until they're ready the columns are blank
    and their tooltip says "computing the pixel areas…"; a result for a
    coordinate system that changed meanwhile is discarded and computed
    again. Outside the exact-per-row case, an image of more than 4,000,000
    pixels has its areas interpolated from a grid of every n-th pixel,
    within 10⁻⁶ (relative) of the evaluated ones (see [Pixel
    areas](coordinate-systems.md#pixel-areas)). The areas are kept as
    32-bit floats (relative rounding about 6 × 10⁻⁸, well inside that
    bound; sums are taken in 64 bits), up to 512 MB for all images
    together, the least recently used dropped first. If the computation
    fails (logged), the columns stay blank.
  - **Another image's coordinate system.** A column computed on another
    image on the same pixel grid updates when the ROIs change, and when
    the ROIs' own image changes its coordinate system, but not when only
    that other image's coordinate system is changed. Add the column again
    after changing it.
  - **Whole pixels, not the polygon.** These count whole pixels (those
    whose centers are inside the ROI, with data in the band), so **area of
    pixels with data** differs from **Area on the body**, which measures
    the drawn polygon: by up to half a pixel along the perimeter, and by
    every no-data pixel inside. Report which you used, and its method.
  - **Uncertainty.** The area method's own error is below 10⁻⁶ (relative)
    for map pixels of a few km or less (see [Pixel
    areas](coordinate-systems.md#pixel-areas)). The larger terms are the
    image's georeferencing (a wrong pixel size or CRS scales every area;
    Varda can't check it) and the pixel-selection rule above, whose
    relative effect is about the perimeter in pixels / (2 × the pixel
    count): small for large ROIs, large for ROIs a few pixels across. Areas
    are on the reference surface, not along the terrain. A pixel whose area
    can't be computed (past the edge of where the projection can be
    converted) leaves the value blank.
- **Expression**: arithmetic on other columns, with `sqrt`, `log`, `log10`,
  `exp`, `abs`, `min`, `max`, `round`, `pi` and `e`. A value that can't be
  computed is left blank.

Calculated columns update as ROIs change. The table shows six significant
figures. **Export...** writes the ROIs and their columns as GeoJSON,
GeoPackage or a shapefile. **Import…** adds polygons from a GIS file as ROIs,
with its columns as table columns (see
[Importing vector data](importing-vector-data.md)).

### Uncertainty of a mean spectrum

The standard deviation is the **spread within the ROI**: real variation
across the surface plus noise. It isn't the uncertainty of the mean. For
that, use the standard error of the mean, per band:

  SE = σ / √n

where σ is the standard deviation and n the band's valid pixel count.

Neighboring pixels aren't independent. The instrument's point-spread
function, resampling in map projection, and real geology at scales larger
than a pixel all make nearby pixels alike, so the n pixels carry less
information than n independent samples. SE = σ/√n then **understates** the
uncertainty. Report it as a lower bound, or use an effective n (for example,
the number of point-spread-function footprints or resampling blocks in the
ROI) and say which you used. Report n, σ and how the ROI was drawn alongside
any mean spectrum.

## Ratio Explorer

Ratio spectra divide a spectrum of interest by a nearby, spectrally bland
one, to cancel shared atmospheric and instrument effects. Turn on **Ratio
Explorer** under a view (General Image Analysis and the dual-image
workspace), then
Ctrl-click (Cmd-click) for the numerator box and Ctrl-right-click for the
denominator box. **Box Width** and **Box Height** (5 px each by default,
1–99) apply to both. The ratio is plotted in **Ratio Spectra**. Press S (or
**Save Boxes as ROIs**) to keep both boxes as ROIs; Esc clears them.

With **Lock to sensor column** (in the ROI Manager) on, the denominator box
moves to the numerator's detector column, using the CRISM geometry file
found beside the image. Without that file, a denominator click places the box
where you clicked.
Placing a new numerator with the lock on also moves the existing denominator
into the new numerator's column, keeping its row and box size. If the lock
can't be satisfied (the numerator's observation strip has no pixels on the
denominator's row, or there is no geometry file), the denominator stays where
it is and a message at the top of the view says so.

The plot shows the numerator and denominator spectra (their box means, with
the same units as the image) next to the ratio, labeled "Numerator (col, row)"
and "Denominator (col, row)", so you can see which one a feature comes from.

The ratio is, band by band, the **mean of the numerator box divided by the
mean of the denominator box** (not the mean of pixel-by-pixel ratios). A band
where the denominator mean is zero, or either mean is missing, is left blank.
ROIs work the same way: **Set as Denominator** on one, then **Plot Ratio
Spectrum** on another.

### Uncertainty of a ratio

Varda doesn't compute an uncertainty for ratios. To first order, with
independent errors in the numerator mean *a* and the denominator mean *b*:

  σ_R / R = √( (σ_a / a)² + (σ_b / b)² ),  with σ_a = s_a / √n_a and σ_b = s_b / √n_b

where *s* is each box's standard deviation in that band and *n* its pixel
count. This holds while each relative error is small (under about 10 %). It
inherits the spatial-correlation caveat above, and errors shared by the two
boxes (such as residual detector striping in the same column) aren't
independent.

To get the numbers, save the boxes as ROIs and add **mean**, **std** and
**count** columns for the bands you report.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

- For figures: the bands, stretch and limits, as in [the caption
  section](#stating-the-display-in-a-figure-caption), whether library
  spectra are scaled and offset, and which library each came from (cite it;
  see [Spectral libraries](spectral-libraries.md#reporting-and-citing)).
- For spectra: whether each is a single pixel, a box (its size) or an ROI
  (its shape, how and where it was drawn, its pixel count), and that the
  plotted spread is ±1 population standard deviation.
- For ratios: the numerator and denominator areas, their sizes and pixel
  counts, whether the denominator was locked to the same detector column,
  and that the ratio is of the two means.
- For ROI measures: whether areas are in pixels, map units (planar) or on
  the body, and the coordinate system. For areas and perimeters on the
  body: "measured geodesically (Karney, 2013) on [the surface in the
  column's tooltip, e.g. the 3376.2 km sphere of the HiRISE polar
  products], ROI edges densified to one pixel", the body-model difference
  from the tooltip when there is one (e.g. "within 0.009% of the IAU 2015
  reference ellipsoid"), and that the areas are horizontal (no topography).

### Uncertainty and quality

- **A standard deviation is spread, not error.** Give σ/√n (with n) for a
  mean, and say that spatial correlation makes it a lower bound.
- **Ratios** carry the propagated error above; Varda doesn't report it.
- **Map areas** are only as good as the image's georeferencing and carry the
  projection's distortion. Areas in pixels are in pixel units, whatever
  the pixels' size on the ground.
- **Areas and perimeters on the body** carry no projection distortion. Their
  uncertainty is the image's georeferencing error (a shifted or scaled
  image shifts or scales the ROI), the choice of body model (the
  body-model difference in the tooltip bounds it), and the ROI as drawn;
  topography isn't included.
- **Library comparisons** are qualitative while the library curve is scaled:
  the match of shapes says nothing about absolute reflectance. Library
  spectra are also at their own spectral resolution, often finer than your
  sensor's.
- **Stretches** don't affect any number on this page.
- **Overlays reprojected for display** are placed within ½ overlay pixel
  plus ½ view pixel (nearest neighbor) and never feed a number on this
  page; see [Overlays](#overlays).

### References

- Archinal, B. A., et al. (2018). Report of the IAU Working Group on
  Cartographic Coordinates and Rotational Elements: 2015. *Celestial
  Mechanics and Dynamical Astronomy*, 130, 22.
  https://doi.org/10.1007/s10569-017-9805-5
- Karney, C. F. F. (2013). Algorithms for geodesics. *Journal of Geodesy*,
  87, 43–55. https://doi.org/10.1007/s00190-012-0578-z
- USGS spectral library: Kokaly, R. F., Clark, R. N., Swayze, G. A., Livo,
  K. E., Hoefen, T. M., Pearson, N. C., Wise, R. A., Benzel, W. M., Lowers,
  H. A., Driscoll, R. L., & Klein, A. J. (2017). USGS Spectral Library
  Version 7. *U.S. Geological Survey Data Series 1035*.
  https://doi.org/10.3133/ds1035
- CRISM MICA library: Viviano-Beck, C. E., Seelos, F. P., Murchie, S. L.,
  et al. (2014). Revised CRISM spectral parameters and summary products
  based on the currently detected mineral diversity on Mars. *Journal of
  Geophysical Research: Planets*, 119(6), 1403–1431.
- Your own and loaded libraries: see [Spectral libraries](spectral-libraries.md#reporting-and-citing).
