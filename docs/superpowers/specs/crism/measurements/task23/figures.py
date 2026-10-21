"""Figures for phase1-validation.md."""

import json
import sys
import warnings
from pathlib import Path
import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from varda.crism import validation_report as vr
from varda.crism.calibration import CalibrationLibrary, TransmissionSource
from varda.crism.readers import readTrr3, readDdr, DdrBand
from varda.crism.validation import co2Residual, CO2_WINDOW_NM
from varda.image_loading.data_sources.envi_data_source import ENVIDataSource

warnings.simplefilter("ignore")
plt.rcParams.update(
    {"font.size": 9, "axes.spines.top": False, "axes.spines.right": False}
)
D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
OUT = Path(sys.argv[2])
V = S / "default"
ours_p = V / "frt00005850_07_if167l_vmtr.img"
mtrdr_p = D / "FRT00005850/mtrdr/frt00005850_07_if167j_mtr3.img"
z = np.load(S / "maps-default.npz")
DPI = 100

# --- 1. before / after spectra ------------------------------------------------------
trr3 = readTrr3(D / "FRT00005850/trdr/frt00005850_07_if167l_trr3.lbl")
ddr = readDdr(D / "FRT00005850/ddr/frt00005850_07_de167l_ddr1.lbl")
lib = CalibrationLibrary(
    [D / "ATO0004EC45", D / "FRT00005850"], S / "cache", D / "CAT_v7_4_aux/aux_files"
)
twl = lib.wavelengths(trr3)
vter = ENVIDataSource(str(V / "frt00005850_07_if167l_vter.img"))
vwl = vr._nanometers(vter)
mt = ENVIDataSource(str(mtrdr_p))
mwl = vr._nanometers(mt)
rows = z["rows"]
cols = z["cols"]
refRows = z["refRows"]
refCols = z["refCols"]
sample, line = z["mtrdrSample"], z["mtrdrLine"]
picks = {}
ok = (
    np.isfinite(sample)
    & np.isfinite(z["BD1900_2_mtrdr"])
    & np.isfinite(z["OLINDEX3_mtrdr"])
)
inner = np.zeros_like(ok)
inner[20:-20, 20:-20] = True
ok &= inner
for label, key, pct in (
    ("hydrated (high BD1900_2)", "BD1900_2_mtrdr", 99.9),
    ("olivine-bearing (high OLINDEX3)", "OLINDEX3_mtrdr", 99.5),
    ("bland (median OLINDEX3, low BD1900_2)", None, None),
):
    if key:
        v = np.where(ok, z[key], -np.inf)
        thr = np.percentile(z[key][ok], pct)
        cand = np.argwhere(v >= thr)
        r, c = cand[len(cand) // 2]
    else:
        score = np.abs(
            z["OLINDEX3_mtrdr"] - np.nanmedian(z["OLINDEX3_mtrdr"][ok])
        ) + np.abs(z["BD1900_2_mtrdr"])
        score = np.where(ok, score, np.inf)
        r, c = np.unravel_index(np.argmin(score), score.shape)
    picks[label] = (int(r), int(c))
fig, axes = plt.subplots(1, 3, figsize=(12, 3.8), layout="constrained", sharey=False)
for ax, (label, (r, c)) in zip(axes, picks.items()):
    s, ln = int(round(sample[r, c])), int(round(line[r, c]))
    raw = trr3.lines(slice(ln - 1, ln + 2))[:, s - 1 : s + 2].astype(float)
    inc = np.radians(
        ddr.band(DdrBand.INCIDENCE_AREOID, slice(ln - 1, ln + 2))[:, s - 1 : s + 2]
    )
    before = np.nanmean((raw / np.cos(inc)[..., None]).reshape(-1, raw.shape[-1]), 0)
    after = np.nanmean(
        vr._read(vter, slice(ln - 1, ln + 2), slice(s - 1, s + 2)).reshape(
            -1, vwl.size
        ),
        0,
    )
    mr, mc = int(r + refRows[0]), int(c + refCols[0])
    theirs = np.nanmean(
        vr._read(mt, slice(mr - 1, mr + 2), slice(mc - 1, mc + 2)).reshape(
            -1, mwl.size
        ),
        0,
    )
    keep = (vwl > 1000) & (vwl < 2600)
    ax.plot(
        np.nanmedian(twl[s - 1 : s + 2], 0),
        before,
        color="0.6",
        lw=0.9,
        label="TRR3 ÷ cos i (before)",
    )
    ax.plot(vwl[keep], after[keep], color="C0", lw=1.1, label="Varda (after)")
    km = (mwl > 1000) & (mwl < 2600)
    ax.plot(mwl[km], theirs[km], color="C1", lw=1.0, ls="--", label="MTRDR")
    ax.set_xlim(1000, 2600)
    ax.set_title(f"{label}\ndetector column {s}, line {ln} (3 × 3 mean)", fontsize=8)
    ax.set_xlabel("Wavelength (nm)")
    lo, hi = np.nanpercentile(np.r_[after[keep], theirs[km]], [1, 99])
    ax.set_ylim(lo * 0.9, hi * 1.06)
axes[0].set_ylabel("I/F ÷ cos i")
axes[0].legend(fontsize=7, frameon=False)
fig.savefig(OUT / "v2_spectra_before_after.png", dpi=DPI)
plt.close(fig)


# --- 2. CO2 residual by detector column ----------------------------------------------
def byColumn(img: Path, info: Path, sampleBand: int) -> tuple[np.ndarray, np.ndarray]:
    src = ENVIDataSource(str(img))
    wl = vr._nanometers(src)
    good = vr._goodBands(img, src.bandCount)
    idx = np.flatnonzero((wl > CO2_WINDOW_NM[0]) & (wl < CO2_WINDOW_NM[1]) & good)
    cube = vr._read(src, slice(0, src.height), slice(0, src.width), idx)
    co2 = co2Residual(wl[idx], cube)
    inf = ENVIDataSource(str(info))
    smp = vr._read(
        inf, slice(0, inf.height), slice(0, inf.width), np.array([sampleBand])
    )[..., 0]
    smp[smp >= 65534] = np.nan
    okk = np.isfinite(co2) & np.isfinite(smp)
    x = np.round(smp[okk]).astype(int)
    v = co2[okk]
    o = np.argsort(x, kind="stable")
    u, f = np.unique(x[o], return_index=True)
    return u, np.array([np.median(g) for g in np.split(v[o], f[1:])])


fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 3.8), layout="constrained", sharey=True)
x, m = byColumn(ours_p, V / "frt00005850_07_in167l_vmtr.img", 0)
xm, mm = byColumn(mtrdr_p, D / "FRT00005850/mtrdr/frt00005850_07_in167j_mtr3.img", 5)
a1.plot(xm, mm, color="C1", lw=0.8, label=f"MTRDR (median {np.median(mm):.4f})")
a1.plot(x, m, color="C0", lw=0.8, label=f"Varda (median {np.median(m):.4f})")
a1.set_title("FRT00005850: median CO2 residual by detector column", fontsize=9)
a1.set_xlabel("Detector column")
a1.set_ylabel("CO2 residual (RMS, 1940–2100 nm)")
a1.legend(fontsize=7, frameon=False)
for name, color in (
    ("frs00031442", "C0"),
    ("frs0002fe75", "C2"),
    ("frs00031260", "C3"),
):
    x, m = byColumn(
        V / f"{name}_01_if168l_vmtr.img", V / f"{name}_01_in168l_vmtr.img", 0
    )
    a2.plot(
        x, m, color=color, lw=0.8, label=f"{name.upper()} (median {np.median(m):.4f})"
    )
    x, m = byColumn(
        S / "frs-nobasis" / f"{name}_01_if168l_vmtr.img",
        S / "frs-nobasis" / f"{name}_01_in168l_vmtr.img",
        0,
    )
    a2.plot(x, m, color=color, lw=0.6, ls=":", alpha=0.8)
