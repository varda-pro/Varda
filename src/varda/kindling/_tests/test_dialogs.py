import keyring
import pytest
from keyring.backends import fail
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QLabel, QMessageBox

from varda.kindling import ai_settings, kindle_dialog
from varda.kindling.ai_settings import KEYRING_SERVICE, AISettings
from varda.kindling.ai_settings_dialog import AISettingsDialog
from varda.kindling.docs import EXAMPLES
from varda.kindling.kindle_dialog import KindleDialog
from varda.kindling.providers import ProviderError
from varda.plugins.plugin_manager import VardaPluginManager

from .fakes import ScriptedProvider, call, replaying, tools


@pytest.fixture
def settings(tmp_path) -> AISettings:
    return AISettings(
        QSettings(str(tmp_path / "varda.ini"), QSettings.Format.IniFormat)
    )


def test_the_key_lives_in_the_keychain(settings, memoryKeyring, tmp_path):
    assert settings.apiKey() == ""
    settings.setApiKey("sk-ant-test")
    assert memoryKeyring.passwords[(KEYRING_SERVICE, "anthropic")] == "sk-ant-test"
    assert (
        "sk-ant-test" not in (tmp_path / "varda.ini").read_text()
        if (tmp_path / "varda.ini").exists()
        else True
    )
    settings.removeApiKey()
    settings.removeApiKey()  # removing twice is fine
    assert settings.apiKey() == ""


def test_kindling_needs_a_key(settings):
    with pytest.raises(ProviderError, match="Add an API key"):
        settings.createProvider()


def test_the_settings_dialog_saves(qtbot, settings):
    dialog = AISettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog.key.setText("  sk-ant-new  ")
    dialog.model.setCurrentText("claude-sonnet-5-5")
    dialog.budget.setValue(250_000)
    dialog.workspace.setText(" wrkspc_123 ")
    dialog._save()
    assert settings.apiKey() == "sk-ant-new"
    assert settings.workspaceId == "wrkspc_123"
    assert (
        settings.createProvider().client.default_headers["anthropic-workspace-id"]
        == "wrkspc_123"
    )
    assert (settings.model, settings.tokenBudget) == ("claude-sonnet-5-5", 250_000)
    reopened = AISettingsDialog(settings)
    qtbot.addWidget(reopened)
    assert (
        reopened.key.text() == ""
        and reopened.key.placeholderText() == "Saved in your keychain"
    )


def _exampleScript():
    files = {
        "plugin.toml": (EXAMPLES / "band-depth" / "plugin.toml")
        .read_text()
        .replace('name = "band-depth"', 'name = "kindled-depth"'),
        "plugin.py": (EXAMPLES / "band-depth" / "plugin.py").read_text(),
        "tests/test_plugin.py": (
            EXAMPLES / "band-depth" / "tests" / "test_plugin.py"
        ).read_text(),
    }
    writes = [
        call("write_file", f"w{i}", path=p, content=c)
        for i, (p, c) in enumerate(files.items())
    ]
    return [
        tools(*writes, text="Writing the plugin."),
        tools(call("run_checks")),
        tools(call("finish", summary="Maps band depth at 1.93 µm.")),
    ]


@pytest.fixture
def dialog(qtbot, tmp_path, settings, monkeypatch):
    monkeypatch.setattr(
        settings,
        "createProvider",
        lambda: ScriptedProvider(replaying(_exampleScript())),
    )
    manager = VardaPluginManager(pluginsFolder=tmp_path / "plugins")
    manager.loadInstalled()
    widget = KindleDialog(manager, [], settings, folder=tmp_path / "draft")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_kindle_build_review_and_enable(qtbot, dialog, tmp_path, monkeypatch):
    monkeypatch.setattr(kindle_dialog.QMessageBox, "information", lambda *a: None)
    dialog.description.setPlainText("band depth at 1.93 µm")
    dialog.startButton.click()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == 2, timeout=60_000)
    assert "ready to review" in dialog.headline.text()
    assert "Processing → Spectral Parameters → Band Depth" in dialog.location.text()
    assert dialog.enableButton.isEnabled()
    tabs = [dialog.details.tabText(i) for i in range(dialog.details.count())]
    assert tabs[0] == "Checks ✓" and "plugin.py" in tabs and tabs[-1] == "Log"
    dialog.enableButton.click()
    assert dialog.manager.record("kindled-depth").loaded
    assert not (tmp_path / "draft").exists()


def test_discarding_asks_then_removes_the_draft(qtbot, dialog, tmp_path, monkeypatch):
    dialog.description.setPlainText("band depth")
    dialog.startButton.click()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == 2, timeout=60_000)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No
    )
    dialog.reject()
    assert (tmp_path / "draft").exists()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    dialog.reject()
    assert not (tmp_path / "draft").exists()


def test_without_a_description_nothing_starts(dialog):
    dialog.startButton.click()
    assert dialog.pages.currentIndex() == 0


def test_each_provider_keeps_its_own_key_and_model(settings, memoryKeyring):
    settings.setApiKey("sk-ant-test")
    settings.model = "claude-sonnet-5-5"
    settings.provider = "openai"
    assert (settings.apiKey(), settings.model) == ("", "")
    settings.setApiKey("sk-openai")
    settings.model = "gpt-test"
    assert memoryKeyring.passwords[(KEYRING_SERVICE, "openai")] == "sk-openai"
    settings.provider = "anthropic"
    assert (settings.apiKey(), settings.model) == ("sk-ant-test", "claude-sonnet-5-5")


