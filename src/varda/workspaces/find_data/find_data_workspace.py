"""Find Data: search a catalog for observations of a place, see their
footprints on a map, and download them. Varda knows no missions:
the catalogs (``varda.catalogs``, and plugins' ``registerCatalogs``) do."""

from __future__ import annotations

from collections.abc import MutableSequence
from datetime import datetime, time, timezone

import geopandas as gpd
import numpy as np
import PyQt6Ads as ads
import shapely
from PyQt6.QtCore import (
    QDate,
    QModelIndex,
    QSortFilterProxyModel,
    QStringListModel,
    Qt,
)
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from varda.catalogs.basemaps import BasemapLayer, bundledImage, layersFor
from varda.catalogs.bodies import toLongitudeLatitude
from varda.catalogs.catalog import (
    Catalog,
    CatalogError,
    Collection,
    Product,
    SearchPage,
    SearchQuery,
)
from varda.catalogs.credentials import hasCredentials, loadCredentials
from varda.catalogs.footprints import FootprintFileCatalog
from varda.catalogs.gazetteer import (
    GAZETTEER_CREDIT,
    featureView,
    loadGazetteer,
    search,
)
from varda.catalogs.http import fetchBytes
from varda.catalogs.registry import availableCatalogs
from varda.catalogs.stac import StacCatalog
from varda.common.entities import VardaRaster
from varda.common.observable_list import ObservableList
from varda.common.parameter_widgets import groupWidget
from varda.common.ui import (
    ElidingComboBox,
    FlowLayout,
    HBoxBuilder,
    MutedLabel,
    VardaDockWidget,
    WrapperWidget,
)
from varda.image_loading.image_loading_service import ImageLoadingService
from varda.utilities.background import runInBackground
from varda.workspaces.find_data.basemap import (
    MapFrame,
    basemap,
    globalBasemap,
    imageBody,
    imageBounds,
    imageMapBounds,
)
from varda.workspaces.find_data.catalog_dialogs import (
    CredentialsDialog,
    StacApisDialog,
    applySavedStacApis,
)
from varda.workspaces.find_data.collection_picker import CollectionCombo
from varda.workspaces.find_data.downloads_panel import DownloadsPanel
from varda.workspaces.find_data.map_view import ADD, REMOVE, FootprintMap
from varda.workspaces.find_data.imagery import ImageryStreamer
from varda.workspaces.find_data.projection import Bounds, MapProjection
from varda.workspaces.find_data.results_model import SORT_ROLE, ResultsModel

AREA_VIEW, AREA_BOX, AREA_IMAGE = "Map view", "Box on the map", "An image's footprint"
DEFAULT_LIMIT = 100
THUMBNAIL_SIZE, THUMBNAIL_MIN = 260, 160  # px: the largest, and the panel's least


