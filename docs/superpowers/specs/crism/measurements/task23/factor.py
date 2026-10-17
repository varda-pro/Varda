"""The spectral factor between ours and the MTRDR by detector column and by
line: q(x, λ) = median over the column's pixels of ours ÷ MTRDR. Split into a
smooth-in-λ part s (a cubic in λ per column, fitted outside 1880–2100 nm) and
the rest h = q / s, and recompute each parameter on MTRDR × s, × h, × q to
attribute the column component of the parameter differences."""

import json
import sys
import warnings
from pathlib import Path
import numpy as np
from varda.crism import validation_report as vr
from varda.crism.validation import summaryParameter
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

warnings.simplefilter("ignore", RuntimeWarning)

ours_p, mtrdr_p, ours_in, out = map(Path, sys.argv[1:5])
ours, ref = ENVIDataSource(str(ours_p)), ENVIDataSource(str(mtrdr_p))
w = vr._overlap(ours, ref)
wl = vr._nanometers(ours)
matched = vr._matchBands(wl, vr._nanometers(ref))
good = (
    vr._goodBands(ours_p, ours.bandCount)
    & vr._goodBands(mtrdr_p, ref.bandCount)[matched]
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
info = ENVIDataSource(str(ours_in))
src = vr._read(info, w.rows, w.cols, np.array([0, 1]))
sample, line = src[..., 0], src[..., 1]
both = np.isfinite(A).all(-1) & np.isfinite(B).all(-1) & np.isfinite(sample)
ratio = np.where(both[..., None], A / B, np.nan)
cols = np.round(sample[both]).astype(int)
lines = np.round(line[both]).astype(int)
R = ratio[both]  # (n, b)
P = ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")


def groupMedian(keys, values):
    order = np.argsort(keys, kind="stable")
    k, first = np.unique(keys[order], return_index=True)
    groups = np.split(values[order], first[1:])
    return k, np.array([np.median(g, axis=0) for g in groups])


ux, qx = groupMedian(cols, R)  # (ncol, b)
ut, qt = groupMedian(lines, R)
# smooth part per column: cubic in λ fitted outside the CO2/hydration window
fitBands = ~((wl > 1880) & (wl < 2100))
u = (wl - 1800) / 800
V = np.vander(u, 4)
coef, *_ = np.linalg.lstsq(V[fitBands], np.log(qx[:, fitBands]).T, rcond=None)
sx = np.exp((V @ coef).T)
hx = qx / sx


# the factors' column variation only (relative to their median over columns)
def rel(f):
    return f / np.median(f, axis=0, keepdims=True)


colIndex = {c: i for i, c in enumerate(ux)}
idx = np.array([colIndex[c] for c in cols])
Bn = B[both]
res = {
    "columns": ux.tolist(),
    "wavelengths": wl.tolist(),
    "q_columns": qx.tolist(),
    "q_lines": qt.tolist(),
    "lines": ut.tolist(),
}
base = {p: summaryParameter(p, wl, Bn) for p in P}
actual = {p: summaryParameter(p, wl, A[both]) - base[p] for p in P}
for label, factor in (("q", rel(qx)), ("smooth", rel(sx)), ("structured", rel(hx))):
    for p in P:
        d = summaryParameter(p, wl, Bn * factor[idx]) - base[p]
        _, dcol = groupMedian(cols, d[:, None])
        _, acol = groupMedian(cols, actual[p][:, None])
        dcol, acol = dcol[:, 0], acol[:, 0]
        acol = acol - np.median(acol)
        dcol = dcol - np.median(dcol)
        c = np.corrcoef(acol, dcol)[0, 1]
        explained = 1 - np.var(acol - dcol) / np.var(acol)
        res[f"{label}_{p}"] = {
            "corr": float(c),
            "explained": float(explained),
            "std": float(dcol.std()),
            "actualStd": float(acol.std()),
        }
        print(
            f"{label:10s} {p:9s} corr {c:+.3f} explained {explained:+.3f} std {dcol.std():.5f} (actual column std {acol.std():.5f})"
        )
json.dump(res, open(out, "w"))
