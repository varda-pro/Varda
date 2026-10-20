# Reprojecting images

**Processing → Geometry → Reproject Image** puts an image on another map
projection: the one Varda recommends for the scene, exactly another image's
pixel grid, or a coordinate system you type. The result is a new image with
the same bands. A companion quality image shows where its values were
interpolated and where it has data. Both join your project, and a
`reprojection` record in the result's metadata says what was done.

Use it when a map's shapes or slopes are distorted (the coordinate-system
check's `high-distortion` tip), before Terrain on an image in degrees, to
put two images on one grid, or to make a figure in a standard projection.
Measurements don't need it: Varda measures lengths and areas on the body
whatever the projection (see
[How Varda measures](coordinate-systems.md#how-varda-measures)).

## What it does

1. It works out the **target CRS**: the recommendation, the other image's
   CRS, or the text you typed. A CRS that can't be read, or one on another
   body, is refused with the reason (a Mars image never goes onto Earth's
   EPSG:4326, for instance).
2. It builds the **output grid** (below): the image's footprint in the
   target CRS, at the resolution you ask for, with pixel edges aligned to
   whole multiples of the pixel size. With **Another image's grid** the
   output is exactly that image's grid.
3. It **warps** each band with GDAL's warper and your resampling kernel,
   into a 32-bit float cube with no-data as NaN.
4. It makes the **quality image** and the **record**.

When the target is the image's own CRS (Varda compares the definitions, not
the names) and Resolution is 0 with the whole extent, or the target grid is
the image's own grid, nothing is resampled. The values are copied (as
32-bit floats, like every result, so float64 or large-integer values are
rounded), the description says "already on this grid; nothing resampled",
and the record says `"copied": true` with no kernel or operation.

## Targets

