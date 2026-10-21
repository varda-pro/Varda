"""Eigenvalue Plot: inspect a transform result's eigenvalues to pick how many
components to keep, and jump to Inverse Transform with that number."""

import numpy as np
from PyQt6.QtCore import Qt

from varda.analysis.eigenvalue_dialog import EigenvaluePlotDialog
from varda.analysis.eigenvalue_plot import EigenvaluePlot
from varda.analysis.linear_algebra import suggestedComponents
from varda.analysis.transforms import PcaAnalysis
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource


def _pcaResult() -> tuple[VardaRaster, VardaRaster]:
    rng = np.random.default_rng(0)
    sources = rng.normal(size=(20 * 30, 3)) * np.array([3.0, 2.0, 1.0])
    cube = (sources @ rng.normal(size=(3, 8))).reshape(20, 30, 8)
    image = VardaRaster(ArrayDataSource(cube), name="scene")
    analysis = PcaAnalysis()
    analysis.components.set(8)
    return image, analysis.run(image, lambda p, m: None)


def _cell(dialog, row, column) -> str:
    model = dialog.table.model()
    assert model is not None
    return model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole)


def test_dialog_plots_and_tabulates_the_eigenvalues(qtbot):
    image, result = _pcaResult()
    dialog = EigenvaluePlotDialog([image, result], image=result)
    qtbot.addWidget(dialog)

    assert isinstance(dialog.plot, EigenvaluePlot)
    eigenvalues = np.asarray(result.extraMetadata["transform"]["eigenvalues"])
    assert dialog.components.get() == suggestedComponents(eigenvalues, "PCA")
    assert dialog.components.range == (1, 8)
    model = dialog.table.model()
    assert model is not None and model.rowCount() == 8
    assert _cell(dialog, 0, 0) == "1"
    assert float(_cell(dialog, 0, 1)) > float(_cell(dialog, 1, 1))  # descending
    assert _cell(dialog, 7, 3) == "100.0%"  # cumulative variance
    assert dialog.keepButton.isEnabled()


def test_dialog_explains_when_the_image_is_not_a_transform_result(qtbot):
    image, result = _pcaResult()
    dialog = EigenvaluePlotDialog([image, result], image=image)
    qtbot.addWidget(dialog)

    assert dialog.plot is None
    assert "PCA" in dialog.statusLabel.text()
    assert not dialog.keepButton.isEnabled()

    dialog.imageParam.set(result)
    assert isinstance(dialog.plot, EigenvaluePlot)
    assert dialog.keepButton.isEnabled()


def test_keep_button_requests_an_inverse_with_the_chosen_count(qtbot):
    image, result = _pcaResult()
    dialog = EigenvaluePlotDialog([image, result], image=result)
    qtbot.addWidget(dialog)
    dialog.components.set(3)
    requests = []
    dialog.sigInverseRequested.connect(lambda img, n: requests.append((img, n)))

    dialog.keepButton.click()

    assert requests == [(result, 3)]


def test_eigenvalue_plot_is_offered_in_the_transforms_menu():
    from varda._actions._processing_actions import (
        EIGENVALUE_PLOT_ACTION_ID,
        PROCESSING_ACTIONS,
        categoryMenuId,
    )
    from varda._actions._menu_ids import MenuId

    (action,) = [a for a in PROCESSING_ACTIONS if a.id == EIGENVALUE_PLOT_ACTION_ID]
    assert action.title == "Eigenvalue Plot…"
    assert action.menus is not None
    assert action.menus[0].id == categoryMenuId(MenuId.PROCESSING, "Transforms")


def test_small_variance_shares_are_not_rounded_to_zero(qtbot):
    """Release QA 0.12.0 #12: shares below 0.005 % read "0.00%"."""
    rng = np.random.default_rng(0)
    sources = rng.normal(size=(20 * 30, 3)) * np.array([3.0, 2.0, 1.0])
    cube = (sources @ rng.normal(size=(3, 8))).reshape(20, 30, 8)
    cube += 0.001 * rng.normal(size=cube.shape)  # components 4-8: ~1e-6 %
    image = VardaRaster(ArrayDataSource(cube), name="scene")
    analysis = PcaAnalysis()
    analysis.components.set(8)
    result = analysis.run(image, lambda p, m: None)
    dialog = EigenvaluePlotDialog([image, result], image=result)
    qtbot.addWidget(dialog)
    shares = [float(_cell(dialog, row, 2).rstrip("%")) for row in range(8)]
    assert all(share > 0 for share in shares)  # none reads "0.00%"
    assert shares == sorted(shares, reverse=True)
