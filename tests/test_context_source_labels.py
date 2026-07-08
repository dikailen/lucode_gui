from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _Task:
    id: str = "task"
    title: str = "Summarize context"
    instruction: str = "Summarize the provided context."
    skill_id: str = "project_explorer"
    model: str = ""
    mcp: list[str] = None
    read_set: list[str] = None
    write_intent: list[str] = None

    def __post_init__(self):
        self.mcp = list(self.mcp or [])
        self.read_set = list(self.read_set or [])
        self.write_intent = list(self.write_intent or [])


class _Registry:
    def __init__(self):
        self.infos = {
            "cloud": {
                "id": "cloud",
                "configured": True,
                "backend_type": "openai_compatible",
                "is_local": False,
                "privacy_level": "cloud",
                "base_url": "https://api.example.com/v1",
                "supports_tools": True,
                "probe": {"status": "ok"},
            },
            "local": {
                "id": "local",
                "configured": True,
                "backend_type": "ollama",
                "is_local": True,
                "privacy_level": "local",
                "base_url": "http://127.0.0.1:11434/v1",
                "supports_tools": True,
                "probe": {"status": "ok"},
            },
        }

    def get_model_info(self, model_id: str) -> dict:
        return dict(self.infos[model_id])

    def first_configured(self, preferred, privacy_mode: str = "local_first") -> str:
        for model_id in list(preferred or []):
            if model_id in self.infos:
                return model_id
        raise ValueError("No configured models")


def test_file_snapshot_source_label_is_project_private_without_text_guessing():
    from runtime.compute.context_sources import label_context_source
    from runtime.compute.sensitivity import classify_task_sensitivity

    label = label_context_source("file_snapshot", path="src/app.py", text="plain contents")

    decision = classify_task_sensitivity(_Task(), context_labels=[label])

    assert label.sensitivity == "project_private"
    assert decision.sensitivity == "project_private"
    assert "source:file_snapshot" in decision.reasons


def test_secret_file_source_label_is_secret_without_relying_on_instruction_text():
    from runtime.compute.context_sources import label_context_source
    from runtime.compute.sensitivity import classify_task_sensitivity

    label = label_context_source("file_snapshot", path=".env", text="plain placeholder")

    decision = classify_task_sensitivity(_Task(), context_labels=[label])

    assert label.sensitivity == "secret"
    assert decision.sensitivity == "secret"
    assert "source:file_snapshot" in decision.reasons


def test_browser_and_terminal_source_labels_force_local_only():
    from runtime.compute.context_sources import label_context_source
    from runtime.compute.sensitivity import classify_task_sensitivity

    browser = label_context_source("browser_dom", text="visible dashboard text")
    terminal = label_context_source("terminal_output", text="pytest output")

    assert classify_task_sensitivity(_Task(), context_labels=[browser]).sensitivity == "local_only"
    assert classify_task_sensitivity(_Task(), context_labels=[terminal]).sensitivity == "local_only"


def test_memory_entry_source_label_uses_tags_for_sensitivity():
    from runtime.compute.context_sources import label_memory_entry
    from runtime.compute.sensitivity import classify_task_sensitivity
    from runtime.memory.resolver import MemoryPackEntry

    entry = MemoryPackEntry(
        id="mem-1",
        kind="project_fact",
        summary="Use the previous dashboard observation.",
        tags=("browser_dom",),
    )
    label = label_memory_entry(entry)

    assert label.sensitivity == "local_only"
    assert classify_task_sensitivity(_Task(), context_labels=[label]).sensitivity == "local_only"


def test_compute_guard_uses_context_source_labels_before_text_rules():
    from runtime.compute.context_sources import label_context_source
    from runtime.compute.placement_guard import ComputePlacementGuard

    terminal_label = label_context_source("terminal_output", text="ordinary looking text")
    guard = ComputePlacementGuard(
        model_registry=_Registry(),
        privacy_mode="cloud_allowed",
        mode="enforce",
        context_labels=[terminal_label],
    )

    decision = guard.guard_planner_request("Summarize the previous result.", ["cloud", "local"])

    assert decision.sensitivity == "local_only"
    assert decision.model_id == "local"
    assert decision.planner_side == "local"


def test_run_context_store_exports_file_browser_and_terminal_source_labels(tmp_path):
    from runtime.execution.run_context import RunContextStore

    source_file = tmp_path / "src" / "app.py"
    source_file.parent.mkdir()
    source_file.write_text("print('hello')\n", encoding="utf-8")
    store = RunContextStore(tmp_path)

    store.record_file_snapshot(path=source_file, task_id="t1", excerpt="print('hello')")
    store.record_tool_output(
        tool="desktop_browser",
        action="summary",
        summary="visible dashboard text",
        task_id="t2",
    )
    store.record_tool_output(
        tool="terminal",
        action="output",
        summary="pytest output",
        task_id="t3",
    )
    store.record_tool_output(
        tool="workspace_edit",
        action="check",
        summary="local tool output",
        task_id="t4",
    )

    labels = store.source_labels()
    label_pairs = {(label.source_type, label.path): label.sensitivity for label in labels}

    assert label_pairs[("file_snapshot", "src/app.py")] == "project_private"
    assert label_pairs[("browser_summary", "")] == "local_only"
    assert label_pairs[("terminal_output", "")] == "local_only"
    assert label_pairs[("tool_output", "")] == "project_private"
