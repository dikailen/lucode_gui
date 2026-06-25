from __future__ import annotations

from runtime.memory.flywheel import FlywheelStore
from planning.planner_schema import PlannedTask
from runtime.memory.resolver import MemoryPack, MemoryPackEntry, MemoryResolver


def test_memory_resolver_builds_planner_pack_from_flywheel_and_session_summary(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    verification = flywheel.append_entry(
        kind="verification_command",
        summary="After GUI theme changes run python -m pytest tests/test_gui_minimal_theme.py -q",
        tags=["gui", "theme", "pytest"],
        source="distiller",
        metadata={
            "confidence": 0.86,
            "scope": ["lucode/gui/theme.py", "tests/test_gui_minimal_theme.py"],
            "status": "active",
        },
    )
    flywheel.append_entry(
        kind="pipeline_summary",
        summary="Pipeline single_agent: 1 completed, 0 failed.",
        tags=["single_agent"],
        source="pipeline",
        metadata={"confidence": 1.0},
    )

    resolver = MemoryResolver(tmp_path, flywheel=flywheel)
    pack = resolver.resolve_for_planner(
        "修改 lucode/gui/theme.py 的 GUI theme",
        session_summary="上一轮已经完成 GUI 侧边栏密度调整。",
        recent_turns=[{"role": "user", "content": "继续 GUI 主题"}],
    )

    assert pack.entries
    assert pack.entries[0].id == verification["id"]
    assert pack.entries[0].kind == "verification_command"
    assert pack.entries[0].injection_policy == "auto"
    assert "Pipeline single_agent" not in pack.render_for_planner()
    rendered = pack.render_for_planner()
    assert "项目经验" in rendered
    assert "背景，不是本轮新任务" in rendered
    assert "python -m pytest tests/test_gui_minimal_theme.py -q" in rendered
    assert "上一轮已经完成 GUI 侧边栏密度调整" in rendered


def test_memory_resolver_keeps_failure_lessons_as_planner_candidates(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    lesson = flywheel.append_entry(
        kind="failure_lesson",
        summary="Parallel workers conflicted when both wrote lucode/gui/theme.py",
        tags=["failure", "gui", "theme"],
        source="distiller",
        metadata={
            "confidence": 0.92,
            "scope": ["lucode/gui/theme.py"],
            "status": "active",
        },
    )

    pack = MemoryResolver(tmp_path, flywheel=flywheel).resolve_for_planner(
        "继续修改 lucode/gui/theme.py",
    )
    rendered = pack.render_for_planner()

    assert pack.failure_lesson_candidates
    assert pack.failure_lesson_candidates[0].id == lesson["id"]
    assert pack.failure_lesson_candidates[0].injection_policy == "planner_candidate"
    assert "失败教训候选" in rendered
    assert "只有 planner 明确采用后才可进入 worker" in rendered


def test_memory_resolver_filters_low_confidence_and_conflicted_entries(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    flywheel.append_entry(
        kind="tool_hint",
        summary="Use pytest for GUI tests",
        tags=["gui", "pytest"],
        source="distiller",
        metadata={"confidence": 0.49, "scope": ["tests/test_gui.py"], "status": "active"},
    )
    flywheel.append_entry(
        kind="tool_hint",
        summary="Use outdated command for GUI tests",
        tags=["gui", "pytest"],
        source="distiller",
        metadata={"confidence": 0.9, "scope": ["tests/test_gui.py"], "status": "conflicted"},
    )

    pack = MemoryResolver(tmp_path, flywheel=flywheel).resolve_for_planner("GUI pytest")

    assert pack.entries == []
    assert pack.failure_lesson_candidates == []
    assert pack.render_for_planner() == ""


def test_memory_resolver_filters_blocked_statuses_and_records_ignored_ids(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    active = flywheel.append_entry(
        kind="tool_hint",
        summary="Use active GUI pytest command",
        tags=["gui", "pytest"],
        source="distiller",
        metadata={"confidence": 0.9, "scope": ["tests/test_gui.py"], "status": "active"},
    )
    blocked_entries = [
        flywheel.append_entry(
            kind="tool_hint",
            summary=f"Blocked GUI pytest command {status}",
            tags=["gui", "pytest"],
            source="distiller",
            metadata={"confidence": 0.9, "scope": ["tests/test_gui.py"], "status": status},
        )
        for status in ("conflicted", "expired", "rejected")
    ]

    pack = MemoryResolver(tmp_path, flywheel=flywheel).resolve_for_planner("GUI pytest")

    assert [entry.id for entry in pack.entries] == [active["id"]]
    assert set(pack.ignored_entry_ids) == {entry["id"] for entry in blocked_entries}


def test_memory_resolver_redacts_secrets_before_rendering(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    flywheel.append_entry(
        kind="tool_hint",
        summary="Use token=sk-secret123456 for GUI pytest setup",
        tags=["gui", "pytest"],
        source="distiller",
        metadata={
            "confidence": 0.9,
            "scope": ["tests/test_gui.py"],
            "status": "active",
        },
    )

    rendered = MemoryResolver(tmp_path, flywheel=flywheel).resolve_for_planner("GUI pytest").render_for_planner()

    assert "sk-secret123456" not in rendered
    assert "[redacted]" in rendered or "[REDACTED_SECRET]" in rendered

def test_memory_pack_exports_memory_interface_metadata():
    from runtime.memory.resolver import MemoryPack, MemoryPackEntry

    pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-auto",
                kind="verification_command",
                summary="Run pytest",
                confidence=0.86,
                injection_policy="auto",
            )
        ],
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Avoid conflicting writes",
                confidence=0.91,
                injection_policy="planner_candidate",
            )
        ],
        ignored_entry_ids=["mem-ignored"],
    )

    metadata = pack.to_memory_interface()

    assert metadata["version"] == 1
    assert metadata["provided_entry_ids"] == ["mem-auto"]
    assert metadata["candidate_entry_ids"] == ["mem-failure"]
    assert metadata["ignored_entry_ids"] == ["mem-ignored"]
    assert metadata["adopted_entry_ids"] == []
    assert metadata["decision_reasons"]["mem-auto"] == "provided_to_planner"
    assert metadata["decision_reasons"]["mem-failure"] == "planner_candidate_only"

