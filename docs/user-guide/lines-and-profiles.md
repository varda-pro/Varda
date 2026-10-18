# Lines and profiles

Draw lines on an image and plot a band along them: most often a topographic
profile across a DEM. Lines work in the General Image Analysis workspace.

## Draw a line

1. In the **Lines** tab (beside the ROI Manager), pick what to draw:
   - **Straight**: drag from one end to the other, or click both ends.
   - **Polyline**: click each vertex; double-click, right-click or press
     Enter to finish. Backspace removes the last vertex.
   - **Freehand**: press, drag and release. The stroke is thinned to a
     polyline that follows it within three quarters of a pixel.
2. Turn on **Draw Line** under any view and draw. It stays on, so you can draw
   line after line. Esc drops the line in progress; Cmd-drag (Ctrl-drag on
   Windows and Linux) pans the view, even partway through a line.

Each line is shown on every view in its color. In the Lines tab you can
rename a line (double-click its name), change its color (double-click the
swatch), delete lines, and export them as GeoJSON, GeoPackage or a
shapefile, in the image's map coordinates. **Import…** adds lines from a GIS
file, such as a rover traverse or mapped contacts (see
[Importing vector data](importing-vector-data.md)). Lines are saved with the
session.
On a georeferenced image, a 3D Terrain workspace can drape them over a DEM
(its **Add Lines** menu lists the open workspaces' lines; see
[3D Terrain](3d-terrain.md)).

## Profiles

The **Profiles** tab plots the selected lines, or every line when none is
selected, one curve per line in the line's color. Choose the **band** to
sample (a DEM has one).

- Samples are about a pixel apart along the line, interpolated between pixel
  centers; no-data shows as gaps.
- Distance is in meters for georeferenced images: straight-line on the
  map in a projected image, along the body's surface (geodesic) in longitude
  and latitude. Profiles 5 km or longer are plotted in kilometers. Without
  georeferencing, distance is in pixels.
- Hover over the plot to read the distance and each line's value there (the
  nearest sample at or past that distance); a white × marks the same spot
  on the image.
- When distance is in meters, the **vertical exaggeration** of the current
  view is shown under the plot and changes as you zoom. It assumes the
  values are in meters too, as a DEM's usually are.

**Export…** saves every sample as CSV, or the plot as PNG (at least 2000 px
wide), SVG or PDF for a figure. The CSV's columns are `line` (its name),
`distance_m` (or `distance_px`), `x_px` and `y_px` (the image position, with
pixel corners at whole numbers, so a pixel's center is at .5), `map_x` and
`map_y` (in the image's map units, for georeferenced images), and the value,
headed with the band's name; no data is left empty. Above the columns, lines
starting with `#` record the Varda version, the image's name and file, its
CRS (name, EPSG code if any, and full WKT), the distance unit and how it
was measured, and the band with its unit when Varda knows it (results of
Varda's analyses carry one; most files don't). Spreadsheets and pandas
(`comment="#"`) skip or read these lines.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

- The image (for a DEM: its product, source, resolution and stated vertical
  precision) and the band profiled.
- Where the lines are: export them (GeoJSON, GeoPackage or shapefile), or
  give the end points' map coordinates from the CSV.
- How distance was measured: geodesic on the ellipsoid or sphere of the
  image's CRS (name the CRS), or planar in the named map projection.
- That values were interpolated bilinearly about one pixel apart.

How profiles are sampled:

| Step | What Varda does |
| --- | --- |
| Spacing | Each segment between vertices is cut into equal steps of at most 1 pixel; every vertex is a sample. |
| Values | Bilinear interpolation between the four nearest pixel centers. If any of them with a nonzero weight is no data (or NaN), the sample is no data. Within half a pixel of the image's edge, the edge pixels' values are used. Off the image, no data. |
| Geographic distance | The sum of geodesic distances between successive samples on the CRS's own ellipsoid or sphere (pyproj's `Geod.inv`, which uses Karney's algorithm). The path is the line as drawn on the image, straight in longitude and latitude, not a great circle. |
| Projected distance | The sum of straight-line distances between samples in map coordinates, converted to meters with the CRS's unit. |
| Freehand lines | Douglas–Peucker simplification, tolerance 0.75 pixels. |
| CSV precision | Distances and map coordinates in full (the shortest decimal that reads back as the same 64-bit number), pixel positions to 3 decimals, values to 10 significant digits. |

### Uncertainty and quality

- **Values inherit the image's error.** A DEM profile carries the DEM's
  vertical error; report its producer's stated precision. Bilinear
  interpolation smooths the values a little, so narrow peaks and pits read
  lower and higher than the DEM's own pixels.
- **One no-data pixel blanks nearby samples.** Any sample within a pixel of
  a no-data pixel is a gap, so gaps look slightly wider than the no-data
  area.
- **Projected distances include the projection's scale error.** A map
  projection's distances are true only where its scale factor is 1 (along
  an equirectangular projection's standard parallel, for example), and Varda
  doesn't correct them. Over long lines, or far from where the projection
  is true, compute geodesic distances from the CSV's map coordinates (with
  pyproj, say) or profile a copy of the image in longitude and latitude.
- **Distances are only as good as the georeferencing.** See
  [Reporting results from Varda](reporting-results.md#precision-is-not-accuracy).
- **The CSV's header records the image and CRS** but not, for most
  images, the values' unit: note it alongside the file when the header
  says it isn't recorded.
- **The vertical exaggeration** shown under the plot is the plot's aspect
  ratio, right only when values are in meters. It's for figure captions,
  not a measurement.

### References

- Geodesics: Karney, C. F. F. (2013). Algorithms for geodesics. *Journal of
  Geodesy*, 87(1), 43–55.
- Line simplification: Douglas, D. H., & Peucker, T. K. (1973). Algorithms
  for the reduction of the number of points required to represent a
  digitized line or its caricature. *The Canadian Cartographer*, 10(2),
  112–122.
- PROJ, through pyproj, does the coordinate work: give the PROJ version from
  **Help → Copy System Info**.
