"""Each spectral plot's Libraries choice is saved with the session and
restored; sessions saved before it existed open with every library on."""

from varda.common.di_types import ProjectImages
from varda.session import SessionState, WorkspaceState, readSession, writeSession
from varda.spectral_libraries.store import (
    MICA_LIBRARY_NAME,
    USGS_LIBRARY_NAME,
    defaultStore,
)
from varda.utilities.debug import generate_random_image
from varda.workspaces.dual_image_workspace.dual_image_workspace import (
    DualImageWorkspace,
    DualImageWorkspaceConfig,
)
from varda.workspaces.general_image_analysis import (
    GeneralImageAnalysisConfig,
    GeneralImageAnalysisWorkflow,
)
from varda.workspaces.workspace_kinds import buildWorkspace, describeWorkspace


def _reopen(qtbot, workspace, images, tmp_path):
    """Save the workspace in a session file, read it back and rebuild it."""
    described = describeWorkspace(workspace)
    assert described is not None
    kind, shown = described
    paths = [f"/data/{i}.img" for i in range(len(shown))]
    state = SessionState(
        paths, [WorkspaceState(kind, paths, {}, workspace.sessionState())]
    )
    (saved,) = readSession(writeSession(state, tmp_path / "s")).workspaces
    copy = buildWorkspace(kind, shown, images, saved.extra)
    qtbot.addWidget(copy)
    return copy


def _general(qtbot, images):
    workspace = GeneralImageAnalysisWorkflow(GeneralImageAnalysisConfig(images))
    qtbot.addWidget(workspace)
    return workspace


def test_the_general_workspaces_plots_keep_their_libraries(qtbot, tmp_path):
    images = ProjectImages([generate_random_image((20, 20, 10))])
    source = _general(qtbot, images)
    source.plotWidget.referencePicker.setExcluded([MICA_LIBRARY_NAME])
    second = source.pixelSpectraDocks.newPlot()
    second.referencePicker.setExcluded([USGS_LIBRARY_NAME])

    copy = _reopen(qtbot, source, images, tmp_path)
    assert copy.plotWidget.referencePicker.excludedLibraries() == [MICA_LIBRARY_NAME]
    first, *later = copy.pixelSpectraDocks.plots
    assert first.referencePicker.excludedLibraries() == []
    assert later == []  # a second pixel plot is opened when it's needed...
    reopened = copy.pixelSpectraDocks.newPlot()
    # ...and gets the second plot's choice
    assert reopened.referencePicker.excludedLibraries() == [USGS_LIBRARY_NAME]


def test_the_dual_workspaces_plots_keep_their_libraries(qtbot, tmp_path):
    images = ProjectImages([generate_random_image((20, 20, 10)) for _ in range(2)])
    config = DualImageWorkspaceConfig(images)
    config.image2Param.set(images[1])
    source = DualImageWorkspace(config)
    qtbot.addWidget(source)
    source.plotWidget.referencePicker.setExcluded([USGS_LIBRARY_NAME])
    source.pixelPlotWidget.referencePicker.setExcluded([MICA_LIBRARY_NAME])

    copy = _reopen(qtbot, source, images, tmp_path)
    assert isinstance(copy, DualImageWorkspace)
    assert copy.plotWidget.referencePicker.excludedLibraries() == [USGS_LIBRARY_NAME]
    assert copy.pixelPlotWidget.referencePicker.excludedLibraries() == [
        MICA_LIBRARY_NAME
    ]


def test_a_session_saved_before_the_choice_was_kept_opens_with_all_on(qtbot):
    defaultStore().setPlotLibrariesOff({MICA_LIBRARY_NAME})  # new plots' default
    images = ProjectImages([generate_random_image((20, 20, 10)) for _ in range(2)])
    for kind, old in [("general", {"lines": []}), ("dual", {})]:
        workspace = buildWorkspace(kind, list(images), images, old)
        qtbot.addWidget(workspace)
        assert isinstance(workspace, (GeneralImageAnalysisWorkflow, DualImageWorkspace))
        assert workspace.plotWidget.referencePicker.excludedLibraries() == []
        assert workspace.pixelPlotWidget.referencePicker.excludedLibraries() == []


def test_new_workspaces_plots_start_with_the_most_recent_choice(qtbot):
    defaultStore().setPlotLibrariesOff({MICA_LIBRARY_NAME})
    workspace = _general(qtbot, ProjectImages([generate_random_image((20, 20, 10))]))
    assert workspace.plotWidget.referencePicker.excludedLibraries() == [
        MICA_LIBRARY_NAME
    ]
    assert workspace.pixelPlotWidget.referencePicker.excludedLibraries() == [
        MICA_LIBRARY_NAME
    ]
