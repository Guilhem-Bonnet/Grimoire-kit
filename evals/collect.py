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

ARMS = ("governed", "baseline")


def collect_standard_metrics(project_root: Path, task_id: str) -> dict[str, Any]:
    """Collecte verify/score/gate ; les échecs structurels deviennent des nulls."""
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
        gate = check_evidence_gates(project_root, task_id=task_id)
        metrics.update({"gate_ok": gate.ok, "gate_missing": list(gate.missing)})
    except (ValueError, FileNotFoundError, OSError):
        pass
    return metrics


def collect_record(project_root: Path, witness: str, task_id: str, arm: str) -> dict[str, Any]:
    """Construit un run-record complet, métriques externes à null."""
    if arm not in ARMS:
        msg = f"arm invalide {arm!r} — attendu : {', '.join(ARMS)}"
        raise ValueError(msg)
    return {
        "$schema": "grimoire-evals-run-record/v1",
        "collected_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "kit_version": __version__,
        "witness": witness,
        "task_id": task_id,
        "arm": arm,
        "standard": collect_standard_metrics(project_root.resolve(), task_id),
        "external": {
            "completed": None,
            "tests_green": None,
            "regressions": None,
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
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    record = collect_record(args.project, args.witness, args.task, args.arm)
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
