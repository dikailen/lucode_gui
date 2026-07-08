from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _skill_text(name: str) -> str:
    return (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")


def test_final_synthesizer_contract_mentions_accepted_evidence_only():
    text = _skill_text("final-synthesizer")

    assert "accepted evidence" in text
    assert "needs_recheck" in text
    assert "rejected" in text


def test_worker_and_supervisor_contracts_mention_runtime_evidence_binding():
    worker = _skill_text("full-worker-contract")
    supervisor = _skill_text("full-supervisor")

    assert "runtime-owned evidence" in worker
    assert "timeline" in worker
    assert "approval token" in supervisor
    assert "accepted evidence" in supervisor


def test_p7_planner_contract_mentions_reliability_fields():
    text = _skill_text("orchestrator-planner")

    assert "sensitivity" in text
    assert "difficulty" in text
    assert "placement" in text
    assert "evidence_requirements" in text


def test_p7_executor_contracts_mention_claims_and_evidence_gaps():
    worker = _skill_text("full-worker-contract")
    serial = _skill_text("serial-executor-contract")
    solo = _skill_text("solo-executor-contract")

    assert "claim_id" in worker
    assert "evidence_refs" in worker
    assert "evidence_requirements" in serial
    assert "evidence gap" in serial
    assert "evidence gap" in solo
