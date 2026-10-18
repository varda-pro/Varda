"""Qt widgets for editing parameters (``varda.common.parameter``).

Each widget class is registered for the parameter type it edits; build one
with ``parameterWidget(param)``, or a whole form with ``groupWidget(group)``.
Widgets subscribe to their parameter only while they exist (see
``connectWhileAlive``), so a closed dialog's widgets are never called.
"""

from __future__ import annotations

import inspect
import os
import weakref
from collections.abc import Callable, Iterable

from psygnal import SignalInstance
from PyQt6 import sip
from PyQt6.QtCore import QObject, QSignalBlocker, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from varda.common.entities import Color, VardaRaster
from varda.common.parameter import (
    BoolParameter,
    ColorParameter,
    EnumParameter,
    FilePathParameter,
    FloatParameter,
    ImageParameter,
    IntParameter,
    MultiChoiceParameter,
    Parameter,
    ParameterGroup,
    StringParameter,
    Vec2Parameter,
    parsePositions,
)
from varda.common.ui import (
    FloatSlider,
    HBoxBuilder,
    SliderBuilder,
    SpinBoxBuilder,
    VBoxBuilder,
    notListedLabel,
)
from varda.common.vec2 import Vec2

type WidgetFactory = Callable[..., QWidget]

_factories: dict[type, WidgetFactory] = {}


def widgetFor[F: WidgetFactory](paramType: type[Parameter]) -> Callable[[F], F]:
    """Register a widget class (called with ``(param, parent)``) as the editor
    for ``paramType`` and its subclasses."""

    def register(factory: F) -> F:
        _factories[paramType] = factory
        return factory

    return register


def parameterWidget(param: Parameter, parent: QWidget | None = None) -> QWidget:
    """The editing widget for ``param``."""
    for cls in type(param).__mro__:
        if cls in _factories:
            return _factories[cls](param, parent)
    raise TypeError(f"No widget is registered for {type(param).__name__}")


