# Task 23 measurement scripts (Phase 1 validation)

The scripts behind the attribution in `../../phase1-validation.md`
(2026-10-16). Scratch code, kept so the numbers can be reproduced; not part
of Varda. The pass/fail numbers themselves come from
`src/varda/crism/_tests/test_regression.py`, which needs none of these.
Paths point at `/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/`;
each script takes the output folder `S` (processing outputs stay outside the
repository) as its first argument.

Order: process with `python -m varda.crism process ... --out S/default
--sensor-space` (FRT00005850 and the three FRSs), `frs_variants.py`
(the FRSs without the basis and with a leave-one-out basis) and `toggles.py`
(FRT00005850 with one step changed at a time), then the analyses.

| Script | What it measures |
|---|---|
| `scan_scores.py` | every PDS AT and CAT VS ADR scored on the four TRR3s (the CAT comparison) |
| `maps.py` | ours and the MTRDR's parameter maps, albedo and CO2 on the overlap, with both source backplanes |
| `attribution.py`, `columns.py` | block means, the independent-noise ceiling, shared pixel-scale fluctuations, the column and line components of the difference |
| `factor.py` | the per-column spectral factor ours ÷ MTRDR, split into smooth and structured parts, recomputed into each parameter |
| `own_columns.py` | each product's own per-column parameter profile and its column-to-column scatter |
| `lines.py`, `shape_lines.py` | the along-track components against the DDR geometry (EGN) |
| `bowl.py`, `patch_columns.py` | the 2 µm bowl and the per-column structure against CAT's VS ADR artifact spectra |
| `rsc.py` | a post hoc column-ratio destripe of ours |
| `toggle_table.py` | the step-toggle table |
| `co2.py`, `basis_shift.py` | V3's CO2 residual of a product; the basis's shift of the parameter medians |
| `crater.py`, `run_crater.py`, `analyze_crater.py` | the fusion prototype's 36 m stack, repeatability and pair metrics (the same port as the test) |
| `figures.py` | the figures in `../../figures/` |
