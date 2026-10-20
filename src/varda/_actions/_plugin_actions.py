"""The Plugins menu: kindle a feature with your AI, manage installed plugins
and install new ones."""

from __future__ import annotations

from app_model.types import Action, MenuRule

from varda._actions._menu_ids import MenuGroup, MenuId
from varda.common.di_types import ProjectImages
from varda.common.ui import openOwnedDialog
from varda.kindling.ai_settings import AISettings
from varda.kindling.ai_settings_dialog import AISettingsDialog
from varda.kindling.kindle_dialog import KindleDialog
from varda.maingui import MainGUI
from varda.plugins.plugin_manager import VardaPluginManager
from varda.plugins.plugins_dialog import (
    ManagePluginsDialog,
    chooseFolder,
    chooseZip,
    installPlugin,
)


def kindleFeature(
    manager: VardaPluginManager, images: ProjectImages, mainGui: MainGUI
) -> None:
    """Open Kindle a Feature; until an AI is set up, the AI settings first."""
    settings = AISettings()

    def kindle() -> None:
        openOwnedDialog(KindleDialog(manager, list(images), settings, parent=mainGui))

    if settings.isReady():
        kindle()
        return
    dialog = AISettingsDialog(settings, parent=mainGui)
    dialog.accepted.connect(lambda: kindle() if settings.isReady() else None)
    openOwnedDialog(dialog)


def aiSettings(mainGui: MainGUI) -> None:
    openOwnedDialog(AISettingsDialog(parent=mainGui))


def managePlugins(manager: VardaPluginManager, mainGui: MainGUI) -> None:
    openOwnedDialog(ManagePluginsDialog(manager, parent=mainGui))


def installFromFolder(manager: VardaPluginManager, mainGui: MainGUI) -> None:
    folder = chooseFolder(mainGui)
    if folder is not None:
        installPlugin(manager, folder, mainGui)


def installFromZip(manager: VardaPluginManager, mainGui: MainGUI) -> None:
    archive = chooseZip(mainGui)
    if archive is not None:
        installPlugin(manager, archive, mainGui)


PLUGIN_ACTIONS: list[Action] = [
    Action(
        id="varda.plugins.kindle",
        title="Kindle a Feature (Build a plugin with AI)…",
        tooltip="Describe a feature and have your AI build it as a plugin",
        callback=kindleFeature,
        menus=[MenuRule(id=MenuId.PLUGINS, group=MenuGroup.PLUGINS_KINDLE, order=1)],
    ),
    Action(
        id="varda.plugins.ai_settings",
        title="AI Assistant Settings…",
        tooltip="The AI provider, API key and model Kindle a Feature uses",
        callback=aiSettings,
        menus=[MenuRule(id=MenuId.PLUGINS, group=MenuGroup.PLUGINS_KINDLE, order=2)],
    ),
    Action(
        id="varda.plugins.manage",
        title="Manage Plugins…",
        tooltip="See installed plugins and what they add; enable, disable, reload or remove them",
        callback=managePlugins,
        menus=[MenuRule(id=MenuId.PLUGINS, group=MenuGroup.PLUGINS_MANAGE, order=1)],
    ),
    Action(
        id="varda.plugins.install_folder",
        title="Install from Folder…",
        tooltip="Install a plugin from a folder containing plugin.toml",
        callback=installFromFolder,
        menus=[MenuRule(id=MenuId.PLUGINS, group=MenuGroup.PLUGINS_INSTALL, order=1)],
    ),
    Action(
        id="varda.plugins.install_zip",
        title="Install from Zip…",
        tooltip="Install a plugin from a .zip of its folder",
        callback=installFromZip,
        menus=[MenuRule(id=MenuId.PLUGINS, group=MenuGroup.PLUGINS_INSTALL, order=2)],
    ),
]
