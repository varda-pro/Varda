# Coordinate systems

A georeferenced image has a coordinate system (CRS): the body it is on, the
surface its map coordinates are measured on (a sphere or an ellipsoid), and
the map projection that turns longitude and latitude into map x and y.
Planetary data comes in many conventions, and two images of the same place
can disagree on each of these. This page says what Varda shows about an
image's coordinate system, the conventions behind it, and how Varda decides
which body an image is on, so you can state them in a methods section.

## What Varda shows about an image's coordinate system

**Image Properties** (right-click an image in the image list, then
**Properties…**) has a
**Coordinate system** group. Its first line describes the CRS in words, for
example:

> Mars, sphere R = 3394.84 km, Equirectangular, center lat 15°, 18 m/px

That is a real CRISM MTRDR: equirectangular on a sphere of Mars's local
radius at its standard parallel, 15°N. The parts, in order:

| Part | Meaning | Where it comes from |
|---|---|---|
| Body | The body the CRS is on, e.g. "Mars". **Unknown body (sphere R = …)** when Varda can't tell (see [below](#how-varda-identifies-the-body)). | The CRS's authority code, datum, ellipsoid and name, then its radius |
| Surface | **sphere R = …** or **ellipsoid a = …, c = …**, in km to 10 m (two decimals) | The CRS's ellipsoid: a sphere when its inverse flattening is 0 |
| Projection | **Equirectangular**, **Polar Stereographic (north)** or **(south)**, **Geographic (lon/lat)** for plain degrees, otherwise PROJ's method name (e.g. Transverse Mercator) | The CRS's conversion method. PROJ's "Equidistant Cylindrical" is shown as Equirectangular; a stereographic projection centered on a pole as Polar Stereographic |
| center lat | The projection's standard parallel (`lat_ts`), else its latitude of origin (`lat_0`), in degrees. Not shown for polar projections, whose name says the pole | The conversion's parameters |
| lon_0 | The central meridian, in degrees; shown when it isn't 0° | The conversion's parameters |
| m/px | A pixel's size on the ground at the image center, in meters; the larger side for rectangular pixels. Whole meters from 100 m, otherwise three significant figures | The image's transform (see the limitations below) |

The group also shows:

- **Conventions**: the latitude type (planetocentric or planetographic), the
  direction longitude increases (east- or west-positive) and the longitude
  domain (−180..180 or 0..360). See [Conventions](#conventions).
- **Authority**: the CRS's code, e.g. `IAU_2015:49910` or `EPSG:4326`.
  When the file doesn't declare one but PROJ finds a registered CRS that is
  the same (ignoring axis order), it shows **matches IAU_2015:…**: the
  image's CRS is that one under another name. **none** when PROJ finds no
  match, which is common: a GDAL-labeled CRISM MTRDR's sphere of local radius
  has no code.
- **Pixel size**: the transform's pixel size in the CRS's units (m, km or °),
  and its size on the ground in meters.
- **Show WKT**: the full definition as OGC WKT2, read-only, for copying into
  a methods section or another program. The WKT is the authoritative record;
  the line above summarizes it.

An image without a CRS shows **No coordinate system (pixel coordinates)**:
Varda works in pixels and doesn't measure in meters. Two other cases:

- A **local** (engineering) CRS, such as GDAL's `LOCAL_CS` for a lab image
  or an unreferenced map, has map units but no body or ellipsoid. It shows
  **Local coordinates (no body)**, its name and the pixel size in its units
  (e.g. "Local coordinates (no body), arbitrary, 2 m/px"), and no
  conventions.
- Text that PROJ can't read as a CRS shows **Unreadable coordinate system**
  with PROJ's error.

Line-profile CSV exports carry the same description in their `# CRS:`
header line, followed by the WKT (see
[Lines and profiles](lines-and-profiles.md)).

### Limitations of the ground pixel size

- In a **projected** CRS it is the map pixel size times the size of the
  map unit (1 for meters, 1000 for km). It does **not** include the
  projection's scale error. In equirectangular with standard parallel φ₁,
  east–west map distances at latitude φ are too long by cos φ₁/cos φ
  (Snyder, 1987, pp. 90–91): 2× at 60° when φ₁ = 0°. A polar stereographic
  pixel is true to scale only at the latitude of true scale (Snyder, 1987,
  ch. 21). Use it as a label, not a measurement.
- In **geographic** degrees it is the larger of the east–west size
  (Δλ · π a/180 · cos φ) and the north–south size (Δφ · π a/180), on a
  sphere of the semi-major axis a, at the center latitude φ. On a flattened
  ellipsoid it is approximate to about the flattening (0.6% on Mars).

Geodesic measurement on the body (Karney, 2013) will replace both in a
later version of Varda.

## Conventions

Varda follows the IAU Working Group on Cartographic Coordinates and
Rotational Elements (Archinal et al., 2018) through PROJ's `IAU_2015`
authority, whose codes are those used in OGC services for planetary data
(Hare et al., 2018).

- **IAU 2015 bodies and codes.** A body's code is its NAIF id × 100 plus a
  suffix: `+0` its geographic sphere, `+1` its planetographic ellipsoid, `+2`
  its planetocentric ellipsoid, and `+10` onward its projections (e.g.
  `IAU_2015:49910`, Mars equirectangular on the 3396.19 km sphere;
  `IAU_2015:49915`, the same centered on 180°). Varda takes each body's
  radii from this database, not from a hand-typed table.
- **Planetocentric vs planetographic latitude** (Seidelmann et al., 2002).
  Planetocentric latitude is the angle at the body's center; planetographic
  latitude is the angle between the equator and the normal to the
  ellipsoid. They differ only on a flattened ellipsoid:
  tan φg = (a/c)² tan φc (Snyder, 1987, eq. 3-28). On a sphere they are the
  same, and Varda calls them planetocentric. On a flattened ellipsoid, Varda
  reads the latitude type from the CRS itself: PROJ's planetocentric CRSs
  (e.g. `IAU_2015:49902`, Mars planetocentric on the IAU ellipsoid, and
  projections on it such as `IAU_2015:49912`) name their axes "Planetocentric
  latitude" and use a spherical coordinate system, and Varda reports them as
  planetocentric. An ordinary geodetic latitude on a flattened ellipsoid
  (e.g. `IAU_2015:49901`, or WGS 84 on Earth) is planetographic.
- **East- vs west-positive longitude.** Planetocentric longitude increases
  eastward. Planetographic longitude increases in the direction opposite to
  the body's rotation: westward for Mars, so `IAU_2015:49901` (Mars
  planetographic) has a west-positive longitude axis; eastward for Earth and
  the Moon by tradition, and for Venus, which rotates retrograde. Varda reads the direction from the CRS's
  longitude axis, and PROJ applies it in every transformation.
