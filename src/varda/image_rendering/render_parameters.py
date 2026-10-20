from __future__ import annotations

import pyqtgraph as pg
from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtWidgets import (
    QComboBox,
    QMessageBox,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from varda.common.spectral_axis import bandWavelengthLabels
from varda.common.entities import VardaRaster
from varda.common.parameter import Parameter, ParameterGroup, copyValues
from varda.common.parameter_widgets import (
    connectWhileAlive,
    groupWidget,
    paramLayoutDefault,
    widgetFor,
)
from varda.image_rendering.stretch_algorithms import (
    StretchAlgorithm,
    stretchAlgorithmRegistry,
)


class BandParameter(Parameter[int]):
    """Selects a band by index. The widget lists the image's wavelengths.

    Image-aware: ``setImage()`` must be called before building the widget (mirrors
    ``ImageParameter.setProvider``).
    """

    def __init__(
        self,
        name: str,
        default: int = 0,
        description: str | None = None,
    ):
        super().__init__(name, default, description)
        self.image: VardaRaster | None = None

    def setImage(self, image: VardaRaster) -> None:
        self.image = image
        if self.value >= image.bandCount:
            self.value = 0

    def clone(self) -> BandParameter:
        new = BandParameter(self.name, self.default, self.description)
        if self.image is not None:
            new.setImage(self.image)
        new.value = self.value
        return new


class ColorMapParameter(Parameter[pg.ColorMap]):
    """A pyqtgraph ColorMap, edited via a gradient widget."""

    def __init__(
        self,
        name: str,
        default: pg.ColorMap | None = None,
        description: str | None = None,
    ):
        if default is None:
            default = pg.ColorMap(None, color=[0.0, 1.0])  # simple black -> white map
        super().__init__(name, default, description)

    def clone(self) -> ColorMapParameter:
        return ColorMapParameter(self.name, self.default, self.description)


class StretchParameter(Parameter[StretchAlgorithm]):
    """Selects the active stretch algorithm from the registry.

    The value is the active ``StretchAlgorithm`` instance. One instance of every
    registered algorithm is built up-front and kept, so each option retains its own
    sub-parameters. The widget is a self-contained combo + stacked sub-form.
    """

    DEFAULT_NAME = "Min-Max (Auto Full Range)"
    MANUAL_NAME = (
        "Min-Max (Manual)"  # used by ImageRenderer's manual-stretch convenience methods
    )

    def __init__(self, name: str, description: str | None = None):
        self._instances: dict[str, StretchAlgorithm] = {
            n: cls() for n, cls in stretchAlgorithmRegistry.items()
        }
        default = self._instances.get(self.DEFAULT_NAME) or next(
            iter(self._instances.values())
        )
        super().__init__(name, default, description)
        # Keep each algorithm's ParameterGroup: those whose parameters() builds a
        # fresh group each call would otherwise lose their settings.
        self._paramGroups: dict[str, ParameterGroup] = {}
        for algName, instance in self._instances.items():
            group = instance.parameters()
            self._paramGroups[algName] = group
            group.sigParameterChanged.connect(
                lambda _, algName=algName: self._onSubParamChanged(algName)
            )

    def _onSubParamChanged(self, algName: str) -> None:
        # Only the active algorithm's settings change the rendering.
        if algName == self.nameOf(self.value):
            self.sigParameterChanged.emit(self.value)

    @property
    def current(self) -> StretchAlgorithm:
        return self.value

    @property
    def optionNames(self) -> list[str]:
        return list(self._instances.keys())

    @property
    def optionGroups(self) -> dict[str, ParameterGroup]:
        """Each algorithm's settings, by name."""
        return dict(self._paramGroups)

    def option(self, name: str) -> StretchAlgorithm:
        return self._instances[name]

    def nameOf(self, instance: StretchAlgorithm) -> str:
        for n, inst in self._instances.items():
            if inst is instance:
                return n
        raise ValueError("instance is not one of this parameter's algorithms")

    def selectByName(self, name: str) -> None:
        self.set(self._instances[name])

    def copyFrom(self, source: Parameter[StretchAlgorithm]) -> None:
        """Every algorithm's settings, then the same choice of algorithm (each
        parameter keeps its own algorithm instances)."""
        if not isinstance(source, StretchParameter):
            return
        for name, group in source._paramGroups.items():
            copyValues(group, self._paramGroups[name])
        self.selectByName(source.nameOf(source.current))

    def clone(self) -> StretchParameter:
        new = StretchParameter(self.name, self.description)
        new.selectByName(self.nameOf(self.value))
        return new


@widgetFor(BandParameter)
class BandParameterWidget(QWidget):
    def __init__(self, param: BandParameter, parent=None):
        super().__init__(parent)
        self.param = param
        connectWhileAlive(self.param.sigParameterChanged, self.onParamChanged, self)
        assert self.param.image is not None, (
            "BandParameter.setImage() must be called before building its widget"
        )
        self.comboBox = QComboBox(self)
        image = self.param.image
        labels = bandWavelengthLabels(image.bandModel)
        self.comboBox.addItems(
            labels if labels is not None else [str(w) for w in image.wavelengths]
        )
        self.comboBox.setCurrentIndex(self.param.get())
        self.comboBox.currentIndexChanged.connect(self._onSelectionChanged)

        layout = paramLayoutDefault()
        layout.addWidget(self.comboBox)
        self.setLayout(layout)

    def _onSelectionChanged(self, index: int) -> None:
        self.param.set(index)

    def onParamChanged(self, value: int) -> None:
        if self.comboBox.currentIndex() != value:
            with QSignalBlocker(self.comboBox):
                self.comboBox.setCurrentIndex(value)


@widgetFor(ColorMapParameter)
class ColorMapParameterWidget(QWidget):
    def __init__(self, param: ColorMapParameter, parent=None):
        super().__init__(parent)
        self.param = param
        connectWhileAlive(self.param.sigParameterChanged, self.onParamChanged, self)

        self.gradient = pg.GradientWidget()
        self.gradient.setColorMap(self.param.get())
        self.gradient.sigGradientChanged.connect(self._onGradientChanged)

        layout = paramLayoutDefault()
        layout.addWidget(self.gradient)
        self.setLayout(layout)

    def _onGradientChanged(self, item) -> None:
        try:
            colorMap = item.colorMap()  # raises NotImplementedError for HSV maps
        except NotImplementedError:
            QMessageBox.warning(
                self,
                "Unsupported Color Map",
                "HSV color maps are not supported yet.",
            )
            with QSignalBlocker(self.gradient):
                self.gradient.setColorMap(self.param.get())  # revert
            return
        self.param.set(colorMap)

    def onParamChanged(self, colorMap: pg.ColorMap) -> None:
        with QSignalBlocker(self.gradient):
            self.gradient.setColorMap(colorMap)


@widgetFor(StretchParameter)
class StretchParameterWidget(QWidget):
    def __init__(self, param: StretchParameter, parent=None):
        super().__init__(parent)
        self.param = param
        connectWhileAlive(self.param.sigParameterChanged, self.onParamChanged, self)

        self.comboBox = QComboBox(self)
        self.comboBox.addItems(self.param.optionNames)

        self.stack = QStackedLayout()
        self.stack.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        for name in self.param.optionNames:
            self.stack.addWidget(groupWidget(self.param._paramGroups[name]))

        self.comboBox.currentIndexChanged.connect(self._onComboChanged)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.comboBox)
        layout.addLayout(self.stack)
        self.setLayout(layout)

        self._syncToParam()

    def _onComboChanged(self, index: int) -> None:
        self.param.selectByName(self.param.optionNames[index])

    def _syncToParam(self) -> None:
        index = self.param.optionNames.index(self.param.nameOf(self.param.current))
        with QSignalBlocker(self.comboBox):
            self.comboBox.setCurrentIndex(index)
        self.stack.setCurrentIndex(index)

    def onParamChanged(self, value) -> None:
        self._syncToParam()
