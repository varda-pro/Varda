# Finding data

**Data → Find Data…** opens the **Find Data** workspace:
search a catalog for observations of a place, see their footprints on a
map, and download them. It has five panels: **Search** (left), **Map**
(center), **Preview** (right), **Results** (below the map) and **Downloads**
(beside the results).

## Catalogs

Choose one under **Catalog**, then a **Collection** (click the box to search
collections by body, mission or instrument). Plugins can add more catalogs.

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
  file's coordinate system (by its radius, within 1 %); set **Body** when the
  file doesn't say, as GeoJSON always declares Earth's. Filter: **Product ID
  contains**. A product's ID comes from the first of these columns the file
  has: `ProductId`, `pdsid`, `product_id`, `id`, `obs`, `name`, `Name`; its
  time from `UTCstart`, `UTC_start_time`, `start_datetime`, `datetime`,
  `date`, `StartTime`. Its files are the row's web links, or ODE's file list
  when the row has ODE's `InstHostId`, `InstId`, `ProdType` and `Target`.

A catalog that needs a login asks for it once (**Log In…**) and keeps it in
your system's keychain. The three catalogs above don't need one.

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
- **At most:** results per search (100 by default, up to 1,000). **More
  Results** fetches the next page. The status line says how many the catalog
  matched. STAC's cloud-cover filter is applied by Varda after the catalog
  returns a page, so a page can hold fewer products than asked for, and the
  catalog's total includes products the filter later drops.

### The map and the results

The map shows the collection's body in **Equirectangular**, **North Polar
Stereographic** or **South Polar Stereographic** projection, on a sphere of
the body's mean radius (for drawing only). **Basemap** puts an open
georeferenced image of the same body under the footprints; **Add Overlay…**
draws a vector file (a traverse, a geologic map); **Whole Body** zooms out.

Click a footprint to select it, Shift-click to add, Ctrl-click (Cmd-click on
a Mac) to remove, and Shift-drag to add every footprint in a box. The
**Results** table lists each product with its catalog's metadata, sortable by
column: for ODE, observation type, incidence, emission and phase angles,
solar longitude, map resolution (px/°) and comment, rounded to four
decimal places. **Preview** shows the selected product's thumbnail and details.
**Show only selected** narrows the table to the selection.

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
- **Viewing geometry** in the results (angles, solar longitude) is the
  catalog's, rounded for display. Quote the product's label for exact
  values.
- **Integrity:** a checksum match means the file is the one the catalog
  published. A size match (all ODE gives) catches truncated files but not
  corrupted ones.

### References

- Cite each data set as its archive asks: PDS data sets give a citation,
  often with a DOI, in their documentation; STAC collections may give one in
  their description or in `sci:citation` and `sci:doi` fields.
- PDS ODE REST interface: https://oderest.rsl.wustl.edu/ODE_REST_V2.1.pdf
- STAC specification: https://stacspec.org
