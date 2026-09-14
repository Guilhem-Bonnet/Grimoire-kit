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

import re
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


def tool_pattern_matches(pattern: str, tool_name: str, detail: str = "") -> bool:
    """Match a ``tool_pattern``/``cooldown_after.pattern`` against a pending call.

    Fixes the defect found in real use on Grimoire-Forge: every temporal
    pattern used to be compared to *only* ``tool_name`` via :func:`glob_match`
    — so a documented pattern like ``Bash(git push:*)`` was compared against
    the bare string ``"Bash"`` and could never match. ``require_approval:
    true`` on that pattern therefore answered ``allow`` on the very first
    occurrence: the announced guard did not exist.

    ``docs/hosts.md`` documents patterns in Claude Code's own permission
    shape: ``Bash(rm:*)``, ``Bash(git push:*)``, ``Write(_grimoire/standard/*)``.
    This function is the "tool key" this shape needs:

    - A pattern with no ``(...)`` — ``"*"``, ``"Bash"`` — is the pre-existing
      bare form and is still compared to *tool_name* alone via
      :func:`glob_match`: every rule declared before this fix keeps behaving
      exactly as before.
    - A parenthesised pattern is ``Tool(body)``. The ``Tool`` part is matched
      against *tool_name* (:func:`glob_match`, so ``*`` still works there
      too); ``body`` is then matched against *detail* — the full command
      line for a shell-shaped call, the target file for a file-shaped one
      (see :func:`grimoire.hosts.decisions.tool_facts.policy_tool_detail`).
      Within ``body``, a trailing ``:*`` — the same convention Claude Code's
      own permission syntax uses — means "starts with this command prefix,
      as a whole word": ``Bash(git push:*)`` matches ``git push origin
      main`` but not ``git pushx`` or ``git pull``. The ``:`` is a separator
      in the pattern, never a literal character *detail* must contain.
      Anywhere else, ``body`` is a plain :func:`glob_match` pattern against
      *detail* (e.g. ``Write(_grimoire/standard/*)``), and ``body == "*"``
      matches regardless of *detail* (including an empty one).
    - A call with no *detail* at all (``""`` — an MCP tool with no
      established argument convention yet, see ``policy_tool_detail``) can
      only match the bare-name form or a parenthesised pattern whose body is
      exactly ``"*"``.
    """
    if pattern == "*":
        return True
    open_paren = pattern.find("(")
    if open_paren == -1 or not pattern.endswith(")"):
        return glob_match(pattern, tool_name)
    name_part = pattern[:open_paren]
    body = pattern[open_paren + 1 : -1]
    if not glob_match(name_part, tool_name):
        return False
    if body == "*":
        return True
    if not detail:
        return False
    if body.endswith(":*"):
        prefix = body[:-2]
        return detail == prefix or detail.startswith(prefix + " ")
    return glob_match(body, detail)


#: Defect 2 of the 2026-09-12 session-budget incident (issue #463): a
#: ``per_session`` rule in ``block`` used to have no way back — once its
#: ceiling was reached it refused *every* matching tool, including the edit
#: that would raise the ceiling and the reads needed to even see the
#: problem. Scoped to ``per_session`` only, deliberately not
#: ``cooldown_after``: a cooldown is a bounded-duration refusal (it clears
#: itself after ``minutes``), never a standing block only an edit could
#: lift. A write whose target is one of these two files is always let
#: through: they are exactly the files a human or agent needs to touch to
#: lift the block (see ``docs/hosts.md``, "Garde-fou de conception").
#: Matched against ``tool_detail`` with :func:`glob_match` (a "contains"
#: check via its own ``*`` handling), not the whole pattern language of
#: :func:`tool_pattern_matches` — these are file targets, never tool names.
_REPAIR_EXEMPT_DETAIL_PATTERNS: tuple[str, ...] = (
    "*_grimoire/standard/policies.yaml*",
    "*_grimoire-output/.runs/session-*.json*",
)

