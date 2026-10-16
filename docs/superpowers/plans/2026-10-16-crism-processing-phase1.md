# CRISM Processing Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** turn CRISM IR TRR3 I/F (1.0–2.6 µm) plus its DDR into a corrected,
MTRDR-consistent product, in sensor space and map projected, with a σ
companion and full provenance. It runs headless (API and CLI) and from
Processing > CRISM.

**Architecture:** a Qt-free core in `src/varda/crism/`, one module per step,
driven by a recipe (ParameterGroups) and run as four passes over line chunks
of a memory-mapped cube. Varda data sources expose the CRISM files in the
app. A small Qt package, `src/varda/crism_ui/`, adds the dialog and menu
actions. Validation metrics live in the core and are pinned by synthetic
tests plus data-gated regression tests on `varda-demo-data`.

**Tech stack:** Python 3.13, NumPy, SciPy (`ndimage`, `spatial.cKDTree`,
`sparse`), Numba only where a profile shows it pays, rasterio/GDAL (pixels),
**pvl** (new, BSD-3: PDS3 labels), `xml.etree.ElementTree` (PDS4), attrs,
pyproj, PyQt6 (UI only), pytest and pytest-qt.

**Spec:** `docs/superpowers/specs/2026-10-16-crism-processing-design.md`.
Approved 2026-10-16 with the recommended D1–D8. Evidence ledger:
`docs/superpowers/specs/crism/evidence-ledger.md`. Its entry IDs (R1, A3, G2…)
are cited below. Every task that settles a decision updates its ledger entry
from *(planned)* to the real test name.

## Global Constraints

- Follow `CLAUDE.md`: uv, ruff format plus ruff check, `ty check`, pytest with
  tests colocated in `_tests/test_*.py`; American English in all prose and UI
  text; type hints everywhere and **never `Any`**; no lazy imports;
  camelCase names (the house style in Qt-free packages too, as in
  `mosaicking/`).
- `varda.crism` must not import PyQt6. Add `"varda.crism"` to the
  import-linter `source_modules` in `pyproject.toml`.
- Slow work in the GUI runs through `varda.utilities.background.runInBackground`.
  pyproj CRS objects are created on the calling thread or under
  `varda.utilities.proj_lock.PROJ_LOCK`.
- Internal array convention: `(line, sample, band)`, bands in **ascending
  wavelength**, float32 on disk and float64 in arithmetic. Invalid samples
  are NaN in memory and 65535 on disk. No NaN is ever written.
- Wavelengths are in nanometers everywhere in code.
- Fixed values from the spec:
  - ATM fit window 1850–2200 nm; score window **1947–2066 nm**; continuum
    Legendre degree 3.
  - Despike threshold k = 5.
  - Noise column group 16; quiet fraction 0.5.
  - Chunk 64 lines.
  - Transmission-scoring subset 64 central lines.
  - Elevation-model H accepted range 5–20 km.
  - Projection pixel 18 m (36 m when PIXEL_AVERAGING_WIDTH = 2); the
    nearest-neighbor empty threshold is 0.75·s·√2.
  - MTRDR IR vector of 411 bands; bands above 2600 nm get `bbl = 0`.
- **Output names:** `<stem>_vmtr` (map-projected I/F), `_vmtr_sigma`,
  `<obs>_<ctr>_de<mac>l_vmtr`, `<obs>_<ctr>_in<mac>l_vmtr`, the optional
  `_vter` (sensor space), and `.recipe.json`, `.provenance.json`,
  `.diagnostics.npz`. `<stem>` is the TRR3 stem with `trr3` replaced, e.g.
  `frs00031442_01_if168l_vmtr`.
- **ENVI header keys added by Varda:** `varda sigma file`,
  `varda sigma kind` (`instrument-noise`), `varda sigma steps`,
  `varda history`.
- **Data-gated tests** locate the demo data through `VARDA_DEMO_DATA`
  (default: the folder `varda-demo-data` beside the repository root) and
  `pytest.skip` when files are absent. CAT aux files are never copied into
  the repository or redistributed.
- **New dependency:** `pvl>=1.3`. Announce it in
  `/Users/phillipsm/Documents/Software/VARDA_COORDINATION.md` before
  `uv add`.
- **Shared files** (`pyproject.toml`, `uv.lock`, `src/varda/main.py`,
  `src/varda/_actions/_processing_actions.py`, `CHANGELOG.md`, `CLAUDE.md`,
  `docs/user-guide/README.md`) are touched only in Tasks 1, 21, 22 and 24.
  Rebase on `origin/main` before Task 24. Regenerate `uv.lock` with
  `uv lock`; never hand-merge it.
- Stage explicit paths only. Commit messages end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Missing calibration files without network.** A clear
   `MissingCalibrationError` lists each missing file and where to get it,
   before any pass starts. It must not fail halfway through. Test:
   `test_calibration.py::test_missing_files_listed_before_processing` (Task 6).
2. **Heavily invalid data** (FRS edges: 10% of samples are 65535; empty
   columns and lines; a band that is all invalid). The pipeline finishes,
   those pixels are 65535 in every output, and no NaN or inf is ever written.
   Test: `test_pipeline.py::test_invalid_regions_become_nodata` (Task 20).
3. **Southern hemisphere, longitudes near 0°/360°, and polar observations.**
   φ_P is equatorward (−18.3° gives −15°); x and y stay continuous across
   ±180° and 0°/360°; beyond ±65° the grid is polar stereographic. Test:
   `test_projection.py::test_reference_latitude_and_wrap` (Task 17).
4. **Re-running into a folder that already holds outputs.** Each file is
   written to a temporary name and renamed when complete, so a failed run
   never leaves a header pointing at a partial image. Test:
   `test_writers.py::test_failed_write_leaves_previous_product` (Task 18).
5. **Binned (HRL/HRS, 320 samples) observations.** Calibration selection
   honors the binning code in CDR names, and a column-count mismatch between
   the cube and a CDR raises a clear error. Test:
   `test_calibration.py::test_binned_observation_selects_binned_cdrs`
   (Task 6).

---

### Task 1: Package, dependency and product IDs

**Files:**
- Create: `src/varda/crism/__init__.py` (exports filled in by Task 20; for now a docstring only)
- Create: `src/varda/crism/products.py`
- Create: `src/varda/crism/_tests/__init__.py`, `src/varda/crism/_tests/test_products.py`
- Modify: `pyproject.toml` (add `pvl>=1.3`; add `"varda.crism"` to `source_modules`), `uv.lock` (via `uv add`)
- Modify: `/Users/phillipsm/Documents/Software/VARDA_COORDINATION.md` (log line: the pvl dependency)

**Interfaces:**
- Produces:
  - `ProductId` (attrs frozen):
    - fields: `classType: str` ("FRT"), `observationId: int` (0x5850), `counter: int`, `activity: str` ("IF"), `macro: int`, `sensor: str` ("L"/"S"/"J"), `productType: str` ("TRR"/"DDR"/"MTR"/"TER"), `version: str`;
    - `ProductId.parse(name: str | Path) -> ProductId` (case-insensitive, accepts paths and extensions, raises `ValueError`);
    - `.stem -> str` (lower case, e.g. `"frs00031442_01_if168l_trr3"`);
    - `.observationName -> str` ("FRS00031442");
    - `.replace(**changes) -> ProductId`.
  - `CdrId` (attrs frozen):
    - fields: `level: int` (4 or 6), `partition: int`, `sclk: int`, `kind: str` ("WA", "AT", "BP", "SB", "SW"…), `frameRate: int | None`, `binning: int | None`, `exposure: int | None`, `wavelengthFilter: int | None`, `side: int | None`, `sensor: str`, `version: int`;
    - `CdrId.parse(name: str | Path) -> CdrId`, handling both CDR4 (`CDR4<P><sclk>_<AA><R><B><EEE><W><C><S>_<v>`) and CDR6 (`CDR6_<P>_<sclk>_<AA>_<S>_<v>`).
  - `VsAdrId` (attrs frozen):
    - fields: `partition`, `sclk`, `scanId: str` (5 hex characters), `binning`, `wavelengthFilter`, `sensor`, `version`;
    - `VsAdrId.parse(name)` (e.g. `ADR50924300803_28D04_VS00L_9`).

- [ ] **Step 1: Announce pvl, then add the dependency and the contract.** Append the coordination log line; run `uv add "pvl>=1.3"`; add `"varda.crism",` to `source_modules` in `pyproject.toml`.
- [ ] **Step 2: Write the failing tests** in `test_products.py`:
  - `test_parse_trr3`: `ProductId.parse("FRS00031442_01_IF168L_TRR3.IMG")` gives `classType == "FRS"`, `observationId == 0x31442`, `counter == 1`, `activity == "IF"`, `macro == 168`, `sensor == "L"`, `productType == "TRR"`, `version == "3"`, and `stem == "frs00031442_01_if168l_trr3"`.
  - `test_parse_ddr_and_mtrdr`: `"frt00005850_07_de167l_ddr1.lbl"` gives productType "DDR", version "1"; `"frt00005850_07_if167j_mtr3"` gives sensor "J", productType "MTR".
  - `test_replace_round_trip`: `parse(x).replace(activity="DE", productType="DDR", version="1").stem` is the DDR stem.
  - `test_parse_cdr4`: `CdrId.parse("CDR420843667218_AT0000000L_7")` gives level 4, partition 2, sclk 843667218, kind "AT", binning 0, wavelengthFilter 0, sensor "L", version 7.
  - `test_parse_cdr6`: `CdrId.parse("CDR6_1_0000000000_SW_L_3")` gives level 6, kind "SW", version 3, binning None.
  - `test_parse_vs_adr`: `VsAdrId.parse("ADR50924300803_28D04_VS00L_9.IMG")` gives scanId "28D04", binning 0, version 9.
  - `test_rejects_garbage`: `pytest.raises(ValueError)` for `"notcrism.img"`.
- [ ] **Step 3: Run them.** `uv run pytest src/varda/crism/_tests/test_products.py -v` should FAIL (ImportError).
- [ ] **Step 4: Implement `products.py`** with one compiled regex per ID type (DPSIS §2.3.4).
- [ ] **Step 5: Run them again.** The same command should PASS; then run `uv run lint-imports` and expect "Contracts: … kept".
- [ ] **Step 6: Commit.** `git add pyproject.toml uv.lock src/varda/crism/__init__.py src/varda/crism/products.py src/varda/crism/_tests/__init__.py src/varda/crism/_tests/test_products.py`, then `git commit -m "CRISM: package skeleton, product IDs, pvl dependency"`.

