import sys
from enum import Enum, auto

from PyQt6.QtCore import pyqtSignal, QObject, Qt
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QStackedLayout,
    QApplication,
)
import numpy as np

from varda.common.parameter import (
    ParameterGroup,
    EnumParameter,
    FloatParameter,
    LabeledEnum,
)
from varda.common.entities import VardaRaster
from varda.common.vec2 import Vec2
from varda.utilities import debug
from varda.image_rendering.render_parameters import (
    BandParameter,
    ColorMapParameter,
    StretchParameter,
)
from varda.common.parameter_widgets import (
    connectWhileAlive,
    formWidget,
    groupWidget,
    parameterWidget,
)


class RenderMode(Enum):
    MONO = auto()
    RGB = auto()


class StretchSource(LabeledEnum):
    """Where the stretch and the histogram take their statistics."""

    FULL = "Full image"
    MAIN = "Main view"
    ZOOM = "Zoom view"


Window = tuple[slice, slice]  # rows, columns of the image
# A view's window that moves less than this (pixels) keeps the stretch as it
# is: re-deriving a view's region from another's can jitter by a pixel
WINDOW_TOLERANCE = 1


def _sameWindow(a: Window | None, b: Window | None) -> bool:
    if a is None or b is None:
        return a is b
    return all(
        abs(x.start - y.start) <= WINDOW_TOLERANCE
        and abs(x.stop - y.stop) <= WINDOW_TOLERANCE
        for x, y in zip(a, b)
    )


class RgbBandGroup(ParameterGroup):
    red = BandParameter("Red Band")
    green = BandParameter("Green Band")
    blue = BandParameter("Blue Band")


class MonoViewGroup(ParameterGroup):
    band = BandParameter("Band")
    colorMap = ColorMapParameter("Color Map")


class RendererSettings(ParameterGroup):
    mode = EnumParameter("Mode", RenderMode, RenderMode.MONO)
    rgb = RgbBandGroup()
    mono = MonoViewGroup()
    stretch = StretchParameter("Stretch Algorithm")
    stretchFrom = EnumParameter(
        "Stretch From",
        StretchSource,
        StretchSource.FULL,
        "Where the stretch and the histogram take their statistics: the whole "
        "image, or the part the main or zoom view shows",
    )
    opacity = FloatParameter(
        "Opacity", 1.0, (0.0, 1.0), "%", "Opacity of the rendered image."
    )

    def __init__(self, image: VardaRaster):
        super().__init__()
        self.image = image
        for bandParam in (self.rgb.red, self.rgb.green, self.rgb.blue, self.mono.band):
            bandParam.setImage(image)
        # seed band selections from the image's default bands
        defaultBands = image.defaultBands
        self.rgb.red.value = int(defaultBands[0])
        self.rgb.green.value = int(defaultBands[1])
        self.rgb.blue.value = int(defaultBands[2])
        self.mono.band.value = int(defaultBands[0])

    def clone(self) -> "RendererSettings":
        # RendererSettings needs the image at construction, unlike the base ParameterGroup
        # whose clone() calls self.__class__() with no image.
        return RendererSettings(self.image)


