# Installing Varda

## Download a release (no Python needed)

Get the latest release from the
[Releases page](https://github.com/varda-pro/Varda/releases/latest).

### macOS (Apple silicon)

1. Download `Varda-macos-arm64.dmg` and open it.
2. Drag **Varda** onto the **Applications** shortcut.
3. Open Varda from Applications. Varda isn't signed with an Apple developer
   certificate, so the first time, macOS says it can't verify the app:
   - **macOS 15 (Sequoia) and later:** click **Done**, open **System Settings →
     Privacy & Security**, scroll down to the message about Varda and click
     **Open Anyway**, then confirm.
   - **macOS 14 and earlier:** right-click Varda → **Open**, then **Open**.

   You only do this once.

Intel Macs aren't supported by the download; run Varda from source instead.

### Windows

Download `Varda-windows-x64.zip`, unzip it, and run `Varda.exe`. If Windows
shows "Windows protected your PC", choose **More info → Run anyway**.

### Linux

Download `Varda-linux-x64.tar.gz`, unpack it, and run `Varda` in the
`Varda` folder:

```bash
tar -xzf Varda-linux-x64.tar.gz
./Varda/Varda
```

To add Varda to your applications menu (with its icon), run `./Varda/install.sh`
once. If you move the folder later, run it again from the new place.

The downloads include HyPyRameter band parameters. The PyTorch CNN/ViT
classifiers download PyTorch the first time you use them (see
[Optional features](optional-features.md)).

### Updating

A few seconds after it starts, Varda asks GitHub whether a newer version has
been released, and if so, offers to open its release page. Install the new
version the same way as the first (on macOS, drag it to Applications and
choose **Replace**). **Help → Check for Updates…** checks at any time.

The check sends GitHub only a request for the latest release, with Varda's
version. To turn it off, uncheck **Check for updates when Varda starts** in
the window that announces an update, or in the one **Check for Updates…**
shows.

### Varda 0.5.0 and earlier on macOS

Those releases shipped as a zip with a plain folder, which macOS blocks
library by library ("library load disallowed by system policy"). To run one,
mark the unzipped folder as trusted in Terminal, then open `Varda` inside it:

```bash
xattr -dr com.apple.quarantine ~/Downloads/Varda-macos-arm64
```

## Run from source

You need Python 3.13 and the [uv package manager](https://docs.astral.sh/uv/).

1. Clone the repository and change into it.
2. Install the dependencies, adding the optional extras you want:
   ```bash
   uv sync
   # with the optional extras (see Optional features):
   uv sync --extra hypyrameter --extra ml
   ```
   `uv sync` on its own removes extras that were installed before, so repeat
   the `--extra` flags whenever you sync.
3. Run Varda:
   ```bash
   uv run varda
   ```

To check that an installation works, run `uv run varda --self-test` (or
`Varda --self-test` for a downloaded build). It opens, reads and writes test
images, renders one and runs two analyses, then reports each check.

## Troubleshooting

### Planetary coordinate systems fail with conda or another GIS installed

If a shell with conda (or another GDAL/PROJ installation) active starts
Varda, it can export `PROJ_DATA`, `PROJ_LIB` or `GDAL_DATA` pointing at that
installation's data. An older PROJ database has no `IAU_2015` codes, so images
on other bodies fail with a coordinate system error. At startup Varda checks
these variables: when `PROJ_DATA`/`PROJ_LIB` names a `proj.db` whose layout
version (the `DATABASE.LAYOUT.VERSION` rows) is older than the one bundled
with Varda's rasterio, or that lacks the `IAU_2015` authority, it points
`PROJ_DATA` at the bundled data instead. A `GDAL_DATA` outside Varda that is
missing GDAL's schema files is replaced by the bundled folder in the same way.
Anything usable is left alone, and each override is logged as one INFO line
(it is logged before the log file opens, so it is not in the file). To keep your own settings regardless, set `VARDA_KEEP_PROJ_DATA=1`
before starting Varda.

## Memory

### Open images

Varda keeps an image in memory (no-data as NaN; an integer image holding
no-data becomes 32-bit float, or 64-bit for 32-bit integers, and other images
keep their own type) when it fits comfortably, so the display and analyses read it quickly. Otherwise it
reads the image on demand from its file: just the bands shown, the spectrum
under the cursor, or the whole image when an analysis needs it. Both give
the same values; reading on demand is only slower. Two limits decide:

- **One image** is kept in memory only if it takes at most **25 %** of the
  computer's memory (RAM).
- **All images together** may take at most **40 %** of it (about 6.4 GB with 16 GB
  of RAM: four full CRISM MTRDR cubes of 1.5 GB). When a new image would go
  over, Varda switches the images used least recently to reading on demand
  until it fits; if it still wouldn't fit, the new image is read on demand.
  An image you remove from Varda stops counting once nothing uses it.

The 40 % counts the images Varda holds, so it's a target rather than a hard
cap: switching an image to reading on demand frees its memory only once
nothing else holds its pixels (an analysis still running on it, or an
overlay showing it), so for a while the total can be higher.

The rest of the memory stays free for the system, other programs, the
display, and analyses, which need several times their image (below). Results
that analyses make are held in memory and don't count toward the 40 %. The
limits are `IN_MEMORY_SHARE` and `IN_MEMORY_BUDGET_SHARE` in
`varda/image_loading/image_loading_service.py`.

Loading an image takes about its own size in memory at its peak (a 1.5 GB
cube about 1.5 GB); an integer image holding no-data briefly takes about 1.5
times its in-memory size, while it's converted to
float. On top of that, GDAL (which reads the files) keeps one block cache
for all images: by default 5 % of RAM (about 0.9 GB with 16 GB). To change
it, set the `GDAL_CACHEMAX` environment variable (in MB) before starting
Varda.

### Analyses

Analyses work on the whole image in memory, as 64-bit numbers, so they can
need several times the image's size: an image of 480 × 640 pixels and 438
bands is about 1 GB that way, and most spectral analyses need 4 to 8 times
that (IOVF and the Pixel Purity Index, up to about 20 times).

Before an analysis from the **Processing** menu starts, Varda estimates the
memory it may need and compares it with what's free at that moment:

- If it fits, the analysis just runs.
- If it would use more than about 80 % of the free memory, Varda says how
  much it may need and how much is free, and asks before running.
- If it may need more than the computer has, the warning says it may freeze
  the computer or close other programs, and **Cancel** is the default.

You can always choose **Run Anyway**. To need less, crop or subset the image
first (**Processing → Geometry**): fewer pixels or bands, or just the
wavelength range you need. The estimates are upper bounds, measured for each
analysis.