- **0..360 vs −180..180.** Both are correct; they name the same places.
  Varda reports **0..360** when a geographic image's longitudes go past 180°
  (e.g. a MOLA MEGDR from 0° to 360°) or a projection's central meridian is
  at 180° or beyond (e.g. `IAU_2015:49915`), and **−180..180** otherwise.
  Varda never rewrites an image's coordinates to change its convention.
- **Spheres and ellipsoids.** Products use different reference surfaces for
  the same body, and each is legitimate: the Mars 2000 sphere (3396.19 km),
  the IAU ellipsoid (3396.19 × 3376.20 km), HiRISE polar products' 3376.2 km
  sphere, MOLA's 3396.0 km sphere, and CRISM MTRDRs' sphere of the local
  radius at their standard parallel. The description names the surface so
  you can see which one an image uses.

## How Varda identifies the body

Varda decides which body a CRS is on from this evidence, in order; the
first that answers wins:

1. The label's target name (e.g. PDS3 `TARGET_NAME = MARS`), when the label
   is given. The description in Image Properties doesn't use labels yet: it
   identifies the body from the CRS alone, steps 2 to 5.
2. An `IAU_2015` authority code on the CRS: its NAIF id names the body.
3. Earth's datums: an EPSG datum, ellipsoid or CRS (all of which are
   Earth's), or one named WGS 84.
4. The names of the datum, the ellipsoid and the CRS: "D_MARS",
   "Mars (2015) - Sphere", "GCS_Mars_2000", "Moon_2000" and "Luna" all name
   their body. Short words such as "IO" never name a body on their own.
5. The radius: a body whose polar-to-equatorial radius range, or its IAU
   mean radius, is within 1% of the CRS's semi-major axis. HiRISE's 3376.2 km
   sphere is Mars this way.

When the radius matches several bodies and nothing else names one, or
matches none, the body is **unknown**, and the description says so with the
radius, e.g. "Unknown body (sphere R = 3000.00 km)". Varda never assumes an
unknown body is Earth.

The bodies Varda knows: Mercury, Venus, Earth, the Moon, Mars, Phobos,
Ceres, Vesta, Io, Europa, Ganymede, Callisto, Enceladus and Titan.

## Combining images

<!-- Filled in by a later change: how Varda compares coordinate systems and combines images on them. -->

## How Varda measures

<!-- Filled in by a later change: geodesic lengths, areas and pixel spacing on the body. -->

## What Varda checks when an image opens

<!-- Filled in by a later change: the coordinate-system checks and automatic fixes. -->

## References

- Archinal, B. A., et al. (2018). Report of the IAU Working Group on
  Cartographic Coordinates and Rotational Elements: 2015. *Celestial
  Mechanics and Dynamical Astronomy*, 130, 22.
  https://doi.org/10.1007/s10569-017-9805-5
- Hare, T. M., et al. (2018). Interoperability in planetary research for
  geospatial data analysis. *Planetary and Space Science*, 150, 36–42.
- Karney, C. F. F. (2013). Algorithms for geodesics. *Journal of Geodesy*,
  87, 43–55. https://doi.org/10.1007/s00190-012-0578-z
- PROJ contributors. PROJ documentation: the `IAU_2015` authority and CRS
  identification. https://proj.org
- Seidelmann, P. K., et al. (2002). Report of the IAU/IAG Working Group on
  cartographic coordinates and rotational elements of the planets and
  satellites: 2000. *Celestial Mechanics and Dynamical Astronomy*, 82,
  83–111.
- Snyder, J. P. (1987). *Map Projections: A Working Manual*. USGS
  Professional Paper 1395.
