# Spectral libraries

Varda keeps one set of spectral libraries that every part of the app uses:
the spectral plots' **Library Spectra** overlay, the Spectral Hourglass's
**Identify** step and the **FATT** window. A library you create or load
anywhere appears in all of them straight away.

There are three kinds:

- **Bundled** with Varda, always there and read-only:
  - **CRISM MICA numerators**: the CRISM type spectra (MICA) as corrected
    I/F numerators, comparable to unratioed spectra taken directly from a
    CRISM cube (31 spectra, 0.436–3.897 µm).
  - **USGS minerals**: mineral spectra from the USGS Spectral Library
    Version 7 (splib07a), at the library's own spectral resolution.
- **Yours**: libraries you create and fill with spectra from your images or
  copied from other libraries. Editable.
- **Loaded** from files elsewhere (ENVI, CSV, a USGS splib07 folder).
  Read-only; Varda remembers them by path and reads them again each time it
  starts.

**Processing → Spectral Libraries…** opens the **Spectral Library Manager**,
where you browse, build, edit and export them.

## The Spectral Library Manager

- **Libraries** (left): every library with its number of spectra. Bundled
  and loaded libraries are grayed: they are read-only. **New…** creates an
  empty library of yours, **Load…** loads a file, **Load USGS Folder…** a
  folder of USGS splib07 spectra, **Rename…** renames one of yours,
  **Delete…** deletes one of yours (and its files; it asks first), or
  **Forget** stops remembering a loaded file (the file stays), and
  **Export…** saves any library (see [Exporting](#exporting)).
- **Spectra** (middle): the selected library's spectra, with their value
  units and source. **Search spectra by name** filters them. Select one or
  several to preview them; **Rename…** and **Delete** edit your own
  libraries, and **Copy to Library…** copies the selected spectra, records
  and all, into one of your libraries or a new one. This is how you build a
  library from bundled or loaded ones.
- **Preview and record** (right): the selected spectra plotted against
  wavelength in nm (as stored, not scaled), and below, what's recorded for
  the selected spectrum and the library: its file, description and the
  citation for bundled libraries.

If a remembered library file can't be read when Varda starts (a drive is
unplugged, or the file was moved or deleted), the manager says so at the
top, with the path and the reason, and the plots' library picker notes that
a remembered library is missing. Varda keeps looking for it at each start
until you press **Forget Missing**; to use it from its new place, load it
again.

## Adding spectra from your images

**Add to Library…** saves a spectrum you are looking at in one of your
libraries (or a new one you name in the same dialog):

- **On a spectral plot**: click a curve (a pixel spectrum, a box mean or an
  ROI mean), then **Add to Library…** under **Selected Curve**. The curve's
  own values are saved, not as shown: any **Y Scale** or **Y Offset** is
  left out. The plot's wavelengths are converted to nm. A plot whose x axis
  is band numbers (an image without wavelengths) can't add to a library:
  give the image its wavelengths first (image list → **Properties…**).
- **In the Spectral Hourglass**: **Add to Library…** under the endmember
  table saves the selected endmembers' mean spectra (all endmembers with
  pixels, if none is selected), each with its ± 1 σ and pixel count.
- **A library spectrum on a plot**: **Add to Library…** copies the library
  spectrum itself (its own values and wavelengths, not the plot's resampled
  or scaled view), keeping its record.

The dialog asks for the spectrum's name and its **value units**, i.e. what
the numbers are (reflectance, I/F, radiance in W m⁻² sr⁻¹ µm⁻¹, DN…). Varda
doesn't know this from the image, so state it: it is recorded with the
spectrum and shown wherever the spectrum is. Left blank, the spectrum says
"not stated".

### What is recorded with each spectrum

| Field | Meaning |
|---|---|
| Name | As you give it |
| Wavelengths | In nm, as plotted (not resampled) |
| Values | The spectrum's own values, in its value units |
| Value units | What the values are, as you stated them |
| σ (region means) | Per-sample population standard deviation (ddof 0) of the pixels behind the mean, in the value units |
| Image, Image file | The image's name and file path (blank for an image made in Varda and not saved) |
| Region | `pixel (col 12, row 40)`; `5×5 px box at (col 12, row 40)` (the box around the clicked pixel; an even-sized box extends one pixel further right and down); `ROI “dunes”`; or `endmember “clay” (Spectral Hourglass)`. Columns and rows count from 0 at the image's top-left |
| Pixels | How many pixels are behind the spectrum (1 for a single pixel) |
| Statistic | `single pixel` or `mean ± 1 σ` |
| Note | How the values were prepared, e.g. a Spectral Hourglass endmember is the mean of its pixels' *prepared* spectra, with the bands kept and whether IOVF filtered them |
| Varda version, Added | The Varda version and the date and time (UTC) the spectrum was added |
| Library, File | For a spectrum read from a library: that library's name and file |
| Copied from | For a spectrum copied from another library: that library |

