"""The real-data check: Bennu's OSIRIS-REx shape model (PDS Small Bodies
Node), kept in ~/Documents/Software/varda-aux/bennu/ and never committed.
Skipped when it isn't there."""

import logging
import math
from pathlib import Path

import numpy as np
import pytest

from varda.geo.bodies import BODIES
from varda.shapes.formats import SHAPE_SUFFIXES, readShape
from varda.shapes.geophysics import GeophysicsSettings, geophysics

logger = logging.getLogger(__name__)

BENNU_FOLDER = Path.home() / "Documents" / "Software" / "varda-aux" / "bennu"
# Barnouin et al. (2019), Nat. Geosci. 12, 247, Table 1
BENNU_VOLUME_KM3 = 0.0615
BENNU_MEAN_RADIUS_M = 244.0


def _models() -> list[Path]:
    if not BENNU_FOLDER.is_dir():
        return []
    found = [p for p in BENNU_FOLDER.rglob("*") if p.suffix.lower() in SHAPE_SUFFIXES]
    return sorted(found, key=lambda p: p.stat().st_size)


pytestmark = pytest.mark.skipif(not _models(), reason=f"no Bennu shape model in {BENNU_FOLDER}")


@pytest.fixture(scope="module")
def bennu():
    """The smallest (lowest-resolution) Bennu model found."""
    read = readShape(_models()[0], "Bennu")
    if read.suggestedUnit == "m":
        read = readShape(_models()[0], "Bennu", "m")
    return read.model


def test_bennu_reads_closed_with_the_papers_volume_and_radius(bennu):
    assert bennu.isClosed
    assert bennu.volume == pytest.approx(BENNU_VOLUME_KM3, rel=0.01)
    meanRadius = (3 * bennu.volume / (4 * math.pi)) ** (1 / 3) * 1000
    assert meanRadius == pytest.approx(BENNU_MEAN_RADIUS_M, rel=0.01)


@pytest.mark.slow
def test_bennu_slopes(bennu):
    density, sigma = BODIES["Bennu"].density or (0.0, 0.0)
    period = BODIES["Bennu"].rotationPeriodH or 0.0
    settings = GeophysicsSettings(density, sigma, period, maxExactPlates=50_000)
    out = {d.name: d for d in geophysics(bennu, settings, lambda *_: None)}
    slope = out["slope"].values
    logger.warning(
        "Bennu (%s, %d plates, evaluated on %d): slope mean %.2f°, max %.2f°",
        bennu.name, bennu.plateCount, out["slope"].provenance["evaluatedOn"],
        np.average(slope, weights=bennu.areas), slope.max(),
    )  # fmt: skip
    assert ((slope >= 0) & (slope <= 90)).all()
