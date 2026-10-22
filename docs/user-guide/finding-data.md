# Finding data

**Data → Find Data…** opens the **Find Data** workspace:
search for observations of a place, see their footprints on a map, and
download them. It has five panels: **Search** (left), **Map** (center),
**Preview** (right), and **Results** and **Downloads** (tabs below the map).

## Choosing what to search

1. **The body.** The buttons at the top of **Search** are the bodies with
   data (Mars, the Moon, Mercury, Venus, Earth first, then the rest), each
   with how many collections it has. Choosing one turns the map to that
   body and clears the previous body's results, footprints and previews:
   they would be drawn on the wrong world.
2. **The collection.** Below is every collection of that body from every
   catalog, in one list grouped by mission and instrument. Each says which
   catalog offers it (a small tag at the right: **PDS ODE**, **STAC**,
   **File**). Type to narrow the list: every word must appear in the
   collection's mission, instrument, title, description or catalog, so
   `crism` lists every CRISM collection and `crism ode` only ODE's. Up,
   Down and Enter work from the search field. A body with only one
   collection chooses it for you; otherwise nothing is chosen until you
   pick, and **Search** waits.
3. The chosen collection's name, description and catalog appear below the
   list, with **Log In to …** when its catalog needs a login.

**Sources** (bottom of the panel) manages the catalogs: **STAC APIs…**,
**Add Footprint File…**, logins, and **Reload the Catalogs** (for one that
couldn't be reached; the panel says which). Plugins can add catalogs; their
collections join the same list. The catalogs:

- **STAC catalogs**: any STAC API. By default, USGS Astrogeology
  (`https://stac.astrogeology.usgs.gov/api`: Mars, the Moon, Europa and
  more) and Earth Search (`https://earth-search.aws.element84.com/v1`:
  Sentinel-2, Landsat and more). **STAC APIs…** adds, removes or edits them
  (**Restore Defaults** brings these two back). A collection's body comes
  from its `ssys:targets`; collections without one are taken as Earth's.
  Filter: **Max. cloud cover** (100 % by default), for items that report
  `eo:cloud_cover`.
- **PDS Orbital Data Explorer (ODE)**: NASA PDS products of many missions to
  Mars, the Moon, Mercury and Venus, through ODE's REST interface. Each
  collection is one instrument host, instrument and product type with
  footprints. Filters: **Max. incidence angle** and **Max. emission angle**
  (90°, which means no limit), and **Product ID contains** (for example
  `frt`; case doesn't matter).
- **Footprint files**: a vector file of footprints (shapefile, GeoJSON,
  GeoPackage, KML, FlatGeobuf), such as ODE's footprint shapefiles, searched
  offline. Add one with **Add Footprint File…**. The body comes from the
  file's coordinate system: its `IAU_2015` code, else the body its datum,
  ellipsoid or name names, else its radius (within 1 % of one body's IAU 2015
  radii; radii from PROJ's `IAU_2015` authority, Archinal et al., 2018).
  Projected footprints are converted to degrees on that body's IAU 2015
  sphere (3396.19 km for Mars). A projected file on no known body is refused
  with a message rather than taken as Earth, and a geographic one is listed
  with no body. Set **Body** when the file doesn't say, as GeoJSON always
  declares Earth's. Filter: **Product ID
  contains**. A product's ID comes from the first of these columns the file
  has: `ProductId`, `pdsid`, `product_id`, `id`, `obs`, `name`, `Name`; its
  time from `UTCstart`, `UTC_start_time`, `start_datetime`, `datetime`,
  `date`, `StartTime`. Its files are the row's web links, or ODE's file list
  when the row has ODE's `InstHostId`, `InstId`, `ProdType` and `Target`.

A catalog that needs a login asks for it once and keeps it in your
system's keychain. The three catalogs above don't need one.

## Searching

- **Area:** **Map view** (what the map shows), **Box on the map** (a box you
  move and resize), or **An image's footprint** (the bounds of an open,
  georeferenced image, picked under **Image**). The search area is always a
  longitude–latitude rectangle: products whose footprints touch it are
  found, even if they miss the image itself.
- **Dates:** tick **From** and **To**. Dates are UTC days and both are
  inclusive: **From** starts at 00:00 UTC and **To** runs to the end of its
  day (23:59:59.999999 UTC), for every catalog. ODE is sent the dates alone
  and reads them the same way (to 23:59:59.999). For footprint files, a
  product's time (the start-time column) must fall within the dates; STAC
  and ODE apply their own observation-time filters.
- **Filters** (folded until opened): the chosen collection's catalog's own
  filters (listed with each catalog above) and **At most**, results per
  search (100 by default, up to 1,000). **More
  Results** fetches the next page. The status line says how many the catalog
  matched. STAC's cloud-cover filter is applied by Varda after the catalog
  returns a page, so a page can hold fewer products than asked for, and the
  catalog's total includes products the filter later drops.

### The map and the results

The map shows the chosen body in **Equirectangular**, **North Polar
Stereographic** or **South Polar Stereographic** projection, on a sphere of
the body's IAU mean radius (for drawing only; footprints and images keep
their longitude and latitude). A body Varda doesn't know keeps its name and
is drawn at Earth's mean radius, so a polar view's meters are nominal.

**Basemap** shows the body's global imagery (see below) or an open georeferenced image of the same body
under the footprints; **Names** labels features with their IAU names;
**Previews** draws the selected products' browse images over their
footprints (see [Previews](#previews)); **Go to a feature** zooms to a feature by name; **Overlays** adds a vector
file over the map (a traverse, a geologic map) or clears them; **Whole
Body** zooms out.

Click a footprint to select it, Shift-click to add, Ctrl-click (Cmd-click on
a Mac) to remove, and Shift-drag to add every footprint in a box. The
**Results** table lists each product with its catalog's metadata, sortable by
column, with each product's thumbnail beside its ID: for ODE, observation
type, incidence, emission and phase angles, solar longitude, map resolution
(px/°) and comment, rounded to four decimal places. **Show only selected**
narrows the table to the selection.

**Preview** is a gallery of the selected products (the first 40): the one in
front large, its details below, and a strip of the others to click. Turn it
with the round arrows, Left and Right, the scroll wheel or a trackpad swipe;
the product in front is outlined in white on the map (the other selected in
yellow), and double-clicking its picture zooms the map to it.

### Previews

**Preview** shows the catalog's largest browse image of the selected
product, as large as the panel and at the screen's full resolution: for ODE,
its large browse image (`query=lgbrowse`; ODE's thumbnails are about 32
pixels tall); for a STAC catalog, an overview or visual asset that is a PNG,
JPEG or GIF, else the item's thumbnail. A product with none shows its
thumbnail, or says "No preview." Browse images are fetched when you select a
product and kept while the workspace is open (the latest 256 MB of them, at
most 2048 pixels on a side); files over 25 MB aren't fetched.

With **Previews** checked, the map draws the browse images of the selected
products (the first 12) over their footprints, clipped to them. A browse
image is drawn only when it is a north-up map of the footprint's longitude
and latitude box, as ODE's and USGS's browse images of CTX, HiRISE and
THEMIS VIS are. Two checks decide it:

1. **Shape:** its width over height is the box's, in degrees (simple
   cylindrical) or in ground lengths at a latitude within the box
   (equirectangular), within 5 %.
2. **Overlap:** laid over the box, its picture (what isn't black or white
   fill) covers the footprint: an intersection over union of at least 0.85,
   measured at 512 pixels on the longer side. Varda's measurements gave 0.96
   to 0.99 for CTX, HiRISE and THEMIS VIS, and 0.25 to 0.72 for browse
   images that aren't maps of the footprint (THEMIS IR strips in sensor
   geometry, CRISM MTRDR figures).

Anything else (a strip in sensor geometry, a figure with plots) stays in the
Preview panel, which then says it isn't drawn on the map. Footprints across
±180° or reaching a pole aren't drawn either, since a longitude-latitude box
can't place them. In a polar view the image is reprojected (nearest
neighbor).

