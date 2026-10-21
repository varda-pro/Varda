"""Fix round 1: the smooth cross-track part of ours − MTRDR (a 15-column
running median of the per-column medians) for D2300 and BD1900_2, against
per-column candidates: the smile offset, the fitted shift (raw, smoothed,
extrapolated), the ESC frame, the column fit's residual, and each toggle's
own per-column effect (how much of the smooth difference it would remove)."""

import json
import sys
import warnings
from pathlib import Path

import numpy as np
from scipy.ndimage import median_filter

from varda.crism.atmosphere import smoothShift
from varda.crism.calibration import CalibrationLibrary
from varda.crism.readers import readDetectorFrame, readTrr3
from varda.crism.smile import escOffsets, standardVector

warnings.simplefilter("ignore")
D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
PARAMS = ("D2300", "BD1900_2", "BD2290", "BD2310")
SMOOTH = 15


def columnProfile(z, name, columns):
    d = z[f"{name}_ours"] - z[f"{name}_mtrdr"]
    s = np.round(z["oursSample"])
    ok = np.isfinite(d) & np.isfinite(s)
    out = np.full(columns, np.nan)
    keys = s[ok].astype(int)
    order = np.argsort(keys, kind="stable")
    u, first = np.unique(keys[order], return_index=True)
    for k, g in zip(u, np.split(d[ok][order], first[1:])):
        out[k] = np.median(g)
    return out


def smooth(p):
    ok = np.isfinite(p)
    out = np.full_like(p, np.nan)
    out[ok] = median_filter(p[ok], SMOOTH, mode="nearest")
    return out


def corr(a, b):
    ok = np.isfinite(a) & np.isfinite(b)
    return float(np.corrcoef(a[ok], b[ok])[0, 1]) if ok.sum() > 10 else float("nan")


z = np.load(S / "maps-default.npz")
diag = np.load(S / "default/frt00005850_07_if167l_vmtr.diagnostics.npz")
cols = 640
trr3 = readTrr3(D / "FRT00005850/trdr/frt00005850_07_if167l_trr3.lbl")
lib = CalibrationLibrary(
    [D / "ATO0004EC45", D / "FRT00005850"], S / "cache", D / "CAT_v7_4_aux/aux_files"
)
vector = standardVector()
waPath = next(
    (D / "CAT_v7_4_aux/aux_files/CDRs/WA").glob("CDR410803692813_WA0000000L_3.*")
)
wa = readDetectorFrame(waPath.with_suffix(waPath.suffix)).alignedTo(vector.detectorRows)
offsets = escOffsets(wa, vector.wavelengths)  # (640, 411)
vw = vector.wavelengths


def at(arr, nm):
    return arr[:, int(np.argmin(np.abs(vw - nm)))]


shift = diag["columnShiftNm"]
valid = np.isfinite(shift)
smoothed = smoothShift(np.nan_to_num(shift), valid)
firstValid, lastValid = np.flatnonzero(valid)[[0, -1]]
esc = diag["escCorrection"]
candidates = {
    "smile offset 2300 nm": at(offsets, 2300),
    "smile offset 2120 nm": at(offsets, 2120),
    "smile offset 1930 nm": at(offsets, 1930),
    "smile offset 1985 nm": at(offsets, 1985),
    "fitted shift (raw)": np.where(valid, shift, np.nan),
    "fitted shift (smoothed)": smoothed,
    "ESC 2120 nm": at(esc, 2120),
    "ESC 2290 nm": at(esc, 2290),
    "ESC 1930 nm": at(esc, 1930),
    "ESC 1985 nm": at(esc, 1985),
    "column fit residual RMS": np.where(valid, diag["columnResidualRms"], np.nan),
    "column fit reduced chi2": np.where(valid, diag["columnReducedChiSquare"], np.nan),
    "column beta": np.where(valid, diag["columnBeta"], np.nan),
}
toggles = (
    "destripe",
    "noShift",
    "noEsc",
    "noHydrationMask",
    "perPixel",
    "cat11D87",
    "noAtm",
)
out = {"fitValidColumns": [int(firstValid), int(lastValid)]}
print(
    f"column fit valid {firstValid}-{lastValid}; overlap columns",
    int(np.nanmin(z["oursSample"])),
    int(np.nanmax(z["oursSample"])),
)
for name in PARAMS:
    raw = columnProfile(z, name, cols)
    sm = smooth(raw)
    hf = raw - sm
    total = np.nanvar(raw)
    row = {
        "smoothShareOfColumnVariance": float(np.nanvar(sm) / total),
        "smoothStd": float(np.nanstd(sm)),
        "highStd": float(np.nanstd(hf)),
    }
    row["correlations"] = {k: corr(sm, smooth(v)) for k, v in candidates.items()}
    row["toggles"] = {}
    for t in toggles:
        zt = np.load(S / f"toggle-{t}/maps.npz")
        rt = columnProfile(zt, name, cols)
        smt = smooth(rt)
        effect = sm - smt  # what the toggle changes in the smooth difference
        ok = np.isfinite(sm) & np.isfinite(smt)
        removed = 1 - np.var(smt[ok]) / np.var(sm[ok])
        row["toggles"][t] = {
            "smoothStdAfter": float(np.std(smt[ok])),
            "removedShare": float(removed),
            "effectCorr": corr(sm, effect),
            "highStdAfter": float(np.nanstd(rt - smt)),
        }
    out[name] = row
    print(
        f"\n{name}: smooth share of column variance {row['smoothShareOfColumnVariance']:.2f}; smooth std {row['smoothStd']:.5f}, high std {row['highStd']:.5f}"
    )
    for k, v in sorted(row["correlations"].items(), key=lambda kv: -abs(kv[1])):
        print(f"  corr with {k:28s} {v:+.2f}")
    for t, v in row["toggles"].items():
        print(
            f"  toggle {t:16s} smooth std {v['smoothStdAfter']:.5f} (removes {v['removedShare']:+.2f}) high std {v['highStdAfter']:.5f}"
        )
json.dump(out, open(S / "smooth-columns.json", "w"), indent=1)