#: Exact wording mirrored byte-for-byte in
#: ``rust/grimoire-policies-core/src/lib.rs`` — the Rust parity tests
#: (``tests/unit/test_policies_rust_parity.py``) compare this string, not
#: just the verdict.
REPAIR_EXEMPTION_REASON = "Exemption de réparation : lecture seule ou fichier de politique/état de session"

#: Appended to every budget-block reason so the refusal itself names its own
#: way out — defect 4 of the same incident: a session stuck on its own
#: budget had no hint that ``grimoire policies reset-session`` or editing
#: ``per_session`` in the rules file existed.
_BUDGET_REMEDY_SUFFIX = (
    " ; `grimoire policies reset-session` ou relever `per_session` dans `_grimoire/standard/policies.yaml`"
)


def _looks_like_grimoire_policies_command(tool_detail: str) -> bool:
    """Whether *tool_detail* is a ``Bash`` invocation of ``grimoire policies ...``.

    Relapse of the 2026-09-12 session-budget incident, found 2026-09-14 on
    Grimoire-Forge (issue #481): ``grimoire policies reset-session`` — the
    very command every budget-block reason recommends
    (:data:`_BUDGET_REMEDY_SUFFIX`) — is itself a ``Bash`` call whose first
    word (``grimoire``) is not one of :data:`~grimoire.hosts.decisions.tool_facts._READ_ONLY_LEADING_COMMANDS`,
    so :func:`~grimoire.hosts.decisions.tool_facts.is_read_only_command`
    classifies it as a mutation, and its command line never matches
    :data:`_REPAIR_EXEMPT_DETAIL_PATTERNS` (those are file-path globs, not
    command lines). A refusal that recommends an action the same refusal
    then refuses is a broken remedy, not a narrower one — this closes that
    gap without widening the exemption to every ``Bash`` call: only the
    invocation shape of ``grimoire policies`` itself.

    Recognises every shape seen in practice by looking only at the *first*
    word of each shell segment (split on ``&&``/``||``/``;``/``|``, the same
    boundary :func:`~grimoire.hosts.decisions.tool_facts.is_read_only_command`
    uses) — never a bare substring search: ``echo grimoire policies`` must
    stay refused, since it never invokes the command, only names it. The
    recognised first-word shapes are the bare command (``grimoire policies
    reset-session``), through a virtualenv's ``bin/`` (``/path/.venv/bin/grimoire
    policies status``), and via the module runner (``python -m grimoire
    policies reset-session``, ``python3 -m grimoire policies status``). The
    subcommand itself is never checked — ``status`` and ``reset-session``
    are the only two documented (``docs/hosts.md``, "Visibilité et remise à
    zéro"), and both are either read-only or self-limiting to the session's
    own state file, never a route to anything this exemption should guard
    against.
    """
    for segment in re.split(r"&&|\|\||;|\|", tool_detail):
        tokens = segment.split()
        if not tokens:
            continue
        basename = tokens[0].rsplit("/", 1)[-1]
        if basename == "grimoire" and tokens[1:2] == ["policies"]:
            return True
        if basename in ("python", "python3") and tokens[1:4] == ["-m", "grimoire", "policies"]:
            return True
    return False


