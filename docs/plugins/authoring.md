# Writing a Varda plugin

A plugin adds features to Varda without changing Varda itself. Today a plugin
can add:

- **analyses**: new items in the Processing menu that turn an image into a new
  image, such as a spectral index, a filter or a classifier
- **file formats**: new kinds of image that File → Import Image(s) can open

This guide walks through [`examples/band-depth`](examples/band-depth/), a
complete plugin that adds **Processing → Spectral Parameters → Band Depth**.
You can also describe a feature and have your AI write the plugin with
**Plugins → Kindle a Feature (Build a plugin with AI)…** (see
[the user guide](../user-guide/kindle-a-feature.md)); the AI reads this guide.

## What a plugin looks like

A plugin is a folder:

```text
band-depth/
  plugin.toml        # what the plugin is
  plugin.py          # the code
  tests/test_plugin.py   # optional, but strongly encouraged
  README.md
```

`plugin.toml`:

```toml
[plugin]
name = "band-depth"        # unique; lowercase letters, digits, - and _
version = "0.1.0"
description = "Band depth at a chosen wavelength, against a straight-line continuum between two shoulders"
author = "Your Name"
api = "1"                  # the plugin API version it was written for
entry = "plugin.py"        # the module Varda imports
requires = []              # optional: modules it needs beyond Varda's own
```

Install a plugin with **Plugins → Install from Folder…** or **Install from
Zip…**, and manage installed plugins with **Plugins → Manage Plugins…**:
enable, disable, reload after editing, remove, or see why one didn't load.
Installed plugins live in Varda's app-data folder, and work in the
downloadable app as well as when running from source.

## Import only from `varda.api`

`varda.api` is the plugin API: a documented set of classes and helpers that
stays stable within an API version. Everything else in Varda is internal and
can change in any release.

| Name | What it's for |
|---|---|
| `Analysis` | Base class for an analysis (see below) |
| `IntParameter`, `FloatParameter`, `BoolParameter`, `EnumParameter`, `StringParameter`, `MultiChoiceParameter`, `Vec2Parameter` | An analysis's settings. Varda builds the settings dialog from them |
| `VardaRaster` | An image: `getData()`, `getBands([...])`, `bandModel`, `bandNames`, `transform`, `crs`, `name` |
| `BandModel` | `image.bandModel`: band `centers` in nm (or `None` if unknown), `fwhm`, `badBands`, `noData` |
| `readCube(image)` | The image as a float64 `(rows, cols, bands)` cube with no-data and bad bands handled, plus a `(rows, cols)` mask of usable pixels |
| `singleBandRaster(image, values, name=, bandName=)` | A one-band result on the input's grid and georeferencing |
| `sameBandsRaster(image, values, name=, description=)` | A result with the input's bands, e.g. a filtered cube |
| `ArrayDataSource` | Build any other result from a numpy array (with wavelengths, band names, transform and CRS) |
| `DataSource` | The interface a new file format implements |
| `AnalysisContext` | Extras the workspace provides, such as `context.rois` |
| `ProgressCallback` | `reportProgress(percent, message)` |
| `hookimpl` | Marks the functions that register your features |

## An analysis

```python
from varda.api import Analysis, FloatParameter, hookimpl, readCube, singleBandRaster


class BandDepth(Analysis):
    analysisId = "band_depth"            # unique within your plugin
    name = "Band Depth"                  # the menu item and dialog title
    description = "Depth of an absorption at a chosen wavelength …"
    category = "Spectral Parameters"     # the Processing submenu; "Filtering/Spectral" nests (new ones are created)

    left = FloatParameter("Left shoulder", 1800.0, (300.0, 5000.0), "nm")
    center = FloatParameter("Band center", 1930.0, (300.0, 5000.0), "nm")
    right = FloatParameter("Right shoulder", 2070.0, (300.0, 5000.0), "nm")

    def unavailableReason(self, image):
        # Shown instead of Run when this image can't be used
        if image.bandModel.centers is None:
            return f"{image.name} has no wavelengths in known units."
        return ""

    def run(self, image, reportProgress):
        reportProgress(0, "reading image")
        cube, valid = readCube(image)
        # … compute `depth`, a (rows, cols) array …
        depth[~valid] = float("nan")
        return singleBandRaster(image, depth, name=f"{image.name} BD", bandName="BD1930")


@hookimpl
def registerAnalyses():
    return [BandDepth]
```

What Varda does with it:
- **Menu and dialog:** it adds the menu item, and a settings dialog with an image picker built from your Parameters.
- **Running:** `run` executes in a background thread with a progress dialog, so `run` must not touch widgets. The result joins the project's images, and can be saved to ENVI or GeoTIFF.
- **Provenance:** every result records the analysis, its settings, its inputs (with how they were made), the Varda version, and your plugin's name and version, and keeps the record in the files it is saved to. If a library does the scientific work, name its distribution in `software = ("spectral",)` and its installed version is recorded too.
- **Memory:** before your analysis runs, Varda checks its estimate of the memory it needs against what's free and warns if it may not fit. By default it assumes 6 times the image in 64-bit numbers. If yours needs less (a band-by-band filter) or more, say so: `memoryFactor = 2` (a multiple of rows × columns × bands × 8 bytes), plus `memoryFixed` for a fixed amount, or override `estimateMemory(image)`.

Other optional methods:
- `prepareFor(image)`: adapt the settings to an image before the dialog shows them, for example by offering only the choices its wavelengths support
- `createPreviewWidget(image)`: a widget shown under the settings

To use the workspace's ROIs, set `needsRois = True` and read `self.context.rois`.

Rules of thumb:
- **Units:** work in nanometers through `image.bandModel.centers`, never by guessing units from the values.
- **Bad pixels:** keep no-data as NaN; `readCube` already gives you NaN for it.
- **Errors:** raise `ValueError` with a sentence the user can act on, and it's shown to them.

## A file format

```python
from varda.api import hookimpl


class MyFormatDataSource:
    """Implements varda.api.DataSource: width, height, bandCount, dtype,
    wavelengths, getData(bandIndices=None, window=None), and so on."""


@hookimpl
def registerDataSources():
    return [("My Format", (".myf",), MyFormatDataSource)]
```

## Testing

Put tests in `tests/`, run with `uv run pytest path/to/plugin`. Build small
synthetic images with `ArrayDataSource`, plant something you know the answer
to, and check the result, as
[`examples/band-depth/tests/test_plugin.py`](examples/band-depth/tests/test_plugin.py)
does.

## Sharing

A curated plugin gallery is coming, and you'll be able to submit to it from
Varda. Until then, share the plugin folder as a zip; others install it with
**Plugins → Install from Zip…**.

## Trust

A plugin is Python code that runs inside Varda with the user's permissions.
Varda says so when installing. Write plugins that do only what they describe:
no network access, no files written outside what the user asks for.
