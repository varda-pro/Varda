"""Settings → AI Assistant: which AI builds plugins. API keys (one per
provider) live only in the operating system's keychain (``keyring``), never
in a settings file or a session; the provider, model, server address and
token limit are ordinary settings."""

from __future__ import annotations

import keyring
import keyring.errors
from keyring.backends import fail, null
from PyQt6.QtCore import QSettings

from varda.kindling.providers import (
    ANTHROPIC,
    ANTHROPIC_MODELS,
    COMPATIBLE,
    LOCAL_SERVERS,
    OPENAI,
    AnthropicProvider,
    OpenAIProvider,
    Provider,
    ProviderError,
)

KEYRING_SERVICE = "Varda AI Assistant"
PROVIDER_KEY = "ai/provider"
MODEL_KEY = "ai/model"  # Anthropic's (the first provider); others are per provider
TOKEN_BUDGET_KEY = "ai/tokenBudget"
WORKSPACE_KEY = "ai/workspaceId"
BASE_URL_KEY = "ai/openai-compatible/baseUrl"
DEFAULT_TOKEN_BUDGET = 400_000
# Keys entered where there is no keychain: kept for this session only
_sessionKeys: dict[str, str] = {}
NO_KEYCHAIN_NOTE = (
    "No secure key store was found on this computer, so the key won't be "
    "saved between sessions: it works until you quit Varda. On Linux, install "
    "gnome-keyring or KWallet (or the keyrings.alt Python package) to keep it."
)
PROVIDER_LABELS = {
    ANTHROPIC: "Anthropic (Claude)",
    OPENAI: "OpenAI",
    COMPATIBLE: "OpenAI-compatible server (Ollama, LM Studio, …)",
}


def makeProvider(
    provider: str, apiKey: str, model: str, baseUrl: str = "", workspaceId: str = ""
) -> Provider:
    """The provider these settings describe; raises ProviderError when one
    is missing."""
    if provider != COMPATIBLE and not apiKey:
        raise ProviderError("Add an API key in Plugins → AI Assistant Settings first.")
    if not model:
        raise ProviderError("Choose a model in Plugins → AI Assistant Settings first.")
    if provider == ANTHROPIC:
        return AnthropicProvider(apiKey, model, workspaceId)
    return openAIProvider(provider, apiKey, model, baseUrl)


def openAIProvider(
    provider: str, apiKey: str, model: str, baseUrl: str = ""
) -> OpenAIProvider:
    """OpenAI itself, or the OpenAI-compatible server at ``baseUrl``."""
    if provider == OPENAI:
        return OpenAIProvider(apiKey, model)
    if not baseUrl:
        raise ProviderError(
            "Enter the server's address in Plugins → AI Assistant Settings."
        )
    return OpenAIProvider(
        apiKey, model, baseUrl, official=False, name=f"The server at {baseUrl}"
    )


class AISettings:
    def __init__(self, settings: QSettings | None = None):
        self.settings = settings if settings is not None else QSettings()

    @property
    def provider(self) -> str:
        provider = str(self.settings.value(PROVIDER_KEY, ANTHROPIC))
        return provider if provider in PROVIDER_LABELS else ANTHROPIC

    @provider.setter
    def provider(self, provider: str) -> None:
        self.settings.setValue(PROVIDER_KEY, provider)

    @property
    def needsKey(self) -> bool:
        """Local servers usually don't ask for one."""
        return self.provider != COMPATIBLE

    def modelFor(self, provider: str) -> str:
        if provider == ANTHROPIC:
            return str(self.settings.value(MODEL_KEY, ANTHROPIC_MODELS[0]))
        return str(self.settings.value(f"ai/{provider}/model", ""))

    @property
    def model(self) -> str:
        return self.modelFor(self.provider)

    @model.setter
    def model(self, model: str) -> None:
        key = MODEL_KEY if self.provider == ANTHROPIC else f"ai/{self.provider}/model"
        self.settings.setValue(key, model)

    @property
    def baseUrl(self) -> str:
        """The OpenAI-compatible server's address (its /v1 endpoint)."""
        return str(self.settings.value(BASE_URL_KEY, LOCAL_SERVERS["Ollama"]))

    @baseUrl.setter
    def baseUrl(self, url: str) -> None:
        self.settings.setValue(BASE_URL_KEY, url)

    @property
    def recipient(self) -> str:
        """Who receives what Kindle a Feature sends, for the privacy note."""
        if self.provider == COMPATIBLE:
            return f"the server at {self.baseUrl}"
        return "Anthropic" if self.provider == ANTHROPIC else "OpenAI"

    @property
    def tokenBudget(self) -> int:
        return int(
            self.settings.value(TOKEN_BUDGET_KEY, DEFAULT_TOKEN_BUDGET, type=int)
        )

    @tokenBudget.setter
    def tokenBudget(self, tokens: int) -> None:
        self.settings.setValue(TOKEN_BUDGET_KEY, tokens)

    @property
    def workspaceId(self) -> str:
        """Only for an API key that isn't tied to a workspace (not a secret)."""
        return str(self.settings.value(WORKSPACE_KEY, ""))

    @workspaceId.setter
    def workspaceId(self, workspaceId: str) -> None:
        self.settings.setValue(WORKSPACE_KEY, workspaceId)

    @property
    def hasKeychain(self) -> bool:
        """Whether keyring found somewhere secure to keep keys."""
        return not isinstance(keyring.get_keyring(), (fail.Keyring, null.Keyring))

    def apiKey(self, provider: str = "") -> str:
        """The provider's saved key (the chosen provider's by default), or ""
        if there's none. Raises ProviderError when the keychain can't be used."""
        provider = provider or self.provider
        if provider in _sessionKeys:
            return _sessionKeys[provider]
        try:
            return keyring.get_password(KEYRING_SERVICE, provider) or ""
        except keyring.errors.NoKeyringError:
            return ""
        except keyring.errors.KeyringError as error:
            raise ProviderError(f"Varda couldn't read the keychain: {error}") from error

    def setApiKey(self, key: str, provider: str = "") -> None:
        provider = provider or self.provider
        _sessionKeys.pop(provider, None)
        try:
            keyring.set_password(KEYRING_SERVICE, provider, key)
        except keyring.errors.NoKeyringError:
            _sessionKeys[provider] = key  # works for this session only
        except keyring.errors.KeyringError as error:
            raise ProviderError(
                f"Varda couldn't save the key in the keychain: {error}"
            ) from error

    def removeApiKey(self, provider: str = "") -> None:
        provider = provider or self.provider
        _sessionKeys.pop(provider, None)
        try:
            keyring.delete_password(KEYRING_SERVICE, provider)
        except (keyring.errors.PasswordDeleteError, keyring.errors.NoKeyringError):
            pass  # there was none
        except keyring.errors.KeyringError as error:
            raise ProviderError(f"Varda couldn't remove the key: {error}") from error

    def isReady(self) -> bool:
        """Whether Kindle a Feature can start: a key where one is needed, and
        a model."""
        try:
            return bool(self.model) and (not self.needsKey or bool(self.apiKey()))
        except ProviderError:
            return False

    def createProvider(self) -> Provider:
        return makeProvider(
            self.provider, self.apiKey(), self.model, self.baseUrl, self.workspaceId
        )
