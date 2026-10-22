"""The Varda plugin API, version 1: the only module plugins should import.

Everything here is documented and kept stable within an API version
(``api = "1"`` in ``plugin.toml``); anything else in ``varda`` is internal
and may change between releases. Retired names keep working for a release
with a ``VardaDeprecationWarning`` first. See ``docs/plugins/authoring.md``.

A plugin module implements one or more hooks::

    from varda.api import Analysis, FloatParameter, hookimpl

    class BandDepth(Analysis):
        ...

    @hookimpl
    def registerAnalyses():
        return [BandDepth]
"""

from varda.analysis.analysis import Analysis, AnalysisContext, ProgressCallback
from varda.analysis.rasters import readCube, sameBandsRaster, singleBandRaster
from varda.catalogs.catalog import (
    BrowseImage,
    Catalog,
    CatalogError,
    CatalogProgress,
    Collection,
    Product,
    RemoteFile,
    SearchPage,
    SearchQuery,
)
from varda.common.entities import VardaRaster
from varda.common.parameter import (
    BoolParameter,
    EnumParameter,
    FloatParameter,
    IntParameter,
    MultiChoiceParameter,
    StringParameter,
    Vec2Parameter,
)
from varda.common.spectral_axis import BandModel
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.image_loading.data_sources.data_source import DataSource
from varda.plugins._hooks import hookimpl
from varda.plugins.manifest import API_VERSION

__all__ = [
    "API_VERSION",
    "Analysis",
    "AnalysisContext",
    "ArrayDataSource",
    "BandModel",
    "BoolParameter",
    "BrowseImage",
    "Catalog",
    "CatalogError",
    "CatalogProgress",
    "Collection",
    "DataSource",
    "EnumParameter",
    "FloatParameter",
    "IntParameter",
    "MultiChoiceParameter",
    "Product",
    "ProgressCallback",
    "RemoteFile",
    "SearchPage",
    "SearchQuery",
    "StringParameter",
    "VardaRaster",
    "Vec2Parameter",
    "hookimpl",
    "readCube",
    "sameBandsRaster",
    "singleBandRaster",
]
