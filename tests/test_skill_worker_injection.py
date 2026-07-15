from planning.planner_schema import PlannedTask, parse_planner_result
from runtime.agents.factory import AgentFactory
from skills.loader import skill_runtime_metadata


def test_parse_planner_result_attaches_bound_skill_ids_without_rewriting_skill_id():
    result = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "planner adopted a candidate",
          "refined_request": "Fix UI.",
          "tasks": [
            {
              "id": "task_ui",
              "title": "Fix UI",
              "instruction": "Fix desktop/src/App.tsx.",
              "skill_id": "code_engineer",
              "model": "worker-model",
              "mcp": ["project_filesystem_readonly"]
            }
          ],
          "skill_interface": {
            "version": 1,
            "candidate_skill_ids": ["electron_ui_refactor"],
            "adopted_skill_ids": ["electron_ui_refactor"],
            "task_bindings": {
              "task_ui": ["electron_ui_refactor"]
            }
          }
        }
        """,
        fallback_user_input="Fix UI.",
    )

    task = result.tasks[0]
    assert task.skill_id == "code_engineer"
    assert task.bound_skill_ids == ["electron_ui_refactor"]


def test_parse_planner_result_filters_bindings_to_adopted_candidates():
    result = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "planner emitted unsafe bindings",
          "refined_request": "Fix UI.",
          "tasks": [
            {
              "id": "task_ui",
              "title": "Fix UI",
              "instruction": "Fix desktop/src/App.tsx.",
              "skill_id": "code_engineer",
              "model": "worker-model",
              "bound_skill_ids": ["task_level_bypass"]
            }
          ],
          "skill_interface": {
            "version": 1,
            "candidate_skill_ids": ["electron_ui_refactor", "context_ledger"],
            "adopted_skill_ids": ["electron_ui_refactor", "not_a_candidate"],
            "task_bindings": {
              "task_ui": [
                "electron_ui_refactor",
                "context_ledger",
                "not_a_candidate",
                "missing_skill"
              ]
            }
          }
        }
        """,
        fallback_user_input="Fix UI.",
    )

    task = result.tasks[0]
    assert task.skill_id == "code_engineer"
    assert task.bound_skill_ids == ["electron_ui_refactor"]


