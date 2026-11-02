"""Auto Align in the Mosaic workspace: match, fit, review bands, add the
displacement field."""

import json

import numpy as np
import pytest
from affine import Affine
from PyQt6.QtCore import Qt
from rasterio.crs import CRS
from scipy import ndimage

from varda.common.entities import VardaRaster
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.workspaces.mosaic.mosaic_workspace import MosaicWorkspace

PIXEL = 18.0
SHIFT = (2.0, -1.0)  # the right strip's content is this far off (px)


def _strip(cube: np.ndarray, firstCol: int, name: str) -> VardaRaster:
    transform = Affine(PIXEL, 0, 500000.0 + firstCol * PIXEL, 0, -PIXEL, 4000000.0)
    source = ArrayDataSource(
        cube.astype(np.float32),
        wavelengths=np.array([500.0, 650.0, 800.0]),
        wavelengthUnits="nm",
        transform=transform,
        crs=CRS.from_epsg(32612),
    )
    return VardaRaster(source, name=name)


@pytest.fixture
def workspace(qtbot):
    rng = np.random.default_rng(0)
    texture = ndimage.gaussian_filter(rng.normal(size=(160, 220)), 2.0)
    scene = np.stack([0.3 + g * texture for g in (0.6, 1.0, 1.4)], axis=2)
    moved = np.real(
        np.fft.ifft2(
            ndimage.fourier_shift(
                np.fft.fft2(scene, axes=(0, 1)), (SHIFT[1], SHIFT[0], 0)
            ),
            axes=(0, 1),
        )
    )
    left, right = _strip(scene[:, :140], 0, "left"), _strip(moved[:, 80:], 80, "right")
    images = [left, right]
    widget = MosaicWorkspace([left, right], images)
    qtbot.addWidget(widget)
    widget.inputsPanel.list.setCurrentRow(1)
    return widget


def _align(workspace, qtbot):
    dialog = workspace.openAutoAlign(1)
    qtbot.addWidget(dialog)
    dialog.tileSize.setCurrentIndex(0)  # 32 px
    dialog.runButton.click()
    qtbot.waitUntil(lambda: dialog.alignment is not None, timeout=20000)
    return dialog


def test_auto_align_fits_the_image(workspace, qtbot):
    dialog = _align(workspace, qtbot)
    expected = (-SHIFT[0] * PIXEL, SHIFT[1] * PIXEL)
    assert np.allclose(
        workspace.project.input(1).correction[:2, 2], expected, atol=0.05 * PIXEL
    )
    assert "Fitted a translation" in dialog.summary.text()
    assert workspace.tiePanel.table.rowCount() == dialog.alignment.added
    assert workspace.tiePanel.table.item(0, 2).text().startswith("Auto, band")


def test_leaving_out_a_band_refits_without_it(workspace, qtbot):
    dialog = _align(workspace, qtbot)
    assert dialog.bands.rowCount() == len(dialog.alignment.bands) > 1
    use = dialog.bands.item(0, dialog.bands.columnCount() - 1)
    band = int(use.data(Qt.ItemDataRole.UserRole))
    use.setCheckState(Qt.CheckState.Unchecked)
    points = [p for p in workspace.project.tiePointsOf(1) if p.band == band]
    assert points and not any(p.enabled for p in points)
    assert "Fitted" in dialog.summary.text()


def test_the_displacement_field_joins_the_images(workspace, qtbot):
    dialog = _align(workspace, qtbot)
    assert dialog.fieldButton.isVisible()
    dialog.fieldButton.click()
    field = workspace.projectImages[-1]
    assert field.name == "Displacement of right from left"
    assert field.bandNames == [
        "East displacement",
        "North displacement",
        "Match quality",
        "Phase-plane SNR",
    ]
    snr = field.getData()[:, :, 3]
    assert np.nanmax(snr) <= 1 and np.isfinite(snr).any()
    east = field.getData()[:, :, 0]
    assert np.isclose(np.nanmedian(east), SHIFT[0] * PIXEL, atol=0.1 * PIXEL)
    record = field.extraMetadata["provenance"]
    assert record["process"] == "Displacement Field"
    assert [i["name"] for i in record["inputs"]] == ["right", "left"]
    assert record["parameters"]["tileSize"] == 32
    assert record["parameters"]["method"] == dialog.alignment.method.name


def test_the_automatic_points_survive_a_session(workspace, qtbot):
    dialog = _align(workspace, qtbot)
    state = json.loads(json.dumps(workspace.sessionState()))  # as a .varda file
    reopened = MosaicWorkspace(workspace.images, [])
    qtbot.addWidget(reopened)
    reopened.restoreSessionState(state)
    points = reopened.project.tiePointsOf(1)
    assert points and all(p.auto for p in points)
    # the outlier threshold refits with, and the provenance of the alignment
    assert reopened._thresholds == {1: dialog.threshold.value()}
    assert reopened._alignments[1]["method"] == dialog.alignment.method.name


def _column(table, name):
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    return headers.index(name)


def test_the_phase_snr_is_shown_per_point_and_per_band(workspace, qtbot):
    dialog = _align(workspace, qtbot)
    table = workspace.tiePanel.table
    column = _column(table, "Phase SNR")
    point = workspace.tiePanel._points()[0]
    assert table.item(0, column).text() == f"{point.phaseSnr:.2f}"
    bandColumn = _column(dialog.bands, "Phase SNR")
    value = float(dialog.bands.item(0, bandColumn).text())
    assert 0 < value <= 1
    assert "Median phase-plane SNR" in dialog.summary.text()


def test_points_without_a_phase_snr_show_it_blank(workspace, qtbot):
    workspace.project.addTiePoint(1, (5, 5), 0, (85, 5))
    workspace.tiePanel.refresh()
    table = workspace.tiePanel.table
    assert table.item(0, _column(table, "Phase SNR")).text() == ""
