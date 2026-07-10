from __future__ import annotations

from dataclasses import dataclass, field

from runtime.compute.context_sources import ContextSourceLabel, label_context_source, strongest_context_sensitivity
from runtime.context.token_counter import estimate_tokens


@dataclass(frozen=True)
class ContextEnvelope:
    source_type: str
    dehydrated_summary: str
    source_labels: tuple[ContextSourceLabel, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    raw_artifact_ref: str = ""
    privacy_level: str = "public"
    token_budget: int = 0

    def __post_init__(self) -> None:
        labels = self.source_labels or (label_context_source(self.source_type, text=self.dehydrated_summary),)
        privacy, _ = strongest_context_sensitivity(labels)
        object.__setattr__(self, "source_labels", tuple(labels))
        object.__setattr__(self, "privacy_level", privacy if self.privacy_level == "public" else self.privacy_level)
        object.__setattr__(self, "token_budget", self.token_budget or estimate_tokens(self.dehydrated_summary))

    @classmethod
    def from_tool_result(cls, *, tool: str, action: str, result) -> "ContextEnvelope":
        source_type = _tool_source_type(tool, action)
        summary = str(getattr(result, "summary", "") or "")
        evidence = str(getattr(result, "evidence_ref", "") or "").strip()
        return cls(
            source_type=source_type,
            dehydrated_summary=summary,
            source_labels=(label_context_source(source_type, text=summary),),
            evidence_refs=(evidence,) if evidence else (),
            raw_artifact_ref=str(getattr(result, "raw_artifact_ref", "") or "").strip(),
        )


def _tool_source_type(tool: str, action: str) -> str:
    value = f"{tool} {action}".lower()
    if "browser" in value:
        return "browser_summary"
    if any(marker in value for marker in ("terminal", "shell", "command")):
        return "terminal_output"
    return "tool_output"
