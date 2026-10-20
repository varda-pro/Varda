# FITS images and ALTWG/SPC terrain cubes

Varda opens FITS (Flexible Image Transport System) images and cubes:
`.fits`, `.fit`, `.fts` and tile-compressed `.fz` files. Use **File > Open
Image** as for any other image. It is aimed first at the small-body terrain
products of OSIRIS-REx's Altimetry Working Group (ALTWG) and
stereophotoclinometry (SPC), which are on the PDS Small Bodies Node. Plain
astronomical FITS images open too.

FITS is read with `astropy.io.fits` (Astropy Collaboration 2022), not GDAL.

## What opens

- **The image.** Varda uses the primary HDU when it holds pixels. Otherwise
  it uses the first image extension (e.g. an empty primary followed by a
  `SCI` extension). The image may have two axes (one band) or three (a
  cube). Axes beyond the third must have length 1. **Image Properties >
  FITS header** shows the full primary header and, for an extension or a
  compressed image, that HDU's own header too (its `BUNIT`, `PLANEn`,
  `TARGET`...). The metadata records
  which HDU was read and the indices of any other image extensions.
- **Axes.** FITS stores `NAXIS1` fastest: `NAXIS1` is x (columns), `NAXIS2`
  is y (rows) and `NAXIS3` is the band. Varda's pixel (x, y) is the FITS pixel
  (x + 1, y + 1). FITS row 1 (the first stored) is drawn at the **top**, as
  the ALTWG products' PDS4 labels display them ("Line: Top to Bottom").
  Astronomy viewers such as DS9 draw row 1 at the bottom, so a FITS image
  appears flipped vertically in Varda compared with them. The pixel values
  and coordinates are the same.
- **Reading.** Pixels are read from a memory map of the file, a window at a
  time. Large cubes therefore open quickly and fit Varda's image-memory
  budget (Varda keeps an image in memory only when it fits). Tile-compressed
  (`.fz`) images can't be memory-mapped, so they are decompressed whole when
  first read.

## Planes, names and units

Each plane along `NAXIS3` is one band.

- **ALTWG/SPC cubes** name every plane with a `PLANEn` card (the Altimetry
  SIS, §5.2.1.2), or `PLANE_nn` in the Map Format SIS (§5.2.2). The card's
  value is the plane's name. Its unit comes from, in this order:
  1. a unit in parentheses or brackets at the end of the value (`'Latitude (deg)'`);
  2. square brackets in the card's comment, as the archived products have it
     (`PLANE19 = 'Elevation' / [m]`);
  3. a one-word comment (`/ Degrees`).

  The band is named `name (unit)`, e.g. **Elevation (m)**. A plane with no
  stated unit keeps just its name (e.g. **Sigma**, **Quality**, **Relative
  albedo**). Varda never guesses a unit. Planes are identified by their full
  names, **never by position**. A plane whose name isn't in the SIS's
  Table 6 (or the archived products' longer forms, such as "Latitude of
  vertices") still loads as a named band. It is listed under
  `fits.unrecognizedPlanes` in the metadata and noted in the log.
- **Other FITS files.** Planes are named `Plane 1`, `Plane 2` and so on.
  A single plane uses its `EXTNAME` when it has one. `BUNIT` gives the unit
  of every plane. When the third axis is a linear spectral WCS axis
  (`CTYPE3 = 'WAVE'` or `'AWAV'`, with `CRVAL3`, `CRPIX3`, `CDELT3` and
  `CUNIT3`; meters when `CUNIT3` is absent; Greisen et al. 2006), the bands
  get wavelengths in nm.
- **Display.** A terrain cube shows its height by default, in grayscale. In
  order of preference, that is **Elevation**, **Height relative to normal
  plane** (or **Height above plane normal**) and then **Radius**. Other cubes
  use Varda's usual choice of display bands.

## Values and no-data

- **Scaling.** Values are physical values: `BZERO + BSCALE × stored value`
  (FITS Standard §5.3). Scaled or `BLANK`-bearing integer data becomes
  float32 (8- and 16-bit) or float64 (32- and 64-bit). Unsigned 16-bit data,
  which FITS stores as signed with `BZERO = 32768`, reads as 0–65535.
- **No-data reads as NaN.** No-data means:
  - IEEE NaN, the ALTWG products' documented no-data value (Map Format SIS
    §4.4.4);
  - stored values equal to `BLANK` in integer images (the image's no-data
    value is then `BZERO + BSCALE × BLANK`);
  - in ALTWG/SPC cubes only, values at or below −1 × 10³⁰. **This is
    Varda's own heuristic, not part of the SIS**, which documents only NaN as
    no-data. Varda adds it because some older DTMs mark missing vertices with
    large negative numbers such as −1 × 10³⁸, and no quantity in these
    products comes near that.

  The rule applied to an image is recorded in its metadata
  (`fits.noData`). Because of the heuristic, a value at or below −1 × 10³⁰ in
  an ALTWG cube can't be told apart from fill. **You can change the rule in
  Image Properties.** Setting a no-data value there replaces Varda's rule
  (BLANK and the −1 × 10³⁰ heuristic) with that value; NaN always stays
  no-data. Enter `nan` to keep only the SIS's NaN. The edit is kept in
  sessions.

