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
A line's **Length** is measured the same way as profile distances (on the
body, in meters, when the image is georeferenced). Hover over it to see how:
the method and surface (e.g. "geodesic (Karney 2013) on sphere R = 3396.19
km"), the body-model difference when it's over 0.1%, and the projection's
distortion along the line when it's over 1% or 1° (a tip only: the length
doesn't use it). A length Varda can't measure (part of the line can't be
converted to longitude and latitude) shows as "—".
On a georeferenced image, a 3D Terrain workspace can drape them over a DEM
(its **Add Lines** menu lists the open workspaces' lines; see
[3D Terrain](3d-terrain.md)).

## Profiles

The **Profiles** tab plots the selected lines, or every line when none is
selected, one curve per line in the line's color. Choose the **band** to
sample (a DEM has one).

- Samples are about a pixel apart along the line, interpolated between pixel
  centers; no-data shows as gaps.
- Distance is in meters for georeferenced images, measured **on the body**
  (geodesic) in every coordinate system, projected or longitude and
  latitude, so the map projection's scale error never enters it (see
  [How Varda measures](coordinate-systems.md#how-varda-measures)). Profiles
  5 km or longer are plotted in kilometers. Without georeferencing,
  distance is in pixels.
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
CRS (described in words, as in
[Coordinate systems](coordinate-systems.md), and its full WKT), the distance unit and how it
was measured, the projection's distortion along each line (not used in the
distances), the distances' limits, and the band with its unit when Varda knows it (results of
Varda's analyses carry one; most files don't). Spreadsheets and pandas
(`comment="#"`) skip or read these lines. For the real CRISM MTRDR
coordinate system (a 3394.84 km sphere) the distance lines read, e.g.:

```
# distance unit: m (geodesic, Karney 2013, on sphere R = 3394.84 km; IAU 2015 reference ellipsoid differs by 0.017%)
# projection distortion along the line: up to 1.6% scale error (east–west) at 18.0°N (not in the distances)
# distance limits: horizontal, on the reference surface (not along topography); samples about 1 px apart; plus the image's georeferencing error, which Varda can't know
```

With several lines, the body-model difference is the largest of them ("up
to"), and each line gets its own distortion line. Samples PROJ can't convert
to longitude and latitude (off the disk of an orthographic image, say) have
an empty distance, as do all after them on their line, and a
`distance gaps:` line says so.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

- The image (for a DEM: its product, source, resolution and stated vertical
  precision) and the band profiled.
- Where the lines are: export them (GeoJSON, GeoPackage or shapefile), or
  give the end points' map coordinates from the CSV.
- How distance was measured: geodesic (Karney, 2013) on the sphere or
  ellipsoid of the image's CRS, named with its radius as the CSV header
  gives it (e.g. "on the 3394.84 km sphere of the CRISM MTRDR CRS"), in any
  projection; the body-model difference from the header, with the
  reference it compares to (the IAU 2015 ellipsoid; Archinal et al., 2018);
  and that distances are horizontal, not along topography. For example:
  "Distances are geodesic (Karney, 2013) on the CRS's 3394.84 km sphere
  (0.02% from the IAU 2015 Mars ellipsoid), horizontal, plus the image's
  georeferencing error."
- That values were interpolated bilinearly about one pixel apart.

How profiles are sampled:

| Step | What Varda does |
| --- | --- |
| Spacing | Each segment between vertices is cut into equal steps of at most 1 pixel; every vertex is a sample. |
| Values | Bilinear interpolation between the four nearest pixel centers. If any of them with a nonzero weight is no data (or NaN), the sample is no data. Within half a pixel of the image's edge, the edge pixels' values are used. Off the image, no data. |
| Distance (any CRS) | Each sample's map position is converted with PROJ to longitude and planetocentric latitude on the CRS's own sphere or ellipsoid, then to that surface's geodetic (planetographic) latitude, and the geodesic distances between successive samples on that surface are summed (pyproj's `Geod.inv`, Karney's algorithm). The path is the line as drawn on the image (straight on the map), not a single great circle between its ends. |
| Engineering or local CRS | A CRS on no body (no sphere or ellipsoid, e.g. a lab or rover frame) can't be measured on a body. With linear units, distances are planar: straight-line steps between samples in map coordinates, converted to meters with the units; the CSV header and the length tooltip say "planar, in the coordinate system's units (no body ellipsoid)". With unknown or angular units, distances are in pixels, as without georeferencing. |
| Line length | The same sum along the line densified to about 1 pixel, so it equals the profile's last distance. |
| Body-model term | When the CRS's surface differs from the body's IAU 2015 reference ellipsoid by more than 1 m, the same points are measured on that ellipsoid too and the relative difference is reported (see [How Varda measures](coordinate-systems.md#how-varda-measures)). |
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
- **Distances are geodesic on the CRS's surface, whatever the
  projection.** The projection's scale error doesn't enter them (an
  east–west line at 60°N on a 0°-centered equirectangular map is measured
  at its true length, not twice it). The CSV records the projection's
  distortion along each line only so you know how far the map's own
  shapes and slopes are off. The geodesic algorithm's error is a few
  nanometers; following the line drawn on the image adds at most about a
  pixel.
- **The body-model term.** Distances are on the surface the data were
  projected on (as ISIS, ArcGIS and QGIS measure). When that's a sphere on
  a flattened body, or a nonstandard radius, the header gives how much the
  same line differs on the body's IAU 2015 reference ellipsoid: up to about
  0.6% on Mars, 0 on the Moon. Report it, or use it as the uncertainty of
  the choice of surface.
- **Distances are horizontal.** They're on the reference surface, not along
  topography: on a slope the path over the ground is longer.
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
- Reference ellipsoids: Archinal, B. A., et al. (2018). Report of the IAU
  Working Group on Cartographic Coordinates and Rotational Elements: 2015.
  *Celestial Mechanics and Dynamical Astronomy*, 130, 22.
  https://doi.org/10.1007/s10569-017-9805-5
- Map projection scale factors: Snyder, J. P. (1987). *Map Projections: A
  Working Manual* (USGS Professional Paper 1395).
- Line simplification: Douglas, D. H., & Peucker, T. K. (1973). Algorithms
  for the reduction of the number of points required to represent a
  digitized line or its caricature. *The Canadian Cartographer*, 10(2),
  112–122.
- PROJ, through pyproj, does the coordinate work: give the PROJ version from
  **Help → Copy System Info**.
