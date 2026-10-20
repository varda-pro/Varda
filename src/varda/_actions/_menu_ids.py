from enum import StrEnum


class MenuId(StrEnum):
    FILE = "varda/file"
    DATA = "varda/data"
    WORKSPACE = "varda/workspace"
    VIEW = "varda/view"
    PROCESSING = "varda/processing"
    PLUGINS = "varda/plugins"
    DEBUG = "varda/debug"
    HELP = "varda/help"


class MenuGroup(StrEnum):
    FILE_IO = "1_io"
    FILE_SESSION = "2_session"
    FILE_EXIT = "9_exit"
    DATA_FIND = "1_find"
    WORKSPACE_NEW = "1_new"
    WORKSPACE_MANAGE = "2_manage"
    PLUGINS_KINDLE = "0_kindle"
    PLUGINS_MANAGE = "1_manage"
    PLUGINS_INSTALL = "2_install"
    PLUGINS_FEATURES = "3_features"  # what installed plugins add, also listed here
    DEBUG_TESTING = "1_testing"
    HELP_LINKS = "1_links"
    HELP_INFO = "2_info"
    HELP_ABOUT = "9_about"


MENUBAR: list[tuple[str, str]] = [
    (MenuId.FILE, "File"),
    (MenuId.DATA, "Data"),
    (MenuId.WORKSPACE, "Workspace"),
    (MenuId.VIEW, "View"),
    (MenuId.PROCESSING, "Processing"),
    (MenuId.PLUGINS, "Plugins"),
    (MenuId.DEBUG, "Debug"),
    (MenuId.HELP, "Help"),
]


def menuBar(debug: bool) -> list[tuple[str, str]]:
    """The menu bar; the Debug menu only in development (``VARDA_DEBUG=1``)."""
    return [entry for entry in MENUBAR if debug or entry[0] != MenuId.DEBUG]
