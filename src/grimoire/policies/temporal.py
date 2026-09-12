"""Temporal policies: per-session budgets, prior approval, cooldowns.

Point 3 of the 2026-09-12 positioning audit
(``docs/audits/positionnement-2026-09-12.md``): the industry references in
``framework/agentic-industry-reference.md`` (sections 5 and 10 — AgentCore
Dogwood, the OWASP Agent Control Standard) express policy as more than a
static rule table: prior approval for some action classes, per-session
counters and budgets, cooldown windows. ``grimoire.policies.engine`` and its
``_BUILTIN_RULES`` had none of that — every verdict was a pure function of
the request, with no notion of "this session" at all.

This module adds exactly that, without touching :class:`PolicyEngine`'s
existing contract: a :class:`~grimoire.policies.schemas.PolicyRule` that sets
``require_approval``, ``per_session`` or ``cooldown_after`` (see
``schemas.py``) becomes a *temporal* rule, evaluated here against a
:class:`~grimoire.policies.session_state.SessionState` in addition to the
engine's own request-only evaluation. A rule with none of the three fields
set is invisible to this module — the pre-existing engine behaviour for
every rule defined before this change.

Backend
-------
Same split as ``engine.py``: :func:`evaluate_temporal` decides, per call,
between the pure-Python reference loop (:func:`_evaluate_python`) and the
compiled Rust core (:func:`_evaluate_rust`, ``grimoire_policies_core``) using
the same ``GRIMOIRE_POLICIES_BACKEND`` override — see
``rust/grimoire-policies-core/src/lib.rs`` for the mirrored decision logic,
which is the oracle: the pure rule+state→verdict function is written once in
Rust and once in Python, tested for parity
(``tests/unit/test_policies_rust_parity.py``), and *this* module is the only
caller. Persistence (:mod:`grimoire.policies.session_state`) stays Python on
both backends — only the decision crosses into Rust.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from grimoire.policies.engine import _use_rust_backend, rust_core_module
from grimoire.policies.schemas import MatchedRule, PolicyRule, VerdictKind
from grimoire.policies.session_state import RuleState, SessionState

if TYPE_CHECKING:
    from collections.abc import Sequence

# Severity order, identical to engine.py's _SEVERITY — kept private here too
# since the two moduless never need to compare across each other's tuples.
_SEVERITY: dict[VerdictKind, int] = {VerdictKind.ALLOW: 0, VerdictKind.WARN: 1, VerdictKind.BLOCK: 2}


def glob_match(pattern: str, text: str) -> bool:
    """``*`` is the only wildcard — deliberately narrower than :mod:`fnmatch`.

    A full glob (character classes, ``?``) would need its own mirror in the
    Rust core (see the module docstring) for no expressive gain a tool-name
    pattern ever needs: ``Bash(rm:*)``, ``mcp__*__write*``, ``*`` for "every
    tool". Matching is case-sensitive, like tool names themselves.
    """
    if pattern == "*" or pattern == text:
        return True
    if "*" not in pattern:
        return False
    parts = pattern.split("*")
    if not text.startswith(parts[0]):
        return False
    if not text.endswith(parts[-1]):
        return False
    cursor = len(parts[0])
    end = len(text) - len(parts[-1]) if parts[-1] else len(text)
    for part in parts[1:-1]:
        if not part:
            continue
        idx = text.find(part, cursor, end)
        if idx == -1:
            return False
        cursor = idx + len(part)
    return cursor <= end


@dataclass(frozen=True, slots=True)
class TemporalDecision:
    """What :func:`evaluate_temporal` hands back to ``tool_policy.py``."""

    verdict: VerdictKind
    reason: str
    matched_rules: tuple[MatchedRule, ...]
    state: SessionState
    """The (mutated) session state — the caller persists it with
    :func:`grimoire.policies.session_state.save_session_state`."""


def _duration_minutes(started_at: str, now: datetime) -> float:
    try:
        started = datetime.fromisoformat(started_at)
    except ValueError:
        return 0.0
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return max(0.0, (now - started).total_seconds() / 60.0)


def _hits_in_window(hits: list[str], now: datetime, minutes: float) -> int:
    threshold = now.timestamp() - minutes * 60.0
    count = 0
    for hit in hits:
        try:
            ts = datetime.fromisoformat(hit)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts.timestamp() >= threshold:
            count += 1
    return count


def _evaluate_one_rule(
    rule: PolicyRule,
    rule_state: RuleState,
    *,
    tool_name: str,
    is_write: bool,
    now: datetime,
    session_started_at: str,
) -> tuple[VerdictKind, str, bool]:
    """One temporal rule against its own counters. Returns ``(verdict, reason, record_hit)``.

    Mutates *rule_state* in place for the branches that proceed (allow or
    ask): a refused call (cooldown or budget already at its ceiling) leaves
    counters untouched, since it never ran. ``record_hit`` tells the caller
    whether this attempt should be appended to the cooldown window — true for
    every branch except an already-active cooldown, so the window reflects
    calls that were actually attempted while it was cooling down too.
    """
    # 1) Cooldown — the most immediate, rate-limit-shaped refusal.
    cooldown = rule.cooldown_after
    if cooldown is not None and glob_match(cooldown.pattern, tool_name):
        hits = _hits_in_window(rule_state.hits, now, cooldown.minutes)
        if hits >= cooldown.count:
            return (
                VerdictKind.BLOCK,
                f"Refroidissement actif pour {cooldown.pattern!r} "
                f"({cooldown.count} appels en {cooldown.minutes:g} min) — réessaie plus tard",
                True,
            )

    # 2) Per-session budgets — the ceiling already reached, not the one about to be.
    budget = rule.per_session
    if budget is not None:
        if budget.max_tool_calls is not None and rule_state.calls >= budget.max_tool_calls:
            return (
                VerdictKind.BLOCK,
                f"Budget de {budget.max_tool_calls} appels d'outil atteint pour cette session",
                False,
            )
        if is_write and budget.max_writes is not None and rule_state.writes >= budget.max_writes:
            return (
                VerdictKind.BLOCK,
                f"Budget de {budget.max_writes} écritures atteint pour cette session",
                False,
            )
        if budget.max_cost_usd is not None and rule_state.cost_usd >= budget.max_cost_usd:
            return (
                VerdictKind.BLOCK,
                f"Budget de {budget.max_cost_usd:g} $ atteint pour cette session",
                False,
            )
        if budget.max_duration_min is not None:
            elapsed = _duration_minutes(session_started_at, now)
            if elapsed >= budget.max_duration_min:
                return (
                    VerdictKind.BLOCK,
                    f"Fenêtre de {budget.max_duration_min:g} min dépassée pour cette session",
                    False,
                )

    # 3) Prior approval — first occurrence in the session only.
    verdict = VerdictKind.ALLOW
    reason = ""
    if rule.require_approval and not rule_state.approved:
        verdict = VerdictKind.WARN
        reason = f"Approbation requise pour {tool_name!r} — première occurrence dans cette session"
        rule_state.approved = True

    # Record the attempt: every non-cooldown-refused call, whether it
    # allows, asks, or is refused by a budget — a budget refusal still
    # counts as "the rule was invoked", useful for `grimoire policies status`.
    rule_state.calls += 1
    if is_write:
        rule_state.writes += 1
    rule_state.cost_usd += rule.estimated_cost_usd
    return verdict, reason, True


def _evaluate_python(
    rules: Sequence[PolicyRule],
    state: SessionState,
    *,
    tool_name: str,
    is_write: bool,
    now: datetime,
) -> tuple[VerdictKind, str, tuple[MatchedRule, ...]]:
    matched: list[MatchedRule] = []
    effective = VerdictKind.ALLOW
    reason = ""
    now_iso = now.isoformat()
    for rule in rules:
        if not rule.is_temporal or not glob_match(rule.tool_pattern, tool_name):
            continue
        rule_state = state.rule_state(rule.id)
        verdict, rule_reason, record_hit = _evaluate_one_rule(
            rule,
            rule_state,
            tool_name=tool_name,
            is_write=is_write,
            now=now,
            session_started_at=state.started_at,
        )
        if record_hit:
            rule_state.hits.append(now_iso)
        if verdict is not VerdictKind.ALLOW or rule_reason:
            matched.append(MatchedRule(rule_id=rule.id, verdict=verdict, reason=rule_reason))
        if _SEVERITY[verdict] > _SEVERITY[effective]:
            effective = verdict
            reason = rule_reason
    return effective, reason, tuple(matched)


def _evaluate_rust(
    rules: Sequence[PolicyRule],
    state: SessionState,
    *,
    tool_name: str,
    is_write: bool,
    now: datetime,
) -> tuple[VerdictKind, str, tuple[MatchedRule, ...]]:
    _rust_core = rust_core_module()
    assert _rust_core is not None
    temporal_rules = [rule for rule in rules if rule.is_temporal]
    rule_tuples = [
        (
            rule.id,
            rule.tool_pattern,
            rule.require_approval,
            rule.per_session.max_tool_calls if rule.per_session else None,
            rule.per_session.max_writes if rule.per_session else None,
            rule.per_session.max_cost_usd if rule.per_session else None,
            rule.per_session.max_duration_min if rule.per_session else None,
            rule.cooldown_after.pattern if rule.cooldown_after else "",
            rule.cooldown_after.count if rule.cooldown_after else 0,
            rule.cooldown_after.minutes if rule.cooldown_after else 0.0,
            rule.estimated_cost_usd,
        )
        for rule in temporal_rules
    ]
    state_tuples = [
        (
            rule.id,
            (rs := state.rule_state(rule.id)).calls,
            rs.writes,
            rs.cost_usd,
            rs.approved,
            [_iso_to_epoch(h, now) for h in rs.hits],
        )
        for rule in temporal_rules
    ]
    # Epoch seconds cross the FFI boundary, never ISO strings: the Rust core
    # (see lib.rs) has no date-parsing dependency, by design, so every
    # timestamp is a float on both sides of the call and converted back to
    # ISO only here, on the way out.
    verdict_str, reason, matched_raw, deltas = _rust_core.evaluate_temporal(
        rule_tuples,
        state_tuples,
        tool_name,
        is_write,
        now.timestamp(),
        _iso_to_epoch(state.started_at, now),
    )
    for rule_id, calls, writes, cost_usd, approved, hits_epoch in deltas:
        rs = state.rule_state(rule_id)
        rs.calls = calls
        rs.writes = writes
        rs.cost_usd = cost_usd
        rs.approved = approved
        rs.hits = [datetime.fromtimestamp(h, tz=UTC).isoformat() for h in hits_epoch]
    matched = tuple(
        MatchedRule(rule_id=rule_id, verdict=VerdictKind(verdict), reason=reason_text)
        for rule_id, verdict, reason_text in matched_raw
    )
    return VerdictKind(verdict_str), reason, matched


def _iso_to_epoch(value: str, now: datetime) -> float:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return now.timestamp()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def evaluate_temporal(
    rules: Sequence[PolicyRule],
    state: SessionState,
    *,
    tool_name: str,
    is_write: bool,
    now: datetime | None = None,
) -> TemporalDecision:
    """Evaluate every temporal rule in *rules* against *state* for one call.

    Mutates and returns *state*; the caller (:mod:`grimoire.hosts.decisions.tool_policy`)
    is responsible for persisting it with
    :func:`grimoire.policies.session_state.save_session_state` — this
    function never touches disk, so it stays trivially testable and is the
    exact function exercised by the Rust parity tests.
    """
    now = now or datetime.now(UTC)
    if _use_rust_backend():
        verdict, reason, matched = _evaluate_rust(rules, state, tool_name=tool_name, is_write=is_write, now=now)
    else:
        verdict, reason, matched = _evaluate_python(rules, state, tool_name=tool_name, is_write=is_write, now=now)
    return TemporalDecision(verdict=verdict, reason=reason, matched_rules=matched, state=state)
