from varda._actions._data_actions import DATA_ACTIONS
from varda._actions._debug_actions import DEBUG_ACTIONS
from varda._actions._file_actions import FILE_ACTIONS
from varda._actions._help_actions import HELP_ACTIONS
from varda._actions._menu_ids import MENUBAR as MENUBAR
from varda._actions._plugin_actions import PLUGIN_ACTIONS
from varda._actions._processing_actions import (
    PROCESSING_ACTIONS,
    PROCESSING_SUBMENUS as PROCESSING_SUBMENUS,
)
from varda._actions._view_actions import VIEW_ACTIONS
from varda._actions._workspace_actions import WORKSPACE_ACTIONS

ALL_ACTIONS = [
    *FILE_ACTIONS,
    *DATA_ACTIONS,
    *WORKSPACE_ACTIONS,
    *VIEW_ACTIONS,
    *PROCESSING_ACTIONS,
    *PLUGIN_ACTIONS,
    *DEBUG_ACTIONS,
    *HELP_ACTIONS,
]
