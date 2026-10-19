import ctypes
import functools
import logging
from collections.abc import Callable
import os
import sys
from pathlib import Path

import numpy as np

from PyQt6.QtWidgets import (
    QFileDialog,
    QApplication,
)

from .data_sources import DataSource, RasterioDataSource, InMemoryDataSource
from .crs_check import checkImageCrs
from .error_box import showPlainError
from .plain_errors import PlainError, explainOpenError
from .data_sources.cached_data_source import CachedDataSource
from .data_sources.in_memory_data_source import InMemoryBudget
from .data_sources.registry import datasource_registry, get_image_type_filter
from varda.branding.loader import BusyDialog
from varda.common.entities import VardaRaster
from varda.utilities.background import runInBackground

logger = logging.getLogger(__name__)


def openDataSource(filePath: str) -> DataSource:
    """Open a DataSource for the given file path.

    The first registered source whose extensions match and whose ``canOpen``
    (if it has one) accepts the file opens it; e.g. an ``.img`` is ENVI only
    with a header beside it. Otherwise RasterioDataSource (any GDAL format).
    """
    ext = Path(filePath).suffix.lower()
    for entry in datasource_registry:
        if ext not in entry.fileExtensions:
            continue
        canOpen = getattr(entry.dataSourceClass, "canOpen", None)
        if canOpen is None or canOpen(filePath):
            return entry.dataSourceClass(filePath)

    # Default: try RasterioDataSource (supports any GDAL format)

    return RasterioDataSource(filePath)


