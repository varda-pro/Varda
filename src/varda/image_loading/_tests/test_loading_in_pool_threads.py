"""Images loaded one after another on the same Qt pool thread, as the
self-test's ENVI then GeoTIFF round trip does.

Loading checks the CRS on the worker, so each load calls PROJ there. pyproj
freed a pool thread's PROJ context between tasks and the next load used the
freed context (SIGSEGV in proj_create); see ``utilities/proj_lock.py``. The
loads run in a separate Python so a crash fails this test, not the suite.
"""

import os
import subprocess
import sys

SCRIPT = """
import sys, time
from pathlib import Path

import numpy as np
from affine import Affine
from PyQt6.QtCore import QThreadPool
from PyQt6.QtWidgets import QApplication
from rasterio.crs import CRS

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.image_loading_service import ImageLoadingService
from varda.image_loading.raster_writer import writeRaster

app = QApplication([])
pool = QThreadPool.globalInstance()
pool.setMaxThreadCount(1)  # every load on the same pool thread
pool.setExpiryTimeout(-1)  # which stays alive between loads
source = ArrayDataSource(
    np.ones((8, 8, 3), np.float32),
    crs=CRS.from_epsg(32612),
    transform=Affine(30.0, 0.0, 500000.0, 0.0, -30.0, 3500000.0),
)
image = VardaRaster(dataSource=source, name="pool")
folder = Path(sys.argv[1])
for name in ("a.img", "a.tif", "b.img", "b.tif"):
    results = []
    ImageLoadingService.load_image_data(
        str(writeRaster(image, folder / name)), results.append, results.append
    )
    deadline = time.monotonic() + 60
    while not results and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    loaded = results[0]
    assert isinstance(loaded, VardaRaster), loaded
    assert loaded.crs is not None and loaded.crs.to_epsg() == 32612
    loaded.close()
    time.sleep(0.2)  # the task's Python thread state is dropped
print("loaded")
"""


def test_loads_one_after_another_on_one_pool_thread(tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    done = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(tmp_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert done.returncode == 0, (done.returncode, done.stderr[-3000:])
    assert "loaded" in done.stdout
