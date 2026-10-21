# Small bodies and shape models

On small, irregular bodies (Bennu, Ryugu, Eros, 67P…) a map projection
gives wrong distances and areas, and latitude and longitude can name more
than one point of the surface ([Small and irregular
bodies](coordinate-systems.md#small-and-irregular-bodies)). Scientists
working on them use a **shape model**: a triangle mesh of **plates** in
body-fixed kilometers. Varda reads shape models, shows them in 3D, reads the
plate under a click, colors plates by per-plate data with its uncertainty,
and exports them.

Open one in **Workspace → New 3D Terrain Workspace**, then **Surface: Shape
model** at the top, then **Open Shape Model…**.

## Coordinates

Varda follows the IAU convention for small bodies (Archinal et al., 2018):

- **X, Y, Z**: body-fixed Cartesian coordinates in **km**, about the center
  of mass, in the shape model's own body-fixed frame (+Z the north pole).
- **Latitude**: planetocentric, degrees: the angle between the equator and
  the line from the center to the point.
- **Longitude**: east, 0–360°.
- **Radius**: the distance from the center, km.

Latitude, longitude and radius are attributes of a point, never a way to
find one: on a body with overhangs, a line from the center can cross the
surface more than once. Everything Varda reports comes from a point on the
mesh itself.

## Opening a shape model

**Body** names the body the model is of (Varda guesses it from the file
name, such as "bennu_g_00880mm.obj"). It's used to check the model's unit
and, for Shape Geophysics, to fill in the body's density and rotation period
([the small-body table](coordinate-systems.md#the-small-body-table)).

| Format | Suffix | What Varda reads | Reference |
|---|---|---|---|
| Wavefront OBJ | `.obj` | `v x y z` vertices and `f` faces (1-based; `a/t/n` forms; polygons are split into triangles from their first corner); other lines are ignored | Wavefront *Advanced Visualizer* manual, App. B1 |
| Gaskell PLT | `.plt`, `.tab` | the vertex count, `index x y z` lines, the plate count, `index a b c` lines (1-based); or both counts on the first line, as some PDS archives write them | PDS Small Bodies Node shape-model data-set descriptions |
| Gaskell ICQ | `.icq` (or a `.txt` whose first line is one integer) | q, then the 6 (q + 1)² vertices of the six cube faces' grids; each grid square becomes two plates, and vertices repeated along the faces' seams are merged, so a global model is closed | Gaskell et al. (2008) |
| FITS X/Y/Z planes | `.fits`, `.fit` | an ALTWG/SPC cube with X, Y and Z coordinate planes (named by `PLANEn` cards; km, or m when the card's unit says so): each grid square whose four corners have coordinates becomes two plates; fill values (below −10³⁰) are skipped. An **open patch** | ALTWG SIS; [FITS images](fits-images.md) |
| NAIF DSK type 2 | `.bds` | the first segment's vertices and plates, through SpiceyPy. Needs the `spice` extra (`uv sync --extra spice`); release builds include it. Without it Varda says "Install the spice extra to read DSK files" | NAIF DSK Required Reading |

After reading, Varda **orients** the model: neighboring plates are made to
run their shared edge in opposite directions (consistent winding), and the
whole model is turned so its normals point outward (a positive signed
volume for a closed model; away from the patch's middle for an open one).
When any plate was turned, the panel says **"Normals were flipped to face
outward."** Results (normals, slopes, areas' signs) are computed on the
oriented model.

**Meters or kilometers.** Some OBJ exports are in meters. When a body is
set and the model's mean radius is between 300 and 3000 times the body's
(km), Varda asks **"This model's size suggests meters; read as meters?"**;
Yes divides the vertices by 1000. Without a body Varda can't tell: check
the panel's volume against the body's.

The panel then lists the plates, vertices and **mean edge** (the mean
length of the model's edges, in m: its resolution), whether it is
**closed** (every edge shared by exactly two plates) with its **volume**
(km³; the polyhedron's exact volume), or **open** (a patch, or a model with
holes).

## The view

Drag to orbit, right-drag to pan, scroll to zoom; **Reset View** looks at
the body from (+X, −Y, +Z). The light is a **headlight** that follows the
camera, or, with **Light from the sun**, comes from the direction of the
**subsolar point** you give (planetocentric latitude and east longitude).
Plates are shaded flat: each plate is one facet, as in the model.

**Display limit.** Models of more than 512,000 plates are shown decimated:
vertices are merged on a cubic grid (each cell's vertices become their mean)
until the model fits, and each shown plate takes the color of the model's
plate nearest its centroid. The panel says "Shown with N of M plates";
readouts and exports always use every plate. Exported web pages are
limited to 200,000 plates so that ordinary laptops' browsers stay
responsive.

## The readout

Click the model (without dragging) to read a plate:

`Plate 81204 · X 0.1234 Y −0.2001 Z 0.0452 km · lat 10.215° lon 301.652° r 0.2394 km · slope 12.5 ± 0.5 deg`

The page sends the click's **view ray**; Varda finds where that ray first
meets the **full** model (a ray cast through a bounding volume hierarchy,
with the Möller–Trumbore ray–triangle test; Möller & Trumbore, 1997). So the
point is where you clicked, on the surface you see, even on a decimated
display or where a line from the center crosses the surface twice. X, Y, Z
are that point; latitude, longitude and radius are computed from it; the
plate is the one it lies on. The last part is the plate's value (± its 1σ
when the data has one) for the dataset that colors the plates.

**Precision.** Coordinates are given to 0.1 m and angles to 0.001°: these
are the point on the mesh, not the body. The mesh's own error (its
resolution, the mean edge, and the shape model's stated accuracy) is the
limit on what the numbers mean.

## Plate data

**Plate data** is one value per plate with a unit, and optionally its 1σ
uncertainty, made for **one** shape model. **Add Plate Data…** reads:

- a **plate CSV** that Varda wrote (Export → Plate Data as CSV…): lines
  starting with `#` give the shape's name, its **content hash** (SHA-256 of
  its vertices and plates), its plate count and each dataset's provenance,
  then a `plate` column (0-based) and one column per value
  (`name [unit]`) and per sigma (`name sigma`);
- an **ALTWG plate-ancillary FITS table**: the table's value column (the
  one that isn't a plate index, a location or SIGMA) and its SIGMA column,
  ordered by a FACET/PLATE/ID column when there is one.

**Matching.** Data is used only on the model it was made for: the same
content hash **and** plate count. A plate CSV for another resolution of the
same body, or for a regenerated model with the same number of plates, is
refused: "slope was made for another shape model (3,145,728 plates, hash
8dfd7092…); this one has 3,145,728 plates, hash 6cdaab6d…." ALTWG tables
carry no hash, so they are matched by plate count only; the status line and
the data's provenance say so ("matched by plate count only: the table names
no shape model"). Check that such a table belongs to your model (its
product name names the model's resolution). OBJ files Varda writes keep
every bit of the vertices, so a model exported and read back keeps its hash.

**Coloring.** **Color by** picks a dataset and a **Color map** (from the
2nd to the 98th percentile of the values; with 100 or fewer plates, from
the lowest to the highest), with a color bar; **RGB** colors red, green and
blue by three datasets (each stretched over its own range). Plates without
a value are dark gray. **Uncertainty** shows the data's sigma:

- **Color by sigma**: the plates are colored by their 1σ instead;
- **Fade uncertain plates**: plates whose σ/|value| exceeds the threshold
  (0.5 by default) are faded toward gray, so only the well-determined
  values stand out.

## Shape Geophysics

**Shape Geophysics…** (in the shape panel, or **Processing → Shape Models →
Shape Geophysics…**, which opens a 3D Terrain workspace on a shape model)
computes, for every plate of a **closed** model, the gravity of the body
and what it means for loose material on the surface. An open patch or a
model with holes is refused: "Gravity needs a closed shape model; this one
has holes or is a patch."

### Method

The body is a **constant-density polyhedron**, the shape model filled with
density ρ, rotating about its +Z axis with period P (ω = 2π/P). At each
plate's centroid:

- **Gravity**: the exact potential and acceleration of the polyhedron
  (Werner, 1994; Werner & Scheeres, 1997): sums over the model's edges
  and plates, valid on and off the surface. Varda checks its
  implementation against a uniform sphere (−GM/r outside), Poisson's
  equation inside (∇²V = 4πGρ), and the closed-form potential of a cube
  (to 10⁻¹⁰), Werner's own test body. G = 6.67430 × 10⁻¹¹ m³ kg⁻¹ s⁻²
  (CODATA 2018).
- **Centrifugal** acceleration ω²(x, y, 0) and potential −½ω²(x² + y²).
- **Total acceleration** a = gravity + centrifugal; **effective potential**
  V_eff = V + centrifugal potential.
- **Slope**: the angle between a and the plate's inward normal (Scheeres
  et al., 2016; Barnouin et al., 2019): 0° where the total acceleration
  points straight into the surface; above the material's angle of repose,
  loose material can slide.
- **Geopotential elevation**: (V_eff − V_ref)/|a| (Scheeres et al., 2016;
  "geopotential altitude" in Barnouin et al., 2019): how high a plate
  stands in the combined gravitational and rotational potential, in m.
  Material moves from high to low elevation, which isn't necessarily
  toward the center.
- **Tilt**: the angle between the surface normal and the radius vector
  (zero on a sphere; Hergenrother et al., 2014, §2.3.5). The surface normal
  is the area-weighted mean normal of the plates whose centroids lie within
  the **tilt radius** of the plate's (each plate's own normal at 0 m). ALTWG
  tilt products may define the surface differently: check before comparing.

### Parameters

| Parameter | Meaning | Units | Valid range | Default |
|---|---|---|---|---|
| Density ρ | bulk density of the body | kg/m³ | > 0 | the body's value (table below), else none: enter one |
| Density σ | 1σ uncertainty of ρ | kg/m³ | 0 ≤ σ < ρ | the body's value |
| Rotation period P | sidereal, about +Z | h | > 0 | the body's value |
| Reference potential | V_ref: the **area-weighted mean** of V_eff over the plates (as ALTWG products), or its **minimum** (elevations then ≥ 0) | — | — | area-weighted mean |
| Tilt radius | plates averaged for tilt's normal | m | ≥ 0 | 0 (the plate's own normal) |
| Evaluate exactly up to | models with more plates are evaluated on a coarser model (below) | plates | ≥ 1000 | 200,000 |

The defaults come from [the small-body
table](coordinate-systems.md#the-small-body-table); the dialog shows each
value's source. For Bennu: ρ = 1190 ± 13 kg/m³ (Barnouin et al., 2019),
P = 4.296061 h (Hergenrother et al., 2019). The dialog estimates the run
time: the gravity sums cost about 2.5 N² operations for N plates (on a
14-core desktop, 20,000 plates take 2 s and 200,000 plates about 3 min;
laptops take two to four times longer). It runs in the background, with
progress in the status line.

### Outputs

Twelve plate datasets, added to the panel's plate data (and colored by
slope), each carrying its provenance (the settings, G, the method, the
mean edge, the plate counts):

| Output | Units |
|---|---|
| area | m² |
| gravitational acceleration (magnitude) | m/s² |
| centrifugal acceleration (magnitude) | m/s² |
| total acceleration (magnitude) | m/s² |
| potential (V_eff, negative) | J/kg |
| slope | ° |
| geopotential elevation | m |
| tilt | ° |
| slope (ρ−σ), slope (ρ+σ) | ° |
| geopotential elevation (ρ−σ), geopotential elevation (ρ+σ) | m |

Export them with **Export → Plate Data as CSV…**.

### Uncertainty and limitations

- **Density.** Gravity scales with ρ, the centrifugal terms don't, so
  slope and elevation are **not** linear in ρ: Varda recomputes them at
  ρ − σ and ρ + σ. Report the range: a lower density means relatively
  more spin and steeper slopes away from the poles and the equator. The
  density σ itself usually comes from the mass and volume (e.g. Bennu's
  ±13 kg/m³), not from the interior.
- **Constant density.** The body is assumed uniform. Real rubble piles
  aren't: Scheeres et al. (2019) found evidence for substantial density
  heterogeneity within Bennu. Slopes near a denser or emptier region are
  biased in ways the density σ doesn't capture.
- **Resolution.** Slopes and tilts are of the plates, at the model's
  **mean edge** (recorded in the provenance): a finer model shows steeper
  local slopes. Compare slopes only between models of similar resolution,
  and state it. Even on a perfect sphere the plates' facets tilt them by up
  to a few degrees from the radius (about 2–3° for 20,000 plates on a
  sphere).
- **Coarse evaluation.** Above the exact limit, gravity is computed on a
  decimated, closed model of at most that many plates; each plate takes the
  gravity of the coarse plate nearest its centroid, and its potential is
  carried to its own centroid to first order (V ≈ V_coarse − g·Δr).
  Normals, centrifugal terms, slopes and tilts use every plate. The error
  grows with the difference between the two surfaces near the plate (of
  the order of the coarse mean edge). The status line and the provenance
  ("evaluatedOn") say when this was done.
- **Reference potential.** Elevations depend on V_ref: state which.
- **Rotation.** Uniform rotation about +Z at the period given; precession,
  tumbling (Dimorphos after DART) and YORP spin-up are not modeled.

### Reporting your results

> Surface slopes and geopotential elevations were computed in Varda
> (version <X.Y>) from the <N>-plate (mean edge <e> m) shape model of
> <body> (<source>), assuming a uniform bulk density of <ρ> ± <σ> kg m⁻³
> (<source>) and a rotation period of <P> h (<source>), using the
> constant-density polyhedron gravity of Werner & Scheeres (1997) with the
> centrifugal term; slope is the angle between the total acceleration and
> the inward surface normal, and elevation is measured from the
> area-weighted mean effective potential (Scheeres et al., 2016). Slopes at
> ρ ± σ span <a>–<b>° (mean).

Add "gravity was evaluated on a <M>-plate decimated model" when the
provenance's `evaluatedOn` is below the plate count.

## Exports

- **Shape as OBJ**: vertices in km, to 17 significant digits; plates
  1-based, wound outward.
- **Shape as STL**: binary STL in km (float32: about 7 significant
  digits).
- **Plate Data as CSV**: every dataset, with the shape's hash and each
  dataset's provenance.
- **Web Page**: a self-contained page (three.js included) with the coloring
  and the view, at most 200,000 plates.
- **Still**: a PNG of the view at a chosen size, with the color bar.

## Reporting your results

State the shape model (its product name and version, resolution as the
mean edge, its plate count and the content hash Varda's CSV records), its
source (e.g. the PDS Small Bodies Node), whether Varda flipped its normals
or read it as meters, and for each plate dataset its source and how it
was matched. A methods sentence:

> We used the <N>-plate (mean edge <e> m) global shape model of <body>
> (<citation>; <archive and product ID>), oriented outward, read in Varda
> (version <X.Y>; Varda contributors, <year>); plate values are reported
> with their 1σ uncertainties.

## Sessions

The workspace's surface, the shape model's file, its body and unit, the
plate data files, the coloring, the light and the camera are saved with the
session (paths, not the meshes), and **Package Project** carries the files.

## Limitations

- Varda doesn't repair, simplify (beyond display) or edit meshes.
- Map projections of irregular bodies, draping images onto shapes,
  footprints, backplanes and lidar are not yet available.
- Very large models take memory while open: see [Limits found](#limits-found).

## Limits found

(Measured with the largest model tried; see the release notes for the
machine.)

## References

- Archinal, B. A., et al. (2018). Report of the IAU Working Group on
  Cartographic Coordinates and Rotational Elements: 2015. *Celestial
  Mechanics and Dynamical Astronomy*, 130, 22.
  https://doi.org/10.1007/s10569-017-9805-5
- Barnouin, O. S., et al. (2019). Shape of (101955) Bennu indicative of a
  rubble pile with internal stiffness. *Nature Geoscience*, 12, 247–252.
- Barnouin, O. S., et al. (2019), above: slope and geopotential altitude
  as used for Bennu.
- Ericson, C. (2005). *Real-Time Collision Detection*. Morgan Kaufmann,
  §5.1.5 (closest point on a triangle).
- Hergenrother, C. W., et al. (2014). The Design Reference Asteroid for the
  OSIRIS-REx mission target (101955) Bennu. arXiv:1409.4704, §2.3.5
  (surface tilt).
- Hergenrother, C. W., et al. (2019). The operational environment and
  rotational acceleration of asteroid (101955) Bennu from OSIRIS-REx
  observations. *Nature Communications*, 10, 1291.
- Gaskell, R. W., et al. (2008). Characterizing and navigating small bodies
  with imaging data. *Meteoritics & Planetary Science*, 43, 1049–1061.
- Möller, T., & Trumbore, B. (1997). Fast, minimum storage ray–triangle
  intersection. *Journal of Graphics Tools*, 2(1), 21–28.
- NAIF. DSK Required Reading. https://naif.jpl.nasa.gov
- Nagy, D. (1966). The gravitational attraction of a right rectangular
  prism. *Geophysics*, 31, 362–371 (the cube check).
- Scheeres, D. J., et al. (2016). The geophysical environment of Bennu.
  *Icarus*, 276, 116–140.
- Scheeres, D. J., et al. (2019). The dynamic geophysical environment of
  (101955) Bennu based on OSIRIS-REx measurements. *Nature Astronomy*, 3,
  352–361.
- Werner, R. A. (1994). The gravitational potential of a homogeneous
  polyhedron or don't cut corners. *Celestial Mechanics and Dynamical
  Astronomy*, 59, 253–278.
- Werner, R. A., & Scheeres, D. J. (1997). Exterior gravitation of a
  polyhedron derived and compared with harmonic and mascon gravitation
  representations of asteroid 4769 Castalia. *Celestial Mechanics and
  Dynamical Astronomy*, 65, 313–344.
