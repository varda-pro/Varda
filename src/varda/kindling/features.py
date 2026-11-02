"""What Varda can already do, for the model to compare a request with: every
menu command (Varda's own and installed plugins'), the file formats it opens
and the catalogs Find Data searches."""

from __future__ import annotations

from app_model import Application
from app_model.types import MenuItem, SubmenuItem

from varda.catalogs.registry import availableCatalogs
from varda.image_loading.data_sources.registry import datasource_registry

# Menus a user reaches, by app_model id, with their names (the menu bar's
# but Debug, then the context menus)
MENUS = {
    "varda/file": "File",
    "varda/data": "Data",
    "varda/workspace": "Workspace",
    "varda/view": "View",
    "varda/processing": "Processing",
    "varda/plugins": "Plugins",
    "varda/help": "Help",
    "varda/viewport/context": "Image view (right-click)",
    "varda/image_list/context": "Image list (right-click)",
}


def _title(text: str) -> str:
    return text.replace("&", "").rstrip("…").rstrip(".").strip()


def _walk(app: Application, menuId: str, path: str, lines: list[str]) -> None:
    for item in app.menus.get_menu(menuId) if menuId in app.menus else ():
        if isinstance(item, SubmenuItem):
            _walk(app, str(item.submenu), f"{path} → {_title(item.title)}", lines)
        elif isinstance(item, MenuItem):
            command = item.command
            line = f"- {path} → {_title(str(command.title))}"
            tip = command.tooltip or command.status_tip
            if tip:
                line += f": {tip}"
            lines.append(line)


def featureInventory(app: Application | None) -> str:
    """Varda's features as a plain list, one per line, with where they are."""
    lines: list[str] = []
    if app is not None:
        for menuId, name in MENUS.items():
            _walk(app, menuId, name, lines)
    formats = ", ".join(
        f"{d.formatName} ({' '.join(d.fileExtensions)})" for d in datasource_registry
    )
    catalogs = ", ".join(c.name for c in availableCatalogs())
    lines.append(f"- Opens these file formats: {formats}")
    lines.append(f"- Find and Fetch Data searches these catalogs: {catalogs}")
    return "\n".join(dict.fromkeys(lines))
