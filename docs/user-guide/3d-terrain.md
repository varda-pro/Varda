# 3D Terrain

Show a DEM as a lit, shadowed surface, drape an image on it, and export
figures and interactive web pages. Open it from **Workspace → New 3D Terrain
Workspace** with the DEM loaded.

## The surface

- **DEM**: any loaded elevation image (a single-band image is picked first);
  **Band** appears for multi-band images. No-data leaves holes.
- **Drape**: what covers the surface:
  - **None (shaded relief)**: a neutral surface, shaded by the sun;
  - **Elevation colors**, with a **Color map**;
  - any loaded image on the DEM's pixel grid, rendered as it is in 2D (its
    bands, a 2-98 % stretch);
  - any georeferenced image of the DEM's body that overlaps it, whatever its
    name, pixel size or map projection, such as the 25 cm orthoimage that
    comes with a 1 m HiRISE DTM. It's placed by its georeferencing alone and
    is transparent where it doesn't reach. To correct a georeferencing
    error, add it as an [overlay](#overlays) and Auto Align it instead.
    Images that can't be placed are named under the list, with the reason.

  A large image is drawn at texture size: only the part over the DEM, at
  most 8192 pixels a side, read from the file's overviews when it has them
  (JPEG 2000 and tiled GeoTIFFs do), so a HiRISE orthoimage drapes in
  seconds.
- **Detail**: how finely the surface follows the DEM: at most 512 (Low),
  1024 (Medium) or 2048 (High) points on the longer side. Each point is the
  mean height of a block of DEM pixels, placed at the block's center. The
  status line gives the ground size, the height range (in the height unit),
  the grid, how many DEM pixels a side each point averages, and how many
  points of gaps were filled.
- **Height unit**: the unit of the DEM's heights: **Meters** (the default),
  **Kilometers** or **Feet**. Ground sizes are in meters, so heights are
  converted to meters for the view, the exaggeration, the color bar's label
  and 3D printing. When the DEM's CRS declares a vertical unit (a compound
  CRS with a vertical part, or a 3D CRS), Varda starts from it (US survey
  feet count as feet, 2 parts per million apart); otherwise check it
  yourself, since most planetary DEMs don't say. It's saved with the
  session.
- **Exaggeration** stretches the heights; **Sun azimuth** (clockwise from
  north) and **Sun elevation** place the light; **Cast shadows** and **Sky
  light** (which lifts the shadows) shape it; **Background** sets the color
  around the surface.

Drag to orbit, right-drag (or Shift-drag) to pan, and scroll to zoom.
**Reset View** returns to the starting view from the south. **Save View…**
keeps the current camera by name; double-click a saved view to return to it.
Views and settings are saved with the session.

## Shape models

**Surface** at the top switches between a **DEM** and a **Shape model**: a
triangle mesh of a small, irregular body (Bennu, Ryugu, Eros…) in
body-fixed km, where a DEM's map projection would mislead. Shape mode has
its own panel and view: open OBJ, Gaskell PLT and ICQ, FITS X/Y/Z planes or
NAIF DSK models, click a plate to read its position and value, color plates
by plate data and its uncertainty, and export OBJ, STL, plate CSV, web pages
and stills. See [Small bodies and shape models](small-bodies.md).

## Block diagrams and 3D printing

- **Sides (block diagram)** stands the surface on walls down to a flat base,
  like a cut block. **Base depth** is how far below the lowest point the
  base is, in the height unit (Automatic: a quarter of the relief); it's
  exaggerated with the heights. **Side color** offers earth, light gray, white, dark gray, black
  or any color (Custom…).
- **Edges** shapes the outline where the data ends, in the view and in the
  printed solid: **Smooth** (the default) cuts the grid cells' corners and
  relaxes the outline, so a DEM whose footprint is tilted on the map gets
  clean sides; **Stepped** follows the grid cell by cell; **Trimmed to a
  rectangle** keeps the largest rectangle with data everywhere, for a block
  with straight sides and square corners.
- **Fill gaps** (on by default) fills no-data gaps inside the DEM, such as a
  DTM's voids on steep slopes, from the heights around them, so they don't
  show as pits. No data around the outside of the DEM stays empty. The
  filled heights are invented: **Show filled gaps** tints those points
  magenta in the view (and in stills, animations and the exported web
  page; it tints the surface and its drape, so lower an opaque overlay's
  opacity to see it there), and the status line gives how many there are.
