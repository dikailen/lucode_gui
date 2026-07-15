from __future__ import annotations

import os
import importlib.util

import pytest

HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
pytestmark = pytest.mark.skipif(not HAS_PYSIDE, reason="PySide6 is not installed")

if HAS_PYSIDE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QObject, QEvent, Qt  # noqa: E402
    from PySide6.QtTest import QTest  # noqa: E402
    from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QMenu, QMessageBox, QPushButton, QSplitter, QStackedWidget, QWidget  # noqa: E402

    from lucode.gui.chat_session import GuiChatSession  # noqa: E402
    from lucode.gui.main_window import MainWindow  # noqa: E402
    from lucode.gui.session_sidebar import SessionSidebar  # noqa: E402
    from lucode.gui.sidebar_data import load_default_skill_cards  # noqa: E402
    from lucode.gui.widgets import AnswerBlock, MessageBubble  # noqa: E402


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_main_window_uses_splitter_with_sidebar_and_chat_pane(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    splitter = window.findChild(QSplitter, "MainSplitter")
    sidebar = window.findChild(SessionSidebar, "SessionSidebar")
    chat_pane = window.findChild(QWidget, "ChatWorkspacePage")
    toolbar = window.findChild(QFrame, "ComposerToolbar")
    workspace_stack = window.findChild(QStackedWidget, "MainWorkspaceStack")

    assert splitter is not None
    assert splitter.count() == 2
    assert sidebar is not None
    assert chat_pane is not None
    assert toolbar is not None
    assert workspace_stack is not None
    assert window.session_title_label.text() == "新会话"
    assert window.scroll_area.parentWidget().objectName() == "ChatWorkspacePage"
    ancestor = window.input_box.parentWidget()
    while ancestor is not None and ancestor.objectName() != "ChatWorkspacePage":
        ancestor = ancestor.parentWidget()
    assert ancestor is not None


def test_sidebar_toggle_hides_and_restores_sidebar(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    sidebar = window.findChild(SessionSidebar, "SessionSidebar")
    toggle = window.findChild(QPushButton, "SidebarToggleButton")

    assert sidebar is not None
    assert toggle is not None
    assert sidebar.isVisible()

    toggle.click()
    app.processEvents()
    assert sidebar.isVisible()
    assert sidebar.property("collapsed") is True
    assert sidebar.maximumWidth() <= 76
    collapsed_sizes = window.main_splitter.sizes()
    assert collapsed_sizes[0] <= 76
    assert collapsed_sizes[1] > 0

    rail_toggle = window.findChild(QPushButton, "SidebarRailToggleButton")
    assert rail_toggle is not None
    assert rail_toggle.isVisible()

    rail_toggle.click()
    app.processEvents()
    assert sidebar.isVisible()
    assert sidebar.property("collapsed") is False
    assert sidebar.maximumWidth() == 294
    expanded_sizes = window.main_splitter.sizes()
    assert 286 <= expanded_sizes[0] <= 304


def test_workbench_shell_matches_concept_geometry(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    splitter = window.findChild(QSplitter, "MainSplitter")
    sidebar = window.findChild(SessionSidebar, "SessionSidebar")
    chat_header = window.findChild(QFrame, "ChatHeader")
    mode_host = window.findChild(QWidget, "TopModeHost")

    workspace_stack = window.findChild(QStackedWidget, "MainWorkspaceStack")
    settings_page = window.findChild(QFrame, "SettingsWorkspacePage")

    assert splitter is not None
    assert sidebar is not None
    assert settings_page is not None
    assert workspace_stack is not None
    assert chat_header is not None
    assert mode_host is None
    assert window.findChild(QLabel, "TopStatusChip") is None
    assert window.findChild(QPushButton, "TopSettingsButton") is None

    assert 286 <= sidebar.width() <= 304
    assert 60 <= chat_header.height() <= 68
    assert window.status.isHidden()

    window.session_sidebar.sidebar_settings_button.click()
    app.processEvents()

    sizes = splitter.sizes()
    assert len(sizes) == 2
    assert 286 <= sizes[0] <= 304
    assert workspace_stack.currentWidget() is settings_page
    assert window.session_title_label.text() == "设置"


def test_top_mode_area_is_removed_from_header(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    mode_host = window.findChild(QWidget, "TopModeHost")
    mode_chip = window.findChild(QLabel, "TopModeChip")
    top_mode_buttons = window.findChildren(QPushButton, "TopModeButton")

    assert mode_host is None
    assert mode_chip is None
    assert top_mode_buttons == []


def test_unified_mode_is_the_only_visible_mode_control(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.show()
    app.processEvents()

    mode_buttons = window.control_bar.findChildren(QPushButton, "SegButton")
    button_modes = [button.property("mode_id") for button in mode_buttons]

    assert window.findChild(QLabel, "TopModeChip") is None
    assert button_modes == ["auto"]
    assert all(mode not in button_modes for mode in ("solo", "serial", "full"))


def test_plugin_entry_switches_main_workspace_without_replacing_sidebar_sessions(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    stack = window.findChild(QStackedWidget, "MainWorkspaceStack")
    plugin_page = window.findChild(QFrame, "PluginWorkspacePage")
    plugin_button = window.session_sidebar.findChild(QPushButton, "SidebarTabPlugins")

    assert stack is not None
    assert plugin_page is not None
    assert plugin_button is not None
    assert stack.currentWidget() is window.chat_workspace_page

    plugin_button.click()
    app.processEvents()

    assert stack.currentWidget() is plugin_page
    assert window.session_title_label.text() == "插件"
    assert window.findChild(QLabel, "PluginWorkspaceTitle").text() == "插件"
    assert window.findChildren(QPushButton, "SkillCardButton")
    assert window.findChildren(QFrame, "McpStatusRow")
    assert window.session_sidebar.findChild(QPushButton, "SidebarTabPlugins").isChecked()


def test_chat_tab_returns_from_plugin_workspace_to_chat_workspace(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    stack = window.findChild(QStackedWidget, "MainWorkspaceStack")
    plugin_button = window.session_sidebar.findChild(QPushButton, "SidebarTabPlugins")
    chats_button = window.session_sidebar.findChild(QPushButton, "SidebarTabChats")

    plugin_button.click()
    app.processEvents()
    assert stack.currentWidget() is window.plugin_workspace_page

    chats_button.click()
    app.processEvents()
    assert stack.currentWidget() is window.chat_workspace_page
    assert chats_button.isChecked()


def test_chat_tab_returns_from_plugin_and_settings_without_delete_prompt(app, tmp_path, monkeypatch):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    prompts = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: prompts.append(True) or QMessageBox.No)

    stack = window.findChild(QStackedWidget, "MainWorkspaceStack")
    plugin_button = window.session_sidebar.findChild(QPushButton, "SidebarTabPlugins")
    chats_button = window.session_sidebar.findChild(QPushButton, "SidebarTabChats")

    plugin_button.click()
    app.processEvents()
    chats_button.click()
    app.processEvents()

    assert stack.currentWidget() is window.chat_workspace_page
    assert prompts == []

    window.session_sidebar.sidebar_settings_button.click()
    app.processEvents()
    chats_button.click()
    app.processEvents()

    assert stack.currentWidget() is window.chat_workspace_page
    assert prompts == []


def test_chat_tab_mouse_click_does_not_flash_popup_window(app, tmp_path, monkeypatch):
    class PopupWatcher(QObject):
        def __init__(self):
            super().__init__()
            self.seen: list[str] = []

        def eventFilter(self, obj, event):
            if event.type() == QEvent.Show and isinstance(obj, (QDialog, QMessageBox, QMenu)):
                self.seen.append(f"{obj.metaObject().className()}:{obj.objectName()}")
            return False

    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.No)

    watcher = PopupWatcher()
    app.installEventFilter(watcher)
    try:
        window.session_sidebar.sidebar_settings_button.click()
        app.processEvents()
        watcher.seen.clear()

        chats_button = window.session_sidebar.findChild(QPushButton, "SidebarTabChats")
        QTest.mouseClick(chats_button, Qt.LeftButton)
        app.processEvents()
        app.processEvents()
    finally:
        app.removeEventFilter(watcher)

    assert window.workspace_stack.currentWidget() is window.chat_workspace_page
    assert watcher.seen == []


def test_plugin_workspace_uses_compact_split_and_dense_rows(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    plugin_button = window.session_sidebar.findChild(QPushButton, "SidebarTabPlugins")
    plugin_button.click()
    app.processEvents()

    skills_rows = window.findChildren(QFrame, "SkillCardRow")
    mcp_rows = window.findChildren(QFrame, "McpStatusRow")
    skill_drop_hint = window.findChild(QLabel, "SkillDropHint")
    mcp_drop_hint = window.findChild(QLabel, "McpDropHint")
    split = window.findChild(QFrame, "PluginWorkspaceBody")
    panels = window.findChildren(QFrame, "PluginWorkspaceSection")
    skills_panel = next((panel for panel in panels if panel.property("pluginSectionRole") == "skills"), None)
    mcp_panel = next((panel for panel in panels if panel.property("pluginSectionRole") == "mcp"), None)

    assert skill_drop_hint is not None
    assert mcp_drop_hint is not None
    assert split is not None
    assert skills_panel is not None
    assert mcp_panel is not None
    assert skills_panel.acceptDrops()
    assert mcp_panel.acceptDrops()
    assert mcp_panel.maximumWidth() <= 360
    assert len(skills_rows) == 4
    assert len(mcp_rows) >= 2
    assert all(row.maximumHeight() <= 76 for row in skills_rows)
    assert all(row.maximumHeight() <= 68 for row in mcp_rows)


def test_plugin_workspace_skill_rows_use_status_chips_and_delete_pills(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    window.session_sidebar.findChild(QPushButton, "SidebarTabPlugins").click()
    app.processEvents()

    switches = window.findChildren(QPushButton, "SkillEnableSwitch")
    delete_buttons = window.plugin_workspace_page.findChildren(QPushButton, "SkillDeleteButton")
    chips = window.findChildren(QLabel, "SkillRowChips")
    core_ids = {
        card.id
        for card in load_default_skill_cards()
        if any(str(chip).strip() == "核心" for chip in card.chips)
    }
    delete_ids = {str(button.property("skill_id") or "") for button in delete_buttons}

    assert switches == []
    assert len(chips) >= 4
    assert any("已启用" in chip.text() for chip in chips)
    assert delete_ids == set()
    assert core_ids


def test_plugin_workspace_non_core_delete_pill_uninstalls_and_persists(app, tmp_path, monkeypatch):
    source = tmp_path / "drag-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: Drag Skill\ndescription: Drag installed skill.\n---\n\nBody\n",
        encoding="utf-8",
    )

    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.plugin_state_store.install_skill_from_path(source)
    window._rebuild_plugin_workspace()
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    window.session_sidebar.findChild(QPushButton, "SidebarTabPlugins").click()
    app.processEvents()

    core_ids = {
        card.id
        for card in load_default_skill_cards()
        if any(str(chip).strip() == "核心" for chip in card.chips)
    }
    before_ids = {
        str(button.property("skill_id") or "")
        for button in window.plugin_workspace_page.findChildren(QPushButton, "SkillCardButton")
    }
    delete_button = window.plugin_workspace_page.findChildren(QPushButton, "SkillDeleteButton")[0]
    skill_id = str(delete_button.property("skill_id") or "")

    assert skill_id
    assert skill_id not in core_ids

    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: QMessageBox.Yes)
    QTest.mouseClick(delete_button, Qt.LeftButton, Qt.NoModifier, delete_button.rect().center())
    app.processEvents()

    after_ids = {
        str(button.property("skill_id") or "")
        for button in window.plugin_workspace_page.findChildren(QPushButton, "SkillCardButton")
    }

    assert skill_id in window.removed_skill_ids
    assert after_ids == before_ids - {skill_id}
    assert core_ids.issubset(after_ids)
    assert window.plugin_workspace_page.findChildren(QPushButton, "SkillEnableSwitch") == []

    reloaded = MainWindow(workspace=tmp_path, chat_session=GuiChatSession(workspace=tmp_path))
    reloaded.resize(1600, 1000)
    reloaded.show()
    app.processEvents()
    reloaded.session_sidebar.findChild(QPushButton, "SidebarTabPlugins").click()
    app.processEvents()
    reloaded_ids = {
        str(button.property("skill_id") or "")
        for button in reloaded.plugin_workspace_page.findChildren(QPushButton, "SkillCardButton")
    }

    assert skill_id not in reloaded_ids
    assert core_ids.issubset(reloaded_ids)



def test_chat_rows_use_direct_output_canvas_without_tinted_stripes(app, tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    window = MainWindow(workspace=tmp_path, chat_session=session)
    window.resize(1600, 1000)
    window.show()
    app.processEvents()

    user_bubble = window.add_message("user", "hello")
    answer = window.add_answer_block("**direct** answer")
    app.processEvents()

    user_row = user_bubble.parentWidget()
    answer_row = answer.parentWidget()

    assert isinstance(user_bubble, MessageBubble)
    assert isinstance(answer, AnswerBlock)
    assert user_row is not None
    assert answer_row is not None
    assert user_row.objectName() == "UserMessageRow"
    assert answer_row.objectName() == "AssistantAnswerRow"
    assert answer_row.property("visualRole") == "assistant"
    assert answer.objectName() == "AnswerBlock"
    assert answer.findChild(QLabel, "AnswerText") is answer.content_label