class ImageRenderer(QObject):
    sigShouldRefresh: pyqtSignal = pyqtSignal()

    def __init__(
        self,
        image: VardaRaster | None = None,
        settings: RendererSettings | None = None,
    ):
        super().__init__()
        if settings is not None:
            self.settings = settings
        elif image is not None:
            self.settings = RendererSettings(image)
        else:
            raise ValueError("Either image or settings must be provided.")
        self.image = self.settings.image
        # Three caches, cleared only as far as a change needs:
        # - the raw bands, re-read only when the band selection changes
        # - the 8-bit picture (and its no-data mask), redone for any other
        #   change except opacity
        # - the final RGBA, rebuilt from the 8-bit picture for opacity
        self.cachedRender = None
        self._stretchedData = None  # latest render post-stretch but pre-colormap
        self._rawBandData = None  # extracted band data with no processing applied
        self._rawKey: tuple | None = None
        self._rgb8: np.ndarray | None = None
        self._noData: np.ndarray | None = None
        self._lastOpacity = self.settings.opacity.get()
        # The part of the image each view shows, set by the views, and the
        # raw bands the statistics come from (cached, so its identity tells
        # the histogram when it changed)
        self._windows: dict[StretchSource, Window] = {}
        self._sample: np.ndarray | None = None
        self._sampleKey: tuple | None = None
        # Overlays leave no-data pixels see-through instead of opaque black
        self.transparentNoData = False
        self.settings.sigParameterChanged.connect(self._onSettingsChanged)
        self.image.sigMetadataChanged.connect(self._onImageEdited)

    def _onImageEdited(self) -> None:
        """The image's metadata changed (a no-data value, say): read again."""
        self._rawBandData = self._rgb8 = self._stretchedData = None
        self._rawKey = self._sample = self._sampleKey = None
        self.cachedRender = None
        self.sigShouldRefresh.emit()

    def _onSettingsChanged(self, *args) -> None:
        # Each signal is one parameter change; if opacity moved, that was it
        opacity = self.settings.opacity.get()
        if opacity != self._lastOpacity:
            self._lastOpacity = opacity
        else:
            self._rgb8 = None
            self._stretchedData = None
        self.cachedRender = None
        self.sigShouldRefresh.emit()

    def _bandKey(self) -> tuple:
        mode = self.settings.mode.get()
        if mode == RenderMode.MONO:
            return (mode, int(self.settings.mono.band.get()))
        rgb = self.settings.rgb
        return (mode, int(rgb.red.get()), int(rgb.green.get()), int(rgb.blue.get()))

    def _readBands(self) -> np.ndarray:
        """The selected bands as float with NaN for no-data, read once per
        band selection (read-only: stretches must not modify it)."""
        key = self._bandKey()
        if self._rawBandData is None or self._rawKey != key:
            raw = self.image.getBands(list(key[1:]))
            data = np.asarray(
                np.ma.filled(raw, np.nan) if np.ma.isMaskedArray(raw) else raw
            )
            data.setflags(write=False)
            self._rawBandData, self._rawKey = data, key
        return self._rawBandData

    def setStretchWindow(self, source: StretchSource, window: Window | None) -> None:
        """The part of the image a view shows; when the stretch comes from that
        view, it's computed again."""
        if _sameWindow(self._windows.get(source), window):
            return
        if window is None:
            self._windows.pop(source, None)
        else:
            self._windows[source] = window
        if self.settings.stretchFrom.get() is source:
            self._rgb8 = None
            self._stretchedData = None
            self.cachedRender = None
            self.sigShouldRefresh.emit()

    def getSampleData(self) -> np.ndarray:
        """The raw bands the stretch and the histogram take their statistics
        from: the whole image, or the window of the chosen view (the whole
        image while that view has none, or shows only no-data)."""
        data = self._readBands()
        window = self._windows.get(self.settings.stretchFrom.get())
        key = (id(data), window)
        if self._sample is None or self._sampleKey != key:
            sample = data if window is None else data[window]
            if sample.size == 0 or not np.isfinite(sample).any():
                sample = data
            self._sample, self._sampleKey = sample, key
        return self._sample

    def render(self):
        """
        Render the image with the current band and stretch settings.
        Returns: numpy ndarray with shape (height, width, 4) representing an RGBA image.
        """
        if self.cachedRender is not None:
            return self.cachedRender
        if self.image is None or self.settings is None:
            raise ValueError("Image and settings must be set before rendering.")
        if self._rgb8 is None:
            self._rgb8, self._noData = self._renderRgb8()
        alpha = np.full(
            (self._rgb8.shape[0], self._rgb8.shape[1], 1),
            int(self.settings.opacity.get() * 255),
            dtype=np.uint8,
        )
        if self.transparentNoData:
            alpha[self._noData] = 0
        rgba = np.concatenate((self._rgb8, alpha), axis=2)
        self.cachedRender = rgba
        return rgba

    def _renderRgb8(self) -> tuple[np.ndarray, np.ndarray]:
        """The stretched (and, in mono, color-mapped) 8-bit RGB picture and
        the (rows, cols) mask of pixels with no data in any channel."""
        mode = self.settings.mode.get()
        data = self.settings.stretch.current.apply(
            self._readBands(), self.getSampleData()
        )
        self._stretchedData = data
        noData = np.asarray(np.isnan(data).all(axis=2))
        # a new array (the stretch's output may be the read-only bands); clip
        # so values outside 0-1 saturate instead of wrapping in the 8-bit cast
        data = np.nan_to_num(np.clip(data, 0.0, 1.0), nan=0.0)

        if mode == RenderMode.MONO:
            data = np.squeeze(data, axis=2)  # back to 2D because ColorMap expects it
            lut = self.settings.mono.colorMap.get().getLookupTable(
                0, 1, 256, alpha=False
            )
            return np.asarray(lut[(data * 255).astype(np.uint8)]), noData
        return np.asarray((data * 255).astype(np.uint8)), noData

    def getStretchedData(self) -> np.ndarray:
        if self._stretchedData is None:
            self.render()
        assert self._stretchedData is not None
        return self._stretchedData

    def getRawBandData(self) -> np.ndarray:
        if self._rawBandData is None:
            self.render()
        assert self._rawBandData is not None
        return self._rawBandData

    def getMinMaxValues(self):
        if self.cachedRender is None:
            self.render()
        return self.settings.stretch.current.minMaxVals()

    def setManualStretch(self, lo: float, hi: float) -> None:
        """Switch to the manual stretch and set every channel to [lo, hi]."""
        manual = self.settings.stretch.option(StretchParameter.MANUAL_NAME)
        value = Vec2(float(lo), float(hi))
        # batch the param edits into a single refresh
        with self.settings.sigParameterChanged.blocked():
            manual.config.redStretch.set(value)
            manual.config.greenStretch.set(value)
            manual.config.blueStretch.set(value)
            self.settings.stretch.selectByName(StretchParameter.MANUAL_NAME)
        self.settings.sigParameterChanged.emit(self.settings)

    def setStretchMinMax(self, channel: int, lo: float, hi: float) -> None:
        """Set one channel's manual min/max (0=red, 1=green, 2=blue).

        When switching into the manual stretch, seed all channels from the current
        stretch's computed min/max so the other channels don't jump.
        """
        stretch = self.settings.stretch
        manual = stretch.option(StretchParameter.MANUAL_NAME)
        channelParams = [
            manual.config.redStretch,
            manual.config.greenStretch,
            manual.config.blueStretch,
        ]
        # batch all edits (seed + select + channel set) into a single refresh
        with self.settings.sigParameterChanged.blocked():
            if stretch.current is not manual:
                self.render()  # ensure the current stretch has computed its min/max
                seed = stretch.current.minMaxVals()
                if seed is not None:
                    mins = np.resize(
                        np.atleast_1d(np.asarray(seed[0], dtype=float)).ravel(), 3
                    )
                    maxs = np.resize(
                        np.atleast_1d(np.asarray(seed[1], dtype=float)).ravel(), 3
                    )
                    for i, param in enumerate(channelParams):
                        param.set(Vec2(float(mins[i]), float(maxs[i])))
                stretch.selectByName(StretchParameter.MANUAL_NAME)
            channelParams[channel].set(Vec2(float(lo), float(hi)))
        self.settings.sigParameterChanged.emit(self.settings)

    def getSettingsPanel(
        self, stretchFrom: bool = False, opacity: bool = True
    ) -> "RendererSettingsPanel":
        """The settings panel; ``stretchFrom`` shows Stretch From, for views
        that report their windows (``setStretchWindow``); ``opacity`` the
        opacity (left out where something else sets it)."""
        return RendererSettingsPanel(
            self.settings, stretchFrom=stretchFrom, opacity=opacity
        )