def _is_repair_exempt(is_write: bool, tool_detail: str) -> bool:
    """Whether this call must always be let through a ``block``-ing ``per_session`` budget.

    True for any non-mutating call (``Read``/``Glob``/``Grep``, or — once
    defect 1's fix in :mod:`grimoire.hosts.decisions.tool_facts` classifies
    it correctly — a read-only ``Bash`` call like ``cat``/``grep``/``git
    status``), for a mutating call that targets the policy rule file or the
    session-state file itself, and — since the 2026-09-14 relapse (issue
    #481) — for any invocation of ``grimoire policies`` (``status``,
    ``reset-session``) regardless of how it is classified, since that
    command is the documented way out of exactly this block. Applies to
    every ``per_session`` dimension alike (``max_tool_calls``,
    ``max_writes``, ``max_cost_usd``, ``max_duration_min``) — this function
    is the single gate every one of them calls before refusing. Never for
    anything else: the exemption is named and narrow, not a general escape
    hatch for a stuck session.
    """
    if not is_write:
        return True
    if any(glob_match(pattern, tool_detail) for pattern in _REPAIR_EXEMPT_DETAIL_PATTERNS):
        return True
    return _looks_like_grimoire_policies_command(tool_detail)


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
    tool_detail: str,
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
    # 1) Cooldown — the most immediate, rate-limit-shaped refusal. No repair
    # exemption here: the incident and defect 2 (issue #463) are about
    # `per_session`, not `cooldown_after` — a cooldown is a bounded-duration
    # refusal (`minutes`), never a standing block only a `policies.yaml` edit
    # could lift.
    cooldown = rule.cooldown_after
    if cooldown is not None and tool_pattern_matches(cooldown.pattern, tool_name, tool_detail):
        hits = _hits_in_window(rule_state.hits, now, cooldown.minutes)
        if hits >= cooldown.count:
            return (
                VerdictKind.BLOCK,
                f"Refroidissement actif pour {cooldown.pattern!r} "
                f"({cooldown.count} appels en {cooldown.minutes:g} min) — réessaie plus tard",
                True,
            )

    # 2) Per-session budgets — the ceiling already reached, not the one about to be.
    # Defect 2 (issue #463): none of the four checks below ever refuses a call
    # `_is_repair_exempt` clears first — see that function's docstring.
    budget = rule.per_session
    if budget is not None:
        if budget.max_tool_calls is not None and rule_state.calls >= budget.max_tool_calls:
            if _is_repair_exempt(is_write, tool_detail):
                return VerdictKind.ALLOW, REPAIR_EXEMPTION_REASON, False
            return (
                VerdictKind.BLOCK,
                f"Budget de {budget.max_tool_calls} appels d'outil atteint pour cette session"
                f"{_BUDGET_REMEDY_SUFFIX}",
                False,
            )
        if is_write and budget.max_writes is not None and rule_state.writes >= budget.max_writes:
            if _is_repair_exempt(is_write, tool_detail):
                return VerdictKind.ALLOW, REPAIR_EXEMPTION_REASON, False
            return (
                VerdictKind.BLOCK,
                f"Budget de {budget.max_writes} écritures atteint pour cette session{_BUDGET_REMEDY_SUFFIX}",
                False,
            )
        if budget.max_cost_usd is not None and rule_state.cost_usd >= budget.max_cost_usd:
            if _is_repair_exempt(is_write, tool_detail):
                return VerdictKind.ALLOW, REPAIR_EXEMPTION_REASON, False
            return (
                VerdictKind.BLOCK,
                f"Budget de {budget.max_cost_usd:g} $ atteint pour cette session{_BUDGET_REMEDY_SUFFIX}",
                False,
            )
        if budget.max_duration_min is not None:
            elapsed = _duration_minutes(session_started_at, now)
            if elapsed >= budget.max_duration_min:
                if _is_repair_exempt(is_write, tool_detail):
                    return VerdictKind.ALLOW, REPAIR_EXEMPTION_REASON, False
                return (
                    VerdictKind.BLOCK,
                    f"Fenêtre de {budget.max_duration_min:g} min dépassée pour cette session"
                    f"{_BUDGET_REMEDY_SUFFIX}",
                    False,
                )

    # 3) Prior approval — asks until a PostToolUse for this pattern in this
    # session proves the call actually ran (see `record_post_tool_use_approval`
    # below). This function never marks a rule approved itself: PreToolUse
    # fires whether the host grants, refuses or has not yet answered the
    # prompt, so setting `approved` here on the mere *asking* would let a
    # refused prompt's retry fall through as `allow` — a guard that fails
    # open. Fixed 2026-09-12 after review of the first version of this file,
    # which did exactly that; see `test_a_refused_approval_keeps_asking`.
    verdict = VerdictKind.ALLOW
    reason = ""
    if rule.require_approval and not rule_state.approved:
        verdict = VerdictKind.WARN
        reason = f"Approbation requise pour {tool_name!r} — en attente de confirmation"

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
    tool_detail: str,
    is_write: bool,
    now: datetime,
) -> tuple[VerdictKind, str, tuple[MatchedRule, ...]]:
    matched: list[MatchedRule] = []
    effective = VerdictKind.ALLOW
    reason = ""
    now_iso = now.isoformat()
    for rule in rules:
        if not rule.is_temporal or not tool_pattern_matches(rule.tool_pattern, tool_name, tool_detail):
            continue
        rule_state = state.rule_state(rule.id)
        verdict, rule_reason, record_hit = _evaluate_one_rule(
            rule,
            rule_state,
            tool_name=tool_name,
            tool_detail=tool_detail,
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
    tool_detail: str,
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
        tool_detail,
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
    tool_detail: str = "",
    is_write: bool,
    now: datetime | None = None,
) -> TemporalDecision:
    """Evaluate every temporal rule in *rules* against *state* for one call.

    *tool_detail* is the command line or target file :func:`tool_pattern_matches`
    needs to resolve a parenthesised pattern like ``Bash(git push:*)`` — see
    :func:`grimoire.hosts.decisions.tool_facts.policy_tool_detail`. Left at
    ``""``, only bare-name patterns (``"*"``, ``"Bash"``) and a parenthesised
    pattern whose body is exactly ``"*"`` can match — never a false match, at
    worst a temporal rule that stays silent instead of misfiring.

    Mutates and returns *state*; the caller (:mod:`grimoire.hosts.decisions.tool_policy`)
    is responsible for persisting it with
    :func:`grimoire.policies.session_state.save_session_state` — this
    function never touches disk, so it stays trivially testable and is the
    exact function exercised by the Rust parity tests.
    """
    now = now or datetime.now(UTC)
    if _use_rust_backend():
        verdict, reason, matched = _evaluate_rust(
            rules, state, tool_name=tool_name, tool_detail=tool_detail, is_write=is_write, now=now
        )
    else:
        verdict, reason, matched = _evaluate_python(
            rules, state, tool_name=tool_name, tool_detail=tool_detail, is_write=is_write, now=now
        )
    return TemporalDecision(verdict=verdict, reason=reason, matched_rules=matched, state=state)