def test_parse_planner_result_ignores_task_level_bound_skills_without_interface_adoption():
    result = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "planner tried task-level bypass",
          "refined_request": "Fix UI.",
          "tasks": [
            {
              "id": "task_ui",
              "title": "Fix UI",
              "instruction": "Fix desktop/src/App.tsx.",
              "skill_id": "code_engineer",
              "model": "worker-model",
              "bound_skill_ids": ["task_level_bypass"]
            }
          ]
        }
        """,
        fallback_user_input="Fix UI.",
    )

    assert result.tasks[0].bound_skill_ids == []


def test_agent_factory_injects_only_bound_extra_skill_bodies(monkeypatch):
    bodies = {
        "worker_contract": "WORKER_CONTRACT",
        "code_engineer": "PRIMARY_CODE_ENGINEER",
        "electron_ui_refactor": "BOUND_ELECTRON_UI",
        "project_explorer": "BOUND_PROJECT_EXPLORER",
        "skill_creator": "BOUND_SKILL_CREATOR",
    }

    def fake_load_skill(skill_id):
        return bodies[str(skill_id)]

    monkeypatch.setattr("runtime.agents.factory.load_skill", fake_load_skill)
    monkeypatch.setattr(
        "runtime.agents.factory.skill_runtime_metadata",
        lambda skill_id: {"id": skill_id, "source": "sample", "summary": "", "path": ""},
    )

    task = PlannedTask(
        id="task_ui",
        title="Fix UI",
        instruction="Fix desktop/src/App.tsx.",
        skill_id="code_engineer",
        model="worker-model",
        bound_skill_ids=[
            "code_engineer",
            "electron_ui_refactor",
            "project_explorer",
            "skill_creator",
        ],
    )
    factory = AgentFactory(model_registry=object(), mcp_manager=object())

    instructions = factory._task_instructions(task)

    assert "PRIMARY_CODE_ENGINEER" in instructions
    assert "## Bound Skill Context" in instructions
    assert "BOUND_ELECTRON_UI" in instructions
    assert "BOUND_PROJECT_EXPLORER" in instructions
    assert "BOUND_SKILL_CREATOR" not in instructions
    assert instructions.count("PRIMARY_CODE_ENGINEER") == 1


def test_agent_factory_skips_non_assignable_bound_skill_bodies(monkeypatch):
    bodies = {
        "worker_contract": "WORKER_CONTRACT",
        "code_engineer": "PRIMARY_CODE_ENGINEER",
        "ready_skill": "READY_BODY",
        "disabled_skill": "DISABLED_BODY",
        "incomplete_skill": "INCOMPLETE_BODY",
        "rule_only_skill": "RULE_ONLY_BODY",
    }
    metadata = {
        "code_engineer": {"id": "code_engineer", "source": "sample", "summary": "", "path": ""},
        "ready_skill": {
            "id": "ready_skill",
            "source": "workspace",
            "summary": "",
            "path": "",
            "enabled": True,
            "assignable": True,
            "metadata_status": "ready",
        },
        "disabled_skill": {
            "id": "disabled_skill",
            "source": "workspace",
            "summary": "",
            "path": "",
            "enabled": False,
            "assignable": True,
            "metadata_status": "ready",
        },
        "incomplete_skill": {
            "id": "incomplete_skill",
            "source": "workspace",
            "summary": "",
            "path": "",
            "enabled": True,
            "assignable": True,
            "metadata_status": "incomplete",
        },
        "rule_only_skill": {
            "id": "rule_only_skill",
            "source": "workspace",
            "summary": "",
            "path": "",
            "enabled": True,
            "assignable": False,
            "metadata_status": "ready",
        },
    }

    monkeypatch.setattr("runtime.agents.factory.load_skill", lambda skill_id: bodies[str(skill_id)])
    monkeypatch.setattr("runtime.agents.factory.skill_runtime_metadata", lambda skill_id: metadata[str(skill_id)])

    task = PlannedTask(
        id="task_ui",
        title="Fix UI",
        instruction="Fix desktop/src/App.tsx.",
        skill_id="code_engineer",
        model="worker-model",
        bound_skill_ids=[
            "ready_skill",
            "disabled_skill",
            "incomplete_skill",
            "rule_only_skill",
        ],
    )
    factory = AgentFactory(model_registry=object(), mcp_manager=object())

    instructions = factory._task_instructions(task)

    assert "READY_BODY" in instructions
    assert "DISABLED_BODY" not in instructions
    assert "INCOMPLETE_BODY" not in instructions
    assert "RULE_ONLY_BODY" not in instructions


def test_skill_runtime_metadata_exposes_assignability_flags():
    meta = skill_runtime_metadata("execution_supervisor")

    assert meta["enabled"] is True
    assert meta["assignable"] is False
    assert meta["internal"] is True
    assert meta["metadata_status"] == "ready"


def test_agent_factory_truncates_bound_skill_body(monkeypatch):
    long_body = "x" * 7000
    monkeypatch.setattr(
        "runtime.agents.factory.load_skill",
        lambda skill_id: (
            "PRIMARY"
            if skill_id == "code_engineer"
            else "WORKER_CONTRACT"
            if skill_id == "worker_contract"
            else long_body
        ),
    )
    monkeypatch.setattr(
        "runtime.agents.factory.skill_runtime_metadata",
        lambda skill_id: {"id": skill_id, "source": "sample", "summary": "", "path": ""},
    )

    task = PlannedTask(
        id="task_code",
        title="Fix code",
        instruction="Fix runtime bug.",
        skill_id="code_engineer",
        model="worker-model",
        bound_skill_ids=["extra_code_skill"],
    )
    factory = AgentFactory(model_registry=object(), mcp_manager=object())

    instructions = factory._task_instructions(task)

    assert long_body not in instructions
    assert long_body[:6000] in instructions
    assert "[truncated]" in instructions
