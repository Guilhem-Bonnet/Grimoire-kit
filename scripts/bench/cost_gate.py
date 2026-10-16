#!/usr/bin/env python3
"""Garde de coût face au nu à chaque barrière (B-00, ADP-5).

Rejoue un sous-lot fixe et pré-déclaré (``docs/bench/cost-gate-subset.json``)
par ``nu`` et ``kit-gov``, puis compare le coût médian par tâche résolue du
bras gouverné à celui du nu. Le verdict compare ce ratio à celui de la barrière
précédente (``--previous``), avec une tolérance en points (décision 13 du plan,
5 par défaut, arbitraire et non mesurée).

Le sous-lot est saturé (les deux bras résolvent tout) : il mesure le coût, pas
le succès, et le rapport le dit. Un coût inconnu n'est jamais compté pour zéro :
si un bras n'a pas tous ses coûts connus (``cost_known_ratio`` < 1), le verdict
est ``unevaluated`` et il bloque comme un dépassement.

Lancement d'un sous-lot (dépense réelle, hors de ce module) : voir
``docs/bench-three-arms.md`` §11. Évaluation, sans appel modèle :

    python scripts/bench/cost_gate.py --results <workspace>/state/results.jsonl \
        --previous docs/bench/cost-gate-baseline.json --out-dir <dossier>

Code de sortie : 0 ``pass`` ou ``waived``, 1 ``exceeded``, 2 ``unevaluated``.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from results_io import read_results_rows

NU_ARM = "nu"
GOV_ARM = "kit-gov"
DEFAULT_TOLERANCE_POINTS = 5.0
#: Cible de l'issue #642, rappelée dans le rapport à titre d'information (jamais un seuil de blocage ici).
TARGET_RATIO_PCT = 105.0
SUBSET_PATH = Path(__file__).resolve().parents[2] / "docs" / "bench" / "cost-gate-subset.json"

EXIT_CODES = {"pass": 0, "waived": 0, "exceeded": 1, "unevaluated": 2}


def cost_from_result(result: dict[str, Any] | None) -> tuple[float, bool]:
    """Coût d'un événement ``result`` de ``claude -p`` : ``(montant, connu)``.

    Un événement absent, sans ``total_cost_usd`` ou à ``null`` est INCONNU
    (``(0.0, False)``) ; un ``0`` explicite est un coût connu. Le montant reste
    ``0.0`` pour l'inconnu afin de ne pas casser les sommes existantes ; c'est
    ``connu`` qui dit que ce zéro n'est pas une mesure.
    """
    if not result:
        return 0.0, False
    raw = result.get("total_cost_usd")
    if raw is None or isinstance(raw, bool):
        return 0.0, False
    try:
        return float(raw), True
    except (TypeError, ValueError):
        return 0.0, False


def row_cost_known(row: dict[str, Any]) -> bool:
    """Le coût d'une ligne de ``results.jsonl`` est-il une mesure ?

    ``cost_known`` explicite fait foi. Les lignes antérieures à B-00 ne le
    portent pas : un coût strictement positif y est une mesure, un ``0.0`` ou
    un ``null`` ne se distingue pas d'un coût manquant et vaut inconnu (même
    convention que la lecture tolérante de trace.v1).
    """
    explicit = row.get("cost_known")
    if explicit is not None:
        return bool(explicit)
    cost = row.get("total_cost_usd")
    return isinstance(cost, int | float) and not isinstance(cost, bool) and cost > 0


def cost_known_fields(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Champs ``cost_known_runs`` / ``cost_known_ratio`` d'un bras dans ``report.json``.

    Sous 1, ``total_cost_usd`` et la médiane du même rapport sont des bornes basses.
    """
    known = sum(1 for r in rows if row_cost_known(r))
    return {"cost_known_runs": known, "cost_known_ratio": (known / len(rows)) if rows else None}


def partial_cost_notes(per_arm: dict[str, Any], arms: Sequence[str]) -> list[str]:
    """Lignes Markdown (suivies d'un blanc) qui disent quels bras ont un coût partiel ; vide sinon."""
    notes = []
    for arm in arms:
        a = per_arm.get(arm)
        ratio = a.get("cost_known_ratio") if a else None
        if a and ratio is not None and ratio < 1.0:
            total = a["n_runs"] + a["n_invalid"]
            notes.append(
                f"> Bras `{arm}` : coût connu pour {a['cost_known_runs']}/{total} runs "
                f"(cost_known_ratio {ratio:.2f}) : les coûts ci-dessus sont des bornes basses."
            )
    return [*notes, ""] if notes else []


def filter_tasks(tasks: Sequence[Any], subset_path: Path | None) -> list[Any]:
    """Restreint les tâches tirées (objets à ``task_id``) au sous-lot déclaré ; tout si ``subset_path`` est ``None``.

    Une tâche déclarée mais absente du tirage arrête avant toute dépense : un sous-lot
    amputé ne mesurerait plus le même coût.
    """
    if subset_path is None:
        return list(tasks)
    wanted = load_subset(subset_path)
    absent = sorted(set(wanted) - {t.task_id for t in tasks})
    if absent:
        raise SystemExit(f"sous-lot {subset_path} : tâches absentes du tirage ({', '.join(absent)}), aucun run lancé")
    return [t for t in tasks if t.task_id in set(wanted)]