def test_memory_pack_builds_task_pack_from_matching_high_confidence_entries():
    pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-gui",
                kind="tool_hint",
                summary="Use GUI pytest after theme edits.",
                confidence=0.91,
                scope=("lucode/gui/theme.py", "tests/test_gui_minimal_theme.py"),
                injection_policy="auto",
            ),
            MemoryPackEntry(
                id="mem-docs",
                kind="project_fact",
                summary="Docs live under docs/.",
                confidence=0.93,
                scope=("docs/",),
                injection_policy="auto",
            ),
            MemoryPackEntry(
                id="mem-low",
                kind="tool_hint",
                summary="Planner-only hint.",
                confidence=0.67,
                scope=("lucode/gui/theme.py",),
                injection_policy="planner_candidate",
            ),
        ],
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Do not inject this unless planner binds it.",
                confidence=0.95,
                scope=("lucode/gui/theme.py",),
                injection_policy="planner_candidate",
            )
        ],
    )
    task = PlannedTask(
        id="worker-gui",
        title="GUI theme",
        instruction="Update the GUI theme",
        skill_id="code_engineer",
        model="gpt",
        read_set=["lucode/gui/theme.py"],
        write_intent=["lucode/gui/theme.py"],
    )

    task_pack = pack.for_task(task)
    rendered = task_pack.render_for_worker()

    assert task_pack.provided_entry_ids == ["mem-gui"]
    assert "mem-gui" in rendered
    assert "Use GUI pytest after theme edits." in rendered
    assert "mem-docs" not in rendered
    assert "mem-low" not in rendered
    assert "mem-failure" not in rendered

def test_task_memory_pack_requires_scope_match_not_tag_or_title_only():
    pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-tag-only",
                kind="tool_hint",
                summary="This GUI hint has no concrete file scope match.",
                confidence=0.91,
                tags=("gui",),
                scope=("lucode/gui/sidebar.py",),
                injection_policy="auto",
            )
        ]
    )
    task = PlannedTask(
        id="worker-theme",
        title="GUI theme",
        instruction="Update GUI theme controls",
        skill_id="code_engineer",
        model="gpt",
        read_set=["lucode/gui/theme.py"],
        write_intent=["lucode/gui/theme.py"],
    )

    task_pack = pack.for_task(task)

    assert task_pack.provided_entry_ids == []
    assert task_pack.render_for_worker() == ""


