# 3D views, overlays and profiles

Status: approved direction (2026-10-15); built in phases.

## Goal

Let a planetary scientist drape data on terrain and make figures from it
without leaving Varda. The motivating case is a CRISM RGB parameter composite
on a CTX DEM. That takes three things: precise co-registration of the overlay
to the DEM, a 3D viewer, and profiles (most often topographic) drawn as lines
in any workspace and plotted. Exports must be good enough for papers: high
resolution stills, and interactive views a reader can open in a browser.

## Decisions

1. **Renderer: three.js in Qt WebEngine, the same viewer in the app and on the
   web.** The 3D view is a three.js page shown in a `QWebEngineView`; an export
   writes the same page, with its data, as one self-contained HTML file that
   opens in any browser (no server), so what a reader sees is what the author
   saw. three.js (MIT) is vendored in `resources/` (offline; no CDN). Data goes
   to the page through a custom URL scheme as binary (heights as float32,
   overlays as PNG), not JSON.
   - Probed 2026-10-15: PyQt6-WebEngine 6.10 (it must match PyQt6's minor
     version) gives WebGL2 with 16384 px textures, offscreen too, so CI can
     test the viewer.
   - Cost: WebEngine adds about 290 MB unpacked (roughly 100 MB to each
     download; the 0.8.0 dmg is 209 MB). Rejected alternatives: a native
     OpenGL renderer (pyqtgraph.opengl or our own GLSL) plus a separate three.js
     template for the web (two renderers that would drift apart, and weaker
     visuals); VTK/PyVista (as large, and its web export needs trame).
2. **Terrain on the GPU.** The DEM becomes a height texture displacing a
   grid mesh in the vertex shader, decimated to fit the GPU (at most 4096
   vertices a side by default, the full DEM for exports when it fits).
   Normals come from the heights in the shader, so lighting and vertical
   exaggeration change instantly. Heights stay in the DEM's units (meters);
   horizontal coordinates are the DEM's map coordinates, so overlays,
   lines and scale bars share one frame.
3. **Overlays are rendered in 2D, then draped.** An overlay is any image
   rendered by `ImageRenderer` (bands, stretch, color map: a CRISM RGB
   composite is just its RGB settings), resampled into the DEM's map grid
   through its alignment, and draped as a texture with opacity. Several
   overlays stack; class maps keep their class colors.
4. **Co-registration reuses the mosaic core.** Each overlay has the same
   recipe as a mosaic input: its georeferencing, a 3x3 map correction and
   an optional warp, fitted from tie points. Auto Align (`mosaicking.matching`:
   phase correlation with the Leprince refinement, ORB, ASP, LoFTR/SIFT)
   matches the overlay against the DEM's orthoimage (or hillshade, when there
   is no orthoimage), on the overlay band with the best SNR. Manual tie points
   and nudging work as in the Mosaic workspace. The alignment is saved with
   the session.
5. **Lines are a third geometry, beside points and ROIs, in every image
   workspace.** Straight lines, polylines and freehand (streamed) lines, drawn
   with a Draw Line tool under each view, kept in image pixel coordinates
   with a name and color, listed in a Lines manager, and saved with the
   session. A profile samples a band along a line (bilinear, at the image's
   pixel spacing) against distance along the line in map units (meters, from
   the CRS; geodesic on the body for geographic CRSs), or in pixels without
   georeferencing. The Profiles plot shows several lines and bands, with a
   vertical exaggeration readout, and exports CSV, PNG and SVG. Lines also
   appear in the 3D view on the surface, and their profiles can be taken
   from the DEM under an overlay.
6. **Figures.** Stills render off screen at any size (up to the GPU's limit,
   tiled beyond it) with optional scale bar, north arrow, color bars for
   overlays and the DEM, and a vertical exaggeration note. Camera views are
   saved by name. Later: flyover animations (an orbit or a path along a
   line) to MP4 and GIF, and glTF export for other 3D tools.

## Phases

1. **Lines and profiles** (independent of 3D): the Draw Line tool (straight,
   polyline, freehand), the line collection, the Lines manager, the Profiles
   plot (bands, multiple lines, distance in meters, CSV/PNG/SVG export), and
   sessions; in General Image Analysis and Dual Image.
2. **The 3D workspace**: a DEM (any single-band elevation image) with a
   hillshade or color-map drape, vertical exaggeration, sun azimuth and
   elevation, orbit, pan and zoom, camera views, high-resolution stills, and
   web (HTML) export. WebEngine joins the dependencies and the bundle; the
   self-test checks that WebGL starts.
3. **Overlays and co-registration**: overlays from loaded images with their
   render settings and opacity; alignment from georeferencing, then Auto
   Align against the DEM's orthoimage and tie points (reusing the mosaic
   core); several overlays; class maps.
4. **Figure polish**: scale bar, north arrow and color bars in exports;
   lines and their labels on the surface; profiles from the 3D view; flyover
   animations; glTF export.

## Testing

- Lines and profiles: sampling, distances (projected, geographic and
  ungeoreferenced), freehand simplification and the plot's export are plain
  functions with unit tests; the tools are tested through pointer events.
- 3D: the page's data (heights, textures, scene description) is built by
  Qt-free code and tested directly; the WebEngine page is tested offscreen
  (WebGL2 works there) by loading a small DEM, reading back its state through
  the web channel, and rendering a still.
- Co-registration: synthetic overlays shifted, rotated and scaled by known
  amounts, recovered by Auto Align, as in the mosaic tests.
