"""Set Coordinate System: the CRS an image is read in, typed (an EPSG or
IAU_2015 code, WKT, or a PROJ string) or the same as another open image's,
described in words as it is typed. A CRS on another body than the image's
is warned about, not refused: the user may know better."""

from __future__ import annotations

from collections.abc import Sequence

from pyproj import CRS
from pyproj.exceptions import CRSError
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from varda.common.entities import VardaRaster
from varda.common.ui import ElidingComboBox, MutedLabel
from varda.geo.bodies import bodyNamed
from varda.geo.describe import describeCrs
from varda.geo.equivalence import canTransform
from varda.utilities.proj_lock import PROJ_LOCK

NOT_SAME_AS = "—"


def _wrapped(label: QLabel) -> QLabel:
    label.setWordWrap(True)
    return label


class SetCrsDialog(QDialog):
    """Choose the CRS ``image`` is read in; ``images`` are the open images
    offered under "Same as" (those with a CRS, other than ``image``)."""

    def __init__(
        self,
        image: VardaRaster,
        images: Sequence[VardaRaster] = (),
        parent: QWidget | None = None,
        text: str = "",
    ):
        """``text``: what the field starts with (an unreadable CRS to
        correct), on one line."""
        super().__init__(parent)
        self.image = image
        self.setWindowTitle(f"Set Coordinate System of {image.name}")
        self._others = [
            other for other in images if other is not image and other.crs is not None
        ]
        self.crsEdit = QLineEdit()
        self.crsEdit.setPlaceholderText("IAU_2015:49900, EPSG:4326, WKT or PROJ")
        self.crsEdit.setToolTip(
            "An authority code (IAU_2015, EPSG, ESRI), WKT, or a PROJ string; "
            "the image's map coordinates are then read in it, unchanged"
        )
        self.crsEdit.textChanged.connect(self._update)
        self.crsEdit.textEdited.connect(self._onTyped)
        self.sameAsBox = ElidingComboBox()
        self.sameAsBox.addItem(NOT_SAME_AS)
        for other in self._others:
            self.sameAsBox.addItem(other.name)
        self.sameAsBox.setToolTip("Use another open image's coordinate system")
        self.sameAsBox.currentIndexChanged.connect(self._onSameAs)
        self.descriptionLabel = _wrapped(QLabel())
        self.warningLabel = _wrapped(QLabel())
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.addRow("Coordinate system:", self.crsEdit)
        form.addRow("Same as:", self.sameAsBox)
        layout = QVBoxLayout(self)
        layout.addWidget(
            _wrapped(
                MutedLabel(
                    "The image's map coordinates (its pixel positions) stay as "
                    "they are; they are read in this coordinate system instead "
                    "of the file's. The file isn't changed."
                )
            )
        )
        layout.addLayout(form)
        layout.addWidget(self.descriptionLabel)
        layout.addWidget(self.warningLabel)
        layout.addStretch()
        layout.addWidget(self.buttons)
        self.setMinimumWidth(420)
        self.crsEdit.setText(" ".join(text.split("\n")))
        self._update()

    def _onSameAs(self, index: int) -> None:
        if index <= 0:
            return
        crs = self._others[index - 1].crs
        assert crs is not None
        with PROJ_LOCK:
            wkt = crs.to_wkt()
        self.crsEdit.setText(wkt)

    def _onTyped(self) -> None:
        self.sameAsBox.setCurrentIndex(0)  # no longer another image's

    def chosenWkt(self) -> str | None:
        """The chosen CRS as WKT; None when nothing readable is typed."""
        text = self.crsEdit.text().strip()
        if not text:
            return None
        with PROJ_LOCK:
            try:
                return CRS.from_user_input(text).to_wkt()
            except CRSError:
                return None

    def reason(self) -> str:
        """Why this CRS, for the image's ``crsFix`` record."""
        index = self.sameAsBox.currentIndex()
        if index > 0:
            return (
                f"The same as {self._others[index - 1].name} (Set coordinate system…)"
            )
        return "Chosen by hand (Set coordinate system…)"

    def _update(self) -> None:
        chosen = self.chosenWkt()
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok is not None
        ok.setEnabled(chosen is not None)
        self.warningLabel.setText("")
        if chosen is None:
            typed = bool(self.crsEdit.text().strip())
            self.descriptionLabel.setText(
                "Not a coordinate system Varda can read." if typed else ""
            )
            return
        image = self.image
        description = describeCrs(chosen, image.transform, image.width, image.height)
        self.descriptionLabel.setText(description.text)
        reference = self._imageCrs()
        if reference is None:
            return
        problem = canTransform(reference, chosen)
        if problem is not None:
            body = description.body or "that body"
            self.warningLabel.setText(
                f"⚠ {problem}. Use it only if you know the image is on {body}."
            )

    def _imageCrs(self) -> CRS | str | None:
        """What the image is on, to compare a choice with: its label's body
        (its IAU 2015 sphere) when the label names one, else its CRS."""
        hints = self.image.labelHints
        body = bodyNamed(hints.target) if hints is not None and hints.target else None
        if body is not None:
            return body.sphereCrs()
        return self.image.crs
