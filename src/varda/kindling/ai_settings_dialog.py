"""Plugins → AI Assistant Settings: the provider, API key, model and token
limit Kindle a Feature uses."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from varda.common.ui import MutedLabel
from varda.kindling.ai_settings import (
    PROVIDER_LABELS,
    NO_KEYCHAIN_NOTE,
    AISettings,
    makeProvider,
    openAIProvider,
)
from varda.kindling.providers import (
    ANTHROPIC,
    ANTHROPIC_MODELS,
    COMPATIBLE,
    LOCAL_SERVERS,
    OPENAI,
    Provider,
    ProviderError,
)
from varda.utilities.background import runInBackground

KEYS_URL = "https://console.anthropic.com/settings/keys"
OPENAI_KEYS_URL = "https://platform.openai.com/api-keys"
KEY_HELP = {
    ANTHROPIC: (
        f'Create a key at <a href="{KEYS_URL}">console.anthropic.com</a>. It\'s '
        "kept in your system's keychain, never in a file."
    ),
    OPENAI: (
        f'Create a key at <a href="{OPENAI_KEYS_URL}">platform.openai.com</a>. '
        "It's kept in your system's keychain, never in a file."
    ),
    COMPATIBLE: (
        "Local servers such as Ollama and LM Studio don't need a key; leave it "
        "empty. A hosted server's key is kept in your system's keychain."
    ),
}
KEY_PLACEHOLDER = {ANTHROPIC: "sk-ant-…", OPENAI: "sk-…", COMPATIBLE: "Optional"}
BUDGET_NOTE = {
    ANTHROPIC: (
        "Varda stops building when a feature (or each round of changes) has "
        "used this many tokens; text Anthropic has cached counts a tenth, as "
        "it's billed. Anthropic bills your account for what's used."
    ),
    OPENAI: (
        "Varda stops building when a feature (or each round of changes) has "
        "used this many tokens. OpenAI bills your account for what's used."
    ),
    COMPATIBLE: (
        "Varda stops building when a feature (or each round of changes) has "
        "used this many tokens. A local model costs nothing but time; give it "
        "a context window of 32k tokens or more, and a model that supports "
        "tool calls."
    ),
}


class AISettingsDialog(QDialog):
    def __init__(
        self, settings: AISettings | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle("AI Assistant Settings")
        self.setMinimumWidth(560)
        self.settings = settings if settings is not None else AISettings()

        self.provider = QComboBox()
        for provider, label in PROVIDER_LABELS.items():
            self.provider.addItem(label, provider)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.removeKey = QPushButton("Remove Key")
        self.removeKey.clicked.connect(self._removeKey)
        keyRow = QHBoxLayout()
        keyRow.addWidget(self.key, 1)
        keyRow.addWidget(self.removeKey)
        self.keyHelp = QLabel()
        self.keyHelp.setOpenExternalLinks(True)
        self.keyHelp.setWordWrap(True)
        self.workspace = QLineEdit(self.settings.workspaceId)
        self.workspace.setPlaceholderText(
            "Only for a key that isn't tied to a workspace"
        )
        self.baseUrl = QLineEdit(self.settings.baseUrl)
        self.baseUrl.setPlaceholderText("http://localhost:11434/v1")
        self.serverPreset = QComboBox()
        self.serverPreset.addItem("Preset…")
        for name, url in LOCAL_SERVERS.items():
            self.serverPreset.addItem(name, url)
        self.serverPreset.activated.connect(self._usePreset)
        serverRow = QHBoxLayout()
        serverRow.addWidget(self.baseUrl, 1)
        serverRow.addWidget(self.serverPreset)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.loadModels = QPushButton("Load Models")
        self.loadModels.setToolTip("List the models the server offers")
        self.loadModels.clicked.connect(self._loadModels)
        modelRow = QHBoxLayout()
        modelRow.addWidget(self.model, 1)
        modelRow.addWidget(self.loadModels)
        self.budget = QSpinBox()
        self.budget.setRange(50_000, 5_000_000)
        self.budget.setSingleStep(50_000)
        self.budget.setGroupSeparatorShown(True)
        self.budget.setSuffix(" tokens")
        self.budget.setValue(self.settings.tokenBudget)
        self.budgetNote = MutedLabel("")
        self.budgetNote.setWordWrap(True)

        self.form = form = QFormLayout()
        form.addRow("Provider:", self.provider)
        form.addRow("Server:", serverRow)
        form.addRow("API key:", keyRow)
        form.addRow("", self.keyHelp)
        form.addRow("Workspace ID:", self.workspace)
        form.addRow("Model:", modelRow)
        form.addRow("Limit per feature:", self.budget)
        form.addRow("", self.budgetNote)

        self.status = MutedLabel("")
        self.status.setWordWrap(True)
        test = QPushButton("Test Connection")
        test.clicked.connect(self._test)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.addButton(test, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(buttons)

        self.provider.setCurrentIndex(self.provider.findData(self.settings.provider))
        self.provider.currentIndexChanged.connect(lambda _: self._showProvider())
        self._showProvider()

    def chosenProvider(self) -> str:
        return self.provider.currentData()

    def _showProvider(self) -> None:
        """Show the chosen provider's rows and its saved model and key."""
        provider = self.chosenProvider()
        model = self.settings.modelFor(provider)
        self.form.setRowVisible(1, provider == COMPATIBLE)
        self.form.setRowVisible(4, provider == ANTHROPIC)
        self.loadModels.setVisible(provider != ANTHROPIC)
        self.keyHelp.setText(
            KEY_HELP[provider]
            if self.settings.hasKeychain
            else f"{KEY_HELP[provider]}<br><b>{NO_KEYCHAIN_NOTE}</b>"
        )
        self.budgetNote.setText(BUDGET_NOTE[provider])
        self.model.clear()
        if provider == ANTHROPIC:
            self.model.addItems(ANTHROPIC_MODELS)
        self.model.setCurrentText(model)
        modelEdit = self.model.lineEdit()
        assert modelEdit is not None  # the combo box is editable
        modelEdit.setPlaceholderText("e.g. qwen3:14b" if provider == COMPATIBLE else "")
        self.status.clear()
        self._showSavedKey()

    def _usePreset(self, index: int) -> None:
        url = self.serverPreset.itemData(index)
        if url:
            self.baseUrl.setText(url)
        self.serverPreset.setCurrentIndex(0)

    def _savedKey(self) -> str:
        return self.settings.apiKey(self.chosenProvider())

    def _showSavedKey(self) -> None:
        try:
            saved = bool(self._savedKey())
        except ProviderError as error:
            saved = False
            self.status.setText(str(error))
        self.key.clear()
        self.key.setPlaceholderText(
            (
                "Saved in your keychain"
                if self.settings.hasKeychain
                else "Kept this session"
            )
            if saved
            else KEY_PLACEHOLDER[self.chosenProvider()]
        )
        self.removeKey.setEnabled(saved)

    def _keyToUse(self) -> str:
        return self.key.text().strip() or self._savedKey()

    def _providerFromFields(self) -> Provider:
        return makeProvider(
            self.chosenProvider(),
            self._keyToUse(),
            self.model.currentText().strip(),
            self.baseUrl.text().strip(),
            self.workspace.text().strip(),
        )

    def _test(self) -> None:
        try:
            provider = self._providerFromFields()
        except ProviderError as error:
            self.status.setText(str(error))
            return
        self.status.setText("Testing…")
        runInBackground(
            provider.test,
            self.status.setText,
            lambda error: self.status.setText(str(error)),
            description="Testing the AI connection",
        )

    def _loadModels(self) -> None:
        provider = self.chosenProvider()
        try:
            key = self._keyToUse()
        except ProviderError as error:
            self.status.setText(str(error))
            return
        if provider == OPENAI and not key:
            self.status.setText("Enter an API key first.")
            return
        try:
            client = openAIProvider(provider, key, "", self.baseUrl.text().strip())
        except ProviderError as error:
            self.status.setText(str(error))
            return
        self.status.setText("Loading the models…")
        runInBackground(
            client.listModels,
            self._showModels,
            lambda error: self.status.setText(str(error)),
            description="Listing the AI models",
        )

    def _showModels(self, models: list[str]) -> None:
        current = self.model.currentText()
        self.model.clear()
        self.model.addItems(models)
        self.model.setCurrentText(current or (models[0] if models else ""))
        self.status.setText(
            f"{len(models)} models available."
            if models
            else "The server lists no models."
        )

    def _removeKey(self) -> None:
        try:
            self.settings.removeApiKey(self.chosenProvider())
            self.status.setText("The key was removed from your keychain.")
        except ProviderError as error:
            self.status.setText(str(error))
        self._showSavedKey()

    def _save(self) -> None:
        self.settings.provider = self.chosenProvider()
        try:
            if self.key.text().strip():
                self.settings.setApiKey(self.key.text().strip())
        except ProviderError as error:
            self.status.setText(str(error))
            return
        self.settings.model = self.model.currentText().strip()
        self.settings.workspaceId = self.workspace.text().strip()
        self.settings.baseUrl = self.baseUrl.text().strip()
        self.settings.tokenBudget = self.budget.value()
        self.accept()
