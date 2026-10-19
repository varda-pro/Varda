import matplotlib
import pyqtgraph as pg

# Varda's image arrays are (rows, columns, bands). pyqtgraph assumes
# (columns, rows) unless told otherwise, which shows every image transposed
# (rotated and mirrored). Set here, not at app startup, so every viewport,
# test and script that renders images gets it.
pg.setConfigOptions(imageAxisOrder="row-major")


# pyqtgraph's color-map menu (every gradient, e.g. Render Settings) lists
# Matplotlib's maps through matplotlib.pyplot, which loads Matplotlib's fonts:
# 20+ s the first time (a bundled Varda keeps that cache in a temporary folder,
# so every launch). The registry holds the same names without fonts.
_listMaps = pg.colormap.listMaps


def _listMapsWithoutFonts(source: str | None = None) -> list[str]:
    if source is not None and source.lower() == "matplotlib":
        return sorted(matplotlib.colormaps)
    return _listMaps(source)


pg.colormap.listMaps = _listMapsWithoutFonts

from .image_renderer import ImageRenderer  # noqa: E402
from .raster_view.image_viewport import ImageViewport  # noqa: E402
