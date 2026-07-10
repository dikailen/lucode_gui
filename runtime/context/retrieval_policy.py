from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalPolicy:
    source_type: str
    sensitivity: str
    allowed_for_embedding: bool
    allowed_for_prompt: bool
    requires_redaction: bool
    reason: str


def decide_retrieval_policy(envelope) -> RetrievalPolicy:
    source_type = str(getattr(envelope, "source_type", "unknown") or "unknown")
    sensitivity = str(getattr(envelope, "privacy_level", "public") or "public")
    if source_type in {"task_state", "accepted_evidence", "approval_state"}:
        return RetrievalPolicy(source_type, sensitivity, False, True, False, "current_state_must_not_be_cold_only")
    if sensitivity in {"secret", "local_only"}:
        return RetrievalPolicy(source_type, sensitivity, False, False, True, "sensitive_source_not_embeddable")
    if source_type in {"skill_metadata", "plugin_doc", "failure_lesson"}:
        return RetrievalPolicy(source_type, sensitivity, True, True, sensitivity == "project_private", "cold_context_allowed")
    return RetrievalPolicy(source_type, sensitivity, False, True, sensitivity == "project_private", "not_a_cold_context_source")
