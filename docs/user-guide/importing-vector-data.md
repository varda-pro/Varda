# Importing vector data

Bring mapped polygons, points and lines from GIS files into Varda: **Import…**
in the **ROI Manager** (polygons become ROIs), the **Points Manager** (points)
and the **Lines** tab (lines). Each import adds to what's already there; it
can't be undone, but you can delete what it added.

1. Click **Import…** and choose a file. The file dialog opens in the folder
   you last imported from.
2. Varda reads the file in the background, then shows the import dialog:
   the layer, how to read the coordinates, which columns give names and
   classes, and a preview.
3. Click **Import**. A message says how many features were imported and
   what was skipped, and why.

## Formats

- Anything GDAL reads through pyogrio: **Shapefile** (`.shp`, with its
  `.dbf`, `.shx` and `.prj`), **GeoPackage** (`.gpkg`; choose the **Layer**
  when it has several), **GeoJSON**, **KML**, **FlatGeobuf** (`.fgb`),
  **GPX** and more (choose **All files**). A layer with no attribute columns
  (geometry only) imports like any other.
- **CSV of points**: one row per point. Varda looks for longitude and latitude
  columns (`lon`, `longitude`, `long`, `lng` and `lat`, `latitude`), then
  ISIS `sample` and `line` columns, then x and y columns (`x`/`y`,
  `easting`/`northing`, `col`/`row`), then the first two numeric columns,
  ignoring case; change them with **X column** and **Y column**. Rows without
  both coordinates are skipped. The other columns are attributes.
- **ISIS sample and line** (as `campt` and `qview` write them) are 1-based,
  with whole numbers at pixel *centers*: sample 1, line 1 is the center of
  the first pixel. Varda's pixel coordinates are 0-based with whole numbers on
  pixel *edges*, so Varda subtracts 0.5 from each: sample 1, line 1 becomes
  (0.5, 0.5), the first pixel's center, where Varda puts a point saved from
  that pixel. The dialog says so. Columns with other names (`col`/`row`,
  `x`/`y`) are taken as Varda's own pixel coordinates, unchanged.

## Coordinates

**Coordinates** says what the file's coordinates are. The dialog shows the
coordinate system Varda **detected** and how.

- **The file's coordinate system** (the default when the file has one):
  coordinates are reprojected from it to the image's coordinate system with
  PROJ (through pyproj), then to pixels with the image's geotransform.
