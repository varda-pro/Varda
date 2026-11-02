"""
Plugin manager for the Varda application.

Plugins come from three places:

- **Installed plugins**: folders with a ``plugin.toml`` in Varda's app-data
  ``plugins`` folder, put there by Plugins → Install from Folder/Zip (or
  Kindle a Feature). They load in the packaged app too, and can be enabled,
  disabled, reloaded and removed.
- **Entry points** in the ``varda.plugins`` group, for plugins installed as
  Python packages.
- **The development folder** ``plugins/user_plugins/`` in the source tree
  (not in the packaged app).

What a plugin adds (analyses, file formats) is handed to a
``ContributionSink``, which the app implements, so this module knows nothing
about menus.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Protocol

import attrs
from pluggy import PluginManager
from PyQt6.QtCore import QStandardPaths

import varda
from varda.analysis.analysis import Analysis
from varda.catalogs.catalog import Catalog
from varda.plugins.manifest import (
    MANIFEST_NAME,
    PluginError,
    PluginManifest,
    readManifest,
)

logger = logging.getLogger(__name__)

SKIPPED_FOLDER = "examples"  # sample plugins, documentation only
STATE_FILE = "state.json"  # which installed plugins are disabled
INSTALLED = "installed"
ENTRY_POINT = "entry point"
DEVELOPMENT = "development"


@attrs.frozen
class Contributions:
    """What one plugin adds to Varda."""

    analyses: tuple[type[Analysis], ...] = ()
    # (format name, file extensions, DataSource class)
    dataSources: tuple[tuple[str, tuple[str, ...], type], ...] = ()
    catalogs: tuple[type[Catalog], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.analyses or self.dataSources or self.catalogs)


class ContributionSink(Protocol):
    """Adds a plugin's contributions to the app; returns how to take them away."""

    def add(self, plugin: str, contributions: Contributions) -> Callable[[], None]: ...


@attrs.define(eq=False)
class PluginRecord:
    """One plugin Varda knows about, loaded or not."""

    name: str
    source: str  # INSTALLED, ENTRY_POINT or DEVELOPMENT
    folder: Path | None = None
    manifest: PluginManifest | None = None
    enabled: bool = True
    module: object | None = None
    error: str = ""
    contributions: Contributions = attrs.field(factory=Contributions)
    _remove: Callable[[], None] | None = None

    @property
    def loaded(self) -> bool:
        return self.module is not None

    @property
    def version(self) -> str:
        return self.manifest.version if self.manifest is not None else ""