a2.axhline(
    0.0083, color="0.4", lw=0.8, ls="--", label="V3 bar 0.0083 (per-pixel median)"
)
a2.set_title(
    "Rim FRSs, Varda: with the artifact basis (solid) and without (dotted)", fontsize=9
)
a2.set_xlabel("Detector column")
a2.legend(fontsize=7, frameon=False)
a2.set_ylim(0, 0.016)
fig.savefig(OUT / "co2_by_column.png", dpi=DPI)
plt.close(fig)

# --- 3. parameter maps ---------------------------------------------------------------
for name in ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310"):
    a, b = z[f"{name}_ours"], z[f"{name}_mtrdr"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2), layout="constrained")
    lo, hi = np.nanpercentile(b, [2, 98])
    d = a - b
    sp = np.nanpercentile(np.abs(d), 98)
    for ax, (t, im, cm, v0, v1) in zip(
        axes,
        (
            ("Varda", a, "viridis", lo, hi),
            ("MTRDR", b, "viridis", lo, hi),
            ("Varda − MTRDR", d, "RdBu_r", -sp, sp),
        ),
    ):
        h = ax.imshow(im, cmap=cm, vmin=v0, vmax=v1, interpolation="nearest")
        ax.set_title(f"{name}: {t}", fontsize=9)
        ax.axis("off")
        fig.colorbar(h, ax=ax, shrink=0.75)
    fig.savefig(OUT / f"v2_map_{name.lower()}.png", dpi=80)
    plt.close(fig)

