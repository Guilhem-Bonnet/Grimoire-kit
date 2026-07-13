#!/usr/bin/env python3
"""Collecteur de run-record pour le protocole d'évaluation (docs/evals-protocol.md).

Agrège les métriques « par construction » (verify / score / gate) d'un projet
témoin dans un run-record JSON. Les métriques externes (complétion, régressions,
coût tokens, interventions humaines) sont laissées à ``null`` : elles sont
renseignées par l'opérateur de campagne, jamais inventées.

Usage::

    python evals/collect.py --project <path> --witness web-app-todo \
        --task feat-due-dates --arm governed [--out record.json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any

from grimoire.__version__ import __version__
from grimoire.core.agentic_standard import (
    calculate_compliance_score,
    check_evidence_gates,
    verify_standard_profile,
)

ARMS = ("governed", "baseline", "activated")
ENROLLED_ARMS = ("governed", "activated")


def collect_standard_metrics(
    project_root: Path,
    task_id: str,
    *,
    gate_target_state: str | None = None,
) -> dict[str, Any]:
    """Collecte verify/score/gate ; les échecs structurels deviennent des nulls.

    ``gate_target_state`` : pour le bras ``activated``, le gate est évalué à
    l'état ``review`` (celui que le mécanisme d'activation impose) ; sans état
    cible, un projet starter fraîchement enrôlé passe trivialement le gate et
    la mesure d'engagement ne mesure rien.
    """
    metrics: dict[str, Any] = {
        "verify_ok": None,
        "profile": None,
        "error_count": None,
        "warning_count": None,
        "score": None,
        "threshold": None,
        "gate_ok": None,
        "gate_missing": [],
    }
    try:
        verify = verify_standard_profile(project_root, task_id=task_id)
        metrics.update({
            "verify_ok": verify.ok,
            "profile": verify.profile,
            "error_count": verify.error_count,
            "warning_count": verify.warning_count,
        })
    except (ValueError, FileNotFoundError, OSError):
        return metrics
    try:
        score = calculate_compliance_score(project_root, task_id=task_id)
        metrics.update({"score": score.score, "threshold": score.threshold})
    except (ValueError, FileNotFoundError, OSError):
        pass
    try:
        gate = check_evidence_gates(project_root, task_id=task_id, target_state=gate_target_state)
        metrics.update({"gate_ok": gate.ok, "gate_missing": list(gate.missing)})
    except (ValueError, FileNotFoundError, OSError):
        pass
    return metrics


def collect_record(
    project_root: Path,
    witness: str,
    task_id: str,
    arm: str,
    standard_task_id: str = "bootstrap",
) -> dict[str, Any]:
    """Construit un run-record complet, métriques externes à null.

    Le bloc ``standard`` n'a de sens que pour les bras enrôlés (``governed``
    et ``activated``) : le bras ``baseline`` ne reçoit aucun artefact du
    standard, ses métriques restent ``null`` (protocole §Collecte).

    Schéma v2 : le champ ``regressions`` (règle primaire 2026-07-03) est
    conservé tel quel ; s'y ajoutent en secondaire ``regressions_hard``
    (test/build baseline cassé ou supprimé) et ``regressions_adapted``
    (test modifié, suite verte, contrat préservé) — comptage secondaire
    pré-enregistré (ACTIVATION.md / ACTIVATION-V2.md).

    ``standard_task_id`` : id de tâche des artefacts du standard dans la copie
    enrôlée (``bootstrap`` par défaut — celui que scaffolde ``grimoire
    standard init`` et que le mécanisme d'activation impose). Il est distinct
    de ``task_id``, le label de la tâche d'évaluation ; en v1 le label était
    utilisé pour verify/gate, qui pointaient donc sur des artefacts
    inexistants (voir evals/reports/2026-07-03/ERRATA.md).
    """
    if arm not in ARMS:
        msg = f"arm invalide {arm!r} — attendu : {', '.join(ARMS)}"
        raise ValueError(msg)
    standard = (
        collect_standard_metrics(
            project_root.resolve(),
            standard_task_id,
            gate_target_state="review" if arm == "activated" else None,
        )
        if arm in ENROLLED_ARMS
        else None
    )
    return {
        "$schema": "grimoire-evals-run-record/v2",
        "collected_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "kit_version": __version__,
        "witness": witness,
        "task_id": task_id,
        "arm": arm,
        "standard": standard,
        "external": {
            "completed": None,
            "tests_green": None,
            "regressions": None,
            "regressions_hard": None,
            "regressions_adapted": None,
            "tokens_cost": None,
            "human_interventions": None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--witness", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument(
        "--standard-task-id",
        default="bootstrap",
        help="Id de tâche des artefacts du standard dans la copie enrôlée (défaut : bootstrap).",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    record = collect_record(
        args.project,
        args.witness,
        args.task,
        args.arm,
        standard_task_id=args.standard_task_id,
    )
    payload = json.dumps(record, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"run-record écrit : {args.out}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