def _mark_approved_python(rules: Sequence[PolicyRule], tool_name: str, tool_detail: str) -> list[str]:
    return [rule.id for rule in rules if tool_pattern_matches(rule.tool_pattern, tool_name, tool_detail)]


def _mark_approved_rust(rules: Sequence[PolicyRule], tool_name: str, tool_detail: str) -> list[str]:
    _rust_core = rust_core_module()
    assert _rust_core is not None
    rule_tuples = [(rule.id, rule.tool_pattern) for rule in rules]
    return list(_rust_core.matching_approval_rule_ids(rule_tuples, tool_name, tool_detail))


def record_post_tool_use_approval(
    rules: Sequence[PolicyRule], state: SessionState, *, tool_name: str, tool_detail: str = ""
) -> bool:
    """Mark every ``require_approval`` rule matching *tool_name* as approved.

    The counterpart to the fix in :func:`_evaluate_one_rule`'s docstring:
    ``PostToolUse`` is the one event a host emits only when the tool actually
    ran — which for a ``require_approval`` rule means the human (or the
    host's own policy) said yes to the ``ask`` that ``PreToolUse`` raised.
    Called from :mod:`grimoire.hosts.decisions.evidence_trace` (the existing
    ``PostToolUse`` decision) once per real execution, never from the
    ``PreToolUse`` path.

    Mutates *state* in place and returns whether anything changed — the
    caller (the decision) only needs to persist the state when it did.
    Idempotent: marking an already-approved rule again is a no-op, and a
    non-matching or non-``require_approval`` rule is never touched.
    """
    approval_rules = tuple(rule for rule in rules if rule.require_approval)
    if not approval_rules:
        return False
    rule_ids = (
        _mark_approved_rust(approval_rules, tool_name, tool_detail)
        if _use_rust_backend()
        else _mark_approved_python(approval_rules, tool_name, tool_detail)
    )
    changed = False
    for rule_id in rule_ids:
        rule_state = state.rule_state(rule_id)
        if not rule_state.approved:
            rule_state.approved = True
            changed = True
    return changed
