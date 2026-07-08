from planning.planner_schema import PlannedTask, parse_planner_result
from runtime.agents.factory import AgentFactory


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


def test_agent_factory_injects_only_bound_extra_skill_bodies(monkeypatch):
    bodies = {
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


def test_agent_factory_truncates_bound_skill_body(monkeypatch):
    long_body = "x" * 7000
    monkeypatch.setattr(
        "runtime.agents.factory.load_skill",
        lambda skill_id: "PRIMARY" if skill_id == "code_engineer" else long_body,
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

    assert len(instructions) < 7000
    assert "[truncated]" in instructions
