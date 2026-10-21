# Phase 0 measurement scripts

The quick scripts behind measurements M1–M11 in `../evidence-ledger.md`
(2026-10-16). They are scratch code, kept so the numbers can be reproduced;
they are not part of Varda. Paths point at
`/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper/`.
Run from this folder (several `exec` a sibling for shared helpers), in this
order: `trr.py` (writes the WA/noise `.npy` files the others read), then
any of the rest.

| Script | Measurement |
|---|---|
| `proj.py`, `snap.py`, `geo.py` | M1: MTRDR sphere radius, tie-point rule, DE backplane check |
| `trr.py` | M3, M4, M6: TRR3 layout, WA smile, shift-difference noise |
| `co2.py`, `co2b.py` | M7: CO2-window residual of the official MTRDRs |
| `vs.py`, `fit.py`, `cmp.py`, `cog.py` | M7, M10: classic and fitted volcano-scan corrections on the FRSs |
| `stripe.py` | M11: residual column striping |