- **Export for 3D Printing…** saves the terrain as a closed solid in STL,
  the format slicers (Cura, PrusaSlicer, Bambu Studio and others) open.
  Choose the **width** in millimeters (between the outermost grid points),
  the **exaggeration**, the **base**
  under the lowest point (3 mm by default), and the **detail** (points on
  the longer side; 512 suits most printers). The dialog shows the size it
  will print, and how many points of the surface are filled gaps. The
  solid is watertight, with sides around any gaps left unfilled, so it's
  ready to slice. STL has no place for a per-point mask, so the print's
  own count of filled points (on its own grid) goes in the file's 80-byte
  header ("Varda terrain solid; N gap points filled") and the status line.

## Lines

**Add Lines** drapes lines over the surface. Lines drawn with Draw Line in an
open General Image Analysis workspace are listed under its image's name
(any georeferenced image of the DEM's body, not only the DEM): add all of
them or one line at a time, each keeping its name and color. They're copied
in, so later edits in that workspace don't change them, and the session
keeps them even after that workspace is closed.

**From a File…** reads a vector file instead: a rover traverse or mapped
contacts (GeoJSON, GeoPackage, shapefile, KML). Polygons show as their
outlines. Longitude and latitude are taken as degrees on the DEM's body
(planetary GeoJSON files always claim Earth's): longitude east, and
latitude of the DEM's own type (planetocentric or planetographic, see
[Coordinate systems](coordinate-systems.md)). Other map projections of the
same body are converted to the DEM's; a file on another body is refused
with a message. GeoJSON can't store a Mars map projection, so a
GeoJSON whose "degrees" are really projected coordinates is read in the
DEM's projection; a GeoPackage keeps the projection and is the safer choice.
Tick a layer to show or hide it; double-click it to change its color. Lines
follow the exaggeration and are saved with the session (files as their
paths).

The **Profiles** panel (below the view) plots the DEM along the selected
lines, or along every shown line when none is selected, with the same
readout, vertical exaggeration and CSV or image export as the Profiles tab
of General Image Analysis (see [Lines and profiles](lines-and-profiles.md)).

## Figures and web pages

- **Show** puts annotations on the view and in stills:
  - a **north arrow**, which turns with the camera;
  - a **scale bar**, the ground distance at the view's center (a
    perspective view has no single scale; it is most accurate in views from
    above), with the vertical exaggeration;
  - a **color bar** for the elevation colors drape: the color map over the
    elevation range it stretches across.

- **Export Still…** renders the view as a PNG at HD, 4K, 8K, a 3000 px
  square or any size up to 16384 px a side, optionally with a transparent
  background for compositing.
- **Export Animation…** renders a flyover: the camera circles the view's
  center (a full turn or a part of one) over as many frames as you choose,
  at HD, Full HD or square sizes. It's saved as an animated WebP (sharp and
  small), a GIF, or a folder of numbered PNG frames for a video editor.
- **Export Web Page…** saves the view as one self-contained HTML file that
  opens in any modern web browser, with no server and nothing to install.
  Readers can orbit and zoom, and change the exaggeration, the sun and the
  shadows. When gaps were filled, its panel says how many points and how,
  and a **Filled gaps** box tints them; the page's data
  (`window.VARDA_SCENE.filled`) holds the count, the method and a bit mask
  of the filled grid points. It's suited to sharing with coauthors or
  publishing as supplementary material.

The 3D view needs WebGL 2 (any graphics from the last decade). If it isn't
available, the workspace says so; updating the graphics driver usually
helps.

## Overlays

Drape images that aren't on the DEM's grid, a CRISM parameter composite over
a CTX DEM say, on top of the surface:

1. **Add Overlay** lists the loaded, georeferenced images of the DEM's body.
   An overlay starts where its georeferencing puts it; images in another map
   projection are reprojected to the DEM's.
