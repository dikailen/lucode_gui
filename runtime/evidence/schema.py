from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceRef:
    ref_id: str
    kind: str
    source: str = ""
    event_seq: int = 0
    excerpt: str = ""
    sha256: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Claim:
    claim_id: str
    task_id: str
    text: str
    claim_type: str = "observation"
    confidence: float = 0.0
    evidence_refs: list[str] = field(default_factory=list)
    risk_level: str = "normal"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GateVerdict:
    claim_id: str
    status: str
    reasons: list[str] = field(default_factory=list)
    required_rework: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvidenceGateResult:
    mode: str
    claims: list[Claim] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)
    verdicts: list[GateVerdict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "claims": [item.to_dict() for item in self.claims],
            "evidence": [item.to_dict() for item in self.evidence],
            "verdicts": [item.to_dict() for item in self.verdicts],
        }
