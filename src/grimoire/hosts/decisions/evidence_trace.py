"""``PostToolUse``: remind the agent that a write owes a line of proof.

Also the one place :func:`grimoire.policies.temporal.record_post_tool_use_approval`
is called from (issue #429, point 3, fixed after review 2026-09-12): a
``require_approval`` temporal rule must not be marked approved by the mere
act of asking at ``PreToolUse`` — a host emits ``PostToolUse`` only when the
tool actually ran, which is the only proof that a prior ``ask`` was granted.
See that function's docstring for the fail-open bug this closes.

Also the one place a sub-agent delegation call is measured (#657): :func:`_record_delegation`, reached when
:func:`_is_delegation_tool` recognises ``hook.tool_name`` (``Task``/``Agent``
on Claude Code, ``agent``/``runSubagent`` on Copilot). Living in this module
rather than its own decision keeps it on the one event every host already
routes to this function — the hosts package resolves exactly one decision
per event (issue #419) — and, on Claude Code, requires widening this
decision's ``PostToolUse`` matcher (see ``grimoire.hosts.collect.governance_hooks``
and ``grimoire.hosts.emitters.claude_code._MATCHER_TABLE``) to also fire on
``Task``/``Agent`` calls, which carried neither a write nor an execute
before this issue.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from grimoire.core.standard_checks.evidence_journal import (
    append_evidence_event,
    build_bash_event,
    build_file_write_event,
)
from grimoire.core.standard_state import active_task_id, is_standard_enrolled
from grimoire.hosts.decisions._shared import Decision, HookInput, Outcome
from grimoire.hosts.decisions.tool_facts import ToolFacts, classify_tool, policy_tool_detail
from grimoire.policies.schemas import ActionKind, MutationClass

if TYPE_CHECKING:
    from grimoire.hosts.decisions.repetition_guard import RepetitionVerdict


def _record_temporal_approval(hook: HookInput, facts: ToolFacts) -> None:
    """Best-effort, and a no-op cost (one ``Path.is_file()``) when the
    project declares no ``require_approval`` rule at all — same guard
    shape as :mod:`grimoire.hosts.decisions.tool_policy`'s own fast path.
    Never raises: a session state that fails to load or save degrades to
    "nothing recorded this call", not a broken ``PostToolUse``.

    *facts* must come from the same :func:`classify_tool` call
    :func:`decide_evidence_trace` already makes for its own ``FILE_WRITE``
    check — recomputing it here would risk deriving a different
    ``tool_detail`` (see :func:`grimoire.hosts.decisions.tool_facts.policy_tool_detail`)
    than the one :mod:`.tool_policy` used at ``PreToolUse`` for the same
    call, which would silently break approval matching.
    """
    if not hook.session_id:
        return
    try:
        from grimoire.policies.rules_config import load_custom_rules

        custom_rules = load_custom_rules(hook.project_root)
        if not any(rule.require_approval for rule in custom_rules):
            return

        from datetime import UTC, datetime

        from grimoire.policies.session_state import load_session_state, save_session_state
        from grimoire.policies.temporal import record_post_tool_use_approval

        now_iso = datetime.now(UTC).isoformat()
        state = load_session_state(hook.project_root, hook.session_id, now_iso=now_iso)
        if record_post_tool_use_approval(
            custom_rules,
            state,
            tool_name=hook.tool_name or "unknown",
            tool_detail=policy_tool_detail(facts),
        ):
            save_session_state(hook.project_root, state, now_iso=now_iso)
    except Exception:
        return


def _exit_code(tool_response: dict[str, object]) -> int | None:
    for key in ("exit_code", "exitCode", "returncode", "return_code", "code"):
        value = tool_response.get(key)
        if isinstance(value, bool):
            continue  # bool is an int subclass; never the exit code a host meant
        if isinstance(value, int):
            return value
    return None


def _record_observed_actions(hook: HookInput, facts: ToolFacts, task_id: str) -> None:
    """Issue #582 lot G2 : le journal que le pack de preuve projette, pas la recopie manuelle.

    Best-effort and cheap by construction — see
    :mod:`grimoire.core.standard_checks.evidence_journal` for why this never
    calls :func:`grimoire.core.execution_needs.resolve_need`: this runs on
    every tool call of every session, and the 30 ms hook budget has no room
    for a needs resolution that touches disk on top of the file writes below.
    """
    try:
        if facts.command:
            append_evidence_event(
                hook.project_root, task_id, build_bash_event(facts.command, exit_code=_exit_code(hook.tool_response))
            )
        elif facts.kind is ActionKind.FILE_WRITE:
            for target in facts.targets:
                append_evidence_event(hook.project_root, task_id, build_file_write_event(target))
    except Exception:
        return


#: Tool names that mean "a sub-agent was delegated to", lower-cased for
#: matching. ``task`` is Claude Code's older name for the same capability,
#: recently renamed ``agent``; ``runsubagent`` is VS Code Copilot's — see
#: this PR's ``grimoire-uncertainties`` block, never confirmed against a
#: live Copilot payload.
_DELEGATION_TOOL_NAMES = frozenset({"task", "agent", "runsubagent"})

#: Keys tried in order for each field, across hosts whose delegation tool
#: input shape has not converged. Claude Code's ``Task``/``Agent`` documents
#: ``subagent_type``/``description``/``model``; ``agentName``/``modelName``
#: are what real Copilot sessions on this machine actually carry on a
#: ``runSubagent`` invocation (audit: ``_scratch/delegation-audit/
#: delegation_audit.py``, #657 follow-up) — the rest are defensive fallbacks
#: for a host that spells them differently still.
_DELEGATION_AGENT_KEYS = ("subagent_type", "agent_type", "agentType", "agentName", "agent", "name")
_DELEGATION_MODEL_KEYS = ("model", "modelName")
#: ``prompt`` is deliberately absent: it is the full delegation prompt, not a
#: short caller-declared description, and it used to leak its first 160
#: characters into a ``desc:`` tag exported by ``_to_langfuse_trace`` and
#: OTel — the exact thing this constant's docstring already promised not to
#: do. A call with no ``description``/``task`` now logs no ``desc:`` tag at
#: all, rather than falling back to the prompt.
_DELEGATION_DESCRIPTION_KEYS = ("description", "task")

#: Free text truncated to this length before it reaches the ledger — enough
#: to identify the delegation, never the full prompt (which can carry file
#: contents or secrets the calling tool read).
_DELEGATION_DESCRIPTION_MAX_LEN = 160


#: Clés où l'hôte donne la durée d'un sous-agent terminé, dans ``tool_response``.
#: Claude Code : ``totalDurationMs`` (observé dans les transcripts locaux, champ
#: ``toolUseResult`` d'un appel ``Agent`` au premier plan terminé) ; les autres
#: orthographes sont des replis défensifs, non observés.
_DELEGATION_DURATION_KEYS = ("totalDurationMs", "total_duration_ms", "durationMs", "duration_ms")

#: Tags de datation d'une délégation (#687). ``PostToolUse`` d'un sous-agent au
#: premier plan part à sa FIN : sans durée, ``started_at`` est une date de fin.
DELEGATION_BACKGROUND_TAG = "background"
DELEGATION_TIMING_COMPLETION_TAG = "timing:completion"


def _delegation_duration_ms(tool_response: dict[str, object]) -> float | None:
    """Durée (ms) du sous-agent lue dans *tool_response*, ``None`` si absente ou invalide."""
    for key in _DELEGATION_DURATION_KEYS:
        value = tool_response.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
    return None


def _is_background_delegation(tool_input: dict[str, object], tool_response: dict[str, object]) -> bool:
    """Lancement en arrière-plan : l'outil rend aussitôt, ``PostToolUse`` date donc le lancement."""
    return bool(tool_input.get("run_in_background")) or bool(tool_response.get("isAsync"))