# --- 4. brightness ratio --------------------------------------------------------------
ratio = z["albedoOurs"] / z["albedoMtrdr"]
lines = json.load(open(S / "lines-default.json"))
fig, (a1, a2) = plt.subplots(
    1,
    2,
    figsize=(11, 4.2),
    layout="constrained",
    gridspec_kw={"width_ratios": [1, 1.3]},
)
h = a1.imshow(ratio, cmap="RdBu_r", vmin=0.85, vmax=1.15, interpolation="nearest")
a1.axis("off")
fig.colorbar(h, ax=a1, shrink=0.8)
a1.set_title("FRT00005850: brightness ratio Varda ÷ MTRDR (1.3–1.7 µm)", fontsize=9)
L = np.round(z["oursLine"])
X = np.round(z["oursSample"])
okk = np.isfinite(ratio) & np.isfinite(L)
ul = np.unique(L[okk]).astype(int)
prof = np.array([np.median(ratio[okk & (L == t)]) for t in ul])
emi = ddr.band(DdrBand.EMISSION_AREOID)
emi = np.nanmedian(np.where(emi > -1000, emi, np.nan), axis=1)
a2.plot(ul, prof, color="C0", lw=0.9, label="ratio, median per line")
ax2 = a2.twinx()
ax2.plot(ul, emi[ul], color="C1", lw=0.9, label="emission angle")
ax2.set_ylabel("Emission angle (deg)", color="C1")
ux = np.unique(X[okk]).astype(int)
pc = np.array([np.median(ratio[okk & (X == t)]) for t in ux])
a2.set_xlabel("Line (along track)")
a2.set_ylabel("Ratio", color="C0")
a2.set_title(
    f"along track: r = {lines['albedoRatio']['emission']:+.2f} with emission; across track: {pc.min():.3f}–{pc.max():.3f}",
    fontsize=9,
)
fig.savefig(OUT / "v2_brightness_ratio.png", dpi=DPI)
plt.close(fig)

# --- 5. attribution: column profiles and the shape ratio -----------------------------
s = np.round(z["oursSample"])
ms = np.round(z["mtrdrSample"])
fig, axes = plt.subplots(5, 1, figsize=(9, 10), layout="constrained", sharex=True)
for ax, name in zip(axes, ("D2300", "BD2290", "BD1900_2", "OLINDEX3", "BD2310")):
    a, b = z[f"{name}_ours"], z[f"{name}_mtrdr"]
    okk = np.isfinite(a) & np.isfinite(b) & np.isfinite(s) & np.isfinite(ms)
    xs = np.unique(s[okk]).astype(int)
    ax.plot(xs, [np.median(a[okk & (s == x)]) for x in xs], lw=0.7, label="Varda")
    ax.plot(xs, [np.median(b[okk & (ms == x)]) for x in xs], lw=0.7, label="MTRDR")
    ax.set_ylabel(name)
axes[0].legend(fontsize=7, frameon=False)
axes[0].set_title(
    "FRT00005850: median of each parameter by detector column (each product's own source column)",
    fontsize=9,
)
axes[-1].set_xlabel("Detector column")
fig.savefig(OUT / "v2_column_profiles.png", dpi=80)
plt.close(fig)

rep = json.load(open(S / "validate-5850/report.json"))["shapeRatio"]
w = np.array(rep["wavelengthsNm"])
b = np.array(rep["byBand"])
trrIdx = trr3
vs = {t.scanId: t for t in lib.transmissions(trr3, TransmissionSource.CAT_VS_ADR)}[
    "11D87"
]
cw = np.nanmedian(twl[270:370], 0)
fig, ax = plt.subplots(figsize=(10, 3.8), layout="constrained")
tol = np.where((w >= 1940) & (w <= 2100), 0.02, 0.01)
ax.fill_between(w, 1 - tol, 1 + tol, color="0.9", label="V2 tolerance")
ax.plot(
    w,
    b,
    color="C0",
    lw=1.0,
    label="Varda ÷ MTRDR, each ÷ its 1.3–1.7 µm mean (median over pixels)",
)
for k, (lab, col) in enumerate((("McGuire", "C3"), ("Pelkey", "C2"))):
    a = np.nanmedian(vs.artifacts[k][270:370], 0)
    okk = np.isfinite(cw) & np.isfinite(a)
    ax.plot(
        cw[okk],
        1 - a[okk],
        color=col,
        lw=0.8,
        ls="--",
        label=f"1 − CAT 11D87 {lab} artifact spectrum (ln)",
    )
ax.set_xlim(1000, 2600)
ax.set_ylim(0.95, 1.02)
ax.set_xlabel("Wavelength (nm)")
ax.set_ylabel("Shape ratio")
ax.set_title(
    "FRT00005850: spectral shape against the MTRDR; the 2 µm bowl follows CAT's artifact patch",
    fontsize=9,
)
ax.legend(fontsize=7, frameon=False, loc="lower left")
fig.savefig(OUT / "v2_shape_ratio.png", dpi=DPI)
plt.close(fig)
print("figures written")
