"""Journaling a persona choice, or its absence, into the trace ledger.

Both functions defer their own heavy imports (:mod:`grimoire.traces.ledger`
and friends) to the call itself — they are best-effort observability, never
on the path a hook must pay for merely to be importable. ``record_agent_miss``
is public API (``grimoire agent-miss``, :mod:`grimoire.cli.app`); the other is
private, used only by :mod:`.activation`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path


def _record_agent_dispatch(project_root: Path, agent_name: str, task_id: str) -> None:
    """Journaliser dans le TraceLedger que *agent_name* a été choisi comme persona d'entrée.

    Symétrique de ``MissionService._record_refusal`` : le fait constaté (ici,
    un choix d'agent, là-bas, un gate rouge) n'est jamais la source d'un état
    métier, seulement une entrée dans le journal d'observabilité qui permet
    d'y répondre plus tard. Best-effort — un journal indisponible ou
    illisible ne doit jamais faire échouer l'activation de session qu'il se
    contente d'observer (issue #365).
    """
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import AGENT_DISPATCH_TAG, TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        TraceLedger(project_root / TRACES_DIR).record(
            run_id=f"entry-persona-{uuid.uuid4().hex[:12]}",
            workflow_instance_id="",
            mission_id="",
            task_id=task_id,
            recipe_id="grimoire.entry-persona",
            outcome=TraceOutcome.SUCCESS,
            started_at=datetime.now(UTC).isoformat(),
            agent_id=agent_name,
            tags=[AGENT_DISPATCH_TAG],
        )
    except Exception:  # noqa: S110 — observabilité : jamais au prix de l'activation elle-même
        pass


def record_agent_miss(
    project_root: Path,
    *,
    category: str,
    specialty: str = "",
    fallback_agent: str = "",
    reason: str = "",
) -> bool:
    """Journaliser dans le TraceLedger un non-choix : aucun spécialiste ne convenait.

    Symétrique de ``_record_agent_dispatch`` (issue #366) : là un choix
    d'agent, ici son absence — le concierge a cherché un spécialiste pour une
    demande et n'en a trouvé aucun, ou s'est rabattu sur un généraliste faute
    de mieux. C'est ce non-choix qui signale un besoin non couvert (issue
    #389) ; sans lui, le déclencheur (#355) n'a rien sur quoi se déclencher.

    Le triage qui produit ce fait se fait dans le raisonnement de la persona
    concierge (``archetypes/meta/agents/concierge.md``), pas dans du code du
    kit qui pourrait l'observer lui-même — cette fonction, appelée par
    ``grimoire agent-miss``, est donc le seul point d'écriture pour ce
    signal. *category* et *specialty* classent la demande, ils n'en portent
    jamais le contenu : c'est à l'appelant de ne transmettre qu'une
    étiquette. Best-effort par construction, comme son symétrique : un
    journal indisponible ou illisible ne doit jamais faire échouer la
    résolution qu'il se contente d'observer — mais l'appelant a besoin de
    savoir si l'écriture a eu lieu pour ne jamais l'affirmer à tort. Renvoie
    ``True`` si le fait a été écrit, ``False`` s'il a été avalé par le
    ``except`` ci-dessous.
    """
    try:
        from grimoire.core.standard_generation import TRACES_DIR
        from grimoire.traces.ledger import AGENT_MISS_TAG, TraceLedger
        from grimoire.traces.schemas import TraceOutcome

        tags = [AGENT_MISS_TAG, f"category:{category}"]
        if specialty:
            tags.append(f"specialty:{specialty}")
        if reason:
            tags.append(f"reason:{reason}")

        TraceLedger(project_root / TRACES_DIR).record(
            run_id=f"concierge-miss-{uuid.uuid4().hex[:12]}",
            workflow_instance_id="",
            mission_id="",
            task_id="",
            recipe_id="grimoire.entry-persona.miss",
            outcome=TraceOutcome.FAILURE,
            started_at=datetime.now(UTC).isoformat(),
            agent_id=fallback_agent,
            tags=tags,
        )
        return True
    except Exception:  # observabilité : jamais au prix de la résolution elle-même
        return False
