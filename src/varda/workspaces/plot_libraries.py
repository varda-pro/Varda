"""A workspace's spectral plots' Libraries choices (the libraries each one
turned off), saved with the session and restored from it."""

from __future__ import annotations

from collections.abc import Mapping

from varda.plotting.plot import VardaPlotWidget
from varda.workspaces.pixel_spectra_docks import PixelSpectraDocks

KEY = "plotLibrariesOff"


def plotLibrariesState(
    plot: VardaPlotWidget, pixelPlots: PixelSpectraDocks | None = None
) -> dict[str, dict]:
    """``{KEY: {"plot": [...], "pixelPlots": [[...], ...]}}`` for a workspace's
    session state: the main plot's choice and each pixel plot's, in order."""
    state: dict = {"plot": plot.referencePicker.excludedLibraries()}
    if pixelPlots is not None:
        state["pixelPlots"] = pixelPlots.librariesOff()
    return {KEY: state}


def restorePlotLibraries(
    sessionState: Mapping,
    plot: VardaPlotWidget,
    pixelPlots: PixelSpectraDocks | None = None,
) -> None:
    """Restore what ``plotLibrariesState`` saved. A session saved before the
    choice was kept opens with every library on, as its plots had then."""
    saved = sessionState.get(KEY, {})
    plot.referencePicker.setExcluded(saved.get("plot", []))
    if pixelPlots is not None:
        pixelPlots.restoreLibrariesOff(saved.get("pixelPlots", [[]]))