### Basemaps, names and Go to

The basemap and names are for finding your way around: where Isidis Planitia
or Valles Marineris is, or which part of Gale crater a footprint covers.
They're not data: don't measure on them (see [Uncertainty and
quality](#uncertainty-and-quality)).

**Global imagery.** For each body, **Basemap** offers one or more global
mosaics, the first chosen by default. Each comes in two parts:

- A small global image bundled with Varda (2048 × 1024 pixels for Mars's
  MOLA relief, the Moon, Mercury and Titan, 1024 × 512 for the others), so
  the basemap works offline.
- Sharper tiles streamed from NASA's Solar System Treks as you zoom in.
  Varda works out the zoom whose pixels are no larger than the screen's
  (up to the layer's deepest), fetches only the tiles in view (at most 48;
  a coarser zoom beyond that), and draws them over the bundled image. Tiles
  are fetched in the background, six at a time, a third of a second after
  the view stops moving; the map never waits for them. One view's tiles are
  fetched at a time: when you move on, tiles not yet started for the old
  view are skipped. A tile that hasn't arrived within 10 seconds is left to
  the bundled image. Only replies that are images are cached.

| Body | Layer (Trek identifier) | Deepest zoom | Credit |
|---|---|---|---|
| Mercury | MESSENGER MDIS global mosaic (`Mercury_MESSENGER_MDIS_Basemap_BDR_Mosaic_Global_166m`) | 7 | NASA/JHUAPL/Carnegie Institution of Washington (MESSENGER MDIS) |
| Venus | Magellan radar global mosaic (`Venus_Magellan_C3-MDIR_Global_Mosaic_2025m`) | 5 | NASA/JPL (Magellan SAR); USGS |
| Moon | LRO WAC global mosaic (`LRO_WAC_Mosaic_Global_303ppd_v02`) | 8 | NASA/GSFC/Arizona State University (LRO LROC WAC) |
| Mars | MOLA color shaded relief (`Mars_MGS_MOLA_ClrShade_merge_global_463m`) | 7 | NASA/JPL/GSFC (MGS MOLA); USGS |
| Mars | Viking MDIM 2.1 color mosaic (`Mars_Viking_MDIM21_ClrMosaic_global_232m`) | 7 | NASA/JPL (Viking Orbiter); USGS |
| Phobos | Viking global mosaic (`Phobos_Viking_Mosaic_40ppd_DLRcontrol`) | 5 | NASA/JPL (Viking Orbiter); DLR |
| Ceres | Dawn FC global mosaic (`Ceres_Dawn_FC_DLR_global_59ppd_Feb2016`) | 5 | NASA/JPL-Caltech/UCLA/MPS/DLR/IDA (Dawn FC) |
| Vesta | Dawn FC HAMO global mosaic (`Vesta_Dawn_HAMO_Photo_DLR_global_74ppd_IAU`) | 6 | NASA/JPL-Caltech/UCLA/MPS/DLR/IDA (Dawn FC) |
| Io | Voyager and Galileo color mosaic (`Io_GalileoSSI_Voyager_Global_Mosaic_ClrMerge_1km`) | 4 | NASA/JPL (Voyager ISS, Galileo SSI); USGS |
| Europa | Voyager and Galileo global mosaic (`Europa_Voyager_GalileoSSI_global_mosaic_500m`) | 5 | NASA/JPL (Voyager ISS, Galileo SSI); USGS |
| Ganymede | Voyager and Galileo global mosaic (`Ganymede_Voyager_GalileoSSI_global_mosaic_1km`) | 5 | NASA/JPL (Voyager ISS, Galileo SSI); USGS |
| Callisto | Voyager and Galileo global mosaic (`Callisto_Voyager_GalileoSSI_global_mosaic_1km`) | 5 | NASA/JPL (Voyager ISS, Galileo SSI); USGS |
| Enceladus | Cassini ISS global mosaic (`Enceladus_Cassini_mosaic_global_110m`) | 5 | NASA/JPL/Space Science Institute (Cassini ISS) |
| Titan | Cassini ISS global mosaic (`Titan_ISS_P19658_Mosaic_Global_4km`) | bundled only | NASA/JPL/Space Science Institute (Cassini ISS) |

All are served by NASA Solar System Treks (https://trek.nasa.gov) as WMTS
tiles in their equirectangular ("EQ") projection, which is the map's own:
tile URLs are
`https://trek.nasa.gov/tiles/<Body>/EQ/<layer>/1.0.0/default/default028mm/<zoom>/<row>/<column>.<jpg or png>`,
with 2^(zoom+1) × 2^zoom tiles of 256 pixels, each 180°/2^zoom on a side,
rows from 90°N and columns from 180°W. Each layer's capabilities are at
`https://trek.nasa.gov/tiles/<Body>/EQ/<layer>/1.0.0/WMTSCapabilities.xml`;
all were checked on 2026-10-17. Trek's Titan tiles are centered on 180°
though their capabilities say -180° (Kraken Mare, Ligeia Mare and Menrva
fall 180° from their IAU positions), so Varda bundles a copy shifted back to
IAU longitudes and doesn't stream Titan. Earth has no global imagery or
names. The map shows the active layer's credit under it.

**Projections.** Tiles are streamed only in **Equirectangular**. In the
polar projections, Varda reprojects the bundled image to the view
(nearest neighbor, 1500 × 1500 pixels for the hemisphere, clear beyond the
equator), so the polar basemap is no sharper than the bundled image.

**Offline.** When a tile can't be fetched (no connection, or Trek is down),
the bundled image shows in its place and the status bar says how many tiles
are missing. Tiles already fetched are kept in a cache and work offline.

**The tile cache** is the `basemap-tiles` folder in Varda's cache folder
(on macOS `~/Library/Caches/Varda/basemap-tiles`, on Windows
`%LOCALAPPDATA%\Varda\cache\basemap-tiles`, on Linux
`~/.cache/Varda/basemap-tiles`), laid out as
`<body>/<layer>/<zoom>/<row>/<column>.<ext>`. It holds at most 200 MB: past
that, the least recently used tiles are deleted until it's under 180 MB.
You can delete the folder at any time.

**Names.** With **Names** checked, features are labeled with their names
from the IAU Gazetteer of Planetary Nomenclature. Only names adopted by the
IAU are included; Varda bundles them for every body above. A feature is
labeled once its diameter spans at least 28 screen pixels, so large features
show when zoomed out and smaller ones appear as you zoom in. Labels are
placed largest feature first, and one that would overlap a label already
placed is left out (at most 60 labels at once). A feature the Gazetteer
gives no diameter (an eruptive center, say) counts as 1/400 of the body's
circumference. Hover over a label for the feature's type and diameter.
Positions are the Gazetteer's center points: latitudes planetocentric as
published, longitudes converted from the Gazetteer's 0–360° east to the
map's -180° to 180° east.

**Go to.** Type a name in **Go to a feature…**: the list completes on any
part of the name, ignoring case. Pick one or press Enter, and the map zooms
to a view twice the feature's diameter across, centered on it (wider in
longitude by 1/cos(latitude), so the feature keeps its shape). Pressing Enter
on a partial name goes to the best match: an exact name first, then the
largest feature whose name starts with what you typed, then the largest
containing it. Accents are ignored ("angstrom" finds Ångström).

## Downloading

Check products (or select them) and click **Add to Downloads**. Each
product's files are listed by role: **Data files**, **Labels**, **Auxiliary
files** and **Browse images**. The essential ones are checked (for ODE, the
product's own data and label files; for STAC, its data assets); use
**Check** to change that. A size marked "≈" is
the archive's rounded figure (ODE gives sizes in kilobytes).

**Download** fetches the selected products, or every one not yet
downloaded, one file at a time. Files go to the **Folder** (by default
`Varda Data` in your home folder), in one folder per product:
`<folder>/<catalog>/<collection>/<product>/`, with characters other than
letters, digits, `.`, `_` and `-` replaced by `_`.

How Varda makes sure a file is complete:

- A file downloads to `<name>.part` and takes its name only once complete.
  An interrupted or canceled download resumes from the `.part` file.
- **Checksum:** when the catalog publishes an MD5 or SHA-256 checksum (STAC
  assets with `file:checksum`), the file must match it. A file that doesn't
  is deleted and reported.
- **Size:** when the catalog gives an exact size (STAC's `file:size`), the
  file must have it. Otherwise Varda checks the size the server sends
  (`Content-Length`), when it sends one.
- ODE and footprint files give no checksums, so their files are checked by
  size only, and only against the server's figure.
- A file already in place is kept and not downloaded again if it passes
  these checks. Without a checksum or exact size, any file of that name
  passes: delete it to fetch a newer version.
- One failed file doesn't stop the rest; the product is marked failed with
  the reason for each file. **Select** → by status picks the failed ones to
  try again.

With **Open in Varda when done** (on by default), a product opens when all its
essential files have downloaded. A PDS product opens through its label (the
`.xml` of a PDS4 product, or a detached PDS3 `.lbl`): its data file (`.dat`,
`.img`, ...) is raw pixels, and only the label says how to read them. To open
one yourself, choose the label in **File → Open**. Pixels set to any of the
label's special constants (missing, or a saturation value: CaSSIS fills the
area off the image with one), or outside its valid range, are no-data.

**Export List…** saves the list as a CSV (product, file, URL, size, checksum
and target path) or as a shell script of resumable `curl` commands, to
download elsewhere.

## Reporting your results

What to state in a methods section, and what to cite. See also [Reporting
results from Varda](reporting-results.md) for what applies to every process.

### What to state

- The archive or catalog (for STAC, the API's URL), the collection, and each
  product's ID.
- The product's version or processing level, and its release, from the
  product's label (PDS) or the catalog's metadata.
- When you downloaded it.
- Any filters that shaped the selection (area, dates, viewing angles, cloud
  cover), if the selection itself matters to your argument.

Varda doesn't keep a record of where a downloaded file came from. The
folder names give the catalog, collection and product, but not the URL, the
date, the version or the search. To keep that record:

- **Export List…** as CSV when you download, and keep it with the data. It
  has each file's URL and checksum.
- Keep the product's label files (checked by default for ODE). For STAC, the
  item's metadata isn't saved: note its ID and collection, or save the item
  from the catalog's web page.
- Note the download date yourself.

### Uncertainty and quality

- **Footprints are the catalog's,** and often simplified. They're for
  finding data, not for locating it: use the georeferencing of the
  downloaded product, and its stated accuracy (see [Reporting results from
  Varda](reporting-results.md#precision-is-not-accuracy)).
- **Previews are pictures, not data.** A browse image is the catalog's
  rendering (stretched, often with a color composite of its own) and is
  placed on the map by the footprint's box, so it is only as accurate as
  the footprint: the overlap check above means it lines up with the
  footprint to within a few percent of the footprint's size. Don't measure
  brightness or position from it; download the product.
- **Viewing geometry** in the results (angles, solar longitude) is the
  catalog's, rounded for display. Quote the product's label for exact
  values.
- **Basemaps and names are for orientation.** The bundled images are
  0.35° per pixel (1024 pixels across) or 0.18° (2048 across); streamed tiles go down to
  the Trek layer's resolution. Each mosaic has its own control network and
  version, and may be offset from the coordinates of your data, from other
  mosaics and from the Gazetteer by up to several of its pixels (more on
  the older Voyager- and Galileo-based mosaics). Gazetteer center points are
  approximate, and for large or irregular features (a planitia, a regio) only
  nominal. To locate or measure, use the georeferenced products themselves.
- **Integrity:** a checksum match means the file is the one the catalog
  published. A size match (all ODE gives) catches truncated files but not
  corrupted ones.

### References

- Cite each data set as its archive asks: PDS data sets give a citation,
  often with a DOI, in their documentation; STAC collections may give one in
  their description or in `sci:citation` and `sci:doi` fields.
- PDS ODE REST interface: https://oderest.rsl.wustl.edu/ODE_REST_V2.1.pdf
- STAC specification: https://stacspec.org
- Basemap imagery: NASA Solar System Treks, https://trek.nasa.gov, with the
  data credits in the table above; each layer's description on Trek names
  its source products. The mosaics are released through NASA's PDS and served by
  Trek without use restrictions; credit them as given.
- Feature names: Gazetteer of Planetary Nomenclature, IAU Working Group for
  Planetary System Nomenclature (WGPSN), https://planetarynames.wr.usgs.gov,
  center points from USGS Astrogeology's GIS downloads
  (`https://asc-planetarynames-data.s3.us-west-2.amazonaws.com/<BODY>_nomenclature_center_pts.zip`),
  retrieved 2026-10-17. Each bundled table's first line gives its URL and
  retrieval date (`resources/basemaps/names/`).
