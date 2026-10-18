"""Choose a CRISM geometry (IN or DDR/DE) file by hand, for an image whose
companion isn't found automatically (an IOVF result, say, whose name no longer
matches the PDS pattern). The file is checked (``checkGeometryFile``), then
set as the image's override, so every feature that reads the geometry uses it."""

from __future__ import annotations

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QPushButton, QWidget

from varda.common.entities import VardaRaster
from varda.image_loading.crism_geometry import (
    COLUMNS,
    checkGeometryFile,
    geometryDataFile,
    geometryOverride,
    geometryStartFolder,
    setGeometryOverride,
)

GEOMETRY_FILE_FILTER = (
    "CRISM geometry (*.img *.IMG *.hdr *.HDR *.tif *.TIF);;All files (*)"
)
CHOOSE_TEXT = "Choose Geometry File…"
CHOOSE_TOOLTIP = (
    "Point Varda to this image's CRISM IN file (…_in…, with the detector "
    "columns) when it isn't found beside the image or the file it was made from"
)


def chooseGeometryFile(
    image: VardaRaster, need: str = COLUMNS, parent: QWidget | None = None
) -> bool:
    """Ask for ``image``'s geometry file (the dialog opens in its folder),
    check it for ``need`` and set it as the image's override. False when
    canceled or refused (the refusal says why)."""
    current = geometryOverride(image, need)
    path, _ = QFileDialog.getOpenFileName(
        parent,
        f"CRISM geometry file for {image.name}",
        current or geometryStartFolder(image),
        GEOMETRY_FILE_FILTER,
    )
    return bool(path) and useGeometryFile(image, path, need, parent)


def useGeometryFile(
    image: VardaRaster, path: str, need: str = COLUMNS, parent: QWidget | None = None
) -> bool:
    """Set ``path`` as ``image``'s geometry file if it serves ``need``;
    otherwise say why and leave the image as it was."""
    path = geometryDataFile(path)
    problem = checkGeometryFile(path, image, need)
    if problem:
        QMessageBox.warning(parent, "Geometry file not used", problem)
        return False
    setGeometryOverride(image, need, path)
    return True


def chooseGeometryButton(
    image: VardaRaster, need: str = COLUMNS, parent: QWidget | None = None
) -> QPushButton:
    """A button that runs :func:`chooseGeometryFile` for ``image``."""
    button = QPushButton(CHOOSE_TEXT, parent)
    button.setToolTip(CHOOSE_TOOLTIP)
    button.clicked.connect(lambda: chooseGeometryFile(image, need, button.window()))
    return button
