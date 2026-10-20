# Linked Images

**Workspace → New Linked Images Workspace…** views any number of images
side by side, linked so that one cursor and one line read them all. It's
made for checking the products of a processing pipeline against each other:
a result next to its sample counts, flags and exclusions, say, all on one
grid.

## Opening one

The dialog lists the loaded images, with those on the first image's pixel
grid already ticked. Choose two or more, and how to link them:

- **By pixel:** the same pixel in every image. Use it for products of one
  pipeline, which share a grid.
- **By map location:** the same place on the ground. Use it for images on
  different grids or map projections (each must be georeferenced; an image
  without georeferencing is linked by pixel, and a notice across the top of
  the workspace names it).

Linking by pixel images that aren't on the first image's grid (another size
or georeferencing) pairs different places, so a notice across the top of the
workspace names those images too.

Images of different bodies (or with an Earth coordinate system on planetary
data) are never linked by map location: there is no "same place" between
them. The notice names them and their body, e.g. "EARTH: on Earth, not Mars;
not linked" (bodies compared with the first georeferenced image). On them,
the cursor isn't shown, their spectrum plot says "off the image", panning
doesn't move them, and a line drawn on one isn't added (the status bar says
why). Images whose body Varda can't identify are linked when their radii
match, and otherwise treated the same way.

## Reading every image at once

- The views pan and zoom together.
- With **Pixel Select** on, a **click** in any view puts the cursor (yellow) on every view, and the
  **Linked Spectra** panel plots each image's spectrum there, one plot per
  image so that each keeps its own units (a spectrum, a count, a flag); each
  plot pans and zooms on its own. Each image is read at the pixel the
  cursor falls in (not interpolated), and its plot's title gives that
  pixel's column and row (counted from 0). **Ctrl+drag**
  (⌘-drag on a Mac) updates the spectra as you go. Bad bands and no-data values show as gaps.
  **Export…** saves the spectra at the cursor as CSV, with columns `image`,
  `col` and `row` (the pixel read, counted from 0), `map_x` and `map_y`
  (the cursor's position in that image's CRS; empty without
  georeferencing), `x` and `x_unit` (the wavelength and its unit, such as
  nm or µm, or the band number with `x_unit` "band") and `value` (empty
  for no data and bad bands). Lines starting with `#` above the columns
  give the Varda version and each image's CRS (name and WKT).
- A **bit mask** shows as bits by wavelength instead of a number: a
  pipeline's per-frame exclusions (bit *i* set where frame *i* of a stack
  was left out) become a chart of which frames were left out at each
  wavelength, titled with how many. Bit 0 is the least significant (value
  1). Whole-number images whose names contain "exclu", "mask", "bits",
  "quality" or the word "QA" (in any case) start that way; the **As Bits**
  button switches any whole-number image. **Processing → Statistics → Decode Bit
  Mask** turns a mask into images: the number of bits set, one bit, or any
  of several.
- **Draw Line** in any view draws a line on all of them, and the **Linked
  Profiles** panel plots each image's profile along it (pick the band for
  each). The **Lines** panel renames, recolors, deletes and exports lines;
  selecting lines shows only their profiles. See
  [Lines and profiles](lines-and-profiles.md).
- The **Display** panel sets each image's bands, stretch and color map,
  with its histograms below.
- **Crop** makes a cropped copy of the image it's used on.

## Arranging the windows

Every view and panel is a window of its own inside the workspace: drag one
by its title to put it beside, above or below another, drop it on another
to share a tab, or float it (its title bar's button) and put it on a second
screen. The views start in a grid, with Display and Lines on the left and
the spectra and profiles on the right. Views can't be closed, so none gets
lost; panels can, and drawing a line brings Linked Profiles back.

The images, the link, the cursor, the lines and each image's display
settings are saved with the session.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

Linking only finds where to read each image; it doesn't change or resample
any of them. What you report is the images' own values at those places.

### What to state

- The images compared, and the link (by pixel, or by map location).
- For values you report: each image's pixel (column and row, counted from
  0) or map location, as the plot titles and the exported CSV give them.
- For profiles across images, the same as for any
  [profile](lines-and-profiles.md#reporting-your-results), per image.

How images are linked:

| Step | What Varda does |
| --- | --- |
| By pixel | The same column and row in every image. Images not on the first image's grid (another size, CRS or transform) are named in a notice across the top of the workspace. |
| By map location | The point's map coordinates in the georeferencing of the image you clicked, converted to each other image's CRS at the same planetocentric longitude and latitude (PROJ through pyproj, plus the latitude conversion between a sphere and an ellipsoid; see [Moving points between surfaces of one body](coordinate-systems.md#moving-points-between-surfaces-of-one-body)), then to its pixel through its georeferencing. CRSs that differ only in names (e.g. GDAL's PDS3 and ENVI readings of one CRISM product) need no conversion. Images of another body aren't converted at all: they aren't linked. No co-registration is done. |
| Values | The spectrum of the pixel the point falls in (nearest pixel, no interpolation). |
| Lines | Vertices are kept in the first image's coordinates and moved to each image as above; each image's profile is then sampled along straight segments between them on its own grid. |
| Bits | Bit *i* is (value >> *i*) & 1, so bit 0 is the least significant. A signed image's values are read as the unsigned integer of the same width (an int16 −1 is sixteen bits set), as **Decode Bit Mask** reads them. |

### Uncertainty and quality

- **By map location is only as good as the georeferencing.** Any offset
  between two images' georeferencing goes straight into the comparison: the
  "same place" can be a pixel or more apart, and Varda doesn't report the
  offset. Check it on a sharp feature. If it matters, measure it (Auto Align
  in the [Mosaic workspace](mosaicking.md) reports the tie points' residuals
  between two images) and report it, or link images that are already
  co-registered.
- **Nearest pixel.** Each image is read at the pixel containing the point,
  so with different pixel sizes the readings cover different areas of the
  ground, each centered up to half a pixel (in each direction) from the
  point.
- **Unreferenced images are read by pixel.** In a map link, an image
  without georeferencing is read by pixel; the notice at the top of the
  workspace names it. Don't report its values as being at the same place.
- **Different bodies aren't linked.** By map location, an image of another
  body than the first georeferenced image (or one whose body can't be told
  and whose radii differ) is named in the notice and has no reading. Linked
  by pixel, images are still paired pixel for pixel, whatever their body.
- **By pixel assumes one grid.** Images of different sizes or grids are
  still read pixel for pixel; the notice at the top of the workspace names
  them. Link them by map location instead.
- **Lines between images with different projections** follow straight
  segments on each image's own grid, so long segments can trace slightly
  different ground paths. Add vertices to keep them close.

### References

- Coordinate conversion uses PROJ, through pyproj: give the PROJ version
  from **Help → Copy System Info**.
