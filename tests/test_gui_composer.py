from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMenu, QPushButton  # noqa: E402

from lucode.gui.chat_session import GuiChatSession  # noqa: E402
from lucode.gui.main_window import ChatInput, MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_composer_shell_contains_toolbar_input_and_actions(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    composer = window.findChild(QFrame, "ComposerShell")
    toolbar = window.findChild(QFrame, "ComposerToolbar")
    input_row = window.findChild(QFrame, "ComposerInputRow")

    assert composer is not None
    assert toolbar is not None
    assert input_row is not None
    assert toolbar.parentWidget() is composer
    assert input_row.parentWidget() is composer
    assert composer.layout().indexOf(input_row) < composer.layout().indexOf(toolbar)
    assert 118 <= composer.height() <= 128
    assert window.input_box.parentWidget() is input_row
    terminal_button = window.findChild(QPushButton, "ComposerTerminalButton")
    browser_button = window.findChild(QPushButton, "ComposerBrowserButton")
    assert terminal_button is not None
    assert browser_button is not None
    assert terminal_button.parentWidget().objectName() == "ChatHeader"
    assert browser_button.parentWidget().objectName() == "ChatHeader"
    assert window.model_display_button.parentWidget() is toolbar
    assert window.action_button.parentWidget() is toolbar
    assert window.findChildren(QPushButton, "SendButton") == []
    assert window.findChildren(QPushButton, "StopButton") == []


def test_composer_terminal_and_browser_buttons_are_inline_static_entry_points(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    header = window.findChild(QFrame, "ChatHeader")
    title = window.findChild(QLabel, "SessionTitleLabel")
    toolbar = window.findChild(QFrame, "ComposerToolbar")
    terminal_button = window.findChild(QPushButton, "ComposerTerminalButton")
    browser_button = window.findChild(QPushButton, "ComposerBrowserButton")

    assert header is not None
    assert title is not None
    assert toolbar is not None
    assert terminal_button is not None
    assert browser_button is not None
    assert terminal_button.parentWidget() is header
    assert browser_button.parentWidget() is header
    assert header.layout().indexOf(title) < header.layout().indexOf(terminal_button)
    assert header.layout().indexOf(terminal_button) < header.layout().indexOf(browser_button)
    assert toolbar.layout().indexOf(terminal_button) < 0
    assert toolbar.layout().indexOf(browser_button) < 0
    assert terminal_button.toolTip() == "终端"
    assert browser_button.toolTip() == "浏览器"
    assert window.findChild(QFrame, "ToolDockPanel") is None
    assert window.findChild(QFrame, "StaticTerminalPanel") is None
    assert window.findChild(QFrame, "StaticBrowserPanel") is None


def test_composer_model_display_sits_before_single_round_action(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.list_configured_models = lambda: [("deepseek_v4_flash_model", "DeepSeek deepseek-v4-flash")]
    session.settings.orchestrator_model_priority = ["deepseek_v4_flash_model"]
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    toolbar = window.findChild(QFrame, "ComposerToolbar")
    model_button = window.findChild(QPushButton, "ComposerModelButton")
    action_button = window.findChild(QPushButton, "ComposerActionButton")
    top_status = window.findChild(QLabel, "TopStatusChip")
    top_mode = window.findChild(QLabel, "TopModeChip")
    top_settings = window.findChild(QPushButton, "TopSettingsButton")

    assert toolbar is not None
    assert model_button is not None
    assert action_button is not None
    assert toolbar.layout().indexOf(model_button) < toolbar.layout().indexOf(action_button)
    assert "使用模型" not in model_button.text()
    assert "DeepSeek" not in model_button.text()
    assert "_" not in model_button.text()
    assert "_model" not in model_button.text()
    assert "▾" in model_button.text()
    assert model_button.text().startswith("deepseek-v4-flash")
    assert action_button.text() == "\u2191"
    assert action_button.property("running") is False
    assert top_status is None
    assert top_mode is None
    assert top_settings is None


def test_composer_model_button_builds_lightweight_model_menu(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.list_configured_models = lambda: [
        ("deepseek_v4_flash_model", "DeepSeek deepseek-v4-flash"),
        ("mimo_v2_5_model", "MiMo mimo-v2.5"),
    ]
    session.settings.orchestrator_model_priority = ["deepseek_v4_flash_model"]
    changed = []
    session.set_model_for_role = lambda role, model_id: changed.append((role, model_id))
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    menu = window._build_model_menu()
    actions = menu.actions()

    assert isinstance(menu, QMenu)
    assert [action.data() for action in actions] == ["deepseek_v4_flash_model", "mimo_v2_5_model"]
    assert [action.text() for action in actions] == ["deepseek-v4-flash", "mimo-v2.5"]
    assert actions[0].isCheckable()
    assert actions[0].isChecked()
    actions[1].trigger()
    app.processEvents()

    assert changed == [("orchestrator", "mimo_v2_5_model")]


def test_chat_input_enter_submits_shift_enter_inserts_newline(app):
    input_box = ChatInput()
    submitted = []
    input_box.submit_requested.connect(lambda: submitted.append(True))

    input_box.setPlainText("hello")
    input_box.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert submitted == [True]
    assert input_box.toPlainText() == "hello"

    input_box.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.ShiftModifier, "\n"))
    assert submitted == [True]
    assert "\n" in input_box.toPlainText()


def test_composer_controls_follow_running_and_stopping_state(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    window.set_running(True)
    assert window.action_button.isEnabled()
    assert window.action_button.text() == "\u25a0"
    assert window.action_button.property("running") is True
    assert not window.input_box.isEnabled()

    window.set_running(False)
    assert window.action_button.isEnabled()
    assert window.action_button.text() == "\u2191"
    assert window.action_button.property("running") is False
    assert window.input_box.isEnabled()

    window.set_stopping()
    assert not window.action_button.isEnabled()
    assert window.action_button.text() == "\u25a0"
    assert not window.input_box.isEnabled()
