"""Every analysis's dialog fits a laptop screen: its settings scroll, and
nothing in it is cut off at its smallest width."""

import numpy as np
import pytest
from affine import Affine
from rasterio import CRS

from varda.analysis.dialog import RunAnalysisDialog
from varda.analysis.fatt_map import FattMapAnalysis
from varda.analysis.image_math import ApplyMaskAnalysis
from varda.analysis.spectral_library import LibrarySpectrum
from varda.analysis.registry import availableAnalyses
from varda.common.entities import VardaRaster
from varda.common.layout_audit import assertNothingClipped
from varda.common.parameter_widgets import ImageParameterWidget
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.debug import generate_random_image


@pytest.mark.parametrize("analysis", availableAnalyses(), ids=lambda a: a.name)
def test_an_analysis_dialog_fits_a_laptop_screen(qtbot, analysis):
    images = [generate_random_image((30, 30, 10)) for _ in range(2)]
    dialog = RunAnalysisDialog(images, [analysis], analysis=analysis, image=images[0])
    qtbot.addWidget(dialog)
    minimum = dialog.minimumSizeHint()
    assert minimum.width() <= 800 and minimum.height() <= 400
    dialog.resize(minimum.width(), 560)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)


def test_the_fatt_map_dialog_fits_a_laptop_screen(qtbot):
    """Opened from the FATT window's Map Ticked Targets…, with the exclusion
    settings and a mask to choose."""
    images = [generate_random_image((30, 30, 10)) for _ in range(2)]
    analysis = FattMapAnalysis([LibrarySpectrum("a", np.arange(10.0), np.ones(10))])
    dialog = RunAnalysisDialog(
        images,
        [FattMapAnalysis],
        analysis=FattMapAnalysis,
        instance=analysis,
        image=images[0],
    )
    qtbot.addWidget(dialog)
    assert {"exclude", "minimumSignal"} <= analysis.params.keys()
    assert images[1] in analysis.exclude.imageProvider()
    minimum = dialog.minimumSizeHint()
    assert minimum.width() <= 800 and minimum.height() <= 400
    dialog.resize(minimum.width(), 560)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
    dialog.resize(1280, 800)
    assertNothingClipped(dialog)


def test_the_not_listed_note_fits_a_laptop_screen(qtbot):
    """Apply Mask, with images it can't use: the note under the dropdown
    wraps instead of widening the dialog or being cut off."""

    def raster(name: str, bands: int, x0: float) -> VardaRaster:
        return VardaRaster(
            ArrayDataSource(
                np.zeros((30, 30, bands), np.float32),
                transform=Affine(18.0, 0, x0, 0, -18.0, 4e6),
                crs=CRS.from_epsg(32612),
            ),
            name=name,
        )

    image = raster("frt00004185_07_if165j_mtr3_iovf", 10, 5e5)
    mask = raster("shadow mask", 2, 5e5)
    far = [
        raster(f"a far away scene with a long name {n}" * 2, 1, 9e5) for n in range(4)
    ]
    photo = generate_random_image((30, 30, 1))
    images = [image, mask, *far, photo]
    dialog = RunAnalysisDialog(
        images, [ApplyMaskAnalysis], analysis=ApplyMaskAnalysis, image=image
    )
    qtbot.addWidget(dialog)
    notes = [
        w.notListed
        for w in dialog.findChildren(ImageParameterWidget)
        if w.param.explain()
    ]
    assert len(notes) == 1 and not notes[0].isHidden()
    assert "doesn't overlap this image" in notes[0].text()
    minimum = dialog.minimumSizeHint()
    assert minimum.width() <= 800 and minimum.height() <= 400
    dialog.resize(minimum.width(), 560)
    dialog.show()
    qtbot.waitExposed(dialog)
    assertNothingClipped(dialog)
    dialog.resize(1280, 800)
    assertNothingClipped(dialog)
