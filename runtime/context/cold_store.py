from __future__ import annotations

from dataclasses import dataclass

from runtime.context.retrieval_policy import decide_retrieval_policy


@dataclass(frozen=True)
class ColdStoreAdmission:
    accepted: bool
    reason: str


def admit_to_cold_store(envelope) -> ColdStoreAdmission:
    policy = decide_retrieval_policy(envelope)
    return ColdStoreAdmission(policy.allowed_for_embedding, policy.reason)
