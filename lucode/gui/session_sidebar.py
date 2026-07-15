from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QPoint, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from lucode.gui.i18n import Translator, normalize_language
from lucode.gui.sidebar_data import (
    McpRow,
    SkillCard,
    load_default_mcp_rows,
    load_default_skill_cards,
)


class SessionSidebar(QFrame):
    """Workbench sidebar for conversations, skills, and MCP status."""

    new_session_requested = Signal()
    chats_requested = Signal()
    session_selected = Signal(str)
    session_deleted = Signal(str)
    settings_requested = Signal()
    plugins_requested = Signal()
    collapse_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SessionSidebar")
        self.setMinimumWidth(294)
        self.setMaximumWidth(294)
        self.setProperty("collapsed", False)
        self.setProperty("activeTab", "chats")
        self.setProperty("transitioning", False)
        self._session_store = None
        self._selected_session_id = ""
        self._enabled = True
        self._active_tab = "chats"
        self._collapsed = False
        self._activity_session_id = ""
        self._activity_state = ""
        self._language = 'zh'
        self._t = Translator(self._language)
        self._pending_delete_session_id = ""
        self._items_by_session_id: dict[str, Any] = {}
        self._skill_cards = load_default_skill_cards()
        self._mcp_rows = load_default_mcp_rows()
        self._pending_tab = ""
        self._pending_session_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 28, 12, 18)
        layout.setSpacing(18)

        self.icon_rail = QFrame()
        self.icon_rail.setObjectName("SidebarIconRail")
        icon_layout = QVBoxLayout(self.icon_rail)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon_layout.setSpacing(8)
        self.icon_logo = QLabel("L")
        self.icon_logo.setObjectName("SidebarRailLogo")
        self.icon_logo.setAlignment(Qt.AlignCenter)
        icon_layout.addWidget(self.icon_logo)
        self.rail_chats_button = self._make_rail_button("聊", "chats", "SidebarRailChats")
        self.rail_plugins_button = self._make_rail_button("插", "plugins", "SidebarRailPlugins")
        for button in (self.rail_chats_button, self.rail_plugins_button):
            icon_layout.addWidget(button)
        icon_layout.addStretch(1)
        self.rail_settings_button = QPushButton("⚙")
        self.rail_settings_button.setObjectName("SidebarRailSettingsButton")
        self.rail_settings_button.clicked.connect(self.settings_requested.emit)
        icon_layout.addWidget(self.rail_settings_button)
        self.rail_toggle_button = QPushButton(">")
        self.rail_toggle_button.setObjectName("SidebarRailToggleButton")
        self.rail_toggle_button.clicked.connect(self.collapse_requested.emit)
        icon_layout.addWidget(self.rail_toggle_button)
        self.icon_rail.hide()
        layout.addWidget(self.icon_rail, 1)

        self.full_content = QFrame()
        self.full_content.setObjectName("SidebarFullContent")
        full_layout = QVBoxLayout(self.full_content)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(14)
        layout.addWidget(self.full_content, 1)

        header = QHBoxLayout()
        title = QLabel("")
        title.setObjectName("SidebarTitle")
        title.hide()
        header.addWidget(title)
        header.addStretch(1)
        full_layout.addLayout(header)

        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.nav = QFrame()
        self.nav.setObjectName("SidebarNav")
        nav_layout = QVBoxLayout(self.nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)
        self.chats_tab = self._make_tab_button(self._t('sidebar.chats'), "chats", "SidebarTabChats")
        self.plugins_tab = self._make_tab_button(self._t('sidebar.plugins'), "plugins", "SidebarTabPlugins")
        for button in (self.chats_tab, self.plugins_tab):
            nav_layout.addWidget(button)
            self.tab_group.addButton(button)
        self.chats_tab.setChecked(True)
        full_layout.addWidget(self.nav)

        self.new_session_button = QPushButton(self._t('sidebar.new_chat'))
        self.new_session_button.setObjectName("SidebarNewSessionButton")
        self.new_session_button.clicked.connect(self.new_session_requested.emit)
        full_layout.addWidget(self.new_session_button)

        self.search_box = QLineEdit()
        self.search_box.setObjectName("SessionSearchBox")
        self.search_box.setPlaceholderText(self._t('sidebar.search'))
        self.search_box.textChanged.connect(lambda _text: self.refresh())
        full_layout.addWidget(self.search_box)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("SessionListScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        full_layout.addWidget(self.scroll, 1)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.scroll.setWidget(self.list_host)

        self.utility_bar = QFrame()
        self.utility_bar.setObjectName("SidebarUtilityBar")
        utility_layout = QHBoxLayout(self.utility_bar)
        utility_layout.setContentsMargins(0, 0, 0, 0)
        utility_layout.setSpacing(8)
        self.sidebar_settings_button = QPushButton(self._t('control.settings_tip'))
        self.sidebar_settings_button.setObjectName("SidebarSettingsButton")
        self.sidebar_settings_button.setToolTip(self._t('control.settings_tip'))
        self.sidebar_settings_button.setProperty("withLabel", True)
        self.sidebar_settings_button.clicked.connect(self.settings_requested.emit)
        utility_layout.addWidget(self.sidebar_settings_button)
        utility_layout.addStretch(1)
        self.sidebar_toggle_button = QPushButton("<")
        self.sidebar_toggle_button.setObjectName("SidebarToggleButton")
        self.sidebar_toggle_button.setToolTip(self._t('main.sidebar.toggle_tip'))
        self.sidebar_toggle_button.clicked.connect(self.collapse_requested.emit)
        utility_layout.addWidget(self.sidebar_toggle_button)
        full_layout.addWidget(self.utility_bar)
        self.sidebar_toggle_button.setVisible(True)

        self.empty_label = QLabel(self._t('sidebar.empty'))
        self.empty_label.setObjectName("SidebarEmpty")
        self.empty_label.setWordWrap(True)
        self.list_layout.addWidget(self.empty_label)
        self.list_layout.addStretch(1)
        self._sync_rail_buttons()

    def set_session_store(self, session_store) -> None:
        self._session_store = session_store

    def set_language(self, language: str) -> None:
        self._language = normalize_language(language)
        self._t = Translator(self._language)
        self.new_session_button.setText(self._t('sidebar.new_chat'))
        self.chats_tab.setText(self._t('sidebar.chats'))
        self.plugins_tab.setText(self._t('sidebar.plugins'))
        self.search_box.setPlaceholderText(self._t('sidebar.search'))
        self.sidebar_settings_button.setText(self._t('control.settings_tip'))
        self.sidebar_settings_button.setToolTip(self._t('control.settings_tip'))
        self.sidebar_toggle_button.setToolTip(self._t('main.sidebar.toggle_tip'))
        self.rail_settings_button.setToolTip(self._t('control.settings_tip'))
        self.rail_toggle_button.setToolTip(self._t('main.sidebar.toggle_tip'))
        self._sync_rail_buttons()
        self._sync_delete_buttons()
        self.refresh()

    def refresh(self) -> None:
        self._refresh_session_cache()
        query = self.search_box.text().strip()
        items = []
        if self._session_store is not None:
            try:
                if query and hasattr(self._session_store, "search"):
                    items = list(self._session_store.search(query, limit=50))
                elif hasattr(self._session_store, "list_items"):
                    items = list(self._session_store.list_items(limit=50))
            except Exception:
                items = []
        self._render_items(items)

    def select_session(self, session_id: str) -> None:
        self._selected_session_id = str(session_id or "")
        if self._active_tab == "chats":
            self.refresh()

    def set_active_tab(self, tab_id: str) -> None:
        normalized = str(tab_id or "").strip().lower()
        if normalized not in {"chats", "plugins", "settings"}:
            normalized = "chats"
        self._active_tab = normalized
        self.setProperty("activeTab", normalized)
        self.tab_group.setExclusive(False)
        self.chats_tab.setChecked(normalized == "chats")
        self.plugins_tab.setChecked(normalized == "plugins")
        self.tab_group.setExclusive(True)
        self._sync_rail_buttons()
        self._apply_enabled_state()

    def session_title(self, session_id: str) -> str:
        item = self._items_by_session_id.get(str(session_id or ""))
        return _item_title(item) if item is not None else ""

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._apply_enabled_state()

    def set_session_activity(self, session_id: str, state: str = "") -> None:
        self._activity_session_id = str(session_id or "").strip()
        self._activity_state = str(state or "").strip().lower()
        if self._active_tab == "chats":
            self.refresh()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = bool(collapsed)
        if self._collapsed:
            self.setMinimumWidth(64)
            self.setMaximumWidth(64)
            self.layout().setContentsMargins(12, 18, 12, 18)
        else:
            self.setMinimumWidth(294)
            self.setMaximumWidth(294)
            self.layout().setContentsMargins(20, 28, 12, 18)
        self.setProperty("collapsed", self._collapsed)
        self.style().unpolish(self)
        self.style().polish(self)
        self.full_content.setVisible(not self._collapsed)
        self.icon_rail.setVisible(self._collapsed)
        self._sync_rail_buttons()
        self._apply_enabled_state()

    def _make_tab_button(self, text: str, tab_id: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCheckable(True)
        button.setProperty("sidebarNavItem", True)
        button.setMinimumHeight(34)
        button.setMaximumHeight(40)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        button.clicked.connect(lambda _checked=False, value=tab_id: self._switch_tab(value))
        return button

    def _make_rail_button(self, text: str, tab_id: str, object_name: str) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setCheckable(True)
        button.setToolTip({"chats": self._t('sidebar.chats'), "skills": self._t('sidebar.skills'), "mcp": self._t('sidebar.mcp')}.get(tab_id, tab_id))
        button.clicked.connect(lambda _checked=False, value=tab_id: self._switch_tab(value))
        return button

    def _switch_tab(self, tab_id: str) -> None:
        if tab_id not in {"chats", "plugins"}:
            return
        self._pending_delete_session_id = ""
        self.setProperty("transitioning", True)
        self.set_active_tab(tab_id)
        self._pending_tab = tab_id
        QTimer.singleShot(0, self._finish_tab_switch)

    def _finish_tab_switch(self) -> None:
        tab_id = self._pending_tab
        self._pending_tab = ""
        if tab_id == "plugins":
            self.plugins_requested.emit()
        elif tab_id == "chats":
            self.chats_requested.emit()
        self.setProperty("transitioning", False)
        self._apply_enabled_state()

    def _apply_enabled_state(self) -> None:
        self.new_session_button.setEnabled(self._enabled)
        self.search_box.setEnabled(self._enabled)
        self.sidebar_settings_button.setEnabled(self._enabled)
        self.sidebar_toggle_button.setEnabled(self._enabled)
        self.rail_settings_button.setEnabled(self._enabled)
        self.rail_toggle_button.setEnabled(self._enabled)
        for button in (self.chats_tab, self.plugins_tab):
            button.setEnabled(self._enabled)
        for button in (self.rail_chats_button, self.rail_plugins_button):
            button.setEnabled(self._enabled)
        for button in self.findChildren(QPushButton, "SessionRowButton"):
            button.setEnabled(self._enabled)
        delete_enabled = self._enabled and self._active_tab == "chats"
        for button in self.findChildren(QPushButton, "SessionDeleteButton"):
            button.setEnabled(delete_enabled)
        self._sync_delete_buttons()
        for button in self.findChildren(QPushButton, "SkillCardButton"):
            button.setEnabled(self._enabled)
        for row in self.findChildren(QFrame, "SessionRow"):
            row.setEnabled(self._enabled)

    def _sync_delete_buttons(self) -> None:
        for button in self.findChildren(QPushButton, "SessionDeleteButton"):
            session_id = str(button.property("session_id") or "")
            confirming = bool(session_id and session_id == self._pending_delete_session_id)
            button.setText(self._t('sidebar.delete_confirm') if confirming else self._t('sidebar.delete'))
            button.setToolTip(self._t('sidebar.delete_prompt') if confirming else self._t('sidebar.delete'))
            button.setProperty("confirming", confirming)
            style = button.style()
            style.unpolish(button)
            style.polish(button)
            button.update()

    def _sync_rail_buttons(self) -> None:
        self.rail_chats_button.setChecked(self._active_tab == "chats")
        self.plugins_tab.setChecked(self._active_tab == "plugins")
        self.rail_plugins_button.setChecked(self._active_tab == "plugins")
        self.sidebar_toggle_button.setText(">" if self._collapsed else "<")
        self.rail_toggle_button.setText(">" if self._collapsed else "<")

    def _clear_list_layout(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is None:
                continue
            widget.hide()
            if widget is self.empty_label:
                continue
            # Remove stale rows from the QObject tree before delayed Qt destruction.
            widget.setParent(None)
            widget.deleteLater()

    def _refresh_session_cache(self) -> None:
        if self._session_store is None or not hasattr(self._session_store, "list_items"):
            return
        try:
            items = list(self._session_store.list_items(limit=50))
        except Exception:
            return
        self._items_by_session_id = {
            _item_session_id(item): item for item in items if _item_session_id(item)
        }

    def _render_items(self, items: list[Any]) -> None:
        self._clear_list_layout()
        self._items_by_session_id = {
            _item_session_id(item): item for item in items if _item_session_id(item)
        }
        if not items:
            self.empty_label.setText(self._t('sidebar.empty'))
            self.empty_label.show()
            self.list_layout.addWidget(self.empty_label)
            self.list_layout.addStretch(1)
            self._apply_enabled_state()
            return
        self.empty_label.hide()
        for item in items:
            session_id = _item_session_id(item)
            activity_state = self._activity_state if session_id and session_id == self._activity_session_id else ""
            row = _SessionRow(
                item,
                selected=session_id == self._selected_session_id,
                language=self._language,
                activity_state=activity_state,
            )
            row.session_selected.connect(self._on_session_selected)
            row.delete_requested.connect(self._on_delete_requested)
            self.list_layout.addWidget(row)
        self.list_layout.addStretch(1)
        self._apply_enabled_state()

    def _on_session_selected(self, session_id: str) -> None:
        self._pending_delete_session_id = ""
        self._sync_delete_buttons()
        self._selected_session_id = str(session_id or "")
        self._pending_session_id = self._selected_session_id
        self.setProperty("transitioning", True)
        self._apply_enabled_state()
        QTimer.singleShot(0, self._finish_session_selection)

    def _finish_session_selection(self) -> None:
        session_id = self._pending_session_id
        self._pending_session_id = ""
        if session_id:
            self.session_selected.emit(session_id)
        self.setProperty("transitioning", False)
        self._apply_enabled_state()

    def _on_delete_requested(self, session_id: str) -> None:
        if self.property("transitioning") is True or self._active_tab != "chats":
            return
        if not session_id or self._session_store is None:
            return
        if self._pending_delete_session_id != session_id:
            self._pending_delete_session_id = session_id
            self._sync_delete_buttons()
            return
        self._pending_delete_session_id = ""
        try:
            self._session_store.delete(session_id)
        except Exception:
            return
        if self._selected_session_id == session_id:
            self._selected_session_id = ""
        self.session_deleted.emit(session_id)
        self.refresh()


class _SessionRow(QFrame):
    session_selected = Signal(str)
    delete_requested = Signal(str)

    def __init__(
        self,
        item,
        *,
        selected: bool = False,
        language: str = 'zh',
        activity_state: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("SessionRow")
        self.session_id = _item_session_id(item)
        self.setProperty("selected", bool(selected))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(34)
        self.setMaximumHeight(40)
        self.setCursor(Qt.PointingHandCursor)
        self._press_select_pending = False
        self.style().unpolish(self)
        self.style().polish(self)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 6, 3)
        layout.setSpacing(8)

        self._t = Translator(language)
        title = _item_title(item, language=language)
        meta = _item_meta(item, language=language)

        text_host = QVBoxLayout()
        text_host.setContentsMargins(0, 0, 0, 0)
        text_host.setSpacing(1)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SessionRowTitle")
        self.title_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title_label.setMinimumWidth(0)
        self.title_label.setMaximumWidth(188)
        self.title_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.title_label.setToolTip(_item_full_title(item, language=language))
        text_host.addWidget(self.title_label)
        self.meta_label = QLabel(meta)
        self.meta_label.setObjectName("SessionRowMeta")
        self.meta_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.meta_label.setMinimumWidth(0)
        self.meta_label.setMaximumWidth(188)
        self.meta_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        text_host.addWidget(self.meta_label)
        layout.addLayout(text_host, 1)

        self.row_button = QPushButton(title, self)
        self.row_button.setObjectName("SessionRowButton")
        self.row_button.setProperty("session_id", self.session_id)
        self.row_button.clicked.connect(lambda: self.session_selected.emit(self.session_id))
        self.row_button.hide()

        self.activity_dot = QLabel("\u25cf", self)
        self.activity_dot.setObjectName("SessionActivityDot")
        self.activity_dot.setProperty("session_id", self.session_id)
        self.activity_dot.setProperty("state", str(activity_state or ""))
        self.activity_dot.setToolTip(self._t('main.status.running') if activity_state else "")
        layout.addWidget(self.activity_dot)
        self.activity_dot.setVisible(bool(activity_state))

        self.delete_button = QPushButton("🗑", self)
        self.delete_button.setObjectName("SessionDeleteButton")
        self.delete_button.setProperty("session_id", self.session_id)
        self.delete_button.setToolTip(self._t('sidebar.delete'))
        self.delete_button.setText(self._t('sidebar.delete'))
        self.delete_button.setMaximumWidth(68)
        self.delete_button.setMinimumWidth(52)
        self.delete_button.setMinimumHeight(26)
        self.delete_button.setMaximumHeight(26)
        self.delete_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.delete_button.clicked.connect(lambda: self.delete_requested.emit(self.session_id))
        layout.addWidget(self.delete_button)

    def mousePressEvent(self, event) -> None:
        if self.delete_button.geometry().contains(event.position().toPoint()):
            self._press_select_pending = False
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            self._press_select_pending = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._press_select_pending:
            self._press_select_pending = False
            if self.rect().contains(event.position().toPoint()) and not self.delete_button.geometry().contains(event.position().toPoint()):
                self.session_selected.emit(self.session_id)
                event.accept()
                return
        self._press_select_pending = False
        super().mouseReleaseEvent(event)


class _SkillCardRow(QFrame):
    delete_requested = Signal(str)

    def __init__(self, card: SkillCard, *, deletable: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("SkillCardRow")
        self.setMinimumHeight(64)
        self.setMaximumHeight(74)
        self.setProperty("listRow", True)
        self.setProperty("skill_id", card.id)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        title = QLabel(card.title)
        title.setObjectName("SkillRowTitle")
        title.setWordWrap(True)
        top.addWidget(title, 1)

        chips = QLabel("  ".join(str(chip) for chip in card.chips))
        chips.setObjectName("SkillRowChips")
        chips.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(chips)
        if deletable:
            delete_button = QPushButton("删除")
            delete_button.setObjectName("SkillDeleteButton")
            delete_button.setProperty("skill_id", card.id)
            delete_button.setCursor(Qt.PointingHandCursor)
            delete_button.setFocusPolicy(Qt.NoFocus)
            delete_button.clicked.connect(lambda _checked=False, value=card.id: self.delete_requested.emit(value))
            top.addWidget(delete_button)
        layout.addLayout(top)

        description = QLabel(card.description)
        description.setObjectName("SkillRowDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.card_button = QPushButton(card.title, self)
        self.card_button.setObjectName("SkillCardButton")
        self.card_button.setProperty("skill_id", card.id)
        self.card_button.hide()


class _McpStatusRow(QFrame):
    def __init__(self, row: McpRow, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("McpStatusRow")
        self.setMinimumHeight(56)
        self.setMaximumHeight(66)
        self.setProperty("mcp_id", row.id)
        self.setProperty("status", row.status)
        self.setProperty("listRow", True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)

        text_host = QVBoxLayout()
        text_host.setContentsMargins(0, 0, 0, 0)
        text_host.setSpacing(2)
        name = QLabel(row.title)
        name.setObjectName("McpName")
        name.setWordWrap(True)
        text_host.addWidget(name)
        if row.detail:
            detail = QLabel(row.detail)
            detail.setObjectName("McpDetail")
            detail.setWordWrap(True)
            text_host.addWidget(detail)
        layout.addLayout(text_host, 1)

        status = QLabel(row.status)
        status.setObjectName("McpStatus")
        layout.addWidget(status, 0, Qt.AlignRight | Qt.AlignVCenter)


def _item_session_id(item) -> str:
    if isinstance(item, dict):
        return str(item.get("session_id") or item.get("history_id") or "")
    return str(getattr(item, "session_id", "") or getattr(item, "history_id", "") or "")


def _item_title(item, *, language: str = 'zh') -> str:
    text = _item_full_title(item, language=language)
    return text[:34] + "..." if len(text) > 36 else text


def _item_full_title(item, *, language: str = 'zh') -> str:
    if isinstance(item, dict):
        title = item.get("title") or item.get("last_user") or item.get("session_id") or ""
    else:
        title = getattr(item, "title", "") or getattr(item, "last_user", "") or getattr(item, "session_id", "")
    text = str(title or "").replace("\n", " ").strip()
    return text or Translator(language)('sidebar.untitled')


def _item_meta(item, *, language: str = 'zh') -> str:
    if isinstance(item, dict):
        updated = item.get("updated_at") or ""
        count = item.get("message_count") or 0
    else:
        updated = getattr(item, "updated_at", "") or ""
        count = getattr(item, "message_count", 0) or 0
    parts = []
    t = Translator(language)
    relative = _relative_time(updated, language=language)
    if relative:
        parts.append(relative)
    if count:
        parts.append(f"{count} {t('sidebar.messages')}")
    return " - ".join(parts)


def _relative_time(value: str, *, language: str = 'zh') -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        updated = datetime.fromisoformat(text.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        seconds = max(0, int((now - updated).total_seconds()))
    except ValueError:
        return text[:10]
    if seconds < 60:
        return Translator(language)('sidebar.just_now')
    minutes = seconds // 60
    if minutes < 60:
        return Translator(language)('sidebar.minutes_ago', count=minutes)
    hours = minutes // 60
    if hours < 24:
        return Translator(language)('sidebar.hours_ago', count=hours)
    days = hours // 24
    if days < 30:
        return Translator(language)('sidebar.days_ago', count=days)
    return updated.date().isoformat()
