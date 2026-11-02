from PyQt6 import QtWidgets, QtCore
from PyQt6.QtWidgets import QDialog, QMessageBox


class ProcessDialog(QDialog):
    """Dialog box that can dynamically generate parameter controls for an image
    process and create new images in the _test_project_module_thing.
    """

    sigProcessFinished = QtCore.pyqtSignal()

    def __init__(self, image=None, parent=None):
        super().__init__(parent)
        self.image = image
        self.current_dialog = None
        self.proj = self._getProjectContext()
        self.image_combobox = None

    def openProcessControlMenu(self, process):
        if self.current_dialog:
            self.current_dialog.close()

        self.current_dialog = QtWidgets.QDialog()
        self.current_dialog.setWindowTitle(process.name)
        layout = QtWidgets.QFormLayout()
        layout.setSpacing(10)
        self.current_dialog.setLayout(layout)

        # Add image selection combobox at the top
        if self.proj:
            # Create a label for the image selection
            imageLabel = QtWidgets.QLabel("Select Image:")
            imageLabel.setToolTip("Select the image to process")

            # Create the combobox
            self.image_combobox = QtWidgets.QComboBox()

            # Get all images from the project
            all_images = self.proj.getAllImages()

            # Populate the combobox with image names
            for i, img in enumerate(all_images):
                name = img.metadata.name or f"Image {i}"
                self.image_combobox.addItem(
                    name, i
                )  # Store the image index as user data

            # Set the current image as the selected item if it exists
            if self.image:
                # Find the index of the current image in the project
                for i, img in enumerate(all_images):
                    if img is self.image:
                        self.image_combobox.setCurrentIndex(i)
                        break

            # Connect the combobox signal to update the selected image
            self.image_combobox.currentIndexChanged.connect(self.updateSelectedImage)

            # Add the combobox to the layout
            layout.addRow(imageLabel, self.image_combobox)

            # Add a separator
            separator = QtWidgets.QFrame()
            separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
            separator.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
            layout.addRow(separator)

        # Store parameter widgets
        self.parameter_widgets = {}

        for name, details in process.parameters.items():
            paramName = QtWidgets.QLabel()
            paramName.setText(name)
            paramName.setToolTip(details["description"])

            if details["type"] is float:
                lineEdit = QtWidgets.QDoubleSpinBox()
                lineEdit.setValue(details["default"])
                lineEdit.setDecimals(2)
                if "min" in details and "max" in details:
                    lineEdit.setRange(details["min"], details["max"])
                self.parameter_widgets[name] = lineEdit
                layout.addRow(paramName, lineEdit)

            elif details["type"] is bool:
                lineEdit = QtWidgets.QCheckBox()
                lineEdit.setChecked(details["default"])
                self.parameter_widgets[name] = lineEdit
                layout.addRow(paramName, lineEdit)

        layout.addItem(
            QtWidgets.QSpacerItem(
                0,
                20,
                QtWidgets.QSizePolicy.Policy.Minimum,
                QtWidgets.QSizePolicy.Policy.Expanding,
            )
        )
        executeButton = QtWidgets.QPushButton("Execute")
        executeButton.clicked.connect(lambda: self.processImage(process))
        layout.addWidget(executeButton)
        layout.addItem(
            QtWidgets.QSpacerItem(
                60,
                0,
                QtWidgets.QSizePolicy.Policy.Fixed,
                QtWidgets.QSizePolicy.Policy.Minimum,
            )
        )
        self.current_dialog.exec()

    def processImage(self, process):
        """Execute the image process and create a new image."""
        if self.image is None:
            QMessageBox.warning(self, "Error", "No image selected for processing.")
            return

        if self.proj is None:
            QMessageBox.critical(self, "Error", "No project context available.")
            return

        try:
            # Import dataclasses for copying metadata
            from dataclasses import replace

            # Get parameter values
            kwargs = {}
            for param_name, widget in self.parameter_widgets.items():
                if isinstance(widget, QtWidgets.QDoubleSpinBox):
                    kwargs[param_name] = widget.value()
                elif isinstance(widget, QtWidgets.QCheckBox):
                    kwargs[param_name] = widget.isChecked()

            # Close the dialog first
            if self.current_dialog:
                self.current_dialog.accept()

            print(f"Processing {process.name} with parameters: {kwargs}")

            # Create process instance
            process_instance = process()

            # Get the appropriate input data for this process type
            if hasattr(self.image, "raster"):
                input_data = self.image.raster
            else:
                QMessageBox.critical(
                    self, "Error", "Selected image has no raster data."
                )
                return

            print(f"Input data shape for {process.name}: {input_data.shape}")

            if (
                hasattr(process, "input_data_type")
                and process.input_data_type == "current_rgb"
            ):
                current_band = self.image.band[0]
                print(
                    f"Using RGB bands: R={current_band.r}, G={current_band.g}, B={current_band.b}"
                )

            # Execute process with appropriate input data
            processed_raster = process_instance.execute(input_data, **kwargs)

            print(f"Processing completed. New raster shape: {processed_raster.shape}")

            # Create new metadata using dataclasses.replace
            original_name = self.image.metadata.name or "Image"

            # Generate a more descriptive name for RGB-based processes
            if process.input_data_type == "current_rgb":
                current_band = self.image.band[0]
                band_info = f"b{current_band.r}_b{current_band.g}_b{current_band.b}"

                # Create descriptive name: basename_b1_b2_b3_Process
                new_name = f"{original_name}_{band_info}_{process.name}"
            else:
                # For non-RGB processes, use the original simple naming
                new_name = f"{original_name} - {process.name}"

            new_metadata = replace(
                self.image.metadata,
                name=new_name,
                filePath=None,
            )

            print(f"Creating new image: {new_metadata.name}")

            # Create new image in the project
            new_index = self.proj.createImage(
                raster=processed_raster, metadata=new_metadata
            )

            print(f"New image created at index: {new_index}")

            QMessageBox.information(
                self,
                "Process Complete",
                f"{process.name} completed successfully!\n"
                f"New image created: {new_metadata.name}",
            )

            # Emit signal that processing is finished
            self.sigProcessFinished.emit()

        except Exception as e:
            import traceback

            error_details = traceback.format_exc()
            print(f"Processing error: {error_details}")
            QMessageBox.critical(
                self, "Process Error", f"Error executing {process.name}:\n{str(e)}"
            )

    def updateSelectedImage(self, index):
        """
        Update the selected image when the combobox selection changes.

        Args:
            index: The index of the selected item in the combobox
        """
        if self.image_combobox and self.proj:
            # Get the image index from the combobox user data
            image_index = self.image_combobox.itemData(index)

            # Get the image from the project
            try:
                self.image = self.proj.getImage(image_index)
                print(f"Selected image: {self.image.metadata.name}")
            except (IndexError, AttributeError) as e:
                print(f"Error selecting image: {e}")
                self.image = None

    def _getProjectContext(self):
        """Get the project context from the parent widget chain."""
        # Walk up the parent chain to find the MainGUI window
        parent = self.parent()
        while parent:
            if hasattr(parent, "proj"):
                return parent.proj
            parent = parent.parent()
        return None