def formWidget(
    params: Iterable[Parameter | ParameterGroup], parent: QWidget | None = None
) -> QWidget:
    """A labeled form editing ``params``; nested groups are flattened in."""
    formLayout = QFormLayout()
    formLayout.setContentsMargins(0, 0, 0, 0)
    formLayout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
    formLayout.setFieldGrowthPolicy(formLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    def addRows(items: Iterable[Parameter | ParameterGroup]) -> None:
        for param in items:
            if isinstance(param, ParameterGroup):
                addRows(param.params.values())
                continue
            label = QLabel(param.name)
            widget = parameterWidget(param)
            if param.description is not None:
                label.setToolTip(param.description)
                widget.setToolTip(param.description)
            formLayout.addRow(label, widget)

    addRows(params)
    widget = QWidget(parent)
    widget.setLayout(formLayout)
    return widget


def groupWidget(group: ParameterGroup, parent: QWidget | None = None) -> QWidget:
    """A labeled form editing every parameter in ``group``."""
    return formWidget(group.params.values(), parent)


def connectWhileAlive(signal: SignalInstance, slot: Callable, owner: QObject) -> None:
    """Connect ``slot`` to a parameter signal for as long as ``owner`` exists.

    Parameters often outlive the widgets showing them; once ``owner`` is
    deleted the connection removes itself instead of calling into it. A bound
    method is held weakly, so the connection never keeps its widget alive."""
    ownerRef = weakref.ref(owner)
    target = weakref.WeakMethod(slot) if inspect.ismethod(slot) else lambda: slot

    def call(*args) -> None:
        alive = ownerRef()
        function = target()
        if alive is None or sip.isdeleted(alive) or function is None:
            signal.disconnect(call)
            return
        function(*args)

    signal.connect(call)


def paramLayoutDefault() -> HBoxBuilder:
    return HBoxBuilder(
        alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, margins=0
    )


@widgetFor(IntParameter)
class IntParameterWidget(QWidget):
    def __init__(self, param: IntParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        self.spinBox = (
            SpinBoxBuilder(self)
            .sizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            .minimumSize(0, 30)  # as wide as its range needs (0: from its hint)
            .range(param.range if param.range is not None else (-100000, 100000))
            .default(param.get())
            .binding(self.valueChanged)
            .build()
        )
        self.spinBox.setSingleStep(param.step)
        self.unitLabel = QLabel(param.units) if param.units is not None else None
        if param.range is not None:
            self.slider = (
                SliderBuilder()
                .orientation(Qt.Orientation.Horizontal)
                .range(param.range)
                .default(param.get())
                .binding(self.valueChanged)
                .build()
            )
            self.slider.setSingleStep(param.step)
        else:
            self.slider = None
        self.setLayout(
            paramLayoutDefault()
            .withWidget(self.spinBox)
            .withWidget(self.unitLabel)
            .withWidget(self.slider)
        )
        connectWhileAlive(param.sigRangeChanged, self.onRangeChanged, self)
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def onRangeChanged(self, range: tuple[int, int]) -> None:
        with QSignalBlocker(self.spinBox):
            self.spinBox.setRange(*range)
            self.spinBox.setValue(self.param.get())
        if self.slider is not None:
            with QSignalBlocker(self.slider):
                self.slider.setRange(*range)
                self.slider.setValue(self.param.get())

    def valueChanged(self, value: int) -> None:
        self.onParamChanged(value)
        self.param.set(value)

    def onParamChanged(self, value: int) -> None:
        if self.spinBox.value() != value:
            with QSignalBlocker(self.spinBox):
                self.spinBox.setValue(value)
        if self.slider is not None and self.slider.value() != value:
            with QSignalBlocker(self.slider):
                self.slider.setValue(value)


@widgetFor(FloatParameter)
class FloatParameterWidget(QWidget):
    def __init__(self, param: FloatParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        paramLayout = paramLayoutDefault()

        self.spinBox = QDoubleSpinBox(parent=self)
        # decimals first: the spin box rounds its range and value to them
        if param.decimals is not None:
            self.spinBox.setDecimals(param.decimals)
        if param.range is not None:
            self.spinBox.setRange(param.range[0], param.range[1])
        else:
            self.spinBox.setRange(-100000.0, 100000.0)
        if param.step is not None:
            self.spinBox.setSingleStep(param.step)
        self.spinBox.setValue(param.get())
        self.spinBox.valueChanged.connect(self.param.set)
        paramLayout.addWidget(self.spinBox)

        if param.units is not None:
            paramLayout.addWidget(QLabel(param.units))

        if param.range is not None and param.showSlider:
            self.slider = FloatSlider(parent=self)
            self.slider.setOrientation(Qt.Orientation.Horizontal)
            self.slider.setRange(param.range[0], param.range[1])
            self.slider.setValue(param.get())
            self.slider.sigFloatValueChanged.connect(self.param.set)
            paramLayout.addWidget(self.slider)
        else:
            self.slider = None
        self.setLayout(paramLayout)
        # setting the parameter calls onParamChanged, which syncs both controls
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def onParamChanged(self, value: float) -> None:
        if self.spinBox.value() != value:
            with QSignalBlocker(self.spinBox):
                self.spinBox.setValue(value)
        if self.slider is not None and self.slider.value() != value:
            with QSignalBlocker(self.slider):
                self.slider.setValue(value)


@widgetFor(Vec2Parameter)
class Vec2ParameterWidget(QWidget):
    def __init__(self, param: Vec2Parameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        paramLayout = paramLayoutDefault()
        self.xSpinBox = self._makeSpinBox(param.range[0], param.get().x)
        self.xSpinBox.valueChanged.connect(self.onXChanged)
        paramLayout.addWidget(QLabel(param.valueNames[0]))
        paramLayout.addWidget(self.xSpinBox)

        self.ySpinBox = self._makeSpinBox(param.range[1], param.get().y)
        self.ySpinBox.valueChanged.connect(self.onYChanged)
        paramLayout.addWidget(QLabel(param.valueNames[1]))
        paramLayout.addWidget(self.ySpinBox)
        self.setLayout(paramLayout)
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def _makeSpinBox(self, range: Vec2, value: float) -> QDoubleSpinBox:
        spinBox = QDoubleSpinBox(parent=self)
        # decimals first: the spin box rounds its range and value to them
        if self.param.decimals is not None:
            spinBox.setDecimals(self.param.decimals)
        spinBox.setRange(range.x, range.y)
        if self.param.step is not None:
            spinBox.setSingleStep(self.param.step)
        spinBox.setValue(value)
        return spinBox

    def onXChanged(self, value: float) -> None:
        vec = self.param.get()
        vec.x = value
        self.param.set(vec)

    def onYChanged(self, value: float) -> None:
        vec = self.param.get()
        vec.y = value
        self.param.set(vec)

    def onParamChanged(self, value: Vec2) -> None:
        if self.xSpinBox.value() != value.x:
            with QSignalBlocker(self.xSpinBox):
                self.xSpinBox.setValue(value.x)
        if self.ySpinBox.value() != value.y:
            with QSignalBlocker(self.ySpinBox):
                self.ySpinBox.setValue(value.y)


@widgetFor(StringParameter)
class StringParameterWidget(QWidget):
    def __init__(self, param: StringParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        self.lineEdit = QLineEdit(self)
        self.lineEdit.setText(param.get())
        self.lineEdit.textChanged.connect(self.param.set)
        self.setLayout(paramLayoutDefault().withWidget(self.lineEdit))
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def onParamChanged(self, text: str) -> None:
        if self.lineEdit.text() != text:
            with QSignalBlocker(self.lineEdit):
                self.lineEdit.setText(text)


@widgetFor(FilePathParameter)
class FilePathParameterWidget(QWidget):
    """The path (typed, taken when editing finishes, or cleared with the
    field's clear button) and Browse…, which opens in the parameter's
    ``startFolder``."""

    def __init__(self, param: FilePathParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        self.lineEdit = QLineEdit(self)
        self.lineEdit.setText(param.get())
        self.lineEdit.setPlaceholderText(param.placeholder)
        self.lineEdit.setClearButtonEnabled(True)
        self.lineEdit.editingFinished.connect(
            lambda: self._take(self.lineEdit.text().strip())
        )
        self.lineEdit.textChanged.connect(self._onTextChanged)
        self.browseButton = QPushButton("Browse…", self)
        self.browseButton.clicked.connect(self._browse)
        self.setLayout(
            paramLayoutDefault().withWidget(self.lineEdit).withWidget(self.browseButton)
        )
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def _onTextChanged(self, text: str) -> None:
        if not text:  # cleared
            self._take("")

    def _take(self, path: str) -> None:
        if path != self.param.get():
            self.param.set(path)

    def _browse(self) -> None:
        current = self.param.get()
        start = os.path.dirname(current) if current else self.param.startFolder
        path, _ = QFileDialog.getOpenFileName(
            self, self.param.name, start, self.param.fileFilter
        )
        if path:
            self._take(path)

    def onParamChanged(self, path: str) -> None:
        if self.lineEdit.text() != path:
            with QSignalBlocker(self.lineEdit):
                self.lineEdit.setText(path)


@widgetFor(BoolParameter)
class BoolParameterWidget(QWidget):
    def __init__(self, param: BoolParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        self.checkBox = QCheckBox(self)
        self.checkBox.setChecked(param.get())
        self.checkBox.checkStateChanged.connect(self.stateChanged)
        self.setLayout(paramLayoutDefault().withWidget(self.checkBox))
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def stateChanged(self, state: Qt.CheckState) -> None:
        self.param.set(state == Qt.CheckState.Checked)

    def onParamChanged(self, isChecked: bool) -> None:
        with QSignalBlocker(self.checkBox):
            self.checkBox.setChecked(isChecked)


@widgetFor(EnumParameter)
class EnumParameterWidget(QWidget):
    def __init__(self, param: EnumParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        self.comboBox = QComboBox(self)
        self.comboBox.addItems(param.enumNames)
        self.refreshComboBox()
        self.comboBox.currentIndexChanged.connect(self.enumSelectionChanged)
        self.setLayout(paramLayoutDefault().withWidget(self.comboBox))
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def enumSelectionChanged(self, index: int) -> None:
        self.param.set(list(self.param.enumType)[index])

    def refreshComboBox(self) -> None:
        self.comboBox.setCurrentIndex(list(self.param.enumType).index(self.param.value))

    def onParamChanged(self, _value) -> None:
        with QSignalBlocker(self.comboBox):
            self.refreshComboBox()


@widgetFor(MultiChoiceParameter)
class MultiChoiceParameterWidget(QWidget):
    """A checkable list of the choices with All / None buttons."""

    LIST_HEIGHT = 180

    def __init__(self, param: MultiChoiceParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param

        self.listWidget = QListWidget(self)
        self.listWidget.setMaximumHeight(self.LIST_HEIGHT)
        self.allButton = QPushButton("All", self)
        self.noneButton = QPushButton("None", self)
        self._rebuild()

        # Select by position: "2-15, 22" (1-based, matching the list order)
        self.rangeEdit = QLineEdit(self)
        self.rangeEdit.setPlaceholderText("e.g. 3-19, 22")
        self.rangeEdit.setToolTip(
            "Select entries by their position in the list, e.g. 3-19, 22"
        )
        self.rangeButton = QPushButton("Select", self)

        self.listWidget.itemChanged.connect(self._itemChanged)
        self.allButton.clicked.connect(param.selectAll)
        self.noneButton.clicked.connect(param.selectNone)
        self.rangeButton.clicked.connect(self._selectRange)
        self.rangeEdit.returnPressed.connect(self._selectRange)
        connectWhileAlive(param.sigChoicesChanged, self.onChoicesChanged, self)
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

        self.setLayout(
            VBoxBuilder(margins=0)
            .withWidget(self.listWidget)
            .withLayout(
                HBoxBuilder(margins=0)
                .withWidget(self.allButton)
                .withWidget(self.noneButton)
                .withWidget(self.rangeEdit)
                .withWidget(self.rangeButton)
            )
        )

    def onChoicesChanged(self, _choices: list[str]) -> None:
        self._rebuild()

    def _selectRange(self) -> None:
        positions = parsePositions(self.rangeEdit.text(), len(self.param.choices))
        self.param.set([self.param.choices[i] for i in positions])

    def _rebuild(self) -> None:
        selected = set(self.param.get())
        with QSignalBlocker(self.listWidget):
            self.listWidget.clear()
            for choice in self.param.choices:
                item = QListWidgetItem(choice)
                item.setToolTip(self.param.tooltips.get(choice, ""))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(_checkState(choice in selected))
                self.listWidget.addItem(item)

    def _items(self) -> list[QListWidgetItem]:
        return [
            item
            for i in range(self.listWidget.count())
            if (item := self.listWidget.item(i)) is not None
        ]

    def _itemChanged(self, _item: QListWidgetItem) -> None:
        self.param.set(
            [
                item.text()
                for item in self._items()
                if item.checkState() == Qt.CheckState.Checked
            ]
        )

    def onParamChanged(self, value: list[str]) -> None:
        selected = set(value)
        with QSignalBlocker(self.listWidget):
            for item in self._items():
                item.setCheckState(_checkState(item.text() in selected))


def _checkState(checked: bool) -> Qt.CheckState:
    return Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked


@widgetFor(ColorParameter)
class ColorParameterWidget(QWidget):
    def __init__(self, param: ColorParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        self.colorButton = QPushButton(self)
        self.colorButton.setFixedSize(40, 40)
        self.updateColorDisplay()
        self.colorButton.clicked.connect(self.openColorDialog)
        self.setLayout(paramLayoutDefault().withWidget(self.colorButton))
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def updateColorDisplay(self) -> None:
        self.colorButton.setStyleSheet(
            f"background-color: {self.param.get().toQColor().name()}; "
            "border: 1px solid #000000; border-radius: 4px;"
        )

    def onParamChanged(self, _color: Color) -> None:
        self.updateColorDisplay()

    def openColorDialog(self) -> None:
        color = QColorDialog.getColor(self.param.get().toQColor(), self, "Select Color")
        if color.isValid():
            self.param.set(Color.fromQColor(color))


@widgetFor(ImageParameter)
class ImageParameterWidget(QWidget):
    def __init__(self, param: ImageParameter, parent: QWidget | None = None):
        super().__init__(parent)
        self.param = param
        images = list(param.imageProvider())
        assert images or param.optional, (
            "Image Parameter requires at least 1 available image!"
        )
        # an optional parameter's first choice is None
        self.imageList: list[VardaRaster | None] = (
            [None, *images] if param.optional else list(images)
        )
        self.comboBox = QComboBox(self)
        self.comboBox.addItems(
            [
                param.noneLabel if image is None else image.name
                for image in self.imageList
            ]
        )
        # Show the parameter's current value (it may have been set before
        # the widget existed) before wiring user changes back to it.
        self._showValue(param.get())
        self.comboBox.currentIndexChanged.connect(self.imageSelectionChanged)
        # the images left out of the dropdown, and why
        self.notListed = notListedLabel(param.explain(), self)
        self.setLayout(
            VBoxBuilder(margins=0)
            .withLayout(paramLayoutDefault().withWidget(self.comboBox))
            .withWidget(self.notListed)
        )
        connectWhileAlive(param.sigParameterChanged, self.onParamChanged, self)

    def imageSelectionChanged(self, index: int) -> None:
        self.param.setOrClear(self.imageList[index])

    def onParamChanged(self, value: VardaRaster | None) -> None:
        with QSignalBlocker(self.comboBox):
            self._showValue(value)

    def _showValue(self, value: VardaRaster | None) -> None:
        # identity, not equality: two rasters are distinct choices even if equal
        index = next(
            (i for i, image in enumerate(self.imageList) if image is value), -1
        )
        if index >= 0 and index != self.comboBox.currentIndex():
            self.comboBox.setCurrentIndex(index)