def load_subset(path: Path = SUBSET_PATH) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks or not all(isinstance(t, str) for t in tasks):
        raise ValueError(f"{path} : « tasks » doit être une liste non vide d'identifiants de tâche")
    return list(tasks)


def _models(rows: Sequence[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    for row in rows:
        usage = row.get("model_usage")
        if isinstance(usage, dict):
            seen.update(str(k) for k in usage)
    return sorted(seen)


def _arm_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    valid = [r for r in rows if r.get("transcript_check") != "invalid"]
    resolved = [r for r in valid if r.get("success") is True]
    known = [r for r in valid if row_cost_known(r)]
    resolved_known = [float(r["total_cost_usd"]) for r in resolved if row_cost_known(r)]
    n_valid = len(valid)
    all_resolved_known = bool(resolved) and len(resolved_known) == len(resolved)
    return {
        "n_runs": n_valid,
        "n_invalid": len(rows) - n_valid,
        "n_resolved": len(resolved),
        "success_rate": (len(resolved) / n_valid) if n_valid else None,
        "cost_known_runs": len(known),
        "cost_known_ratio": (len(known) / n_valid) if n_valid else None,
        "median_cost_usd_resolved": statistics.median(resolved_known) if all_resolved_known else None,
        "mean_cost_usd_resolved": statistics.fmean(resolved_known) if all_resolved_known else None,
        "total_known_cost_usd": sum(float(r["total_cost_usd"]) for r in known),
        "models": _models(valid),
    }


def evaluate_cost_gate(
    rows: Sequence[dict[str, Any]],
    *,
    subset: Sequence[str],
    previous: dict[str, Any] | None = None,
    tolerance_points: float = DEFAULT_TOLERANCE_POINTS,
    waiver: dict[str, Any] | None = None,
    expected_runs_per_arm: int | None = None,
) -> dict[str, Any]:
    """Verdict de la garde de coût sur les lignes du sous-lot.

    ``previous`` : le ``cost-gate.json`` de la barrière précédente (ou la base
    ``docs/bench/cost-gate-baseline.json``). Sans lui, aucun seuil n'existe : le
    verdict est ``unevaluated``, jamais ``pass``.
    ``expected_runs_per_arm`` : nombre de runs attendus par bras ; un sous-lot
    incomplet (run manquant) est ``unevaluated``.
    """
    wanted = set(subset)
    in_subset = [r for r in rows if r.get("task_id") in wanted]
    arms = {arm: _arm_summary([r for r in in_subset if r.get("arm") == arm]) for arm in (NU_ARM, GOV_ARM)}
    reasons: list[str] = []
    warnings: list[str] = []

    missing_tasks = sorted(t for t in wanted if not any(r.get("task_id") == t for r in in_subset))
    if missing_tasks:
        reasons.append(f"tâches du sous-lot absentes des résultats : {', '.join(missing_tasks)}")
    for arm, summary in arms.items():
        if summary["n_runs"] == 0:
            reasons.append(f"aucun run valide pour le bras {arm}")
        elif expected_runs_per_arm is not None and summary["n_runs"] != expected_runs_per_arm:
            reasons.append(f"bras {arm} : {summary['n_runs']} runs valides au lieu de {expected_runs_per_arm}")
        elif summary["cost_known_ratio"] is not None and summary["cost_known_ratio"] < 1.0:
            reasons.append(
                f"bras {arm} : coût connu pour {summary['cost_known_runs']}/{summary['n_runs']} runs "
                f"(cost_known_ratio {summary['cost_known_ratio']:.2f}) : ratio non évaluable, un coût inconnu n'est pas zéro"
            )
        elif summary["median_cost_usd_resolved"] is None:
            reasons.append(f"bras {arm} : aucune tâche résolue, coût par tâche résolue indéfini")

    saturated = all(s["n_runs"] > 0 and s["n_resolved"] == s["n_runs"] for s in arms.values())
    if not saturated:
        warnings.append(
            "sous-lot non saturé : un bras a des échecs, le coût par tâche résolue ne compare plus les mêmes tâches"
        )

    nu_cost = arms[NU_ARM]["median_cost_usd_resolved"]
    gov_cost = arms[GOV_ARM]["median_cost_usd_resolved"]
    ratio_pct: float | None = None
    if nu_cost and gov_cost is not None and not reasons:
        ratio_pct = 100.0 * gov_cost / nu_cost
    elif nu_cost == 0:
        reasons.append("coût médian du nu nul : ratio indéfini")

    previous_pct = previous.get("ratio_pct") if previous else None
    if not isinstance(previous_pct, int | float) or isinstance(previous_pct, bool):
        previous_pct = None
        reasons.append("pas de ratio de la barrière précédente (--previous) : aucun seuil, rien n'est jugé")
    gap = None if ratio_pct is None or previous_pct is None else ratio_pct - float(previous_pct)

    models = sorted({m for s in arms.values() for m in s["models"]})
    if previous and previous.get("models") and models and sorted(previous["models"]) != models:
        warnings.append(
            f"modèles différents de la barrière précédente ({', '.join(previous['models'])} -> {', '.join(models)}) : "
            "l'écart mêle surcoût du kit et changement de modèle (voir B-01)"
        )

    if gap is not None and ratio_pct is not None:
        verdict = "exceeded" if gap > tolerance_points else "pass"
    else:
        verdict = "unevaluated"

    result: dict[str, Any] = {
        "schema": "grimoire.bench.cost-gate.v1",
        "verdict": verdict,
        "ratio_pct": ratio_pct,
        "previous_ratio_pct": previous_pct,
        "gap_points": gap,
        "tolerance_points": tolerance_points,
        "target_ratio_pct": TARGET_RATIO_PCT,
        "saturated": saturated,
        "subset": sorted(wanted),
        "arms": arms,
        "models": models,
        "reasons": reasons,
        "warnings": warnings,
        "waiver": None,
        "note": "sous-lot saturé : mesure le coût, pas le succès",
    }
    if waiver is not None and verdict in ("exceeded", "unevaluated"):
        missing = [k for k in ("by", "reason", "date") if not str(waiver.get(k) or "").strip()]
        if missing:
            result["reasons"].append(f"dérogation refusée : champs manquants {', '.join(missing)}")
        else:
            result["waiver"] = {**{k: waiver[k] for k in ("by", "reason", "date")}, "verdict_before_waiver": verdict}
            result["verdict"] = "waived"
    return result


def _pct(value: float | None) -> str:
    return "n/d" if value is None else f"{value:.1f} %"


def render_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Garde de coût face au nu (B-00)",
        "",
        f"**Verdict : {result['verdict']}**",
        "",
        f"- Ratio kit-gov / nu (coût médian par tâche résolue) : **{_pct(result['ratio_pct'])}**",
        f"- Barrière précédente : {_pct(result['previous_ratio_pct'])}",
        f"- Écart : {'n/d' if result['gap_points'] is None else format(result['gap_points'], '+.1f') + ' points'} "
        f"(tolérance {result['tolerance_points']:.1f} points, fixée par décision, pas mesurée)",
        f"- Cible de l'issue #642 : {_pct(result['target_ratio_pct'])} (information, pas un seuil de blocage ici)",
        f"- Tâches du sous-lot : {', '.join(result['subset'])}",
        f"- Modèles vus : {', '.join(result['models']) or 'inconnus'}",
        "",
        "| Bras | Runs | Résolus | Coût connu | cost_known_ratio | Coût médian/tâche résolue |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for arm, s in result["arms"].items():
        med = s["median_cost_usd_resolved"]
        ratio = s["cost_known_ratio"]
        lines.append(
            f"| {arm} | {s['n_runs']} | {s['n_resolved']} | {s['cost_known_runs']} | "
            f"{'n/d' if ratio is None else format(ratio, '.2f')} | {'inconnu' if med is None else f'{med:.3f} $'} |"
        )
    lines += ["", f"> {result['note']}."]
    if result["waiver"]:
        w = result["waiver"]
        lines += [
            "",
            f"Dérogation de {w['by']} du {w['date']} (verdict avant dérogation : {w['verdict_before_waiver']}) : {w['reason']}",
        ]
    for key, title in (("reasons", "Raisons"), ("warnings", "Avertissements")):
        if result[key]:
            lines += ["", f"## {title}", ""] + [f"- {item}" for item in result[key]]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", type=Path, required=True, help="results.jsonl du sous-lot rejoué")
    parser.add_argument("--subset", type=Path, default=SUBSET_PATH)
    parser.add_argument(
        "--previous", type=Path, default=None, help="cost-gate.json de la barrière précédente (ou la base)"
    )
    parser.add_argument("--tolerance-points", type=float, default=DEFAULT_TOLERANCE_POINTS)
    parser.add_argument(
        "--waiver", type=Path, default=None, help='JSON {"by","reason","date"} : dérogation tracée de Guilhem'
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="écrit cost-gate.json et cost-gate.md")
    args = parser.parse_args(argv)

    subset_doc = json.loads(args.subset.read_text(encoding="utf-8"))
    subset = load_subset(args.subset)
    previous = json.loads(args.previous.read_text(encoding="utf-8")) if args.previous else None
    waiver = json.loads(args.waiver.read_text(encoding="utf-8")) if args.waiver else None
    expected = len(subset) * int(subset_doc.get("k", 0)) or None
    result = evaluate_cost_gate(
        read_results_rows(args.results),
        subset=subset,
        previous=previous,
        tolerance_points=args.tolerance_points,
        waiver=waiver,
        expected_runs_per_arm=expected,
    )
    text = render_markdown(result)
    if args.out_dir:
        args.out_dir.mkdir(parents=True, exist_ok=True)
        (args.out_dir / "cost-gate.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        (args.out_dir / "cost-gate.md").write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return EXIT_CODES[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
