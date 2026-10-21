"""The along-track (line) component of each difference against the DDR geometry."""

import sys
import json
import numpy as np
import warnings
from varda.crism.readers import readDdr, DdrBand

warnings.simplefilter("ignore")
z = np.load(sys.argv[1])
ddr = readDdr(sys.argv[2])
L = np.round(z["oursLine"])
ok0 = np.isfinite(L)
geometry = {}
for name, band in (
    ("incidence", DdrBand.INCIDENCE_AREOID),
    ("emission", DdrBand.EMISSION_AREOID),
    ("phase", DdrBand.PHASE),
):
    g = ddr.band(band)
    geometry[name] = np.nanmedian(np.where(g > -1000, g, np.nan), axis=1)
alb = z["albedoOurs"] / z["albedoMtrdr"]
out = {}
for p in ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310", "albedoRatio"):
    d = alb if p == "albedoRatio" else z[f"{p}_ours"] - z[f"{p}_mtrdr"]
    ok = ok0 & np.isfinite(d)
    lines = np.unique(L[ok]).astype(int)
    prof = np.array([np.median(d[ok & (L == t)]) for t in lines])
    row = {"std": float(prof.std())}
    for g, v in geometry.items():
        row[g] = float(np.corrcoef(prof, v[lines])[0, 1])
    out[p] = row
    print(
        f"{p:11s} line-profile std {prof.std():.5f}  corr with "
        + " ".join(f"{g} {row[g]:+.3f}" for g in geometry)
    )
print(
    "geometry ranges",
    {g: (float(np.nanmin(v)), float(np.nanmax(v))) for g, v in geometry.items()},
)
json.dump(out, open(sys.argv[3], "w"))
