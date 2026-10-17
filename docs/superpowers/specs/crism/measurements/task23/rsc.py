"""Post hoc: an unconditional per-column destripe of ours (each band ÷ its
column median over a 15-column running median of the column medians, the
classic column-ratio RSC), then the Pearson r against the MTRDR. Also the same
applied to the MTRDR itself (how much column structure it still has)."""

import sys
import warnings
import numpy as np
from pathlib import Path
from scipy.ndimage import median_filter
from varda.crism import validation_report as vr
from varda.crism.validation import summaryParameter, compareMaps
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

warnings.simplefilter("ignore")
ours_p, mtrdr_p, ours_in = sys.argv[1:4]
width = int(sys.argv[4]) if len(sys.argv) > 4 else 15
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
src = vr._read(ENVIDataSource(ours_in), w.rows, w.cols, np.array([0]))[..., 0]
ok = np.isfinite(A).all(-1) & np.isfinite(B).all(-1) & np.isfinite(src)
x = np.round(src[ok]).astype(int)
a = A[ok]
b = B[ok]
order = np.argsort(x, kind="stable")
u, first = np.unique(x[order], return_index=True)
med = np.array([np.median(g, 0) for g in np.split(a[order], first[1:])])
f = med / median_filter(med, size=(width, 1), mode="nearest")
fa = f[np.searchsorted(u, x)]
for p in ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310"):
    pb = summaryParameter(p, wl, b)
    r0 = compareMaps(summaryParameter(p, wl, a), pb).pearson
    r1 = compareMaps(summaryParameter(p, wl, a / fa), pb).pearson
    print(f"{p:9s} r {r0:.3f} -> column-ratio destripe ({width}) {r1:.3f}")