def test_memory_resolver_injects_high_confidence_active_project_fact_from_store(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    fact = flywheel.append_entry(
        kind="project_fact",
        summary="Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
        tags=["memory", "resolver"],
        source="distiller",
        metadata={
            "confidence": 0.86,
            "scope": ["runtime/memory/resolver.py"],
            "status": "active",
            "injection_policy": "auto",
        },
    )

    pack = MemoryResolver(tmp_path, flywheel=flywheel).resolve_for_planner("memory resolver rendering")
    task = PlannedTask(
        id="worker-memory",
        title="Memory resolver",
        instruction="Update memory resolver rendering.",
        skill_id="code_engineer",
        model="gpt",
        read_set=["runtime/memory/resolver.py"],
        write_intent=["runtime/memory/resolver.py"],
    )
    task_pack = pack.for_task(task)

    assert [entry.id for entry in pack.entries] == [fact["id"]]
    assert pack.entries[0].kind == "project_fact"
    assert pack.entries[0].injection_policy == "auto"
    assert task_pack.provided_entry_ids == [fact["id"]]
    assert "Memory resolver context rendering" in task_pack.render_for_worker()


def test_memory_resolver_does_not_inject_worker_report_only_project_fact_until_confident(tmp_path):
    flywheel = FlywheelStore(tmp_path)
    fact = flywheel.append_entry(
        kind="project_fact",
        summary="Memory resolver context rendering is implemented in runtime/memory/resolver.py.",
        tags=["memory", "resolver"],
        source="distiller",
        metadata={
            "confidence": 0.65,
            "scope": ["runtime/memory/resolver.py"],
            "status": "active",
            "injection_policy": "auto",
        },
    )

    pack = MemoryResolver(tmp_path, flywheel=flywheel).resolve_for_planner("memory resolver rendering")
    task = PlannedTask(
        id="worker-memory",
        title="Memory resolver",
        instruction="Update memory resolver rendering.",
        skill_id="code_engineer",
        model="gpt",
        read_set=["runtime/memory/resolver.py"],
        write_intent=["runtime/memory/resolver.py"],
    )
    task_pack = pack.for_task(task)

    assert [entry.id for entry in pack.entries] == [fact["id"]]
    assert pack.entries[0].kind == "project_fact"
    assert pack.entries[0].injection_policy == "planner_candidate"
    assert task_pack.provided_entry_ids == []
    assert task_pack.render_for_worker() == ""


def test_memory_pack_applies_planner_failure_lesson_bindings_to_one_task():
    pack = MemoryPack(
        entries=[
            MemoryPackEntry(
                id="mem-auto",
                kind="tool_hint",
                summary="Run GUI pytest after changing theme.py.",
                confidence=0.91,
                scope=("lucode/gui/theme.py",),
                injection_policy="auto",
            )
        ],
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Avoid parallel writes to lucode/gui/theme.py after rollback.",
                confidence=0.78,
                scope=("lucode/gui/theme.py",),
                injection_policy="planner_candidate",
            )
        ],
    )
    pack.apply_memory_interface(
        {
            "adopted_entry_ids": ["mem-failure"],
            "task_bindings": {"worker-gui": ["mem-failure"]},
            "adoption_reasons": {"mem-failure": "planner bound rollback lesson to GUI worker"},
        }
    )
    gui_task = PlannedTask(
        id="worker-gui",
        title="GUI theme",
        instruction="Update theme.py",
        skill_id="code_engineer",
        model="gpt",
        read_set=["lucode/gui/theme.py"],
        write_intent=["lucode/gui/theme.py"],
    )
    docs_task = PlannedTask(
        id="worker-docs",
        title="Docs",
        instruction="Update docs",
        skill_id="code_engineer",
        model="gpt",
        read_set=["docs/readme.md"],
        write_intent=["docs/readme.md"],
    )

    gui_pack = pack.for_task(gui_task)
    docs_pack = pack.for_task(docs_task)
    rendered = gui_pack.render_for_worker()

    assert gui_pack.provided_entry_ids == ["mem-auto", "mem-failure"]
    assert "失败教训" in rendered
    assert "mem-failure" in rendered
    assert "Avoid parallel writes" in rendered
    assert docs_pack.provided_entry_ids == []
    assert docs_pack.render_for_worker() == ""


def test_memory_pack_ignores_unadopted_failure_lesson_even_if_scope_matches():
    pack = MemoryPack(
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Scope matches but planner did not adopt it.",
                confidence=0.88,
                scope=("lucode/gui/theme.py",),
                injection_policy="planner_candidate",
            )
        ]
    )
    task = PlannedTask(
        id="worker-gui",
        title="GUI theme",
        instruction="Update theme.py",
        skill_id="code_engineer",
        model="gpt",
        read_set=["lucode/gui/theme.py"],
        write_intent=["lucode/gui/theme.py"],
    )

    task_pack = pack.for_task(task)

    assert task_pack.provided_entry_ids == []
    assert task_pack.render_for_worker() == ""


def test_memory_pack_exports_adoption_metadata_after_planner_binding():
    pack = MemoryPack(
        failure_lesson_candidates=[
            MemoryPackEntry(
                id="mem-failure",
                kind="failure_lesson",
                summary="Avoid conflicting writes",
                confidence=0.81,
                injection_policy="planner_candidate",
            )
        ]
    )
    pack.apply_memory_interface(
        {
            "adopted_entry_ids": ["mem-failure"],
            "task_bindings": {"worker-gui": ["mem-failure"]},
            "adoption_reasons": {"mem-failure": "planner adopted for worker-gui"},
        }
    )

    metadata = pack.to_memory_interface()

    assert metadata["candidate_entry_ids"] == ["mem-failure"]
    assert metadata["adopted_entry_ids"] == ["mem-failure"]
    assert metadata["task_bindings"] == {"worker-gui": ["mem-failure"]}
    assert metadata["adoption_reasons"] == {"mem-failure": "planner adopted for worker-gui"}
    assert metadata["decision_reasons"]["mem-failure"] == "adopted_by_planner"


def test_memory_pack_records_pending_usage_by_source_once_and_flushes():
    pack = MemoryPack()

    pack.record_usage(["mem-a", "mem-a", "", "mem-b"], source="worker")
    pack.record_usage(["mem-a"], source="worker")
    pack.record_usage(["mem-a"], source="planner_provided")

    assert pack.pop_pending_usage() == {
        "worker": ["mem-a", "mem-b"],
        "planner_provided": ["mem-a"],
    }
    assert pack.pop_pending_usage() == {}
