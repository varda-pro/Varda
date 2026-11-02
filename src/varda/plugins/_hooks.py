"""
Hook specifications for plugins to implement.
"""

import logging

import pluggy

logger = logging.getLogger(__package__)

hookspec = pluggy.HookspecMarker("varda")
hookimpl = pluggy.HookimplMarker("varda")


@hookspec
def onLoad(app):
    """Hook called on plugin load."""


@hookspec
def onUnload(app):
    """Hook called on plugin unload."""


@hookspec
def registerAnalyses():
    """Return ``Analysis`` subclasses. Each appears in the Processing menu under
    its ``category``, with a settings dialog generated from its Parameters."""


@hookspec
def registerCatalogs():
    """Return ``Catalog`` subclasses: sources of data the Find Data workspace
    can search (footprints on a map) and download from."""


@hookspec
def registerDataSources():
    """Return ``(formatName, fileExtensions, DataSourceClass)`` tuples: file
    formats Varda can open, offered in File → Import Image(s)…"""


# Varda's own wrapper around every plugin's onLoad (this module registers
# itself as a plugin), so one failing plugin is logged instead of raised
@hookimpl(wrapper=True)
def onLoad(app):  # noqa: F811
    try:
        logger.info("Initializing plugins...")
        yield
        logger.info("Plugins initialized successfully!")
    except Exception as e:
        logger.error(
            f"One of the plugins failed to initialize! Exception: {e}", exc_info=True
        )


# def hookimpl(func=None, **hookopts):
#     def decorate(fn):
#
#         @functools.wraps(fn)
#         def startup_wrapper(*args, **kwargs):
#             pluginName = fn.__module__.split('.')[-1]
#             try:
#                 logger.info(f"Starting Plugin: {pluginName}")
#                 return fn(*args, **kwargs)
#             except Exception as e:
#                 logger.error(f"Error in {pluginName}: error: {e}", exc_info=True)
#
#         return _hookimpl(**hookopts)(startup_wrapper)
#
#     if func:
#         return decorate(func)
#     return decorate
