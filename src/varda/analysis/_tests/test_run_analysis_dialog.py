"""The Run Analysis dialog picks an image and an analysis and shows its settings."""

import numpy as np

from varda.analysis.analysis import Analysis
from varda.analysis.dialog import RunAnalysisDialog
from varda.analysis.transforms import PcaAnalysis
from varda.common.entities import VardaRaster
from varda.common.parameter import IntParameter
from varda.common.vec2 import Vec2
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.utilities.debug import generate_random_image


class AlphaAnalysis(Analysis):
    name = "Alpha"
    description = "First test analysis."
    knob = IntParameter("Knob", 1, range=(0, 9))

    def run(self, image, reportProgress):
        return image


class BetaAnalysis(Analysis):
    name = "Beta"

    def run(self, image, reportProgress):
        return image


def test_dialog_lists_analyses_and_defaults_to_the_first(qtbot):
    images = [generate_random_image((10, 10, 10)) for _ in range(2)]
    dialog = RunAnalysisDialog(images, [AlphaAnalysis, BetaAnalysis])
    qtbot.addWidget(dialog)

    assert [dialog.analysisCombo.itemText(i) for i in range(2)] == ["Alpha", "Beta"]
    assert isinstance(dialog.selectedAnalysis(), AlphaAnalysis)
    assert dialog.selectedImage() is images[0]


def test_dialog_can_be_preselected(qtbot):
    images = [generate_random_image((10, 10, 10)) for _ in range(2)]
    dialog = RunAnalysisDialog(
        images, [AlphaAnalysis, BetaAnalysis], image=images[1], analysis=BetaAnalysis
    )
    qtbot.addWidget(dialog)

    assert isinstance(dialog.selectedAnalysis(), BetaAnalysis)
    assert dialog.selectedImage() is images[1]


def test_a_preselected_analysis_hides_the_picker_and_names_the_dialog(qtbot):
    images = [generate_random_image((10, 10, 10))]
    dialog = RunAnalysisDialog(
        images, [AlphaAnalysis, BetaAnalysis], analysis=BetaAnalysis
    )
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Beta"
    assert dialog.analysisCombo.isHidden()
    assert isinstance(dialog.selectedAnalysis(), BetaAnalysis)


def test_settings_follow_the_chosen_analysis_and_persist(qtbot):
    images = [generate_random_image((10, 10, 10))]
    dialog = RunAnalysisDialog(images, [AlphaAnalysis, BetaAnalysis])
    qtbot.addWidget(dialog)
    alpha = dialog.selectedAnalysis()
    assert isinstance(alpha, AlphaAnalysis)
    alpha.knob.set(7)

    dialog.analysisCombo.setCurrentIndex(1)
    assert isinstance(dialog.selectedAnalysis(), BetaAnalysis)
    dialog.analysisCombo.setCurrentIndex(0)

    assert dialog.selectedAnalysis() is alpha  # same instance, settings kept
    assert alpha.knob.value == 7


class NeedsRoisAnalysis(Analysis):
    name = "Needs ROIs"
    needsRois = True

    def run(self, image, reportProgress):
        return image


def test_dialog_hands_the_workspace_rois_and_images_to_the_analysis(qtbot):
    from varda.analysis.analysis import AnalysisContext
    from varda.rois.roi_collection import ROICollection

    images = [generate_random_image((10, 10, 10))]
    rois = ROICollection()
    dialog = RunAnalysisDialog(
        images, [NeedsRoisAnalysis], analysis=NeedsRoisAnalysis, rois=rois
    )
    qtbot.addWidget(dialog)

    assert dialog.selectedAnalysis().context == AnalysisContext(
        rois=rois, images=images
    )


def test_dialog_blocks_an_analysis_that_needs_rois_when_there_are_none(qtbot):
    images = [generate_random_image((10, 10, 10))]
    dialog = RunAnalysisDialog(images, [NeedsRoisAnalysis], analysis=NeedsRoisAnalysis)
    qtbot.addWidget(dialog)

    assert not dialog.runButton.isEnabled()
    assert "ROI" in dialog.statusLabel.text()


def test_accepting_emits_the_analysis_and_image(qtbot):
    images = [generate_random_image((10, 10, 10)) for _ in range(2)]
    dialog = RunAnalysisDialog(images, [AlphaAnalysis], image=images[1])
    qtbot.addWidget(dialog)
    received = []
    dialog.connectOnRun(
        lambda analysis, image, path: received.append((analysis, image, path))
    )

    dialog.accept()

    ((analysis, image, path),) = received
    assert isinstance(analysis, AlphaAnalysis)
    assert image is images[1]
    assert path is None  # saving to a file is off by default


def test_pca_shows_the_bands_it_will_use_and_refuses_a_one_wavelength_range(qtbot):
    """Release QA #6: equal from/to ran silently on every band."""
    image = VardaRaster(
        ArrayDataSource(
            np.random.default_rng(0).normal(size=(10, 10, 40)),
            wavelengths=np.linspace(1000.0, 2600.0, 40),
            wavelengthUnits="nm",
        ),
        name="cube",
    )
    dialog = RunAnalysisDialog([image], [PcaAnalysis], analysis=PcaAnalysis)
    qtbot.addWidget(dialog)
    assert dialog.statusLabel.text() == "Uses 40 of 40 bands, 1000–2600 nm."
    assert dialog.runButton.isEnabled()

    dialog.selectedAnalysis().wavelengths.set(Vec2(1309.0, 1309.0))

    assert not dialog.runButton.isEnabled()
    assert "needs at least two" in dialog.statusLabel.text()
