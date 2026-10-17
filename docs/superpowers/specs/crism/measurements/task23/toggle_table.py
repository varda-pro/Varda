import json
import sys
from pathlib import Path

S = Path(sys.argv[1])
P = ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")
runs = {"default": (S / "validate-5850/report.json", S / "attribution-default.json")}
for d in sorted(S.glob("toggle-*")):
    if (d / "attribution.json").exists():
        runs[d.name[7:]] = (d / "report/report.json", d / "attribution.json")
print(
    f"{'run':16s} "
    + " ".join(f"{p:>16s}" for p in P)
    + "   co2 ours  shape min  shape@2400"
)
for name, (rep, att) in runs.items():
    r = json.load(open(rep))
    a = json.load(open(att))
    cells = [
        f"{r['parameters'][p]['pearson']:.3f}/{r['parameters'][p]['medianBias']:+.4f}"
        for p in P
    ]
    wl = r["shapeRatio"]["wavelengthsNm"]
    b = r["shapeRatio"]["byBand"]
    at = min(range(len(wl)), key=lambda i: abs(wl[i] - 2400))
    print(
        f"{name:16s} "
        + " ".join(f"{c:>16s}" for c in cells)
        + f"   {r['co2Residual']['ours']['median']:.5f}  {r['shapeRatio']['min']:.4f}  {b[at]:.4f}"
    )
print()
print(
    f"{'run':16s} "
    + " ".join(f"{p:>22s}" for p in P)
    + "  (column fraction / r column-removed)"
)
for name, (rep, att) in runs.items():
    a = json.load(open(att))
    print(
        f"{name:16s} "
        + " ".join(
            f"{a[p]['diffColumnFraction']:>10.2f}/{a[p]['pearsonColumnRemoved']:.3f}/{a[p]['varRatioOursOverMtrdr']:.2f}"
            for p in P
        )
    )