2. The **Overlay Display** panel (it opens when you add an overlay;
   **Display…** brings it back) sets the selected overlay's mode, bands,
   stretch and color map as in the image views, with its histograms. Drag a
   histogram's edges to set the stretch. For a CRISM parameter cube, pick any
   three parameters for red, green and blue by name (OLINDEX3, LCPINDEX2,
   HCPINDEX2 for the MAF composite, say). The overlay redraws once you stop
   changing things. Like any panel, it can be docked beside the view or
   floated. **Opacity** and the tick box in the Overlays list change the
   view at once.
3. **Auto Align** matches the overlay's band with the best signal against
   **Align to**: an image on the DEM's grid (its orthoimage works best) or a
   hillshade of the DEM. It uses the Mosaic workspace's matching (phase
   correlation refined to a fraction of a pixel, with features as a
   fallback), and fits the **Model**: Translation by default, as
   map-projected products are mostly just offset, or rigid, similarity,
   affine, projective, polynomial or a thin-plate spline for warped ones.
   The panel reports the tie points the fit kept and their RMS residual,
   in the DEM's map units and labeled with them (m for a DEM projected in
   meters, ° for a geographic one), the image and band matched (with the
   band's wavelength, or its name, and its SNR) and the tie points' median
   phase-plane SNR. All of it is saved with the overlay's alignment in the
   session.
   **Match using** chooses the image that is matched: the overlay itself,
   or another loaded image with the same georeferencing. A parameter map
   often has little texture to match, but a scene made from the same
   observation (its reflectance convolved to the reference camera's band,
   for example a CTX-like scene from CRISM) matches well, and the alignment
   found for it moves the overlay.
4. **Shift E, N** moves the overlay by hand, in the DEM's map units
   (as labeled: meters for a DEM projected in meters, degrees for a
   geographic one), after the fit.
   **Alignment: Copy** and **Paste** give another overlay the same
   alignment (products of one observation share it); **Reset** returns the
   overlay to its georeferencing.

Overlays, their display settings and their alignments are saved with the
session, and they're
included in stills and exported web pages (with their opacity).

Over Jezero, a CRISM MTRDR (FRT00005850) aligns to the USGS CTX DTM's
orthoimage with about ten tie points and an RMS of 10 m, half a CRISM pixel;
its georeferencing was off by about 160 m west and 340 m north.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

The 3D view, stills, animations, web pages and STL files are
visualizations. Take any number you report (heights, slopes, distances,
volumes) from the DEM itself, with Varda's [profiles](lines-and-profiles.md)
or analyses, not from the 3D surface.

### What to state

- The DEM (product, its source, resolution and stated vertical precision),
  the band shown and its height unit.
- For a figure: the vertical exaggeration, the sun's azimuth and elevation,
  whether shadows were cast, the drape and its stretch or color map, any
  overlays and how they were aligned, and whether gaps were filled (how
  many points, and how; see the table below).
- For an overlay: the reference it was aligned to (an image, or the DEM
  hillshade), the matched image and band (both shown in the panel), the
  model, the tie points kept, their RMS and median phase-plane SNR, and any
  manual shift.

How the surface is made, beyond the panel's settings:

| Step | What Varda does |
| --- | --- |
| Decimation | Every *n* × *n* block of DEM pixels becomes one grid point, their mean height ignoring no data; *n* is the DEM's longer side divided by the Detail, rounded up (the status line's "every *n* pixels"). Edge blocks may be partial. Each point stands at the center of its block (a partial block's own center), in the view, web page and STL alike; the surface ends at the outermost points, half a block inside the DEM's edges. |
| Height storage | In the view and the web page, heights are stored in 16 bits: 65,535 levels from the lowest to the highest point, so one step is (highest − lowest) / 65,534. 3D-printing exports use the heights unrounded. |
| Gap filling | Gaps enclosed by data take the nearest grid point's height, then are smoothed by 4 passes of a 3 × 3 mean (over valid neighbors). Done on the decimated grid. The count and this method are saved with the session (`filled`) and the web page. |
| Ground size | Projected DEM: pixel size × pixel count, in meters. Geographic DEM: degrees × π*a*/180, with *a* the body's equatorial radius, and the east–west size multiplied by the cosine of the center latitude. |
| Drape | The image's own rendering with a 2–98 % linear stretch (also for elevation colors, whose color bar shows that range), drawn at most 8192 px a side: every *n*-th pixel, *n* the longer side over 8192 rounded up, read from the file's overviews where it has them (the nearest overview pixels). The stretch's statistics come from the pixels drawn. A drape off the DEM's grid is drawn over the DEM's extent plus 2 % on each side, then resampled by map location (bilinear, GDAL's warper, transparency kept) onto a texture covering the DEM's extent, no finer than the image's pixels and at most 8192 px a side. |
| Lighting defaults | Sun azimuth 315°, elevation 35°, sky light 0.35, shadows on, exaggeration 1. |
| Overlays | The part over the DEM, plus 10 % of the DEM's extent on each side (room for an alignment to move it), drawn as for a drape, with the Overlay Display's settings (and, once those have read the image's bands, their statistics); reprojected to the DEM's projection with bilinear resampling at about that resolution; textures at most 8192 px a side. |
| Overlay Auto Align | Matches the overlay's highest-SNR band (SNR as in [Mosaicking](mosaicking.md#what-to-state); the band and its SNR are shown and saved), reprojected as above, against band 1 of the reference (or a hillshade of the band shown, its heights converted to meters by the height unit: sun azimuth 315°, elevation 45°), with the Mosaic workspace's matching (see [Mosaicking](mosaicking.md#reporting-your-results)). The fit leaves out points more than 1 pixel (of the reprojected overlay) off. |
| Height unit | Heights × 1 (m), 1000 (km) or 0.3048 (ft) give meters; the exaggeration multiplies the result. |
| Smooth edges | Outline points move toward their neighbors, by at most half a grid cell. |
| Vector files | Geographic coordinates beyond ±360° longitude or ±90° latitude are taken to be in the DEM's projection already. |

### Uncertainty and quality

- **Heights in the view are smoothed.** Block averaging lowers peaks and
  raises pits; the larger *n* is, the more. The 16-bit step is
  (relief) / 65,534: for 4,000 m of relief, about 6 cm, far below most
  DEMs' vertical precision.
- **Filled gaps are invented heights.** They carry no measurement, so
  their error is unknown and grows with the gap's size. The status line
  and the session give how many grid points were filled; **Show filled
  gaps** marks them in the view, stills and web pages, and the web page
  holds their mask. An STL can't mark points, so its header gives only the
  count. Say in a caption when a figure's gaps were filled, and show them
  (or turn **Fill gaps** off) where the surface's shape matters.
- **Vertical exaggeration depends on the height unit.** Heights are
  converted from the **Height unit** to meters, the ground's unit, so the
  exaggeration, the scale bar's "vertical exaggeration" and a print's
  height are true only when the height unit is right. Check it for DEMs
  whose CRS doesn't declare one; Varda 0.9.0 and earlier always took
  heights as meters.
- **Geographic DEMs are drawn as a flat rectangle.** The east–west size is
  right only at the center latitude, and uses a sphere of the equatorial
  radius, so shapes are stretched away from the center latitude, more for
  DEMs spanning many degrees. Use a projected DEM for figures where shape
  matters.
- **Positions on the surface.** Each grid point stands at the center of
  the block of DEM pixels it averages, so its position is right, but its
  height is the block's mean (see above). (Varda 0.9.0 and earlier spread
  the points evenly from edge to edge, misplacing them by up to half a
  block, more with a partial edge block.)
- **The scale bar** is measured at the view's center. In a perspective view
  it's wrong elsewhere in the frame; use views from above for figures with
  a scale.
- **Overlay alignment RMS is precision, not accuracy.** It's how well the
  overlay agrees with the reference, in the DEM's map units (as labeled).
  The overlay is only as well placed
  as the reference: report the reference's own geolocation accuracy. A
  manual shift isn't part of the RMS. The median phase-plane SNR (0–1; see
  [Mosaicking](mosaicking.md#uncertainty-and-quality)) says how cleanly the
  tiles matched; it's blank when the matching fell back to features.
- **Draped images are for display.** The drape and overlays are stretched,
  resampled (bilinear) and, when large, downsampled. Don't read values from
  them.

### References

- Overlay matching (phase correlation, its sub-pixel refinement, RANSAC,
  ORB and the transform models): see the references in
  [Mosaicking](mosaicking.md#references).
- 3D rendering uses three.js (release 170), which has no formal citation;
  name it if your journal asks for software.
