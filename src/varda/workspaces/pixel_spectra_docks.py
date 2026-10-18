"""One or more pixel-spectra plots for a workspace, each in its own dock."""

from __future__ import annotations

from collections.abc import Callable, Sequence

import PyQt6Ads as ads
from PyQt6.QtCore import QChildEvent, QEvent, QObject, QSignalBlocker, pyqtSignal
from PyQt6.QtWidgets import QWidget

from varda.common.entities import VardaRaster
from varda.common.ui import VardaDockWidget
from varda.plotting.pixel_spectra_plot import PixelSpectraPlotWidget
from varda.plotting.plot import Curve

BASE_TITLE = "Pixel Spectra"


class PixelSpectraDocks(QObject):
    """Owns a workspace's pixel-spectra plots.

    Exactly one plot is *active* and receives pixel selections. Closing a dock
    only hides it; the next selection re-shows the active one, so a closed plot
    is never a dead end. Additional plots let spectra from different regions be
    collected side by side.
    """

    sigPlotAdded = pyqtSignal(object)  # a new PixelSpectraPlotWidget

    def __init__(
        self,
        dockManager: ads.CDockManager,
        anchor: ads.CDockWidget,
        *,
        configurePlot: Callable[[PixelSpectraPlotWidget], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        """New docks are tabbed alongside ``anchor`` (then alongside the previous
        pixel plot). ``configurePlot`` runs on each new plot, e.g. to add
        workspace-specific sidebar sections."""
        super().__init__(parent)
        self._dockManager = dockManager
        self._anchor = anchor
        self._configurePlot = configurePlot
        self.plots: list[PixelSpectraPlotWidget] = []
        self._docks: dict[PixelSpectraPlotWidget, VardaDockWidget] = {}
        self._baseTitles: dict[PixelSpectraPlotWidget, str] = {}
        self._active: PixelSpectraPlotWidget | None = None
        # Restored Libraries choices for plots not opened yet, by plot number
        self._savedLibrariesOff: list[list[str]] = []

    # Clicking in a plot, or on its dock tab, makes it the active one. Those
    # presses go to whichever child widget is under the mouse, so each plot and
    # tab is watched with all its children, including ones added later; never
    # the whole application, which would see every event of every window.

    def _watch(self, widget: QWidget) -> None:
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a1 is None or not isinstance(a0, QWidget):
            return False
        if a1.type() == QEvent.Type.MouseButtonPress:
            clicked = self._plotAt(a0)
            if clicked is not None and clicked is not self._active:
                self.setActive(clicked)
        elif a1.type() == QEvent.Type.ChildAdded:
            assert isinstance(a1, QChildEvent)
            child = a1.child()
            if isinstance(child, QWidget):
                self._watch(child)
        return False

    def _plotAt(self, widget: QWidget) -> PixelSpectraPlotWidget | None:
        for plot in self.plots:
            tab = self._docks[plot].tabWidget()
            if _within(widget, plot) or (tab is not None and _within(widget, tab)):
                return plot
        return None

    @property
    def active(self) -> PixelSpectraPlotWidget:
        """The plot receiving pixel selections (created on demand)."""
        if self._active is None:
            return self.newPlot()
        return self._active

    @property
    def activeDock(self) -> VardaDockWidget:
        return self.dockFor(self.active)

    def dockFor(self, plot: PixelSpectraPlotWidget) -> VardaDockWidget:
        return self._docks[plot]

    def newPlot(
        self, *, title: str | None = None, activate: bool = True
    ) -> PixelSpectraPlotWidget:
        """Open another pixel-spectra plot, numbered unless ``title`` is given,
        and (by default) make it the one that receives pixel selections."""
        plot = PixelSpectraPlotWidget()
        if title is None:
            number = len(self.plots) + 1
            title = BASE_TITLE if number == 1 else f"{BASE_TITLE} {number}"
        dock = VardaDockWidget(title)
        dock.setWidget(plot)

        neighbour = self._docks[self.plots[-1]] if self.plots else self._anchor
        self.plots.append(plot)
        self._docks[plot] = dock
        self._baseTitles[plot] = title
        if len(self.plots) <= len(self._savedLibrariesOff):
            plot.referencePicker.setExcluded(
                self._savedLibrariesOff[len(self.plots) - 1]
            )
        if self._configurePlot is not None:
            self._configurePlot(plot)

        self._watch(plot)
        tab = dock.tabWidget()
        if tab is not None:
            self._watch(tab)

        plot.newPlotButton.clicked.connect(lambda: self.newPlot())
        plot.activeCheckBox.toggled.connect(
            lambda checked, plot=plot: self._onActiveToggled(plot, checked)
        )

        area = neighbour.dockAreaWidget()
        if area is not None:
            self._dockManager.addDockWidget(
                ads.DockWidgetArea.CenterDockWidgetArea, dock, area
            )
        else:
            self._dockManager.addDockWidget(
                ads.DockWidgetArea.BottomDockWidgetArea, dock
            )

        if activate or self._active is None:
            self.setActive(plot)
        else:
            self.setActive(self._active)  # refresh the new plot's checkbox/title
        self.sigPlotAdded.emit(plot)
        return plot

    def librariesOff(self) -> list[list[str]]:
        """Each plot's Libraries choice (the libraries turned off), in the
        order the plots were opened: for a session."""
        return [plot.referencePicker.excludedLibraries() for plot in self.plots]

    def restoreLibrariesOff(self, saved: Sequence[Sequence[str]]) -> None:
        """Give the plots their saved choices, by plot number; plots opened
        later (a session reopens only the first) get theirs when they open."""
        self._savedLibrariesOff = [list(names) for names in saved]
        for plot, names in zip(self.plots, self._savedLibrariesOff):
            plot.referencePicker.setExcluded(names)

    def dedicated(self, title: str) -> DedicatedPlotSink:
        """A sink whose spectra go to their own plot (created on first use,
        re-shown if closed) instead of the active one — e.g. ratio spectra."""
        return DedicatedPlotSink(self, title)

    def showDock(self, plot: PixelSpectraPlotWidget) -> None:
        dock = self.dockFor(plot)
        if dock.isClosed():
            dock.toggleView(True)

    def setActive(self, plot: PixelSpectraPlotWidget) -> None:
        self._active = plot
        for candidate in self.plots:
            isActive = candidate is plot
            with QSignalBlocker(candidate.activeCheckBox):
                candidate.activeCheckBox.setChecked(isActive)
            base = self._baseTitles[candidate]
            self._docks[candidate].setWindowTitle(
                f"{base} (active)" if isActive else base
            )

    def addPixelSpectra(
        self,
        images: Sequence[VardaRaster],
        x: int,
        y: int,
        *,
        labelWithImageName: bool = False,
    ) -> list[Curve]:
        """Plot a selection on the active plot, re-showing its dock if closed."""
        return self._shownActivePlot().addPixelSpectra(
            images, x, y, labelWithImageName=labelWithImageName
        )

    def addSpectrum(self, wavelengths, values, label: str) -> Curve:
        """Plot any spectrum on the active plot, re-showing its dock if closed."""
        return self._shownActivePlot().addSpectrum(wavelengths, values, label)

    def addSpectra(self, entries: Sequence[tuple]) -> list[Curve]:
        """Plot several spectra as one selection on the active plot."""
        return self._shownActivePlot().addSpectra(entries)

    def _shownActivePlot(self) -> PixelSpectraPlotWidget:
        plot = self.active
        self.showDock(plot)
        return plot

    def _onActiveToggled(self, plot: PixelSpectraPlotWidget, checked: bool) -> None:
        if checked:
            self.setActive(plot)
        elif plot is self._active:
            # exactly one plot stays active
            with QSignalBlocker(plot.activeCheckBox):
                plot.activeCheckBox.setChecked(True)


def _within(widget: QWidget, container: QWidget) -> bool:
    return widget is container or container.isAncestorOf(widget)


class DedicatedPlotSink:
    """Plots spectra into a plot of its own, so e.g. ratio spectra do not mix
    with plain pixel spectra. The plot is created on first use and never
    becomes the target of pixel clicks by itself."""

    def __init__(self, docks: PixelSpectraDocks, title: str) -> None:
        self._docks = docks
        self._title = title
        self._plot: PixelSpectraPlotWidget | None = None

    def plot(self) -> PixelSpectraPlotWidget:
        if self._plot is None or self._plot not in self._docks.plots:
            self._plot = self._docks.newPlot(title=self._title, activate=False)
        self._docks.showDock(self._plot)
        return self._plot

    def addSpectra(self, entries: Sequence[tuple]) -> list[Curve]:
        return self.plot().addSpectra(entries)

    def addSpectrum(self, wavelengths, values, label: str) -> Curve:
        return self.plot().addSpectrum(wavelengths, values, label)
