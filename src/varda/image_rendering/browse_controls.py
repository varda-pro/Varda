"""The render settings' browse products: the picker (CRISM's standard set,
the user's own, and New, Edit and Delete for those), the readout of the
channels shown, and the dialog that defines a custom product. Custom
products are kept in QSettings as ``asText`` strings."""

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QStandardItem, QStandardItemModel
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from varda.analysis.browse_products import (
    CRISM_STANDARD,
    BrowseProduct,
    channelProblem,
    channelSource,
    parameterName,
    parseProduct,
    productProblem,
)
from varda.analysis.hypyrameter_adapter import (
    HYPYRAMETER_AVAILABLE,
    parameterDescription,
    validParameterNames,
)
from varda.common.entities import VardaRaster
from varda.common.parameter_widgets import connectWhileAlive
from varda.common.ui import ElidingComboBox, MutedLabel
from varda.image_rendering.browse_composites import COMPOSITES

SETTINGS_KEY = "display/customBrowseProducts"
BANDS_LABEL = "None (choose bands)"
PRODUCT_ROLE = Qt.ItemDataRole.UserRole + 1
CHANNELS = ("Red", "Green", "Blue")


def customProducts(settings: QSettings | None = None) -> list[BrowseProduct]:
    value = (settings or QSettings()).value(SETTINGS_KEY, [])
    texts = [value] if isinstance(value, str) else list(value or [])
    products = []
    for text in texts:
        try:
            products.append(parseProduct(str(text)))
        except ValueError:
            continue  # written by a newer Varda, say
    return products


def saveCustomProducts(
    products: list[BrowseProduct], settings: QSettings | None = None
) -> None:
    (settings or QSettings()).setValue(SETTINGS_KEY, [p.asText() for p in products])


def channelTip(channel: str, image: VardaRaster) -> str:
    """What a channel is, and what it reads on this image."""
    centers = image.bandModel.centers
    if centers is None:
        return channel
    problem = channelProblem(channel, centers)
    if problem:
        return problem
    name = parameterName(channel)
    if name is not None:
        return f"{channel}: {parameterDescription(name)}"
    return f"{channel}: {channelSource(channel, centers)}"


