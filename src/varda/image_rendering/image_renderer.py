import math
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

from varda.analysis.browse_products import (
    BrowseProduct,
    parseProduct,
    productRanges,
)
from varda.common.parameter import (
    ParameterGroup,
    EnumParameter,
    FloatParameter,
    LabeledEnum,
    StringParameter,
)
from varda.common.entities import VardaRaster
from varda.common.vec2 import Vec2
from varda.utilities import debug
from varda.image_rendering.browse_composites import COMPOSITES
from varda.image_rendering.browse_controls import BrowseChannels, BrowseProductPicker
from varda.image_rendering.stretch_algorithms import ManualValueStretchRGB
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
# Images with more pixels than this are drawn a piece at a time (the part a
# view shows, at about the screen's resolution: see ``renderPiece``), and
# their stretch and histogram take their statistics from a regular subsample
# of about SAMPLE_PIXELS pixels (every k-th row and column)
LARGE_IMAGE_PIXELS = 16_000_000
SAMPLE_PIXELS = 4_000_000
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
    browse = StringParameter(
        "Browse Product",
        "",
        "The browse product shown in place of the bands, as "
        "'NAME = red, green, blue' ('' shows the bands)",
    )

    def __init__(self, image: VardaRaster):
        super().__init__()
        self.image = image
        # Choosing a browse product sets the stretch to its channels' ranges
        # once its composite is ready; leaving it brings back the stretch
        # chosen before
        self.browseStretchPending = False
        self._stretchBeforeBrowse: str | None = None
        self.mode.sigParameterChanged.connect(self._onModeChanged)
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

    def browseProduct(self) -> BrowseProduct | None:
        """The browse product shown, or None when the bands are."""
        text = self.browse.get()
        try:
            return parseProduct(text) if text else None
        except ValueError:
            return None

    def showBrowseProduct(self, product: BrowseProduct | None) -> None:
        """Show a browse product in RGB, or (None) the chosen bands again."""
        stretch = self.stretch
        with self.sigParameterChanged.blocked():
            if product is None:
                self.browse.set("")
                self.browseStretchPending = False
                if self._stretchBeforeBrowse is not None:
                    stretch.selectByName(self._stretchBeforeBrowse)
                    self._stretchBeforeBrowse = None
            else:
                if self._stretchBeforeBrowse is None:
                    self._stretchBeforeBrowse = stretch.nameOf(stretch.current)
                self.browse.set(product.asText())
                self.mode.set(RenderMode.RGB)
                self.browseStretchPending = True
        self.sigParameterChanged.emit(self)

    def _onModeChanged(self, *args) -> None:
        if self.mode.get() == RenderMode.MONO and self.browse.get():
            self.showBrowseProduct(None)  # one band: the product's gone


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
        COMPOSITES.sigReady.connect(self._onCompositeReady)

    def _onImageEdited(self) -> None:
        """The image's metadata changed (a no-data value, say): read again."""
        COMPOSITES.forget(self.image)
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
        if self._applyBrowseStretch():
            return  # that change refreshes
        self.sigShouldRefresh.emit()

    def _onCompositeReady(self, image: VardaRaster, text: str) -> None:
        if image is not self.image or self.settings.browse.get() != text:
            return
        if self._applyBrowseStretch():
            return
        self._rgb8 = self._stretchedData = self.cachedRender = None
        self.sigShouldRefresh.emit()

    def _composite(self) -> np.ndarray | None:
        """The browse product's channels, once computed (asked for until
        then, while the bands show)."""
        product = self.settings.browseProduct()
        if product is None:
            return None
        composite = COMPOSITES.get(self.image, product)
        if composite is None:
            COMPOSITES.request(self.image, product)
        return composite

    def _applyBrowseStretch(self) -> bool:
        """Set the stretch to a newly chosen product's channel ranges once its
        composite is ready; True if it did (a settings change follows)."""
        product = self.settings.browseProduct()
        if not self.settings.browseStretchPending or product is None:
            return False
        composite = COMPOSITES.get(self.image, product)
        if composite is None:
            return False
        self.settings.browseStretchPending = False
        self.setStretchRanges(productRanges(product, composite))
        return True

    def showsBrowseProduct(self) -> bool:
        """Whether the picture is a browse product's composite (not yet,
        while it's computed)."""
        return self._rawKey is not None and self._rawKey[0] == "browse"

    def _bandKey(self) -> tuple:
        if self.settings.browse.get() and self._composite() is not None:
            return ("browse", self.settings.browse.get())
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
            if key[0] == "browse":
                data = self._composite()
                assert data is not None
            else:
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

            def thinned(part: np.ndarray) -> np.ndarray:
                if not self.isLarge():
                    return part
                return part[(slice(None, None, self.sampleStep(part)),) * 2]

            # thinned first: the check for data doesn't scan a large image
            sample = thinned(data if window is None else data[window])
            if sample.size == 0 or not np.isfinite(sample).any():
                sample = thinned(data)
            self._sample, self._sampleKey = sample, key
        return self._sample

    def isLarge(self) -> bool:
        """Whether the image is drawn a piece at a time, with statistics
        from a subsample (``LARGE_IMAGE_PIXELS``)."""
        return self.image.width * self.image.height > LARGE_IMAGE_PIXELS

    @staticmethod
    def sampleStep(data: np.ndarray) -> int:
        """Every how many rows and columns a large image's statistics read."""
        pixels = data.shape[0] * data.shape[1]
        return max(1, math.ceil(math.sqrt(pixels / SAMPLE_PIXELS)))

    def render(self):
        """
        Render the image with the current band and stretch settings.
        Returns: numpy ndarray with shape (height, width, 4) representing an RGBA image.
        """
        if self.cachedRender is not None:
            return self.cachedRender
        if self.image is None or self.settings is None:
            raise ValueError("Image and settings must be set before rendering.")
        if self._rgb8 is None or self._noData is None:
            self._rgb8, self._noData = self._renderRgb8()
        self.cachedRender = self._withAlpha(self._rgb8, self._noData)
        return self.cachedRender

    def renderPiece(self, rows: slice, cols: slice) -> np.ndarray:
        """RGBA of ``rows`` and ``cols`` of the image (slices, with a step to
        read every n-th pixel), stretched as the whole image is: how a view
        draws a large image. Not cached."""
        data = self._readBands()[rows, cols]
        rgb8, noData = self._rgb8Of(data, keep=False)
        return self._withAlpha(rgb8, noData)

    def renderReduced(
        self, maxSide: int, window: tuple[slice, slice] | None = None
    ) -> np.ndarray:
        """RGBA of the image, or of ``window`` (rows, cols) of it, at most
        ``maxSide`` pixels on a side (every n-th pixel): how a 3D texture is
        drawn. Once the bands are read (the image is shown), a slice of them
        stretched as the views are; otherwise read reduced
        (``VardaRaster.getDataReduced``, from the file's overviews) and
        stretched from that reading, so the whole image is never read for
        it. Not cached."""
        rows, cols = window or (slice(0, self.image.height), slice(0, self.image.width))
        r0, r1, _ = rows.indices(self.image.height)
        c0, c1, _ = cols.indices(self.image.width)
        step = max(1, math.ceil(max(r1 - r0, c1 - c0) / maxSide))
        key = self._bandKey()
        if (self._rawBandData is not None and self._rawKey == key) or (
            key[0] == "browse"
        ):
            return self.renderPiece(slice(r0, r1, step), slice(c0, c1, step))
        raw = self.image.getDataReduced(list(key[1:]), (r0, c0, r1 - r0, c1 - c0), step)
        data = np.asarray(
            np.ma.filled(raw, np.nan) if np.ma.isMaskedArray(raw) else raw
        )
        sample = data[(slice(None, None, self.sampleStep(data)),) * 2]
        rgb8, noData = self._rgb8Of(data, keep=False, sample=sample)
        return self._withAlpha(rgb8, noData)

    def _withAlpha(self, rgb8: np.ndarray, noData: np.ndarray) -> np.ndarray:
        alpha = np.full(
            (rgb8.shape[0], rgb8.shape[1], 1),
            int(self.settings.opacity.get() * 255),
            dtype=np.uint8,
        )
        if self.transparentNoData:
            alpha[noData] = 0
        return np.concatenate((rgb8, alpha), axis=2)

    def _renderRgb8(self) -> tuple[np.ndarray, np.ndarray]:
        """The stretched (and, in mono, color-mapped) 8-bit RGB picture and
        the (rows, cols) mask of pixels with no data in any channel."""
        return self._rgb8Of(self._readBands(), keep=True)

    def _rgb8Of(
        self, bands: np.ndarray, keep: bool, sample: np.ndarray | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        """``bands`` stretched to 8 bits, with statistics from ``sample`` (by
        default ``getSampleData``) (``keep``: as the image's stretched data,
        ``getStretchedData``)."""
        if sample is None:
            sample = self.getSampleData()
        data = self.settings.stretch.current.apply(bands, sample)
        mode = RenderMode.RGB if self.showsBrowseProduct() else self.settings.mode.get()
        if keep:
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
        """The current stretch's limits. They come from the statistics
        sample alone, so a large image isn't drawn whole to find them."""
        if self.cachedRender is None:
            self.settings.stretch.current.apply(
                self._readBands()[:1, :1], self.getSampleData()
            )
        return self.settings.stretch.current.minMaxVals()

    def setManualStretch(self, lo: float, hi: float) -> None:
        """Switch to the manual stretch and set every channel to [lo, hi]."""
        self.setStretchRanges([(lo, hi)] * 3)

    def setStretchRanges(self, ranges: list[tuple[float, float]]) -> None:
        """Switch to the manual stretch with each channel's [lo, hi], in a
        single refresh."""
        manual = self.settings.stretch.option(StretchParameter.MANUAL_NAME)
        assert isinstance(manual, ManualValueStretchRGB)
        channels = (
            manual.config.redStretch,
            manual.config.greenStretch,
            manual.config.blueStretch,
        )
        with self.settings.sigParameterChanged.blocked():
            for param, (lo, hi) in zip(channels, ranges):
                param.set(Vec2(float(lo), float(hi)))
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

        # Browse products, for images with wavelengths
        self.browsePicker: BrowseProductPicker | None = None
        if settings.image.bandModel.centers is not None:
            layout.addWidget(QLabel("Browse Product:"))
            self.browsePicker = BrowseProductPicker(settings, self)
            layout.addWidget(self.browsePicker)

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
        self._browseIndex = self.bandStack.addWidget(BrowseChannels(settings))
        layout.addLayout(self.bandStack)
        self._syncBandStack()
        connectWhileAlive(settings.mode.sigParameterChanged, self._syncBandStack, self)
        connectWhileAlive(
            settings.browse.sigParameterChanged, self._syncBandStack, self
        )

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
        the bands, the color map with its handles, or the browse product's
        channels."""
        return self.bandStack

    def _syncBandStack(self, *args) -> None:
        if self.settings.browse.get():
            self.bandStack.setCurrentIndex(self._browseIndex)
            return
        isRgb = self.settings.mode.get() == RenderMode.RGB
        self.bandStack.setCurrentIndex(self._rgbIndex if isRgb else self._monoIndex)


if __name__ == "__main__":
    q_app = QApplication(sys.argv)
    image = debug.generate_random_image((100, 100, 10), (10, 10, 10))
    renderer = ImageRenderer(image)
    settingsPanel = renderer.getSettingsPanel()
    settingsPanel.show()
    q_app.exec()