### Task 2: Test data crops

The real-data crops every later unit test reads, plus the script that made
them.

**Files:**
- Create: `src/varda/crism/_tests/data/make_crops.py`
  - It reads from `VARDA_DEMO_DATA` and writes the crops beside itself.
  - Run it once, by hand; the crops are committed.
- Create (generated):
  - `src/varda/crism/_tests/data/frs00031442_01_if168l_trr3.{img,lbl}`: lines 80–95, samples 260–379, all 438 bands, ROWNUM table kept;
  - `.../frs00031442_01_de168l_ddr1.{img,lbl}`: the same window;
  - `.../cdr490947778566_wa0000000l_3.{img,lbl}` and `.../cdr420862848019_at0000000l_7.{img,lbl}`: samples 260–379;
  - `.../frt00005850_07_if167j_mtr3.hdr`, `.../frt00005850_07_if167j_mtr3.lbl` and `.../frt00005850_07_wv167j_mtr3.tab` (text only).
- Create: `src/varda/crism/_tests/conftest.py`, with fixtures `cropDir() -> Path` and `demoData() -> Path` (skips when absent).
- Create: `src/varda/crism/resources/mtrdr_wavelengths.csv`: the full 489-row wv table (spect_id, row, wavelength_nm, fwhm_nm, bbl) copied from FRT00005850, with a header comment naming the source product.

**Interfaces:**
- Produces:
  - the fixtures `cropDir`, `demoData`;
  - the crop files. Their labels keep the original keywords but have `LINES`, `LINE_SAMPLES`, `FILE_RECORDS` and `^ROWNUM_TABLE` rewritten, plus a comment `/* Varda test crop: lines 80-95, samples 260-379 of <product> */`.

- [ ] **Step 1: Write `make_crops.py`.**
  - It rewrites the PDS3 label numbers with regex substitutions and asserts that each anchor matched.
  - It copies the ROWNUM table bytes unchanged.
- [ ] **Step 2: Run it.** `VARDA_DEMO_DATA=/Users/phillipsm/Documents/Software/varda-demo-data uv run python src/varda/crism/_tests/data/make_crops.py` should leave the files above in the data folder at about 3.6 MB in total (`du -sh`).
- [ ] **Step 3: Check the crops with GDAL.**
  ```bash
  uv run python -c "import rasterio; d=rasterio.open('src/varda/crism/_tests/data/frs00031442_01_if168l_trr3.lbl'); print(d.count, d.width, d.height)"
  ```
  Expected: `438 120 16`.
- [ ] **Step 4: Commit.** `git add src/varda/crism/_tests/data/ src/varda/crism/_tests/conftest.py src/varda/crism/resources/mtrdr_wavelengths.csv`, then `git commit -m "CRISM: test crops and the MTRDR wavelength table"`.

### Task 3: Labels (PDS3 and PDS4)

**Files:**
- Create: `src/varda/crism/labels.py`
- Test: `src/varda/crism/_tests/test_labels.py`

**Interfaces:**
- Consumes: the Task 2 crops, and the full FRT00005850 `.xml` labels in demo data (data-gated).
- Produces:
  - `LabelValue = str | int | float | list["LabelValue"]`.
  - `ArrayLayout` (attrs frozen):
    - fields: `dataFile: Path`, `offset: int` (bytes), `lines: int`, `samples: int`, `bands: int`, `interleave: Literal["BIL", "BSQ", "BIP"]`, `dtype: np.dtype`, `rownumOffset: int | None` (bytes);
    - `def memmap(self) -> np.ndarray`, which returns the stored axis order: BIL `(line, band, sample)`, BSQ `(band, line, sample)`.
  - `ProductLabel` (attrs frozen):
    - fields: `path: Path`, `keywords: dict[str, LabelValue]` (top-level keywords, names as written, e.g. `"MRO:WAVELENGTH_FILE_NAME"`), `image: ArrayLayout`, `bandNames: tuple[str, ...]`;
    - `def get(self, key: str, default: LabelValue | None = None) -> LabelValue | None`.
  - `def readLabel(path: Path) -> ProductLabel` (`.lbl`/`.LBL` via pvl, `.xml` via ElementTree; it resolves `^IMAGE` and `^ROWNUM_TABLE` record pointers using `RECORD_BYTES`).

- [ ] **Step 1: Write the failing tests.**
  - `test_trr3_crop_label`: for `readLabel(cropDir / "frs00031442_01_if168l_trr3.lbl")`:
    - `image.lines == 16`, `image.samples == 120`, `image.bands == 438`;
    - `image.interleave == "BIL"`, `image.dtype == np.dtype("<f4")`, `image.offset == 0`;
    - `image.rownumOffset == 16 * 438 * 120 * 4`;
    - `get("MRO:WAVELENGTH_FILE_NAME") == "CDR490947778566_WA0000000L_3.IMG"`.
  - `test_ddr_band_names`: the DDR crop label has `len(bandNames) == 14`, `bandNames[0] == "INA at areoid, deg"`, `bandNames[13] == "Spare"` and `image.interleave == "BSQ"`.
  - `test_cdr_label`: the WA crop has `image.lines == 1`, `image.bands == 438`.
  - `test_pds4_matches_pds3` (data-gated, FRT00005850 IF167L): `readLabel(xml).image` equals `readLabel(lbl).image` in lines, samples, bands, interleave, dtype and offset.
  - `test_source_products_list`: `get("SOURCE_PRODUCT_ID")` is a list containing `"FRS00031442_01_SC168L_EDR0"`.
- [ ] **Step 2: Run them.** `uv run pytest src/varda/crism/_tests/test_labels.py -v` should FAIL.
- [ ] **Step 3: Implement `labels.py`.**
  - PDS3: `pvl.load`; descend `FILE` → `IMAGE`; turn pvl `Quantity` into its value; turn sets and sequences into lists.
  - PDS4: read `File_Area_Observational/Array_3D_*`; map axis names Line/Band/Sample ("Last Index Fastest") to the interleave; read `offset` and `Special_Constants/missing_constant`.
- [ ] **Step 4: Run them again.** The same command should PASS (the data-gated test is skipped without demo data).
- [ ] **Step 5: Commit.** `git commit -m "CRISM: PDS3/PDS4 label reading"` (explicit paths).

### Task 4: Readers

**Files:**
- Create: `src/varda/crism/readers.py`
- Test: `src/varda/crism/_tests/test_readers.py`

**Interfaces:**
- Consumes: `readLabel`, `ProductId`, `CdrId`, `VsAdrId`.
- Produces:
  - **`Trr3`** (attrs frozen):
    - fields: `id: ProductId`, `label: ProductLabel`, `detectorRows: np.ndarray` (bands,) int, ascending-wavelength order;
    - `def lines(self, rows: slice) -> np.ndarray`: float64 `(l, s, b)` in ascending wavelength. Invalid samples become NaN: 65535, values below 1e-6, and non-finite values;
    - properties `shape -> tuple[int, int, int]`, `binning -> int` (`PIXEL_AVERAGING_WIDTH`), `sclk -> float` (from `SPACECRAFT_CLOCK_START_COUNT`), `wavelengthFileName -> str`.
  - **`def readTrr3(path: Path) -> Trr3`**: band order is reversed when the detector rows ascend, because the L rows ascend with descending λ.
  - **`DdrBand(IntEnum)`**: `INCIDENCE_AREOID=0, EMISSION_AREOID=1, PHASE=2, LATITUDE=3, LONGITUDE=4, INCIDENCE_MOLA=5, EMISSION_MOLA=6, SLOPE=7, SLOPE_AZIMUTH=8, ELEVATION=9, THERMAL_INERTIA=10, ALBEDO=11, LOCAL_SOLAR_TIME=12, SPARE=13`.
  - **`Ddr`** (attrs frozen):
    - fields: `id: ProductId`, `label: ProductLabel`;
    - `def band(self, band: DdrBand, rows: slice | None = None) -> np.ndarray`: float64 `(l, s)`, with 65535 as NaN.
  - **`def readDdr(path: Path) -> Ddr`**.
  - **`DetectorFrame`** (attrs frozen):
    - fields: `name: str` (product ID), `values: np.ndarray` (s, b) float64 with NaN for 65535, `detectorRows: np.ndarray` (b,);
    - `def alignedTo(self, rows: np.ndarray) -> np.ndarray` returns `(s, len(rows))` in the order of `rows`, and raises `ValueError` when a row is missing.
  - **`def readDetectorFrame(path: Path, line: int = 0) -> DetectorFrame`**: WA, AT, BP, SB; also VS ADR lines 0–2.
  - **`def readVsAdr(path: Path) -> tuple[DetectorFrame, DetectorFrame, DetectorFrame]`**: transmission, McGuire artifact, Pelkey artifact (ledger source-policy entry).
  - **`def usableLines(ddr: Ddr) -> np.ndarray`**: (l,) bool, False for slew rows. A row is a slew row when its median along-track ground step (from DDR lat/lon) exceeds 4× the scene median; only the largest contiguous run of good rows is kept. The heuristic is adapted from jezero-rim-mapping (MIT), with attribution in the docstring.

- [ ] **Step 1: Write the failing tests.**
  - `test_trr3_layout_and_nodata` (R1, R3): for `readTrr3(crop).lines(slice(0, 16))`:
    - the shape is `(16, 120, 438)`;
    - the NaN fraction equals the fraction of raw samples that are 65535 or below 1e-6, computed independently from `np.fromfile`.
  - `test_rownum_table` and `test_ascending_wavelengths` (R2): `trr3.detectorRows` is strictly decreasing in row number (wavelength ascending). The wavelengths come from `readDetectorFrame(wa).alignedTo(trr3.detectorRows)`; their column medians are increasing, with the first valid value about 1001 nm and the last about 3936 nm, each ±2 nm.
  - `test_ddr_band_names` (R4): `readDdr(crop).band(DdrBand.INCIDENCE_AREOID)` lies in (0°, 90°), and the label band name for `DdrBand.SPARE` is "Spare".
  - `test_detector_frame_alignment`: `alignedTo` raises `ValueError` for a row absent from the frame.
  - `test_usable_lines_drops_slew`: a synthetic DDR (built in-test) whose first 5 rows step 10× farther than the rest gives `usableLines(...)[:5]` all False and the rest True.
  - `test_vs_adr_layout` (data-gated on `CAT_v7_4_aux`): `readVsAdr` on `ADR/VS/ADR50924300803_28D04_VS00L_9.IMG` returns three frames of shape (640, 438), and the transmission median at the band nearest 2007 nm is below 0.8.
