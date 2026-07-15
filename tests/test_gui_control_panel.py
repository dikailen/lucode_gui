from __future__ import annotations

from lucode.gui.chat_session import GuiChatSession
from lucode.gui.control_panel import (
    compact_execution_mode_label,
    execution_mode_label,
    execution_mode_options,
    privacy_mode_options,
    query_refiner_available_for_mode,
    role_options,
    roles_for_mode,
)
from lucode.gui.i18n import Translator


def test_execution_mode_options_cover_known_modes():
    keys = [key for key, _label in execution_mode_options()]
    assert keys == ["auto"]
    assert all(label for _key, label in execution_mode_options())


def test_auto_mode_labels_and_tooltips_are_unified():
    assert execution_mode_label("solo", "zh") == "\u81ea\u52a8\u6267\u884c"
    assert execution_mode_label("full", "en") == "Auto execution"
    assert compact_execution_mode_label("serial", "zh") == "\u81ea\u52a8"
    assert compact_execution_mode_label("full", "en") == "Auto"
    assert Translator("zh")("control.mode_tip.auto").startswith("\u7edf\u4e00 Agent Loop")
    assert Translator("en")("control.mode_tip.auto").startswith("Unified Agent Loop")

def test_privacy_mode_options_cover_known_modes():
    keys = [key for key, _label in privacy_mode_options()]
    assert keys == ["offline", "local_first", "cloud_allowed"]


def test_role_options_follow_role_order():
    keys = [key for key, _label in role_options()]
    assert keys == ["query_refiner", "orchestrator", "executor", "final_synthesizer"]


def test_auto_and_legacy_modes_use_unified_loop_roles():
    for mode in ("auto", "solo", "serial", "full"):
        rows = roles_for_mode(mode)
        roles = [r for r, _u in rows]
        assert roles == ["orchestrator", "executor", "final_synthesizer"]
        usage = dict(rows)
        assert usage["orchestrator"] == "always"
        assert usage["executor"] == "always"
        assert usage["final_synthesizer"] == "conditional"


def test_unknown_mode_falls_back_to_auto_roles():
    assert [r for r, _u in roles_for_mode("bogus")] == [
        "orchestrator",
        "executor",
        "final_synthesizer",
    ]


def test_query_refiner_available_in_unified_loop():
    assert query_refiner_available_for_mode("auto") is True
    assert query_refiner_available_for_mode("solo") is True
    assert query_refiner_available_for_mode("serial") is True
    assert query_refiner_available_for_mode("full") is True


def test_set_query_refiner_enabled_persists(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    assert session.set_query_refiner_enabled(True) is True
    assert session.settings.query_refiner_enabled is True
    assert session.set_query_refiner_enabled(False) is False
    assert session.settings.query_refiner_enabled is False


def test_set_execution_mode_normalizes_and_persists(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    assert session.set_execution_mode("FULL") == "auto"
    assert session.settings.execution_mode == "auto"
    assert session.set_execution_mode("bogus") == "auto"
    assert session.settings.execution_mode == "auto"


def test_set_privacy_mode_normalizes_and_persists(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    assert session.set_privacy_mode("cloud_allowed") == "cloud_allowed"
    assert session.settings.privacy_mode == "cloud_allowed"
    assert session.set_privacy_mode("nonsense") == "local_first"
    assert session.settings.privacy_mode == "local_first"


def test_set_model_for_role_promotes_to_front(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.settings.executor_model_priority = ["a", "b", "c"]
    result = session.set_model_for_role("executor", "c")
    assert result[0] == "c"
    assert result == ["c", "a", "b"]
    assert session.settings.executor_model_priority == ["c", "a", "b"]


def test_set_model_for_role_accepts_role_alias(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.settings.orchestrator_model_priority = ["x"]
    result = session.set_model_for_role("主脑", "y")
    assert result == ["y", "x"]
    assert session.settings.orchestrator_model_priority == ["y", "x"]


def test_set_model_for_role_empty_id_is_noop_on_priority_order(tmp_path):
    session = GuiChatSession(workspace=tmp_path)
    session.settings.executor_model_priority = ["a", "b"]
    result = session.set_model_for_role("executor", "")
    assert result == ["a", "b"]
