from planning.plan_normalizer import normalize_plan_for_execution
from planning.planner_schema import PlannerResult, parse_planner_result


def test_parse_planner_result_preserves_skill_interface_without_rebinding_task_skill():
    result = parse_planner_result(
        """
        {
          "route_type": "single_agent",
          "reason": "candidate skill was useful",
          "refined_request": "Fix the Electron UI layout.",
          "tasks": [
            {
              "id": "task_ui",
              "title": "Fix UI",
              "instruction": "Fix desktop/src/App.tsx layout.",
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
            },
            "reasons": {
              "electron_ui_refactor": "UI candidate matched desktop/src."
            },
            "rejected_skill_ids": ["context_ledger"],
            "rejection_reasons": {
              "context_ledger": "Not a context compression task."
            }
          }
        }
        """,
        fallback_user_input="Fix the Electron UI layout.",
    )

    assert result.skill_interface["version"] == 1
    assert result.skill_interface["candidate_skill_ids"] == ["electron_ui_refactor"]
    assert result.skill_interface["task_bindings"] == {"task_ui": ["electron_ui_refactor"]}
    assert result.tasks[0].skill_id == "code_engineer"


def test_planner_result_defaults_skill_interface_to_empty_dict():
    result = PlannerResult(
        route_type="direct_answer",
        reason="simple answer",
        refined_request="hello",
    )

    assert result.skill_interface == {}


def test_normalizer_preserves_skill_interface_when_route_is_downgraded():
    plan = parse_planner_result(
        """
        {
          "route_type": "multi_agent",
          "reason": "planner over split",
          "refined_request": "Read README.",
          "tasks": [
            {
              "id": "task_readme",
              "title": "Read README",
              "instruction": "Read README.md.",
              "skill_id": "project_explorer",
              "model": "worker-model",
              "mcp": ["project_filesystem_readonly"],
              "parallel_group": 2,
              "depends_on": ["setup"]
            }
          ],
          "needs_synthesis": true,
          "synthesis_instruction": "Merge results.",
          "skill_interface": {
            "version": 1,
            "candidate_skill_ids": ["project_explorer"],
            "adopted_skill_ids": ["project_explorer"],
            "task_bindings": {
              "task_readme": ["project_explorer"]
            }
          }
        }
        """,
        fallback_user_input="Read README.",
    )

    normalized, notes = normalize_plan_for_execution(plan)

    assert notes
    assert normalized.route_type == "single_agent"
    assert normalized.skill_interface == plan.skill_interface
    assert normalized.tasks[0].skill_id == "project_explorer"
