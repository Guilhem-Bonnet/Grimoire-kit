"""Mission Intake — deterministic classification of a human request into a mission draft.

No LLM required. Uses keyword scoring, pattern matching, and heuristics to:
- Detect mission type and scope
- Score risk profile
- Propose initial tasks
- Preview likely policy checks
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.missions.schemas import RiskProfile, TaskType

__all__ = [
    "IntakeRequest",
    "IntakeResult",
    "MissionIntakeService",
    "PolicyHint",
    "TaskProposal",
]

# ── Keyword tables ────────────────────────────────────────────────────────────

_RISK_CRITICAL_PATTERNS = re.compile(
    r"\b(drop\s+table|rm\s+-rf|delete\s+(all|database|schema|prod)|"
    r"truncate|wipe|nuke|overwrite\s+prod|force[\s-]push|reset[\s-]hard|"
    r"revoke\s+access|disable\s+(auth|security|tls|ssl)|"
    r"secret|password|token|credential|api.?key)\b",
    re.IGNORECASE,
)
_RISK_HIGH_PATTERNS = re.compile(
    r"\b(delete|remove|drop|migrate|deploy\s+to\s+prod|publish|release|"
    r"rollback|revert|archive|purge|rotate|revoke|impersonat)\b",
    re.IGNORECASE,
)
_RISK_STANDARD_PATTERNS = re.compile(
    r"\b(create|add|implement|refactor|update|modify|change|replace|"
    r"install|configure|setup|build|generate|scaffold)\b",
    re.IGNORECASE,
)
_RISK_LOW_PATTERNS = re.compile(
    r"\b(read|list|show|display|explain|describe|analyze|review|check|"
    r"inspect|search|find|query|report|summarize|document)\b",
    re.IGNORECASE,
)

_SENSITIVE_PATHS = re.compile(
    r"(\.env|\.secret|credentials|id_rsa|\.pem|\.key|vault|keystore)",
    re.IGNORECASE,
)
_NETWORK_SCOPE = re.compile(
    r"\b(api|http|request|endpoint|webhook|deploy|cloud|aws|gcp|azure|k8s|docker)\b",
    re.IGNORECASE,
)
_MEMORY_SCOPE = re.compile(
    r"\b(memory|qdrant|weaviate|neo4j|vector|embed|recall|inject)\b",
    re.IGNORECASE,
)
_PACK_SCOPE = re.compile(
    r"\b(pack|plugin|extension|module|install|registry)\b",
    re.IGNORECASE,
)

_TASK_TYPE_KEYWORDS: list[tuple[re.Pattern[str], TaskType]] = [
    (re.compile(r"\b(test|spec|coverage|pytest|tdd|bdd|assertion)\b", re.IGNORECASE), TaskType.TEST),
    (re.compile(r"\b(document|doc|readme|changelog|guide|wiki)\b", re.IGNORECASE), TaskType.DOCUMENTATION),
    (re.compile(r"\b(migrat|port|convert|upgrade|downgrade|transfer)", re.IGNORECASE), TaskType.MIGRATION),
    (re.compile(r"\b(secur|audit|pentest|vulnerabilit|cve|owasp|harden)", re.IGNORECASE), TaskType.SECURITY),
    (re.compile(r"\b(analyz|review|investigat|diagnos|assess|evaluat|research)", re.IGNORECASE), TaskType.ANALYSIS),
    (re.compile(r"\b(architect|design|plan|structure|schema|erd|adr)\b", re.IGNORECASE), TaskType.ARCHITECTURE),
    (re.compile(r"\b(clean|remov|delet|purge|deprecat|retire)", re.IGNORECASE), TaskType.CLEANUP),
    (re.compile(r"\b(deploy|run|operate|monitor|alert|oncall|incident)\b", re.IGNORECASE), TaskType.OPERATION),
    (re.compile(r"\b(implement|build|create|add|code|develop|fix|debug|refactor)\b", re.IGNORECASE), TaskType.IMPLEMENTATION),
]

# Policy hints triggered by scope/risk combinations
_POLICY_HINTS: list[tuple[re.Pattern[str], str, str]] = [
    (_SENSITIVE_PATHS, "secret_access", "block"),
    (re.compile(r"\b(pack|plugin)\b", re.IGNORECASE), "pack_activation_requires_evidence", "warn"),
    (re.compile(r"\b(deploy|publish|release)\b", re.IGNORECASE), "no_destructive_without_strict", "warn"),
    (re.compile(r"\b(close|done|finish|complete)\b", re.IGNORECASE), "task_close_requires_verification", "warn"),
    (_MEMORY_SCOPE, "memory_injection", "warn"),
]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class IntakeRequest:
    raw_text: str
    project_root: Path | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def text_lower(self) -> str:
        return self.raw_text.lower()


@dataclass(frozen=True, slots=True)
class TaskProposal:
    title: str
    task_type: TaskType
    risk_profile: RiskProfile
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "task_type": self.task_type.value,
            "risk_profile": self.risk_profile.value,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class PolicyHint:
    rule_id: str
    expected_verdict: str
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "expected_verdict": self.expected_verdict, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class IntakeResult:
    mission_title: str
    mission_type: str
    risk_profile: RiskProfile
    scope_hints: tuple[str, ...]
    task_proposals: tuple[TaskProposal, ...]
    policy_hints: tuple[PolicyHint, ...]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_title": self.mission_title,
            "mission_type": self.mission_type,
            "risk_profile": self.risk_profile.value,
            "scope_hints": list(self.scope_hints),
            "confidence": self.confidence,
            "task_proposals": [t.to_dict() for t in self.task_proposals],
            "policy_hints": [p.to_dict() for p in self.policy_hints],
        }


# ── Service ───────────────────────────────────────────────────────────────────

class MissionIntakeService:
    """Classify a raw request into a mission draft without LLM inference.

    Usage::

        svc = MissionIntakeService()
        result = svc.analyze(IntakeRequest(raw_text="Implement the recipe schema"))
        # result.risk_profile → RiskProfile.STANDARD
        # result.task_proposals[0].task_type → TaskType.IMPLEMENTATION
    """

    def analyze(self, request: IntakeRequest) -> IntakeResult:
        text = request.raw_text
        risk = self._score_risk(text)
        task_type = self._detect_task_type(text)
        scopes = self._detect_scopes(text)
        policy_hints = self._build_policy_hints(text, risk)
        proposals = self._propose_tasks(text, task_type, risk)
        title = self._extract_title(text)
        mission_type = self._detect_mission_type(task_type)
        confidence = self._confidence_score(text, task_type, risk)

        return IntakeResult(
            mission_title=title,
            mission_type=mission_type,
            risk_profile=risk,
            scope_hints=tuple(scopes),
            task_proposals=tuple(proposals),
            policy_hints=tuple(policy_hints),
            confidence=confidence,
        )

    # ── Scoring helpers ────────────────────────────────────────────────────

    def _score_risk(self, text: str) -> RiskProfile:
        if _RISK_CRITICAL_PATTERNS.search(text) or _SENSITIVE_PATHS.search(text):
            return RiskProfile.SECURITY_CRITICAL
        hits_high = len(_RISK_HIGH_PATTERNS.findall(text))
        hits_std = len(_RISK_STANDARD_PATTERNS.findall(text))
        hits_low = len(_RISK_LOW_PATTERNS.findall(text))
        if hits_high >= 2:
            return RiskProfile.STRICT
        if hits_high >= 1:
            return RiskProfile.STANDARD
        if hits_std >= 1:
            return RiskProfile.STANDARD
        if hits_low >= 1:
            return RiskProfile.LIGHT
        return RiskProfile.STANDARD

    def _detect_task_type(self, text: str) -> TaskType:
        for pattern, task_type in _TASK_TYPE_KEYWORDS:
            if pattern.search(text):
                return task_type
        return TaskType.IMPLEMENTATION

    def _detect_scopes(self, text: str) -> list[str]:
        scopes: list[str] = []
        if _NETWORK_SCOPE.search(text):
            scopes.append("network")
        if _MEMORY_SCOPE.search(text):
            scopes.append("memory")
        if _PACK_SCOPE.search(text):
            scopes.append("pack")
        if re.search(r"\b(file|path|dir|folder|filesystem|write|read)\b", text, re.IGNORECASE):
            scopes.append("filesystem")
        if re.search(r"\b(test|spec|coverage)\b", text, re.IGNORECASE):
            scopes.append("tests")
        return scopes or ["repo"]

    def _build_policy_hints(self, text: str, risk: RiskProfile) -> list[PolicyHint]:
        hints: list[PolicyHint] = []
        for pattern, rule_id, verdict in _POLICY_HINTS:
            if pattern.search(text):
                hints.append(PolicyHint(rule_id=rule_id, expected_verdict=verdict))
        if risk in (RiskProfile.STRICT, RiskProfile.SECURITY_CRITICAL):
            hints.append(PolicyHint(
                rule_id="no_destructive_without_strict",
                expected_verdict="block",
                reason=f"Risk profile is {risk.value}",
            ))
        return hints

    def _propose_tasks(self, text: str, primary_type: TaskType, risk: RiskProfile) -> list[TaskProposal]:
        proposals: list[TaskProposal] = [
            TaskProposal(
                title=self._extract_title(text),
                task_type=primary_type,
                risk_profile=risk,
                rationale="Primary task detected from intent",
            )
        ]
        # Auto-suggest a TEST task if implementation detected and no test keywords present
        if primary_type == TaskType.IMPLEMENTATION and not re.search(r"\btest\b", text, re.IGNORECASE):
            proposals.append(TaskProposal(
                title=f"Write tests for: {self._extract_title(text)}",
                task_type=TaskType.TEST,
                risk_profile=RiskProfile.LIGHT,
                rationale="Auto-suggested: implementation tasks require test evidence",
            ))
        return proposals

    def _extract_title(self, text: str) -> str:
        # Take first sentence (up to 80 chars), strip trailing punctuation
        first = re.split(r"[.!?\n]", text.strip())[0].strip()
        if len(first) > 80:
            first = first[:77] + "..."
        return first or text[:80]

    def _detect_mission_type(self, task_type: TaskType) -> str:
        mapping = {
            TaskType.ANALYSIS: "analysis",
            TaskType.ARCHITECTURE: "architecture",
            TaskType.IMPLEMENTATION: "implementation",
            TaskType.TEST: "quality",
            TaskType.DOCUMENTATION: "documentation",
            TaskType.MIGRATION: "migration",
            TaskType.SECURITY: "security",
            TaskType.OPERATION: "operations",
            TaskType.CLEANUP: "maintenance",
        }
        return mapping.get(task_type, "implementation")

    def _confidence_score(self, text: str, task_type: TaskType, risk: RiskProfile) -> float:
        """Heuristic confidence (0.0–1.0) based on signal strength."""
        score = 0.5
        word_count = len(text.split())
        if word_count >= 10:
            score += 0.1
        if word_count >= 30:
            score += 0.1
        # Strong keyword match → higher confidence
        if any(p.search(text) for p, _ in _TASK_TYPE_KEYWORDS):
            score += 0.2
        if risk != RiskProfile.STANDARD:
            score += 0.1
        return min(1.0, score)
