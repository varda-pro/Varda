import json
import sys
import numpy as np
from scipy.ndimage import median_filter

res = json.load(open(sys.argv[1]))
for name in ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310"):
    col = np.array(res[name]["columnProfile"], dtype=float)
    ok = np.isfinite(col)
    x = np.arange(col.size)
    c = col[ok]
    xs = x[ok]
    smooth = median_filter(c, size=15, mode="nearest")
    hf = c - smooth
    poly = np.polyval(np.polyfit(xs, c, 3), xs)
    print(
        f"{name:9s} cols {ok.sum()} median {np.median(c):+.5f} std {c.std():.5f} | smooth(15) std {smooth.std():.5f} hf std {hf.std():.5f} | cubic std {poly.std():.5f} resid-cubic std {(c - poly).std():.5f} | p5..p95 {np.percentile(c, 5):+.4f} {np.percentile(c, 95):+.4f}"
    )
