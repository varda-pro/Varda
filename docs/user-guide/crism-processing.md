# CRISM processing

**Processing → CRISM → Process CRISM Observation…** turns a CRISM IR
observation's TRR3 I/F cube into a map-projected, MTRDR-consistent I/F with
a per-band uncertainty (σ) and a record of everything that was done. It
re-implements the corrections of the CAT toolchain in Varda, so you can
reprocess an observation with your own choices (a different atmospheric
scan, destriping on or off, a pixel size) and see exactly what each step did.

Phase 1 covers the **IR detector (S), from a TRR3 cube**. VNIR, the IR/VNIR
join, and the empirical photometric correction (EGN) are not done yet; see
[Limits](#limits).

## What you need

For each observation, a folder with its **TRR3 IR I/F** (`*_if*l_trr3.img`
and `.hdr`/`.lbl`) and its **DDR** (`*_ddr1.img`, the geometry). Varda reads
the PDS labels, so the `.lbl` files must sit beside the images. A crop of an
observation works too.

Processing also needs a few **calibration files**, which are not part of
TRR3: the wavelength table (WA), the bad-pixel maps (BP) and the atmospheric
transmission spectra. Varda looks for them, in this order, in:

1. the observation's folder and its `CRISM_CDR/` subfolder (on the command line, any
   folder you add with `--calibration`);
2. its download cache (the app-data `crism` folder);
3. optionally, CAT's `aux_files` folder (**Get Calibration Files…** or
   `--cat-aux`), which adds the volcano-scan transmissions CAT uses.

Anything still missing is **fetched from the PDS** into the cache. Without a
network, Varda lists each missing file and where to get it, **before** any
processing starts. CAT's files are never copied or redistributed by Varda.
**Processing → CRISM → Get CRISM Calibration Files…** shows what an
observation needs and fetches the rest.

## The dialog

1. **Add Folder…** adds an observation folder (repeat for more). Each row
   shows its status.
2. Choose the **recipe**: one group of settings per step (below). **Load
   Recipe…** and **Save Recipe…** keep a recipe as JSON, which is also what
   the command line takes.
3. Choose the **output folder**, then **Run**. Missing calibration files are
   checked and fetched first. **Cancel** stops the batch; a canceled or
   failed observation leaves the output folder as it was, and the others
   still run.

Each map-projected I/F opens in the project when its observation finishes.

## The command line

Everything the dialog does is available without the GUI:

```
python -m varda.crism process <folder or TRR3 file>... --out crism-products
Varda --crism process <folder>... --recipe my-recipe.json
```

(`Varda --crism` is the same command in the packaged app.) Other commands:

- `calibration <folder> [--fetch]` lists the calibration files an
  observation needs and where each was found; `--fetch` downloads the
  missing ones.