def test_a_local_server_needs_a_model_but_no_key(settings, memoryKeyring):
    settings.provider = "openai-compatible"
    assert not settings.isReady()
    settings.model = "qwen3:14b"
    assert settings.isReady()
    provider = settings.createProvider()
    assert str(provider.client.base_url).startswith("http://localhost:11434/v1")
    assert settings.recipient == "the server at http://localhost:11434/v1"


def test_openai_needs_a_key(settings, memoryKeyring):
    settings.provider = "openai"
    settings.model = "gpt-test"
    assert not settings.isReady()
    with pytest.raises(ProviderError, match="Add an API key"):
        settings.createProvider()


def test_the_settings_dialog_sets_up_a_local_server(qtbot, settings, memoryKeyring):
    dialog = AISettingsDialog(settings)
    qtbot.addWidget(dialog)
    assert not dialog.form.isRowVisible(1)  # no server row for Anthropic
    dialog.provider.setCurrentIndex(dialog.provider.findData("openai-compatible"))
    assert dialog.form.isRowVisible(1) and not dialog.form.isRowVisible(4)
    dialog.serverPreset.setCurrentIndex(2)
    dialog.serverPreset.activated.emit(2)  # LM Studio
    assert dialog.baseUrl.text() == "http://localhost:1234/v1"
    dialog.model.setCurrentText("qwen3-14b")
    dialog._save()
    assert settings.provider == "openai-compatible"
    assert (settings.baseUrl, settings.model) == (
        "http://localhost:1234/v1",
        "qwen3-14b",
    )
    assert settings.isReady()
    assert settings.modelFor("anthropic")  # Anthropic's choice is kept


def test_the_kindle_dialog_says_where_the_description_goes(qtbot, settings, tmp_path):
    settings.provider = "openai"
    manager = VardaPluginManager(tmp_path / "plugins")
    dialog = KindleDialog(manager, [], settings)
    qtbot.addWidget(dialog)
    texts = [label.text() for label in dialog.findChildren(QLabel)]
    assert any(t.startswith("Sent to OpenAI:") for t in texts)


def _dialogWith(script, qtbot, tmp_path, settings, monkeypatch):
    monkeypatch.setattr(
        settings, "createProvider", lambda: ScriptedProvider(replaying(script))
    )
    manager = VardaPluginManager(pluginsFolder=tmp_path / "plugins")
    manager.loadInstalled()
    widget = KindleDialog(manager, [], settings, folder=tmp_path / "draft")
    qtbot.addWidget(widget)
    widget.show()
    return widget


def test_an_existing_feature_is_shown_and_can_be_built_anyway(
    qtbot, tmp_path, settings, monkeypatch
):
    existing = tools(
        call(
            "point_to_existing",
            feature="Band Math",
            location="Processing → Spectral Parameters → Band Math",
            explanation="Write R2300 / R2200.",
        )
    )
    dialog = _dialogWith(
        [existing, *_exampleScript()], qtbot, tmp_path, settings, monkeypatch
    )
    dialog.description.setPlainText("a band ratio")
    dialog.startButton.click()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == 2, timeout=60_000)
    assert "already do this" in dialog.headline.text()
    assert "Band Math" in dialog.summary.text()
    assert dialog.buildAnywayButton.isVisible() and not dialog.enableButton.isEnabled()
    dialog.buildAnywayButton.click()
    qtbot.waitUntil(lambda: "ready to review" in dialog.headline.text(), timeout=60_000)
    assert not dialog.buildAnywayButton.isVisible() and dialog.enableButton.isEnabled()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes
    )
    dialog.reject()


def test_a_declined_request_closes_without_asking(
    qtbot, tmp_path, settings, monkeypatch
):
    script = [tools(call("decline", reason="Plugins can't redesign Varda."))]
    dialog = _dialogWith(script, qtbot, tmp_path, settings, monkeypatch)
    dialog.description.setPlainText("redesign the whole app")
    dialog.startButton.click()
    qtbot.waitUntil(lambda: dialog.pages.currentIndex() == 2, timeout=60_000)
    assert "beyond what a plugin can do" in dialog.headline.text()
    assert "Plugins can't redesign Varda." in dialog.summary.text()
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: asked.append(a))
    dialog.reject()
    assert asked == []  # nothing was written, so nothing to discard
    assert not dialog.isVisible()


@pytest.fixture
def noKeychain(monkeypatch):
    """keyring's fail backend, which Linux without a keychain service gets."""
    previous = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    monkeypatch.setattr(ai_settings, "_sessionKeys", {})
    yield
    keyring.set_keyring(previous)


def test_without_a_keychain_the_key_lasts_the_session(settings, noKeychain):
    assert not settings.hasKeychain
    settings.setApiKey("sk-ant-test")
    assert settings.apiKey() == "sk-ant-test"
    assert settings.isReady()
    settings.removeApiKey()
    settings.removeApiKey()
    assert settings.apiKey() == ""


def test_the_dialog_says_when_keys_cannot_be_kept(qtbot, settings, noKeychain):
    dialog = AISettingsDialog(settings)
    qtbot.addWidget(dialog)
    note = dialog.keyHelp.text()
    assert "No secure key store" in note and "gnome-keyring" in note
    assert "keyrings.alt" in note and "won't be saved between sessions" in note
    dialog.key.setText("sk-ant-test")
    dialog._save()
    assert settings.apiKey() == "sk-ant-test"
    assert "Kept this session" in AISettingsDialog(settings).key.placeholderText()
