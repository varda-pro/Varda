"""The FRSs without the artifact basis and with a leave-one-out basis (learned
from the other two FRSs under the same scan)."""

import sys
from pathlib import Path
from varda.crism import atmosphere
from varda.crism.atmosphere import (
    learnArtifactBasis,
    saveArtifactBasis,
    artifactFileName,
)
from varda.crism.calibration import CalibrationLibrary
from varda.crism.observation import CrismObservation
from varda.crism.pipeline import columnResiduals, process
from varda.crism.recipe import Recipe

D = Path("/Users/phillipsm/Documents/Software/varda-demo-data/data-for-paper")
S = Path(sys.argv[1])
FRS = {"FRS00031442": "061C4:AT", "FRS0002FE75": "0A93E:AT", "FRS00031260": "08608:AT"}


def lib(name):
    return CalibrationLibrary(
        [D / "ATO0004EC45", D / name], S / "cache", D / "CAT_v7_4_aux/aux_files"
    )


for name, key in FRS.items():
    obs = CrismObservation.fromFolder(D / name)
    off = Recipe()
    off.atmosphere.artifactModel.set(False)
    process(obs, off, S / "frs-nobasis", library=lib(name))
    print(name, "no basis done", flush=True)
    others = [n for n in FRS if n != name]
    forced = Recipe()
    forced.atmosphere.scan.set(key)
    cols = [
        columnResiduals(CrismObservation.fromFolder(D / n), forced, lib(n))
        for n in others
    ]
    basis = learnArtifactBasis(
        [c.residuals for c in cols],
        key,
        others,
        wavelengths=cols[0].wavelengths,
        sclks=[c.sclk for c in cols],
    )
    folder = S / f"loo-bases-{name}"
    folder.mkdir(exist_ok=True)
    saveArtifactBasis(basis, folder / artifactFileName(key, basis.sclkSpan))
    saved = atmosphere.ARTIFACT_FOLDER
    atmosphere.ARTIFACT_FOLDER = folder
    process(obs, Recipe(), S / "frs-loo", library=lib(name))
    atmosphere.ARTIFACT_FOLDER = saved
    print(name, "leave-one-out done", flush=True)