class RendererSettingsPanel(QWidget):
    """Panel for adjusting render settings, generated from the settings' parameters."""

    def __init__(
        self,
        settings: RendererSettings,
        parent=None,
        stretchFrom: bool = False,
        opacity: bool = True,
    ):
        super().__init__(parent)
        self.setWindowTitle("Image Render Settings")
        self.settings = settings

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.setSpacing(2)

        # Mode
        layout.addWidget(QLabel("Mode:"))
        layout.addWidget(parameterWidget(settings.mode, self))

        # Band / colormap area, swapped by the mode parameter
        self.bandStack = QStackedLayout()
        self.bandStack.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._rgbIndex = self.bandStack.addWidget(groupWidget(settings.rgb))
        self._monoIndex = self.bandStack.addWidget(groupWidget(settings.mono))
        layout.addLayout(self.bandStack)
        self._syncBandStack()
        connectWhileAlive(settings.mode.sigParameterChanged, self._syncBandStack, self)

        # Stretch (self-contained combo + stacked sub-form)
        layout.addWidget(QLabel("Stretch Algorithm:"))
        layout.addWidget(parameterWidget(settings.stretch, self))
        if stretchFrom:
            layout.addWidget(formWidget([settings.stretchFrom], self))

        # Opacity (labeled form row)
        if opacity:
            layout.addWidget(formWidget([settings.opacity], self))

        self.setLayout(layout)

    @property
    def essentialRows(self) -> QStackedLayout:
        """The rows a docked panel keeps in view (``keepInView``): Mode and
        the bands, or the color map with its handles."""
        return self.bandStack

    def _syncBandStack(self, *args) -> None:
        isRgb = self.settings.mode.get() == RenderMode.RGB
        self.bandStack.setCurrentIndex(self._rgbIndex if isRgb else self._monoIndex)


if __name__ == "__main__":
    q_app = QApplication(sys.argv)
    image = debug.generate_random_image((100, 100, 10), (10, 10, 10))
    renderer = ImageRenderer(image)
    settingsPanel = renderer.getSettingsPanel()
    settingsPanel.show()
    q_app.exec()
