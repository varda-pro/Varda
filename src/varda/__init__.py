import os
import sys

# A GDAL_DRIVER_PATH holds plugins built for another GDAL (e.g. conda's), so
# drop one from outside this environment. Done here, before rasterio/pyproj load.
_driverPath = os.environ.get("GDAL_DRIVER_PATH")
if _driverPath and not os.path.realpath(_driverPath).startswith(
    os.path.realpath(sys.prefix)
):
    del os.environ["GDAL_DRIVER_PATH"]

from . import log as log  # noqa: E402
from . import utilities as utilities  # noqa: E402

# A shell with another installation active (e.g. conda) exports its own PROJ/GDAL
# data; an old proj.db fails on planetary (IAU_2015) CRSs. Pin to the bundled
# data before rasterio/pyproj load; it needs only the stdlib. Both the app and
# the self-test and --check-plugin paths import varda first.
for _message in utilities.proj_data.pinProjData():
    log.info(_message)
