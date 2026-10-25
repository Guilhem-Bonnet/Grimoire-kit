"""``PreToolUse``: run the pending call through the policy engine.

The heaviest decision in the package on purpose — it is the only one that
needs :mod:`grimoire.policies.engine`, :mod:`grimoire.policies.schemas` and
the policy request machinery below. Splitting :mod:`grimoire.hosts.decisions`
into a package (issue #419) means the other six decisions never import any
of it.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from grimoire.core.exceptions import GrimoirePolicyError
from grimoire.core.standard_state import active_profile_id, active_task_id, profile_floor_id, state_writes_allowed
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.profile_floor import session_profile
from grimoire.hosts.decisions.tool_facts import (
    ToolFacts,
    approval_fingerprint,
    classify_tool,
    command_surface,
    extract_c_bodies,
    policy_tool_detail,
)
from grimoire.policies.engine import _SEVERITY, PolicyEngine
from grimoire.policies.rules_config import load_custom_rules
from grimoire.policies.schemas import (
    MutationClass,
    PolicyAction,
    PolicyActor,
    PolicyRequest,
    PolicyRule,
    VerdictKind,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Standard profile -> policy risk profile. The standard grades *how much
#: evidence* a project owes; the policy engine grades *how much freedom* it
#: gets. A production project owes the most and gets the least.
_RISK_BY_PROFILE = {"starter": "light", "governed": "standard", "production": "strict"}


def _risk_profile(hook: HookInput) -> str:
    return _RISK_BY_PROFILE.get(session_profile(hook, journal=False), "light")


#: The engine's built-in ``no-destructive-without-strict`` rule blocks
#: destructive mutations below the strict profile — and therefore *allows* them
#: silently at strict, which is where the most damage is possible. A project
#: that owes the most evidence should not be the one where ``rm -rf`` passes
#: unremarked, so strict escalates to a confirmation instead of a green light.
_DESTRUCTIVE_AT_STRICT = PolicyRule(
    id="destructive-requires-confirmation",
    description="Destructive mutations always require explicit confirmation, strict profile included",
    action_kinds=(),
    mutation_classes=(MutationClass.DESTRUCTIVE,),
    risk_profiles=("strict",),
    verdict_on_match=VerdictKind.WARN,
    reason_template="Destructive mutation requires explicit confirmation",
)


def _engine(custom_rules: Sequence[PolicyRule] = ()) -> PolicyEngine:
    engine = PolicyEngine()
    engine.register_rule(_DESTRUCTIVE_AT_STRICT)
    for rule in custom_rules:
        engine.register_rule(rule)
    return engine


def _policy_request(hook: HookInput, facts: ToolFacts, task_id: str, risk: str) -> PolicyRequest:
    return PolicyRequest(
        id=f"req-{uuid.uuid4().hex[:12]}",
        run_id=hook.session_id or "session-unknown",
        task_id=task_id,
        actor=PolicyActor(
            actor_id=hook.agent_name or "agent", host_id=os.environ.get("GRIMOIRE_HOST_ID", "host-unknown")
        ),
        action=PolicyAction(
            kind=facts.kind,
            tool=hook.tool_name or "unknown",
            mutation_class=facts.mutation,
            command=facts.command,
            target_files=facts.targets,
        ),
        risk_profile=risk,
        created_at=datetime.now(UTC).isoformat(),
        context={"event": hook.event.value},
    )


def _evaluate_temporal_layer(
    hook: HookInput,
    custom_rules: tuple[PolicyRule, ...],
    facts: ToolFacts,
) -> tuple[VerdictKind, str, list[str]]:
    """Point 3 (issue #429): budgets, prior approval, cooldowns — session-scoped.

    Returns ``(verdict, reason, matched_rule_ids)`` — ``verdict`` is
    ``VerdictKind.ALLOW`` with an empty reason when no temporal rule is
    declared, no rule matches this call, or the host sent no
    ``session_id`` (there is nothing to key a session's state on; see
    :mod:`grimoire.policies.session_state`). Never raises: a corrupted or
    unwritable session-state file degrades to "new session", exactly like
    :func:`grimoire.policies.session_state.load_session_state` documents.
    """
    temporal_rules = tuple(rule for rule in custom_rules if rule.is_temporal)
    if not temporal_rules or not hook.session_id:
        return VerdictKind.ALLOW, "", []

    from grimoire.policies.session_state import load_session_state, save_session_state
    from grimoire.policies.temporal import evaluate_temporal, remember_pending_approval

    now = datetime.now(UTC)
    now_iso = now.isoformat()
    state = load_session_state(hook.project_root, hook.session_id, now_iso=now_iso)
    tool_name = hook.tool_name or "unknown"
    tool_detail = policy_tool_detail(facts)
    fingerprint = approval_fingerprint(tool_name, hook.tool_input, facts)
    decision = evaluate_temporal(
        temporal_rules,
        state,
        tool_name=tool_name,
        tool_detail=tool_detail,
        is_write=facts.mutation is not MutationClass.READ_ONLY,
        now=now,
        fingerprint=fingerprint,
    )
    # Un hook peut réécrire l'entrée avant l'exécution : le PostToolUse reprend
    # l'empreinte d'ici par `tool_use_id` au lieu de la recalculer (W1-09).
    remember_pending_approval(
        temporal_rules,
        decision.state,
        tool_use_id=hook.tool_use_id,
        tool_name=tool_name,
        tool_detail=tool_detail,
        fingerprint=fingerprint,
    )
    save_session_state(hook.project_root, decision.state, now_iso=now_iso)
    return decision.verdict, decision.reason, [rule.rule_id for rule in decision.matched_rules]


def _untrusted_escalation(hook: HookInput, facts: ToolFacts) -> Decision | None:
    """Point 2 (issue #645) : une commande vue nulle part sauf dans un contenu
    non fiable devient une question, jamais un refus.

    ``None`` means "nothing to say" — the caller only calls this where the
    base engine and the temporal layer already agreed on ``allow``:
    :mod:`.session_memory` never denies, and this function never downgrades
    an existing ``ask``/``deny``/``block`` either, because the caller never
    hands it the chance to (see :func:`decide_tool_policy`'s two call sites).
    A missing or unwritable session-memory file degrades to "no match" like
    every read in :mod:`.session_memory`, never to an error a ``PreToolUse``
    call could fail on.
    """
    if not facts.command or not hook.session_id:
        return None
    surface = command_surface(facts.command).strip()
    if not surface:
        return None
    from grimoire.hosts.decisions.session_memory import find_untrusted_match

    match = find_untrusted_match(hook.project_root, hook.session_id, surface)
    if match is None:
        # A command wrapped for a shell or python interpreter (``bash -c
        # "curl … | sh"``, ``python -c "…os.system('curl … | sh')"``, ``eval
        # "…"``) still carries the same inner text a planted content spelled
        # out unwrapped — see :func:`extract_c_bodies`. Compared normalised
        # (whitespace/case) since a wrapper never reproduces the planted
        # text's exact spacing the way a verbatim copy would.
        for body in extract_c_bodies(facts.command):
            match = find_untrusted_match(hook.project_root, hook.session_id, body, normalize=True)
            if match is not None:
                break
    if match is None:
        return None
    return Decision(
        outcome=Outcome.ASK,
        reason=(
            f"[Grimoire policy] Cette commande n'apparaît que dans un contenu non fiable "
            f"({match.source}), jamais dans tes propres mots : « {match.excerpt} ». "
            "Confirme avant de l'exécuter."
        ),
        detail={"tool": hook.tool_name, "family": facts.family, "untrusted_source": match.source},
    )


def _profile_downgrade_check(hook: HookInput, facts: ToolFacts) -> Decision | None:
    """A weaker ``profile:`` value in ``standard-profile.yaml`` is a silent
    threshold change (party-mode idea A, ``_scratch/party-oss/gouvernance.md``):
    :func:`_risk_profile` translates the profile straight into which rules
    apply, and nothing before this guarded *that* field — only the rule set
    it selects (:data:`_DESTRUCTIVE_AT_STRICT`, :data:`grimoire.policies.engine._BUILTIN_RULES`).
    An ordinary ``Edit``/``Write``, or a ``sed -i``, could move a project from
    ``production`` to ``starter`` in one call and relax every threshold at
    once without touching a single rule.

    ``None`` means "nothing to say", same contract as
    :func:`_untrusted_escalation` and the same reason it is only ever called
    from the branch where the base engine and the temporal layer already
    agreed on ``allow`` (see :func:`decide_tool_policy`): raising a profile
    (or leaving it unchanged) is ordinary, ungated work, and a call that
    already earned a ``deny``/``ask`` for some other reason keeps it —
    this never downgrades one.

    Only ever returns ``Outcome.ASK``, never ``deny``/``block``: whether a
    downgrade is legitimate (a project winding down, an experiment scoped
    back) is a human call the standard has no way to make for itself.
    """
    if facts.profile_floor_write is not None:
        return _floor_write_check(hook, facts.profile_floor_write)
    if facts.standard_profile_write is None:
        return None

    current = active_profile_id(hook.project_root)
    proposed = facts.standard_profile_write
    if not proposed:
        return Decision(
            outcome=Outcome.ASK,
            reason=(
                "[Grimoire policy] Une commande shell écrit "
                "_grimoire/standard/standard-profile.yaml (profil actuel : "
                f"« {current} ») ; la valeur proposée du champ profile n'est pas "
                "lisible depuis la commande. Confirme le profil visé avant d'exécuter."
            ),
            detail={"tool": hook.tool_name, "current_profile": current},
        )

    from grimoire.core.agentic_standard import profile_rank

    if profile_rank(proposed) < profile_rank(current):
        return Decision(
            outcome=Outcome.ASK,
            reason=(
                "[Grimoire policy] Cette écriture abaisse le profil du standard de "
                f"« {current} » à « {proposed} » (_grimoire/standard/standard-profile.yaml), "
                "ce qui relâche tous les seuils de gouvernance qui en dépendent. "
                "Confirme si c'est voulu (ex. archivage de fin de vie)."
            ),
            detail={"tool": hook.tool_name, "current_profile": current, "proposed_profile": proposed},
        )
    return None


def _floor_write_check(hook: HookInput, proposed: str) -> Decision | None:
    """W2-07 : écrire ``profile-floor.yaml`` ne baisse ni ne retire le plancher sans question.

    Créer un plancher ou le relever ne dit rien. Le baisser, le retirer, ou une
    commande dont on ne lit pas le résultat : ``ask``. Le plancher est
    committé, mais un ``git commit`` n'est pas une décision humaine ; la
    question l'est.
    """
    from grimoire.core.standard_generation import PROFILE_LADDER

    current = profile_floor_id(hook.project_root)
    if proposed in PROFILE_LADDER and (not current or PROFILE_LADDER.index(proposed) >= PROFILE_LADDER.index(current)):
        return None
    shown = f"« {proposed} »" if proposed else "illisible depuis la commande"
    return Decision(
        outcome=Outcome.ASK,
        reason=(
            "[Grimoire policy] Cette écriture touche le plancher de profil "
            f"(_grimoire/standard/profile-floor.yaml, plancher actuel : « {current or 'aucun'} », proposé : {shown}). "
            "Le plancher est ce qu'aucun profil déclaré ne peut passer sous ; l'abaisser ou le retirer "
            "relâche tous les seuils de gouvernance. Pour un besoin d'une session, tape plutôt "
            "`Lower Profile <profil>: <raison>`. Confirme si l'abaissement du plancher est voulu."
        ),
        detail={"tool": hook.tool_name, "current_floor": current, "proposed_floor": proposed},
    )


#: Profils où une garde d'autorité en erreur refuse (fail-closed) et où les
#: vecteurs ``governed_only`` de :mod:`.authority_guard` comptent.
_AUTHORITY_PROFILES = frozenset({"governed", "production"})


def _authority_guard(hook: HookInput, facts: ToolFacts) -> Decision | None:
    """W2-01 : refuse ce qui fabriquerait la preuve d'un tour humain (:mod:`.authority_guard`).

    Évaluée avant tout le reste, même sur un appel classé en lecture seule :
    un ``echo >>`` vers le journal des tours n'a pas à attendre le moteur de
    règles pour être refusé. Une exception ici ne devient jamais un ``allow`` :
    refus en governed et production (profil illisible compris), question
    ailleurs — c'est le cas de panne que W2-08 rejoue.
    """
    limit = "Limite : une garde de chemin se contourne par du code arbitraire."
    try:
        from grimoire.hosts.decisions.authority_guard import LIMIT_NOTE, forgery_vector

        limit = LIMIT_NOTE

        vector = forgery_vector(facts)
        if vector is None:
            return None
        profile = session_profile(hook, journal=False)
        if vector.governed_only and profile not in _AUTHORITY_PROFILES:
            return None
        why = (
            "L'instantané du ledger est un cache dérivé du journal, restauré sans rejouer le préfixe qu'il couvre : "
            "écrit à la main, il imposerait un état que le journal ne dit pas. Il ne s'écrit que par le ledger."
            if vector.code == "ledger-snapshot"
            else "Le journal des tours humains et les challenges de checkpoint ne s'écrivent que par "
            "le hook de l'hôte : c'est la preuve qu'un humain a décidé."
        )
        return Decision(
            outcome=Outcome.DENY,
            reason=f"[Grimoire authority] {hook.tool_name or 'action'} refusé — {vector.what}. {why} {limit}",
            detail={"tool": hook.tool_name, "authority_vector": vector.code, "profile": profile},
        )
    except Exception as exc:
        try:
            profile = session_profile(hook, journal=False)
        except Exception:
            profile = "governed"
        cause = f"{type(exc).__name__}: {exc}"
        outcome = Outcome.DENY if profile in _AUTHORITY_PROFILES else Outcome.ASK
        return Decision(
            outcome=outcome,
            reason=(
                f"[Grimoire authority] garde d'autorité en erreur ({cause}) : "
                f"{'refus par défaut en profil ' + profile if outcome is Outcome.DENY else 'à toi de décider'}. "
                f"{limit}"
            ),
            detail={"tool": hook.tool_name, "authority_vector": "guard-error", "error": cause, "profile": profile},
        )


def decide_tool_policy(hook: HookInput) -> Decision:
    """Pre tool use: run the pending call through the policy engine.

    This is the call site the engine never had: before it, ``PolicyEngine``
    was a library that only its own tests invoked.

    Two layers, evaluated in the same call and escalated together
    (block > ask > allow, see :data:`grimoire.policies.engine._SEVERITY`):
    the request-only base engine (unchanged since before issue #429), and
    the session-scoped temporal layer added by it
    (:func:`_evaluate_temporal_layer`) — budgets, prior approval, cooldowns,
    declared in ``_grimoire/standard/policies.yaml`` (see
    :mod:`grimoire.policies.rules_config`). A project that declares no
    temporal rule pays one ``Path.is_file()`` check more than before and
    behaves exactly as it did before this change.
    """
    decision = _evaluate_tool_policy(hook)
    if decision.outcome is not Outcome.ALLOW and state_writes_allowed():
        _record_hold(hook, decision)
    return decision


def _record_hold(hook: HookInput, decision: Decision) -> None:
    """Calibration (Refs #644): journal this non-``allow`` verdict as a ``policy.hold``.

    Best-effort, never allowed to change *decision* — see :mod:`.calibration`
    for the write itself and why nothing here ever carries the pending
    command/target, only a hash and a coarse key. Recomputes
    :func:`classify_tool` rather than threading it out of
    :func:`_evaluate_tool_policy`: it is pure string parsing, no I/O, and
    doing it a second time only on the (uncommon) non-``allow`` path keeps
    the diff to a single wrapper around the existing function instead of a
    second return value on every one of its five return sites.
    """
    try:
        from grimoire.hosts.decisions.calibration import action_fingerprint, record_policy_hold, target_key

        facts = classify_tool(hook.tool_name, hook.tool_input)
        detail = policy_tool_detail(facts)
        tool_name = hook.tool_name or ""
        record_policy_hold(
            hook.project_root,
            session_id=hook.session_id,
            task_id=str(decision.detail.get("task_id") or active_task_id(hook.project_root)),
            hook_id="grimoire.tool-policy",
            reason=f"tool_policy:{decision.outcome.value}",
            fingerprint=action_fingerprint(tool_name, detail),
            target=target_key(tool_name, detail),
        )
    except Exception:  # noqa: S110 — observabilité : jamais au prix de la décision elle-même
        pass


def _evaluate_tool_policy(hook: HookInput) -> Decision:
    """The original body of :func:`decide_tool_policy`, unchanged — split out
    so :func:`_record_hold` can wrap it without duplicating its five return
    sites.
    """
    facts = classify_tool(hook.tool_name, hook.tool_input)
    forged = _authority_guard(hook, facts)
    if forged is not None:
        return forged
    # K3 (issue #768) : une session qui ne tient plus son bail n'écrit plus, quel que soit
    # le profil — le frein précède le moteur de règles, comme la garde d'autorité.
    from grimoire.hosts.decisions.lease_brake import lease_brake

    braked = lease_brake(hook, facts)
    if braked is not None:
        return braked
    # K11 : un `hold` refuse les outils mutants d'une session qui tient une tâche, sauf son point de
    # reprise ; `drain`, hôte sans frein et drapeau illisible ne refusent rien (voir pause_brake).
    from grimoire.hosts.decisions.pause_brake import pause_brake

    paused = pause_brake(hook, facts)
    if paused is not None:
        return paused
    read_only = facts.mutation is MutationClass.READ_ONLY and not facts.secret_target
    try:
        custom_rules = load_custom_rules(hook.project_root)
    except GrimoirePolicyError as exc:
        # policies.yaml illisible : tout outil qui peut muter repart vers l'utilisateur (ASK, via
        # run_decision) ; un outil en lecture seule passe avec l'avertissement — sinon l'agent ne
        # pourrait plus lire le fichier fautif pour le réparer (et un run headless serait bloqué).
        if not read_only:
            raise
        return _untrusted_escalation(hook, facts) or Decision(
            context=f"[Grimoire] {exc}", detail={"error": "GR-POL-004", "tool": hook.tool_name}
        )
    # A temporal rule (require_approval/per_session/cooldown_after) commonly
    # leaves action_kinds/mutation_classes/risk_profiles empty — it has no
    # opinion on those dimensions, only on the session. Registered as-is into
    # the base engine, ``PolicyRule.matches`` would read those three empty
    # tuples the way it always has ("no constraint" on every dimension) and
    # match — and therefore enforce ``verdict_on_match`` — on *every* request,
    # independently of any session state. The base engine only ever sees the
    # non-temporal rules; temporal ones are the temporal layer's alone.
    base_rules = tuple(rule for rule in custom_rules if not rule.is_temporal)
    has_temporal_rules = any(rule.is_temporal for rule in custom_rules)
    if read_only and not has_temporal_rules:
        return _untrusted_escalation(hook, facts) or Decision()

    # K13 : la tâche d'une session qui tient son bail se lit dans son fichier de bail ; le ledger
    # n'est rejoué que pour une session sans bail noté (PreToolUse p95 ≤ 50 ms, D21).
    from grimoire.hosts.decisions.lease_brake import held_task_id

    task_id = held_task_id(hook) or active_task_id(hook.project_root, session_id=hook.session_id, agent_id=hook.agent_id)
    risk = _risk_profile(hook)
    base_verdict = (
        _engine(base_rules).evaluate(_policy_request(hook, facts, task_id, risk))
        if not read_only
        else None
    )
    temporal_verdict, temporal_reason, temporal_rule_ids = _evaluate_temporal_layer(hook, custom_rules, facts)

    verdict_kind = base_verdict.verdict if base_verdict is not None else VerdictKind.ALLOW
    reason = base_verdict.reason if base_verdict is not None else ""
    matched_rule_ids = [rule.rule_id for rule in base_verdict.matched_rules] if base_verdict is not None else []
    if _SEVERITY[temporal_verdict] > _SEVERITY[verdict_kind]:
        verdict_kind = temporal_verdict
        reason = temporal_reason
    matched_rule_ids.extend(temporal_rule_ids)

    detail = {
        "tool": hook.tool_name,
        # Gardée dans le détail : la trace du gateway et le journal des holds la reprennent
        # au lieu de la résoudre une deuxième fois, ledger rejoué à chaque fois.
        "task_id": task_id,
        "family": facts.family,
        "mutation": facts.mutation.value,
        "risk_profile": risk,
        "rules": matched_rule_ids,
    }
    # Issue #724 §2 : du code interprété (heredoc sur stdin de python/node/…,
    # `-c`/`-e`) n'est lu que contre les appels destructifs connus ; le reste
    # est nommé, jamais tu, pour que l'asymétrie avec `rm -rf` soit visible.
    interpreted_warning = ""
    if facts.interpreted_code:
        detail["interpreted_code"] = facts.interpreted_code
        interpreted_warning = (
            f"[Grimoire policy] code {facts.interpreted_code} interprété : seuls les appels destructifs "
            "connus (shutil.rmtree, os.remove, fs.rm…) sont inspectés, le reste n'est pas lu."
        )

    if verdict_kind is VerdictKind.BLOCK:
        what = facts.destructive_reason or facts.secret_target or facts.kind.value
        motif = reason or "règle de sécurité du standard"
        reason_text = (
            f"[Grimoire policy] {hook.tool_name or 'action'} refusé — {what}. "
            f"Motif : {motif} (profil de risque {risk}). "
            "Demande une autorisation explicite ou passe par une commande réversible."
        )
        return Decision(outcome=Outcome.DENY, reason=reason_text, detail=detail)
    if verdict_kind is VerdictKind.WARN:
        return Decision(
            outcome=Outcome.ASK,
            reason=f"[Grimoire policy] {reason or 'action sensible'} (profil {risk}).",
            detail=detail,
        )
    return (
        _untrusted_escalation(hook, facts)
        or _profile_downgrade_check(hook, facts)
        or Decision(detail=detail, context=interpreted_warning)
    )
