"""Find Data: search a catalog for observations of a place, see their
footprints on a map, and download them. Varda knows no missions:
the catalogs (``varda.catalogs``, and plugins' ``registerCatalogs``) do."""

from __future__ import annotations

from collections.abc import Callable, MutableSequence
from datetime import datetime, time, timezone

import geopandas as gpd
import numpy as np
import PyQt6Ads as ads
from superqt import QCollapsible
import shapely
from PyQt6.QtCore import (
    QDate,
    QEvent,
    QObject,
    QSize,
    QTimer,
    QModelIndex,
    QSortFilterProxyModel,
    QStringListModel,
    Qt,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QMenu,
    QCompleter,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from varda.catalogs.basemaps import BasemapLayer, bundledImage, layersFor
from varda.catalogs.bodies import toLongitudeLatitude
from varda.catalogs.catalog import (
    BrowseImage,
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
from varda.catalogs.registry import availableCatalogs
from varda.catalogs.sources import Source, bodies, sourceKey, sourcesFor
from varda.catalogs.stac import StacCatalog
from varda.common.entities import VardaRaster
from varda.common.observable_list import ObservableList
from varda.common.parameter_widgets import groupWidget
from varda.common.ui import (
    FoldingSection,
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
from varda.workspaces.find_data.collection_picker import BodyBar, CollectionFinder
from varda.workspaces.find_data.downloads_panel import DownloadsPanel
from varda.workspaces.find_data.map_view import ADD, REMOVE, FootprintMap
from varda.workspaces.find_data.imagery import ImageryStreamer
from varda.workspaces.find_data.preview_gallery import GalleryItem, PreviewGallery
from varda.workspaces.find_data.preview_images import (
    PictureCache,
    loadPicture,
    toPixmap,
)
from varda.workspaces.find_data.previews import (
    drape,
    footprintBox,
    isMapOfFootprint,
)
from varda.workspaces.find_data.projection import Bounds, MapProjection
from varda.workspaces.find_data.results_model import SORT_ROLE, ResultsModel

AREA_VIEW, AREA_BOX, AREA_IMAGE = "Map view", "Box on the map", "An image's footprint"
DEFAULT_BODY = "Mars"
CHOOSE_COLLECTION = "Choose what to search in the list above."
DEFAULT_LIMIT = 100
PREVIEW_MIN = 160  # px: the Preview panel's picture at its smallest
MIN_MAP_HEIGHT = 200  # px: below this, the map's settings fold away
MAX_MAP_PREVIEWS = 12  # selected products whose browse images go on the map
MAX_GALLERY = 40  # selected products the Preview panel turns through
THUMBNAIL_SIZE = QSize(48, 32)  # px, in the results
PREVIEW_AUTO, PREVIEW_OFF = "Automatic", "Off"
NOT_ON_MAP = (
    "Not drawn on the map: its browse image isn't a north-up map of the "
    "footprint (a strip in sensor geometry, or a figure)."
)


def _subtitle(product: Product) -> str:
    """The gallery's second line: when, and the observation type if known."""
    parts = []
    if product.start:
        parts.append(f"{product.start:%Y-%m-%d}")
    kind = product.properties.get("Observation type")
    if kind:
        parts.append(str(kind))
    return " · ".join(parts)


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
            # images loaded later (a download, say) can be basemaps too; a
            # bound method, so Qt disconnects it when the workspace is deleted
            self.projectImages.sigDataChanged.connect(self._onProjectImagesChanged)
        self._catalogs: dict[str, Catalog] = {}  # one each, so filters persist
        self._sources: list[Source] = []  # every catalog's collections
        self._loading: dict[str, str] = {}  # catalog id: "Listing …"
        self._failed: dict[str, str] = {}  # catalog id: why it couldn't list
        self._chosen: dict[str, str] = {}  # body: the source key chosen there
        self._filtersCatalog: Catalog | None = None  # whose filters are shown
        self._pendingSource = ""  # a session's collection, once it's listed
        self._body = ""
        self._bodyPicked = False  # the user chose a body (or a session did)
        self._query: SearchQuery | None = None
        self._resultsCatalog: Catalog | None = None  # the catalog that found them
        self._nextPage: object | None = None
        self.overlayPaths: list[str] = []
        self._pendingLayer: str | None = None  # a session's basemap layer id
        self._basemapRequest = 0  # the latest basemap drawing asked for
        self._pictures = PictureCache()  # browse images, by URL
        self._mapPreviewRequest = 0  # the latest drawing of previews on the map
        self._notMapLike: set[str] = set()  # products whose image isn't a map
        # each product's standard browse products, once its catalog said
        self._browse: dict[str, list[BrowseImage]] = {}
        self._browseAsked: set[str] = set()
        self._mapUrl: dict[str, str] = {}  # the picture drawn on the map, by product

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
        self.table.setIconSize(THUMBNAIL_SIZE)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        rows = self.table.verticalHeader()
        assert rows is not None
        rows.setDefaultSectionSize(THUMBNAIL_SIZE.height() + 6)
        rows.hide()
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
        self._showBody(DEFAULT_BODY)
        self._loadSources()

    # --- layout ---------------------------------------------------------------------

    def _buildSearchPanel(self) -> None:
        # 1. the body, 2. what to search (every catalog's collections of it),
        # 3. where and when, 4. the chosen catalog's own filters
        self.bodyBar = BodyBar()
        self.bodyBar.sigBodyChanged.connect(self._onBody)
        self.finder = CollectionFinder()
        self.finder.sigChosen.connect(self._onSource)
        self.collectionTitle = QLabel()
        self.collectionTitle.setWordWrap(True)
        titleFont = self.collectionTitle.font()
        titleFont.setBold(True)
        self.collectionTitle.setFont(titleFont)
        self.collectionAbout = MutedLabel(CHOOSE_COLLECTION)
        self.collectionAbout.setWordWrap(True)
        self.loginButton = QPushButton("Log In…")
        self.loginButton.clicked.connect(self._logInToChosen)
        self.loginButton.hide()
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
        self.limit.setSuffix(" results")
        self.filters = QWidget()
        self.filtersLayout = QVBoxLayout(self.filters)
        self.filtersLayout.setContentsMargins(0, 0, 0, 0)
        self.searchButton = QPushButton("Search")
        self.searchButton.setDefault(True)
        self.searchButton.setEnabled(False)
        self.searchButton.setMinimumHeight(32)
        self.searchButton.clicked.connect(self.search)
        self.moreButton = QPushButton("More Results")
        self.moreButton.clicked.connect(self.more)
        self.moreButton.setEnabled(False)
        self.searchStatus = MutedLabel("")
        self.searchStatus.setWordWrap(True)
        self.sourcesStatus = MutedLabel("")
        self.sourcesStatus.setWordWrap(True)
        self.sourcesButton = QPushButton("Sources")
        self.sourcesButton.setToolTip(
            "The catalogs searched: STAC APIs, logins, and footprint files"
        )
        self.sourcesMenu = QMenu(self.sourcesButton)
        self.sourcesMenu.aboutToShow.connect(self._fillSourcesMenu)
        self.sourcesButton.setMenu(self.sourcesMenu)

        where = QFormLayout()
        where.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        where.addRow("Area:", self.areaBox)
        where.addRow("Image:", self.areaImage)
        dates = FlowLayout()
        for check, edit in (
            (self.fromCheck, self.fromDate),
            (self.toCheck, self.toDate),
        ):
            pair = HBoxBuilder(margins=0).withWidget(check).withWidget(edit)
            dates.addWidget(WrapperWidget(pair))
        where.addRow("Dates:", dates)
        filtersBox = QWidget()
        filtersBody = QVBoxLayout(filtersBox)
        filtersBody.setContentsMargins(0, 0, 0, 0)
        filtersBody.addWidget(self.filters)
        limitRow = QFormLayout()
        limitRow.addRow("At most:", self.limit)
        filtersBody.addLayout(limitRow)
        self.filtersSection = QCollapsible(
            "Filters", expandedIcon="▼", collapsedIcon="▶"
        )
        self.filtersSection.setContent(filtersBox)
        self.filtersSection.collapse(animate=False)
        about = QVBoxLayout()
        about.setSpacing(2)
        about.addWidget(self.collectionTitle)
        about.addWidget(self.collectionAbout)
        about.addWidget(self.loginButton, alignment=Qt.AlignmentFlag.AlignLeft)
        buttons = FlowLayout().withWidget(self.searchButton).withWidget(self.moreButton)
        footer = QVBoxLayout()
        footer.setSpacing(4)
        footer.addWidget(self.sourcesStatus)
        footer.addWidget(self.sourcesButton, alignment=Qt.AlignmentFlag.AlignLeft)
        self.searchPanel = QWidget()
        layout = QVBoxLayout(self.searchPanel)
        layout.setSpacing(10)
        layout.addWidget(self.bodyBar)
        layout.addWidget(self.finder, 1)
        layout.addLayout(about)
        layout.addLayout(where)
        layout.addWidget(self.filtersSection)
        layout.addLayout(buttons)
        layout.addWidget(self.searchStatus)
        layout.addLayout(footer)

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
        self.previewBox = ElidingComboBox()
        self.previewBox.addItem(PREVIEW_AUTO, PREVIEW_AUTO)
        self.previewBox.addItem(PREVIEW_OFF, PREVIEW_OFF)
        self.previewBox.setToolTip(
            "What to draw over the selected footprints (up to "
            f"{MAX_MAP_PREVIEWS}): Automatic, the first browse image that is a "
            "north-up map of the footprint; a standard browse product the "
            "selected products have (for CRISM: false color, mafic "
            "mineralogy, phyllosilicates, …); or nothing"
        )
        self.previewBox.currentIndexChanged.connect(lambda _: self._onPreviewChoice())
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
        self.overlaysButton = QPushButton("Overlays")
        self.overlaysButton.setToolTip(
            "Vector files over the map (GeoJSON, shapefile, GeoPackage): a "
            "traverse, a geologic map, …"
        )
        overlays = QMenu(self.overlaysButton)
        overlays.addAction("Add Overlay…", self._chooseOverlay)
        overlays.addAction("Clear Overlays", self._clearOverlays)
        self.overlaysButton.setMenu(overlays)
        whole = QPushButton("Whole Body")
        whole.setToolTip("Zoom out to the whole body")
        whole.clicked.connect(self.map.showWhole)
        self.bodyLabel.hide()  # the body bar says which body
        # wraps onto more rows rather than making the map's panel wide
        bar = (
            FlowLayout(margins=2)
            .withWidget(self.goTo)
            .withWidget(self.overlaysButton)
            .withWidget(whole)
        )
        settings = WrapperWidget(
            FlowLayout(margins=2)
            .withWidget(self.projectionBox)
            .withWidget(
                WrapperWidget(
                    HBoxBuilder(margins=0)
                    .withWidget(QLabel("Basemap:"))
                    .withWidget(self.basemapBox)
                )
            )
            .withWidget(self.namesCheck)
            .withWidget(
                WrapperWidget(
                    HBoxBuilder(margins=0)
                    .withWidget(QLabel("Map preview:"))
                    .withWidget(self.previewBox)
                )
            )
        )
        # Projection, basemap, names and map preview: open while the panel
        # leaves the map room, folded on a small screen (unless you open it)
        self.mapSettings = FoldingSection("Map Settings", settings)
        self.mapSettings.expand()
        self._mapSettingsChosen = False
        # a click is the user's choice, to be kept
        self.mapSettings.sigToggledByUser.connect(self._onMapSettingsClicked)
        self.mapPanel = QWidget()
        self.mapPanel.installEventFilter(self)
        layout = QVBoxLayout(self.mapPanel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(bar)
        layout.addWidget(self.mapSettings)
        layout.addWidget(self.map, 1)

    def _onMapSettingsClicked(self, _expanded: bool) -> None:
        self._mapSettingsChosen = True

    def _fitMapSettings(self) -> None:
        """Fold the map's settings when the panel is too short to leave the
        map MIN_MAP_HEIGHT, and open them again when it's tall enough; once
        you fold or open them yourself, they stay as you left them."""
        if self._mapSettingsChosen:
            return
        if self.mapSettings.isExpanded():
            if self.map.plot.height() < MIN_MAP_HEIGHT:
                self.mapSettings.collapse()
            return
        settings = self.mapSettings.content()
        needed = settings.heightForWidth(self.mapPanel.width())
        if needed <= 0:
            needed = settings.sizeHint().height()
        if self.map.plot.height() - needed >= MIN_MAP_HEIGHT:
            self.mapSettings.expand()

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 is self.mapPanel and a1 is not None and a1.type() == QEvent.Type.Resize:
            QTimer.singleShot(0, self._fitMapSettings)  # once the layout has settled
        return super().eventFilter(a0, a1)

    def _buildPreview(self) -> None:
        self.gallery = PreviewGallery()
        self.gallery.setMinimumSize(PREVIEW_MIN, PREVIEW_MIN)
        self.gallery.sigCurrentChanged.connect(self._onGalleryCurrent)
        self.gallery.sigActivated.connect(self._zoomToProduct)
        self.mapNote = MutedLabel()
        self.mapNote.setWordWrap(True)
        self.details = QLabel()
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.previewPanel = QWidget()
        layout = QVBoxLayout(self.previewPanel)
        layout.addWidget(self.gallery, 3)
        layout.addWidget(self.mapNote)
        layout.addWidget(self.details, 2)

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
        # a tab beside the results: it comes forward when something is added
        manager.addDockWidgetTabToArea(self.downloadsDock, resultsArea)
        self.resultsDock.raise_()

    # --- catalogs and collections ----------------------------------------------------------

    def _catalog(self, catalogId: str) -> Catalog:
        """The workspace's one instance of a catalog (its filters persist)."""
        if catalogId not in self._catalogs:
            cls = next(c for c in availableCatalogs() if c.catalogId == catalogId)
            catalog = self._catalogs[catalogId] = cls()
            if catalog.credentialFields:
                try:
                    loadCredentials(catalog)
                except CatalogError as error:
                    self.status.showMessage(str(error))
            if isinstance(catalog, FootprintFileCatalog):
                # the body the user picks moves the files' collections
                catalog.body.sigParameterChanged.connect(
                    lambda _: self._loadSources([FootprintFileCatalog.catalogId])
                )
        return self._catalogs[catalogId]

    def source(self) -> Source | None:
        """The collection chosen on the current body, with its catalog."""
        return self.finder.chosen()

    def catalog(self) -> Catalog | None:
        source = self.source()
        return self._catalog(source.catalogId) if source is not None else None

    def collection(self) -> Collection | None:
        source = self.source()
        return source.collection if source is not None else None

    def body(self) -> str:
        return self.bodyBar.current() or self._body

    def _loadSources(self, catalogIds: list[str] | None = None) -> None:
        """List every catalog's collections (or those of ``catalogIds``) in the
        background; each catalog's arrive on their own."""
        for cls in availableCatalogs():
            if catalogIds is not None and cls.catalogId not in catalogIds:
                continue
            catalog = self._catalog(cls.catalogId)
            self._loading[catalog.catalogId] = f"Listing {catalog.name}…"
            self._failed.pop(catalog.catalogId, None)
            runInBackground(
                catalog.collections,
                lambda collections, c=catalog: self._addSources(c, collections),
                lambda error, c=catalog: self._sourceFailed(c, error),
                description="Listing a catalog's collections",
                owner=self,
            )
        self._showSourcesStatus()

    def _addSources(self, catalog: Catalog, collections: list[Collection]) -> None:
        self._loading.pop(catalog.catalogId, None)
        self._sources = [s for s in self._sources if s.catalogId != catalog.catalogId]
        self._sources += [
            Source(catalog.catalogId, catalog.name, c, catalog.shortName)
            for c in collections
        ]
        if self._pendingSource:
            pending = next(
                (s for s in self._sources if s.key == self._pendingSource), None
            )
            if pending is not None:
                self._pendingSource = ""
                self._chosen[pending.body] = pending.key
                if pending.body != self.body():
                    self._showBody(pending.body)
        self._refreshSources()
        self._showSourcesStatus()

    def _sourceFailed(self, catalog: Catalog, error: Exception) -> None:
        self._loading.pop(catalog.catalogId, None)
        self._failed[catalog.catalogId] = f"{catalog.name}: {error}"
        self._showSourcesStatus()
        self._refreshSources()

    def _showSourcesStatus(self) -> None:
        notes = list(self._loading.values())
        if self._failed:
            names = [
                self._catalog(c).shortName or self._catalog(c).name
                for c in self._failed
            ]
            listed = (
                f"{', '.join(names[:-1])} and {names[-1]}"
                if len(names) > 1
                else names[0]
            )
            notes.append(
                f"Couldn't reach {listed}. Check your connection, then "
                "Sources → Reload the Catalogs."
            )
        self.sourcesStatus.setText(" ".join(notes))
        # the catalogs' own words, for whoever wants them
        self.sourcesStatus.setToolTip("\n".join(self._failed.values()))

    def _refreshSources(self) -> None:
        """The body bar and the finder, after the sources changed: the body
        and the chosen collection stay. Until the user picks a body, a body
        with no collections gives way to the first that has some."""
        body = self.body()
        counts = bodies(self._sources)
        if counts and body not in dict(counts) and not self._bodyPicked:
            self._showBody(counts[0][0])
            body = self.body()
        if body not in dict(counts):
            counts = [*counts, (body, 0)]
        self.bodyBar.setBodies(counts, body)
        chosen = self._chosen.get(body, "")
        mine = sourcesFor(self._sources, body)
        if not chosen and len(mine) == 1 and not self._loading:
            chosen = mine[0].key  # the only one of every catalog: no need to ask
        before = self.finder.chosenKey
        self.finder.setSources(mine, body, chosen)
        if self.finder.chosenKey != before or (chosen and self._filtersCatalog is None):
            self._onSource(self.finder.chosenKey)

    def _onBody(self, body: str) -> None:
        self._bodyPicked = True
        if body != self._body:
            self._showBody(body)
            self._refreshSources()

    def _showBody(self, body: str) -> None:
        """Change body: the old body's results, footprints and previews go
        (they'd be drawn on the wrong world), and the map turns to the new one."""
        changed = body != self._body
        self._body = body
        self.bodyBar.setCurrent(body)
        self.clearResults()
        if changed:
            self.finder.search.clear()  # another body's words would hide everything
        self.map.setProjection(self.projectionBox.currentData(), body)
        gazetteer = loadGazetteer(body) if body else None
        self.map.setNames(gazetteer)
        self.goToNames.setStringList(list(gazetteer.names) if gazetteer else [])
        self.goTo.setEnabled(gazetteer is not None)
        self.bodyLabel.setText(f"<b>{body}</b>" if body else "")
        self._refreshImageLists(keep=False)
        self._onArea()
        self._onBasemap()
        if changed:
            self.map.showWhole()  # not the old body's coordinates

    def _onSource(self, key: str) -> None:
        """A collection was chosen: say what it is and offer its catalog's filters."""
        source = self.source()
        if source is None or source.key != key:
            self._showCollectionCard(None)
            return
        self._chosen[source.body] = key
        self._showCollectionCard(source)
        catalog = self._catalog(source.catalogId)
        if catalog is not self._filtersCatalog:
            self._filtersCatalog = catalog
            layout = self.filtersLayout
            while (item := layout.takeAt(0)) is not None:
                if (widget := item.widget()) is not None:
                    widget.hide()  # not drawn under the new filters until deleted
                    widget.deleteLater()
            if catalog.params:
                layout.addWidget(groupWidget(catalog))
            else:
                layout.addWidget(MutedLabel(f"{catalog.name} has no other filters."))

    def _showCollectionCard(self, source: Source | None) -> None:
        if source is None:
            self.collectionTitle.setText("")
            self.collectionTitle.hide()
            self.collectionAbout.setText(CHOOSE_COLLECTION)
            self.loginButton.hide()
            self.searchButton.setEnabled(False)
            return
        collection = source.collection
        self.collectionTitle.setText(collection.title)
        self.collectionTitle.show()
        about = collection.description or ""
        self.collectionAbout.setText(
            f"{about}\nFrom {source.catalogName}"
            if about
            else f"From {source.catalogName}"
        )
        catalog = self._catalog(source.catalogId)
        self.loginButton.setVisible(
            bool(catalog.credentialFields) and not hasCredentials(catalog)
        )
        self.loginButton.setText(f"Log In to {catalog.name}…")
        self.searchButton.setEnabled(True)

    def _logInToChosen(self) -> None:
        catalog = self.catalog()
        if catalog is not None and self.logIn(catalog):
            self._showCollectionCard(self.source())

    def _fillSourcesMenu(self) -> None:
        menu = self.sourcesMenu
        menu.clear()
        menu.addAction("STAC APIs…", self._editStacApis)
        menu.addAction("Add Footprint File…", self._addFootprintFile)
        needing = [
            self._catalog(c.catalogId)
            for c in availableCatalogs()
            if c.credentialFields
        ]
        if needing:
            menu.addSeparator()
            for catalog in needing:
                menu.addAction(
                    f"Log In to {catalog.name}…", lambda c=catalog: self.logIn(c)
                )
        menu.addSeparator()
        menu.addAction("Reload the Catalogs", lambda: self._loadSources())

    def clearResults(self) -> None:
        """No results, footprints, selection or previews (a new body)."""
        self._query = None
        self._nextPage = None
        self._resultsCatalog = None
        self.onlySelected.setChecked(False)
        self.results.setProducts([])
        self.map.setFootprints([])
        self.map.setSelected([])
        self.map.keepPreviews(set())
        self.moreButton.setEnabled(False)
        self.searchStatus.setText("")
        self._onSelection()

    def _onProjectImagesChanged(self, _images: list) -> None:
        self._refreshImageLists()

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
        # chosen (on its body) once it's listed
        self._pendingSource = sourceKey(FootprintFileCatalog.catalogId, path)
        self._loadSources([FootprintFileCatalog.catalogId])

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
            return imageBounds(image) if image is not None else None
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
            self._loadSources([StacCatalog.catalogId])

    def search(self) -> None:
        query, catalog = self.query(), self.catalog()
        if query is None or catalog is None:
            self.searchStatus.setText(CHOOSE_COLLECTION)
            return
        if not self._loggedIn(catalog):
            self.searchStatus.setText(f"{catalog.name} needs a login.")
            return
        self._query = query
        self._browse.clear()
        self._browseAsked.clear()
        self._mapUrl.clear()
        self._notMapLike.clear()
        self.onlySelected.setChecked(False)  # a new search lists everything
        self.results.setProducts([])
        self._run(catalog, query, append=False)

    def more(self) -> None:
        catalog = self._resultsCatalog  # the one that found them
        if self._query is None or self._nextPage is None or catalog is None:
            return
        query = SearchQuery(
            self._query.collection,
            self._query.bbox,
            self._query.start,
            self._query.end,
            self._query.limit,
            self._nextPage,
        )
        self._run(catalog, query, append=True)

    def _run(self, catalog: Catalog, query: SearchQuery, append: bool) -> None:
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
        self.searchButton.setEnabled(self.source() is not None)
        self.moreButton.setEnabled(self._nextPage is not None)
        self.searchStatus.setText(str(error))

    def _showPage(self, catalog: Catalog, page: SearchPage, append: bool) -> None:
        self._resultsCatalog = catalog
        products = (self.results.products if append else []) + list(page.products)
        self.results.setProducts(products)
        self._loadThumbnails(page.products)
        self._nextPage = page.nextPage
        self.map.setFootprints(products)
        self.map.setSelected([])
        self.searchButton.setEnabled(self.source() is not None)
        self.moreButton.setEnabled(page.nextPage is not None)
        total = f" of {page.total}" if page.total is not None else ""
        self.searchStatus.setText(
            f"{len(products)}{total} products." if products else "Nothing found here."
        )
        if products and not append and self.areaBox.currentText() != AREA_VIEW:
            self.map.zoomTo(
                tuple(shapely.union_all([p.footprint for p in products]).bounds)
            )

    def _loadThumbnails(self, products) -> None:
        """Each product's thumbnail into the results, in the background."""
        for product in products:
            if product.thumbnail:
                self._withPicture(
                    [product.thumbnail],
                    lambda url, picture, p=product: self.results.setThumbnail(
                        p.id, toPixmap(picture)
                    ),
                    lambda error: None,  # the row just has no picture
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
        self._showMapPreviews()
        shown = products[:MAX_GALLERY]
        self._galleryProducts = {p.id: p for p in shown}
        current = self.gallery.currentId()
        self.gallery.setItems(
            [GalleryItem(p.id, p.title, _subtitle(p)) for p in shown],
            current if current in self._galleryProducts else None,
        )
        self._askBrowse(shown)
        for product in shown:
            self._loadGalleryPicture(product)
        self._onGalleryCurrent(self.gallery.currentId() or "")
        if len(products) > MAX_GALLERY:
            self.mapNote.setText(
                f"The first {MAX_GALLERY} of {len(products)} selected are in the gallery."
            )

    def _loadGalleryPicture(self, product: Product) -> None:
        """The product's picture in the gallery: the chosen browse product (or
        its first, a color composite), else the catalog's browse image, else
        its thumbnail."""
        urls = list(
            dict.fromkeys(
                u
                for u in (
                    self._mapUrl.get(product.id, ""),  # known to be a map
                    *self._browseUrls(product, forMap=False)[:1],
                    product.preview,
                    product.thumbnail,
                )
                if u
            )
        )
        if not urls:
            self.gallery.setUnavailable(product.id, "No preview.")
            return
        self._withPicture(
            urls,
            lambda url, picture: self.gallery.setPicture(product.id, toPixmap(picture)),
            lambda error: self.gallery.setUnavailable(product.id, "No preview."),
        )

    def previewChoice(self) -> str:
        return str(self.previewBox.currentData() or PREVIEW_AUTO)

    def _browseUrls(self, product: Product, forMap: bool = True) -> list[str]:
        """The product's browse products to try, best first: the chosen kind
        alone, or (Automatic) all of them. Empty until its catalog has said."""
        images = self._browse.get(product.id, [])
        choice = self.previewChoice()
        if choice not in (PREVIEW_AUTO, PREVIEW_OFF):
            chosen = [b.url for b in images if b.kind == choice]
            if chosen or forMap:
                return chosen
        return [b.url for b in images]

    def _askBrowse(self, products: list[Product]) -> None:
        """Ask the results' catalog for these products' browse products (once
        each); when they arrive, the gallery and the map use them."""
        catalog = self._resultsCatalog
        if catalog is None:
            return
        for product in products:
            if product.id in self._browseAsked:
                continue
            self._browseAsked.add(product.id)

            def fetch(p: Product = product) -> list[BrowseImage]:
                return catalog.browseImages(p)

            runInBackground(
                fetch,
                lambda images, p=product: self._gotBrowse(p, images),
                lambda error, p=product: self._gotBrowse(p, []),
                description="Listing a product's browse images",
                owner=self,
            )

    def _gotBrowse(self, product: Product, images: list[BrowseImage]) -> None:
        self._browse[product.id] = list(images)
        self._refreshPreviewKinds()
        if product.id in getattr(self, "_galleryProducts", {}) and images:
            self._loadGalleryPicture(product)
        if product in self.selectedProducts()[:MAX_MAP_PREVIEWS]:
            self._notMapLike.discard(product.id)
            self.map.keepPreviews(set(self.map.previewIds()) - {product.id})
            self._showMapPreviews()

    def _refreshPreviewKinds(self) -> None:
        """The chooser offers Automatic, Off, and every kind the selected
        products have (in the catalog's order); a chosen kind stays."""
        kinds: list[str] = []
        for product in self.selectedProducts():
            for image in self._browse.get(product.id, []):
                if image.kind not in kinds:
                    kinds.append(image.kind)
        choice = self.previewChoice()
        if choice not in (PREVIEW_AUTO, PREVIEW_OFF) and choice not in kinds:
            kinds.append(choice)
        wanted = [PREVIEW_AUTO, PREVIEW_OFF, *kinds]
        if [
            self.previewBox.itemData(i) for i in range(self.previewBox.count())
        ] == wanted:
            return
        box = self.previewBox
        box.blockSignals(True)
        box.clear()
        box.addItem(PREVIEW_AUTO, PREVIEW_AUTO)
        box.addItem(PREVIEW_OFF, PREVIEW_OFF)
        if kinds:
            box.insertSeparator(box.count())
        for kind in kinds:
            box.addItem(kind, kind)
        box.setCurrentIndex(max(box.findData(choice), 0))
        box.blockSignals(False)

    def _onPreviewChoice(self) -> None:
        """Another browse product (or none): redraw the map and the gallery."""
        self._notMapLike.clear()
        self._mapUrl.clear()
        self.map.keepPreviews(set())
        self._showMapPreviews()
        for product in getattr(self, "_galleryProducts", {}).values():
            self._loadGalleryPicture(product)
        current = self._galleryProducts.get(self.gallery.currentId() or "")
        if current is not None:
            self._showMapNote(current)

    def _onGalleryCurrent(self, productId: str) -> None:
        """The product in front of the gallery: its details, and its footprint
        outlined on the map."""
        product = getattr(self, "_galleryProducts", {}).get(productId)
        self.map.setFocused(product)
        if product is None:
            self.details.setText("")
            self.mapNote.setText("")
            return
        lines = [f"<b>{product.id}</b>"]
        if product.title != product.id:
            lines.append(product.title)
        if product.start:
            lines.append(f"Start: {product.start:%Y-%m-%d %H:%M:%S}")
        lines += [f"{key}: {value}" for key, value in product.properties.items()]
        self.details.setText("<br>".join(lines))
        self._showMapNote(product)

    def _zoomToProduct(self, productId: str) -> None:
        product = getattr(self, "_galleryProducts", {}).get(productId)
        if product is not None:
            self.map.zoomTo(tuple(product.footprint.bounds))

    def _withPicture(
        self,
        urls: list[str],
        done: Callable[[str, np.ndarray], None],
        failed: Callable[[Exception], None],
    ) -> None:
        """The first of ``urls`` that gives a picture, from the cache or
        fetched in the background, to ``done`` (with its URL)."""
        for url in urls:
            picture = self._pictures.get(url)
            if picture is not None:
                done(url, picture)
                return

        def load() -> tuple[str, np.ndarray]:
            for url in urls[:-1]:
                try:
                    return url, loadPicture(url)
                except CatalogError:
                    continue
            return urls[-1], loadPicture(urls[-1])

        def loaded(result: tuple[str, np.ndarray]) -> None:
            self._pictures.put(*result)
            done(*result)

        runInBackground(
            load, loaded, failed, description="Fetching a preview", owner=self
        )

    def _showMapNote(self, product: Product) -> None:
        if product.id not in self._notMapLike:
            self.mapNote.setText("")
            return
        choice = self.previewChoice()
        kinds = {b.kind for b in self._browse.get(product.id, [])}
        if choice not in (PREVIEW_AUTO, PREVIEW_OFF) and choice not in kinds:
            self.mapNote.setText(f"Not drawn on the map: this product has no {choice}.")
        else:
            self.mapNote.setText(NOT_ON_MAP)

    # --- previews on the map ------------------------------------------------------------

    def _showMapPreviews(self) -> None:
        """Draw the selected products' previews (the first
        ``MAX_MAP_PREVIEWS``) over their footprints: the chosen browse product,
        or (Automatic) the first browse image that is a north-up map of the
        footprint (``previews.isMapOfFootprint``); remove the others."""
        self._mapPreviewRequest += 1
        request = self._mapPreviewRequest
        off = self.previewChoice() == PREVIEW_OFF
        products = [] if off else self.selectedProducts()[:MAX_MAP_PREVIEWS]
        self.map.keepPreviews({p.id for p in products})
        shown = set(self.map.previewIds())
        projector = self.map.projector
        # pyproj objects stay on this thread: workers get plain values
        frame = MapFrame.of(projector)
        automatic = self.previewChoice() == PREVIEW_AUTO
        for product in products:
            box = footprintBox(product.footprint)
            if product.id in shown or box is None:
                continue
            if product.id not in self._browse and self._resultsCatalog is not None:
                continue  # drawn when its browse products arrive (_gotBrowse)
            urls = self._browseUrls(product)
            if automatic and product.preview:
                urls = [*urls, product.preview]
            urls = list(dict.fromkeys(urls))
            if not urls:
                self._notMapLike.add(product.id)
                if self.gallery.currentId() == product.id:
                    self._showMapNote(product)
                continue
            bounds = projector.mapBounds(box)
            if bounds is None:  # in the other hemisphere
                continue
            self._drapeFirstMap(request, product, urls, frame, bounds)

    def _drapeFirstMap(
        self,
        request: int,
        product: Product,
        urls: list[str],
        frame: MapFrame,
        bounds: Bounds,
    ) -> None:
        """In the background: the first of ``urls`` whose picture is a map of
        the footprint, draped onto the map (pictures already fetched are
        reused, those fetched now kept)."""
        cached = {url: self._pictures.get(url) for url in urls}

        Drawn = tuple[str, np.ndarray | None, dict[str, np.ndarray]]

        def onMap() -> Drawn:
            fetched: dict[str, np.ndarray] = {}
            for url in urls:
                picture = cached[url]
                if picture is None:
                    try:
                        picture = fetched[url] = loadPicture(url)
                    except CatalogError:
                        continue
                if isMapOfFootprint(picture, product.footprint):
                    rgba = drape(picture, product.footprint, frame, bounds)
                    return url, rgba, fetched
            return "", None, fetched

        def done(result: Drawn) -> None:
            url, rgba, fetched = result
            for fetchedUrl, picture in fetched.items():
                self._pictures.put(fetchedUrl, picture)
            if url and self._mapUrl.get(product.id) != url:
                self._mapUrl[product.id] = url
                if product.id in getattr(self, "_galleryProducts", {}):
                    self._loadGalleryPicture(product)  # the map, in the gallery too
            self._placePreview(request, product, rgba, bounds)

        runInBackground(
            onMap,
            done,
            lambda error: None,
            description="Drawing a preview on the map",
            owner=self,
        )

    def _placePreview(
        self, request: int, product: Product, rgba: np.ndarray | None, bounds: Bounds
    ) -> None:
        if rgba is None:
            self._notMapLike.add(product.id)
            if self.gallery.currentId() == product.id:
                self._showMapNote(product)
            return
        if request == self._mapPreviewRequest:
            self.map.setPreview(product.id, rgba, bounds)

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
        self._showMapPreviews()  # the map drops them when it changes projection

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
        source = self.source()
        return {
            "body": self.body(),
            "source": source.key if source else "",
            # as Varda 0.12 wrote them, for older Vardas
            "catalog": source.catalogId if source else "",
            "collection": source.collection.id if source else "",
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
            "previews": self.previewChoice() != PREVIEW_OFF,
            "mapPreview": self.previewChoice(),
        }

    def restoreSessionState(self, state: dict) -> None:
        for path in state.get("footprintFiles", []):
            if path not in FootprintFileCatalog.paths:
                FootprintFileCatalog.paths.append(path)
        if "basemapLayer" in state:  # chosen once the collection's body is known
            self._pendingLayer = state["basemapLayer"]
        projection = MapProjection.__members__.get(state.get("projection", ""))
        if projection is not None:
            self.projectionBox.setCurrentIndex(self.projectionBox.findData(projection))
        key = state.get("source") or (
            sourceKey(state["catalog"], state["collection"])
            if state.get("catalog") and state.get("collection")
            else ""
        )
        body = state.get("body")
        if body:
            self._bodyPicked = True
        # shown again even if it's the same: the session's basemap applies then
        self._showBody(body or self.body())
        if key:
            listed = next((s for s in self._sources if s.key == key), None)
            if listed is None:
                self._pendingSource = key  # chosen once its catalog lists it
            else:
                self._chosen[listed.body] = key
                if listed.body != self.body():
                    self._showBody(listed.body)
        self._refreshSources()
        area = self.areaBox.findText(state.get("area", AREA_VIEW))
        self.areaBox.setCurrentIndex(max(area, 0))
        if state.get("box"):
            self.map.setArea(tuple(state["box"]))
        if state.get("view"):
            self.map.zoomTo(tuple(state["view"]), padding=0)
        for path in state.get("overlays", []):
            self.addOverlay(path)
        self.namesCheck.setChecked(state.get("names", True))
        choice = state.get("mapPreview") or (
            PREVIEW_AUTO if state.get("previews", True) else PREVIEW_OFF
        )
        if self.previewBox.findData(choice) < 0:  # a kind: offered once seen
            self.previewBox.addItem(choice, choice)
        self.previewBox.setCurrentIndex(self.previewBox.findData(choice))