- [ ] **Step 2: Run them.** `uv run pytest src/varda/crism/_tests/test_readers.py -v` should FAIL.
- [ ] **Step 3: Implement `readers.py`** on `ArrayLayout.memmap()`. `lines()` copies only the requested rows; it never reads the whole file.
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger.** Fill in the real test names for R1–R4.
- [ ] **Step 6: Commit.** `git commit -m "CRISM: TRR3, DDR, CDR and VS ADR readers"`.

### Task 5: Varda data sources and the MTRDR round trip

**Files:**
- Create: `src/varda/image_loading/data_sources/crism_data_source.py`
- Modify: `src/varda/image_loading/data_sources/__init__.py` (import the new module so it registers)
- Modify: `src/varda/image_loading/data_sources/envi_data_source.py`: copy `varda sigma file`, `varda sigma kind` and `varda sigma steps` into `extraMetadata["sigma"] = {"path": <absolute>, "kind": str, "steps": list[str]}`
- Test: `src/varda/image_loading/data_sources/_tests/test_crism_data_source.py`

**Interfaces:**
- Consumes: `readTrr3`, `readDdr`, `readDetectorFrame`, `readLabel`, `ProductId`.
- Produces:
  - **`CrismProductDataSource(RasterioDataSource)`**, registered with `@register_data_source("CRISM TRDR/DDR/CDR (PDS)", (".lbl", ".xml"))`:
    - `canOpen(path) -> bool` is a classmethod: True for TRR, DDR and CDR4 images whose `INSTRUMENT_ID` is CRISM;
    - nodata 65535;
    - band order is ascending wavelength (map indices on the way into `getBands`/`getData`);
    - wavelengths are the median of WA columns 270–369 when the WA CDR is found (same folder, a `CRISM_CDR/wa` folder above it, or `../CRISM_CDR/wa`), otherwise the band numbers;
    - `extraMetadata["crismColumnWavelengths"]` holds the (s, b) map;
    - `bbl` is 0 for the 65.535 µm placeholder;
    - for a DDR, `bandNames` come from the label.
  - **`CrismMapLabelDataSource(DelegatingDataSource)`**: `canOpen` is True for MTR/TER `.lbl`/`.xml` files with a sibling `.hdr`. It delegates everything to `ENVIDataSource(<hdr>)`, which is the correct georeference (R5).

- [ ] **Step 1: Write the failing tests.**
  - `test_opens_trr3_crop`: `openDataSource(str(crop_trr3_lbl))` is a `CrismProductDataSource`. Its `bandCount == 438`, `nodata == 65535`, its wavelengths are increasing, and `getPixelSpectrum(5, 5)` has NaN at exactly the invalid bands.
  - `test_ddr_band_names_in_app`: `openDataSource(ddr crop).bandNames[0] == "INA at areoid, deg"`.
  - `test_mtrdr_label_uses_hdr` (R5, data-gated FRT00005850): `openDataSource(".../frt00005850_07_if167j_mtr3.lbl").transform` equals the ENVI source's, with origin (4417887.6, 1091944.9).
  - `test_hdr_georeference_matches_de_backplanes` (R5, data-gated): for 200 random valid pixels, the DE lat/lon projected with the header's CRS is within 1 m (median) of the pixel center from `pixelToGeo`.
  - `test_mtrdr_round_trip_metadata` (data-gated): the ENVI source has nodata 65535, 489 wavelengths, `extraMetadata["bbl"]` of length 489, and `extraMetadata["fwhm"]` present.
  - `test_sigma_companion_metadata`: a tiny ENVI pair written in `tmp_path`, whose header has `varda sigma file = x_sigma.img`, opens with `extraMetadata["sigma"]["path"]` absolute and `kind == "instrument-noise"`.
- [ ] **Step 2: Run them.** `uv run pytest src/varda/image_loading/data_sources/_tests/test_crism_data_source.py -v` should FAIL.
- [ ] **Step 3: Implement both sources and the ENVI sigma keys.**
- [ ] **Step 4: Run them again,** then the whole data-source test folder; both should PASS (no regression in the other loaders).
- [ ] **Step 5: Update the ledger** (R5), then commit: `git commit -m "CRISM: open TRR3/DDR/CDR in Varda; MTRDR labels use the ENVI header; sigma companion metadata"`.

### Task 6: Calibration library

**Files:**
- Create: `src/varda/crism/calibration.py`
- Create: `src/varda/crism/resources/at_cdrs.csv`, with columns `name, scan_id, sclk_start, sclk_stop, sha256`:
  - one row per PDS AT CDR (L, binning 0): the 20 local files and their SHA-256, from M5;
  - plus the binned (B = 1, 2, 3) AT names found in `CAT_v7_4_aux/aux_files/CDRs/AT` that are also on PDS `mrocr_0001/cdr/at/`. Check each name with an HTTP HEAD request when writing the table.
- Test: `src/varda/crism/_tests/test_calibration.py`

**Interfaces:**
- Consumes: `Trr3`, `readDetectorFrame`, `readVsAdr`, `CdrId`, `VsAdrId`; `varda.catalogs.http` for downloads.
- Produces:
  - `PDS_CDR_URL = "https://pds-geosciences.wustl.edu/mro/mro-m-crism-2-edr-v1/mrocr_0001/cdr/"`.
  - `class TransmissionSource(LabeledEnum)`: `PDS_AT = "PDS atmospheric transmission CDRs"`, `CAT_VS_ADR = "CAT volcano-scan ADRs"`, `BOTH = "Both"`.
  - `Transmission` (attrs frozen): `scanId: str`, `sourceName: str`, `values: np.ndarray` (s, b) in the cube's band order, `sha256: str`, `artifacts: np.ndarray | None` ((2, s, b) for a VS ADR).
  - `CalibrationFileStatus` (attrs frozen): `name: str`, `kind: str`, `localPath: Path | None`, `url: str | None`.
  - `class MissingCalibrationError(Exception)`, with a `files: list[CalibrationFileStatus]` attribute.
  - `Fetcher = Callable[[str, Path], None]`, which downloads a URL to a path.
  - `class CalibrationLibrary`:
    - `__init__(self, folders: Sequence[Path], cacheFolder: Path, catAuxFolder: Path | None = None, fetch: Fetcher | None = None)`;
    - `status(self, trr3: Trr3) -> list[CalibrationFileStatus]`;
    - `ensure(self, trr3: Trr3) -> None`: fetches what is missing; raises `MissingCalibrationError` listing everything still missing;
    - `wavelengths(self, trr3: Trr3) -> np.ndarray`: (s, b), the WA aligned to the cube;
    - `transmissions(self, trr3: Trr3, source: TransmissionSource) -> list[Transmission]`: deduplicated by SHA-256 of `values`; filtered by sensor, binning and wavelength filter; sorted with the scan whose SCLK period contains `trr3.sclk` first (C3, C4);
    - `badPixels(self, trr3: Trr3) -> np.ndarray | None`: (s, b) bool.
  - `def downloadTo(url: str, target: Path) -> None`: the default `Fetcher`. It writes to `target.with_suffix(".part")` and renames.

- [ ] **Step 1: Write the failing tests.** All use `tmp_path` folders, the crops and a fake `fetch` that records URLs.
  - `test_finds_local_cdr_folder` (C1): a folder with `CRISM_CDR/wa/<crop WA>` makes `wavelengths(trr3)` shape (120, 438) with no fetch calls.
  - `test_fetches_missing_wa`: with an empty folder, `ensure` calls fetch with `PDS_CDR_URL + "wa/cdr490947778566_wa0000000l_3.img"` (and `.lbl`).
  - `test_missing_files_listed_before_processing` (Review Focus 1): when fetch raises `OSError`, `ensure` raises `MissingCalibrationError` whose `files` name the WA and every AT.
  - `test_dedupe_transmissions` (C3): two copies of the crop AT under different names give `len(transmissions(...)) == 1`.
  - `test_period_extrapolation_recorded` (C4): a TRR3 SCLK after every period still returns transmissions, and the first one's period is the latest.
  - `test_binned_observation_selects_binned_cdrs` (Review Focus 5): a fake 320-sample TRR3 with `PIXEL_AVERAGING_WIDTH = 2` selects only binning-1 AT names from the table. A 640-sample CDR given for a 320-sample cube raises `ValueError("... 640 columns ... 320 ...")`.
  - `test_cat_aux_vs_adrs` (data-gated on the aux folder): `transmissions(trr3, TransmissionSource.CAT_VS_ADR)` returns more than 6 entries, each with `artifacts.shape[0] == 2`.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `calibration.py` and generate `at_cdrs.csv`.**
  - Write the table with a short script run once from the demo and aux data. The script is not committed, but the table header records its source.
  - Search order: the given folders (each also as `<folder>/CRISM_CDR/<kind>`), the cache, then the CAT aux `CDRs/<KIND>` and `ADR/VS`.
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Verify a real URL.** `curl -sI <PDS_CDR_URL>at/cdr420862848019_at0000000l_7.img | head -1` should return `HTTP/1.1 200` (or `HTTP/2 200`).
- [ ] **Step 6: Update the ledger** (C1–C4), then commit: `git commit -m "CRISM: calibration library (find, select, dedupe, fetch)"`.

### Task 7: Observation

**Files:**
- Create: `src/varda/crism/observation.py`
- Test: `src/varda/crism/_tests/test_observation.py`

