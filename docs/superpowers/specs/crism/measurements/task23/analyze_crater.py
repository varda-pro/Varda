import itertools
import json
import pickle
import sys
import warnings
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import crater as C

warnings.simplefilter("ignore", RuntimeWarning)
stack = pickle.load(open(sys.argv[1], "rb"))
wl = stack.wavelengths
M = [0, 1, 2, 3, 4]
g5 = C.subsetGains(stack, M)
v5 = C.noiseVariance(stack, M, g5)
Y = np.stack([g5[n][:, None] * stack.values[k] for n, k in enumerate(M)])
three = np.isfinite(Y[:, 0, :]).sum(0) >= 3
print("points >=3 looks", three.sum())
print("chi2 shape (5 MTRDRs, >=3 looks)", C.shapeChiSquare(Y[:, :, three], v5, wl))


def protoBd(y, lo, c, hi):
    def b_(w):
        i = np.argmin(abs(wl - w))
        return np.nanmean(y[:, i - 2 : i + 3, :], 1)

    t = (c - lo) / (hi - lo)
    return 1 - b_(c) / ((1 - t) * b_(lo) + t * b_(hi))


print("prototype BD2300 repeatability", C.repeatability(protoBd(Y, 2160, 2310, 2400)))
print("prototype BD1900 repeatability", C.repeatability(protoBd(Y, 1850, 1930, 2140)))
rep = {}
for name in ("BD2310", "D2300", "BD2290", "BD1900_2", "OLINDEX3"):
    rep[name] = C.repeatability(C.parameterStack(stack, M, g5, name))
print("repeatability", rep)
# pairs
g6 = C.subsetGains(stack, list(range(6)))
v6 = C.noiseVariance(stack, list(range(6)), g6)
print(
    "noise TCH per obs (median sigma, good bands)",
    np.sqrt(np.median(v6[:, C.chiSquareGood(wl)], 1)),
)
res = {"repeatability": rep, "pairs": {}}
for i, j in itertools.combinations(range(6), 2):
    both = np.isfinite(stack.values[i, 0]) & np.isfinite(stack.values[j, 0])
    if both.sum() < 500:
        continue
    yi, yj = stack.values[i][:, both], stack.values[j][:, both]
    raw = C.shapeChiSquare(np.stack([yi, yj]), v6[[i, j]], wl)
    gained = C.shapeChiSquare(
        np.stack([g6[i][:, None] * yi, g6[j][:, None] * yj]), v6[[i, j]], wl
    )
    offs = {
        pp: float(
            np.nanmedian(
                C.summaryParameter(pp, wl, yi.T) - C.summaryParameter(pp, wl, yj.T)
            )
        )
        for pp in ("BD2310", "D2300", "BD2290", "BD1900_2", "OLINDEX3")
    }
    off = offs["BD2310"]
    gbi = C.summaryParameter("BD2310", wl, (g6[i][:, None] * yi).T)
    gbj = C.summaryParameter("BD2310", wl, (g6[j][:, None] * yj).T)
    goff = float(np.nanmedian(gbi - gbj))
    key = f"{stack.names[i]}|{stack.names[j]}"
    res["pairs"][key] = dict(
        n=int(both.sum()),
        chi2ShapeRaw=raw,
        chi2ShapeGained=gained,
        bd2310Offset=off,
        bd2310OffsetGained=goff,
        offsets=offs,
    )
    print(
        f"{key:26s} n={both.sum():6d} chi2 raw {raw:7.2f} gained {gained:6.2f} BD2310 off {off:+.4f} gained {goff:+.4f} | "
        + " ".join(f"{k} {v:+.4f}" for k, v in offs.items() if k != "BD2310")
    )
json.dump(res, open(sys.argv[2], "w"), indent=1)
