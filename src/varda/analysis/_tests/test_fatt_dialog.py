"""The FATT window ranks library targets and sets up a detection map."""

import csv

import numpy as np
import pytest
from PyQt6.QtCore import Qt

from varda.analysis.fatt_dialog import FattDialog
from varda.analysis.fatt_map import FattMapAnalysis
from varda.analysis.spectral_library import LibrarySpectrum
from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.version import vardaVersion

WAVELENGTHS = np.linspace(1000.0, 2600.0, 160)


def _spectrum(continuum: float, *bands: tuple[float, float, float]) -> np.ndarray:
    s = continuum + 0.05 * (WAVELENGTHS - 1000.0) / 1600.0
    for center, width, depth in bands:
        s = s * (1 - depth * np.exp(-0.5 * ((WAVELENGTHS - center) / width) ** 2))
    return s


SCENE = {
    "kaolinite": _spectrum(0.45, (2165, 12, 0.25), (2205, 14, 0.35)),
    "nontronite": _spectrum(0.35, (2285, 18, 0.3), (2400, 25, 0.15)),
    "olivine": _spectrum(0.25),
}
DECOYS = {"calcite": _spectrum(0.4, (2340, 22, 0.35))}


@pytest.fixture
def dialog(qtbot):
    rng = np.random.default_rng(0)
    cube = rng.dirichlet(np.ones(3), size=(40, 40)) @ np.stack(list(SCENE.values()))
    cube += rng.normal(scale=5e-4, size=cube.shape)
    image = VardaRaster(
        ArrayDataSource(
            cube.astype(np.float32), wavelengths=WAVELENGTHS, wavelengthUnits="nm"
        ),
        name="scene",
    )
    library = [LibrarySpectrum(n, WAVELENGTHS, s) for n, s in (SCENE | DECOYS).items()]
    widget = FattDialog([image], library=library)
    qtbot.addWidget(widget)
    return widget


def _analyze(dialog, qtbot):
    dialog.analyze()
    qtbot.waitUntil(lambda: dialog.factors is not None, timeout=10000)


def test_the_default_range_is_the_crism_window(dialog):
    assert (dialog.minWavelength.get(), dialog.maxWavelength.get()) == (1700.0, 2600.0)


def test_analyze_suggests_factors_and_ranks_the_scene_minerals_first(dialog, qtbot):
    _analyze(dialog, qtbot)
    assert dialog.factorCount.get() == 3
    top = {dialog.table.item(row, 1).text() for row in range(3)}
    assert top == set(SCENE)
    assert dialog.table.item(3, 1).text() == "calcite"


def test_changing_the_factor_count_reranks(dialog, qtbot):
    _analyze(dialog, qtbot)
    before = dialog._fits[0].nrmse
    dialog.factorCount.set(1)
    assert dialog._fits[0].nrmse > before


def test_the_ranking_exports_to_csv_with_its_settings(dialog, qtbot, tmp_path):
    _analyze(dialog, qtbot)
    path = tmp_path / "ranking.csv"
    dialog.saveRanking(path)
    lines = path.read_text().splitlines()
    text = "\n".join(line for line in lines if line.startswith("#"))
    assert "Image: scene" in text and "Factors: 3" in text
    assert "Decomposition: Covariance" in text and "every 3rd pixel" in text
    assert "Wavelengths: 1700–2600 nm" in text and f"Varda {vardaVersion()}" in text
    rows = list(csv.reader(line for line in lines if not line.startswith("#")))
    assert rows[0] == ["rank", "target", "nrmse", "angle_deg", "ticked", "fits"]
    assert len(rows) == 1 + len(SCENE) + len(DECOYS)
    assert {row[1] for row in rows[1:4]} == set(SCENE)
    assert rows[1][0] == "1" and float(rows[1][3]) >= 0.0


def test_the_filter_hides_other_targets(dialog, qtbot):
    _analyze(dialog, qtbot)
    dialog.filterEdit.setText("kao")
    shown = [
        r for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r)
    ]
    assert [dialog.table.item(r, 1).text() for r in shown] == ["kaolinite"]


def test_ticked_targets_set_up_the_detection_map(dialog, qtbot):
    _analyze(dialog, qtbot)
    assert not dialog.mapButton.isEnabled()
    for row in range(dialog.table.rowCount()):
        if dialog.table.item(row, 1).text() in ("kaolinite", "calcite"):
            dialog.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
    assert dialog.mapButton.text() == "Map 2 Ticked Targets…"
    dialog.threshold.set(0.5)

    with qtbot.waitSignal(dialog.sigMapRequested) as blocker:
        dialog.mapButton.click()
    image, analysis = blocker.args
    assert image.name == "scene"
    assert isinstance(analysis, FattMapAnalysis)
    assert sorted(t.name for t in analysis.targets) == ["calcite", "kaolinite"]
    assert analysis.threshold.get() == 0.5
    assert analysis.minWavelength.get() == 1700.0


def test_choosing_another_mineral_fits_the_plot_to_it_after_a_zoom(dialog, qtbot):
    """Zooming turned the plot's auto-range off, so the next mineral kept the
    zoomed range and could be drawn off the plot."""
    _analyze(dialog, qtbot)
    viewBox = dialog.fitPlot.getPlotItem().getViewBox()
    dialog.table.selectRow(0)
    viewBox.setYRange(100.0, 101.0)  # as a zoom or pan leaves it: off the data

    dialog.table.selectRow(3)
    fit = dialog._fits[3]
    values = np.concatenate([fit.target, fit.fit])
    values = values[np.isfinite(values)]

    def fitted() -> None:  # pyqtgraph applies auto-range on the next update
        low, high = viewBox.viewRange()[1]
        assert low <= values.min() and values.max() <= high

    qtbot.waitUntil(fitted, timeout=2000)
