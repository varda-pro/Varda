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

That is one real CRISM MTRDR (FRT000107A7): equirectangular, standard
parallel 15°N, on a sphere of Mars's local radius under that scene.
3394.84 km is that scene's radius, not CRISM's: each MTRDR's label gives
the local radius under its own scene, so two CRISM scenes generally have
different spheres. Varda reads each file's own radius and never assumes
one (see [Two CRISM scenes](#two-crism-scenes)). The parts, in order:

| Part | Meaning | Where it comes from |
|---|---|---|
| Body | The body the CRS is on, e.g. "Mars". **Unknown body (sphere R = …)** when Varda can't tell (see [below](#how-varda-identifies-the-body)). | The CRS's authority code, datum, ellipsoid and name, then its radius |
| Surface | **sphere R = …** or **ellipsoid a = …, c = …**, in km to 10 m (two decimals) | The CRS's ellipsoid: a sphere when its inverse flattening is 0 |
| Projection | **Equirectangular**, **Polar Stereographic (north)** or **(south)**, **Geographic (lon/lat)** for plain degrees, otherwise PROJ's method name (e.g. Transverse Mercator) | The CRS's conversion method. PROJ's "Equidistant Cylindrical" is shown as Equirectangular; a stereographic projection centered on a pole as Polar Stereographic |
| center lat | The projection's standard parallel (`lat_ts`), else its latitude of origin (`lat_0`), in degrees. Not shown for polar projections, whose name says the pole | The conversion's parameters |
| lon_0 | The central meridian, in degrees; shown when it isn't 0° | The conversion's parameters |
| m/px | A pixel's size on the ground at the image center, in meters, measured on the body (see [How Varda measures](#how-varda-measures)). When its east–west and north–south sides differ by more than 1% both are shown, e.g. "17.7 m/px (east–west), 18 m/px (north–south)"; otherwise the larger. Whole meters from 100 m, otherwise three significant figures | The image's transform and CRS (see the limitations below) |

The group also shows:

- **Conventions**: the latitude type (planetocentric or planetographic), the
  direction longitude increases (east- or west-positive) and the longitude
  domain (−180..180 or 0..360). See [Conventions](#conventions).
- **Authority**: the CRS's code, e.g. `IAU_2015:49910` or `EPSG:4326`.
  When the file doesn't declare one but a registered CRS is equivalent
  (names ignored, axis order ignored; see
  [What "the same" means](#what-the-same-means)), it shows
  **matches IAU_2015:…**: the image's CRS is that one under another name,
  e.g. a GDAL-written Mars 2000 sphere equirectangular matches
  `IAU_2015:49910`. **none** when nothing matches, which is common: a
  CRISM MTRDR's sphere of local radius has no code.
- **Pixel size**: the transform's pixel size in the CRS's units (m, km or °),
  and its size on the ground in meters.
- **Show WKT**: the full definition as OGC WKT2, read-only, for copying into
  a methods section or another program. The WKT is the authoritative record;
  the line above summarizes it.

The pixel readout in the image views uses the same description: its
latitude and longitude end with the body and latitude type, e.g.
`lat 18.40312° N  lon 77.45120° E  (Mars, planetocentric)`, longitudes are
followed by E or W as the CRS counts them, and a 0..360 image reads
"(0–360)". Map coordinates are shown as `xy` in the CRS's units.

An image without a CRS shows **No coordinate system (pixel coordinates)**:
Varda works in pixels and doesn't measure in meters. Two other cases:

- A **local** (engineering) CRS, such as GDAL's `LOCAL_CS` for a lab image
  or an unreferenced map, has map units but no body or ellipsoid. It shows
  **Local coordinates (no body)**, its name and the pixel size in its units
  (e.g. "Local coordinates (no body), arbitrary, 2 m/px"), and no
  conventions.
- Text that PROJ can't read as a CRS shows **Unreadable coordinate system**
  with PROJ's error; **Show WKT** shows the text as the file has it, and
  **Set coordinate system…** starts with that text, to correct by hand.

Line-profile CSV exports carry the same description in their `# CRS:`
header line, followed by the WKT (see
[Lines and profiles](lines-and-profiles.md)).

### Limitations of the ground pixel size

- It is a geodesic on the CRS's own surface across the center pixel: from
  the middle of its west edge to the middle of its east edge (east–west),
  and from its north edge to its south edge (north–south). "East–west" is
  along the image's rows, so in a rotated or polar image it is the pixel's
  row direction. Other pixels differ wherever the projection's scale
  changes: in equirectangular with standard parallel φ₁, a map pixel's
  east–west side at latitude φ is cos φ/cos φ₁ of its map size on the
  ground (Snyder, 1987, pp. 90–91). For the real CRISM MTRDR (φ₁ = 15°) at
  18.4°N that is 18 m / 1.018 = 17.7 m.
- A HiRISE polar product's 1 m map pixel at 85°N reads 0.998 m/px: polar
  stereographic is true to scale only at the pole there, k = 2/(1 + sin φ)
  (Snyder, 1987, eq. 21-4).
- A CRS on no body (a local CRS) has no ground: its size is the map pixel
  size times the unit's size.

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

When Varda compares two images' coordinate systems, it puts them in one
of five classes:

| Class | Meaning | What Varda does |
|---|---|---|
| **Same** | PROJ says the two CRSs are equal | Uses the coordinates as they are |
| **Equivalent** | The same projection on the same surface; only the names differ | Uses the coordinates as they are |
| **Same body** | The same body, but a different projection, standard parallel, center or surface | Reprojects one onto the other |
| **Different body** | Different bodies, e.g. Mars and Earth | **Never combines them** |
| **Unknown** | A CRS is missing, unreadable, or on no body (a local CRS); or a body is unknown and the two surfaces differ | Can't place one image on the other |

### What "the same" means

**Names don't matter.** The same coordinate system is written many ways:
GDAL reads a CRISM MTRDR's PDS3 label as datum `D_MARS` and its ENVI
header as `D_Mars_2000_IAU_IAG_custom_sphere_lat15`; the Ames Stereo
Pipeline writes it `+proj=eqc +lat_ts=15 … +R=3394839.81331631`; PROJ's
database calls the Mars 2000 sphere "Mars (2015) - Sphere". PROJ alone
treats some of these as different because their names differ. Varda calls
two CRSs **equivalent** when everything that places a pixel on the body
matches, with every name ignored:

| Compared | Tolerance |
|---|---|
| The CRS type (projected, or geographic) and the coordinate system kind | exact |
| The projection method; on a sphere, a method's "(Spherical)" form counts as the method | exact, ignoring case |
| Each projection parameter (standard parallel, central meridian, false easting, …), in degrees or meters whatever units the CRS writes them in; a parameter one CRS leaves out has PROJ's default (0, or 1 for a scale factor) | angles 1e-9°, lengths 1e-6 m, scale factors 1e-12 |
| The ellipsoid's semi-major (a) and semi-minor (b) axes | 1e-3 m |
| The prime meridian | 1e-9° |
| The axes' directions and units, in order | exact |

So a GDAL-written WKT of the Mars 2000 sphere equirectangular and
`IAU_2015:49910` are equivalent, and so are two readings of one CRISM
product. These are **not** the same, and images on them are reprojected:

- a different standard parallel (a CTX DEM's `lat_ts = 18.4` vs `0`), center
  or central meridian;
- a different surface, even on one body: the Mars 2000 sphere (3396.19 km)
  vs the mean-radius sphere (3389.5 km), a CRISM MTRDR's local-radius
  sphere vs the Mars 2000 sphere, or two CRISM MTRDRs, each on the
  local-radius sphere of its own scene. A radius rounded to the centimeter
  (3394839.81 m instead of 3394839.8133 m) also differs: radii are compared
  to a millimeter;
- longitude east-positive vs west-positive (`IAU_2015:49900` vs
  `IAU_2015:49901`), or longitude, latitude vs latitude, longitude axis
  order (`+proj=longlat` vs `IAU_2015:49900`).

Image Properties' **Authority** uses the same test, ignoring axis order:
it shows the first `IAU_2015` or EPSG code that is equivalent, searching
PROJ's suggestions and then every `IAU_2015` code of the image's body (e.g.
`IAU_2015:49900` to `49992` for Mars).

### Moving points between surfaces of one body

"Reprojects" in the **Same body** row means each point keeps its
**planetocentric longitude and latitude**: the same direction from the
body's center (Archinal et al., 2018). Linked Images, applying ROIs to
another image, saving points from several images, vector import and lines
draped on a 3D terrain all move points this way. When both CRSs share one surface, or PROJ knows a real transformation
between them (Earth's datum shifts), Varda uses PROJ's own operation.
Between two surfaces of one body PROJ has none: its "ballpark geographic
offset" keeps the number of the geodetic latitude. Between a sphere and a
flattened ellipsoid that misplaces a point: a planetocentric 45° on the
Mars sphere would become 44.66° planetocentric in `IAU_2015:49902` (about
20 km off), or stay 45° in the planetographic `IAU_2015:49901`, where it is
45.34°. Varda instead:

1. converts the point to longitude (east) and geodetic latitude on its
   own CRS's datum, with PROJ (exact: the projection, the axes, a
   west-positive longitude);
2. converts geodetic to planetocentric latitude on that ellipsoid,
   tan φc = (c/a)² tan φg (Snyder, 1987, eq. 3-28; the same number on a
   sphere);
3. converts planetocentric to geodetic latitude on the target's ellipsoid,
   tan φg = (a/c)² tan φc;
4. converts that to the target CRS with PROJ.

Here a and c are the ellipsoid's equatorial and polar radii. The latitude
type is read from each CRS as described under [Conventions](#conventions).
Round trips are exact to 1e-9°. A west-positive longitude stays
west-positive in its CRS, and x is always the east–west coordinate.
Between two spheres of different radii (the CRISM product above, on its
scene's 3394.84 km sphere, and the 3396.19 km Mars 2000 sphere) the
longitude and latitude are the same numbers, though the distances from the
center differ, so ground distances measured on each differ by the ratio of
the radii (0.04% there).

#### Two CRISM scenes

Each CRISM map-projected product (MTRDR and the others) is on a sphere of
Mars's local radius under its own scene, read from its own file. The
3394.84 km above is one scene's; a scene elsewhere has another, e.g.
3392.50 km with standard parallel 40°S (a constructed example). Both are
identified as Mars, but their surfaces and standard parallels differ, so
they are **Same body**, not equivalent: two different grids, never treated
as one. Tools that need one grid (Band Math, say) don't offer one scene
for the other, and mosaicking refuses them until one is reprojected to the
other's projection. Masks and DEMs are resampled onto the other scene's
grid, and Linked Images, ROIs applied to the other scene, points and lines
reproject between them; each point keeps its planetocentric longitude and
latitude exactly (both are spheres, so the same numbers; round trips to
1e-9°). Ground distances measured in each scene are on that scene's own
sphere, so the same line measured in the two differs by the ratio of their
radii (0.07% for these two). State the radius of each product you measure
on.

### Different bodies are never combined

Coordinates on Mars mean nothing on the Moon, so Varda refuses to combine
images on different bodies, naming both (e.g. "Mars and Earth are different
bodies"). The bodies come from [How Varda identifies the
body](#how-varda-identifies-the-body). When either body is unknown, Varda
compares the two surfaces instead: the same a and b (to 1e-3 m) is taken as
the same body. Different surfaces are **unknown**, not different bodies:
two products of one small body often use different radii, so Varda can't
tell, and doesn't combine them ("Can't tell whether … are the same body
(unknown body, different radii)"). The same holds for an unknown body
against a known one with a different radius. An unknown body is never
assumed to be Earth.

### Recommended projections

Where Varda chooses a map projection for you (Reproject Image's
**Recommended projection**; later Mosaic and 3D Terrain), it follows four
rules, from the scene's **footprint**: its extent in planetocentric
longitude (east) and latitude, from 21 points along each edge of the image.
The footprint contains a pole when the pole's map point falls inside the
image; it then spans all longitudes. "Center" is the middle of the
footprint.

**Global scenes** come first: a footprint that contains both poles, spans
180° of latitude, or (without a pole) spans 300° of longitude. They get
**equirectangular** with `lat_ts` = 0 and `lon_0` = the center longitude
rounded to 1°, and an image already in exactly that projection keeps it.
For a mosaic this is the usual global map. For terrain and measurement the
reason says so plainly: no single projection has low distortion over a
global scene, so Varda's measurements stay geodesic on the body (see
[How Varda measures](#how-varda-measures)); crop to the area of interest to
get a local projection by the rules below.

1. **Polar scenes** (|center latitude| ≥ 65°, or the footprint contains a
   pole) get **polar stereographic** centered on the pole (`lat_0` = ±90,
   `lon_0` = 0). For a **mosaic** the scale is true at the pole (`k` = 1),
   the HiRISE/PDS convention, so products match. For **terrain and
   measurement** it's true at the center latitude rounded to 0.1°
   (`lat_ts`), so the scale is right where the data are. The 65° threshold
   is HiRISE's projection rule (McEwen et al., 2010): equirectangular maps
   above it have large scale errors (137% at 65° for `lat_ts` = 0, k = 1/cos 65°).
2. **Mosaics** otherwise get **equirectangular** with `lat_ts` and `lon_0`
   the center rounded to 1°, the convention of CTX, HiRISE and ISIS
   `cam2map` products, so most inputs need no resampling. When every input
   is in one coordinate system (the same CRS, on the scene's body) that is
   good enough over the scene (below), Varda keeps it instead. Inputs on an
   unknown body count as the scene's body only when their radii match
   (see [Combining images](#combining-images)).
3. **Terrain and measurement** otherwise:
   - Keep the image's own **projected** CRS when it's good enough over the
     scene: at most 1% scale error and 1° angular distortion on a 9 × 9
     grid of longitudes and latitudes over the footprint (see
     [The distortion check](#the-distortion-check)). A geographic CRS
     (degrees) is never kept, because its pixels aren't square on the
     ground.
   - Otherwise **transverse Mercator** centered on the scene (`lat_0`,
     `lon_0` = the center rounded to 0.01°, k₀ = 1), when every point of
     the footprint (a 21 × 21 grid, plus the equator when the scene crosses
     it) lies within 0.14 R of the central meridian. That
     is the angular distance B from the central meridian, with
     sin B = cos φ · |sin Δλ| ≤ sin 0.14 (φ the latitude, Δλ the longitude
     from the center). The scale error there is k − 1 = 1/cos B − 1 ≤ 0.99%
     (Snyder, 1987, eq. 8-1, on a sphere): 475 km either side of the center
     on Mars (950 km wide), 243 km on the Moon.
   - Wider scenes get **Lambert azimuthal equal-area** centered on the scene
     (the center rounded to 0.01°): areas are exact, and the reason states
     the worst angular (shape) distortion over the footprint.
4. **The surface** is the body's IAU 2015 sphere (Archinal et al., 2018),
   unless every input is on the body's IAU reference ellipsoid, when it's
   that ellipsoid. For an unknown body it's the image's own surface. On an
   ellipsoid, the center latitude is converted to planetographic before
   rounding, since PROJ reads `lat_ts` and `lat_0` as geodetic latitudes.

**Rounding** is half away from zero (77.5° → 78°, −77.5° → −78°), not
"round half to even".

Each recommendation states its reason in one line, e.g. "Polar
stereographic: the scene reaches 85.0°N", "Kept its own projection: under
1% scale error over the scene", or "Transverse Mercator centered on the
scene: its equirectangular map has 15.5% scale error here" (a 300 km-wide
scene at 30°N in an equirectangular map with `lat_ts` = 0, where
k = 1/cos 30°). The recommended CRS is a PROJ string with the surface as
`+R=` (sphere) or `+a= +b=` (ellipsoid), `+units=m +no_defs`; report it
from its WKT. Its limits are those of the distortion check: the 9 × 9 grid
can miss a sharper extreme between samples, and the footprint is the image's
rectangle, not its valid data.

### Limitations

- Equivalence compares the projection, not the datum's realization: two
  CRSs on the same sphere with different datum names are equivalent even if
  their producers meant different control networks. Check the WKT (Image
  Properties, **Show WKT**) when that matters.
- CRSs bound to a transformation (`+towgs84`) or compound CRSs (with a
  vertical part) are compared by PROJ alone.

## How Varda measures

Varda measures ground lengths and areas **on the body**, whatever the
image's projection, so a projection's scale error never enters them. The
image's ground pixel size (above) is measured this way; each tool's page
says whether its lengths and areas are (see
[Lines and profiles](lines-and-profiles.md)). The method, in order:

1. **Positions.** Map coordinates (x, y in the CRS's units) are converted
   with PROJ to longitude and latitude on the CRS's **own** surface (its
   sphere or ellipsoid), whatever the axis order, units or longitude
   direction.
2. **Geodesics.** Lengths are the sum of the shortest paths on that surface
   between consecutive points, by Karney's (2013) algorithm (PROJ's
   `geod`). Its error is a few nanometers: negligible.
3. **Densification.** A line is measured through the points you drew it
   with, densified to about one pixel so it follows the line as drawn on the
   image. A polygon's edges are densified to one pixel before they are
   converted, so they follow the straight edges on the image; its area and
   perimeter are then Karney's (2013) geodesic polygon area and perimeter.
   Holes are subtracted from the area and their edges added to the
   perimeter; the parts of a multi-part polygon are summed.
4. **Body-model term.** The CRS's surface is the one the data were
   projected on, which is what ISIS, ArcGIS and QGIS measure on too. When it
   differs from the body's IAU 2015 reference ellipsoid (Archinal et al.,
   2018) by more than 1 m in either radius, the same points are also
   measured on the reference ellipsoid and the relative difference is
   reported:

   *difference* = |*value* − *reference*| / *reference*.

   "The same points" means the same planetocentric longitude and latitude
   (the same direction from the body's center). The ellipsoid's geodesics
   take geodetic (planetographic) latitude, so each planetocentric
   latitude φc is converted with

   tan φg = (a/c)² tan φc

   (Snyder, 1987, eq. 3-28), with a and c the ellipsoid's equatorial and
   polar radii. On the Moon and other bodies whose reference is a sphere,
   and for a CRS already on the reference ellipsoid, there is no
   difference to report. Typical values on Mars, for **lengths**:

   | CRS surface | Where | Length difference |
   |---|---|---|
   | Mars 2000 sphere, R = 3396.19 km (= a) | equator | 0% |
   | Mars 2000 sphere | 60°N | 0.44% |
   | Mars 2000 sphere | 85°N | 0.59%, about (a − c)/c |
   | HiRISE polar sphere, R = 3376.20 km (= c) | 85°N | 0.005% |
   | HiRISE polar sphere | 60°N | 0.15% |
   | HiRISE polar sphere | equator | 0.59% |

   **Areas differ by about twice as much**, since an area scales with the
   square of length (both directions scale): e.g. on the Mars 2000 sphere
   at 85°N, 0.59% for a length and 1.18% for an area.

   The HiRISE polar sphere's radius is Mars's polar radius, so near the
   pole it nearly is the ellipsoid. (Comparing at the same latitude
   *number* instead, i.e. not converting it, would put the point about
   3.5 km farther south on the ellipsoid and give about 1.2% at 85°N; that
   compares two different places.)

**What a measurement does not include:**

- **Topography.** Measurements are horizontal, on the reference surface:
  a slope's length along a DEM is longer.
- **The image's georeferencing error**, which Varda can't know: a position
  is only as good as the image's map placement. Cite the product's own
  accuracy where its documentation gives it.

So the uncertainty to report is: the geodesic algorithm (nanometers), the
densification (at most about one pixel of path following), the body-model
term (above), and the georeferencing error of the image.

**The projection's distortion** (see the distortion tips) is reported
alongside a measurement over its extent, as a tip about planar map-unit
values, shapes and slopes. It is never used to compute the measurement.

**Worked example.** A line drawn east–west at 60°N on a Mars 2000 sphere
equirectangular image with standard parallel 0° (`+proj=eqc +lat_ts=0
+R=3396190`) spans 20 km of map x. Planar map units would call it 20 km, but
equirectangular stretches parallels by 1/cos 60° = 2 there (Snyder, 1987,
p. 91). Varda converts the two ends to 60°N, ±0.1687° longitude on the
3396.19 km sphere and measures the geodesic between them: **9,999.99 m**
(the arc along the parallel is 10,000.00 m; the geodesic cuts slightly
inside it). On the IAU 2015 ellipsoid the same two planetocentric points
are 9,955.75 m apart, so the body-model difference is 0.44%. Report it as
"10.000 km, geodesic (Karney 2013) on the Mars 2000 sphere (R = 3396.19 km);
0.44% shorter on the IAU 2015 ellipsoid; plus the image's georeferencing
error".

## What Varda checks when an image opens

When an image opens, Varda checks its coordinate system against what its
label says and against itself, once. It doesn't ask questions or open
dialogs. Each finding has a severity:

- **Problem**: the coordinates are likely misread or missing. A dot on the
  image's thumbnail, a line in its tooltip, and one status-bar message when
  it opens.
- **Tip**: worth knowing before you measure shapes or combine images. The
  dot and the tooltip line.
- **Info**: a convention, not an error. Shown in Image Properties only.

### Where you'll see it

- **The image list.** An image with a tip or a problem has an **amber dot**
  at the top-right corner of its thumbnail. Its tooltip (hover over it)
  adds one line, `Map: ` and the first problem (else the first tip), e.g.
  "Map: Its CRS says Earth, but its label says Bennu." When the automatic
  fix was applied, the line says what Varda read instead: "Map: Its CRS
  says Earth; read as Mars (from its label)." The dot stays after the fix:
  the file's CRS is still wrong, and the dot says so.
- **The status bar.** When an image with a problem (the automatic fix
  included) joins the image list, the main window's status bar says so for
  15 seconds, e.g. "scene.tif — Its CRS says Earth; read as Mars (from its
  label). Details in Image Properties." Several such images added at once
  give one message, "3 images have coordinate-system problems (a dot on
  their thumbnails). Details in Image Properties." Each image is announced
  once per session, however often the image list changes afterwards. Tips
  and info aren't announced. Images Varda makes (analysis results) aren't
  checked, so they get neither the dot nor a message.
- **Image Properties.** The **Coordinate system** group lists every
  finding, info included, as its severity and message ("Problem: Its CRS
  says Earth, but its label says Mars."), with what to do below it. A
  finding whose fix is in use reads **Fixed** instead ("Fixed: Its CRS says
  Earth, but its label says Mars."), with a note saying who applied it
  ("Varda applied this fix when the image opened.", or "You applied this
  fix."; hover over it for the fix's reason) and an **Undo** button; a fix
  offered but not in use (after Undo) has an **Apply** button, and the
  finding reads **Problem** again. **Set
  coordinate system…** is always there.

Varda never opens a dialog about a finding on its own: the dot, the
tooltip and the status bar tell you, and Image Properties is where you act.

### Where the label facts come from

- A **PDS3** label: a `.lbl` / `.LBL` file beside the image, or a label
  attached to the start of the file: `TARGET_NAME`, and from the
  `IMAGE_MAP_PROJECTION` (or `MAP_PROJECTION`) object
  `COORDINATE_SYSTEM_NAME`, `POSITIVE_LONGITUDE_DIRECTION` and the
  westernmost and easternmost longitudes.
- A **PDS4** label (`target_identification` and the `cart:` mapping
  classes) or an **ISIS** cube label (the `Mapping` group's `TargetName`,
  `LatitudeType`, `LongitudeDirection` and `LongitudeDomain`), as GDAL
  reads them.
- **Varda's provenance**, for an image Varda made: the record of each
  result keeps the label facts of the images it was made from (their
  `labelHints`), so a GeoTIFF Varda wrote from a CRISM product still says
  it is on Mars. Only the **body** carries over from provenance: a result
  may be in a different projection than its inputs, so their longitude and
  latitude conventions don't apply to it. A label's own target wins over
  provenance; inputs on different bodies name none.

### The checks

| Code | Severity | When | Example message | What happens |
|---|---|---|---|---|
| `no-crs` | Problem | The image has map coordinates (a transform that isn't the identity), or its label describes a map projection, but there is no CRS | "Its map coordinates have no coordinate system, so Varda treats it as unreferenced." | Nothing is changed. Use **Set coordinate system…** |
| `unreadable-crs` | Problem | The file has CRS text that PROJ can't read | "Its coordinate system can't be read, so Varda treats it as unreferenced." | Nothing is changed. Image Properties shows the text; use **Set coordinate system…** |
| `earth-crs-on-body` | Problem | The CRS is Earth's (e.g. `EPSG:4326`, WGS 84) but the label's target is another body | "Its CRS says Earth, but its label says Mars." | **Geographic CRS (degrees) on a body Varda knows: fixed automatically**, see below. A projected CRS, or a body Varda doesn't know (e.g. "Its CRS says Earth, but its label says Bennu."): nothing is changed; use **Set coordinate system…**. A target that names no body is no evidence and gives no finding: `SKY`, `N/A`, `NA`, `NONE`, `NULL`, `UNK`, `UNKNOWN`, `CALIBRATION`, `CALIBRATION TARGET`, `CAL`, `DARK`, `DARK CURRENT`, `FLAT`, `FLAT FIELD`, `BIAS`, `STAR`, `STARS`, `SPACE`, `DEEP SPACE` and `TEST`, in any case |
| `unknown-body` | Tip | The CRS's radius matches no body Varda knows, and no label names one | "Varda doesn't know which body it's on: radius 100 km matches no known body." | Nothing is changed. Measurements use the CRS's own surface; the image can only be combined with images on the same surface |
| `ambiguous-body` | Tip | The radius matches several bodies (e.g. Mercury and Callisto at 2425 km), and nothing else names one | "Its radius alone can't tell which body it's on: radius 2425 km matches Mercury, Callisto." | Nothing is changed. Open it with its label, or set the body's own CRS |
| `lon-direction-mismatch` | Problem | The label's positive longitude direction (east or west) differs from the CRS's longitude axis | "Its label says longitude increases to the west, but its CRS says east." | Nothing is changed: GDAL's handling of the label varies by driver and version, so either can be right. Check a known feature's longitude |
| `latitude-type-mismatch` | Tip | The label's latitude type differs from the CRS's: planetocentric on a flattened planetographic CRS, planetographic on a sphere (whose latitude is planetocentric), or planetographic on a planetocentric ellipsoid | "Its label says planetographic latitude, but its CRS's latitude is planetocentric." | Nothing is changed. On Mars the two latitudes differ by up to 0.34° at 45° |
| `high-distortion` | Tip | The projection's scale error or angular distortion over the image passes the tip-off thresholds (below) | "Its projection distorts the map: up to 1.8% scale error (east–west) at 18.4°N, 1.0° angular distortion." Past the stronger thresholds: "Shapes and slopes are badly distorted on this map: …" | Nothing is changed. Measurements are geodesic and unaffected; reproject for figures and terrain (Reproject Image comes in a later version) |
| `lon-0-360` | Info | The image's longitudes run 0..360 (a geographic image past 180°, or a projection centered at 180° or beyond) | "Its longitudes run from 0 to 360° (not −180 to 180°)." | Nothing to do: Varda reads both conventions and never rewrites them |

Without a CRS or with an unreadable one, Varda reports that alone: the other
checks need a CRS. An image with neither map coordinates nor a label
projection is simply unreferenced and gets no finding.

### The one automatic fix, and why only that one

Varda changes a coordinate system on its own in exactly one case: the CRS
is **Earth's and geographic** (plain degrees, e.g. `EPSG:4326`) and the
label's target is **another body Varda knows**. Varda then reads the
coordinates as degrees on that body's IAU 2015 sphere (e.g.
`IAU_2015:49900` for Mars, 3396.19 km), and records the reason, e.g.:

> Its CRS says Earth (WGS 84) but its label's target is Mars; read as
> degrees on the IAU 2015 Mars sphere

This is unambiguous: a geographic CRS carries nothing but longitude and
latitude in degrees, the label names the body, and those degrees can only
be degrees on that body. Software that writes "WGS 84" for planetary
degrees means exactly this. The latitude is then planetocentric (the
sphere's); if the label says planetographic, the `latitude-type-mismatch`
tip appears. The other checks (conventions, distortion) are of the fixed
CRS, the one Varda uses.

Nothing else is fixed automatically:

- A **projected** Earth CRS has map x, y in meters computed with Earth's
  radius. Whether the producer projected with Earth's radius or only wrote
  the wrong CRS can't be told from the file, so Varda can't know where the
  pixels are on the body.
- A **label that disagrees with the CRS** (longitude direction, latitude
  type) could be wrong either way.
- With **no evidence of another body** (no label target), an Earth CRS is
  left as it is: it may really be Earth.

The fixed CRS becomes the image's **corrected coordinate system**: the file
isn't changed, the pixels' degrees aren't changed, and after the fix the
body Varda reports is the label's. The fix is recorded in the image's
metadata as `crsFix` (`from` and `to`, each an authority code such as
`EPSG:4326` and `IAU_2015:49900`, or the WKT when a CRS has none; `reason`;
`automatic`), so every result made from the image carries it in its
provenance (see [Reporting results](reporting-results.md)), and files Varda
exports are written with the corrected CRS. If you keep the file's CRS
instead, the finding stays, says "…; you chose to keep the file's CRS.", and
offers the fix again; the other checks are then of the file's CRS, the one
in use. Sessions keep the corrected CRS
(see
[Sessions and saving](sessions-and-saving.md#corrected-coordinate-systems)).

### Setting or correcting a coordinate system

Every change below applies at once (the Properties dialog's **Cancel**
doesn't undo it), leaves the file and the pixels' map coordinates as they
are, checks the image again, and is kept in sessions (`"crs"` in the
image's `overrides`; see
[Sessions and saving](sessions-and-saving.md#corrected-coordinate-systems)).

The change reaches workspaces already open on the image:

- the cursor readout and Image Properties;
- its ROIs: they stay on the same pixels, their calculated columns
  (**Area on the body** and the rest) are recalculated, and exports and
  sessions write the new CRS;
- the Lines table's lengths, measured again;
- points saved from the image, read on the new CRS. When the Points
  Manager also holds points from other images, the image's points are
  converted to the Points Manager's CRS (that of the first image a point was
  saved from). If they can't be (the new CRS is on another body, or only one
  of the two has a CRS), they keep their old positions and the Points
  Manager says so: delete them and save them again.

- **Undo** (on the automatic fix): use the file's own CRS. The finding
  stays, now saying "…; you chose to keep the file's CRS.", and offers the
  fix again with **Apply**; the dot stays too. The `crsFix` record is kept
  with `undone: true`, so results made from the image say the fix was
  undone.
- **Apply** (on a fix offered): use the fix's CRS. The `crsFix` record is
  the same as the automatic one but with `automatic: false`: you applied it.
- **Set coordinate system…** opens a small dialog:
  - **Coordinate system**: any CRS PROJ reads: an authority code
    (`IAU_2015:49900`, `EPSG:4326`, `ESRI:104905`), WKT, or a PROJ string
    (`+proj=longlat +R=3396190 +no_defs`).
  - **Same as**: another open image that has a CRS; its CRS fills the
    field.
  - As you type, the dialog describes the CRS in words, as Image
    Properties does, with the image's pixel size in it. A pixel size far
    from what you expect (e.g. "0.00391 m/px" when a degree-based image is
    given a CRS in meters) means the CRS's units don't match the image's
    map coordinates.
  - A CRS on another body than the image's (its label's body when the
    label names one Varda knows, else its current CRS's) gets a warning,
    e.g. "⚠ Mars and Earth are different bodies. Use it only if you know
    the image is on Earth." It is allowed: you may know better than the
    label. Text PROJ can't read can't be chosen (OK stays disabled).

  OK uses the CRS, recorded as `crsFix` with `automatic: false` and the
  reason "Chosen by hand (Set coordinate system…)", or "The same as
  *image* (Set coordinate system…)". **Reset to the File's** returns the
  image to how it opened, the automatic fix included.

A session keeps both the CRS you chose and its `crsFix` record (automatic,
applied or set by hand, or undone), so results made after reopening record
the same decision as results made before. A record is kept only while it
still describes the CRS in use: choosing another CRS, or **Reset to the
File's**, replaces it.

### Reporting a corrected CRS

State the CRS the file declared, the CRS used, and why, for example:

> The image's GeoTIFF declared WGS 84 geographic coordinates (EPSG:4326),
> but its PDS3 label's TARGET_NAME is MARS. Varda (version …) read its
> coordinates as degrees on the IAU 2015 Mars sphere (IAU_2015:49900,
> R = 3396.19 km; Archinal et al., 2018).

The `crsFix` record in the provenance of any result gives these values.
For a CRS you set yourself with **Apply** or **Set coordinate system…**
(`automatic: false` in `crsFix`), report it the same way, with your reason.
A record with `undone: true` means the file's own CRS was used instead.

### The distortion check

A map projection can't keep both the sizes and the shapes of a curved
surface: away from its standard lines it stretches some directions more
than others. Varda reports where an image's projection does this enough to
affect what you see: the shapes of features, slopes computed on the map
grid, and measurements in map units.

**Quantities** (Snyder, 1987, ch. 4):

- **h**: the scale along a meridian (north–south), and **k**: the scale
  along a parallel (east–west), as ratios of map length to ground length; 1
  is true to scale.
- **Scale error**: max(|h − 1|, |k − 1|), reported in percent with the
  direction of the larger one.
- **ω**: the maximum angular distortion, the most an angle on the ground
  changes on the map, in degrees. 0 for a conformal projection (e.g. polar
  stereographic).

For a **projected** CRS these come from PROJ (`Proj.get_factors`: the
meridional scale, the parallel scale, the areal scale and the angular
distortion). For a **geographic** CRS (degrees), Varda shows the pixels
square, so the image on screen is an equirectangular map in which a pixel
is Δλ by Δφ degrees. Then h = 1 and

  k = (Δφ/Δλ) · M / (N cos φ)

where M and N are the ellipsoid's meridional and prime-vertical radii of
curvature at latitude φ (equal on a sphere; Snyder, 1987, eqs. 4-18 and
4-20) and Δφ/Δλ is the pixel's aspect in degrees. For square-degree pixels
on a sphere this is Snyder's equirectangular k = cos φ₁ / cos φ with
φ₁ = 0 (p. 91). The areal scale is h·k, and since meridians and parallels
stay perpendicular, sin(ω/2) = |h − k| / (h + k) (Snyder, 1987, eq. 4-9).

**Latitude.** φ in these formulas is the geodetic (planetographic)
latitude. When a CRS's latitude is planetocentric on a flattened ellipsoid
(e.g. `IAU_2015:49902`), Varda converts it with tan φg = (a/c)² tan φc and,
for a geographic image, multiplies M by dφg/dφc = (a/c)² / (cos² φc +
(a/c)⁴ sin² φc), since a pixel spans degrees of planetocentric latitude.
Reported latitudes are in the CRS's own convention.

**Where.** The check samples a 9 × 9 grid of pixel centers spread evenly
from the first pixel to the last, in both directions, and reports the
worst sample: the one with the largest of scale error / 1% and ω / 1°.
Samples PROJ can't invert (outside the projection's valid area) are
skipped.

**Thresholds** (Varda's choice):

| Constant | Value | Meaning |
|---|---|---|
| `SCALE_TIP` | 0.01 | Scale error above 1% → the `high-distortion` tip |
| `ANGLE_TIP_DEG` | 1.0 | ω above 1° → the tip |
| `SCALE_STRONG` | 0.10 | Scale error above 10% → "badly distorted" wording |
| `ANGLE_STRONG_DEG` | 10.0 | ω above 10° → "badly distorted" wording |

Examples: the CRISM MTRDR above (equirectangular, standard parallel 15°,
on its scene's 3394.84 km sphere) at 18.4°N has k = cos 15° / cos 18.4° ≈ 1.018: 1.8%
east–west and ω ≈ 1.0°, a tip. An equirectangular image with standard
parallel 0° at 60°N has k ≈ 2: 100% east–west and ω ≈ 39°, badly
distorted. A CTX DEM whose standard parallel is its own center latitude
has none.

**Measurements are not affected.** Lengths, areas and profiles are
computed geodesically on the body, whatever the projection, so the
distortion changes how the image looks, not what Varda measures. It does
affect what is computed on the map grid itself: shapes traced by eye,
slopes from map-unit pixel spacing, and areas in map units.

**Limitations.** The values are exact for the CRS at each sample (PROJ's
factors, or the closed form above). The 9 × 9 grid can miss a narrow peak
between samples; for the cylindrical, conic and azimuthal projections used
for planetary maps, distortion changes smoothly and is largest at the
image's edges, which the grid includes. The check describes the
projection, not the accuracy of the image's georeferencing.

## References

- Archinal, B. A., et al. (2018). Report of the IAU Working Group on
  Cartographic Coordinates and Rotational Elements: 2015. *Celestial
  Mechanics and Dynamical Astronomy*, 130, 22.
  https://doi.org/10.1007/s10569-017-9805-5
- Hare, T. M., et al. (2018). Interoperability in planetary research for
  geospatial data analysis. *Planetary and Space Science*, 150, 36–42.
- Karney, C. F. F. (2013). Algorithms for geodesics. *Journal of Geodesy*,
  87, 43–55. https://doi.org/10.1007/s00190-012-0578-z
- McEwen, A. S., et al. (2010). The High Resolution Imaging Science
  Experiment (HiRISE) during MRO's Primary Science Phase (PSP). *Icarus*,
  205, 2–37. https://doi.org/10.1016/j.icarus.2009.04.023
- PROJ contributors. PROJ documentation: the `IAU_2015` authority and CRS
  identification. https://proj.org
- Seidelmann, P. K., et al. (2002). Report of the IAU/IAG Working Group on
  cartographic coordinates and rotational elements of the planets and
  satellites: 2000. *Celestial Mechanics and Dynamical Astronomy*, 82,
  83–111.
- Snyder, J. P. (1987). *Map Projections: A Working Manual*. USGS
  Professional Paper 1395.
