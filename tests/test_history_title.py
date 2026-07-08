from __future__ import annotations

from runtime.history.titles import smart_session_title


def test_smart_session_title_removes_polite_prefix_and_keeps_task_intent():
    title = smart_session_title("请你帮我检查当前项目的真实 Chat MVP 是否已经能够保存历史并恢复")

    assert title == "检查 Chat MVP 历史保存恢复"


def test_smart_session_title_removes_urls_and_uses_meaningful_action():
    title = smart_session_title("能不能帮我打开 https://example.com 然后总结页面内容？")

    assert title == "总结页面内容"


def test_smart_session_title_compacts_phase_requests():
    title = smart_session_title("直接进入p4吧，做好风险处理")

    assert title == "P4 风险处理"
