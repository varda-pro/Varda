"""One or more pixel-spectra plots per workspace, each in a reopenable dock."""

import numpy as np
import PyQt6Ads as ads
import pytest
from PyQt6.QtCore import QObject, Qt
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
)

from varda.common.di_types import ProjectImages
from varda.common.ui import VardaDockWidget
from varda.utilities.debug import generate_random_image
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
    DualImageWorkspaceConfig,
)
from varda.workspaces.general_image_analysis.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)
from varda.workspaces.pixel_spectra_docks import PixelSpectraDocks


@pytest.fixture
def image():
    return generate_random_image((20, 20, 10))


def _makeDocks(qtbot, **kwargs) -> tuple[QMainWindow, PixelSpectraDocks]:
    # The window must stay referenced for the test's duration: qtbot.addWidget
    # keeps only a weak reference, and deleting the window deletes every dock.
    window = QMainWindow()
    qtbot.addWidget(window)
    manager = ads.CDockManager(window)
    anchor = VardaDockWidget("ROI Plots")
    anchor.setWidget(QLabel("anchor"))
    manager.addDockWidget(ads.DockWidgetArea.BottomDockWidgetArea, anchor)
    return window, PixelSpectraDocks(manager, anchor, parent=window, **kwargs)


@pytest.fixture
def docks(qtbot):
    window, docks = _makeDocks(qtbot)
    yield docks
    del window


def test_first_plot_is_created_active(docks):
    plot = docks.newPlot()
    assert docks.active is plot
    assert docks.activeDock.widget() is plot
    assert "active" in docks.activeDock.windowTitle()


def test_selection_reopens_a_closed_active_dock(docks, image):
    docks.newPlot()
    docks.activeDock.toggleView(False)
    assert docks.activeDock.isClosed()

    curves = docks.addPixelSpectra([image], 1, 1)

    assert len(curves) == 1
    assert not docks.activeDock.isClosed()


def test_new_plot_becomes_the_target_and_titles_are_numbered(docks, image):
    first = docks.newPlot()
    second = docks.newPlot()
    assert docks.active is second
    assert docks.dockFor(second).windowTitle().startswith("Pixel Spectra 2")

    docks.addPixelSpectra([image], 1, 1)
    assert (len(first.pixelCurves), len(second.pixelCurves)) == (0, 1)

    docks.setActive(first)
    docks.addPixelSpectra([image], 3, 3)
    assert (len(first.pixelCurves), len(second.pixelCurves)) == (1, 1)
    assert "active" in docks.dockFor(first).windowTitle()
    assert "active" not in docks.dockFor(second).windowTitle()


def test_checking_receives_clicks_on_a_plot_makes_it_active(docks):
    first = docks.newPlot()
    second = docks.newPlot()

    first.activeCheckBox.setChecked(True)

    assert docks.active is first
    assert not second.activeCheckBox.isChecked()


def test_unchecking_the_only_active_plot_keeps_it_active(docks):
    plot = docks.newPlot()
    plot.activeCheckBox.setChecked(False)
    assert docks.active is plot
    assert plot.activeCheckBox.isChecked()


def test_clicking_inside_a_plot_makes_it_active(qtbot, docks):
    first = docks.newPlot()
    second = docks.newPlot()
    assert docks.active is second

    qtbot.mouseClick(first, Qt.MouseButton.LeftButton)

    assert docks.active is first
    assert first.activeCheckBox.isChecked()
    assert "active" in docks.dockFor(first).windowTitle()


def test_clicking_a_plots_dock_tab_makes_it_active(qtbot, docks):
    first = docks.newPlot()
    docks.newPlot()

    qtbot.mouseClick(docks.dockFor(first).tabWidget(), Qt.MouseButton.LeftButton)

    assert docks.active is first


def test_a_plot_can_be_created_with_its_own_title_without_becoming_active(docks):
    pixels = docks.newPlot()
    ratios = docks.newPlot(title="Ratio Spectra", activate=False)

    assert docks.dockFor(ratios).windowTitle() == "Ratio Spectra"
    assert docks.active is pixels
    assert not ratios.activeCheckBox.isChecked()