**Interfaces:**
- Consumes: `ProductId`, `readTrr3`, `readDdr`.
- Produces:
  - `CrismObservation` (attrs frozen):
    - fields: `name: str`, `counter: int`, `irCube: Path`, `irDdr: Path`, `vnirCube: Path | None`, `vnirDdr: Path | None`;
    - `CrismObservation.fromFolder(folder: Path, counter: int | None = None) -> CrismObservation`: searches recursively for `*_if*l_trr3.(xml|lbl)`, preferring `.xml`. Without a `counter`, it takes the counter whose IR cube has the most lines (FRT 07, FRS 01). It raises `FileNotFoundError` naming what is missing;
    - `CrismObservation.fromFiles(irCube: Path, irDdr: Path | None = None) -> CrismObservation` (the DDR is found by stem when not given);
    - `readIr(self) -> tuple[Trr3, Ddr]`.

- [ ] **Step 1: Write the failing tests.**
  - `test_from_folder_crop`: a `tmp_path` copy of the crop TRR3 and DDR in `trdr/` and `ddr/` gives `name == "FRS00031442"`, `counter == 1`, and DDR paths found.
  - `test_missing_ddr`: raises `FileNotFoundError` with "DDR" in the message.
  - `test_chooses_central_counter`: two fake counters (labels only, LINES 16 and 400) choose the 400-line one.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `observation.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Commit.** `git commit -m "CRISM: observation discovery"`.

### Task 8: Synthetic cube and validation metrics

The test helper every step's tests share, and the metrics their assertions use.

**Files:**
- Create: `src/varda/crism/_tests/synthetic.py` (a helper module, not collected as tests)
- Create: `src/varda/crism/validation.py` (its interfaces and tests are listed under Task 19's "Produces in `validation.py`" and the validation tests there; build them here, in `test_validation.py`)
- Test: `src/varda/crism/_tests/test_synthetic.py`, `src/varda/crism/_tests/test_validation.py`

**Interfaces:**
- Consumes: the crop WA and AT (`readDetectorFrame`); USGS splib07 spectra already bundled in Varda (find them with `varda.analysis.spectral_library`); `smile.standardVector` is not needed here.
- Produces:
  - `SyntheticTruth` (attrs frozen), with arrays per pixel or column:
    - `surface (l, s, b)` (true I/F after photometry, before atmosphere);
    - `beta (l, s)`, `shiftNm (s,)`, `stripes (s, b)` (multiplicative), `spikes (n, 3)` int (t, x, λ);
    - `noiseSigma (l, s, b)`, `incidenceDeg (l, s)`, `elevationM (l, s)`;
    - `columnWavelengths (s, b)`, `transmission (s, b)`;
    - `artifact (s, b)` (the injected per-column artifact, high-pass);
    - `columnUnitMask (l, s)` (the column-aligned geology unit).
  - `def makeSyntheticCube(lines: int = 64, seed: int = 0, *, snr: float = 100.0, shiftAmplitudeNm: float = 0.8, scaleHeightKm: float = 11.0, stripeAmplitude: float = 0.005, spikeRate: float = 1e-4, artifactAmplitude: float = 0.006) -> tuple[np.ndarray, SyntheticTruth]`:
    - returns the observed cube `(l, 120, 438)`;
    - units come from 4 library spectra: bland basalt, a 1.9 µm hydrated phase, an Fe/Mg smectite, and a carbonate. The carbonate is the column-aligned unit spanning all lines;
    - noise is σ² = a + bR;
    - the atmosphere is `T(λ + δ)^β`, interpolated in ln T along each column's wavelengths.

- [ ] **Step 1: Write the failing tests.**
  - `test_deterministic`: the same seed gives identical arrays.
  - `test_truth_consistency`: the observed cube divided by `exp(beta·ln T(λ+δ))`, by `stripes` and by the artifact factor, minus the surface, has residual std within 5% of `noiseSigma` at non-spike samples.
  - `test_column_unit_spans_all_lines`: `columnUnitMask.all(axis=0)` is True for at least one column.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `synthetic.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Commit.** `git commit -m "CRISM: synthetic cube and validation metrics"`.

### Task 9: Noise model

**Files:**
- Create: `src/varda/crism/noise.py`
- Test: `src/varda/crism/_tests/test_noise.py`

**Interfaces:**
- Consumes: `makeSyntheticCube`.
- Produces:
  - `NoiseModel` (attrs frozen):
    - fields: `floor: np.ndarray` (s, b) = a(λ, x), `shot: np.ndarray` (b,) = b(λ);
    - `def sigma(self, reflectance: np.ndarray) -> np.ndarray`: reflectance `(..., s, b)` gives σ of the same shape, `sqrt(max(a + b·R, 0))`.
  - `def estimateNoise(cube: np.ndarray, columnGroup: int = 16, quietFraction: float = 0.5) -> NoiseModel`:
    - along-track second differences d = R(t) − ½[R(t−1) + R(t+1)];
    - σ = 1.4826·MAD/√1.5;
    - pixels limited to those whose spatial-gradient magnitude is in the lowest `quietFraction`;
    - (a, b) fitted per band by weighted least squares on binned (R, σ²);
    - floor values shared within column groups.

- [ ] **Step 1: Write the failing tests** (N1).
  - `test_recovers_injected_sigma`: on a 128-line synthetic cube with spikes off, `median(model.sigma(truth.surface) / truth.noiseSigma)` is within 1.00 ± 0.03 over 1.0–2.6 µm.
  - `test_stripes_do_not_inflate_sigma`: with `stripeAmplitude=0.02`, the same ratio is still within 1.00 ± 0.03.
  - `test_shot_noise_slope`: the fitted `shot` is within 10% of the injected b.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `noise.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (N1), then commit: `git commit -m "CRISM: noise model from along-track differences"`.

### Task 10: Photometry

**Files:**
- Create: `src/varda/crism/photometry.py`
- Test: `src/varda/crism/_tests/test_photometry.py`

**Interfaces:**
- Produces:
  - `IncidenceFit` (attrs frozen):
    - fields: `coefficients: np.ndarray` (5,) for (1, x, x², t, t²), `rmsDeg: float`;
    - `def evaluate(self, lineIndex: np.ndarray, sampleIndex: np.ndarray) -> np.ndarray`.
  - `def fitIncidence(incidenceDeg: np.ndarray) -> IncidenceFit`: (l, s) with NaN; least squares on valid pixels; x and t in absolute indices.
  - `def lambert(cube: np.ndarray, sigma: np.ndarray, incidenceDeg: np.ndarray) -> tuple[np.ndarray, np.ndarray]`: divides both by cos i, broadcast over bands.

- [ ] **Step 1: Write the failing tests** (P1).
  - `test_polynomial_incidence`: a smooth i(x, t) = 50 + 0.01x + 2e-5x² + 0.02t, quantized to 0.25° steps, gives `fitIncidence(...).evaluate` within 0.02° of the smooth truth, and `rmsDeg` between 0.05 and 0.1.
  - `test_lambert`: `lambert(ones, sigmaOnes, full(60°))` gives 2.0 and 2.0.
  - `test_chunk_consistency`: evaluating on lines 32–63 with absolute indices equals rows 32–63 of the full evaluation.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `photometry.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (P1), then commit: `git commit -m "CRISM: Lambertian photometry with a polynomial incidence fit"`.

### Task 11: Despike

**Files:**
- Create: `src/varda/crism/despike.py`
- Test: `src/varda/crism/_tests/test_despike.py`