## Georeferencing: none is invented

An ALTWG local DTM is a grid on a reference plane, given by the `CNTR_V`,
`UX`, `UY` and `UZ` keywords. Each pixel carries its own body-fixed position
in planes, so the grid is **not a map projection** and has no affine transform
to a map. Varda therefore gives these images **no coordinate system**
(pixel coordinates) and doesn't flag the missing CRS as a problem. It records
what a later version will use to place them, under `fits` in the image's
metadata:

- `target` is the `TARGET` (or `OBJECT`) header as written, and `body` is the
  body Varda knows it as. Varda tries the name with and without a leading
  catalog number ("101955 BENNU", then "BENNU"). A body that isn't in
  Varda's table (Bennu and Ryugu, for now) stays unknown (`null`). It is
  never assumed to be Earth.
- `geometryPlanes` gives the band indices of the latitude, longitude, radius
  and X, Y, Z planes. Per the SIS these are planetocentric latitude and
  east longitude (deg), radius (km), and body-fixed X/Y/Z of the vertex (km).
- `productKeywords` holds the product's `SIGMA`, `SIG_DEF`, `DENSITY`,
  `ROT_RATE`, `REF_POT` and `GSD`, with their comments (which carry the
  units).

If the file has a FITS WCS on its first two axes, Varda records it under
`fits.wcs`: the `CTYPE`, `CRVAL`, `CRPIX`, `CDELT`, `CD`/`PC` keywords, and
whether they are celestial (`RA`/`DEC`, galactic, ecliptic), planetary
(`xxLN`/`xxLT`) or other. Varda doesn't turn a WCS into a coordinate system
yet. The image stays in pixel coordinates.

## Uncertainty

Varda **carries uncertainty as-is**. It doesn't compute or propagate any:

- A **Sigma** plane (or any uncertainty plane) is an ordinary named band. You
  can display it, take ROI statistics on it, or use it in band math, for
  example `Elevation / Sigma` or a mask where Sigma exceeds a threshold. The
  metadata lists such planes under `fits.uncertaintyPlanes`.
- Per the Altimetry SIS (Table 7), the Sigma plane is, for SPC products, a
  best estimate of the standard deviation of each vertex along its vertex
  vector. For OLA products it is the standard deviation of the OLA points
  about the fitted surface in that pixel. The header's `SIG_DEF` names the
  metric, and its unit is the one the file states. When the file states no
  unit, check the product's PDS4 label or SIS. The **Quality** plane (the
  number of OLA points per bin, or an SPC score from 1 to 5) is also a band.
- The global `SIGMA` keyword is recorded under `fits.productKeywords`.
- Derived planes (elevation, slope, tilt, gravity) assume the uniform
  density and rotation rate in the header (`DENSITY`, `ROT_RATE`). Their
  uncertainty from those assumptions isn't in the file. Report the assumed
  values with your results.

For a methods section, give:
- the product file name (`PRODNAME`) and version;
- the planes used;
- the no-data rule (`fits.noData`);
- any band math applied;
- that uncertainties are the product's own Sigma plane as distributed.

## Not supported yet

- **Shape models** (OBJ plate models, Gaskell PLT/ICQ) and **3D display of
  FITS X/Y/Z grids**: planned for the 3D Terrain workspace.
- **FITS WCS → coordinate system**: a WCS is recorded but not used.
- **FITS tables**, including **ALTWG plate-ancillary files** (`FACET_NUM`,
  `LAT`, `LON`, `RAD`, `VALUE`, `SIGMA`, one row per plate). A file with only
  tables gives a clear message.
- **More than one image per file.** Only the first image HDU opens. Other
  image extensions are listed in the metadata.
- **Writing FITS.** Export as ENVI or GeoTIFF instead.

## References

- IAU FITS Working Group (2018). *Definition of the Flexible Image Transport
  System (FITS)*, version 4.0. Pence, W. D., et al. (2010), version 3.0,
  *A&A* 524, A42.
- Greisen, E. W., Calabretta, M. R., Valdes, F. G., & Allen, S. L. (2006).
  Representations of spectral coordinates in FITS. *A&A* 446, 747.
  Calabretta, M. R., & Greisen, E. W. (2002). Representations of celestial
  coordinates in FITS. *A&A* 395, 1077.
- OSIRIS-REx Altimetry Data Product SIS, UA-SIS-9.4.4-307 (Barnouin, O., et
  al.), in the PDS SBN `orex.altimetry` bundle (`document/altimetry_sis.pdf`).
  Its §5.2.1.2 covers the FITS image cubes and §5.2.2 the plane names
  (Tables 6 and 7).
- OSIRIS-REx Map Format SIS, UA-SIS-9.4.4-324, Rev. 3.2 (2023), in the
  `orex.mission` bundle (`document/map_sis.pdf`). Its §4.4.4 covers NaN as
  no-data and §5.2.2 the FITS map files.
- Barnouin, O. S., et al. (2020). Digital terrain mapping by the OSIRIS-REx
  mission. *Planetary and Space Science* 180, 104764.
- Palmer, E. E., et al. (2022). Practical stereophotoclinometry for modeling
  shape and topography on planetary missions. *PSJ* 3, 102.
- Astropy Collaboration (2022). The Astropy Project. *ApJ* 935, 167.