def _importFromPath(moduleName: str, source: Path) -> ModuleType:
    """Import a plugin file or package ``__init__.py`` under ``moduleName``."""
    spec = importlib.util.spec_from_file_location(
        moduleName,
        source,
        submodule_search_locations=[str(source.parent)]
        if source.name == "__init__.py"
        else None,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot import {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[moduleName] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[moduleName]
        raise
    return module


def _moduleName(name: str) -> str:
    return "varda_plugin_" + name.replace("-", "_")


def newHookManager() -> PluginManager:
    """A pluggy manager that knows Varda's hooks."""
    pm = PluginManager("varda")
    pm.add_hookspecs(varda.plugins._hooks)
    pm.register(varda.plugins._hooks)
    return pm


def importPlugin(manifest: PluginManifest, folder: Path) -> ModuleType:
    """Import an installed plugin's entry module, refusing one whose
    requirements Varda doesn't meet."""
    missing = manifest.missingRequirements()
    if missing:
        raise PluginError(f"Needs {', '.join(missing)}, which Varda doesn't have.")
    # No bytecode cache: reload always reads the source (a cached .pyc can
    # look current within the same second), and the folder stays clean for
    # zipping and sharing
    writeBytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        return _importFromPath(_moduleName(manifest.name), folder / manifest.entry)
    finally:
        sys.dont_write_bytecode = writeBytecode


def collectContributions(
    pm: PluginManager, module: object, plugin: str
) -> Contributions:
    """What ``module``, registered with ``pm``, adds through its hook
    implementations; ``plugin`` (name and version) labels its analyses and
    catalogs."""
    analyses: list[type[Analysis]] = []
    for analysis in _returned(pm, module, "registerAnalyses"):
        if not (isinstance(analysis, type) and issubclass(analysis, Analysis)):
            raise PluginError(
                f"registerAnalyses returned {analysis!r}, not an Analysis."
            )
        analysis.plugin = plugin
        analyses.append(analysis)
    dataSources = []
    for entry in _returned(pm, module, "registerDataSources"):
        formatName, extensions, cls = entry
        exts = (extensions,) if isinstance(extensions, str) else extensions
        dataSources.append((str(formatName), tuple(exts), cls))
    catalogs: list[type[Catalog]] = []
    for catalog in _returned(pm, module, "registerCatalogs"):
        if not (isinstance(catalog, type) and issubclass(catalog, Catalog)):
            raise PluginError(f"registerCatalogs returned {catalog!r}, not a Catalog.")
        catalog.plugin = plugin
        catalogs.append(catalog)
    return Contributions(tuple(analyses), tuple(dataSources), tuple(catalogs))


def _returned(pm: PluginManager, module: object, hookName: str) -> list:
    """Everything ``module``'s implementation of a registration hook returned
    (a list or tuple), or nothing if it doesn't implement it."""
    items: list = []
    for impl in getattr(pm.hook, hookName).get_hookimpls():
        if impl.plugin is not module:
            continue
        returned = impl.function()
        if returned is None:
            continue
        if not isinstance(returned, (list, tuple)):
            raise PluginError(f"{hookName} must return a list, not {returned!r}.")
        items.extend(returned)
    return items


def defaultPluginsFolder() -> Path:
    """``plugins`` in Varda's app-data folder (beside the logs and autosave)."""
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(base) / "plugins"


class VardaPluginManager:
    """Discovers, loads and manages Varda's plugins."""

    def __init__(self, pluginsFolder: Path | None = None):
        self.pm = newHookManager()
        self.pluginsFolder = pluginsFolder
        self.records: list[PluginRecord] = []
        self._sink: ContributionSink | None = None
        # load plugins from entrypoints
        for plugin, distribution in self._entryPointPlugins():
            self.records.append(PluginRecord(distribution, ENTRY_POINT, module=plugin))

        # load plugins from local "user_plugins" package
        # plugins can either be standalone .py files, or an installed package
        currPath = Path(varda.__file__).resolve().parent
        pluginFolder = currPath / "plugins" / "user_plugins"
        self._registerPluginsInFolder(pluginFolder)

    def _entryPointPlugins(self) -> list[tuple[object, str]]:
        before = set(self.pm.get_plugins())
        self.pm.load_setuptools_entrypoints("varda.plugins")
        return [
            (plugin, self.pm.get_name(plugin) or "plugin")
            for plugin in self.pm.get_plugins()
            if plugin not in before
        ]

    def _registerPluginsInFolder(self, pluginFolder: Path) -> None:
        """Register each plugin in ``pluginFolder``: a ``.py`` file or a package.

        Skipped: the packaged app (its folder isn't user-editable), the
        ``examples`` folder, and names starting with ``_``. A plugin that fails
        to import is logged and skipped so it can't stop Varda starting.
        """
        if getattr(sys, "frozen", False):
            logger.info("Packaged app: not loading plugins from %s", pluginFolder)
            return
        if not Path(pluginFolder).is_dir():
            return
        for path in sorted(Path(pluginFolder).iterdir()):
            name = path.name
            if name.startswith("_") or name == SKIPPED_FOLDER:
                continue
            if path.suffix == ".py":
                moduleName, source = path.stem, path
            elif path.is_dir() and (path / "__init__.py").is_file():
                moduleName, source = name, path / "__init__.py"
            else:
                continue
            try:
                module = _importFromPath(f"varda_user_plugin_{moduleName}", source)
            except Exception:  # a broken plugin must not stop Varda starting
                logger.exception("Could not load plugin %s", path)
                continue
            self.pm.register(module, name=moduleName)
            self.records.append(PluginRecord(moduleName, DEVELOPMENT, module=module))

    @property
    def hook(self):
        """
        Access the hook interface of the plugin manager.
        """
        return self.pm.hook

    # --- installed plugins --------------------------------------------------

    def loadInstalled(self, sink: ContributionSink | None = None) -> None:
        """Find the installed plugins, load the enabled ones, and hand every
        plugin's contributions (entry points and development ones too) to
        ``sink``."""
        self._sink = sink
        for record in self.records:  # entry points and development plugins
            if record.loaded:
                self._contribute(record)
        if self.pluginsFolder is None or not self.pluginsFolder.is_dir():
            return
        disabled = self._disabled()
        for folder in sorted(self.pluginsFolder.iterdir()):
            if not (folder / MANIFEST_NAME).is_file():
                continue
            record = PluginRecord(folder.name, INSTALLED, folder=folder)
            record.enabled = folder.name not in disabled
            self.records.append(record)
            if record.enabled:
                self._load(record)

    def record(self, name: str) -> PluginRecord:
        for record in self.records:
            if record.name == name:
                return record
        raise KeyError(name)

    def install(self, path: Path, replace: bool = False) -> PluginRecord:
        """Install a plugin from a folder or a ``.zip`` of one and load it.
        Raises PluginError with a message for the user."""
        if self.pluginsFolder is None:
            raise PluginError("Varda has no plugins folder.")
        path = Path(path)
        with tempfile.TemporaryDirectory() as unpacked:
            folder = (
                self._unpack(path, Path(unpacked)) if path.suffix == ".zip" else path
            )
            manifest = readManifest(folder)
            target = self.pluginsFolder / manifest.name
            existing = next((r for r in self.records if r.name == manifest.name), None)
            if existing is not None and not replace:
                raise PluginError(
                    f"A plugin called {manifest.name} is already installed."
                )
            if existing is not None:
                self.remove(existing.name)
            self.pluginsFolder.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                folder, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
            )
        record = PluginRecord(manifest.name, INSTALLED, folder=target)
        self.records.append(record)
        self._load(record)
        return record

    def remove(self, name: str) -> None:
        """Unload an installed plugin and delete its folder."""
        record = self.record(name)
        if record.source != INSTALLED or record.folder is None:
            raise PluginError(
                f"{name} isn't an installed plugin; uninstall it with pip."
            )
        self._unload(record)
        shutil.rmtree(record.folder, ignore_errors=True)
        self.records.remove(record)
        self._saveDisabled()

    def setEnabled(self, name: str, enabled: bool) -> None:
        record = self.record(name)
        if record.source != INSTALLED:
            raise PluginError(f"{name} can't be disabled from Varda.")
        record.enabled = enabled
        self._saveDisabled()
        if enabled and not record.loaded:
            self._load(record)
        elif not enabled:
            self._unload(record)

    def reload(self, name: str) -> None:
        """Re-import an installed plugin, e.g. after its code changed."""
        record = self.record(name)
        self._unload(record)
        if record.enabled:
            self._load(record)

    # --- internals ------------------------------------------------------------

    @staticmethod
    def _unpack(archive: Path, into: Path) -> Path:
        """The plugin folder inside a zip: the root, or its only folder."""
        try:
            with zipfile.ZipFile(archive) as zf:
                for member in zf.namelist():
                    target = (into / member).resolve()
                    if not target.is_relative_to(into.resolve()):
                        raise PluginError(
                            f"{archive.name} has an unsafe path: {member}"
                        )
                zf.extractall(into)
        except zipfile.BadZipFile as error:
            raise PluginError(f"{archive.name} isn't a valid zip file.") from error
        if (into / MANIFEST_NAME).is_file():
            return into
        folders = [p for p in into.iterdir() if p.is_dir() and p.name != "__MACOSX"]
        if len(folders) == 1 and (folders[0] / MANIFEST_NAME).is_file():
            return folders[0]
        raise PluginError(f"{archive.name} has no {MANIFEST_NAME}.")

    def _load(self, record: PluginRecord) -> None:
        """Import and register an installed plugin; a failure is recorded on it
        (shown in Manage Plugins) instead of raised."""
        assert record.folder is not None
        record.error = ""
        try:
            manifest = readManifest(record.folder)
            record.manifest = manifest
            module = importPlugin(manifest, record.folder)
            self.pm.register(module, name=manifest.name)
            record.module = module
            self._contribute(record)
        except Exception as error:  # a broken plugin must not stop Varda
            logger.exception("Could not load plugin %s", record.name)
            record.error = str(error) or type(error).__name__
            self._unload(record)

    def _contribute(self, record: PluginRecord) -> None:
        contributions = self._collect(record)
        record.contributions = contributions
        if contributions and self._sink is not None:
            record._remove = self._sink.add(record.name, contributions)

    def _collect(self, record: PluginRecord) -> Contributions:
        return collectContributions(
            self.pm, record.module, f"{record.name} {record.version}".strip()
        )

    def _unload(self, record: PluginRecord) -> None:
        if record._remove is not None:
            record._remove()
            record._remove = None
        if record.module is not None and self.pm.is_registered(record.module):
            self.pm.unregister(record.module)
        record.module = None
        record.contributions = Contributions()
        prefix = _moduleName(record.name)
        for name in [
            n for n in sys.modules if n == prefix or n.startswith(prefix + ".")
        ]:
            del sys.modules[name]

    def _disabled(self) -> set[str]:
        path = self._statePath()
        try:
            return set(json.loads(path.read_text()).get("disabled", []))
        except (OSError, ValueError, AttributeError):
            return set()

    def _saveDisabled(self) -> None:
        path = self._statePath()
        disabled = sorted(
            r.name for r in self.records if r.source == INSTALLED and not r.enabled
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"disabled": disabled}, indent=2))

    def _statePath(self) -> Path:
        assert self.pluginsFolder is not None
        return self.pluginsFolder / STATE_FILE
