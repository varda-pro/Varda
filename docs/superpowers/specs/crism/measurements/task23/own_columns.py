import sys
import numpy as np
import warnings
from scipy.ndimage import median_filter
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

warnings.simplefilter("ignore")
z = np.load(sys.argv[1])
s = np.round(z["oursSample"])
ms = np.round(z["mtrdrSample"])
fig, ax = plt.subplots(5, 1, figsize=(10, 12), sharex=True)
for k, p in enumerate(("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")):
    a, b = z[f"{p}_ours"], z[f"{p}_mtrdr"]
    ok = np.isfinite(a) & np.isfinite(b) & np.isfinite(s) & np.isfinite(ms)
    xs = np.unique(s[ok]).astype(int)
    pa = np.array([np.median(a[ok & (s == x)]) for x in xs])
    pb = np.array([np.median(b[ok & (ms == x)]) for x in xs])
    hfa = pa - median_filter(pa, 15, mode="nearest")
    hfb = pb - median_filter(pb, 15, mode="nearest")
    print(
        f"{p:9s} own column profile std ours {pa.std():.5f} mtrdr {pb.std():.5f} | column-to-column (hf) ours {hfa.std():.5f} mtrdr {hfb.std():.5f}"
    )
    ax[k].plot(xs, pa, lw=0.7, label="ours")
    ax[k].plot(xs, pb, lw=0.7, label="MTRDR")
    ax[k].set_ylabel(p)
ax[0].legend()
fig.savefig(sys.argv[2], dpi=70)
