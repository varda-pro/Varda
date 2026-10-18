import ctypes
import logging
import os
import sys
from pathlib import Path

import numpy as np

from PyQt6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QApplication,
)

from .data_sources import DataSource, RasterioDataSource, InMemoryDataSource
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

    LARGE_FILE_THRESHOLD_MB = 100

    @classmethod
    def load_image_data(
        cls, file_path=None, on_success_callback=None, on_failure_callback=None
    ):
        """Load a new image as a VardaRaster.

        If file_path is None, prompts the user to select a file.
        on_success_callback receives a VardaRaster on success.
        on_failure_callback receives an error message string on failure
        (without one, the error is shown in a message box).
        """
        logger.debug(f"Loading image data for {file_path}")
        if file_path is None:
            file_path = cls._request_file_path()
            if file_path is None:
                logger.info("No file path provided.")
                return

        dialog = (
            cls._show_large_file_loading_dialog(file_path)
            if cls._get_file_size(file_path) > cls.LARGE_FILE_THRESHOLD_MB
            else None
        )

        def done(vardaRaster: VardaRaster) -> None:
            if dialog is not None:
                dialog.close()
            logger.info(f"Done loading image: {file_path}")
            if on_success_callback:
                on_success_callback(vardaRaster)

        def failed(error: Exception) -> None:
            if dialog is not None:
                dialog.close()
            if on_failure_callback:
                on_failure_callback(str(error))
            else:
                cls._show_error_message(f"Failed to load {file_path}:\n{error}")

        runInBackground(
            lambda: loadRaster(file_path),
            done,
            failed,
            description=f"Loading {file_path}",
        )

    @classmethod
    def load_images(
        cls, file_paths=None, on_success_callback=None, on_failure_callback=None
    ):
        """Load one or more images.

        If file_paths is None, prompts the user to select one or more files.
        Each file is loaded independently in the background, so the callbacks
        are invoked once per file (on_success_callback receives a VardaRaster,
        on_failure_callback receives an error message string).
        """
        if file_paths is None:
            file_paths = cls._request_file_paths()
            if not file_paths:
                logger.info("No file paths provided.")
                return

        for file_path in file_paths:
            cls.load_image_data(
                file_path=file_path,
                on_success_callback=on_success_callback,
                on_failure_callback=on_failure_callback,
            )

    @staticmethod
    def _get_file_size(filePath) -> float:
        """File size in megabytes."""
        try:
            return Path(filePath).stat().st_size / (1024 * 1024)
        except OSError:
            return 0

    @classmethod
    def _show_large_file_loading_dialog(cls, filePath) -> BusyDialog:
        """Say what's happening when a large file takes a while to open."""
        dialog = BusyDialog(
            f"Loading large file ({cls._get_file_size(filePath):.1f} MB)…\n"
            f"{Path(filePath).name}",
            title="Loading Large File",
            parent=QApplication.activeWindow(),
        )
        dialog.showAfter(1000)
        return dialog

    @staticmethod
    def _show_error_message(message):
        """Display an error message to the user."""
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("Error Loading Image")
        msg_box.setText(message)
        msg_box.exec()

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


def loadRaster(filePath: str) -> VardaRaster:
    """Open ``filePath`` as a VardaRaster: in memory when it fits comfortably,
    otherwise read on demand. Blocks; ``ImageLoadingService`` runs it in the
    background."""
    logger.info(f"Loading image: {filePath}")
    return VardaRaster.fromDataSource(wrapForReading(openDataSource(filePath)))
