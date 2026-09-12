"""Policy request, verdict, and rule schemas."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from grimoire.core.exceptions import GrimoirePolicyError


class VerdictKind(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"


class PolicyMode(StrEnum):
    SHADOW = "shadow"
    CANARY = "canary"
    ENFORCED = "enforced"


class ActionKind(StrEnum):
    TOOL_USE = "tool_use"
    FILE_WRITE = "file_write"
    NETWORK = "network"
    SECRET_ACCESS = "secret_access"  # noqa: S105 - policy action kind, not a credential.
    PACK_ACTIVATION = "pack_activation"
    TASK_CLOSE = "task_close"
    MISSION_CLOSE = "mission_close"


class MutationClass(StrEnum):
    READ_ONLY = "read_only"
    MUTATION_CONTROLLED = "mutation_controlled"
    PACK_ACTIVATION = "pack_activation"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True, slots=True)
class PolicyActor:
    actor_id: str
    host_id: str

    def to_dict(self) -> dict[str, Any]:
        return {"actor_id": self.actor_id, "host_id": self.host_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyActor:
        return cls(actor_id=d["actor_id"], host_id=d["host_id"])


@dataclass(frozen=True, slots=True)
class PolicyAction:
    kind: ActionKind
    tool: str
    mutation_class: MutationClass
    command: str = ""
    target_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "tool": self.tool,
            "mutation_class": self.mutation_class.value,
            "command": self.command,
            "target_files": list(self.target_files),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyAction:
        return cls(
            kind=ActionKind(d["kind"]),
            tool=d["tool"],
            mutation_class=MutationClass(d.get("mutation_class", "read_only")),
            command=d.get("command", ""),
            target_files=tuple(d.get("target_files", [])),
        )


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    id: str
    run_id: str
    task_id: str
    actor: PolicyActor
    action: PolicyAction
    risk_profile: str
    created_at: str
    schema_version: str = "grimoire.policy_request.v1"
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "actor": self.actor.to_dict(),
            "action": self.action.to_dict(),
            "risk_profile": self.risk_profile,
            "context": dict(self.context),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyRequest:
        return cls(
            id=d["id"],
            run_id=d["run_id"],
            task_id=d["task_id"],
            actor=PolicyActor.from_dict(d["actor"]),
            action=PolicyAction.from_dict(d["action"]),
            risk_profile=d["risk_profile"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.policy_request.v1"),
            context=d.get("context", {}),
        )


@dataclass(frozen=True, slots=True)
class SessionBudget:
    """A per-session ceiling a :class:`PolicyRule` enforces (issue #429, point 3).

    Every field is optional: a budget only constrains the dimensions it
    names, exactly like :class:`PolicyRule`'s own ``action_kinds`` /
    ``mutation_classes`` / ``risk_profiles`` (empty/``None`` = unconstrained).
    Limits are ceilings the session must stay *under*: the (N+1)th call past
    a limit of N is the one that gets refused, not the Nth.
    """

    max_tool_calls: int | None = None
    max_writes: int | None = None
    max_cost_usd: float | None = None
    max_duration_min: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tool_calls": self.max_tool_calls,
            "max_writes": self.max_writes,
            "max_cost_usd": self.max_cost_usd,
            "max_duration_min": self.max_duration_min,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionBudget:
        _reject_unknown_keys(
            d, {"max_tool_calls", "max_writes", "max_cost_usd", "max_duration_min"}, context="per_session"
        )
        return cls(
            max_tool_calls=d.get("max_tool_calls"),
            max_writes=d.get("max_writes"),
            max_cost_usd=d.get("max_cost_usd"),
            max_duration_min=d.get("max_duration_min"),
        )


@dataclass(frozen=True, slots=True)
class CooldownRule:
    """After *count* matches of *pattern* within a *minutes* window, refuse further matches.

    ``pattern`` is a glob against the tool name — ``*`` is the only wildcard
    (see :func:`grimoire.policies.temporal.glob_match`); this is deliberately
    narrower than :mod:`fnmatch` so the Rust core (the decision oracle, see
    ``rust/grimoire-policies-core``) can mirror it exactly without pulling in
    a full glob library.
    """

    pattern: str
    count: int
    minutes: float

    def to_dict(self) -> dict[str, Any]:
        return {"pattern": self.pattern, "count": self.count, "minutes": self.minutes}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CooldownRule:
        _reject_unknown_keys(d, {"pattern", "count", "minutes"}, context="cooldown_after")
        try:
            return cls(pattern=str(d["pattern"]), count=int(d["count"]), minutes=float(d["minutes"]))
        except KeyError as exc:
            raise GrimoirePolicyError(
                f"cooldown_after: champ manquant {exc}", error_code="GR-POL-002"
            ) from exc


def _reject_unknown_keys(d: dict[str, Any], known: set[str], *, context: str) -> None:
    """Raise a named error on any key ``d`` carries outside ``known``.

    Point 3 of issue #429 requires "validation au chargement, erreur nommée
    sur clé inconnue" for the new declarative temporal keys — a typo in a
    rule's YAML/dict definition must fail loudly at load time, not silently
    match nothing at evaluation time.
    """
    unknown = sorted(set(d) - known)
    if unknown:
        raise GrimoirePolicyError(
            f"{context}: clé(s) inconnue(s) {unknown} (attendu : {sorted(known)})",
            error_code="GR-POL-002",
        )


#: The keys a rule dict may carry — the original six plus the temporal ones
#: added by issue #429. Any other key is a load-time error (see
#: ``_reject_unknown_keys``), which is what makes the format's own evolution
#: safe: a rule saved by a newer kit and loaded by an older one fails to
#: parse instead of silently dropping the field it does not recognise.
_POLICY_RULE_KEYS = {
    "id",
    "description",
    "action_kinds",
    "mutation_classes",
    "risk_profiles",
    "verdict_on_match",
    "reason_template",
    "tool_pattern",
    "require_approval",
    "per_session",
    "cooldown_after",
    "estimated_cost_usd",
}


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """A single policy rule with an evaluator function signature.

    Rules are registered in PolicyEngine and evaluated against PolicyRequest.

    The last four fields are the temporal extension from issue #429 (point 3
    of the 2026-09-12 positioning audit): they are all optional and default
    to "no temporal constraint", so every rule defined before this change
    keeps behaving exactly as before, in both ``to_dict``/``from_dict`` and
    the engine's base ``evaluate()`` loop, which never reads them. They are
    read only by :mod:`grimoire.policies.temporal`, invoked from
    :mod:`grimoire.hosts.decisions.tool_policy` once a session id is
    available.

    - ``tool_pattern``: glob (``*`` only) against the tool name a temporal
      check applies to; ``"*"`` (the default) means "every tool", i.e. a
      session-global budget rather than a per-tool one.
    - ``require_approval``: the first time this rule matches in a session,
      the verdict is ``warn`` (mapped to the host's ``ask`` outcome by
      ``tool_policy.py``) instead of ``allow``; every later match in the same
      session allows silently. A fresh session (see
      :mod:`grimoire.policies.session_state`) asks again.
    - ``per_session``: a :class:`SessionBudget` this rule enforces.
    - ``cooldown_after``: a :class:`CooldownRule` this rule enforces.
    - ``estimated_cost_usd``: the cost (in USD) one matching call adds to the
      session's running total, for ``per_session.max_cost_usd`` — the engine
      has no other source of per-call cost, so a rule that cares about a
      cost budget must say what its own calls are worth.
    """

    id: str
    description: str
    action_kinds: tuple[ActionKind, ...]
    mutation_classes: tuple[MutationClass, ...]
    risk_profiles: tuple[str, ...]
    verdict_on_match: VerdictKind
    reason_template: str = ""
    tool_pattern: str = "*"
    require_approval: bool = False
    per_session: SessionBudget | None = None
    cooldown_after: CooldownRule | None = None
    estimated_cost_usd: float = 0.0

    def matches(self, request: PolicyRequest) -> bool:
        kind_ok = not self.action_kinds or request.action.kind in self.action_kinds
        mutation_ok = not self.mutation_classes or request.action.mutation_class in self.mutation_classes
        profile_ok = not self.risk_profiles or request.risk_profile in self.risk_profiles
        return kind_ok and mutation_ok and profile_ok

    @property
    def is_temporal(self) -> bool:
        """Whether this rule carries any of the point-3 temporal constraints."""
        return bool(self.require_approval or self.per_session is not None or self.cooldown_after is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "action_kinds": [k.value for k in self.action_kinds],
            "mutation_classes": [m.value for m in self.mutation_classes],
            "risk_profiles": list(self.risk_profiles),
            "verdict_on_match": self.verdict_on_match.value,
            "reason_template": self.reason_template,
            "tool_pattern": self.tool_pattern,
            "require_approval": self.require_approval,
            "per_session": self.per_session.to_dict() if self.per_session is not None else None,
            "cooldown_after": self.cooldown_after.to_dict() if self.cooldown_after is not None else None,
            "estimated_cost_usd": self.estimated_cost_usd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyRule:
        """Parse a rule dict — the same shape :meth:`to_dict` writes.

        Unknown keys are rejected by name (see ``_reject_unknown_keys``) so a
        typo (``per_sesion``, ``requires_approval``) fails at load time
        instead of matching nothing forever.
        """
        _reject_unknown_keys(d, _POLICY_RULE_KEYS, context=f"PolicyRule {d.get('id', '?')!r}")
        for required in ("id", "verdict_on_match"):
            if required not in d:
                raise GrimoirePolicyError(
                    f"PolicyRule: champ requis manquant {required!r}", error_code="GR-POL-002"
                )
        per_session_raw = d.get("per_session")
        cooldown_raw = d.get("cooldown_after")
        return cls(
            id=d["id"],
            description=d.get("description", ""),
            action_kinds=tuple(ActionKind(k) for k in d.get("action_kinds", ())),
            mutation_classes=tuple(MutationClass(m) for m in d.get("mutation_classes", ())),
            risk_profiles=tuple(d.get("risk_profiles", ())),
            verdict_on_match=VerdictKind(d["verdict_on_match"]),
            reason_template=d.get("reason_template", ""),
            tool_pattern=d.get("tool_pattern", "*"),
            require_approval=bool(d.get("require_approval", False)),
            per_session=SessionBudget.from_dict(per_session_raw) if per_session_raw else None,
            cooldown_after=CooldownRule.from_dict(cooldown_raw) if cooldown_raw else None,
            estimated_cost_usd=float(d.get("estimated_cost_usd", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class MatchedRule:
    rule_id: str
    verdict: VerdictKind
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule_id": self.rule_id, "verdict": self.verdict.value, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class PolicyVerdict:
    id: str
    request_id: str
    run_id: str
    verdict: VerdictKind
    mode: PolicyMode
    reason: str
    created_at: str
    schema_version: str = "grimoire.policy_verdict.v1"
    matched_rules: tuple[MatchedRule, ...] = ()
    allow_retry_after: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "verdict": self.verdict.value,
            "mode": self.mode.value,
            "reason": self.reason,
            "rules": {"matched": [r.to_dict() for r in self.matched_rules]},
            "allow_retry_after": list(self.allow_retry_after),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyVerdict:
        rules_raw = d.get("rules", {})
        matched = [
            MatchedRule(rule_id=r["rule_id"], verdict=VerdictKind(r["verdict"]), reason=r.get("reason", ""))
            for r in rules_raw.get("matched", [])
        ]
        return cls(
            id=d["id"],
            request_id=d["request_id"],
            run_id=d["run_id"],
            verdict=VerdictKind(d["verdict"]),
            mode=PolicyMode(d["mode"]),
            reason=d["reason"],
            created_at=d["created_at"],
            schema_version=d.get("schema_version", "grimoire.policy_verdict.v1"),
            matched_rules=tuple(matched),
            allow_retry_after=tuple(d.get("allow_retry_after", [])),
        )
