"""Modèle épinglé, appariement refusé entre modèles, suite non saturée (B-01).

Module à part : ``three_arms.py`` dépasse déjà le seuil de taille. Aucune
dépense ici, aucun appel ``claude`` autre que ``claude --version``.

Trois règles, une par défaut constaté :

1. Le banc lançait ``claude -p`` sans ``--model`` : le modèle était celui par
   défaut de l'outil, qui a changé entre le lot K et le lot L
   (``docs/bench/rejeu-lot-l-2026-10-02.md`` §1.3). Le modèle demandé est
   désormais obligatoire pour toute campagne payante et noté sur chaque ligne.
2. Deux lots de modèles différents ne s'apparient pas : mélanger leurs lignes
   dans un même rapport (``--resume``, ``--report-only``) fabrique une
   comparaison entre bras qui mesure le modèle, pas le kit. Une ligne dont le
   modèle n'est pas enregistré (antérieure à B-01) est INCONNUE, pas égale.
3. Une suite où le ``nu`` réussit déjà tout ne peut pas montrer un gain de
   succès : au-delà de 50 % de tâches à 1.0 pour le ``nu``, le rapport
   l'écrit (« SATURÉ ») au lieu de laisser croire à une mesure de succès.
"""

from __future__ import annotations

import json
import subprocess
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from results_io import read_results_rows

#: Au-delà de cette part de tâches à 1.0 pour le ``nu``, la suite est saturée (plan B-01).
SATURATION_THRESHOLD = 0.5
NU_ARM = "nu"
UNRECORDED = "non enregistré (ligne antérieure à B-01)"


class ModelMismatchError(RuntimeError):
    """Des lignes issues de modèles différents (ou d'un modèle inconnu) à apparier."""


def detect_claude_code_version(*, timeout_s: float = 20.0) -> str | None:
    """Version de Claude Code (``claude --version``), ``None`` si elle n'est pas lisible.

    Gratuit : ``--version`` n'appelle aucun modèle. ``None`` veut dire
    « inconnue », jamais une version supposée.
    """
    try:
        proc = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or not out:
        return None
    return out.splitlines()[0].strip()


def require_single_model(model_ids: Iterable[str | None], *, where: str) -> str | None:
    """Le modèle commun à toutes les lignes, ou ``ModelMismatchError``.

    ``None`` en retour : aucune ligne, ou aucune n'enregistre de modèle (tout
    le lot est antérieur à B-01). Un mélange de modèles, ou de lignes
    enregistrées et non enregistrées, est refusé : l'inconnu n'est pas égal.
    """
    seen = {model or None for model in model_ids}
    if len(seen) <= 1:
        return next(iter(seen), None)
    names = sorted(UNRECORDED if model is None else model for model in seen)
    raise ModelMismatchError(
        f"appariement refusé ({where}) : les lignes viennent de modèles différents : "
        + " ; ".join(names)
        + ". Une comparaison entre bras issus de modèles différents mesure le modèle, pas le kit : "
        "rejouer le lot dans un workspace neuf avec un seul `--model`."
    )


def ensure_results_match_model(results_path: Path, model_id: str | None) -> None:
    """Refuse de poursuivre une campagne sur un ``results.jsonl`` d'un autre modèle.

    Appelé avant toute dépense : sans ce contrôle, ``--resume`` ou un simple
    ajout au fichier mélangerait deux modèles dans le rapport suivant.
    """
    if not results_path.is_file():
        return
    rows = read_results_rows(results_path)
    require_single_model(
        [*(row.get("model_id") for row in rows), model_id] if rows else [model_id],
        where=f"campagne en cours sur {results_path}",
    )


