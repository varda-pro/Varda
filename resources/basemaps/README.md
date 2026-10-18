# Find Data basemaps and feature names

Made by `scripts/build_basemaps.py` on 2026-10-17. The mosaics are released
through NASA's PDS and served by Trek without use restrictions, and the
Gazetteer is a US government work; please credit them as below.

## Global images (`<layer>.jpg`)

One global equirectangular image per layer (longitude -180° to 180° east,
latitude 90° to -90°), assembled from NASA Solar System Treks WMTS tiles at
the layer's `bundledZoom` (zoom 2 is 2048 × 1024, zoom 1 is 1024 × 512) and
saved as JPEG at quality 75. Transparent areas are black. Tile URLs are
`https://trek.nasa.gov/tiles/<Body>/EQ/<layer>/1.0.0/default/default028mm/<zoom>/<row>/<col>.<jpg|png>`;
each layer's capabilities are at `.../<layer>/1.0.0/WMTSCapabilities.xml`.
Credits are in `src/varda/catalogs/basemaps.py` (`LAYERS`) and in
`docs/user-guide/finding-data.md`.

Trek's Titan layers are centered on 180° though their capabilities say
-180°. The bundled Titan image is shifted 180° to match IAU longitudes (as
checked against Kraken Mare, Ligeia Mare, Menrva and Xanadu), and Varda
doesn't stream Titan tiles.

## Feature names (`names/<body>.csv.gz`)

From the IAU Working Group for Planetary System Nomenclature's Gazetteer of
Planetary Nomenclature, as USGS Astrogeology publishes it:
`https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/<BODY>_nomenclature_center_pts.zip`
(each file's first line gives its URL and retrieval date). Only names with
the status "Adopted by IAU" are kept. Columns: `name`, `lat` (planetocentric
degrees, as published), `lon` (degrees east, -180 to 180; WGPSN publishes
0–360° east for these bodies), `diameter_km` (empty where WGPSN gives none),
`type` (WGPSN's descriptor term) and `approval`.
