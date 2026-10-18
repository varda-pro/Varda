"""varda.geo is safe from worker threads while the GUI thread uses PROJ."""

import threading
import time

from pyproj import Transformer

from varda.geo.bodies import identifyBody
from varda.geo._tests.fixtures import (
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    LRO_WAC_SOUTH_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    VESTA_IAU,
    WGS84,
)
from varda.utilities.proj_lock import PROJ_LOCK

FIXTURES = [
    CRISM_MTRDR_WKT,
    CTX_DEM_PROJ,
    HIRISE_POLAR_PROJ,
    LRO_WAC_PROJ,
    LRO_WAC_SOUTH_POLAR_PROJ,
    MOLA_MEGDR_PROJ,
    VESTA_IAU,
    WGS84,
]


def _answer(crs: str) -> str | None:
    body = identifyBody(crs).body
    return body.name if body is not None else None


def test_identify_body_from_many_threads():
    expected = {crs: _answer(crs) for crs in FIXTURES}
    errors: list[BaseException] = []
    results: list[tuple[str, str | None]] = []

    def worker():
        try:
            for i in range(200):
                crs = FIXTURES[i % len(FIXTURES)]
                results.append((crs, _answer(crs)))
        except BaseException as error:  # noqa: BLE001 - reported below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    while any(thread.is_alive() for thread in threads):
        with PROJ_LOCK:
            transformer = Transformer.from_crs(
                LRO_WAC_PROJ, "+proj=longlat +R=1737400 +no_defs", always_xy=True
            )
            transformer.transform(1000.0, 2000.0)
        time.sleep(0.001)  # let the workers take the lock
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 8 * 200
    assert all(answer == expected[crs] for crs, answer in results)
