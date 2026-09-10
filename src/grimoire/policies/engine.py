"""Policy Engine — evaluate requests against registered rules and emit verdicts.

Design: rule registry + evaluate() → PolicyVerdict.
Rules are evaluated in order; the most restrictive verdict wins (block > warn > allow).

Backend
-------
The matching loop below (``_evaluate_python``) is the reference implementation
and the only one guaranteed to exist. ``grimoire_policies_core`` is an
optional, PyO3-compiled Rust port of that same loop (see
``rust/grimoire-policies-core/``, issue #354). It is never required: nothing
in the published distribution depends on it, it ships no compiled wheel, and
if the import below fails ``PolicyEngine`` runs the pure-Python path exactly
as before — silently, with no warning, because an absent optional build tool
is the expected case for almost every contributor.

When the compiled module *is* present (built locally with ``maturin
develop``, see CONTRIBUTING.md, or in the dedicated CI job), ``evaluate()``
uses it by default. The ``GRIMOIRE_POLICIES_BACKEND`` environment variable
overrides that choice in both directions — ``"python"`` forces the reference
implementation even when the compiled module is loaded, ``"rust"`` forces the
compiled module and raises :class:`~grimoire.core.exceptions.GrimoirePolicyError`
if it is not available. This is what makes the two implementations testable
against each other (see ``tests/unit/test_policies_rust_parity.py``) without
touching ``PolicyEngine``'s constructor or any other public surface.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from collections.abc import Sequence

try:
    import grimoire_policies_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None

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


def rust_backend_available() -> bool:
    """Whether the compiled ``grimoire_policies_core`` module is importable.

    Purely informational (used by tests and diagnostics) — ``PolicyEngine``
    itself decides its backend on every call via :func:`_use_rust_backend`.
    """
    return _rust_core is not None


def _use_rust_backend() -> bool:
    """Resolve which backend ``evaluate()`` should use for this call.

    Reads ``GRIMOIRE_POLICIES_BACKEND`` fresh every time rather than once at
    import time, so tests can flip it with ``monkeypatch.setenv`` around a
    single call without reloading the module.
    """
    override = os.environ.get("GRIMOIRE_POLICIES_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoirePolicyError(
                "GRIMOIRE_POLICIES_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_policies_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-policies-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoirePolicyError(f"GRIMOIRE_POLICIES_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None


def _evaluate_python(
    rules: Sequence[PolicyRule],
    mode: PolicyMode,
    request: PolicyRequest,
) -> tuple[VerdictKind, str, tuple[MatchedRule, ...], tuple[str, ...]]:
    """Reference implementation of the matching loop, in pure Python.

    Returns ``(effective_verdict, reason, matched_rules, retry_hints)`` —
    everything :meth:`PolicyEngine.evaluate` needs except the verdict's id
    and timestamp, which stay outside both backends (see module docstring).
    """
    matched: list[MatchedRule] = []
    effective_verdict = VerdictKind.ALLOW
    primary_reason = "No rules matched — allowed by default"

    for rule in rules:
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
    if mode == PolicyMode.SHADOW and effective_verdict == VerdictKind.BLOCK:
        effective_verdict = VerdictKind.WARN
        primary_reason = f"[shadow] {primary_reason}"

    # Collect retry hints from matched block rules
    retry_hints = tuple(r.reason for r in matched if r.verdict == VerdictKind.BLOCK)

    return effective_verdict, primary_reason, tuple(matched), retry_hints


def _evaluate_rust(
    rules: Sequence[PolicyRule],
    mode: PolicyMode,
    request: PolicyRequest,
) -> tuple[VerdictKind, str, tuple[MatchedRule, ...], tuple[str, ...]]:
    """Same contract as :func:`_evaluate_python`, delegated to the compiled core.

    Rules and the request are flattened to plain strings/lists — the shape
    ``grimoire_policies_core.evaluate`` accepts — since the Rust side owns no
    Python objects and re-parses every enum value itself (rejecting anything
    it does not recognise, see ``rust/grimoire-policies-core/src/lib.rs``).
    """
    assert _rust_core is not None  # guarded by _use_rust_backend before this is called
    rule_tuples = [
        (
            rule.id,
            [k.value for k in rule.action_kinds],
            [m.value for m in rule.mutation_classes],
            list(rule.risk_profiles),
            rule.verdict_on_match.value,
            rule.reason_template,
        )
        for rule in rules
    ]
    verdict_str, reason, matched_raw, retry_hints = _rust_core.evaluate(
        rule_tuples,
        mode.value,
        request.action.kind.value,
        request.action.mutation_class.value,
        request.risk_profile,
    )
    matched = tuple(
        MatchedRule(rule_id=rule_id, verdict=VerdictKind(verdict), reason=reason_text)
        for rule_id, verdict, reason_text in matched_raw
    )
    return VerdictKind(verdict_str), reason, matched, tuple(retry_hints)


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
        if _use_rust_backend():
            effective_verdict, primary_reason, matched, retry_hints = _evaluate_rust(
                self._rules, self._mode, request
            )
        else:
            effective_verdict, primary_reason, matched, retry_hints = _evaluate_python(
                self._rules, self._mode, request
            )

        verdict_id = f"POL-{request.run_id}-{uuid.uuid4().hex[:6]}"
        return PolicyVerdict(
            id=verdict_id,
            request_id=request.id,
            run_id=request.run_id,
            verdict=effective_verdict,
            mode=self._mode,
            reason=primary_reason,
            created_at=_now_iso(),
            matched_rules=matched,
            allow_retry_after=retry_hints,
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