- **Degrees on Mars** (named for the image's body): the coordinates are
  longitude (east) and latitude in degrees on the image's body, in the
  image's own geographic coordinate system, whatever the file says. The body
  is identified from the image's coordinate system (its `IAU_2015` code, the
  names in it, else its radius within 1 % of one body's IAU 2015 radii); an
  image on no known body is offered **Degrees on the image's body**, and its
  bodies are then not compared with the file's.
- **The image's map coordinates**: the coordinates are already in the image's
  coordinate system; no reprojection, only the geotransform.
- **Image pixels (column, row)**: x is the column and y the row, 0-based, with
  whole numbers on pixel edges (the center of the top-left pixel is 0.5,
  0.5), as Varda exports features of an image without map coordinates.
  Integer pixel indices (numpy's, or a table's 0-based column and row, such
  as Varda's `pixel_x` and `pixel_y`) therefore land on pixel corners: points
  still fall in the right pixel, but line and polygon vertices are half a
  pixel up and left of the pixel centers, and a point's exported position is
  the corner. Add 0.5 to such indices first if that matters.
- **Another coordinate system**: type it in **CRS** (an EPSG code such as
  `EPSG:4326`, WKT or a PROJ string) when the file's is missing or wrong.

How Varda decides:

- **A file without a coordinate system** (a shapefile without a `.prj`, a
  GeoPackage layer without one, a CSV of x and y): the dialog says so and
  asks. Varda starts with **Image pixels** when every coordinate fits inside
  the image's pixel extent, otherwise **The image's map coordinates**; check
  which is right.
- **An image without map coordinates**: only pixel coordinates can be placed
  on it. A file in a map coordinate system is refused ("This image has no map
  coordinates…") unless you choose **Image pixels**.
- **Earth degrees on another body.** GeoJSON always declares WGS 84 (Earth
  longitude and latitude), even for Mars coordinates, and even when it was
  written from a projected coordinate system it can't record. So when a file
  says its coordinates are WGS 84 degrees (any format: GeoJSON, a CSV's
  longitude and latitude, a shapefile whose `.prj` says WGS 84) and the image
  is of another body, Varda starts with **Degrees on Mars** (the image's
  body). It's a choice you can see and change: **The file's coordinate
  system** is refused then, since Earth coordinates can't be placed on Mars,
  and so is `EPSG:4326` typed as another coordinate system.
- **Coordinates that can't be degrees**: when a GeoJSON's coordinates are
  beyond ±360 or ±90, its real coordinate system was lost: Varda shows it as
  unknown and starts with **The image's map coordinates**.
- **Varda's own exports** (ROIs, points and lines) carry a `crs` column with
  the coordinate system's WKT. Varda uses it when the file's own is missing,
  is GeoJSON's WGS 84, or gives the same coordinates (GDAL rewrites some
  projections when it stores them, such as a polar stereographic's scale
  factor; the difference is micrometers). A shapefile cuts the column to 254
  characters, so there the `.prj` is used.
- **Different bodies**: a file in a projected Earth coordinate system (UTM,
  say) can't be placed on a Mars image; Varda refuses rather than guess.
- **Nothing on the image**: when every feature falls outside the image, the
  preview says to check the Coordinates setting; Import stays off.

### Longitude conventions

Longitudes are either 0 to 360° E (the PDS convention for Mars) or −180 to
180° (most GIS tools and GeoJSON). Varda puts a file's longitudes in the
image's convention by adding or subtracting multiples of 360°, so each lands
within 180° of the image's center, and the import message says how many it
shifted:

- **An image in degrees** (longitude and latitude): longitudes are wrapped
  around its center longitude, so a point at −90° lands on a 0–360° image at
  270° E, and one at 270° on a −180–180° image at −90°.
- **A projected image**: the projection wraps longitudes around its central
  meridian itself. When the image reaches more than 180° from that meridian
  (a mosaic over 180° E in a projection centered on 0°), Varda projects with
  PROJ's `+over`, which keeps longitudes as they are, after wrapping them
  around the image's center; so 182° E and −178° land on the same pixel.

## What's imported, and what's skipped

Each manager takes one kind of geometry:

| Manager | Takes | Each feature becomes |
|---|---|---|
| ROI Manager | Polygon, MultiPolygon | one ROI per polygon; a multipolygon's polygons share the name, so they are one class |
| Points Manager | Point, MultiPoint | one point per point; a multipoint's are numbered (`name 1`, `name 2`) |
| Lines tab | LineString, MultiLineString | one line per line string; numbered like points |

Geometry collections are taken apart. Everything else is **skipped and
counted**: for example "12 points skipped: use the Points Manager to import
them". So are:

- features **entirely outside the image** (no part on its pixels; a point
  outside every pixel). Features **partly inside are kept whole**, not
  clipped.
- features whose coordinates **PROJ couldn't convert** (such as a latitude of
  95°), counted apart.
- features **without a geometry**.

**Invalid polygons** (self-intersecting ones, such as a bow tie) are imported
as they are, and the import message counts them: their pixels and statistics
are ill-defined, so repair them in a GIS first.

Z (height) and M values are dropped: features are placed in 2D. A large
import is added all at once, so the table and views update once.

## Names, classes and other columns

- **Name from**: the column that names each feature. Varda guesses `name`,
  `label`, then `id` (in any case). With **(none)**, points and lines are
  numbered as when drawn, and ROIs are named after the file.
- **Class from** (ROIs only): in Varda, ROIs that share a name are one class
  (the ROI table's **Class**), so the class column names the ROIs. Varda
  guesses `class`, `type`, then `unit`. With a class column chosen, the name
  column is kept as an ROI table column. A class takes its color from the
  file's `color` column, or from an existing class of that name, or else the
  next palette color.
- **Other columns** become ROI table columns, or the points' and lines'
  properties; they're exported with them. A `name` column that isn't used for
  names is kept as `source_name`. Missing values are left blank; dates
  become ISO 8601 text. Points aren't kept in sessions yet, so export
  imported points (with their properties) before closing; lines and ROIs are
  kept.
- Varda's own columns are read back: `color` (`#rrggbb` or `#rrggbbaa`),
  `roi_type` (ROIs), `description` (points; `descriptio` in a shapefile) and
  `kind` (lines: Straight, Polyline or Freehand; lines without one are
  Straight with two vertices, Polyline otherwise). Columns Varda derives from
  the image are recomputed, not kept: `crs` (all), `fid` (ROIs), `image`,
  `pixel_x` and `pixel_y` (points), `length` and `units` (lines).

So Varda's own exports round-trip: importing an export onto the same image
gives the same geometry (to floating-point precision; to micrometers for a
shapefile in a projection GDAL rewrites), names, colors, types and extra
columns, for planetary coordinate systems without an EPSG code too.

## Accuracy

- **Reprojection** is PROJ's transformation between the two coordinate
  systems, as pyproj chooses it. Between datums without a known
  transformation (or between planetary coordinate systems on spheres of
  different radii) PROJ uses a "ballpark" transformation with no datum
  shift, which can be off by meters on Earth. When it matters, reproject the
  file yourself with a stated transformation, or give the file in the image's
  coordinate system.
- **Degrees on another body**: latitudes are read in the image's own
  geographic coordinate system. On a sphere that's planetocentric; on an
  ellipsoid (such as an IAU Mars 2000 ellipsoid) it's planetographic. Check
  which your file uses.
- **Vertices are reprojected, not the edges between them**: a long straight
  edge in one projection is a curve in another. Densify long edges before
  exporting if that matters at your scale.
- **The image's georeferencing limits everything**: features land where the
  image's geotransform says, so their placement is no better than the
  image's registration (often several pixels for orbital data). State it.
- **Points snap to a pixel**: a point is saved on the pixel it falls in
  (its **X** and **Y** in the Points Manager), and its spectrum is that
  pixel's. Its exact position is kept as its geometry and exported. A point
  on a pixel edge belongs to the pixel to its right or below. Sub-pixel
  positions don't give sub-pixel spectra.
- **ROI pixels**: an ROI's statistics use the pixels whose centers are inside
  its outer ring (see [ROI statistics](viewing-and-measuring.md#roi-statistics));
  holes in imported polygons are kept in the geometry and exported, but not
  yet drawn or excluded from the statistics.

To report an import, state the file and layer, the coordinate system it was
read in (and whether it was taken as degrees on the image's body), any
longitude shift, the image's coordinate system, how many features were
imported and skipped (the import message), and the image's registration
accuracy.
