# Runtime Memory

Lucode stores lightweight project memory under `.agent_cache/flywheel_memory.jsonl`.
Each line is one JSON object. This file is auxiliary: failure to read or write it
must not block the agent loop.

## Flywheel Entry

Required fields:

- `id`: stable entry id for audit references.
- `time`: local creation timestamp.
- `project_root`: absolute project root at write time.
- `kind`: memory category.
- `source`: writer such as `pipeline`, `repair_loop`, or future `distiller`.
- `summary`: short redacted text, not raw code.
- `tags`: lowercase-ish search hints.
- `metadata`: structured details.

Supported `kind` values for phase 0/1:

- `pipeline_summary`: archive-only execution summary. It is not injected into prompts.
- `failure_case`: existing repair-loop failure record. It is treated as a planner candidate only.
- `project_fact`: stable project fact.
- `verification_command`: successful command with a clear scope.
- `tool_hint`: reliable tool usage or fallback.
- `failure_lesson`: distilled failure lesson, planner candidate by default.
- `path_mapping`: task intent to project path mapping.

Recommended `metadata` fields for distilled entries:

- `confidence`: rule-generated float from 0 to 1.
- `scope`: files, modules, commands, or tool ids where the entry applies.
- `status`: `active`, `conflicted`, `expired`, or `rejected`.
- `fingerprint`: `kind + normalized_scope + normalized_action`.
- `evidence_count`: number of confirming observations.
- `decision_reasons`: short rule decisions explaining why this entry was accepted.

## Injection Rules

Phase 1 uses `MemoryResolver` to create a planner memory pack:

- `pipeline_summary` stays archive-only.
- `project_fact`, `verification_command`, `tool_hint`, and `path_mapping` can be
  auto context only when `confidence >= 0.75`.
- `failure_case` and `failure_lesson` are planner candidates only. They do not
  enter worker prompts unless the planner explicitly chooses them later.
- `conflicted`, `expired`, and `rejected` entries are never auto-injected.
- Low-confidence entries below `0.50` are ignored for prompt context.

All text must be redacted before write and again before rendering.

## Planner Memory Interface

Phase 2 records resolver provenance under `PlannerResult.memory_interface["memory_resolver"]`.
This key is owned by the flywheel memory layer.

Expected fields:

- `version`: schema version for the resolver payload.
- `provided_entry_ids`: high-confidence entries rendered into the planner context.
- `candidate_entry_ids`: failure lessons or other entries shown only as planner candidates.
- `ignored_entry_ids`: entries found by search but filtered before prompt injection.
- `adopted_entry_ids`: entries the planner explicitly adopts. Phase 2 initializes this as empty.
- `decision_reasons`: per-entry reason such as `provided_to_planner`, `planner_candidate_only`, or `filtered_or_below_threshold`.

Do not store resolver data directly at the top level of `memory_interface`.
`PlannerResult.memory_interface["execution_contract"]` is reserved for the M2 execution contract and must remain independently readable and writable.
## Worker Memory Injection

Phase 3 derives a `TaskMemoryPack` from the planner `MemoryPack` for each worker.
Worker auto-injection is narrower than planner injection:

- Only `project_fact`, `verification_command`, `tool_hint`, and `path_mapping` entries with `injection_policy="auto"` are eligible.
- The entry `metadata.scope` must overlap the task `read_set` or `write_intent`.
- Tags, task titles, and natural-language instructions are not enough for worker injection.
- `failure_case` and `failure_lesson` remain planner candidates only until a later planner-adoption contract binds them to a task.
- When a worker prompt receives memory, the runtime emits `TaskMemoryProvided` with the provided entry ids.
- `WorkerReport.artifacts` records `memory_context_provided: ...` as provenance only; it does not claim the model used or accepted those memories.
