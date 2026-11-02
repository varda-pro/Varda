from PyQt6.QtWidgets import QMenu
from PyQt6.QtGui import QAction
from PyQt6.QtCore import QObject

from varda.image_processing.process_controls.processdialog import ProcessDialog


class ImageProcessMenuSystem(QObject):
    """
    A system for creating menus and actions for registered image processes.

    This system:
    1. Creates QMenus/Actions for all registered image processes
    2. Organizes them by category based on the process name (e.g., "my category/my process")
    3. Displays a dialog for parameter editing when a process is selected
    """

    def __init__(self, app, parent=None):
        """
        Initialize the image process menu system.

        Args:
            parent: The parent widget
        """
        super().__init__(parent)
        self.app = app
        self.parent = parent
        self.process_dialog = None

    def createProcessMenu(self):
        """
        Create a menu with all registered image processes.

        Returns:
            QMenu: A menu with all registered image processes organized by category
        """
        # Create the main process menu
        processMenu = QMenu("Process", self.parent)

        # Get all registered image processes
        registry = self.app.registry.imageProcesses

        # Dictionary to store category menus
        category_menus = {}

        # Iterate through all registered processes
        for process_name, process_class in registry.registryItems.items():
            # Get the process name which may include category path
            full_name = process_class.name

            # Split the name into categories and process name
            path_parts = full_name.split("/")

            # The last part is the actual process name
            process_display_name = path_parts[-1].strip()

            # The rest are categories
            categories = [part.strip() for part in path_parts[:-1]]

            # Start with the main process menu
            current_menu = processMenu

            # Create or navigate to category submenus
            for category in categories:
                # Create a key for this category path
                category_key = "/".join(categories[: categories.index(category) + 1])

                # If this category doesn't exist yet, create it
                if category_key not in category_menus:
                    category_menus[category_key] = QMenu(category, self.parent)
                    current_menu.addMenu(category_menus[category_key])

                # Move to this category's menu
                current_menu = category_menus[category_key]

            # Create an action for this process
            action = QAction(process_display_name, self.parent)
            action.triggered.connect(
                lambda checked=False, p=process_class: self.openProcessDialog(p)
            )

            # Add the action to the current menu
            current_menu.addAction(action)

        return processMenu

    def openProcessDialog(self, process_class):
        """
        Open a dialog for editing the parameters of a process.

        Args:
            process_class: The class of the process to edit
        """
        # Get the current image from the parent widget
        current_image = None
        if hasattr(self.parent, "getCurrentImage"):
            current_image = self.parent.getCurrentImage()

        # Create a process dialog if it doesn't exist
        if not self.process_dialog:
            self.process_dialog = ProcessDialog(image=current_image, parent=self.parent)

        # Open the process control menu
        self.process_dialog.openProcessControlMenu(process_class)


class MainMenuBarExtension:
    """
    Extension for the MainMenuBar class to add image process menus.
    """

    @staticmethod
    def addProcessMenuToMainMenuBar(main_menu_bar):
        """
        Add the image process menu to the main menu bar.

        Args:
            main_menu_bar: The MainMenuBar instance to add the menu to
        """
        # Create the image process menu system
        menu_system = ImageProcessMenuSystem(main_menu_bar)

        # Create the process menu
        process_menu = menu_system.createProcessMenu()

        # Add the menu to the main menu bar
        main_menu_bar.addMenu(process_menu)

        # Store the menu system in the main menu bar
        main_menu_bar.process_menu_system = menu_system

        return process_menu