def selection_pin(selection_path: Path, model_id: str | None, claude_code_version: str | None) -> dict[str, Any]:
    """Champs ``model_id`` et ``claude_code_version`` de ``selection.json``.

    Sans ``--model`` (``--dry-run``), on reprend ceux du fichier existant au
    lieu de les effacer : une préparation à sec ne doit pas faire oublier le
    modèle d'une campagne déjà lancée dans ce workspace.
    """
    if model_id:
        return {"model_id": model_id, "claude_code_version": claude_code_version}
    previous: Mapping[str, Any] = {}
    if selection_path.is_file():
        try:
            loaded = json.loads(selection_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            loaded = {}  # illisible : le modèle est alors inconnu (null), pas deviné
        if isinstance(loaded, dict):
            previous = loaded
    return {
        "model_id": previous.get("model_id"),
        "claude_code_version": previous.get("claude_code_version"),
    }


def nu_baseline_pass_rates(records: Sequence[Any]) -> dict[str, float | None]:
    """Part de runs réussis du bras ``nu``, par tâche (runs invalidés exclus).

    ``None`` pour une tâche sans aucun run ``nu`` valide : la valeur est
    inconnue, jamais 0.0.
    """
    outcomes: dict[str, list[bool]] = defaultdict(list)
    all_tasks: set[str] = set()
    for record in records:
        all_tasks.add(record.task_id)
        if record.arm == NU_ARM and not record.invalid:
            outcomes[record.task_id].append(bool(record.success))
    return {
        task_id: (sum(outcomes[task_id]) / len(outcomes[task_id]) if outcomes.get(task_id) else None)
        for task_id in sorted(all_tasks)
    }


def suite_saturation(rates: Mapping[str, float | None]) -> dict[str, Any]:
    """Verdict de saturation : ``SATURÉ``, ``NON SATURÉ`` ou ``NON ÉVALUÉ``.

    Saturée = strictement plus de 50 % des tâches mesurées à 1.0 pour le
    ``nu``. Sans aucune mesure du ``nu``, le verdict est ``NON ÉVALUÉ`` (on ne
    dit pas « non saturée » faute de donnée).
    """
    measured = [rate for rate in rates.values() if rate is not None]
    at_one = sum(1 for rate in measured if rate >= 1.0)
    if not measured:
        return {
            "verdict": "NON ÉVALUÉ",
            "saturated": None,
            "threshold": SATURATION_THRESHOLD,
            "n_tasks_measured": 0,
            "n_tasks_at_1": 0,
            "ratio": None,
        }
    ratio = at_one / len(measured)
    saturated = ratio > SATURATION_THRESHOLD
    return {
        "verdict": "SATURÉ" if saturated else "NON SATURÉ",
        "saturated": saturated,
        "threshold": SATURATION_THRESHOLD,
        "n_tasks_measured": len(measured),
        "n_tasks_at_1": at_one,
        "ratio": ratio,
    }


def render_saturation_lines(saturation: Mapping[str, Any]) -> list[str]:
    """Lignes Markdown de la mention de saturation."""
    verdict = saturation["verdict"]
    if verdict == "NON ÉVALUÉ":
        return ["Saturation de la suite : **NON ÉVALUÉ** (aucun run `nu` valide : le taux de base est inconnu).", ""]
    share = f"{saturation['n_tasks_at_1']}/{saturation['n_tasks_measured']} tâches à 1.0 pour le `nu`"
    threshold = f"{saturation['threshold']:.0%}"
    if saturation["saturated"]:
        return [
            f"> **SATURÉ** : {share} (> {threshold}). La suite ne peut pas montrer un gain de succès : "
            "elle mesure le coût, pas le succès. Aucune conclusion d'efficacité sur le succès n'est permise.",
            "",
        ]
    return [f"Saturation de la suite : NON SATURÉ ({share}, seuil > {threshold}).", ""]


def pin_report_fields(records: Sequence[Any]) -> tuple[dict[str, Any], dict[str, float | None]]:
    """Champs B-01 du rapport et taux ``nu`` par tâche ; ``ModelMismatchError`` si les modèles diffèrent."""
    model_id = require_single_model((r.model_id for r in records), where="rapport")
    rates = nu_baseline_pass_rates(records)
    fields = {
        "model_id": model_id,
        "claude_code_versions": sorted({r.claude_code_version for r in records if r.claude_code_version}),
        "suite_saturation": suite_saturation(rates),
    }
    return fields, rates


def render_pin_lines(report: Mapping[str, Any]) -> list[str]:
    """Lignes Markdown : modèle demandé, versions de Claude Code, saturation."""
    versions = report.get("claude_code_versions") or []
    suffix = f" · Claude Code {', '.join(versions)}." if versions else "."
    if report.get("model_id"):
        lines = [f"Modèle demandé (`--model`) : `{report['model_id']}`{suffix}", ""]
    else:
        lines = [
            "Modèle demandé : **non enregistré** (lot antérieur à B-01 : le modèle effectif ne se lit que dans "
            f"`model_usage`){suffix}",
            "",
        ]
    if len(versions) > 1:
        lines += [
            f"> Plusieurs versions de Claude Code dans ce lot ({', '.join(versions)}) : à lire comme une réserve.",
            "",
        ]
    if report.get("suite_saturation"):
        lines += render_saturation_lines(report["suite_saturation"])
    return lines
