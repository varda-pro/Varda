"""The shape ratio (ours ÷ MTRDR, each ÷ its 1.3–1.7 µm mean) per source line
at a few wavelengths, against the along-track phase angle."""

import sys
import json
import warnings
import numpy as np
from pathlib import Path
from varda.crism import validation_report as vr
from varda.crism.readers import readDdr, DdrBand
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

warnings.simplefilter("ignore")
ours_p, mtrdr_p, ours_in, ddr_p, out = sys.argv[1:6]
ours, ref = ENVIDataSource(ours_p), ENVIDataSource(mtrdr_p)
w = vr._overlap(ours, ref)
wl = vr._nanometers(ours)
matched = vr._matchBands(wl, vr._nanometers(ref))
good = (
    vr._goodBands(Path(ours_p), ours.bandCount)
    & vr._goodBands(Path(mtrdr_p), ref.bandCount)[matched]
)
dr, dc = w.shift
A = vr._read(ours, w.rows, w.cols)[..., good]
B = vr._read(
    ref,
    slice(w.rows.start - dr, w.rows.stop - dr),
    slice(w.cols.start - dc, w.cols.stop - dc),
    matched,
)[..., good]
wl = wl[good]
src = vr._read(ENVIDataSource(ours_in), w.rows, w.cols, np.array([0, 1]))
norm = (wl >= 1300) & (wl <= 1700)
ok = np.isfinite(A).all(-1) & np.isfinite(B).all(-1) & np.isfinite(src[..., 1])
ratio = (A[ok] / A[ok][:, norm].mean(1, keepdims=True)) / (
    B[ok] / B[ok][:, norm].mean(1, keepdims=True)
)
lines = np.round(src[..., 1][ok]).astype(int)
cols = np.round(src[..., 0][ok]).astype(int)
ddr = readDdr(ddr_p)
phase = np.nanmedian(
    np.where(ddr.band(DdrBand.PHASE) > -1000, ddr.band(DdrBand.PHASE), np.nan), axis=1
)
res = {"wavelengths": wl.tolist(), "overall": np.median(ratio, 0).tolist()}
for key, groups in (("line", lines), ("column", cols)):
    order = np.argsort(groups, kind="stable")
    u, first = np.unique(groups[order], return_index=True)
    med = np.array([np.median(g, 0) for g in np.split(ratio[order], first[1:])])
    res[key] = {"index": u.tolist(), "median": med.tolist()}
    for t in (1080, 1200, 2080, 2300, 2400, 2550):
        i = np.argmin(abs(wl - t))
        extra = (
            f" corr phase {np.corrcoef(med[:, i], phase[u])[0, 1]:+.3f}"
            if key == "line"
            else ""
        )
        print(
            f"{key:6s} {t} nm: median {np.median(med[:, i]):.4f} p5–p95 {np.percentile(med[:, i], 5):.4f}–{np.percentile(med[:, i], 95):.4f}{extra}"
        )
json.dump(res, open(out, "w"))
