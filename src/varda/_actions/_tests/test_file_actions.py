"""Importing images by path goes through the same loading pipeline as the dialog."""

from app_model.types import KeyCode, KeyMod, StandardKeyBinding

from varda._actions import _file_actions
from varda.common.di_types import ProjectImages


def test_import_image_paths_loads_each_file_into_the_project(monkeypatch):
    calls = []
    monkeypatch.setattr(
        _file_actions.ImageLoadingService,
        "load_images",
        classmethod(lambda cls, **kwargs: calls.append(kwargs)),
    )
    images = ProjectImages()

    _file_actions.importImagePaths(["/data/a.img", "/data/b.tif"], images)

    (call,) = calls
    assert call["file_paths"] == ["/data/a.img", "/data/b.tif"]
    assert call["on_success_callback"] == images.append


def test_open_imports_images_and_shift_open_opens_a_session():
    """Release QA #23: Ctrl/⌘+O opened a session where users expect an image."""
    keys = {
        action.id: [rule.primary for rule in action.keybindings or []]
        for action in _file_actions.FILE_ACTIONS
    }
    open_ = StandardKeyBinding.Open.to_keybinding_rule().primary
    assert keys["varda.file.import_image"] == [open_]
    assert keys["varda.file.open_session"] == [
        KeyMod.CtrlCmd | KeyMod.Shift | KeyCode.KeyO
    ]
