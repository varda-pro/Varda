# Sessions and saving results

## Workspace tabs

Workspaces open as tabs, which work like a web browser's: drag a tab to
reorder it, drag it out of the tab bar (or double-click it) to open it in a
window of its own, and close it with its × or a middle click. Closing a
workspace's own window puts it back in the tabs. Sessions reopen the
workspaces in the tabs' order.

### Collapsing panels

Every workspace's panels can be collapsed out of the way. Click the pin
button on a panel's title bar to collapse it to a tab on the edge of the
workspace; click that tab to slide the panel back out, and click the pin again
to put it back where it was. Collapsing the panels you aren't using leaves more
room for the images and plots. The CRISM Ratio workspace starts with most of
its panels collapsed (the ROI and Points managers, ROI Plots, and each view's
render settings and histogram), leaving the two images and the ratio plots.

## Sessions

**File → Save Session…** (⌘S) writes a `.varda` file recording the open
images (by path), the open workspaces and their ROIs. **File → Open Session…**
(⌘O) loads the images and reopens the workspaces alongside whatever is already
open. Images that exist only in memory (analysis results you have not
exported) are left out, so export them first, or package the project.

Varda also autosaves the session every three minutes and on quit (to
`autosave.varda` in its app-data folder, next to the logs). After a crash,
**File → Restore Last Session** brings it back.

## Image properties

Right-click an image in the Image List and choose **Properties…** to see
what it is (file, format, size, data type, map projection, pixel size) and
to edit the metadata Varda reads from it:

- its **name**;
- the **no-data value**: the value that marks pixels without data, such as
  a fill value of -999 or 65535 that the file never declared (`nan` for
  NaN, empty for none). Pixels with that value are then left out of the
  display, statistics and analyses like any no-data;
- the **wavelength unit** and the **description**;
- per band: the **name**, **wavelength** and **FWHM**, and whether it's
  **good** (bands not ticked are left out of analyses, as ENVI's bad bands
  list does). **Fill Wavelengths** numbers many bands at once from a start
  and a step.

Edits take effect at once in every view and are kept in sessions and
packaged projects; **Reset to the File's** drops them. For an ENVI image,
**Also save into the ENVI header** writes them into its `.hdr` (backed up
first as `.hdr.bak`), so other software sees them too.

### Corrected coordinate systems

An image can carry a **corrected coordinate system** that Varda uses
instead of the one its file declares. Varda sets one on its own in a single
case: a planetary image tagged with Earth's degrees (WGS 84) whose label
names another body (see
[Coordinate systems](coordinate-systems.md#the-one-automatic-fix-and-why-only-that-one)).

- **The file isn't changed.** The correction lives in Varda, with the
  image, like the edits above; it is never written into the original file
  or its header.
- **Sessions keep it.** A session (and a packaged project) records the
  CRS each image uses, as WKT, among its edits (`"crs"` in the image's
  `overrides`). Reopening the session uses that CRS, even when the
  automatic check would choose another, and checks it again; a session
  that names the file's own CRS (`"crs": null`) keeps the file's. The
  image's `crsFix` record is saved beside its edits (`"crsFix"`) and
  restored with them, so a fix applied by hand, a CRS set by hand and an
  undone fix are recorded the same way after reopening. A session saved
  before Varda checked CRSs doesn't say, so the check made when the image
  opens stands; one saved before Varda kept the records restores the CRS
  without a record of how it was chosen.
- **Editing other metadata keeps it.** Saving edits in Image Properties
  doesn't drop the corrected CRS; **Reset to the File's** returns the image
  to how it opened, including the automatic correction.
- **Exports use it.** Files Varda writes from the image, or from results
  made from it, carry the corrected CRS, and their provenance records the
  correction as `crsFix` (see
  [Reporting results](reporting-results.md#what-varda-records-and-what-it-doesnt)).

## Sharing a project

**File → Package Project…** puts the whole project into one `.varda.zip`
that anyone with Varda can open on their own computer (**File → Open
Session…**), as a packaged Illustrator document can be:

- the session: workspaces, their settings, ROIs and lines;
- every image file, with the files that belong to it (an ENVI header, a PDS
  label, GDAL's `.aux.xml`, world files), under their own names, so the
  pointers inside them still work;
- the other files workspaces use, such as a 3D Terrain workspace's line
  files;
- analysis results that exist only in memory, as ENVI files;
- `manifest.json`: the Varda version, the date, where each image came from,
  and every file's size and SHA-256 checksum, so a reader of your paper can
  check they're working with the same data.

Varda works out the size first. A package over 1 GB asks before it's
written, since it takes a while to write and many services limit how
large a shared file can be; it won't start if the disk lacks the room.
Files a workspace uses that can no longer be found are listed and left out.
Writing happens in the background, with its progress shown. The files are
compressed lightly: the four MISE example products (352 MB) package to
166 MB in a few seconds.

### Opening a package

Opening a `.varda.zip` unpacks it into Varda's app-data folder, in
`sessions/<package name>-<key>` (macOS: `~/Library/Application Support/Varda`;
Windows: `%LOCALAPPDATA%\Varda`; Linux: `~/.local/share/Varda`). The key
comes from the package's `manifest.json` (a hash of it, so it identifies the
exact data), or from the package's path, size and modification time if it has
none.

- **Reopening reuses the folder.** The same, unchanged package is unpacked
  only once; a marker file written after the last file says the unpacking
  finished, and an interrupted one is redone. A changed package gets a new
  folder.
- **Superseded copies are removed.** At startup and after opening a
  package, Varda deletes older unpacked copies of the same package name. The
  newest copy is always kept, as is any copy that the autosave or an open
  image uses (the removals are in the log). A package's only copy is never
  removed, since a session you saved elsewhere may point into it. If Varda
  can't read the autosave, it removes nothing. It only touches folders it
  made inside `sessions/`.
- **It checks the disk first.** If the package's unpacked size (plus a
  margin) won't fit, Varda says so and doesn't start. Unpacked data is a full
  copy, so a 5 GB package takes about 5 GB while it's in use.
- To reclaim the space at once, close the project and restart Varda.

## Saving results

Every Processing result joins the project's image list. Tick **Save result to
file** in the analysis dialog to also write it to disk (ENVI `.img`/`.hdr` or
GeoTIFF), or export any image later with **File → Export Image…** or by
right-clicking an image → **Export Image…**.

In **Save as**, a name without a folder (`result.tif`) goes into the folder
you last exported to, or else the image's own folder; the dialog then shows
the full path, and the status bar says where the file was written. The
suffix picks the format (`.img` or none for ENVI, `.tif` for GeoTIFF).
Trailing dots and spaces are dropped. Names that some system can't store are
refused before anything is written, so the file can be shared with any
computer: the characters `< > : " | ? *` and the names Windows reserves
(`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`). When writing
fails (a folder that doesn't exist, no permission, a full disk) the message
says what to do; the library's own text is under **Show Details…**.

Every result records how it was made: the process, every setting, its
inputs with how each of them was made, the Varda version, the time, the
plugin and its version if a plugin made it, and the versions of the
libraries or tools that did the work. Results made inside workspaces
(mosaics, displacement fields, crops, the Spectral Hourglass's outputs)
record their workspace settings. An exported file keeps the full record,
and metadata such as class names and colors and what Inverse Transform
needs, in its `VARDA` metadata domain (inside a GeoTIFF; in the
`.img.aux.xml` beside an ENVI file, so keep it with the `.img` and `.hdr`),
plus a one-line summary in its description. Varda reads them back when it
opens the file. See [Reporting results from Varda](reporting-results.md)
for what is and isn't recorded, and how to read the record.
