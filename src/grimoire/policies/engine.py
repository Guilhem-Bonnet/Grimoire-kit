"""Policy Engine — evaluate requests against registered rules and emit verdicts.

Design: rule registry + evaluate() → PolicyVerdict.
Rules are evaluated in order; the most restrictive verdict wins (block > warn > allow).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.policies.schemas import (
    ActionKind,
    MatchedRule,
    MutationClass,
    PolicyMode,
    PolicyRequest,
    PolicyRule,
    PolicyVerdict,
    VerdictKind,
)

# Severity order for verdict escalation
_SEVERITY: dict[VerdictKind, int] = {
    VerdictKind.ALLOW: 0,
    VerdictKind.WARN: 1,
    VerdictKind.BLOCK: 2,
}

# Built-in rules (sensible defaults)
_BUILTIN_RULES: list[PolicyRule] = [
    PolicyRule(
        id="no-destructive-without-strict",
        description="Destructive mutations require strict or higher risk profile",
        action_kinds=(),
        mutation_classes=(MutationClass.DESTRUCTIVE,),
        risk_profiles=("light", "standard"),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="Destructive mutation requires strict risk profile",
    ),
    PolicyRule(
        id="pack-activation-requires-evidence",
        description="Pack activation requires prior evidence (lock + doctor)",
        action_kinds=(ActionKind.PACK_ACTIVATION,),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="Pack activation requires pack.lock and doctor success evidence",
    ),
    PolicyRule(
        id="task-close-requires-verification",
        description="Task closure requires needs_verification status and evidence",
        action_kinds=(ActionKind.TASK_CLOSE,),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.WARN,
        reason_template="Closing a task requires verified evidence; transition to needs_verification first",
    ),
    PolicyRule(
        id="secret-access-always-block",
        description="Secret access is always blocked",
        action_kinds=(ActionKind.SECRET_ACCESS,),
        mutation_classes=(),
        risk_profiles=(),
        verdict_on_match=VerdictKind.BLOCK,
        reason_template="Secret access must be explicitly authorised via host capability manifest",
    ),
]


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


class PolicyEngine:
    """Evaluates PolicyRequests against a rule set and emits PolicyVerdicts.

    Usage::

        engine = PolicyEngine(mode=PolicyMode.ENFORCED)
        verdict = engine.evaluate(request)
        if verdict.verdict == VerdictKind.BLOCK:
            raise GrimoirePolicyError(verdict.reason)
    """

    def __init__(self, mode: PolicyMode = PolicyMode.ENFORCED, include_builtins: bool = True) -> None:
        self._mode = mode
        self._rules: list[PolicyRule] = list(_BUILTIN_RULES) if include_builtins else []

    def register_rule(self, rule: PolicyRule) -> None:
        for existing in self._rules:
            if existing.id == rule.id:
                raise GrimoirePolicyError(f"Policy rule already registered: {rule.id}")
        self._rules.append(rule)

    def remove_rule(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.id != rule_id]
        return len(self._rules) < before

    def evaluate(self, request: PolicyRequest) -> PolicyVerdict:
        """Evaluate a request against all registered rules.

        The effective verdict is the most restrictive match (block > warn > allow).
        In shadow mode, blocks are downgraded to warns.
        """
        matched: list[MatchedRule] = []
        effective_verdict = VerdictKind.ALLOW
        primary_reason = "No rules matched — allowed by default"
        retry_hints: list[str] = []

        for rule in self._rules:
            if not rule.matches(request):
                continue
            matched.append(MatchedRule(
                rule_id=rule.id,
                verdict=rule.verdict_on_match,
                reason=rule.reason_template,
            ))
            if _SEVERITY[rule.verdict_on_match] > _SEVERITY[effective_verdict]:
                effective_verdict = rule.verdict_on_match
                primary_reason = rule.reason_template

        # Shadow mode: never block, only warn
        if self._mode == PolicyMode.SHADOW and effective_verdict == VerdictKind.BLOCK:
            effective_verdict = VerdictKind.WARN
            primary_reason = f"[shadow] {primary_reason}"

        # Collect retry hints from matched block rules
        retry_hints = [r.reason for r in matched if r.verdict == VerdictKind.BLOCK]

        verdict_id = f"POL-{request.run_id}-{uuid.uuid4().hex[:6]}"
        return PolicyVerdict(
            id=verdict_id,
            request_id=request.id,
            run_id=request.run_id,
            verdict=effective_verdict,
            mode=self._mode,
            reason=primary_reason,
            created_at=_now_iso(),
            matched_rules=tuple(matched),
            allow_retry_after=tuple(retry_hints),
        )

    def evaluate_or_raise(self, request: PolicyRequest) -> PolicyVerdict:
        """Evaluate and raise GrimoirePolicyError on block in enforced mode."""
        verdict = self.evaluate(request)
        if verdict.verdict == VerdictKind.BLOCK and self._mode == PolicyMode.ENFORCED:
            raise GrimoirePolicyError(
                f"Policy blocked action {request.action.tool}: {verdict.reason}",
                error_code="GR-POL-001",
            )
        return verdict

    @property
    def mode(self) -> PolicyMode:
        return self._mode

    @mode.setter
    def mode(self, value: PolicyMode) -> None:
        self._mode = value

    def rules(self) -> list[PolicyRule]:
        return list(self._rules)
