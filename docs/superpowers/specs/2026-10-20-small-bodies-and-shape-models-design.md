# Small bodies and shape models (Phase 5, first part)

Status: design, for review. Source: the SBMT evaluation,
`~/Documents/Software/sbmt_re/VARDA_RECOMMENDATIONS.md` (§2.2, 2.3, 2.5, 5).
Phase 5 in that report has eight tasks; 5.2 (FITS reader) shipped in v0.11.0.
This spec covers **5.1, 5.3, 5.4 and 5.5**. Backplanes, draping, footprints
and lidar (5.6–5.8) get their own spec later.

## Why

Varda's geometry is map projections. On small, irregular bodies such as Bennu,
Ryugu, Eros and 67P, a projection gives wrong distances and areas, latitude
and longitude can name more than one surface point, and "downhill" is not
radial. Planetary scientists working on these bodies use a **plate shape
model** (a triangle mesh in body-fixed km) as the reference geometry. Varda
can't read or show one today, and Bennu data currently gets no body at all,
because PROJ's `IAU_2015` lacks it.

Success: a user opens a Bennu shape model, sees it in 3D, clicks a plate to
read its position, colors it by slope, and can write a methods section for
the slope map, including its uncertainty, from Varda's guide and outputs
alone.

## Goals

1. **Irregular bodies in `varda.geo` (5.1).** Varda knows when a body is
   irregular and says so wherever a sphere or ellipsoid number would mislead.
   It knows the recent-mission bodies PROJ lacks.
2. **A Qt-free shape-model core, `varda.shapes` (5.3).** Read, measure, ray
   cast and export plate meshes.
3. **Shape mode in the 3D Terrain workspace (5.4).** View a shape model, pick
   plates, color plates by per-plate data with its uncertainty.
4. **Shape Geophysics (5.5).** Per-plate gravity, slope, geopotential
   elevation and tilt, with bounds from the density's uncertainty.

## Non-goals (this spec)

- Viewing-geometry backplanes, draping images onto shapes, spectrometer
  footprints, lidar tracks, structure mapping (5.6–5.8).
- Map projections of irregular bodies. Lat/lon/radius are attributes and a
  readout, never an index.
- Mesh repair, decimation or editing. VTK and trimesh are not added.

## Anti-goals

- **No SBMT code.** SBMT ships no license for reuse. Implement only from the
  published papers and format specifications cited here. The decompiled
  sources may be read only to understand what a feature does, never copied or
  translated.
- No number without its method and uncertainty in the guide.
- No silent sphere fallback on an irregular body.

## Constraints

- `varda.shapes` and the `varda.geo` changes are Qt-free and join the
  import-linter contract.
- Lengths in the core are km for vertices (the shape-model convention), m and
  m² for reported lengths and areas, SI for gravity (m/s², J/kg).
- Coordinates: body-fixed; planetocentric latitude, east longitude 0–360°,
  radius = distance from the center of mass in km, per the IAU WGCCRE
  recommendation for small bodies (Archinal et al. 2018, CMDA 130, 22).
- Slow work goes through `runInBackground`; laptop layouts pass
  `assertNothingClipped` at 1280×800.
- New dependency: **SpiceyPy as an optional extra** (`uv sync --extra spice`),
  used only to read NAIF DSK type 2 files. Without it, DSK is listed as
  unavailable with the reason; everything else works. Release builds include
  it only if it adds little to the bundle (decide during implementation, and
  record the size).
- Every user-visible piece updates its guide page in the same change: a new
  `docs/user-guide/small-bodies.md`, plus `3d-terrain.md` and
  `coordinate-systems.md`.

## Decided

### 5.1 Irregular bodies

- `Body` gains a `shape` class: `sphere-like`, `ellipsoid-like` or
  `irregular`, from the IAU 2015 triaxial radii (Archinal et al. 2018,
  Tables 4–5) with an explicit override list. A body is `irregular` when the
  IAU report gives it no ellipsoid or flags a large departure; the threshold
  and the list are written in the guide.
- A small, cited data file (`src/varda/geo/small_bodies.toml`) adds the bodies
  PROJ lacks: **Bennu, Ryugu, Didymos, Dimorphos, Arrokoth**. Each has its NAIF
  ID, mean radius, rotation period, density ± σ, and a citation **per value**.
  Values are copied from the cited papers during implementation and checked
  against them; none are taken from SBMT. Known bodies also get density ± σ
  and rotation period where the geophysics needs them (Eros, Itokawa, Phobos,
  Vesta, Ceres, 67P), from the cited sources.