- **Recommended projection** (the default) follows Varda's rules for the
  chosen **Purpose** (Measurement, Mosaic or Terrain). In short:
  - global scenes get equirectangular with `lat_ts` = 0;
  - polar scenes (|center latitude| ≥ 65°, or a pole inside the image) get
    polar stereographic, true scale at the pole for a mosaic and at the
    center latitude for measurement and terrain;
  - mosaics otherwise get equirectangular centered on the scene, rounded to
    1°;
  - for measurement and terrain, the image keeps its own projected CRS when
    it has under 1% scale error and 1° angular distortion over the scene;
    otherwise it gets transverse Mercator centered on the scene, or Lambert
    azimuthal equal-area when the scene is too wide.

  The surface is the body's IAU 2015 sphere, or its reference ellipsoid
  when the image is already on that ellipsoid. The full rules and their
  sources are in
  [Recommended projections](coordinate-systems.md#recommended-projections).
  The reason for the choice ends the result's description, e.g. "Polar
  stereographic: the scene reaches 85.0°N". An image whose CRS is on no
  known body (a local engineering CRS) gets no recommendation: choose
  **Coordinate system** or **Another image's grid** instead.
- **Another image's grid** puts the image on the chosen **Image**'s grid:
  its CRS, transform and size. Resolution and Extent don't apply. Use it to
  compare two images pixel for pixel. The list offers the georeferenced
  images of the same body that overlap this one; the others are listed
  with why they can't be used.
- **Coordinate system** uses the CRS in **Coordinate System**: an EPSG or
  IAU_2015 code (`IAU_2015:49910`), WKT, or a PROJ string. When the dialog
  opens it holds the Measurement recommendation as a PROJ string (as WKT
  when the recommendation keeps the image's own CRS), ready to edit.

## Parameters

| Parameter | Meaning | Units / range | Default |
|---|---|---|---|
| Target | Recommended projection; Another image's grid; Coordinate system | — | Recommended projection |
| Purpose | Recommended projection only: Measurement, Mosaic or Terrain, which pick the recommendation rules | — | Measurement |
| Image | Another image's grid, and Another image's extent: the image to match. Offers georeferenced images of the same body that overlap this one | — | none |
| Coordinate System | Coordinate system only: an EPSG or IAU_2015 code, WKT or a PROJ string. Read and checked for the body when the analysis runs | — | the Measurement recommendation |
| Resampling | GDAL's kernel: Nearest neighbor, Bilinear, Cubic, Average, Mode | — | Nearest neighbor for class maps and single-band integer images (masks, bit masks); Bilinear otherwise |
| Resolution | Ground meters per output pixel at the output's center. Not used with Another image's grid | 0 (automatic), or more than 0 up to 10,000,000 m | 0: the source's ground pixel size at its center, √(dx·dy), so a pixel's area is kept |
| Extent | Whole image (the image's footprint), or Another image's extent (the chosen Image's footprint in the target CRS). Not used with Another image's grid | — | Whole image |
| Source No-Data | One more source value to leave out of the resampling, besides the image's own no-data value and NaN | any number, or empty | empty |
| Quality Bands | Also make the quality image | on/off | on |

The settings dialog shows every parameter whichever target you choose; the
table says which ones each target uses.

## Outputs

**The reprojected image**, `<name> reprojected`. It has the source's
bands, wavelengths, band names and band metadata (FWHM, bad bands), and a
class map keeps its `classes` and `classColors`. Values are 32-bit floats;
no data is NaN (outside the source, or where no source data was under the
kernel). Its CRS is the target's, its transform the output grid's.

**The quality image**, `<name> reprojection quality` (with Quality Bands
on), on the same grid, with two bands:

- **Source pixel distance**: for each output pixel, the distance in source
  pixels from its center to the nearest source pixel center. Each output
  center is moved into the source with Varda's exact point transform
  (`transformPoints`). It's 0 where the output center sits on a source
  center and at most √2/2 ≈ 0.71 halfway between four. It's NaN outside
  the source.
- **Valid**: 1 where every band of the result has data, 0 elsewhere.

**The `reprojection` record** in the result's metadata (kept in saved files
and the provenance record):

| Field | Meaning |
|---|---|
| `from.crs`, `from.description` | The source CRS (WKT, or PROJJSON when WKT would lose a west-positive axis), and its one-line description |
| `to.crs`, `to.description` | The target CRS, likewise |
| `copied` | `true` when the image was already on the target grid and its pixels were copied, not warped; `false` otherwise |
| `method` | GDAL's resampling kernel (`nearest`, `bilinear`, `cubic`, `average`, `mode`), or `none (pixels copied)` for a copy |
| `resolutionM` | Ground meters per output pixel at the center: the one asked for, the automatic one, or the other image's |
| `grid` | The output grid: `transform` (a, b, c, d, e, f: pixel → map), `width`, `height` |
| `coordinateOperation` | The PROJ pipeline GDAL's warper ran (with GDAL's x-first axis order), `PROJ` when PROJ's own operation was used, or `null` for a copy (no warp ran) |
| `gdal`, `proj` | The GDAL version, and the PROJ version of the warper (and pyproj's, when they differ) |
| `scaleChange` | `max` and `mean` of the output's ground pixel size over the source's at the same place, on a 9 × 9 grid of output pixels |

The result's description says what was done, e.g. "CTX reprojected to Mars,
sphere R = 3396.19 km, Polar Stereographic (north) at 20 m/px (bilinear);
Polar stereographic: the scene reaches 85.0°N".

## How it works

- **The warp** is GDAL's warper (`rasterio.warp.reproject`), one band at a
  time, with NaN as no-data on both sides. The image's no-data value and
  Source No-Data become NaN first, so they're never averaged into
  neighbors. GDAL approximates the transform between output pixels with a
  linear interpolation within its default error of 0.125 pixel.
- **Between surfaces of one body** (the Mars sphere and the Mars
  ellipsoid, for instance) positions keep their planetocentric longitude and
  latitude, as for points. Varda hands GDAL an explicit PROJ pipeline,
  because PROJ's own "ballpark" operation would misplace pixels by up to
  20 km on Mars. See
  [Rasters between surfaces of one body](coordinate-systems.md#rasters-between-surfaces-of-one-body).
  Between registered Earth datums (NAD27 and WGS 84, say) PROJ's real
  datum transformation is used.
- **The output grid.** The extent is the source's footprint in the target
  CRS: 21 points along each edge of the image, moved exactly. If the
  image contains the target's pole, the extent includes it. Resolution
  meters at the footprint's center become map units through the target's
  areal scale there (PROJ's scale factors; Snyder, 1987, ch. 4): map
  pixel = resolution × √(areal scale) / (meters per map unit). For a
  geographic target (degrees) pixels are square in degrees, with
  Δφ = resolution / M (M the meridional radius of curvature at the center;
  Snyder, 1987, eq. 4-18). The bounds are then snapped outward to whole
  multiples of the pixel size, as `gdalwarp -tap` does, so products made at
  one resolution share pixel edges. A projection's scale changes across
  the map, so the ground pixel size is exact only at the center.
- **The antimeridian.** In a geographic or cylindrical target, an image
  that would cross the target's cut (180° from its central meridian) is
  refused, with a `lon_0` at the scene's center to use instead. Varda never
  makes a world-wide grid for a small scene that straddles the cut. A
  polar image spans every longitude once.
- **The size guard.** An output of more than 400,000,000 pixels is refused
  before anything is allocated, naming its size and a coarser resolution
  that fits.
- **Memory.** Before running, Varda estimates two source bands as 64-bit
  floats, the 32-bit output cube, and three 64-bit output planes for the
  quality image, and checks it against free memory.

## Uncertainty and quality

- **Smoothing.** Bilinear and cubic resampling smooth the data. The
  effective resolution is about 1.5–2× the output pixel when the output and
  input resolutions are about equal (Schowengerdt, 2007, ch. 7).
- **Overshoot.** Cubic (GDAL's cubic convolution, Keys, 1981, a = −0.5)
  can overshoot near edges and change spectra by a few percent at sharp
  boundaries.
- **Nearest neighbor** preserves values but shifts positions by up to ½
  output pixel.
- **Coarsening.** Average is the right choice when the output pixels are
  larger than the source's (`scaleChange` well above 1). Mode is the right
  choice for coarsening class maps.
- **Spectra.** Resampling mixes neighboring spectra. Use nearest neighbor
  for band-ratio work at pixel scale.
- **No-data edges.** GDAL's warper leaves NaN outside the source. Pixels at
  the edge of the data have kernels with fewer samples. The Valid band and
  the source pixel distance mark them: mask out Valid = 0, and treat a ring
  one kernel wide inside the edge with care.
- **Position error.** Moving between CRSs of one body is exact (to 1e-9°
  for Varda's point transform; the warper's approximation is within GDAL's
  default 0.125 px). So the position error is the source's georeferencing
  error, which Varda can't know. Cite the product's own accuracy where its
  documentation gives one.
- **Sphere ↔ ellipsoid.** Positions keep their planetocentric longitude and
  latitude. Ground distances on the two surfaces differ by the body-model
  term (≤ 0.6% on Mars, 0 on the Moon; see
  [How Varda measures](coordinate-systems.md#how-varda-measures)).

**Reading the quality image.**

- **Source pixel distance** is how far each value was interpolated. With
  nearest neighbor it's the position shift itself, in source pixels. With
  bilinear or cubic, small distances (< 0.1) mean the value is close to
  the source sample there; values near 0.5–0.7 are the most smoothed. On
  the image's own grid it's 0 everywhere. When upsampling it repeats a
  regular pattern. When coarsening it says little: each output pixel
  covers several source pixels, and the kernel (Average, Mode) sets the
  smoothing.
- **Valid** is the data mask. Use it to exclude pixels, for instance in
  statistics, rather than testing for NaN band by band.

**Reading `scaleChange`.** It's the output's ground pixel size divided by
the source's, where both are measured on the body at the same place
(√(dx·dy) from Varda's ground spacing). With Resolution 0 it's 1 at the
center. Elsewhere it changes with each projection's scale factor: the
output's ground pixel at a point is the center's divided by
√(areal scale at the point / areal scale at the center) of the target, and
likewise for the source. For example, on an equirectangular source with
`lat_ts` = 0, the east–west ground pixel shrinks with cos φ, so a scene
from 0° to 30°N reprojected to a centered transverse Mercator at the
center's pixel size has `scaleChange` below 1 near the equator and above 1
near 30°N. A `max` above 1 means part of the image was coarsened (detail
lost; consider Average); a `mean` well below 1 means upsampling, which adds
pixels but no detail.

## How to report

A methods sentence needs the source and target CRS, the kernel, the
resolution, and the software versions, all in the record:

> The image was reprojected from *[from.description]* (*[authority code,
> e.g. IAU_2015:49910, or "WKT in the supplementary material"]*) to
> *[to.description]* (*[authority code, or WKT/PROJ string]*) at
> *[resolutionM]* m per pixel at the scene center, with *[method]*
> resampling, using Varda *[version]*'s Reproject Image (GDAL
> *[gdal]* warper, PROJ *[proj]*). Between surfaces of one body, positions
> kept their planetocentric longitude and latitude. Pixels outside the
> source, or without data under the kernel, are no data; the output's
> ground pixel size relative to the source's ranged up to *[scaleChange.max]*
> (mean *[scaleChange.mean]*).

When the record says `"copied": true`, nothing was reprojected or
resampled: don't report a kernel. Write instead "The image was already on
*[to.description]* (*[authority code]*) at *[resolutionM]* m per pixel;
Varda's Reproject Image copied its pixels unchanged (as 32-bit floats)."

Give the CRS by its authority code when it has one; otherwise include the
WKT from `to.crs` (Image Properties, **Show WKT**). If the CRS was
recommended, the reason in the description says why that projection was
chosen. See also [Reporting results from Varda](reporting-results.md).

## References

- Keys, R. (1981). Cubic convolution interpolation for digital image
  processing. *IEEE Transactions on Acoustics, Speech, and Signal
  Processing*, 29(6), 1153–1160. https://doi.org/10.1109/TASSP.1981.1163711
- Schowengerdt, R. A. (2007). *Remote Sensing: Models and Methods for Image
  Processing* (3rd ed.), chapter 7. Academic Press.
- Snyder, J. P. (1987). *Map Projections: A Working Manual*. USGS
  Professional Paper 1395.
- Archinal, B. A., et al. (2018). Report of the IAU Working Group on
  Cartographic Coordinates and Rotational Elements: 2015. *Celestial
  Mechanics and Dynamical Astronomy*, 130, 22.
  https://doi.org/10.1007/s10569-017-9805-5
- GDAL contributors. GDAL documentation: `gdalwarp` and the warper's
  resampling methods and options. https://gdal.org
- PROJ contributors. PROJ documentation: pipelines, `geoc`, and scale
  factors. https://proj.org