**Interfaces:**
- Consumes: `NoiseModel.sigma`.
- Produces:
  - `SpikeReport` (attrs frozen):
    - fields: `perPixel: np.ndarray` (l, s) uint16, `positions: np.ndarray` (n, 3) int32 with absolute line numbers, `leaskCount: np.ndarray` (l, s) uint16 (flags inside 1850–2150 nm);
    - `def merged(self, other: SpikeReport) -> SpikeReport` (for chunks).
  - `def despike(cube: np.ndarray, sigma: np.ndarray, wavelengths: np.ndarray, threshold: float = 5.0, firstLine: int = 0, halo: int = 1) -> tuple[np.ndarray, np.ndarray, SpikeReport]`:
    - `cube` includes `halo` lines above and below; the outputs exclude them;
    - `wavelengths` is the (s, b) per-column map;
    - the prediction combines the median of the 8 spatial neighbors' spectra, scaled to the pixel's level (the ratio of medians over the pixel's whole spectrum), with a 5-band spectral median;
    - σ_p is 1.4826·MAD of the 8 neighbor values at that band, after scaling;
    - a sample is flagged when |R − R̂| / √(σ² + σ_p²) > threshold and it is a strict local extremum in λ;
    - it is replaced by R̂ with σ set to σ_p.

- [ ] **Step 1: Write the failing tests** (K1).
  - `test_recall_and_false_rate`: on the synthetic cube (spikes at 1e-4, 5–20σ), recall is at least 0.95 for spikes of 8σ or more, and false flags are at most 1e-5 per non-spike sample.
  - `test_preserves_narrow_real_feature`: a real 3-band-wide absorption at 2.30 µm present in a 5×5 block is never flagged.
  - `test_chunk_halo_equivalence`: despiking lines 0–63 whole gives the same result as two 32-line chunks with a 1-line halo.
  - `test_leask_count`: spikes injected only at 2.0 µm are all counted in `leaskCount`.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `despike.py`** with vectorized NumPy. Use the `scipy.ndimage` median filter for the spatial neighbors, with the center excluded through a footprint.
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (K1), then commit: `git commit -m "CRISM: despike against the noise model"`.

### Task 12: Atmosphere, part A: column fit and transmission selection

**Files:**
- Create: `src/varda/crism/atmosphere.py`
- Test: `src/varda/crism/_tests/test_atmosphere.py`

**Interfaces:**
- Consumes: `Transmission` (from calibration), the synthetic cube.
- Produces:
  - Constants: `FIT_WINDOW_NM = (1850.0, 2200.0)`, `SCORE_WINDOW_NM = (1947.0, 2066.0)`, `CONTINUUM_DEGREE = 3`.
  - `ColumnFit` (attrs frozen): `beta: np.ndarray` (s,), `shiftNm: np.ndarray` (s,), `residualRms: np.ndarray` (s,) over the score window, `residuals: np.ndarray` (s, b) of ln R minus the model (NaN outside the fit window), `valid: np.ndarray` (s,) bool.
  - `def columnSpectra(cube: np.ndarray, sigma: np.ndarray) -> tuple[np.ndarray, np.ndarray]`: the per-column median over lines of (l, s, b), and the σ of the median (1.2533·σ/√n).
  - `def fitColumns(spectra: np.ndarray, sigma: np.ndarray, wavelengths: np.ndarray, transmission: np.ndarray, *, fitShift: bool = True, degree: int = CONTINUUM_DEGREE) -> ColumnFit`:
    - weighted linear least squares of ln R on [Legendre P0..P3 over the fit window, ln T, ∂ln T/∂λ];
    - ∂ln T/∂λ is the central difference along each column's sorted wavelengths;
    - shift δ = η/β.
  - `def smoothShift(shiftNm: np.ndarray, valid: np.ndarray, degree: int = 3) -> np.ndarray`: robust polynomial in x (iteratively reweighted, Tukey biweight).
  - `SelectionResult` (attrs frozen): `chosen: Transmission`, `scores: dict[str, float]` (scan ID to score), `margin: float` (runner-up minus best), `fit: ColumnFit` (refit with the smoothed shift held fixed).
  - `def selectTransmission(spectra: np.ndarray, sigma: np.ndarray, wavelengths: np.ndarray, candidates: Sequence[Transmission], *, fitShift: bool = True) -> SelectionResult`. The score is the median over valid columns of the σ-weighted residual RMS inside `SCORE_WINDOW_NM` (A3).

- [ ] **Step 1: Write the failing tests.**
  - `test_recovers_beta_and_shift` (A1, A4): on synthetic column spectra (128 lines, artifact off), the smoothed δ is within 3 predicted σ of `truth.shiftNm` in at least 99% of columns, and the column β is within 3σ of the column median of `truth.beta`.
  - `test_selects_injected_transmission` (A3): with the true AT plus 3 decoy ATs (copies shifted by 1.5 nm and scaled in depth by 0.9 and 1.1), the chosen scan is the true one and `margin > 0`.
  - `test_selection_requires_shift_fit` (A3, the M10 regression): with δ = 0.8 nm injected, `selectTransmission(..., fitShift=False)` on a candidate set built so the true scan loses without a shift term picks a decoy, while `fitShift=True` picks the truth.
  - `test_beta_sigma_below_two_wavelength` (A2): the std across pixels of the window fit's β is below that of McGuire's two-wavelength β (1980/2007 nm) on the same synthetic pixels. Put a reference `twoWavelengthBeta` in the test file, not the module.
- [ ] **Step 2: Run them.** `uv run pytest src/varda/crism/_tests/test_atmosphere.py -v` should FAIL.
- [ ] **Step 3: Implement the part A functions.** The per-column solve is batched with `np.linalg.lstsq` over columns, or with normal equations via `np.einsum`.
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (A1–A4), then commit: `git commit -m "CRISM: atmospheric fit per column and objective transmission selection"`.

### Task 13: Atmosphere, part B: per-pixel scale, elevation prior and correction

**Files:**
- Modify: `src/varda/crism/atmosphere.py`
- Test: `src/varda/crism/_tests/test_atmosphere.py` (append)

**Interfaces:**
- Consumes: `SelectionResult`, `ColumnFit`, `DdrBand.ELEVATION`.
- Produces:
  - `ElevationModel` (attrs frozen):
    - fields: `lnBeta0: float`, `scaleHeightKm: float`, `referenceElevationM: float`, `scatter: float` (robust std of ln β about the model), `accepted: bool`;
    - `def prior(self, elevationM: np.ndarray) -> tuple[np.ndarray, np.ndarray]` returns the mean β and σ.
  - `AtmosphereModel` (attrs frozen): `transmission: Transmission`, `shiftNm: np.ndarray` (s,), `artifact: np.ndarray | None` ((s, b), Task 14), `elevation: ElevationModel | None`.
  - `PixelFit` (attrs frozen): `beta`, `betaSigma`, `artifactAmplitude`, `residualRms`, each (l, s) float64.
  - `def fitPixels(cube: np.ndarray, sigma: np.ndarray, wavelengths: np.ndarray, model: AtmosphereModel, elevationM: np.ndarray | None = None) -> PixelFit`:
    - the design per column is [P0..P3, ln T′ (shifted), artifact (if any)];
    - with an elevation prior, β has a Gaussian prior from `model.elevation.prior` (augmented least squares);
    - one precomputed solve per column applies to all lines.
  - `def fitElevationModel(fit: PixelFit, elevationM: np.ndarray) -> ElevationModel`: robust line of ln β against z; `accepted` is `5 <= H <= 20` (A5).
  - `def correct(cube: np.ndarray, sigma: np.ndarray, wavelengths: np.ndarray, model: AtmosphereModel, fit: PixelFit) -> tuple[np.ndarray, np.ndarray]`:
    - R′ = R / exp(β ln T′ + α·artifact) over all bands;
    - σ′ = R′ · sqrt(σ²/R² + (ln T′)² σ_β²). The β–data covariance is neglected, the neglect is recorded, and its size is checked in the test below.

- [ ] **Step 1: Write the failing tests.**
  - `test_pixel_beta_recovery`: synthetic data with the elevation prior on gives β z-scores ((fit − truth)/betaSigma) with std 1.00 ± 0.10 over valid pixels.
  - `test_elevation_model_recovers_H` (A5): `scaleHeightKm` is within 3σ of 11 and `accepted` is True. With β made independent of z, `accepted` is False (H is out of range or poorly determined).
  - `test_beta_regularization_resists_h2o_band` (A5): pixels in the hydrated (1.9 µm) unit have mean β bias below 1σ_β with the prior, and above 2σ_β without it.
  - `test_correct_sigma_chi2`: after `correct`, the reduced χ² of (R′ − truth.surface·stripes) against σ′ over 1.0–2.6 µm is 1.00 ± 0.05 (spikes and artifact off).
  - `test_co2_window_flat_after_correction`: `validation.co2Residual` (Task 8) of the corrected column medians is at most 1.5× its value on the noise-only truth.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement the part B functions.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (A5), then commit: `git commit -m "CRISM: per-pixel atmospheric scale with an elevation prior; correction with sigma"`.

### Task 14: Atmosphere, part C: residual (artifact) model

**Files:**
- Modify: `src/varda/crism/atmosphere.py`
- Create: `src/varda/crism/resources/artifacts/` (learned bases, written in Task 23 from real data)
- Test: `src/varda/crism/_tests/test_atmosphere.py` (append)

**Interfaces:**
- Produces:
  - `ArtifactBasis` (attrs frozen): `scanId: str`, `spectra: np.ndarray` ((s, b), high-passed, unit RMS over the score window), `sourceObservations: tuple[str, ...]`, `createdWith: str` (Varda version).
  - `def highPass(residuals: np.ndarray, wavelengths: np.ndarray, degree: int = CONTINUUM_DEGREE) -> np.ndarray`: projects out the Legendre continuum over the fit window, per column.
  - `def learnArtifactBasis(columnResiduals: Sequence[np.ndarray], scanId: str, observations: Sequence[str], smoothColumns: int = 31) -> ArtifactBasis`:
    - the median across observations of the high-passed column residuals;
    - then a running median across columns (`smoothColumns`);
    - normalized.
  - `def saveArtifactBasis(basis: ArtifactBasis, path: Path) -> None` and `def loadArtifactBasis(path: Path) -> ArtifactBasis` (`.npz` plus a JSON metadata entry).
  - `def bundledArtifactBasis(scanId: str, samples: int) -> ArtifactBasis | None`: from `resources/artifacts/<scanId>.npz`; None when absent or when the column count differs.

- [ ] **Step 1: Write the failing tests** (A6).
  - `test_artifact_basis_recovered`: across 4 synthetic observations (different seeds and surfaces, the same injected artifact), the learned basis correlates at least 0.95 with `truth.artifact` per column (median).
  - `test_artifact_model_preserves_mineral_band`: with the basis applied through `fitPixels` and `correct`, the BD2290 and BD1900_2 of the hydrated and smectite units change by less than 1σ against an artifact-free truth run.
  - `test_artifact_reduces_co2_residual`: with the artifact injected, the M7 column-median metric without the model is more than 2× the metric with it.
  - `test_artifact_basis_leave_one_out` (data-gated, real FRS + FRT TRR3; filled in Task 23): a basis learned without observation k reduces k's residual by at least 30% against no basis.
  - `test_round_trip_npz`: save then load gives equal fields.
- [ ] **Step 2: Run them.** They should FAIL (the data-gated test skips).
- [ ] **Step 3: Implement the part C functions.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (A6, A7), then commit: `git commit -m "CRISM: cross-observation residual model for volcano-scan artifacts"`.

### Task 15: Destripe

**Files:**
- Create: `src/varda/crism/destripe.py`
- Test: `src/varda/crism/_tests/test_destripe.py`

**Interfaces:**
- Produces:
  - `StripeFrame` (attrs frozen): `factors: np.ndarray` ((s, b), multiplicative, applied as division), `sigma: np.ndarray` (s, b), `highFrequency: np.ndarray` (s, b), `smooth: np.ndarray` (s, b), `smoothAccepted: np.ndarray` (s,) bool.
  - `def blandMask(cube: np.ndarray, wavelengths: np.ndarray) -> np.ndarray`: (l, s) bool. Pixels in the lowest 50% of continuum-removed spectral variance over 1.0–2.6 µm, with no band depth above 3σ at 1.9, 2.3 and 2.5 µm.
  - `def estimateStripes(cube: np.ndarray, sigma: np.ndarray, wavelengths: np.ndarray, bland: np.ndarray, *, legendreDegree: int = 5, splitHalf: bool = True) -> StripeFrame`:
    - `cube` may be a `(l, s, b)` memmap, processed band by band;
    - the cross-track model is a LAD Legendre fit of degree 5, through IRLS (L1);
    - ±1-column ratio accumulation in ±x, with the 4 profiles averaged;
    - ln f is split into a smooth-in-λ part (a per-column polynomial of degree 3 in λ) and a high-frequency part;
    - the smooth part is accepted per column when the two line halves agree within 2σ;
    - σ is the bootstrap over lines (50 resamples).
  - `def applyStripes(cube: np.ndarray, sigma: np.ndarray, frame: StripeFrame) -> tuple[np.ndarray, np.ndarray]`: σ′² = (σ/f)² + (R·σ_f/f²)².

- [ ] **Step 1: Write the failing tests** (S1, S2).
  - `test_removes_injected_stripes`: on synthetic data after a truth-based atmosphere removal, the stripe RMS of the corrected cube (column medians ÷ truth) is at most 0.1× the injected 0.5%.
  - `test_preserves_column_aligned_unit`: the carbonate unit's 2.3 µm band depth after correction is at least 95% of truth.
  - `test_smooth_part_needs_split_half`: a smooth column offset present only in the first half of the lines is not removed (`smoothAccepted` False for that column).
  - `test_sigma_includes_frame_uncertainty`: σ′ is at least σ everywhere.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `destripe.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (S1, S2), then commit: `git commit -m "CRISM: destripe that protects column-aligned geology"`.

### Task 16: Smile and the MTRDR vector

**Files:**
- Create: `src/varda/crism/smile.py`
- Test: `src/varda/crism/_tests/test_smile.py`

**Interfaces:**
- Produces:
  - `StandardVector` (attrs frozen): `wavelengths: np.ndarray` (411,), `fwhm: np.ndarray` (411,), `detectorRows: np.ndarray` (411,), `bbl: np.ndarray` (411,) uint8, with 0 above 2600 nm.
  - `def standardVector() -> StandardVector`: the IR rows of `resources/mtrdr_wavelengths.csv`, ascending.
  - `ResampleWeights` (attrs frozen): `indices: np.ndarray` (s, n, 2) int, `weights: np.ndarray` (s, n, 2) float64.
  - `def linearWeights(columnWavelengths: np.ndarray, target: np.ndarray) -> ResampleWeights`: NaN wavelengths are skipped, and a target outside a column's valid range gets zero weights (the output is NaN).
  - `def resample(cube: np.ndarray, sigma: np.ndarray, weights: ResampleWeights) -> tuple[np.ndarray, np.ndarray]`: σ² = Σw²σ².
  - `EscFrame` (attrs frozen): `correction: np.ndarray` (s, n).
  - `def estimateEsc(columnSpectra: np.ndarray, columnOffsetNm: np.ndarray, binNm: float) -> EscFrame`:
    - per band, continuum-normalized column spectra are binned by the column's wavelength offset (`binNm`: 0.125 for FRT/FRS, 0.25 for HRL/HRS);
    - a weighted linear fit gives the model; the correction is model(offset) / model(0).
  - `def applyEsc(cube: np.ndarray, sigma: np.ndarray, frame: EscFrame) -> tuple[np.ndarray, np.ndarray]`.

- [ ] **Step 1: Write the failing tests** (M1, M2).
  - `test_target_vector_matches_mtrdr`: there are 411 bands; the first is 1047.20 nm and the last 3896.76 nm; detector rows 173–193 are absent; and the vector equals the IR wavelengths in the crop MTRDR `.hdr`.
  - `test_resampling_error_and_sigma`: smooth synthetic spectra (no noise) resampled from the per-column WA have RMS error against truth on the target of at most 0.2 × the SNR-100 σ. With noise, the empirical σ of the output matches the propagated σ within 3%.
  - `test_outside_range_is_nan`: a column with no valid WA beyond 2600 nm gives NaN there.
  - `test_esc_removes_column_slope`: an injected multiplicative error linear in column wavelength offset (0.5% per nm) is reduced 10×.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `smile.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (M1, M2), then commit: `git commit -m "CRISM: smile resampling to the MTRDR vector, ESC residual"`.

### Task 17: Projection

**Files:**
- Create: `src/varda/crism/projection.py`
- Test: `src/varda/crism/_tests/test_projection.py`

**Interfaces:**
- Produces:
  - `IAU2000_A = 3396190.0`, `IAU2000_B = 3376200.0`.
  - `def referenceLatitude(centerLatDeg: float) -> float`: the equatorward multiple of 5°; ±90 beyond ±65.
  - `def sphereRadius(latDeg: float) -> float`: ab / √((b cos φ)² + (a sin φ)²).
  - `MapGrid` (attrs frozen):
    - fields: `kind: Literal["equirectangular", "polar"]`, `referenceLatDeg: float`, `radius: float`, `pixelSize: float`, `originX: float`, `originY: float` (the UL corner), `width: int`, `height: int`;
    - `def transform(self) -> Affine`;
    - `def crsWkt(self) -> str`, MTRDR style:
      - the `PROJCS["MRO Mars Equirectangular [IAU 2000] [15.00N; 0.00E]", …]` string as in the FRT00005850 header;
      - the polar case is analogous;
    - `def enviMapInfo(self) -> str` (the exact header format, `units=Meters`).
  - `def mapCoordinates(grid: MapGrid, latDeg: np.ndarray, lonDeg: np.ndarray) -> tuple[np.ndarray, np.ndarray]`: x = R cos φ_P · λ, y = R φ, with longitudes wrapped into (−180°, 180°]; polar stereographic for the polar kind.
  - `def mtrdrGrid(latDeg: np.ndarray, lonDeg: np.ndarray, pixelSize: float, snapToLattice: bool = False) -> MapGrid`:
    - φ_P comes from the median latitude;
    - tie = (x_min − s/2, y_max + s/2), rounded to 0.01 m; with `snapToLattice`, floor and ceil to multiples of s (G1, G2).
  - `Glt` (attrs frozen): `sourceLine: np.ndarray` (H, W) int32, −1 where empty; `sourceSample` likewise.
  - `def nearestNeighborGlt(grid: MapGrid, latDeg: np.ndarray, lonDeg: np.ndarray, maxDistanceFactor: float = 0.75 * np.sqrt(2)) -> Glt`: a `cKDTree` on the projected DDR centers.
  - `def applyGlt(cube: np.ndarray, glt: Glt) -> np.ndarray`: (l, s, b) gives (H, W, b), NaN where empty.
  - `AreaWeights` (attrs frozen): `matrix: scipy.sparse.csr_matrix` ((H·W) × (l·s)), `looks: np.ndarray` (H, W).
  - `def areaWeights(grid: MapGrid, latDeg: np.ndarray, lonDeg: np.ndarray, subsamples: int = 4) -> AreaWeights`: each source quad (corners at neighbor midpoints) is sampled at 4×4 points; the weight is the fraction of points per output pixel.
  - `def applyAreaWeights(cube: np.ndarray, sigma: np.ndarray, weights: AreaWeights) -> tuple[np.ndarray, np.ndarray]`: σ² = Σw²σ² / (Σw)².

- [ ] **Step 1: Write the failing tests.**
  - `test_radius_table` (G1): `sphereRadius` at 0, 5, 15, 30 and 60 equals 3396190.0, 3396036.8126024301, 3394839.8133163122, 3391159.2284034425 and 3381164.3916799151 (from `mtr_map.cat`), to 1e-6.
  - `test_grid_matches_mtrdr_labels` (G1, G2, data-gated): `mtrdrGrid` from the MTRDR DE lat/lon of FRT00005850 gives `referenceLatDeg == 15`, `radius == 3394839.813316312`, origin (4417887.6, 1091944.9) to 0.05 m, and `enviMapInfo()` equal to the header's `map info` apart from whitespace. The same holds for HRL000040FF with `pixelSize=36`.
  - `test_tie_point_rule` (G2): a synthetic lat/lon block has tie = x(W) − s/2 and y(N) + s/2.
  - `test_reference_latitude_and_wrap` (Review Focus 3):
    - `referenceLatitude(-18.3) == -15`, `referenceLatitude(66) == 90`;
    - a lon block straddling 179.9° → −179.9° projects to contiguous x (max step ≤ 2s);
    - a block at lon 359.9° → 0.1° behaves the same;
    - a polar block at 80° N gives `kind == "polar"`.
  - `test_backplanes_trace_to_sensor` (G3): for a synthetic swath, every non-empty GLT pixel's source pixel center is within 0.75·s·√2 of the output pixel center.
  - `test_area_weights_conserve_mean`: a constant cube stays constant; the σ of a 2×2 downsample is σ/2.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `projection.py`.**
  - Build the WKT strings from templates matching the MTRDR header text.
  - Use pyproj only in tests, to cross-check `mapCoordinates` against `pyproj.Transformer` from the WKT.
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Update the ledger** (G1–G3), then commit: `git commit -m "CRISM: MTRDR grid, nearest-neighbor GLT with backplanes, area weighting"`.

### Task 18: Writers

**Files:**
- Create: `src/varda/crism/writers.py`
- Test: `src/varda/crism/_tests/test_writers.py`

**Interfaces:**
- Consumes: `MapGrid`, `StandardVector`.
- Produces:
  - `EnviBands` (attrs frozen): `names: tuple[str, ...]`, `wavelengths: np.ndarray | None`, `fwhm: np.ndarray | None`, `bbl: np.ndarray | None`, `defaultBands: tuple[int, int, int] | None`.
  - `class EnviCubeWriter`, a context manager:
    - `__init__(self, path: Path, lines: int, samples: int, bands: EnviBands, grid: MapGrid | None, description: str, extraHeader: Mapping[str, str])`;
    - `write(self, firstLine: int, block: np.ndarray) -> None`: block `(l, s, b)`; NaN is written as 65535; BSQ float32 through a memmap of the `.part` file;
    - on a clean exit it writes the `.hdr` and renames `.img.part` to `.img`; on an exception it deletes the part files.
  - `def historyString(steps: Sequence[str]) -> str`: joins with "_", e.g. `"PHT(areoid-poly)_DSK(5.0)_ATM(fit;08608;shift;elev;resid1)_RSC(hf)_ESC(linear)_MAP(eqc;18m;nn)"`.
  - `def writeSidecars(stem: Path, recipe: Mapping[str, object], provenance: Mapping[str, object], diagnostics: Mapping[str, np.ndarray]) -> tuple[Path, Path, Path]`: writes `.recipe.json`, `.provenance.json` and `.diagnostics.npz`.
  - `def sha256File(path: Path) -> str`.
  - `def writeCog(enviImage: Path, target: Path) -> Path`: through rasterio's COG driver, under `PROJ_LOCK`.

- [ ] **Step 1: Write the failing tests.**
  - `test_header_matches_mtrdr_style`: write a 3×4×2 cube with a grid from Task 17. The header has `data ignore value = 65535`, `interleave = bsq`, `data type = 4`, `byte order = 0`, a `map info` equal to `grid.enviMapInfo()`, a `coordinate system string` that is `grid.crsWkt()`, and the `wavelength`, `fwhm` and `bbl` lists.
  - `test_nan_written_as_nodata`: reading back with `np.fromfile` gives 65535 where NaN was written, and none of the values are NaN or inf.
  - `test_failed_write_leaves_previous_product` (Review Focus 4): an existing product survives byte-for-byte when an exception is raised inside the writer context, and no `.part` file remains.
  - `test_opens_in_varda`: `openDataSource(written.img)` gives the same values, wavelengths and transform.
  - `test_sidecars_json`: the recipe JSON round-trips through `json.loads`, and the `.npz` arrays are present.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `writers.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Commit.** `git commit -m "CRISM: MTRDR-style ENVI writer, sidecars, COG"`.

### Task 19: Recipe

The `validation.py` interfaces and tests below are built in Task 8; they are listed here beside the recipe because the two are the pipeline's configuration and its yardstick.

**Files:**
- Create: `src/varda/crism/recipe.py`
- Test: `src/varda/crism/_tests/test_recipe.py`

**Interfaces:**
- Consumes: `common.parameter` (ParameterGroup, BoolParameter, FloatParameter, IntParameter, StringParameter, EnumParameter, LabeledEnum); `TransmissionSource`.
- Produces in `recipe.py`:
  - `RECIPE_VERSION = 1`.
  - Enums (`LabeledEnum`):
    - `IncidenceSource`: `AREOID_POLYNOMIAL = "Areoid incidence, polynomial fit"`, `MOLA = "MOLA surface incidence"`;
    - `BetaMode`: `ELEVATION_PRIOR = "Per pixel, with an elevation prior"`, `PER_PIXEL = "Per pixel"`;
    - `SmileMethod`: `LINEAR = "Linear"`, `CUBIC = "Cubic spline"`;
    - `Resampling`: `NEAREST = "Nearest neighbor"`, `AREA = "Area weighted"`.
  - Groups, all with an `enabled: BoolParameter` defaulting True unless noted:
    - `PhotometryStep`: `incidence`;
    - `DespikeStep`: `threshold` (FloatParameter 5.0, range 3–10);
    - `AtmosphereStep`: `source` (TransmissionSource, default `PDS_AT`), `scan` (StringParameter, "" means automatic), `fitShift` (True), `betaMode`, `artifactModel` (True), `continuumDegree` (IntParameter 3, range 1–5);
    - `DestripeStep`: `splitHalf` (True);
    - `SmileStep`: `method`, `esc` (True), `applyFittedShift` (False);
    - `ProjectionStep`: `pixelSize` (FloatParameter 0.0 = automatic 18/36), `resampling`, `snapToLattice` (False);
    - `OutputStep`: `sensorSpace` (False), `cog` (False).
  - `class Recipe(ParameterGroup)` nests `photometry`, `despike`, `atmosphere`, `destripe`, `smile`, `projection` and `output`.
  - `def recipeToDict(recipe: Recipe) -> dict[str, object]` (it includes `recipeVersion`), and `def recipeFromDict(data: Mapping[str, object]) -> Recipe`, which raises `ValueError` on a newer version or an unknown key.
- Produces in `validation.py`:
  - `def co2Residual(wavelengths: np.ndarray, spectra: np.ndarray) -> np.ndarray` (V1, the M7 metric): spectra `(..., b)` give the RMS of spectrum ÷ quadratic fit over 1940–2100 nm, after resampling to 25 points.
  - `def sceneResidualRms(wavelengths: np.ndarray, spectra: np.ndarray) -> float`: the RMS of the median residual spectrum.
  - `def shapeRatio(ours: np.ndarray, reference: np.ndarray, wavelengths: np.ndarray) -> np.ndarray`: per band, the median of (ours/mean₁₃₀₀–₁₇₀₀)/(ref/mean₁₃₀₀–₁₇₀₀).
  - `def bd2310(wavelengths: np.ndarray, cube: np.ndarray) -> np.ndarray` (V2; the prototype's "BD2300": 1 − R2310/continuum(2160, 2400), 5-band means).
  - `def compareMaps(ours: np.ndarray, reference: np.ndarray) -> MapComparison`, where `MapComparison` (attrs frozen) has `pearson: float`, `medianBias: float`, `n: int`.
  - `def geolocationOffset(ours: np.ndarray, reference: np.ndarray) -> tuple[float, float]`: (dx, dy) in pixels, by phase correlation through `varda.mosaicking.matching`.

- [ ] **Step 1: Write the failing tests.**
  - `test_recipe_round_trip`: `recipeFromDict(recipeToDict(r))` gives equal values after changing three parameters.
  - `test_recipe_rejects_newer_version`: version 2 raises `ValueError`.
  - *(Task 8, in `test_validation.py`)* `test_co2_metric_on_synthetic` (V1): a flat spectrum scores below 1e-6; a spectrum with a 1% CO2-shaped residual scores between 0.004 and 0.01.
  - *(Task 8, in `test_validation.py`)* `test_bd2310_matches_prototype` (V2): it equals a reference copy of `stage2b_diag.py:55-58`, embedded in the test with an attribution comment, on random cubes.
  - *(Task 8, in `test_validation.py`)* `test_co2_metric_reproduces_m7` (data-gated): the FRT00005850 MTRDR per-pixel median on a 2× subsample is 0.0082 ± 0.0003.
  - *(Task 8, in `test_validation.py`)* `test_geolocation_offset`: a cube shifted by (1.5, −0.5) px is recovered within 0.05 px.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `recipe.py`.**
- [ ] **Step 4: Run them again.** They should PASS.
- [ ] **Step 5: Commit.** `git commit -m "CRISM: recipe"`.

### Task 20: Pipeline

**Files:**
- Create: `src/varda/crism/pipeline.py`
- Modify: `src/varda/crism/__init__.py` (export `CrismObservation`, `Recipe`, `process`, `ProcessResult`, `CalibrationLibrary`)
- Test: `src/varda/crism/_tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces:
  - `CHUNK_LINES = 64`, `SELECTION_LINES = 64`.
  - `ProgressCallback = Callable[[float, str], None]`, taking a fraction 0–1 and a message.
  - `ProcessResult` (attrs frozen): `mapProjected: Path | None`, `mapSigma: Path | None`, `geometry: Path | None` (DE), `info: Path | None` (IN), `sensorSpace: Path | None`, `recipe: Path`, `provenance: Path`, `diagnostics: Path`.
  - `def process(observation: CrismObservation, recipe: Recipe, outputFolder: Path, *, library: CalibrationLibrary, progress: ProgressCallback | None = None, cancelled: Callable[[], bool] | None = None) -> ProcessResult`. It runs the spec's passes:
    - **Pass A:** noise, then transmission selection on the central `SELECTION_LINES`, the shift and the elevation model.
    - **Pass B:** per chunk with a 1-line halo, the invalid mask (R3), `usableLines`, `library.badPixels`, then photometry, despike and the atmosphere. Output goes to a `.work/` float32 memmap plus σ.
    - **Pass C:** destripe estimated from the work memmap, then per chunk destripe, resample and ESC. Writes the sensor-space products when requested.
    - **Bands:** the output vector is `standardVector()`; `fwhm` comes from it; `bbl` is its own (0 above 2600 nm) and also 0 for any band with under 50% valid samples in the scene (S7).
    - **Pass D:** the GLT or area weights, then the projected IF, σ, DE (DDR bands through the same GLT) and IN (source sample, source line, β, σ_β, ATM residual RMS, artifact amplitude, spike count, Leask count, looks).
    - **Afterwards:** the sidecars; the provenance holds every input and calibration SHA-256, the score table, the per-step diagnostics and wall times. `.work/` is removed.
    - `cancelled()` is checked between chunks and raises `ProcessCancelled` (a new exception in this module).
    - `library.ensure(trr3)` runs first.

- [ ] **Step 1: Write the failing tests.**
  - `test_crop_end_to_end` (uses `tmp_path`, the crop observation, a library over `cropDir`, `Recipe()` defaults): every `ProcessResult` path exists; the IF has 411 bands; `openDataSource(result.mapProjected)` has a CRS whose WKT contains "Equidistant_Cylindrical" and `Standard_Parallel_1",15`; the provenance JSON lists the TRR3 SHA-256 and the chosen scan ID.
  - `test_invalid_regions_become_nodata` (Review Focus 2): a crop copy with 3 columns and 2 lines set to 65535 and one band all 65535 completes, those pixels are 65535 in IF and σ, and no output sample is NaN or inf.
  - `test_sigma_chi2_synthetic` (N2, V1): `process` on a synthetic observation (written to `tmp_path` as TRR3/DDR files through a test helper `writeSyntheticObservation` added to `synthetic.py`) gives a reduced χ² of the sensor-space product against truth (resampled to the target vector) of 1.00 ± 0.05 per band group.
  - `test_chunking_invariance`: `CHUNK_LINES` set to 7 (by monkeypatch) and 64 give identical outputs to 1e-6.
  - `test_recipe_toggles`: with every step disabled, the sensor-space output equals the TRR3 resampled only; disabling just the projection gives `mapProjected is None`.
  - `test_cancel`: `cancelled` returning True after the first chunk raises `ProcessCancelled` and leaves no outputs or `.work/`.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `pipeline.py`.**
- [ ] **Step 4: Run them again,** then the whole `src/varda/crism` suite: `uv run pytest src/varda/crism -q` should PASS in under 60 s.
- [ ] **Step 5: Commit.** `git commit -m "CRISM: four-pass pipeline with progress, cancel and provenance"`.

### Task 21: CLI

**Files:**
- Create: `src/varda/crism/cli.py`, `src/varda/crism/__main__.py`
- Modify: `src/varda/main.py`. Add `CRISM_FLAG = "--crism"`; in `main()`, before the QApplication is created, `if CRISM_FLAG in sys.argv: sys.exit(crismMain(sys.argv[sys.argv.index(CRISM_FLAG) + 1:]))`, mirroring `--check-plugin`.
- Test: `src/varda/crism/_tests/test_cli.py`

**Interfaces:**
- Produces: `def crismMain(argv: Sequence[str]) -> int` with argparse subcommands:
  - `process <folder-or-files...> [--recipe r.json] [--out DIR] [--sensor-space] [--cog] [--cache DIR] [--cat-aux DIR]` processes each observation and prints one line per product;
  - `calibration <folder> [--fetch]` prints `CalibrationFileStatus` rows and fetches with `--fetch`;
  - `learn-artifacts <folder...> --out DIR` processes the atmosphere only, then `learnArtifactBasis` per scan and `saveArtifactBasis`;
  - `validate <ours.img> <mtrdr.img> [--report DIR]` writes `report.json` plus figures: before/after spectra, CO2 residual by column, and maps of ours, the MTRDR and their difference for D2300, BD2290, BD1900_2, OLINDEX3 and BD2310 (matplotlib, Agg).
  - The default cache is `appdirs.user_data_dir("Varda") / "crism"`.

- [ ] **Step 1: Write the failing tests.**
  - `test_cli_process_crop`: `crismMain(["process", str(cropObsDir), "--out", str(tmp_path)])` returns 0 and the `vmtr` `.img` exists.
  - `test_cli_calibration_lists_missing`: an empty cache with no network (monkeypatch `downloadTo` to raise) returns 2 and prints "missing" with the WA name.
  - `test_cli_bad_recipe`: a recipe file with an unknown key returns 2 with the key named.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement `cli.py`, `__main__.py` and the `main.py` dispatch.**
- [ ] **Step 4: Run them again.** They should PASS. Then `uv run python -m varda.crism --help` should list the 4 subcommands.
- [ ] **Step 5: Commit.** `git commit -m "CRISM: command line (python -m varda.crism, Varda --crism)"`.

### Task 22: GUI dialog and menu

**Files:**
- Create: `src/varda/crism_ui/__init__.py`, `src/varda/crism_ui/process_dialog.py`, `src/varda/crism_ui/calibration_dialog.py`
- Modify: `src/varda/_actions/_processing_actions.py`. Add a "CRISM" category submenu through `submenuItem(MenuId.PROCESSING, "CRISM", order)` and two actions:
  - `CRISM_PROCESS_ACTION_ID = "varda.processing.crism_process"`, "Process CRISM Observation…";
  - `CRISM_CALIBRATION_ACTION_ID = "varda.processing.crism_calibration"`, "Get CRISM Calibration Files…".
- Test: `src/varda/crism_ui/_tests/test_process_dialog.py`

**Interfaces:**
- Consumes: `process`, `Recipe`, `CalibrationLibrary`, `CrismObservation`; `groupWidget`; `runInBackground`; `onMainThread`; `ImageLoadingService.load_images`.
- Produces:
  - `class ProcessCrismDialog(QDialog)`:
    - an observation list (an Add Folder… button, removable rows, a per-row status), one collapsible `groupWidget` per recipe step, an output folder picker, Load/Save Recipe buttons, Run and Cancel, and a progress bar plus label;
    - `sigProcessed = pyqtSignal(object)` emits each `ProcessResult` (the menu callback loads `mapProjected` into the project);
    - the batch runs sequentially in one `runInBackground` task;
    - a failure on one observation marks its row failed with the message, and the batch continues;
    - Cancel sets a flag read by `cancelled`.
  - `class CalibrationDialog(QDialog)`: for the chosen observation folder it shows a table of `CalibrationFileStatus` rows and a Fetch Missing button, plus a CAT aux folder setting (QSettings key `crism/catAuxFolder`). The library's cache comes from `QStandardPaths.AppDataLocation / "crism"`.

- [ ] **Step 1: Write the failing tests** (pytest-qt; `process` is monkeypatched to a fake that sleeps 0 and returns a fixed result, or raises for one observation).
  - `test_batch_continues_after_failure`: 3 observations, the second raising, give rows marked done, failed and done, and `sigProcessed` emits twice.
  - `test_cancel_stops_batch`: cancelling after the first observation leaves the remaining rows "canceled".
  - `test_recipe_save_load`: the dialog's saved recipe JSON loads back into equal parameter values.
  - `test_menu_actions_registered`: both action IDs are in `PROCESSING_ACTIONS` and have the `crism` category menu.
- [ ] **Step 2: Run them.** They should FAIL.
- [ ] **Step 3: Implement the dialogs and the actions.**
- [ ] **Step 4: Run them again,** then `uv run pytest src/varda/_actions -q`; both should PASS.
- [ ] **Step 5: Smoke-test the app.** Run `uv run varda`, open Processing > CRISM > Process CRISM Observation…, add `varda-demo-data/data-for-paper/FRS00031442`, and Run. The product opens in a workspace, and its spectrum at a pixel shows no 2 µm CO2 triplet.
- [ ] **Step 6: Commit.** `git commit -m "CRISM: Process CRISM Observation dialog and calibration dialog"`.

### Task 23: Real-data runs, learned artifact basis and validation (Phase 1 definition of done)

**Files:**
- Create: `src/varda/crism/_tests/test_regression.py` (data-gated)
- Create (generated): `src/varda/crism/resources/artifacts/<scanId>.npz`, for the scans chosen on the 5 TRR3s
- Create: `docs/superpowers/specs/crism/phase1-validation.md`, `docs/superpowers/specs/crism/figures/*.png`
- Modify: `docs/superpowers/specs/crism/evidence-ledger.md` (results; A6's leave-one-out numbers)

**Interfaces:**
- Consumes: the CLI subcommands; the `validation` metrics; the fusion prototype's `fusion_core.py` (ported locally in the test, with attribution) for V3's gain and repeatability.

- [ ] **Step 1: Learn the artifact basis.**
  ```bash
  uv run python -m varda.crism learn-artifacts $DEMO/FRS00031442 $DEMO/FRS0002FE75 $DEMO/FRS00031260 $DEMO/FRT00005850 --out src/varda/crism/resources/artifacts
  ```
  Expected: one `.npz` per chosen scan, and a printed leave-one-out table in which every observation's residual drops by at least 30%. If one doesn't, record it in the ledger (A6) and set the default `artifactModel` to False before continuing.
- [ ] **Step 2: Write `test_regression.py`,** encoding the spec's V2 and V3 tolerances exactly:
  - `test_frt5850_grid_identical`: same φ_P, R and s; tie within 0.01 m.
  - `test_frt5850_geolocation`: median offset ≤ 0.25 px.
  - `test_frt5850_shape_ratio`: within ±1% over 1.05–2.6 µm outside 1.94–2.10 µm, and ±2% inside.
  - `test_frt5850_co2_not_worse`: ours ≤ the MTRDR's (0.0082).
  - `test_frt5850_parameter_maps`: Pearson ≥ 0.90 for D2300, BD2290, BD1900_2, OLINDEX3 and BD2310; median bias ≤ 0.0014 (BD2310) and 0.0016 (BD1900_2), and for the others ≤ the crater-floor repeatability measured in-test from the five MTRDRs with the same code.
  - `test_frs_co2_residual[FRS00031442|FRS0002FE75|FRS00031260]`: per-pixel median ≤ 0.0083 and scene residual RMS ≤ 0.0080.
  - `test_frs31442_overlap_offsets`: the shape χ² and the BD2310 median offset against the overlapping crater-floor MTRDRs are ≤ the maximum MTRDR–MTRDR value computed in-test (≤ 0.004 for BD2310).
  - `test_artifact_basis_leave_one_out` (from Task 14), now filled in.
- [ ] **Step 3: Run them.** `VARDA_DEMO_DATA=... uv run pytest src/varda/crism/_tests/test_regression.py -v`. Expected: PASS, or, for a failure, a measured cause that is either fixed in the owning task's module (with a regression test there) or, if it lies outside Phase 1 (EGN), recorded in `phase1-validation.md` together with the tolerance decision for Michael. Tolerances are never loosened silently.
- [ ] **Step 4: Write the report.**
  - Run `python -m varda.crism validate` for FRT00005850 (and HRL000040FF if time allows) and the FRS CO2 comparisons. This produces the figures.
  - Write `phase1-validation.md`: a table of every V1–V3 number against its tolerance, where each difference comes from (step toggles), and a CAT comparison of our scan choice against `obs_info/crism_obs_info.txt` for the same observations.
- [ ] **Step 5: Commit.** `git add` the test, the artifact `.npz` files, the report, the figures and the ledger; then `git commit -m "CRISM: Phase 1 validation against MTRDR and on the rim FRSs"`.

### Task 24: Documentation, rebase and PR

**Files:**
- Create: `docs/user-guide/crism-processing.md` (what it does, the dialog, the CLI, the outputs and their bands, calibration files and the CAT aux option, σ and provenance, limits: no EGN or VNIR yet)
- Modify: `docs/user-guide/README.md` (one line), `CLAUDE.md` (one "Important" bullet for `crism/` + `crism_ui/`), `CHANGELOG.md` (`## Unreleased` → `### Added`), `THIRD_PARTY_NOTICES` (jezero-rim-mapping MIT pieces, if any were adapted; the PDS data tables)
- Modify: the spec's status line (Phase 1 built, with a link to the validation report)

- [ ] **Step 1: Write the docs.** Ledger entries are already updated.
- [ ] **Step 2: Rebase.** Run `git fetch origin && git rebase origin/main`. If `uv.lock` conflicts, run `uv lock` and keep both CHANGELOG entries. Log the rebase in `VARDA_COORDINATION.md`.
- [ ] **Step 3: Run the full checks.**
  - `uv run ruff format src/varda/crism src/varda/crism_ui && uv run ruff check src/varda/crism src/varda/crism_ui`: clean.
  - `uv run ty check src/varda/crism src/varda/crism_ui`: no errors.
  - `uv run lint-imports`: contracts kept.
  - `uv run pytest -q`: all pass.
- [ ] **Step 4: Bundle check.** `uv run pyinstaller Varda.spec && dist/Varda/Varda --self-test` should pass, with `pvl` and `crism/resources` included (add them to `Varda.spec` datas if missing).
- [ ] **Step 5: Push and open the PR.**
  - `git push -u origin feature/crism-processing`.
  - Open "CRISM processing, Phase 1: TRR3 IR → MTRDR-consistent products" with `gh pr create`. The body holds the validation table, the figures (before/after spectra, CO2 residual by column, the MTRDR comparison maps) and the ledger entries added in Phase 1, ending with the PR attribution line.
  - Wait for the four required checks, deciding from JSON (`gh pr checks N --required --json bucket`), and report. Don't merge without Michael's go-ahead.