- On an irregular body:
  - the CRS health check adds "Irregular body: distances and areas on a
    sphere or ellipsoid are approximate; use a shape model";
  - `geo.measure` labels its results "approximate (sphere model)" instead of a
    body-model error term it can't bound;
  - `recommendProjection` refuses the terrain and measurement purposes, and
    says why.
- `coordinate-systems.md` gains "Small and irregular bodies", with the four
  reasons projections fail there (report §2.2).

### 5.3 `varda.shapes`

- `ShapeModel`: vertices (float64, km), plates (int32 triangles), and derived,
  cached plate centroids, unit normals, areas, adjacency, mean edge length, and
  a content hash. Closed or open (FITS X/Y/Z patches) is a property.
- Readers: Wavefront OBJ (`v`/`f`), Gaskell PLT, Gaskell ICQ (q grid, two
  triangles per quad), FITS X/Y/Z-plane grids (from the v0.11.0 FITS reader;
  fill skipped), and NAIF DSK type 2 through SpiceyPy when installed. Format
  references: the Wavefront manual App. B1; the PDS SBN shape-model data-set
  descriptions; Gaskell et al. 2008 (MAPS 43, 1049); NAIF DSK Required
  Reading.
- Ray casting: a numba BVH with Möller–Trumbore intersection (Möller &
  Trumbore 1997); closest point on the mesh.
- Per-vertex and per-plate planetocentric lat/lon/radius.
- Export OBJ and STL (reusing `terrain/solid.py`'s STL writer).
- **Plate data:** a named per-plate array with units and an optional sigma,
  matched to a shape by **content hash and plate count**, never count alone.
  Read from CSV and ALTWG plate-ancillary FITS tables; written as CSV with
  the shape's hash in the header.
- Validation: a sphere mesh's area and volume against 4πr² and 4/3·πr³ within
  the mesh's polygonal error; ray hits against analytic sphere intersections.

### 5.4 Shape mode in 3D Terrain

- A "Shape model" surface source beside "DEM".
- three.js indexed `BufferGeometry`; lighting by a sun direction in the body
  frame, or a headlight (default).
- Picking with three.js `Raycaster`, reported back like the existing `varda:`
  console messages: XYZ (km), lat/lon/radius, plate ID, and the plate value ±
  sigma.
- Plate coloring: colormap with a legend, RGB from three plate arrays, and a
  sigma view (sigma as color, or values faded where sigma/value exceeds a
  threshold).
- Level of detail: a plate-count cap like the existing Detail setting; the web
  export is capped at a stated resolution.
- The 3M-plate Bennu model is the stress case for WebEngine memory; the guide
  states the limits found.

### 5.5 Shape Geophysics

- A Processing analysis that takes a shape model, density ρ ± σρ and rotation
  period P (defaults from the body table, overridable), and the U_ref choice
  (area-weighted mean, as in ALTWG, or minimum).
- Constant-density polyhedron gravity (Werner 1994, CMDA 59, 253; Werner &
  Scheeres 1997, CMDA 65, 313) plus the centrifugal term, evaluated at plate
  centroids by a numba kernel.
- Outputs, as plate data: area (m²), gravitational, centrifugal and total
  acceleration (m/s²), potential (J/kg), slope (°), geopotential elevation (m),
  tilt (°, over a stated radius in m); and **slope and elevation at ρ − σρ and
  ρ + σρ**, since slope isn't linear in ρ.
- Definitions follow Scheeres et al. 2016 (Icarus 276, 116) for slope and
  geopotential elevation, and Barnouin et al. 2019 and the ALTWG SIS for tilt.
- Validation: a uniform sphere (U = −GM/r outside) and Werner's cube.
- The guide states: constant density (Bennu's interior is heterogeneous; cite
  the source after checking it), resolution dependence (slopes are at the mean
  plate edge length, which the output records), the U_ref choice, and the
  density bounds. Models above ~200k plates are evaluated on a coarser model
  and interpolated, and the output says so.

## Data

- Tests use synthetic meshes (spheres, cubes, an open patch, a two-lobe
  body) generated in the tests.
- The real-data check is **Bennu (OSIRIS-REx)**: a global SPC/OLA shape model
  and an ALTWG plate product from the PDS Small Bodies Node, downloaded into
  `~/Documents/Software/varda-aux/` and never committed. Tests that need it
  skip when it's absent.

## Left to the builder

- File layout inside `varda.shapes`, the BVH's construction details, the LOD
  scheme, and the UI layout of the shape panel, within the constraints above.
- The irregular threshold's exact value, if the IAU tables leave a choice;
  it's written in the guide either way.
- Whether SpiceyPy goes into the release bundle (by its size).
