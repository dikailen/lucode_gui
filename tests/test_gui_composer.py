from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton  # noqa: E402

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
    assert window.model_display_button.parentWidget() is toolbar
    assert window.action_button.parentWidget() is toolbar
    assert window.findChildren(QPushButton, "ComposerToolButton") == []
    assert window.findChildren(QPushButton, "SendButton") == []
    assert window.findChildren(QPushButton, "StopButton") == []


def test_composer_model_display_sits_before_single_round_action(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.settings.orchestrator_model_priority = ["deepseek-v4-pro"]
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
    assert "deepseek-v4-pro" in model_button.text()
    assert action_button.text() == "\u2191"
    assert action_button.property("running") is False
    assert top_status is None
    assert top_mode is None
    assert top_settings is None


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
