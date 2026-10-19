from typing import Optional, Type

from PyQt6.QtCore import QObject, pyqtSignal, QEvent
from PyQt6.QtWidgets import QFrame, QSizePolicy, QToolButton, QWidget
from PyQt6.QtGui import QAction, QActionGroup

from varda.common.ui import FlowLayout
from varda.image_rendering.raster_view.viewport_tools.viewport_tool import ViewportTool
from varda.image_rendering.raster_view.image_viewport import ImageViewport
from varda.image_rendering.raster_view.pointer_event import KeyEvent
from varda.image_rendering.raster_view.viewport_tools.tool_registry import ToolRegistry


class WrappingToolBar(QWidget):
    """A row of tool buttons that wraps onto another row when the view is
    narrow, so no tool hides behind a QToolBar's » overflow menu."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._flow = FlowLayout(self)
        self._flow.setSpacing(2)
        self._buttons: dict[QAction, QToolButton] = {}
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def addToolAction(self, action: QAction) -> QToolButton:
        """Add ``action`` (also to ``actions()``) as a button in the row."""
        self.addAction(action)
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        self._flow.addWidget(button)
        self._buttons[action] = button
        return button

    def addSeparator(self) -> None:
        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.VLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        self._flow.addWidget(line)

    def widgetForAction(self, action: QAction) -> QToolButton | None:
        return self._buttons.get(action)


class ToolManager(QObject):
    """
    Manager for viewport tools.

    Manages tools for a single viewport, ensuring that only one tool is active at a time.
    """

    sigToolActivated = pyqtSignal(object)  # Emits the activated tool
    sigToolDeactivated = pyqtSignal(object)  # Emits the deactivated tool

    def __init__(
        self,
        viewport: ImageViewport,
        parent=None,
        excludeCategories: tuple[str, ...] = (),
        excludeTools: tuple[Type[ViewportTool], ...] = (),
    ):
        """``excludeCategories`` and ``excludeTools``: tool categories and
        tools this viewport has no use for."""
        super().__init__(parent)
        self.excludeCategories = excludeCategories
        self.excludeTools = excludeTools
        self.viewport = viewport
        self.viewport.installEventFilter(self)
        self.activeTool: Optional[ViewportTool] = None
        self.toolActions: dict[Type[ViewportTool], QAction] = {}
        self.toolRegistry = ToolRegistry()
        self.toolBar = self._createToolbar()

    def getToolbar(self):
        """Get the toolbar for this viewport."""
        return self.toolBar

    def activateTool(self, toolClass: Type[ViewportTool]):
        """Set the active tool and install only mouse event filters."""
        if self.activeTool:
            self.deactivateCurrentTool()

        self.activeTool = toolClass(self.viewport, parent=self)
        self.activeTool.activate()
        self.sigToolActivated.emit(self.activeTool)

    def select(self, toolClass: Type[ViewportTool]) -> None:
        """Turn a tool on as its toolbar button would (the button shows it)."""
        action = self.toolActions.get(toolClass)
        if action is not None:
            action.setChecked(True)
        self.activateTool(toolClass)

    def deactivateCurrentTool(self):
        """Deactivate the currently active tool."""
        if self.activeTool is None:
            return
        # Deactivate the tool
        self.activeTool.deactivate()
        self.sigToolDeactivated.emit(self.activeTool)
        self.activeTool = None

    def eventFilter(self, a0, a1):
        """
        This is specifically for forwarding KeyPress events to the active tool.
        Otherwise, the KeyPress events get consumed before reaching the imageItem.
        """
        event = a1
        if event.type() == QEvent.Type.KeyPress and self.activeTool is not None:
            return self.activeTool.onKeyEvent(KeyEvent.fromQt(event))
        return False

    def _createToolbar(self) -> WrappingToolBar:
        """Create a toolbar with actions for all available tools, in the
        order they were registered (Pixel Select, the first step of looking
        at an image, comes first)."""
        toolbar = WrappingToolBar()

        # One tool at a time, but the active one can be clicked again to turn
        # it off and go back to plain navigation.
        actionGroup = QActionGroup(toolbar)
        actionGroup.setExclusionPolicy(QActionGroup.ExclusionPolicy.ExclusiveOptional)

        # Get all registered tools from the registry

        # Add tools by category
        currentCategory = None

        categories = [
            c
            for c in self.toolRegistry.getCategories()
            if c not in self.excludeCategories
        ]

        for category in categories:
            # Get tools in this category
            tools = [
                tool
                for tool in self.toolRegistry.getToolsByCategory(category)
                if tool not in self.excludeTools
            ]
            if not tools:
                continue

            # Add separator between categories
            if currentCategory is not None:
                toolbar.addSeparator()

            currentCategory = category

            # Add each tool's action to the toolbar
            for toolClass in tools:
                action = toolClass.createAction(toolbar)
                action.triggered.connect(
                    lambda checked, tc=toolClass: self._onToolTriggered(tc, checked)
                )
                actionGroup.addAction(action)
                toolbar.addToolAction(action)
                self.toolActions[toolClass] = action

        return toolbar

    def _onToolTriggered(self, toolClass: Type[ViewportTool], checked: bool = True):
        """Activate the tool, or deactivate it when its button was toggled off."""
        if checked:
            self.activateTool(toolClass)
        else:
            self.deactivateCurrentTool()