def _is_delegation_tool(tool_name: str) -> bool:
    """Whether *tool_name* is a sub-agent delegation call, host-independently."""
    return tool_name.strip().lower() in _DELEGATION_TOOL_NAMES


def _first_str(tool_input: dict[str, object], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _delegation_fields(tool_input: dict[str, object]) -> dict[str, object]:
    """*tool_input*, with a nested ``toolSpecificData`` dict flattened in.

    A real Copilot ``runSubagent`` invocation carries ``agentName``/
    ``modelName`` under ``toolSpecificData``, not at the top level (#657
    follow-up). Top-level keys win on a collision — they are the shape
    Claude Code's ``Task``/``Agent`` already documents.
    """
    nested = tool_input.get("toolSpecificData")
    if isinstance(nested, dict):
        return {**nested, **tool_input}
    return tool_input


def _record_delegation(hook: HookInput, task_id: str) -> None:
    """Best-effort, silent line in the TraceLedger for one delegation call.

    Issue #657: nothing in the kit measured whether a
    delegation happened, to which agent, or with which model — the gate at
    ``PreToolUse`` only covers ``execute``/``write``/``secret``, and
    ``SubagentStop`` reports gate state, never the call that started the
    delegation. This is pure measurement, never governance: it must inject
    no ``additionalContext`` (zero tokens spent on a verdict nobody asked
    for) and must never turn a full disk or a bad journal into a broken
    ``PostToolUse`` — same contract as :func:`_record_temporal_approval` and
    :func:`_record_observed_actions` above.
    """
    try:
        import uuid
        from datetime import UTC, datetime, timedelta

        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import DELEGATION_TAG, TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        fields = _delegation_fields(hook.tool_input)
        agent_name = _first_str(fields, _DELEGATION_AGENT_KEYS) or "(sans nom)"
        model = _first_str(fields, _DELEGATION_MODEL_KEYS)
        description = _first_str(fields, _DELEGATION_DESCRIPTION_KEYS)[:_DELEGATION_DESCRIPTION_MAX_LEN]

        tags = [DELEGATION_TAG, f"tool:{hook.tool_name}"]
        # #687 : dater la délégation à son LANCEMENT. Au premier plan,
        # PostToolUse part à la fin du sous-agent : maintenant - durée.
        # En arrière-plan, l'outil rend aussitôt : maintenant est le lancement.
        # Sans durée (premier plan), la date reste une date de fin, signalée.
        now = datetime.now(UTC)
        started_at = now
        if _is_background_delegation(hook.tool_input, hook.tool_response):
            tags.append(DELEGATION_BACKGROUND_TAG)
        else:
            duration_ms = _delegation_duration_ms(hook.tool_response)
            if duration_ms is None:
                tags.append(DELEGATION_TIMING_COMPLETION_TAG)
            else:
                started_at = now - timedelta(milliseconds=duration_ms)
        if description:
            tags.append(f"desc:{description}")

        TraceLedger(hook.project_root / TRACES_DIR).record(
            run_id=hook.session_id or f"delegation-{uuid.uuid4().hex[:12]}",
            workflow_instance_id="",
            mission_id="",
            task_id=task_id,
            recipe_id="grimoire.delegation",
            outcome=TraceOutcome.SUCCESS,
            started_at=started_at.isoformat(),
            agent_id=agent_name,
            host_id=hook.host,
            model=model,
            tags=tags,
            # #657 follow-up : sans trace_id, TraceLedger._next_id relit tout
            # le journal et fait len(existing)+1 — deux appels PostToolUse
            # concurrents (plusieurs délégations dans un même message)
            # produisent alors le même id. Un uuid4 est unique sans relire
            # le journal.
            trace_id=f"TRC-delegation-{uuid.uuid4().hex}",
        )
    except Exception:  # observabilité pure : jamais au prix du hook lui-même
        return


def _record_session_mutation(hook: HookInput, facts: ToolFacts, task_id: str) -> None:
    """Issue #638 lot A : la session compte ses écritures, jamais leur contenu.

    C'est ce compteur que ``Stop`` lit pour refuser une clôture hors tâche
    (``_no_task_closure``) sans jamais bloquer une session qui n'a fait que
    lire : seule une classification autre que lecture seule — fichier écrit
    ou édité, commande Bash mutante ou destructive — compte. Best-effort,
    comme le journal de preuve juste au-dessus.
    """
    if facts.mutation is MutationClass.READ_ONLY or not hook.session_id:
        return
    try:
        from datetime import UTC, datetime

        from grimoire.policies.session_state import note_session_mutation

        note_session_mutation(
            hook.project_root, hook.session_id, task_id=task_id, now_iso=datetime.now(UTC).isoformat()
        )
    except Exception:
        return


def _record_untrusted_content(hook: HookInput, facts: ToolFacts) -> None:
    """Issue #645 lot 5.2 : mémorise un extrait de sortie d'outil externe.

    Avant toute question d'enrôlement, comme :func:`_record_temporal_approval`
    juste au-dessus : la mémoire que :mod:`.tool_policy` consulte doit exister
    même sur un projet qui n'a pas adopté le standard — ``decide_tool_policy``
    lui-même ne conditionne jamais son verdict à l'enrôlement. Best-effort,
    déjà garanti par :func:`grimoire.hosts.decisions.session_memory.record_tool_output`
    lui-même ; le ``try`` ici couvre l'import.
    """
    try:
        from grimoire.hosts.decisions.session_memory import record_tool_output

        record_tool_output(hook, facts)
    except Exception:
        return


def _record_repetition(hook: HookInput, facts: ToolFacts) -> RepetitionVerdict | None:
    """Issue #668 : même appel, même échec, trois fois — un signal, jamais un blocage.

    Best-effort, comme :func:`_record_untrusted_content` juste au-dessus :
    avant toute question d'enrôlement (le rappel « tourne en rond » n'a pas de
    raison de dépendre de l'adoption du standard), et le ``try`` ici ne couvre
    que l'import. Jamais pour un appel de délégation (:func:`_is_delegation_tool`) :
    ce chemin est une mesure pure « zéro coût en tokens » (#657) — un appel de
    délégation qui ne répète jamais son propre outil de délégation n'a de
    toute façon rien d'exact à répéter, et le test qui pin ce contrat
    (``test_post_tool_use_logs_a_delegation_call_silently``) attend une
    ``Decision`` strictement vide.
    """
    if _is_delegation_tool(hook.tool_name):
        return None
    try:
        from grimoire.hosts.decisions.repetition_guard import evaluate_and_record

        return evaluate_and_record(hook, facts)
    except Exception:
        return None


def _with_repetition(decision: Decision, verdict: RepetitionVerdict | None) -> Decision:
    """Fusionne le verdict de répétition dans *decision*, sans jamais écraser
    le contexte déjà présent (le rappel d'évidence sur une écriture) — les deux
    peuvent coexister, séparés par un saut de ligne, plutôt que de se disputer
    le seul champ ``additionalContext`` qu'un hôte lit sur ``PostToolUse``.
    """
    if verdict is None:
        return decision
    detail = {**decision.detail, "repetition": verdict.to_dict()}
    context = decision.context
    if verdict.nudge:
        context = f"{context}\n{verdict.nudge}" if context else verdict.nudge
    return replace(decision, context=context, detail=detail)


def decide_evidence_trace(hook: HookInput) -> Decision:
    """Post tool use: remind the agent that a write owes a line of proof."""
    facts = classify_tool(hook.tool_name, hook.tool_input)
    _record_temporal_approval(hook, facts)
    _record_untrusted_content(hook, facts)
    repetition = _record_repetition(hook, facts)
    if not is_standard_enrolled(hook.project_root):
        return _with_repetition(Decision(), repetition)
    task_id = active_task_id(hook.project_root)
    _record_observed_actions(hook, facts, task_id)
    _record_session_mutation(hook, facts, task_id)
    if _is_delegation_tool(hook.tool_name):
        _record_delegation(hook, task_id)
    if facts.kind is not ActionKind.FILE_WRITE:
        return _with_repetition(Decision(), repetition)
    touched = ", ".join(facts.targets[:3]) or "le fichier modifié"
    context = (
        f"[Grimoire] Écriture enregistrée ({touched}). Ajoute la preuve correspondante à "
        f"_grimoire-output/evidence/{task_id}/evidence-pack.md — commande exécutée, test vert ou diff clé."
    )
    decision = Decision(
        outcome=Outcome.ALLOW, context=context, detail={"task_id": task_id, "targets": list(facts.targets)}
    )
    return _with_repetition(decision, repetition)
