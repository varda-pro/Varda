"""FRT00005850 with one step toggled at a time; each run validated against the MTRDR."""

import subprocess
import sys
import time
from pathlib import Path
from varda.crism import atmosphere
from varda.crism.calibration import CalibrationLibrary, TransmissionSource
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import process
from varda.crism.recipe import IncidenceSource, BetaMode, Recipe
from varda.crism.validation_report import validate

D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
which = sys.argv[2:]
MTRDR = D / "FRT00005850/mtrdr/frt00005850_07_if167j_mtr3.img"
MTRDR_IN = D / "FRT00005850/mtrdr/frt00005850_07_in167j_mtr3.img"


def variant(name):
    r = Recipe()
    hydration = None
    if name == "destripe":
        r.destripe.enabled.set(True)
    elif name == "noEsc":
        r.smile.esc.set(False)
    elif name == "noSmile":
        r.smile.enabled.set(False)
    elif name == "mola":
        r.photometry.incidence.set(IncidenceSource.MOLA)
    elif name == "noDespike":
        r.despike.enabled.set(False)
    elif name == "perPixel":
        r.atmosphere.betaMode.set(BetaMode.PER_PIXEL)
    elif name == "noShift":
        r.atmosphere.fitShift.set(False)
    elif name == "noHydrationMask":
        hydration = (0.0, -1.0)
    elif name == "cat11D87":
        r.atmosphere.source.set(TransmissionSource.BOTH)
        r.atmosphere.scan.set("11D87:VS")
    elif name == "noAtm":
        r.atmosphere.enabled.set(False)
    elif name == "destripeNoShift":
        r.destripe.enabled.set(True)
        r.atmosphere.fitShift.set(False)
    else:
        raise SystemExit(name)
    return r, hydration


for name in which:
    recipe, hydration = variant(name)
    saved = atmosphere.HYDRATION_MASK_NM
    if hydration is not None:
        atmosphere.HYDRATION_MASK_NM = hydration
    out = S / f"toggle-{name}"
    library = CalibrationLibrary(
        [D / "ATO0004EC45", D / "FRT00005850"],
        S / "cache",
        D / "CAT_v7_4_aux/aux_files",
    )
    t = time.time()
    result = process(
        CrismObservation.fromFolder(D / "FRT00005850"), recipe, out, library=library
    )
    atmosphere.HYDRATION_MASK_NM = saved
    print(name, "processed", round(time.time() - t), "s", flush=True)
    validate(result.mapProjected, MTRDR, out / "report")
    subprocess.run(
        [
            sys.executable,
            str(S.parent / "scripts/maps.py"),
            str(result.mapProjected),
            str(MTRDR),
            str(result.info),
            str(MTRDR_IN),
            str(out / "maps.npz"),
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(S.parent / "scripts/attribution.py"),
            str(out / "maps.npz"),
            str(out / "attribution.json"),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for p in out.glob("*_vmtr*.img"):
        if "_in" not in p.name:
            p.unlink()  # keep only the IN and the reports
    print(name, "done", flush=True)
