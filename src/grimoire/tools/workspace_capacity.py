"""Vue « Capacité » du cockpit (P71) — l'admission K10 rendue lisible, sans rien écrire.

``GET /api/workspace/capacity`` montre les goulots d'étranglement : combien de
workers tiennent un créneau (WIP), l'occupation de chaque réservoir d'admission
(runners CI, hôte charge/mémoire, sessions LLM par fournisseur), le quota de
tokens du jour, et la file des tâches prêtes dans l'ordre réel d'admission —
chaque tâche différée porte sa raison.

Tout vient de :func:`grimoire.missions.admission.build_plan` sur le ledger réel
(même plan à blanc que ``grimoire queue plan``) : aucun compteur à côté, aucune
écriture, pas même un événement. Une sonde muette, une section de
``capacity.yaml`` illisible ou une consommation inconnue se rendent
``inconnu`` — jamais une valeur devinée (doctrine d'ancrage des affirmations).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.missions.admission import (
    Capacity,
    Defer,
    HostReading,
    LedgerView,
    QueuePlan,
    build_plan,
    load_capacity,
    read_host,
)
from grimoire.missions.pause import read_pause
from grimoire.missions.service import TaskService

__all__ = ["REASON_LABELS", "capacity_view"]

#: La raison d'un ``Defer`` en clair — le mot que la file affiche à côté de la tâche.
REASON_LABELS: dict[str, str] = {
    "wip": "WIP atteint",
    "ci": "CI pleine",
    "host": "hôte chargé",
    "llm": "sessions LLM saturées",
    "quota": "quota du jour atteint",
    "pause": "pause",
    "provider": "fournisseur différé",
    "unknown": "inconnue",
}

_PROBE_MUTE = "sonde muette (/proc/meminfo ou getloadavg illisible)"


def _reservoir(
    rid: str, libelle: str, utilise: float | int | None, capacite: float | int | None, etat: str, detail: str
) -> dict[str, Any]:
    return {"id": rid, "libelle": libelle, "utilise": utilise, "capacite": capacite, "etat": etat, "detail": detail}


def _ci_reservoir(capacity: Capacity, view: LedgerView) -> dict[str, Any]:
    if err := capacity.error_for("ci"):
        return _reservoir("ci.runners", "Runners CI", None, None, "inconnu", err)
    busy = len(view.ci_occupants)
    runners = capacity.ci_runners
    etat = "plein" if runners is not None and busy >= runners else "ok"
    occupants = ", ".join(sorted(view.ci_occupants)[:3])
    detail = f"tenus par {occupants}" if occupants else "aucun créneau tenu"
    return _reservoir("ci.runners", "Runners CI", busy, runners, etat, detail)


def _host_reservoirs(capacity: Capacity, host: HostReading | None) -> list[dict[str, Any]]:
    """Le pool ``host`` en jauges : charge par CPU, mémoire disponible.

    Non déclaré, il ne gate rien — la lecture réelle (si la sonde parle) reste
    affichée, avec « aucune limite déclarée » ; muette, elle vaut ``inconnu``,
    jamais un chiffre inventé.
    """
    if err := capacity.error_for("host"):
        return [_reservoir("host", "Hôte", None, None, "inconnu", err)]
    limits = capacity.host
    if limits is None:
        if host is None:
            return [_reservoir("host", "Hôte", None, None, "non_declare", f"pool non déclaré — {_PROBE_MUTE}")]
        detail = f"charge {host.load_per_cpu:.2f}/CPU · {host.mem_available_mb} Mo libres — aucune limite déclarée"
        return [_reservoir("host", "Hôte", None, None, "non_declare", detail)]
    if host is None:
        return [_reservoir("host", "Hôte", None, None, "inconnu", f"pool déclaré mais {_PROBE_MUTE}")]
    out: list[dict[str, Any]] = []
    load = round(host.load_per_cpu, 2)
    if limits.max_load_per_cpu is not None:
        etat = "plein" if host.load_per_cpu > limits.max_load_per_cpu else "ok"
        out.append(_reservoir("host.charge", "Hôte — charge/CPU", load, limits.max_load_per_cpu, etat,
                              f"charge {load}/CPU (max {limits.max_load_per_cpu:g})"))
    else:
        out.append(_reservoir("host.charge", "Hôte — charge/CPU", load, None, "ok", f"charge {load}/CPU — non bornée"))
    if limits.min_mem_available_mb is not None:
        etat = "plein" if host.mem_available_mb < limits.min_mem_available_mb else "ok"
        out.append(_reservoir("host.memoire", "Hôte — mémoire libre", host.mem_available_mb, None, etat,
                              f"{host.mem_available_mb} Mo disponibles (min {limits.min_mem_available_mb} Mo)"))
    else:
        out.append(_reservoir("host.memoire", "Hôte — mémoire libre", host.mem_available_mb, None, "ok",
                              f"{host.mem_available_mb} Mo disponibles — non bornée"))
    return out


def _llm_reservoirs(capacity: Capacity, view: LedgerView) -> list[dict[str, Any]]:
    if err := capacity.error_for("llm"):
        return [_reservoir("llm", "Sessions LLM", None, None, "inconnu", err)]
    out: list[dict[str, Any]] = []
    for provider, limit in sorted(capacity.llm_sessions.items()):
        running = sum(1 for h in view.held if h.provider == provider)
        etat = "plein" if running >= limit else "ok"
        out.append(_reservoir(f"llm.{provider}", f"Sessions {provider}", running, limit, etat,
                              f"{running}/{limit} session(s) en cours"))
    return out


def _wip(capacity: Capacity, view: LedgerView) -> dict[str, Any]:
    actifs = len(view.held)
    if err := capacity.error_for("wip"):
        return {"actifs": actifs, "limite": None, "par_role": None, "etat": "inconnu", "detail": err}
    limite = capacity.wip_global
    etat = "plein" if limite is not None and actifs >= limite else "ok"
    detail = "sans limite déclarée" if limite is None else f"{actifs}/{limite} créneau(x) tenus"
    return {"actifs": actifs, "limite": limite, "par_role": capacity.wip_per_role, "etat": etat, "detail": detail}


def _quota(capacity: Capacity, view: LedgerView) -> dict[str, Any]:
    if err := capacity.error_for("quota"):
        return {"mode": "inconnu", "consomme": None, "plafond": None, "statut": "unknown", "detail": err}
    used = view.tokens
    if capacity.unlimited:
        detail = "illimité — jamais de refus par quota"
        return {"mode": "illimite", "consomme": used.tokens, "plafond": None, "statut": used.status, "detail": detail}
    plafond = int(capacity.tokens_per_day)
    if used.tokens is None:
        detail = "consommation du jour inconnue sous un quota fini"
    else:
        reste = max(plafond - used.tokens, 0)
        detail = f">= {used.tokens}/{plafond} tokens consommés — {reste} restants au mieux"
    return {"mode": "fini", "consomme": used.tokens, "plafond": plafond, "statut": used.status, "detail": detail}


def _queue(plan: QueuePlan, titles: dict[str, str]) -> list[dict[str, Any]]:
    """La file dans l'ordre réel d'admission (``plan.order``), verdict et raison par tâche."""
    deferred = {d.task_id: d for d in plan.deferred}
    rows: list[dict[str, Any]] = []
    for rang, (task_id, base, effective) in enumerate(plan.order, start=1):
        decision: Defer | None = deferred.get(task_id)
        rows.append({
            "rang": rang,
            "task_id": task_id,
            "titre": titles.get(task_id, task_id),
            "priorite": base,
            "priorite_effective": effective,
            "verdict": "defer" if decision is not None else "admit",
            "raison": decision.reason if decision is not None else None,
            "raison_libelle": REASON_LABELS.get(decision.reason, decision.reason) if decision is not None else None,
            "detail": decision.detail if decision is not None else "",
            "retry_at": decision.retry_at if decision is not None and decision.retry_at != "unknown" else None,
        })
    return rows


def capacity_view(
    project_root: Path,
    *,
    now: datetime | None = None,
    probe: Callable[[], HostReading | None] = read_host,
) -> dict[str, Any]:
    """L'état de l'admission pour *project_root* : WIP, réservoirs, quota, file. Lecture pure.

    Même plan à blanc que ``grimoire queue plan`` (routes du routeur lues, jamais
    écrites ; ``autonomous`` suit ``GRIMOIRE_UNATTENDED`` comme ``queue_plan``).
    *probe* est la sonde de l'hôte — injectable pour prouver qu'une sonde muette
    s'affiche « inconnue », jamais devinée.
    """
    service = TaskService(project_root.resolve())
    capacity = load_capacity(service.home_root)
    moment = now or datetime.now(UTC)
    host = probe()
    if not service.has_ledger:
        view = LedgerView(moment, host=host)
        return {
            "ledger": False,
            "wip": _wip(capacity, view),
            "reservoirs": [_ci_reservoir(capacity, view), *_host_reservoirs(capacity, host), *_llm_reservoirs(capacity, view)],
            "quota": _quota(capacity, view),
            "file": [],
            "exclues": [],
            "famine": [],
            "pause": [],
        }
    autonomous = os.environ.get("GRIMOIRE_UNATTENDED", "").strip() == "1"
    plan = build_plan(
        service.ledger, capacity, now=moment, host=host,
        route_of=service._route_ref,  # la même lecture de route que `queue_plan` (K9) — jamais écrite
        autonomous=autonomous, pause=read_pause(service.home_root),
    )
    titles = {t.id: t.title for t in service.ledger.list_tasks()}
    return {
        "ledger": True,
        "wip": _wip(capacity, plan.view),
        "reservoirs": [
            _ci_reservoir(capacity, plan.view),
            *_host_reservoirs(capacity, plan.view.host),
            *_llm_reservoirs(capacity, plan.view),
        ],
        "quota": _quota(capacity, plan.view),
        "file": _queue(plan, titles),
        "exclues": [{"task_id": t, "titre": titles.get(t, t), "raison": r} for t, r in plan.excluded],
        "famine": [f.task_id for f in plan.famine],
        "pause": [flag.label for flag in plan.pause.flags],
    }
