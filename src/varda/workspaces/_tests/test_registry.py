"""The workspace registry: every workspace implements the Workspace protocol,
and session kinds name one workspace each."""

import inspect
import typing

from PyQt6.QtWidgets import QWidget

from varda.workspaces.registry import WORKSPACES, buildWorkspace, registered
from varda.workspaces.workspace import Workspace


def test_each_kind_names_one_workspace():
    kinds = [workspace.kind for workspace in WORKSPACES]
    assert len(set(kinds)) == len(kinds)
    assert all(isinstance(kind, str) and kind for kind in kinds)


def test_every_workspace_implements_the_protocol():
    # roiCollection may be set in __init__, so it isn't on every class
    members = typing.get_protocol_members(Workspace) - {"roiCollection"}
    for workspace in WORKSPACES:
        assert issubclass(workspace, QWidget)
        for name in members:
            assert hasattr(workspace, name), (workspace, name)
        assert inspect.ismethod(workspace.fromSession), workspace  # a classmethod


def test_only_registered_workspaces_are_known(qtbot):
    widget = QWidget()
    qtbot.addWidget(widget)
    assert registered(widget) is None


def test_an_unknown_kind_is_skipped():
    assert buildWorkspace("no_such_kind", [], []) is None