- `learn-artifacts <folders>... --out <folder>` learns an artifact basis (see
  [Defaults](#defaults)) from several observations and prints its
  leave-one-out check.
- `validate <ours.img> <mtrdr.img>` compares a product with an official
  MTRDR and writes a report with figures.

`process` also takes `--sensor-space` (write the unprojected cube), `--cog`
(also write a Cloud Optimized GeoTIFF), `--cache`, `--cat-aux` and
`--calibration`. Exit code 0 means everything succeeded, 1 that some
observations failed, 2 a usage error or missing calibration files.

## The steps

| Step | What it does |
|---|---|
| Photometry | Divide by cos(incidence), from the areoid angle (or MOLA). |
| Despike | Replace isolated spikes (default 5σ above neighbors). |
| Atmospheric correction | Fit the gas transmission (CO₂, and the 2 µm band in particular) per pixel, with a wavelength shift and an elevation prior; divide it out. |
| Destripe | Remove column striping (off by default). |
| Smile | Resample onto the standard 411-band MTRDR wavelength vector, with the empirical per-column shift offsets (ESC). |
| Projection | Map-project onto the MTRDR grid (18 m pixels; 36 m for binned observations). |

The projection reproduces the MTRDR's grid: the same reference latitude,
radius, pixel size and origin, so a product lines up pixel for pixel with
the official MTRDR of the same observation.

## Outputs

Products go in the output folder, named like the MTRDR's (with `_vmtr`):

| File | Contents |
|---|---|
| `frs00031442_01_if168l_vmtr` | The map-projected I/F, 411 bands, ENVI float32. Bands above 2600 nm are flagged bad (`bbl = 0`). |
| `…_vmtr_sigma` | σ for each band and pixel (see below). |
| `…_de168l_vmtr` | The 14 DDR backplanes, map-projected: incidence and emission (areoid and MOLA), phase, latitude, longitude, slope and azimuth, elevation, thermal inertia, albedo, local solar time. |
| `…_in168l_vmtr` | Backplanes of the processing itself: source sample and line (0-based), β (the transmission's strength), its empirical σ, the atmospheric fit's RMS residual, the artifact amplitude, spike count, Leask count, effective looks. |
| `…_vter` | Optional (sensor space): the same I/F before map projection. |
| `.recipe.json`, `.provenance.json`, `.diagnostics.npz` | Sidecars: the recipe, the provenance, and the diagnostics behind it. |

Invalid samples are 65535 in every product; no NaN is ever written. Each
file is written under a temporary name and moved into place when complete, so
a failed run never leaves a header pointing at a partial image, and rerunning
into a folder replaces the previous products only if the new run succeeds.

## σ and provenance

**σ is the instrument noise**, estimated from brightness-normalized
along-track differences and propagated through every step, so it varies with
wavelength (it is chromatic). It does not include the *systematic*
uncertainties: the choice of atmospheric scan, the artifact amplitude, β's
excess scatter and the destriping frame. Those are reported **per band in
the provenance**, relative to I/F, so you can judge when they matter more
than the noise. ENVI headers carry `varda sigma file`, `varda sigma kind`
(`instrument-noise`), `varda sigma steps` and `varda history`, so a product
says how it was made.

`.provenance.json` also records the Varda version, the inputs and the
calibration files used (with their checksums), the recipe, what each step
did (for example the scan chosen and its score, β's range, how many spikes
were replaced, the elevation model's H), and the wall time of each pass.

## Defaults

- **Destripe is off** for TRR3 input: TRR3 is already filtered, so
  destriping changes little and can slightly harm it. Turn it on in the
  recipe to compare.
- **ESC is on**: the per-column empirical shift offsets (like CAT's) are
  applied after resampling.
- **Artifact model on, but only within its trained epoch.** The volcano-scan
  transmissions carry instrument artifacts that CAT fits. Varda ships bases
  learned from three 2014 observations (FRS00031442, FRS0002FE75,
  FRS00031260), valid for observations within about 180 days of that span.
  Outside it the step is skipped and the reason recorded; the pipeline never
  applies a basis to an epoch it was not learned for. `learn-artifacts`
  learns one for another epoch.
- **Atmospheric scan** is chosen automatically by how well it flattens the
  scene's CO₂ band (1.95–2.07 µm); name one in the recipe to force it.

## Limits

- **No EGN.** The empirical photometric correction CAT applies is not
  reproduced; beyond 2.2 µm our I/F differs from the MTRDR's by about 1% in a
  way that follows the phase angle.
- **IR only, no VNIR and no join.** The S detector, from TRR3.
- **Not bit-for-bit CAT.** The 2 µm region differs from an MTRDR by up to
  about 3%, where the MTRDR carries CAT's artifact correction and we do not
  have a basis for that epoch.
- **Validation status.** Phase 1 meets the MTRDR's grid, geolocation and CO₂
  residual, and falls short of the planned correlation of the mineral
  parameters with the MTRDR's (r of 0.63 to 0.90 against a target of 0.90), a
  few spectral-shape tolerances and a few parameter biases. Each shortfall
  has a measured cause, and the open decisions are Michael's. The full
  account is in the
  [Phase 1 validation report](../superpowers/specs/crism/phase1-validation.md).

Design and decisions:
[the design](../superpowers/specs/2026-10-16-crism-processing-design.md) and
its [evidence ledger](../superpowers/specs/crism/evidence-ledger.md).
