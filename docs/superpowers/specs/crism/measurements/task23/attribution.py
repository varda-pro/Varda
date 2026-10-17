"""Why ours and the MTRDR's parameter maps correlate below 0.90: noise ceiling,
block means, shared vs independent pixel-scale fluctuations, and the column
(cross-track) and line components of the difference."""

import json
import sys
import numpy as np

PARAMS = ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")
z = np.load(sys.argv[1])
MAD = 1.4826


def pearson(a, b):
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    da, db = a - a.mean(), b - b.mean()
    return float(da @ db / np.sqrt((da @ da) * (db @ db))), int(m.sum())


def block(a, f):
    r, c = a.shape[0] // f * f, a.shape[1] // f * f
    x = a[:r, :c].reshape(r // f, f, c // f, f)
    with np.errstate(invalid="ignore"):
        return x.mean(axis=(1, 3))  # NaN if any is NaN


def robustVar(x):
    x = x[np.isfinite(x)]
    return (MAD * np.median(np.abs(x - np.median(x)))) ** 2


def sensor(values, sample, line):
    """Map values (h, w) to sensor space by their source pixel (first wins)."""
    ok = np.isfinite(values) & np.isfinite(sample) & np.isfinite(line)
    s = np.round(sample[ok]).astype(int)
    t = np.round(line[ok]).astype(int)
    out = np.full((t.max() + 1, s.max() + 1), np.nan)
    out[t, s] = values[ok]
    return out


def secondDiff(p, axis):
    p = np.moveaxis(p, axis, 0)
    d = p[1:-1] - 0.5 * (p[:-2] + p[2:])
    return np.moveaxis(d, 0, axis)


def pad(a, shape):
    out = np.full(shape, np.nan)
    out[: a.shape[0], : a.shape[1]] = a
    return out


res = {}
same = (np.round(z["oursSample"]) == np.round(z["mtrdrSample"])) & (
    np.round(z["oursLine"]) == np.round(z["mtrdrLine"])
)
res["sameSourceFraction"] = float(
    same.sum() / (np.isfinite(z["oursSample"]) & np.isfinite(z["mtrdrSample"])).sum()
)
for name in PARAMS:
    a, b = z[f"{name}_ours"], z[f"{name}_mtrdr"]
    r = {}
    r["pearson"], r["n"] = pearson(a, b)
    r["pearsonSameSource"], _ = pearson(
        np.where(same, a, np.nan), np.where(same, b, np.nan)
    )
    for f in (2, 4, 8):
        r[f"pearsonBlock{f}"], _ = pearson(block(a, f), block(b, f))
    # sensor space: ours by its own source, the MTRDR by its own source
    A = sensor(a, z["oursSample"], z["oursLine"])
    B = sensor(b, z["mtrdrSample"], z["mtrdrLine"])
    shape = (max(A.shape[0], B.shape[0]), max(A.shape[1], B.shape[1]))
    A, B = pad(A, shape), pad(B, shape)
    both = np.isfinite(A) & np.isfinite(B)
    A, B = np.where(both, A, np.nan), np.where(both, B, np.nan)
    r["pearsonSensor"], _ = pearson(A, B)
    # noise from along-track second differences (texture included: an upper bound)
    for side, P in (("ours", A), ("mtrdr", B)):
        dAlong = secondDiff(P, 0)
        dAcross = secondDiff(P, 1)
        r[f"noiseAlong_{side}"] = float(np.sqrt(robustVar(dAlong) / 1.5))
        r[f"noiseAcross_{side}"] = float(np.sqrt(robustVar(dAcross) / 1.5))
        r[f"var_{side}"] = float(np.nanvar(P))
        r[f"robustVar_{side}"] = float(robustVar(P))

    # expected r if each map is the same signal plus its own independent noise
    def ceiling(kind):
        ra = max(1 - r[f"noise{kind}_ours"] ** 2 / r["var_ours"], 0)
        rb = max(1 - r[f"noise{kind}_mtrdr"] ** 2 / r["var_mtrdr"], 0)
        return float(np.sqrt(ra * rb))

    r["ceilingAlong"] = ceiling("Along")
    r["ceilingAcross"] = ceiling("Across")
    # are pixel-scale fluctuations shared? correlation of the second differences
    r["highPassCorrAlong"], _ = pearson(secondDiff(A, 0), secondDiff(B, 0))
    r["highPassCorrAcross"], _ = pearson(secondDiff(A, 1), secondDiff(B, 1))
    # the difference: column (detector sample) and line components
    d = A - B
    total = float(np.nanvar(d))
    col = np.nanmedian(d, axis=0)
    dc = d - col[None, :]
    line = np.nanmedian(dc, axis=1)
    rest = dc - line[:, None]
    r["diffVar"] = total
    r["diffColumnFraction"] = float(1 - np.nanvar(dc) / total)
    r["diffLineFraction"] = float((np.nanvar(dc) - np.nanvar(rest)) / total)
    r["diffRestFraction"] = float(np.nanvar(rest) / total)
    r["diffRestNoiseAlong"] = float(np.sqrt(robustVar(secondDiff(rest, 0)) / 1.5))
    r["pearsonColumnRemoved"], _ = pearson(A - col[None, :], B)
    r["pearsonColumnLineRemoved"], _ = pearson(A - col[None, :] - line[:, None], B)
    r["columnProfile"] = col.tolist()
    r["varRatioOursOverMtrdr"] = r["var_ours"] / r["var_mtrdr"]
    res[name] = r
    print(
        name,
        {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in r.items()
            if k != "columnProfile"
        },
    )
json.dump(res, open(sys.argv[2], "w"), indent=1)
print("same source fraction", res["sameSourceFraction"])