class SelectedFilter(QSortFilterProxyModel):
    """Sorts the results; with ``setShown``, lists only those products."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.shown: set[str] | None = None

    def setShown(self, ids: set[str] | None) -> None:
        self.shown = ids
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:  # noqa: N803 (Qt's names)
        if self.shown is None:
            return True
        model = self.sourceModel()
        return (
            isinstance(model, ResultsModel)
            and model.products[source_row].id in self.shown
        )


class FindDataWorkspace(QMainWindow):
    def __init__(
        self,
        projectImages: MutableSequence[VardaRaster] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Find Data")
        self.projectImages = projectImages if projectImages is not None else []
        if isinstance(self.projectImages, ObservableList):
            # images loaded later (a download, say) can be basemaps too
            self.projectImages.sigDataChanged.connect(
                lambda _: self._refreshImageLists()
            )
        self._catalogs: dict[str, Catalog] = {}  # one each, so filters persist
        self._collections: list[Collection] = []
        self._query: SearchQuery | None = None
        self._resultsCatalog: Catalog | None = None  # the catalog that found them
        self._nextPage: object | None = None
        self.overlayPaths: list[str] = []
        self._pendingLayer: str | None = None  # a session's basemap layer id
        self._basemapRequest = 0  # the latest basemap drawing asked for

        self.map = FootprintMap()
        self.map.sigSelect.connect(self._onMapSelect)
        self.streamer = ImageryStreamer(self.map, self)
        self.results = ResultsModel(self)
        self.proxy = SelectedFilter(self)
        self.proxy.setSourceModel(self.results)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        selection = self.table.selectionModel()
        assert selection is not None
        selection.selectionChanged.connect(lambda *_: self._onSelection())
        self.table.doubleClicked.connect(lambda _: self._zoomToSelection())

        applySavedStacApis()
        self._buildSearchPanel()
        self._buildMapBar()
        self._buildPreview()
        self._buildResultsPanel()
        self.downloads = DownloadsPanel()
        self.downloads.sigOpen.connect(self.openImages)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.streamer.sigStatus.connect(
            lambda text: self.status.showMessage(text, 8000)
        )
        self._buildDocks()
        self._onCatalog()

    # --- layout ---------------------------------------------------------------------

    def _buildSearchPanel(self) -> None:
        self.catalogBox = ElidingComboBox()
        for catalog in availableCatalogs():
            self.catalogBox.addItem(catalog.name, catalog.catalogId)
        self.catalogBox.currentIndexChanged.connect(lambda _: self._onCatalog())
        self.catalogAbout = MutedLabel("")
        self.catalogAbout.setWordWrap(True)
        self.loginButton = QPushButton("Log In…")
        self.loginButton.clicked.connect(lambda: self.logIn(self.catalog()))
        self.stacApisButton = QPushButton("STAC APIs…")
        self.stacApisButton.setToolTip("Add, remove or edit the STAC APIs searched")
        self.stacApisButton.clicked.connect(self._editStacApis)
        # drops down a search field over the collections, by body and mission
        self.collectionBox = CollectionCombo()
        self.collectionBox.setToolTip(
            "Click to search collections by body, mission or instrument"
        )
        self.collectionBox.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.collectionBox.currentIndexChanged.connect(lambda _: self._onCollection())
        self.addFileButton = QPushButton("Add Footprint File…")
        self.addFileButton.clicked.connect(self._addFootprintFile)
        self.filters = QWidget()
        self.filtersLayout = QVBoxLayout(self.filters)
        self.filtersLayout.setContentsMargins(0, 0, 0, 0)
        self.areaBox = ElidingComboBox()
        self.areaBox.addItems([AREA_VIEW, AREA_BOX, AREA_IMAGE])
        self.areaBox.currentIndexChanged.connect(lambda _: self._onArea())
        self.areaImage = ElidingComboBox()
        self.fromCheck, self.toCheck = QCheckBox("From"), QCheckBox("To")
        self.fromDate, self.toDate = (
            QDateEdit(QDate(2000, 1, 1)),
            QDateEdit(QDate.currentDate()),
        )
        for check, edit in (
            (self.fromCheck, self.fromDate),
            (self.toCheck, self.toDate),
        ):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.setEnabled(False)
            check.toggled.connect(edit.setEnabled)
        self.limit = QSpinBox()
        self.limit.setRange(1, 1000)
        self.limit.setValue(DEFAULT_LIMIT)
        self.searchButton = QPushButton("Search")
        self.searchButton.setDefault(True)
        self.searchButton.clicked.connect(self.search)
        self.moreButton = QPushButton("More Results")
        self.moreButton.clicked.connect(self.more)
        self.moreButton.setEnabled(False)
        self.searchStatus = MutedLabel("")
        self.searchStatus.setWordWrap(True)

        form = QFormLayout()
        # fields fill the panel on every platform (macOS keeps them narrow)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.addRow("Catalog:", self.catalogBox)
        form.addRow("", self.catalogAbout)
        form.addRow(
            "",
            FlowLayout().withWidget(self.loginButton).withWidget(self.stacApisButton),
        )
        form.addRow("Collection:", self.collectionBox)
        form.addRow("", self.addFileButton)
        form.addRow("Area:", self.areaBox)
        form.addRow("Image:", self.areaImage)
        dates = FlowLayout()
        for check, edit in (
            (self.fromCheck, self.fromDate),
            (self.toCheck, self.toDate),
        ):
            pair = HBoxBuilder(margins=0).withWidget(check).withWidget(edit)
            dates.addWidget(WrapperWidget(pair))
        form.addRow("Dates:", dates)
        form.addRow("At most:", self.limit)
        buttons = FlowLayout().withWidget(self.searchButton).withWidget(self.moreButton)
        self.searchPanel = QWidget()
        layout = QVBoxLayout(self.searchPanel)
        layout.addLayout(form)
        layout.addWidget(QLabel("<b>Filters</b>"))
        layout.addWidget(self.filters)
        layout.addLayout(buttons)
        layout.addWidget(self.searchStatus)
        layout.addStretch(1)

    def _buildMapBar(self) -> None:
        self.bodyLabel = QLabel()
        self.projectionBox = ElidingComboBox()
        for projection in MapProjection:
            self.projectionBox.addItem(projection.value, projection)
        self.projectionBox.currentIndexChanged.connect(lambda _: self._onProjection())
        self.basemapBox = ElidingComboBox()
        self.basemapBox.setToolTip(
            "Global imagery of the body (bundled, with sharper tiles from NASA's "
            "Solar System Treks as you zoom in), or a loaded, georeferenced "
            "image of the same body"
        )
        self.basemapBox.currentIndexChanged.connect(lambda _: self._onBasemap())
        self.basemapBox.activated.connect(lambda _: self._forgetSessionLayer())
        self.namesCheck = QCheckBox("Names")
        self.namesCheck.setChecked(True)
        self.namesCheck.setToolTip(
            "Label features with their IAU names: large ones zoomed out, "
            "smaller ones as you zoom in"
        )
        self.namesCheck.toggled.connect(self._onNamesShown)
        self.goTo = QLineEdit()
        self.goTo.setPlaceholderText("Go to a feature…")
        self.goTo.setClearButtonEnabled(True)
        self.goTo.setMinimumWidth(160)
        self.goTo.setToolTip(
            "Type a feature's name (Gale, Valles Marineris, Tycho, …) and press "
            "Enter or pick it from the list: the map zooms to it"
        )
        self.goToNames = QStringListModel(self)
        completer = QCompleter(self.goToNames, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setMaxVisibleItems(12)
        completer.activated.connect(self.goToFeature)
        self.goTo.setCompleter(completer)
        self.goTo.returnPressed.connect(lambda: self.goToFeature(self.goTo.text()))
        overlay = QPushButton("Add Overlay…")
        overlay.setToolTip(
            "A vector file (GeoJSON, shapefile, GeoPackage): a traverse, a geologic map, …"
        )
        overlay.clicked.connect(self._chooseOverlay)
        clear = QPushButton("Clear Overlays")
        clear.clicked.connect(self._clearOverlays)
        whole = QPushButton("Whole Body")
        whole.clicked.connect(self.map.showWhole)
        # wraps onto more rows rather than making the map's panel wide
        bar = (
            FlowLayout(margins=2)
            .withWidget(
                WrapperWidget(
                    HBoxBuilder(margins=0)
                    .withWidget(self.bodyLabel)
                    .withWidget(self.projectionBox)
                )
            )
            .withWidget(
                WrapperWidget(
                    HBoxBuilder(margins=0)
                    .withWidget(QLabel("Basemap:"))
                    .withWidget(self.basemapBox)
                )
            )
            .withWidget(self.namesCheck)
            .withWidget(self.goTo)
            .withWidget(overlay)
            .withWidget(clear)
            .withWidget(whole)
        )
        self.mapPanel = QWidget()
        layout = QVBoxLayout(self.mapPanel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.map, 1)

    def _buildPreview(self) -> None:
        self.thumbnail = QLabel("Select a product to see it here.")
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setWordWrap(True)
        self.thumbnail.setMinimumSize(THUMBNAIL_MIN, THUMBNAIL_MIN)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.previewPanel = QWidget()
        layout = QVBoxLayout(self.previewPanel)
        layout.addWidget(self.thumbnail)
        layout.addWidget(self.details, 1)

    def _buildResultsPanel(self) -> None:
        add = QPushButton("Add to Downloads")
        add.setToolTip(
            "The checked products (or, with none checked, the selected ones)"
        )
        add.clicked.connect(self.addToDownloads)
        checkAll = QPushButton("Check All")
        checkAll.clicked.connect(
            lambda: self.results.setChecked([p.id for p in self.results.products])
        )
        checkNone = QPushButton("Check None")
        checkNone.clicked.connect(
            lambda: self.results.setChecked(list(self.results.checked), False)
        )
        self.onlySelected = QCheckBox("Show only selected")
        self.onlySelected.setToolTip(
            "Only the products selected now; selecting on the map changes the list"
        )
        self.onlySelected.toggled.connect(self._onOnlySelected)
        bar = FlowLayout(margins=2)
        for widget in (add, checkAll, checkNone, self.onlySelected):
            bar.addWidget(widget)
        self.resultsPanel = QWidget()
        layout = QVBoxLayout(self.resultsPanel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table, 1)
        layout.addLayout(bar)

    def _buildDocks(self) -> None:
        # every panel fits its dock: forms scroll down, rows wrap, names elide
        self.dockManager = ads.CDockManager(self)
        mapDock = VardaDockWidget("Map").withWidget(self.mapPanel)
        manager = self.dockManager
        manager.addDockWidget(ads.DockWidgetArea.CenterDockWidgetArea, mapDock)
        manager.addDockWidget(
            ads.DockWidgetArea.LeftDockWidgetArea,
            VardaDockWidget("Search").withWidget(self.searchPanel),
            mapDock.dockAreaWidget(),
        )
        manager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea,
            VardaDockWidget("Preview").withWidget(self.previewPanel),
            mapDock.dockAreaWidget(),
        )
        self.resultsDock = VardaDockWidget("Results").withWidget(self.resultsPanel)
        resultsArea = manager.addDockWidget(
            ads.DockWidgetArea.BottomDockWidgetArea,
            self.resultsDock,
            mapDock.dockAreaWidget(),
        )
        self.downloadsDock = VardaDockWidget("Downloads").withWidget(self.downloads)
        manager.addDockWidget(
            ads.DockWidgetArea.RightDockWidgetArea, self.downloadsDock, resultsArea
        )

    # --- catalogs and collections ----------------------------------------------------------

    def catalog(self) -> Catalog:
        catalogId = self.catalogBox.currentData()
        if catalogId not in self._catalogs:
            cls = next(c for c in availableCatalogs() if c.catalogId == catalogId)
            catalog = self._catalogs[catalogId] = cls()
            if catalog.credentialFields:
                try:
                    loadCredentials(catalog)
                except CatalogError as error:
                    self.status.showMessage(str(error))
            if isinstance(catalog, FootprintFileCatalog):
                # the body the user picks changes the collections' map
                catalog.body.sigParameterChanged.connect(
                    lambda _: self._onCatalog(
                        select=self.collectionBox.currentData() or ""
                    )
                )
        return self._catalogs[catalogId]

    def collection(self) -> Collection | None:
        index = self.collectionBox.currentIndex()
        return self._collections[index] if 0 <= index < len(self._collections) else None

    def body(self) -> str:
        collection = self.collection()
        return collection.body if collection is not None else ""

    def _onCatalog(self, select: str = "") -> None:
        if self.catalogBox.count() == 0:
            return
        catalog = self.catalog()
        self.catalogAbout.setText(catalog.description)
        self.loginButton.setVisible(bool(catalog.credentialFields))
        self.stacApisButton.setVisible(isinstance(catalog, StacCatalog))
        self.addFileButton.setVisible(isinstance(catalog, FootprintFileCatalog))
        layout = self.filtersLayout
        while (item := layout.takeAt(0)) is not None:
            if (widget := item.widget()) is not None:
                widget.hide()  # not drawn under the new filters until deleted
                widget.deleteLater()
        if catalog.params:
            layout.addWidget(groupWidget(catalog))
        else:
            layout.addWidget(MutedLabel("This catalog has no filters."))
        self.collectionBox.setCollections([])
        self._collections = []
        self.searchStatus.setText("Loading collections…")
        runInBackground(
            catalog.collections,
            lambda collections: self._showCollections(catalog, collections, select),
            lambda error: self.searchStatus.setText(str(error)),
            description="Listing a catalog's collections",
            owner=self,
        )

    def _showCollections(
        self, catalog: Catalog, collections: list[Collection], select: str
    ) -> None:
        if catalog is not self.catalog():
            return  # the user moved on
        self._collections = list(collections)
        self.collectionBox.setCollections(self._collections, select)
        self.searchStatus.setText(
            f"{len(collections)} collections."
            if collections
            else "This catalog has no collections yet."
        )
        self._onCollection()

    def _onCollection(self) -> None:
        body = self.body()
        self.bodyLabel.setText(f"<b>{body}</b>" if body else "")
        self.map.setProjection(self.projectionBox.currentData(), body)
        gazetteer = loadGazetteer(body) if body else None
        self.map.setNames(gazetteer)
        self.goToNames.setStringList(list(gazetteer.names) if gazetteer else [])
        self.goTo.setEnabled(gazetteer is not None)
        self._refreshImageLists(keep=False)
        self._onArea()
        self._onBasemap()

    def _refreshImageLists(self, keep: bool = True) -> None:
        """The loaded images of the collection's body, offered as basemaps
        and search areas. With ``keep``, the current choices stay (and the
        basemap is redrawn only if its image went away)."""
        body = self.body()
        images = [i for i in self.projectImages if imageBody(i) == body] if body else []
        chosen = {
            box: box.currentData() if keep else None
            for box in (self.basemapBox, self.areaImage)
        }
        for box in (self.basemapBox, self.areaImage):
            box.blockSignals(True)
            box.clear()
        self.basemapBox.addItem("None", None)
        layers = layersFor(body) if body else []
        for layer in layers:
            self.basemapBox.addItem(layer.title, layer)
            self.basemapBox.setItemData(
                self.basemapBox.count() - 1,
                f"{layer.title}: {layer.fullCredit}",
                Qt.ItemDataRole.ToolTipRole,
            )
        for image in images:
            self.basemapBox.addItem(image.name, image)
            self.areaImage.addItem(image.name, image)
        for box, image in chosen.items():
            index = next(
                (i for i in range(box.count()) if box.itemData(i) is image), -1
            )
            if box is self.basemapBox and not keep and layers:
                # the body's global imagery, or what a session chose (kept
                # until the user picks: collections can be listed twice)
                pending = self._pendingLayer
                if pending is None:
                    index = 1
                elif pending == "":  # the session's basemap was None
                    index = 0
                else:
                    index = max(self._layerIndex(pending), 1)
            if index >= 0:
                box.setCurrentIndex(index)
            box.blockSignals(False)
        if keep:
            self._onArea()
            if (
                chosen[self.basemapBox] is not None
                and self.basemapBox.currentData() is None
            ):
                self._onBasemap()  # its image was closed

    def _forgetSessionLayer(self) -> None:
        self._pendingLayer = None

    def _layerIndex(self, layerId: str) -> int:
        """The basemap list's row for a layer id (-1 if it's not there)."""
        box = self.basemapBox
        return next(
            (
                i
                for i in range(box.count())
                if isinstance(layer := box.itemData(i), BasemapLayer)
                and layer.layerId == layerId
            ),
            -1,
        )

    def _addFootprintFile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Footprint File",
            "",
            "Vector files (*.shp *.geojson *.json *.gpkg *.kml *.fgb);;All files (*)",
        )
        if path:
            self.addFootprintFile(path)

    def addFootprintFile(self, path: str) -> None:
        if path not in FootprintFileCatalog.paths:
            FootprintFileCatalog.paths.append(path)
        self._onCatalog(select=path)

    # --- search -------------------------------------------------------------------------

    def _onArea(self) -> None:
        mode = self.areaBox.currentText()
        self.map.showArea(mode == AREA_BOX)
        self.areaImage.setEnabled(mode == AREA_IMAGE and self.areaImage.count() > 0)

    def searchArea(self) -> tuple[float, float, float, float] | None:
        mode = self.areaBox.currentText()
        if mode == AREA_BOX:
            return self.map.areaBounds()
        if mode == AREA_IMAGE:
            image = self.areaImage.currentData()
            return imageBounds(image, self.body()) if image is not None else None
        return self.map.viewBounds()

    def query(self) -> SearchQuery | None:
        collection = self.collection()
        if collection is None:
            return None

        def moment(check: QCheckBox, edit: QDateEdit, at: time) -> datetime | None:
            if not check.isChecked():
                return None
            return datetime.combine(edit.date().toPyDate(), at, timezone.utc)

        # UTC days, both inclusive: To is the end of its day
        return SearchQuery(
            collection.id,
            self.searchArea(),
            moment(self.fromCheck, self.fromDate, time.min),
            moment(self.toCheck, self.toDate, time.max),
            self.limit.value(),
        )

    def logIn(self, catalog: Catalog) -> bool:
        """Ask for the catalog's login; whether it now has one."""
        CredentialsDialog(catalog, self).exec()
        return hasCredentials(catalog)

    def _loggedIn(self, catalog: Catalog) -> bool:
        return hasCredentials(catalog) or self.logIn(catalog)

    def _editStacApis(self) -> None:
        if StacApisDialog(self).exec():
            self._catalogs.pop(StacCatalog.catalogId, None)  # it caches collections
            self._onCatalog()

    def search(self) -> None:
        query = self.query()
        if query is None:
            self.searchStatus.setText("Choose a collection first.")
            return
        if not self._loggedIn(self.catalog()):
            self.searchStatus.setText("This catalog needs a login.")
            return
        self._query = query
        self.onlySelected.setChecked(False)  # a new search lists everything
        self.results.setProducts([])
        self._run(query, append=False)

    def more(self) -> None:
        if self._query is None or self._nextPage is None:
            return
        query = SearchQuery(
            self._query.collection,
            self._query.bbox,
            self._query.start,
            self._query.end,
            self._query.limit,
            self._nextPage,
        )
        self._run(query, append=True)

    def _run(self, query: SearchQuery, append: bool) -> None:
        catalog = self.catalog()
        self.searchButton.setEnabled(False)
        self.moreButton.setEnabled(False)
        self.searchStatus.setText("Searching…")
        runInBackground(
            lambda: catalog.search(query, lambda fraction, message: None),
            lambda page: self._showPage(catalog, page, append),
            self._searchFailed,
            description=f"Searching {catalog.name}",
            owner=self,
        )

    def _searchFailed(self, error: Exception) -> None:
        self.searchButton.setEnabled(True)
        self.moreButton.setEnabled(self._nextPage is not None)
        self.searchStatus.setText(str(error))

    def _showPage(self, catalog: Catalog, page: SearchPage, append: bool) -> None:
        self._resultsCatalog = catalog
        products = (self.results.products if append else []) + list(page.products)
        self.results.setProducts(products)
        self._nextPage = page.nextPage
        self.map.setFootprints(products)
        self.map.setSelected([])
        self.searchButton.setEnabled(True)
        self.moreButton.setEnabled(page.nextPage is not None)
        total = f" of {page.total}" if page.total is not None else ""
        self.searchStatus.setText(
            f"{len(products)}{total} products." if products else "Nothing found here."
        )
        if products and not append and self.areaBox.currentText() != AREA_VIEW:
            self.map.zoomTo(
                tuple(shapely.union_all([p.footprint for p in products]).bounds)
            )

    # --- downloads ----------------------------------------------------------------------

    def addToDownloads(self) -> None:
        products = self.results.checkedProducts() or self.selectedProducts()
        catalog = self._resultsCatalog
        if not products or catalog is None:
            self.status.showMessage("Check or select products in the results first.")
            return
        if not self._loggedIn(catalog):
            return
        self.downloads.add(catalog, products)
        self.downloadsDock.toggleView(True)
        self.downloadsDock.raise_()

    def openImages(self, paths: list) -> None:
        ImageLoadingService.load_images(
            file_paths=[str(p) for p in paths],
            on_success_callback=self.projectImages.append,
        )

    # --- selection and preview --------------------------------------------------------------

    def selectedProducts(self) -> list[Product]:
        selection = self.table.selectionModel()
        assert selection is not None
        rows = sorted(
            {self.proxy.mapToSource(i).row() for i in selection.selectedRows()}
        )
        return [self.results.products[r] for r in rows]

    def selectProducts(
        self, ids: list[str], add: bool = False, remove: bool = False
    ) -> None:
        """Select products (adding to the selection, or removing from it).
        With Show only selected, the table then lists the new selection."""
        current = {p.id for p in self.selectedProducts()}
        wanted = (
            current - set(ids) if remove else (current | set(ids) if add else set(ids))
        )
        if self.onlySelected.isChecked():
            self.proxy.setShown(wanted)
        selection = self.table.selectionModel()
        assert selection is not None
        selection.clearSelection()
        for row, product in enumerate(self.results.products):
            if product.id in wanted:
                index = self.proxy.mapFromSource(self.results.index(row, 1))
                if not index.isValid():
                    continue
                selection.select(
                    index,
                    selection.SelectionFlag.Select | selection.SelectionFlag.Rows,
                )
                if product.id in ids:
                    self.table.scrollTo(index)

    def _onMapSelect(self, ids: list[str], mode: str) -> None:
        self.selectProducts(ids, add=mode == ADD, remove=mode == REMOVE)

    def _onOnlySelected(self, only: bool) -> None:
        selected = [p.id for p in self.selectedProducts()]
        self.proxy.setShown(set(selected) if only else None)
        self.selectProducts(selected)

    def _onSelection(self) -> None:
        products = self.selectedProducts()
        self.map.setSelected(products)
        if len(products) != 1:
            self.thumbnail.setText(
                "Select one product to see it here." if products else ""
            )
            self.details.setText(
                f"{len(products)} products selected." if products else ""
            )
            return
        product = products[0]
        lines = [f"<b>{product.id}</b>"]
        if product.title != product.id:
            lines.append(product.title)
        if product.start:
            lines.append(f"Start: {product.start:%Y-%m-%d %H:%M:%S}")
        lines += [f"{key}: {value}" for key, value in product.properties.items()]
        self.details.setText("<br>".join(lines))
        self.thumbnail.setText(
            "Loading the thumbnail…" if product.thumbnail else "No thumbnail."
        )
        if product.thumbnail:
            runInBackground(
                lambda: fetchBytes(product.thumbnail),
                lambda data: self._showThumbnail(product, data),
                lambda error: self.thumbnail.setText("No thumbnail."),
                description="Fetching a thumbnail",
                owner=self,
            )

    def _showThumbnail(self, product: Product, data: bytes) -> None:
        if self.selectedProducts() != [product]:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            self.thumbnail.setText("No thumbnail.")
            return
        side = max(min(THUMBNAIL_SIZE, self.thumbnail.width()), THUMBNAIL_MIN)
        self.thumbnail.setPixmap(
            pixmap.scaled(
                side,
                side,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _zoomToSelection(self) -> None:
        products = self.selectedProducts()
        if products:
            self.map.zoomTo(
                tuple(shapely.union_all([p.footprint for p in products]).bounds)
            )

    # --- basemap and overlays -----------------------------------------------------------------

    def _onProjection(self) -> None:
        self.map.setProjection(self.projectionBox.currentData(), self.body())
        self._onBasemap()

    def _onBasemap(self) -> None:
        # a newer choice drops the result of any drawing still under way
        self._basemapRequest += 1
        request = self._basemapRequest
        image = self.basemapBox.currentData()
        layer = image if isinstance(image, BasemapLayer) else None
        self.streamer.setLayer(layer)
        self._showCredit()
        if image is None:
            self.map.setBasemap(None)
            return
        projector = self.map.projector
        # pyproj objects stay on this thread: workers get plain values
        frame = MapFrame.of(projector)
        if layer is not None:
            runInBackground(
                lambda: globalBasemap(bundledImage(layer), frame),
                lambda result: self._showGlobalBasemap(request, *result),
                lambda error: self.status.showMessage(
                    f"The basemap couldn't be drawn: {error}", 8000
                ),
                description="Drawing a global basemap",
                owner=self,
            )
            return
        try:
            bounds = imageMapBounds(image, projector)
        except ValueError as error:
            self.map.setBasemap(None)
            self.status.showMessage(f"The basemap couldn't be drawn: {error}", 8000)
            return
        self.status.showMessage(f"Reprojecting {image.name} for the map…")
        runInBackground(
            lambda: basemap(image, frame.crs, bounds),
            lambda result: self._showBasemap(request, *result),
            lambda error: self.status.showMessage(
                f"The basemap couldn't be drawn: {error}", 8000
            ),
            description="Drawing a basemap",
            owner=self,
        )

    def _showBasemap(self, request: int, rgba: np.ndarray, bounds: Bounds) -> None:
        if request != self._basemapRequest:
            return  # drawn for a choice or projection the map has left
        self.map.setBasemap(rgba, bounds)
        self.status.clearMessage()
        if not self.results.products:
            self.map.zoomToMap(bounds, padding=0.05)

    def _showGlobalBasemap(
        self, request: int, rgba: np.ndarray, bounds: Bounds
    ) -> None:
        if request == self._basemapRequest:  # still the choice and projection
            self.map.setBasemap(rgba, bounds)

    def _showCredit(self) -> None:
        """The active imagery layer's credit, and the names' when shown."""
        layer = self.basemapBox.currentData()
        parts = []
        if isinstance(layer, BasemapLayer):
            parts.append(f"Imagery: {layer.title}, {layer.fullCredit}.")
        if self.namesCheck.isChecked() and self.goTo.isEnabled():
            parts.append(f"{GAZETTEER_CREDIT}.")
        self.map.setCredit(" ".join(parts))

    def _onNamesShown(self, shown: bool) -> None:
        self.map.showNames(shown)
        self._showCredit()

    def goToFeature(self, name: str) -> bool:
        """Zoom to the body's feature best matching ``name`` (an exact name,
        else the largest starting with it, else containing it)."""
        gazetteer = loadGazetteer(self.body()) if self.body() else None
        hits = search(gazetteer, name, limit=1) if gazetteer is not None else []
        if gazetteer is None or not hits:
            if name.strip():
                self.status.showMessage(f"No feature called {name.strip()!r}.", 5000)
            return False
        found = gazetteer.names[hits[0]]
        view = featureView(gazetteer, hits[0])
        if self.map.projector.mapBounds(view) is None:
            self.status.showMessage(
                f"{found} is in the other hemisphere; choose Equirectangular "
                "or the other polar view.",
                8000,
            )
            return False
        self.map.zoomTo(view, padding=0)
        self.status.showMessage(f"{found} ({gazetteer.kinds[hits[0]]})", 5000)
        return True

    def _chooseOverlay(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Add Overlay",
            "",
            "Vector files (*.shp *.geojson *.json *.gpkg *.kml *.fgb);;All files (*)",
        )
        if path:
            self.addOverlay(path)

    def addOverlay(self, path: str) -> None:
        try:
            frame = gpd.read_file(path)
        except Exception as error:  # pyogrio raises many kinds
            self.status.showMessage(f"Varda couldn't read {path}: {error}", 8000)
            return
        frame = toLongitudeLatitude(frame)
        self.map.addOverlay(list(frame.geometry.dropna()))
        if path not in self.overlayPaths:
            self.overlayPaths.append(path)

    def _clearOverlays(self) -> None:
        self.map.clearOverlays()
        self.overlayPaths = []

    # --- sessions ---------------------------------------------------------------------------

    def sessionState(self) -> dict:
        collection = self.collection()
        return {
            "catalog": self.catalogBox.currentData(),
            "collection": collection.id if collection else "",
            "area": self.areaBox.currentText(),
            "projection": self.projectionBox.currentData().name,
            "box": list(self.map.areaBounds()),
            "view": list(self.map.viewBounds()),
            "footprintFiles": list(FootprintFileCatalog.paths),
            "overlays": list(self.overlayPaths),
            "basemapLayer": layer.layerId
            if isinstance(layer := self.basemapBox.currentData(), BasemapLayer)
            else "",
            "names": self.namesCheck.isChecked(),
        }

    def restoreSessionState(self, state: dict) -> None:
        for path in state.get("footprintFiles", []):
            if path not in FootprintFileCatalog.paths:
                FootprintFileCatalog.paths.append(path)
        index = self.catalogBox.findData(state.get("catalog"))
        if index >= 0:
            self.catalogBox.blockSignals(True)
            self.catalogBox.setCurrentIndex(index)
            self.catalogBox.blockSignals(False)
        if "basemapLayer" in state:  # chosen once the collection's body is known
            self._pendingLayer = state["basemapLayer"]
        projection = MapProjection.__members__.get(state.get("projection", ""))
        if projection is not None:
            self.projectionBox.setCurrentIndex(self.projectionBox.findData(projection))
        self._onCatalog(select=state.get("collection", ""))
        area = self.areaBox.findText(state.get("area", AREA_VIEW))
        self.areaBox.setCurrentIndex(max(area, 0))
        if state.get("box"):
            self.map.setArea(tuple(state["box"]))
        if state.get("view"):
            self.map.zoomTo(tuple(state["view"]), padding=0)
        for path in state.get("overlays", []):
            self.addOverlay(path)
        self.namesCheck.setChecked(state.get("names", True))