def physicalMemoryBytes() -> int:
    """Installed RAM, or 8 GiB if it can't be read."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, ValueError, OSError):
        pass
    if sys.platform == "win32":
        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    return 8 * 1024**3


class _MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


IN_MEMORY_SHARE = 0.25  # of installed RAM: images larger than this read on demand
# Of installed RAM, for all in-memory images together. Beyond it the least
# recently used are read on demand. 40 % keeps the majority for the system and
# other programs (a few GB on a laptop), the display, and analyses, which
# need several times their image (see Analysis.estimateMemory); on 16 GB it
# still holds four full CRISM MTRDR cubes (1.5 GB each).
IN_MEMORY_BUDGET_SHARE = 0.40
IN_MEMORY_BUDGET = InMemoryBudget(int(physicalMemoryBytes() * IN_MEMORY_BUDGET_SHARE))


def wrapForReading(
    source: DataSource,
    limitBytes: int | None = None,
    budget: InMemoryBudget = IN_MEMORY_BUDGET,
) -> DataSource:
    """Load ``source`` into memory if it fits within ``limitBytes`` (a quarter
    of RAM by default; float32 once no-data is filled) and, together with the
    images already in memory, within ``budget`` (the least recently used are
    demoted to make room). Otherwise read it on demand through a small cache,
    so opening a very large cube, or many, is quick and doesn't exhaust
    memory."""
    limit = (
        int(physicalMemoryBytes() * IN_MEMORY_SHARE)
        if limitBytes is None
        else limitBytes
    )
    size = (
        source.width
        * source.height
        * source.bandCount
        * max(np.dtype(source.dtype).itemsize, 4)
    )
    if size <= limit and budget.reserve(size):
        try:
            return InMemoryDataSource(source, budget)
        finally:
            budget.release(size)
    logger.info(
        "Reading %s on demand (%.1f GB is more than %.1f GB, or than the "
        "%.1f GB left for images in memory)",
        source.filePath or "image",
        size / 1e9,
        limit / 1e9,
        budget.limitBytes / 1e9,
    )
    return CachedDataSource(source)


class ImageLoadingService:
    """Loads images in the background as VardaRasters (see ``loadRaster``)."""

    BUSY_DELAY_MS = 400  # loads quicker than this show nothing

    @classmethod
    def load_image_data(
        cls, file_path=None, on_success_callback=None, on_failure_callback=None
    ):
        """Load a new image as a VardaRaster.

        If file_path is None, prompts the user to select a file.
        on_success_callback receives a VardaRaster on success.
        on_failure_callback receives a plain error message on failure
        (without one, the error is shown in a message box).
        """
        logger.debug(f"Loading image data for {file_path}")
        if file_path is None:
            file_path = cls._request_file_path()
            if file_path is None:
                logger.info("No file path provided.")
                return
        cls.load_images([file_path], on_success_callback, on_failure_callback)

    @classmethod
    def load_images(
        cls, file_paths=None, on_success_callback=None, on_failure_callback=None
    ):
        """Load one or more images, each in the background.

        If file_paths is None, prompts the user to select one or more files.
        The callbacks run once per file, in the order the files were given
        (not the order they finish), so images join the project as chosen:
        on_success_callback receives a VardaRaster, on_failure_callback a
        plain error message (without one, the error is shown in a message
        box, with the library's own text under Details). A busy indicator
        shows while any of them is still loading.
        """
        if file_paths is None:
            file_paths = cls._request_file_paths()
            if not file_paths:
                logger.info("No file paths provided.")
                return
        paths = [str(path) for path in file_paths]

        def deliver(path: str, outcome: VardaRaster | PlainError) -> None:
            if isinstance(outcome, PlainError):
                logger.info("Couldn't open %s: %s", path, outcome.details)
                if on_failure_callback:
                    on_failure_callback(outcome.message)
                else:
                    cls._show_error(outcome)
            else:
                logger.info(f"Done loading image: {path}")
                if on_success_callback:
                    on_success_callback(outcome)

        batch = _OrderedBatch(paths, deliver, cls._busyDialog(paths))
        for index, path in enumerate(paths):
            runInBackground(
                functools.partial(loadRaster, path),
                functools.partial(batch.settle, index),
                functools.partial(batch.fail, index),
                description=f"Loading {path}",
            )

    @classmethod
    def _busyDialog(cls, paths: list[str]) -> BusyDialog:
        """Says Varda is opening the files, if it takes a moment."""
        names = Path(paths[0]).name if len(paths) == 1 else f"{len(paths)} images"
        dialog = BusyDialog(
            f"Opening {names}…",
            title="Opening",
            parent=QApplication.activeWindow(),
        )
        dialog.showAfter(cls.BUSY_DELAY_MS)
        return dialog

    @staticmethod
    def _show_error(error: PlainError) -> None:
        """Show a load error: the plain message, the library's text as details."""
        showPlainError(QApplication.activeWindow(), "Couldn't Open Image", error)

    @staticmethod
    def _request_file_path():
        """Opens a file dialog to request an image file path from the user."""
        file_name, _ = QFileDialog.getOpenFileName(
            None,
            "Open File",
            "",
            get_image_type_filter(),
        )
        return file_name if file_name else None

    @staticmethod
    def _request_file_paths():
        """Opens a file dialog to request one or more image file paths."""
        file_names, _ = QFileDialog.getOpenFileNames(
            None,
            "Open File(s)",
            "",
            get_image_type_filter(),
        )
        return file_names if file_names else None


class _OrderedBatch:
    """Hands each load's outcome on in the order the files were chosen,
    holding back ones that finish early, and closes the busy indicator when
    all are done."""

    def __init__(
        self,
        paths: list[str],
        deliver: Callable[[str, VardaRaster | PlainError], None],
        busy: BusyDialog,
    ) -> None:
        self._paths = paths
        self._deliver = deliver
        self._busy = busy
        self._outcomes: dict[int, VardaRaster | PlainError] = {}
        self._next = 0

    def settle(self, index: int, outcome: VardaRaster | PlainError) -> None:
        self._outcomes[index] = outcome
        while self._next in self._outcomes:
            self._deliver(self._paths[self._next], self._outcomes.pop(self._next))
            self._next += 1
        if self._next == len(self._paths):
            self._busy.close()
        elif len(self._paths) > 1:
            self._busy.setLabelText(
                f"Opening {len(self._paths)} images… "
                f"({self._next} of {len(self._paths)} open)"
            )

    def fail(self, index: int, error: BaseException) -> None:
        self.settle(index, explainOpenError(self._paths[index], error))


def loadRaster(filePath: str) -> VardaRaster:
    """Open ``filePath`` as a VardaRaster: in memory when it fits comfortably,
    otherwise read on demand, with its coordinate system checked (and the
    one automatic fix applied; see ``crs_check``). Blocks;
    ``ImageLoadingService`` runs it in the background."""
    logger.info(f"Loading image: {filePath}")
    image = VardaRaster.fromDataSource(wrapForReading(openDataSource(filePath)))
    checkImageCrs(image)
    return image