class BrowseProductPicker(QWidget):
    """Which browse product the settings show, or none (the bands)."""

    def __init__(self, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.image: VardaRaster = settings.image
        self.combo = ElidingComboBox(self, minimumCharacters=14)
        self.combo.setToolTip(
            "Show three spectral parameters or reflectances as red, green and "
            "blue: CRISM's standard browse products, or your own. In their "
            "names, VNIR (visible and near-infrared) and IR (infrared) say "
            "which part of the spectrum they show"
        )
        self.model = QStandardItemModel(self)
        self.combo.setModel(self.model)
        self.menuButton = QPushButton("Custom", self)
        self.menuButton.setToolTip("Define, edit or delete your own browse products")
        self.menu = QMenu(self.menuButton)
        self.menuButton.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._fillMenu)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.menuButton)

        self._fill()
        self.combo.activated.connect(self._onActivated)
        connectWhileAlive(settings.browse.sigParameterChanged, self._showCurrent, self)

    def _fill(self) -> None:
        self.model.clear()
        centers = self.image.bandModel.centers
        self.model.appendRow(self._item(BANDS_LABEL, None))
        self._heading("CRISM standard products")
        for product in CRISM_STANDARD:
            self.model.appendRow(self._productItem(product, centers))
        custom = customProducts()
        if custom:
            self._heading("Custom")
            for product in custom:
                self.model.appendRow(self._productItem(product, centers))
        self._showCurrent()

    def _heading(self, text: str) -> None:
        item = QStandardItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.model.appendRow(item)

    def _item(self, text: str, product: BrowseProduct | None) -> QStandardItem:
        item = QStandardItem(text)
        item.setData(None if product is None else product.asText(), PRODUCT_ROLE)
        return item

    def _productItem(self, product: BrowseProduct, centers) -> QStandardItem:
        item = self._item(product.label, product)
        problem = productProblem(product, centers)
        channels = ", ".join(product.channels)
        if problem:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(f"{channels}\nNot available for this image: {problem}")
        else:
            item.setToolTip(channels)
        return item

    def _textAt(self, row: int) -> str | None:
        return self.model.index(row, 0).data(PRODUCT_ROLE)

    def _rowOf(self, text: str | None) -> int:
        for row in range(self.model.rowCount()):
            if self._textAt(row) == text:
                return row
        return -1

    def _showCurrent(self, *args) -> None:
        text = self.settings.browse.get() or None
        row = self._rowOf(text)
        if row < 0 and text is not None:  # from a session, say: list it
            product = self.settings.browseProduct()
            if product is not None:
                self.model.appendRow(
                    self._productItem(product, self.image.bandModel.centers)
                )
                row = self.model.rowCount() - 1
        self.combo.setCurrentIndex(max(row, 0))

    def _onActivated(self, row: int) -> None:
        text = self._textAt(row)
        self.settings.showBrowseProduct(parseProduct(text) if text else None)

    def _current(self) -> BrowseProduct | None:
        product = self.settings.browseProduct()
        return None if product is None or product.standard else product

    def _fillMenu(self) -> None:
        self.menu.clear()
        self.menu.addAction("New Browse Product…", self.newProduct)
        current = self._current()
        if current is not None:
            self.menu.addAction(f"Edit {current.name}…", self.editProduct)
            self.menu.addAction(f"Delete {current.name}", self.deleteProduct)

    def newProduct(self) -> None:
        self._edit(None)

    def editProduct(self) -> None:
        self._edit(self._current())

    def deleteProduct(self) -> None:
        current = self._current()
        saveCustomProducts([p for p in customProducts() if p != current])
        self.settings.showBrowseProduct(None)
        self._fill()

    def _edit(self, product: BrowseProduct | None) -> None:
        dialog = CustomProductDialog(self.image, product, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        made = dialog.product()
        products = [p for p in customProducts() if p not in (product, made)]
        products = [p for p in products if p.name != made.name] + [made]
        saveCustomProducts(products)
        self._fill()
        self.settings.showBrowseProduct(made)


class BrowseChannels(QWidget):
    """The channels of the product shown, and how computing it is going."""

    def __init__(self, settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.image: VardaRaster = settings.image
        self.rows = [QLabel(self) for _ in CHANNELS]
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        for name, row in zip(CHANNELS, self.rows):
            row.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(f"{name}:", row)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        self.status = MutedLabel(parent=self)
        self.status.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        connectWhileAlive(settings.browse.sigParameterChanged, self._show, self)
        COMPOSITES.sigReady.connect(self._onReady)
        COMPOSITES.sigProgress.connect(self._onProgress)
        COMPOSITES.sigFailed.connect(self._onFailed)
        self._show()

    def _show(self, *args) -> None:
        product = self.settings.browseProduct()
        if product is None:
            return
        for channel, row in zip(product.channels, self.rows):
            row.setText(channel)
            row.setToolTip(channelTip(channel, self.image))
        error = COMPOSITES.error(self.image, product)
        computing = not error and COMPOSITES.get(self.image, product) is None
        self.progress.setVisible(computing)
        if computing:
            self.progress.setValue(0)
            self.status.setText(f"Computing {product.name}…")
        else:
            self.status.setText(error or "Each channel is stretched to its range.")
            self.status.setToolTip(
                "" if error else "Min-Max (Manual) under Stretch holds the ranges"
            )

    def _mine(self, image: VardaRaster, text: str) -> bool:
        return image is self.image and text == self.settings.browse.get()

    def _onReady(self, image: VardaRaster, text: str) -> None:
        if self._mine(image, text):
            self._show()

    def _onProgress(self, image: VardaRaster, text: str, percent: int, _message: str):
        product = self.settings.browseProduct()
        if self._mine(image, text) and product is not None:
            self.progress.setVisible(True)
            self.progress.setValue(percent)

    def _onFailed(self, image: VardaRaster, text: str, error: str) -> None:
        if self._mine(image, text):
            self._show()


class CustomProductDialog(QDialog):
    """A name and three channels: each a reflectance, R<nm>, or one of the
    spectral parameters this image supports."""

    def __init__(
        self,
        image: VardaRaster,
        product: BrowseProduct | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit Browse Product" if product else "New Browse Product")
        self.setMinimumWidth(380)
        self.image = image
        centers = image.bandModel.centers
        choices = (
            validParameterNames(centers)
            if HYPYRAMETER_AVAILABLE and centers is not None
            else []
        )
        self.name = QLineEdit(product.name if product else "")
        self.name.setPlaceholderText("e.g. My olivine")
        self.channels: list[QComboBox] = []
        form = QFormLayout()
        form.addRow("Name:", self.name)
        for i, label in enumerate(CHANNELS):
            box = QComboBox()
            box.setEditable(True)
            box.addItems(choices)
            box.setCurrentText(product.channels[i] if product else "")
            line = box.lineEdit()
            assert line is not None
            line.setPlaceholderText("a parameter, or R<nm> such as R1330")
            box.currentTextChanged.connect(self._check)
            self.channels.append(box)
            form.addRow(f"{label}:", box)
        self.problem = MutedLabel(parent=self)
        self.problem.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.name.textChanged.connect(self._check)
        intro = QLabel(
            "Choose what red, green and blue show: a spectral parameter "
            "(computed with HyPyRameter), or the reflectance at a wavelength "
            "in nm, written R<nm> (the median of the five nearest bands)."
        )
        intro.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.problem)
        layout.addWidget(self.buttons)
        self._check()

    def product(self) -> BrowseProduct:
        red, green, blue = (box.currentText().strip() for box in self.channels)
        return BrowseProduct(self.name.text().strip(), (red, green, blue))

    def problemText(self) -> str:
        product = self.product()
        if not product.name:
            return "Give the product a name."
        if "=" in product.name or any("," in c for c in product.channels):
            return "Names and channels can't contain '=' or ','."
        if not all(product.channels):
            return "Choose all three channels."
        if any(s.name == product.name for s in CRISM_STANDARD):
            return f"{product.name} is a CRISM standard product; choose another name."
        return productProblem(product, self.image.bandModel.centers)

    def _check(self, *args) -> None:
        problem = self.problemText()
        self.problem.setText(problem)
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save is not None
        save.setEnabled(not problem)
