# Dangling-slot audit (2026-10-18)

## The crash

A signal connected to a `lambda` or `functools.partial` is not tied to any
receiver: PyQt keeps it connected until the *sender* is destroyed, and psygnal
holds it strongly. When the sender outlives a Qt object the slot touches and
then emits, the slot calls into a deleted C++ object: `RuntimeError: wrapped
C/C++ object … has been deleted`, or a segfault (e4650b1, the blandness
readout). A bound method avoids this for Qt signals: PyQt disconnects it when
the receiver is destroyed. psygnal holds bound methods weakly but knows nothing
about Qt deletion, so a widget whose Python wrapper is still referenced keeps
being called; `connectWhileAlive` (`common/parameter_widgets.py`) is the fix
there.

## Method

1. Every `.connect(lambda …)` and `.connect(partial(…))` in `src/` (tests
   excluded), found with an AST scan (single- and multi-line calls, Qt,
   pyqtgraph and psygnal signals): 238 at `bacff5f`, plus the sites PR #237
   added or changed (line numbers for those are at `5201328`).
2. Each site read and classified: **SAFE** when the sender is owned by (a
   child of) the object the slot touches, or the slot touches no Qt object;
   **RISKY** when the sender can outlive a Qt object the slot touches.
3. A dynamic check: each site was wrapped (temporarily) to record any call
   made while an object the slot captured was already deleted, and the full
   suite run (kindling's subprocess tests excluded). 121 of the 238 sites ran;
   none was called with a deleted capture. The RISKY sites below come from
   reading the lifetimes, then each pattern got a test that reproduces it.

## Results

242 sites: 227 SAFE, 15 RISKY. 7 RISKY sites fixed; 8 are in
`workspaces/dual_image_view/`, which nothing imports (dead code), and were
left alone.

Fixes:

| Pattern | Sites | Fix | Test |
|---|---|---|---|
| The project's image list (`ObservableList`, app lifetime) → lambda on a workspace widget | `raster_overlay.py:207`, `find_data_workspace.py:134`, `terrain_workspace.py:449` | bound method (Qt disconnects it with the receiver) | `workspaces/_tests/test_raster_overlay.py::test_a_deleted_bar_ignores_new_project_images` |
| A sibling's pyqtgraph signal → lambda on another object | `find_data/imagery.py:54` | bound method | `find_data/_tests/test_imagery.py` |
| psygnal model → lambda on a panel; a background step finishing after the workspace closed | `spectral_hourglass/pipeline_panel.py:476`; `task_runner.py` | `connectWhileAlive`; `runInBackground(owner=self)` and a deleted check on progress | `spectral_hourglass/_tests/test_teardown.py` |
| psygnal image signal → bound method on a widget (#237) | `roi_manager_widget.py:135`, `crism_ratio_workspace.py:194` | `connectWhileAlive`, after fixing it (below) | `common/_tests/test_parameter_widgets.py::test_a_deleted_widgets_connection_goes_without_waiting_for_the_collector` |

### `connectWhileAlive` and the #237 segfault

PR #237 found that `connectWhileAlive(image.sigMetadataChanged, …)` segfaulted
the full suite (a `LinearRegionItem` drawn after it was freed), and guarded
the slots with `sip.isdeleted` instead. The slot `connectWhileAlive` built was
never even called in the crashing run. The cause is that its closure held the
signal that held it: a reference cycle per connection, which only the cyclic
garbage collector frees. That garbage shifted when the collector ran, and a
collection during Qt's event dispatch freed a pyqtgraph item Qt was still
drawing (the failure the root `conftest.py` collects at a safe point after each
test to avoid). The slot is now a bound method of a small `QObject` child of
the owner: psygnal holds it weakly, Qt deletes it with the owner, and the
connection goes away without a collection. With that, the #237 sites use
`connectWhileAlive` and the full suite passes.

The pyqtgraph use-after-free under a mid-dispatch collection is still latent:
whatever leaves pyqtgraph items in reference cycles mid-test was not found.

### Related, not in scope

`runInBackground` without `owner=` delivers its result after the widget that
asked for it is gone. 31 calls exist; only the hourglass `TaskRunner` was fixed
here (its result emitted into the deleted panel). The rest were not audited.

## Inventory

Sender, the names the slot captures (free names of a lambda, a partial's
arguments), verdict and reason. "Exercised by the suite" marks the sites the
dynamic check saw run. Paths are under `src/varda/`; lines at `bacff5f`
unless marked #237.

| Site | Sender | Captures | Verdict | Reason |
|---|---|---|---|---|
| _actions/_help_actions.py:69 | `button.clicked` | `_copy`, `text` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| _actions/_plugin_actions.py:37 | `dialog.accepted` | `kindle`, `settings` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| _actions/_processing_actions.py:62 | `runner.sigFinished` | `showAccuracyReport`, `images`, `mainGui` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| _actions/_processing_actions.py:208 | `dialog.sigInverseRequested` | `openInverseTransformDialog`, `images`, `mainGui` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| _actions/_processing_actions.py:237 | `dialog.sigMapRequested` | `openFattMapDialog`, `images`, `mainGui` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| _actions/_processing_actions.py:264 | `dialog.sigProjected` | `ImageLoadingService`, `images` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| _actions/_workspace_actions.py:62 | `dialog.accepted` | `mainGui`, `LinkedImagesWorkspace`, `images`, `dialog` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| _actions/_workspace_actions.py:72 | `dialog.accepted` | `mainGui`, `CrismRatioWorkspace`, `images`, `dialog` | SAFE | Sender is a short-lived dialog/runner/button; the slot touches only app-lifetime objects (main window, project images) or nothing Qt. |
| accuracy_report_dialog.py:139 | `self.imageParam.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. |
| analysis/band_math.py:274 | `self.savedBox.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| analysis/crop_preview.py:164 | `span.sigRegionChangeFinished` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| analysis/dialog.py:142 | `self.imageParam.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. Exercised by the suite. |
| analysis/dialog.py:145 | `self.accepted` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| analysis/eigenvalue_dialog.py:109 | `self.imageParam.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. Exercised by the suite. |
| analysis/eigenvalue_dialog.py:110 | `self.components.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. Exercised by the suite. |
| analysis/fatt_dialog.py:192 | `self.filterEdit.textChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| analysis/fatt_dialog.py:204 | `self.table.currentCellChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| analysis/fatt_dialog.py:205 | `self.table.itemChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| analysis/fatt_dialog.py:254 | `self.imageParam.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. |
| analysis/fatt_dialog.py:255 | `self.factorCount.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. Exercised by the suite. |
| analysis/fatt_dialog.py:256 | `self.threshold.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. Exercised by the suite. |
| analysis/fatt_dialog.py:257 | `self.source.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| analysis/library_picker.py:50 | `self.box.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| analysis/shadow_mask.py:410 | `self.sunAzimuth.sigParameterChanged` | `self` | SAFE | psygnal, Qt-free: the slot touches no Qt object. Exercised by the suite. |
| analysis/shadow_mask.py:411 | `self.sunElevation.sigParameterChanged` | `self` | SAFE | psygnal, Qt-free: the slot touches no Qt object. Exercised by the suite. |
| common/_old_widgets/plot_appearance_panel.py:243 | `self.bg_color_button.colorChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:248 | `self.grid_x_checkbox.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:251 | `self.grid_y_checkbox.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:254 | `self.grid_color_button.colorChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:260 | `self.x_label_combo.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:263 | `self.y_label_combo.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:266 | `self.axis_color_button.colorChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:271 | `self.title_combo.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:274 | `self.title_size_spin.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:277 | `self.title_color_button.colorChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:282 | `self.legend_pos_combo.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_appearance_panel.py:285 | `self.legend_bg_button.colorChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1311 | `open_action.triggered` | `plot_data`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1318 | `duplicate_action.triggered` | `plot_data`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1322 | `rename_action.triggered` | `plot_data`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1328 | `remove_action.triggered` | `plot_data`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1282 | `select_action.triggered` | `thumb` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1285 | `select_action.triggered` | `thumb` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1293 | `unpin_action.triggered` | `self`, `plot_data` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/_old_widgets/plot_manager_tab.py:1297 | `pin_action.triggered` | `self`, `plot_data` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| common/parameter.py:59 | `instanceParam.sigParameterChanged` | `self` | SAFE | psygnal, Qt-free: the slot touches no Qt object. Exercised by the suite. |
| common/ui.py:207 | `self.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| image_loading/export_image_dialog.py:75 | `self.imageParam.sigParameterChanged` | `self` | SAFE | The receiver's own Parameter (psygnal), created and held only by it and its child widgets, so nothing else can set it once the receiver is gone. |
| image_loading/export_image_dialog.py:76 | `self.pathEdit.textChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| image_processing/image_process_menu.py:79 | `action.triggered` | `self`, `False`, `process_class` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| image_processing/process_controls/processdialog.py:98 | `executeButton.clicked` | `process`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| image_rendering/new_histogram_view.py:71 | `viewBox.sigXRangeChanged` | `plot`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| image_rendering/raster_view/viewport_tools/tool_manager.py:124 | `action.triggered` | `self`, `toolClass` | SAFE | Toolbar actions; manager and toolbar both belong to the same workspace; fires only on clicks. Exercised by the suite. |
| image_rendering/render_parameters.py:100 | `group.sigParameterChanged` | `self`, `algName` | SAFE | psygnal, Qt-free parameter: the slot touches no Qt object. Exercised by the suite. |
| kindling/ai_settings_dialog.py:155 | `self.provider.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| library_manager/add_to_library_dialog.py:49 | `self.targetBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| library_manager/manager_window.py:85 | `self.libraryList.currentRowChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| library_manager/manager_window.py:135 | `self.searchEdit.textChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| library_manager/reference_picker.py:59 | `self.searchEdit.textChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| library_manager/reference_picker.py:65 | `self.list.itemDoubleClicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| library_manager/reference_picker.py:71 | `manageButton.clicked` | `openLibraryManager`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| library_manager/reference_picker.py:135 | `action.toggled` | `self`, `library.name` | SAFE | Menu action owned by the picker. |
| lines/line_manager_widget.py:148 | `selection.selectionChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| maingui.py:71 | `self.centralTabs.tabCloseRequested` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| maingui.py:79 | `tabBar.tabMoved` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| optional_features/asp_section.py:91 | `self.redetectButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| optional_features/isis_section.py:58 | `redetect.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| plotting/plot.py:126 | `self.plotDataItem.sigClicked` | `self` | SAFE | Curve is a parentless, Python-owned QObject kept alive by the lambda; its plot item is removed with it. Exercised by the suite. |
| plotting/plot.py:434 | `self.markerConfig.sigParameterChanged` | `self` | SAFE | The plot's own MarkerConfig (psygnal), held only by the plot. Exercised by the suite. |
| plotting/plot.py:526 | `self.viewBox.sigRangeChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| plotting/plot.py:527 | `self.viewBox.sigResized` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| plotting/profile_plot.py:91 | `self.bandBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| plotting/profile_plot.py:102 | `self.plot.getViewBox().sigRangeChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| plugins/plugins_dialog.py:138 | `installFolder.clicked` | `self`, `chooseFolder` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| plugins/plugins_dialog.py:139 | `installZip.clicked` | `self`, `chooseZip` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| points/point_manager_widget.py:101 | `selectionModel.selectionChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/add_column_dialog.py:100 | `self.nameEdit.textChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/add_column_dialog.py:117 | `self.measureBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| rois/add_column_dialog.py:132 | `self.bandBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| rois/add_column_dialog.py:133 | `self.statisticBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/add_column_dialog.py:148 | `self.expressionEdit.textChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/mask_roi_dialog.py:63 | `group.buttonToggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/mask_roi_dialog.py:74 | `self.side.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| rois/mask_roi_dialog.py:75 | `self.band.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/mask_roi_dialog.py:79 | `self.maskBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/mask_roi_dialog.py:71 | `box.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/roi_drawing_widgets.py:144 | `self.newButton.clicked` | `state` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/roi_drawing_widgets.py:147 | `state.sigActiveClassChanged` | `self` | SAFE | psygnal ROIDrawingState, owned by the same workspace as the picker; pickers are built once at workspace creation. Exercised by the suite. |
| rois/roi_drawing_widgets.py:202 | `tool.sigROIDrawingComplete` | `self` | SAFE | The tool is a child of the workspace's ToolManager; the slot touches Qt-free state. Exercised by the suite. |
| rois/roi_drawing_widgets.py:105 | `button.clicked` | `state`, `shape` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/roi_drawing_widgets.py:187 | `manager.sigToolActivated` | `self`, `manager` | SAFE | ToolManager and controller are both children of the workspace; signal fires only on user tool choice. Exercised by the suite. |
| rois/roi_drawing_widgets.py:190 | `manager.sigToolDeactivated` | `self`, `manager` | SAFE | ToolManager and controller are both children of the workspace; signal fires only on user tool choice. Exercised by the suite. |
| rois/roi_manager_widget.py:89 | `self.selectAllButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/roi_manager_widget.py:91 | `self.deselectButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| rois/roi_manager_widget.py:99 | `collection.sigVisibilityChanged` | `self` | SAFE | psygnal ROICollection, owned by the same workspace as the widget; nothing mutates it after the workspace closes. Exercised by the suite. |
| rois/roi_manager_widget.py:164 | `self._table.sigDenominatorClearRequested` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| rois/roi_manager_widget.py:168 | `self._table.sigTemplateClearRequested` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| rois/roi_table_view.py:64 | `plotAction.triggered` | `fid`, `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. Exercised by the suite. |
| rois/roi_table_view.py:68 | `ratioAction.triggered` | `fid`, `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. Exercised by the suite. |
| rois/roi_table_view.py:74 | `onlyAction.triggered` | `fid`, `collection` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. Exercised by the suite. |
| rois/roi_table_view.py:77 | `allAction.triggered` | `collection` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. |
| rois/roi_table_view.py:85 | `setDenomAction.triggered` | `fid`, `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. Exercised by the suite. |
| rois/roi_table_view.py:92 | `clearDenomAction.triggered` | `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. Exercised by the suite. |
| rois/roi_table_view.py:101 | `setTemplateAction.triggered` | `fid`, `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. Exercised by the suite. |
| rois/roi_table_view.py:108 | `clearTemplateAction.triggered` | `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. |
| rois/roi_table_view.py:127 | `renameAction.triggered` | `columnName`, `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. |
| rois/roi_table_view.py:128 | `deleteAction.triggered` | `columnName`, `self` | SAFE | Context-menu action, local to an exec'd menu; the slot touches the view itself or the Qt-free collection. |
| self_test.py:121 | `view.loadFinished` | `outcome` | SAFE | Local objects; the slot writes a dict. |
| self_test.py:122 | `view.sigReady` | `outcome` | SAFE | Local objects; the slot writes a dict. |
| self_test.py:123 | `view.sigFailed` | `outcome` | SAFE | Local objects; the slot writes a dict. |
| startup.py:60 | `app.maingui.sigFilesDropped` | `importImagePaths`, `app` | SAFE | App-lifetime sender and receiver (main window, app context). |
| startup.py:63 | `app.context.changed` | `app` | SAFE | App-lifetime sender and receiver (main window, app context). |
| update_check.py:58 | `box.toggled` | `settings` | SAFE | Sender is a child of the message box / main window; the slot touches QSettings or the main window. Exercised by the suite. |
| update_check.py:161 | `timer.timeout` | `runInBackground`, `_latestOrNone`, `done` | SAFE | Sender is a child of the message box / main window; the slot touches QSettings or the main window. Exercised by the suite. |
| workspaces/crism_ratio/crism_ratio_workspace.py:228 | `manager.sigToolActivated` | `self._onToolActivated`, `index` | SAFE | The ToolManager is a child of the workspace (the receiver). Exercised by the suite. |
| workspaces/dual_image_view/dual_image_selection_dialog.py:257 | `self.opacitySlider.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| workspaces/dual_image_view/dual_image_view.py:176 | `self.spectral_tool_button.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| workspaces/dual_image_view/dual_image_view.py:764 | `self.proj.sigDataChanged[int, ProjectContext.ChangeType]` | `self`, `image_index` | RISKY (dead code, left) | Sender is app-level ProjectContext, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view.py:773 | `self.proj.sigDataChanged[ int, ProjectContext.ChangeType, ProjectContext.ChangeModifier ]` | ? | RISKY (dead code, left) | Sender is app-level ProjectContext, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view.py:1058 | `button.clicked` | `tool_name`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| workspaces/dual_image_view/dual_image_view.py:1090 | `timer.timeout` | `self`, `image_index` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| workspaces/dual_image_view/dual_image_view.py:955 | `vm.sigStretchChanged` | `self`, `image_index` | RISKY (dead code, left) | Sender is another view's view model, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view.py:960 | `vm.sigBandChanged` | `self`, `image_index` | RISKY (dead code, left) | Sender is another view's view model, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view.py:990 | `raster_view.sigImageClicked` | `self`, `image_index` | RISKY (dead code, left) | Sender is an external raster view, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view.py:995 | `raster_view.sigNavigationChanged` | `self`, `image_index` | RISKY (dead code, left) | Sender is an external raster view, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view.py:1002 | `raster_view.sigROIChanged` | `self`, `image_index` | RISKY (dead code, left) | Sender is an external raster view, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view.py:1007 | `raster_view.sigStretchChanged` | `self`, `image_index` | RISKY (dead code, left) | Sender is an external raster view, which outlives this view; the module is not imported anywhere. |
| workspaces/dual_image_view/dual_image_view_controller.py:379 | `self.blink_manager.blink_started` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| workspaces/dual_image_view/dual_image_view_controller.py:382 | `self.blink_manager.blink_stopped` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. (Dead code: not imported.) |
| workspaces/dual_image_workspace/dual_image_workspace.py:483 | `plot.sigSavePointRequested` | `self._savePoint`, `plot` | SAFE | The tool/plot is a (grand)child of the workspace (the receiver). |
| workspaces/dual_image_workspace/dual_image_workspace.py:459 | `tool.sigPixelSelected` | `self._onPixelSelected`, `tool.viewport.imageEntity` | SAFE | The tool/plot is a (grand)child of the workspace (the receiver). Exercised by the suite. |
| workspaces/dual_image_workspace/dual_image_workspace.py:469 | `tool.sigCropRequested` | `self._onCropRequested`, `tool.viewport.imageEntity` | SAFE | The tool/plot is a (grand)child of the workspace (the receiver). |
| workspaces/dual_image_workspace/dual_image_workspace_initializer.py:40 | `self.accepted` | `DualImageWorkspace`, `self` | SAFE | The dialog is the sender; the slot emits the dialog's own signal. Exercised by the suite. |
| workspaces/find_data/catalog_dialogs.py:118 | `add.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/catalog_dialogs.py:120 | `remove.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/catalog_dialogs.py:122 | `reset.clicked` | `DEFAULT_APIS`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/downloads_panel.py:131 | `self.folder.editingFinished` | `FOLDER_KEY`, `QSettings`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:161 | `selection.selectionChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/find_data/find_data_workspace.py:162 | `self.table.doubleClicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:173 | `self.streamer.sigStatus` | `self` | SAFE | The streamer is a child of the workspace; it emits on user/basemap changes, not in teardown. Exercised by the suite. |
| workspaces/find_data/find_data_workspace.py:185 | `self.catalogBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:189 | `self.loginButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:201 | `self.collectionBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:209 | `self.areaBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/find_data/find_data_workspace.py:273 | `self.projectionBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/find_data/find_data_workspace.py:280 | `self.basemapBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/find_data/find_data_workspace.py:281 | `self.basemapBox.activated` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:304 | `self.goTo.returnPressed` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/find_data/find_data_workspace.py:366 | `checkAll.clicked` | `self`, `p` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:370 | `checkNone.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/find_data/find_data_workspace.py:134 | `self.projectImages.sigDataChanged` | `self` | RISKY (fixed) | Project image list (app lifetime) outlives a closed Find Data tab. Fixed: bound method `_onProjectImagesChanged`. Exercised by the suite. |
| workspaces/find_data/find_data_workspace.py:428 | `catalog.body.sigParameterChanged` | `self` | SAFE | The catalog instance is created and held by this workspace only. |
| workspaces/find_data/imagery.py:54 | `self.map.viewBox.sigRangeChanged` | `self` | RISKY (fixed) | The map is the streamer's sibling (it moves into a dock, so it is deleted after the streamer); a range change in teardown started the deleted streamer's timer. Fixed: bound method `_onViewChanged`. Exercised by the suite. |
| workspaces/find_data/map_view.py:144 | `self.viewBox.sigRangeChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/find_data/map_view.py:149 | `self.viewBox.sigRangeChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/general_image_analysis/general_image_analysis.py:345 | `plot.sigSavePointRequested` | `self._savePoint`, `plot` | SAFE | The tool/plot is a (grand)child of the workspace (the receiver). |
| workspaces/general_image_analysis/general_image_analysis.py:383 | `tool.sigPixelSelected` | `self._onPixelSelected`, `tool.viewport.imageEntity` | SAFE | The tool/plot is a (grand)child of the workspace (the receiver). Exercised by the suite. |
| workspaces/general_image_analysis/general_image_analysis.py:396 | `tool.sigCropRequested` | `self._onCropRequested`, `tool.viewport.imageEntity` | SAFE | The tool/plot is a (grand)child of the workspace (the receiver). Exercised by the suite. |
| workspaces/general_image_analysis/general_image_analysis_initializer.py:25 | `self.accepted` | `GeneralImageAnalysisWorkflow`, `self` | SAFE | The dialog is the sender; the slot emits the dialog's own signal. |
| workspaces/linked_images/linked_images_workspace.py:191 | `self.lineManager.sigSelectionChanged` | `self` | SAFE | The ToolManager/tool/line manager is a (grand)child of the workspace (the receiver). Exercised by the suite. |
| workspaces/linked_images/linked_images_workspace.py:83 | `viewport.sigViewRangeChangedManually` | `self.follow`, `index` | SAFE | Viewports and the link are children of the same workspace; the signal fires only on user pan/zoom gestures. Exercised by the suite. |
| workspaces/linked_images/linked_images_workspace.py:125 | `manager.sigToolActivated` | `self._onTool`, `index` | SAFE | The ToolManager/tool/line manager is a (grand)child of the workspace (the receiver). Exercised by the suite. |
| workspaces/linked_images/linked_images_workspace.py:248 | `tool.sigPixelSelected` | `self._onPixel`, `index` | SAFE | The ToolManager/tool/line manager is a (grand)child of the workspace (the receiver). |
| workspaces/linked_images/linked_images_workspace.py:249 | `tool.sigPixelDragged` | `self._onPixel`, `index` | SAFE | The ToolManager/tool/line manager is a (grand)child of the workspace (the receiver). Exercised by the suite. |
| workspaces/linked_images/linked_images_workspace.py:252 | `tool.sigLineDrawn` | `self.addLine`, `index` | SAFE | The ToolManager/tool/line manager is a (grand)child of the workspace (the receiver). |
| workspaces/linked_images/linked_images_workspace.py:254 | `tool.sigCropRequested` | `self._onCrop`, `self.images[index]` | SAFE | The ToolManager/tool/line manager is a (grand)child of the workspace (the receiver). |
| workspaces/linked_images/spectra_panel.py:104 | `action.toggled` | `self`, `index` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/mosaic/cam2map_dialog.py:60 | `button.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/mosaic/mosaic_workspace.py:146 | `self.seamsPanel.sigEdit` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/mosaic/mosaic_workspace.py:262 | `dialog.sigRun` | `dialog`, `self` | SAFE | The dialog is a child of the workspace (dies with it) and is the sender. Exercised by the suite. |
| workspaces/mosaic/mosaic_workspace.py:267 | `dialog.sigBandUsed` | `dialog`, `self` | SAFE | The dialog is a child of the workspace (dies with it) and is the sender. Exercised by the suite. |
| workspaces/mosaic/panels.py:90 | `up.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/mosaic/panels.py:91 | `down.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/mosaic/panels.py:100 | `autoAlign.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/mosaic/panels.py:102 | `fit.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/mosaic/panels.py:457 | `self.method.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/mosaic/panels.py:717 | `self.blend.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/mosaic/panels.py:718 | `self.featherWidth.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/mosaic/panels.py:719 | `self.levels.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/mosaic/views.py:142 | `roi.sigRegionChangeFinished` | `self`, `s`, `r` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/pixel_spectra_docks.py:112 | `plot.newPlotButton.clicked` | `self` | SAFE | The plot lives in a dock of the same workspace; button/checkbox signals fire only on user clicks. Exercised by the suite. |
| workspaces/pixel_spectra_docks.py:113 | `plot.activeCheckBox.toggled` | `self`, `plot` | SAFE | The plot lives in a dock of the same workspace; button/checkbox signals fire only on user clicks. Exercised by the suite. |
| workspaces/raster_overlay.py:181 | `self.reverseAction.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/raster_overlay.py:199 | `self.layerBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/raster_overlay.py:201 | `self.legendButton.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/raster_overlay.py:219 | `group.triggered` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/raster_overlay.py:207 | `images.sigDataChanged` | `self` | RISKY (fixed) | Project image list (app lifetime) outlives a closed workspace's overlay bar. Fixed: bound method. Exercised by the suite. |
| workspaces/ratio_explorer.py:166 | `tool.sigSelectionChanged` | `self._onSelection`, `image` | SAFE | The tool is a child of a ToolManager parented to the same workspace as the controller; fires only on user clicks. Exercised by the suite. |
| workspaces/ratio_explorer.py:167 | `tool.sigSelectionChanged` | `self._mirrorSelection`, `tool.viewport` | SAFE | The tool is a child of a ToolManager parented to the same workspace as the controller; fires only on user clicks. Exercised by the suite. |
| workspaces/spectral_hourglass/endmember_table.py:66 | `self.table.currentCellChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/hourglass_workspace.py:167 | `saveButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/hourglass_workspace.py:169 | `openButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/hourglass_workspace.py:217 | `self.nd.collapseBox.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/hourglass_workspace.py:221 | `self.pipeline.reducePage.sigShowComponent` | `Overlay`, `self` | SAFE | Sibling pages of the same workspace; the signal fires only on a button click. |
| workspaces/spectral_hourglass/identify_panel.py:63 | `self.windowBox.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/identify_panel.py:80 | `self.table.currentCellChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/identify_panel.py:62 | `spin.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/mtmf_review.py:40 | `self.endmemberBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/mtmf_review.py:72 | `addButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/mtmf_review.py:69 | `line.sigPositionChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/nd_visualizer.py:171 | `self.rotateButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/nd_visualizer.py:172 | `self.lassoButton.clicked` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/nd_visualizer.py:183 | `self.axesBox.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/nd_visualizer.py:176 | `shortcut.activated` | `self`, `mode` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/pipeline_panel.py:102 | `self.runButton.clicked` | `run`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:175 | `self.components.sigParameterChanged` | `model` | SAFE | The panel's own IntParameter; the slot calls the Qt-free model. Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:185 | `self.components.sigParameterChanged` | `self` | SAFE | The panel's own IntParameter (psygnal), created and held only by the panel. Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:257 | `self.runButton.clicked` | `run`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:263 | `self.moreButton.clicked` | `run`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:394 | `self.runButton.clicked` | `run`, `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:476 | `model.sigStepChanged` | `self` | RISKY (fixed) | psygnal model signal held the lambda strongly; a step finishing in the background after the workspace closed emitted it into the deleted panel. Fixed: `connectWhileAlive` (and the runner now passes `owner=self` to `runInBackground`). Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:89 | `self.rangeSlider.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/pipeline_panel.py:461 | `button.clicked` | `self`, `False`, `step` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/scene_view.py:141 | `self.overlayBox.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/spectral_hourglass/scene_view.py:145 | `self.componentSpin.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/scene_view.py:148 | `self.mapBandBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/spectral_hourglass/scene_view.py:169 | `self.shapeBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/overlay_panel.py:92 | `self.list.currentRowChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:274 | `self.fpsBox.currentTextChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/terrain/terrain_workspace.py:456 | `self.demBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/terrain/terrain_workspace.py:458 | `self.bandBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:464 | `self.drapeBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:469 | `self.colorMapBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/terrain/terrain_workspace.py:474 | `self.detailBox.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/terrain/terrain_workspace.py:481 | `self.fillGaps.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:488 | `self.showFilled.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:497 | `self.edges.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:510 | `self.heightUnit.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:553 | `self.sides.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:554 | `self.baseDepth.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:559 | `self.shadows.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/terrain/terrain_workspace.py:560 | `self.background.currentIndexChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/terrain/terrain_workspace.py:273 | `box.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:356 | `box.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:449 | `projectImages.sigDataChanged` | `self` | RISKY (fixed) | Project image list (app lifetime) outlives a closed 3D Terrain tab. Fixed: bound method `_onProjectImagesChanged`. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:535 | `box.toggled` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| workspaces/terrain/terrain_workspace.py:556 | `box.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. Exercised by the suite. |
| workspaces/terrain/terrain_workspace.py:558 | `box.valueChanged` | `self` | SAFE | Sender is a child of the receiver (or the receiver itself), so they are destroyed together. |
| common/parameter_widgets.py:342 (#237) | `self.lineEdit.editingFinished` | `self` | SAFE | The line edit is the widget's child. editingFinished can fire as focus leaves during teardown, but the slot reads only the live sender and the Qt-free parameter (whose widgets use connectWhileAlive). |
| image_loading/crism_geometry_chooser.py:66 (#237) | `button.clicked` | `image`, `need`, `button` | SAFE | The sender is the button the slot touches; the image is Qt-free. |
| rois/roi_manager_widget.py:135 (#237, bound method) | `image.sigMetadataChanged (psygnal)` | `self` | RISKY (fixed) | The image outlives the ROI manager. #237 guarded the slot with sip.isdeleted after connectWhileAlive segfaulted the suite; with connectWhileAlive fixed (no reference cycle) it now uses connectWhileAlive. |
| workspaces/crism_ratio/crism_ratio_workspace.py:194 (#237, bound method) | `self.data.sigMetadataChanged (psygnal)` | `self` | RISKY (fixed) | The image outlives the workspace. Same as above: connectWhileAlive instead of the in-slot sip.isdeleted guard. |