A box or ROI mean's σ is the spread of the pixels, not the uncertainty of
the mean: for that, give σ/√n with n (Pixels), and note that spatial
correlation between neighboring pixels makes σ/√n a lower bound (see
[Viewing and measuring](viewing-and-measuring.md#roi-statistics)). Some bands
may have fewer pixels with data than Pixels says (no-data values); the
per-band counts are in a plot's CSV export, not in the library.

## Loading libraries

**Load Library…** (in the manager, the FATT window and the Spectral
Hourglass) and **Load USGS Folder…** read:

- **ENVI spectral libraries** (`.sli` with its `.hdr`): the header needs
  `spectra names`, `wavelength` and `wavelength units`.
- **CSV** (`.csv`/`.txt`): the first column is wavelength, every other column
  a spectrum, named in the first non-comment row; lines starting with `#` are
  comments and empty cells are missing values. A column named `<name> std` is
  that spectrum's σ; `<name> n` columns (pixel counts from a plot export) are
  skipped, so a plot's CSV export loads as a library.
- **USGS splib07 folders**: one sub-folder per spectrum holding its `.txt`
  and `*Wavelengths*.txt` files, as downloaded from the USGS Spectral Library.

Wavelengths may be in nm, µm or cm⁻¹: name the unit in the wavelength column
(e.g. `wavelength_um`), in a `# units: µm` comment, or in the ENVI header's
`wavelength units`; otherwise Varda infers it from the values and logs a
warning. Check the preview in the manager: a library read in the wrong unit
lands far from where you expect. See [Spectral Hourglass](spectral-hourglass.md#preparing-a-spectral-library)
for an example CSV.

## Where your libraries live

Your libraries are saved as you change them, as ENVI spectral libraries in
the `libraries` folder of Varda's app-data folder (beside `plugins`):

- macOS: `~/Library/Application Support/Varda/libraries`
- Windows: `%APPDATA%\Varda\libraries`
- Linux: `~/.local/share/Varda/libraries`

Each library is three files: `<name>.sli` (the values), `<name>.hdr` (the
ENVI header) and `<name>.varda.json` (the record, below). The list of
library files you loaded is `loaded.json` in the same folder, and the
libraries new plots leave out are in `preferences.json` (see
[Which libraries a plot uses](#which-libraries-a-plot-uses)). Back up the
folder to keep your libraries; copy it to another computer to move them.

### The file format

An ENVI spectral library has one wavelength axis for all its spectra.
Spectra from different sensors have different wavelengths, so Varda writes a
library on the **union of its spectra's wavelengths**, each spectrum `NaN`
where it has no sample. Nothing is interpolated or resampled: every value
written is one you saved. Values are 64-bit floats (`data type = 5`),
wavelengths in nm.

What an ENVI header can't hold goes in the sidecar, `<name>.varda.json`:
each spectrum's exact name (ENVI lists can't hold commas or braces, so the
header's names have them removed), source, value units, record and σ, plus
the library's name, description and citation. When Varda reads a library
with its sidecar it drops each spectrum's `NaN` samples, so each spectrum
gets back exactly its own wavelengths. A spectrum's own missing values
(e.g. bad bands) are dropped too; plots and matching ignore missing values
anyway.

Other software (ENVI, SPy) reads the `.sli`/`.hdr` as a standard ENVI
spectral library and ignores the sidecar.

## Exporting

**Export…** in the manager saves any library, bundled ones included:

- **ENVI spectral library** (`.sli`): as above, with the sidecar.
- **CSV** (`.csv`): `wavelength_nm`, then one column per spectrum and a
  `<name> std` column after each region mean; empty cells have no value.
  Comment lines (starting `#`) give the library's name, its citation and one
  `# varda-spectrum: {…}` line per spectrum holding its record as JSON.
  Varda reads these back; tell other CSV readers to skip lines starting with
  `#` (in pandas, `comment="#"`).

## Using libraries

- **Spectral plots** (pixel spectra, the General Image Analysis and Dual
  Image plots, the Spectral Hourglass's spectra): under **Library Spectra**,
  **Libraries (n of m)** chooses which libraries to pick from (one, several
  or all; any library added later is included until you untick it), **Search
  by name** filters the spectra, and **Add to Plot** (or a double-click)
  plots the selected ones. They are scaled and offset to overlay a curve;
  see [Library overlays](viewing-and-measuring.md#library-overlays) for what
  that means for figures and exports. **Manage…** opens the manager.
  See [Which libraries a plot uses](#which-libraries-a-plot-uses) for how
  the choice is remembered.
- **Spectral Hourglass → Identify** and **FATT**: the library list holds
  every library; each window keeps its own choice. See
  [Spectral Hourglass](spectral-hourglass.md) and [FATT](fatt.md).

### Which libraries a plot uses

Each plot remembers the libraries you **turned off**, by name; every other
library is on. So:

- **A library added later is on** in every plot, until you untick it.
- **Sessions keep each plot's choice**: the General Image Analysis and Dual
  Image workspaces' ROI plot and each of their pixel-spectra plots, and the
  Spectral Hourglass's spectra plot. A session reopens only the first
  pixel-spectra plot; the others get their saved choices, in the order they
  were first opened, when you open them again (**New Plot**, or the Ratio
  Explorer's plot). A session saved before Varda kept this opens with every
  library on, as its plots had then.
- **New plots start with your most recent choice**, made in any plot, even
  after Varda restarts. It is kept in `preferences.json` in the libraries
  folder ([below](#where-your-libraries-live)). Opening a session doesn't
  change it.
- **Renaming** one of your libraries in the manager keeps it off wherever it
  was off: in open plots and for new plots. A session saved before the
  rename names the old library, so in that session it comes back on.
- **A library that no longer exists** (deleted, forgotten, or a remembered
  file that couldn't be read when Varda started) is ignored; if a library of
  that name is made or loaded again later, it comes in on.

The choice only affects which spectra the picker lists; it does not change
any spectrum or any plotted curve.

## Reporting and citing

In a methods section, say which library each reference spectrum came from
and how you compared it (overlay, ranked match, FATT), and cite:

- **Bundled libraries**: the manager shows each library's citation.
  - USGS spectral library: Kokaly, R. F., Clark, R. N., Swayze, G. A., Livo,
    K. E., Hoefen, T. M., Pearson, N. C., Wise, R. A., Benzel, W. M., Lowers,
    H. A., Driscoll, R. L., & Klein, A. J. (2017). USGS Spectral Library
    Version 7. *U.S. Geological Survey Data Series 1035*.
    https://doi.org/10.3133/ds1035
  - CRISM MICA library: Viviano-Beck, C. E., Seelos, F. P., Murchie, S. L.,
    et al. (2014). Revised CRISM spectral parameters and summary products
    based on the currently detected mineral diversity on Mars. *Journal of
    Geophysical Research: Planets*, 119(6), 1403–1431.
    https://doi.org/10.1002/2014JE004627
- **Loaded libraries**: cite them as their providers ask; the manager shows
  each one's file.
- **Your libraries**: describe each spectrum from its record: the image
  (and its product ID or citation), the region and number of pixels, whether
  it is one pixel or a mean ± 1 σ, any processing named in its Note, the
  value units and the Varda version. Spectra copied from other libraries
  carry those libraries' names: cite them too. Exporting the library as CSV
  gives all of this in one file to archive with your results.

### Limitations

- A library spectrum is only as good as its source: a mean over a region
  mixes whatever is in it, and the σ says how much the pixels differ, not
  how wrong the mean is.
- Spectra from images keep the image's calibration and units; Varda doesn't
  convert between reflectance, I/F and radiance. Matching (Identify, FATT)
  ignores overall brightness, but a library mixing value units is still
  hard to interpret: state the units.
- Wavelengths are stored as the plot or image had them; any error in the
  image's wavelength calibration is carried into the library.
- Loaded libraries are read when Varda starts and when loaded: if the file
  changes on disk, load it again (Forget, then Load…) to see the change.