def test_a_dedicated_sink_plots_into_its_own_plot_and_leaves_the_active_one_alone(
    docks, image
):
    pixels = docks.newPlot()
    sink = docks.dedicated("Ratio Spectra")
    entry = (np.arange(10.0), np.linspace(1.0, 2.0, 10), "ratio")

    sink.addSpectra([entry])
    sink.addSpectra([entry])

    assert len(docks.plots) == 2
    ratios = docks.plots[1]
    assert docks.dockFor(ratios).windowTitle() == "Ratio Spectra"
    assert len(ratios.pixelCurves) in (1, 2)  # Replace or Collect mode
    assert pixels.pixelCurves == []
    assert docks.active is pixels

    docks.dockFor(ratios).toggleView(False)
    sink.addSpectra([entry])
    assert not docks.dockFor(ratios).isClosed()


def test_new_plot_button_opens_another_plot(docks):
    first = docks.newPlot()
    first.newPlotButton.click()
    assert len(docks.plots) == 2
    assert docks.active is docks.plots[1]


def test_configure_hook_runs_for_every_plot(qtbot):
    seen = []
    window, docks = _makeDocks(qtbot, configurePlot=seen.append)
    docks.newPlot()
    docks.newPlot()
    assert seen == docks.plots
    del window


def test_clicking_deep_inside_a_plot_makes_it_active(qtbot, docks):
    first = docks.newPlot()
    docks.newPlot()
    # the press goes to the plot's graphics view, not the plot widget
    view = first.findChild(QGraphicsView)
    assert view is not None and view.viewport() is not None

    qtbot.mouseClick(view.viewport(), Qt.MouseButton.LeftButton)

    assert docks.active is first


def test_clicking_a_widget_added_to_a_plot_later_makes_it_active(qtbot, docks):
    first = docks.newPlot()
    docks.newPlot()
    late = QPushButton("late", first)

    qtbot.mouseClick(late, Qt.MouseButton.LeftButton)

    assert docks.active is first


def test_clicks_elsewhere_leave_the_active_plot_alone(qtbot, docks):
    docks.newPlot()
    second = docks.newPlot()
    elsewhere = QPushButton("elsewhere")
    qtbot.addWidget(elsewhere)

    qtbot.mouseClick(elsewhere, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(docks._anchor.tabWidget(), Qt.MouseButton.LeftButton)

    assert docks.active is second


def _appFilterInstalls(monkeypatch) -> list[QObject]:
    """The filters installed on the application from now on."""
    installed: list[QObject] = []
    original = QObject.installEventFilter

    def record(self, filterObj):
        if self is QApplication.instance():
            installed.append(filterObj)
        original(self, filterObj)

    monkeypatch.setattr(QObject, "installEventFilter", record)
    return installed


def test_workspaces_install_no_application_wide_filter(qtbot, monkeypatch):
    installed = _appFilterInstalls(monkeypatch)
    first = generate_random_image((20, 30, 10))
    second = generate_random_image((20, 30, 10))
    images = ProjectImages([first, second])
    general = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig(images))
    qtbot.addWidget(general)
    config = DualImageWorkspaceConfig(images)
    config.image1Param.set(first)
    config.image2Param.set(second)
    dual = DualImageWorkspace(config)
    qtbot.addWidget(dual)

    assert installed == []


def test_a_selection_brings_the_pixel_plot_to_the_front(qtbot, docks, image):
    """Release QA #1: the spectrum went to a tab hidden behind ROI Plots."""
    docks.newPlot()
    docks._anchor.setAsCurrentTab()
    assert not docks.activeDock.isCurrentTab()

    docks.addPixelSpectra([image], 1, 1)

    assert docks.activeDock.isCurrentTab()


def test_bringing_the_plot_to_the_front_leaves_keyboard_focus_alone(
    qtbot, docks, image
):
    window = docks.parent()
    docks.newPlot()
    docks._anchor.setAsCurrentTab()
    field = QLineEdit(window)
    window.show()
    qtbot.waitExposed(window)
    field.setFocus()
    qtbot.waitUntil(field.hasFocus)

    docks.addPixelSpectra([image], 1, 1)

    assert docks.activeDock.isCurrentTab()
    assert field.hasFocus()
