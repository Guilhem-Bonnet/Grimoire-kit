"""Agrégation d'une campagne — toutes les exécutions, zéro exclusion.

Lit ``evals/runs/<date>/<task>/<arm>/rep-*/record.json`` (runner) et, s'il
existe, ``judgment.json`` (juge aveugle, cf. JUDGE-CONSIGNE.md), puis imprime
les tableaux du rapport et le calcul du critère A1 entre deux bras. Ce qui
n'est pas jugé reste ``null`` et est compté comme tel.

    python evals/aggregate.py --date 2026-09-04 --tested enforced --reference activated-v3
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parent.parent
EVALS = ROOT / "evals"
RUNS = EVALS / "runs"
TASKS_DIR = EVALS / "tasks"

# pass_hat_k.py est un module local à evals/ (pas un paquet) ; l'exécution
# directe (`python evals/aggregate.py ...`) ajoute déjà son propre dossier à
# sys.path, mais un chargement dynamique (`importlib`, cf. les tests) ne le
# fait pas — insertion explicite pour marcher dans les deux cas.
sys.path.insert(0, str(EVALS))
from pass_hat_k import pass_hat_k  # noqa: E402

_yaml = YAML(typ="safe")


@lru_cache(maxsize=8)
def task_suite(witness: str) -> dict[str, Any]:
    """La suite de tâches (`evals/tasks/<witness>.yaml`) : `repetitions_min`
    (k du pass^k) et la `category` déclarée par tâche."""
    return _yaml.load((TASKS_DIR / f"{witness}.yaml").read_text(encoding="utf-8"))


def task_categories(witness: str) -> dict[str, str]:
    suite = task_suite(witness)
    return {t["id"]: t.get("category", "capability") for t in suite["tasks"]}


def load(date: str) -> list[dict[str, Any]]:
    """Runs valides uniquement ; les runs invalidés (``invalid.json``, incident
    d'infrastructure sans travail d'agent) sont listés à part par ``invalid``."""
    rows = []
    for rec_path in sorted((RUNS / date).glob("*/*/rep-*/record.json")):
        if (rec_path.parent / "invalid.json").is_file():
            continue
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        jpath = rec_path.parent / "judgment.json"
        rec["judgment"] = json.loads(jpath.read_text(encoding="utf-8")) if jpath.is_file() else None
        rec["rep"] = rec_path.parent.name
        rows.append(rec)
    return rows


def invalid(date: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for path in sorted((RUNS / date).glob("*/*/rep-*/invalid.json")):
        out.append((str(path.parent.relative_to(RUNS / date)), json.loads(path.read_text(encoding="utf-8"))))
    return out


def _n(values: list[Any]) -> int:
    return sum(1 for v in values if v is True)


def _sum(values: list[Any]) -> int | None:
    known = [v for v in values if isinstance(v, int | float)]
    return int(sum(known)) if known else None


def _pass_hat_k(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any] | None]:
    """pass^k (audit B13, docs/evals-protocol.md amendement A3) : proportion de
    tâches réussies à TOUTES leurs k répétitions exécutées et jugées,
    k = ``repetitions_min`` de la suite. Calculé sur le total et séparément
    par catégorie (``capability``/``regression``) — un run non exécuté ou
    non jugé n'est ni succès ni échec, il exclut la tâche du dénominateur
    tant qu'elle n'a pas atteint k (voir `evals.pass_hat_k.pass_hat_k`).
    """
    by_witness: dict[str, dict[str, list[bool | None]]] = defaultdict(lambda: defaultdict(list))
    k_by_witness: dict[str, int] = {}
    for r in rows:
        witness = r.get("witness")
        if not witness:
            continue
        k_by_witness.setdefault(witness, task_suite(witness)["repetitions_min"])
        by_witness[witness][r["task_id"]].append((r["judgment"] or {}).get("completed"))

    def combined(keep_category: Any) -> dict[str, Any] | None:
        merged: dict[str, list[bool | None]] = {}
        ks: set[int] = set()
        for witness, by_task in by_witness.items():
            cats = task_categories(witness)
            for task_id, outcomes in by_task.items():
                if keep_category(cats.get(task_id, "capability")):
                    merged[f"{witness}:{task_id}"] = outcomes
                    ks.add(k_by_witness[witness])
        if not merged:
            return None
        # Des suites différentes peuvent déclarer des k différents ; prendre
        # le plus petit garde le calcul défini pour toutes plutôt que de
        # faire échouer par construction une suite qui vise un k plus bas.
        return pass_hat_k(merged, min(ks)).to_dict()

    return {
        "overall": combined(lambda _c: True),
        "capability": combined(lambda c: c == "capability"),
        "regression": combined(lambda c: c == "regression"),
    }


def _cost_by_model(rows: list[dict[str, Any]]) -> dict[str, float]:
    """Coût agrégé par modèle quand le résultat de la CLI expose une
    ventilation (``modelUsage`` ou équivalent, B13). La CLI ne ventile pas
    par *sous-agent nommé* à ce jour — voir docs/evals-protocol.md amendement A3 ;
    reste vide plutôt que d'inventer une clé absente.
    """
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        usage = r["external"].get("model_usage")
        if not usage:
            continue
        for model, entry in usage.items():
            # Branch on the type before calling .get(): entry is only ever a
            # dict when the CLI nests the cost under a key, and some shapes
            # this function explicitly supports (see the docstring's "else
            # entry") give the number directly instead.
            if isinstance(entry, dict):
                cost = entry.get("costUSD") or entry.get("cost_usd") or entry.get("cost")
            else:
                cost = entry
            if isinstance(cost, int | float):
                totals[model] += cost
    return {model: round(total, 4) for model, total in totals.items()}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    j = [r["judgment"] or {} for r in rows]
    costs = [r["external"].get("tokens_cost") for r in rows]
    cost_total = round(sum(c for c in costs if c is not None), 2)
    completed = _n([x.get("completed") for x in j])
    turns = [r["run"].get("num_turns") for r in rows if r["run"].get("num_turns") is not None]
    gov = [r.get("governance") or {} for r in rows]
    std = [r.get("standard") or {} for r in rows]
    pass_k = _pass_hat_k(rows)
    cost_by_model = _cost_by_model(rows)
    return {
        "runs": len(rows),
        "judged": sum(1 for x in j if x),
        "completed": completed,
        "tests_green": _n([r["external"].get("tests_green") for r in rows]),
        "regressions_primary": _sum([x.get("regressions_primary") for x in j]),
        "regressions_hard": _sum([x.get("regressions_hard") for x in j]),
        "regressions_adapted": _sum([x.get("regressions_adapted") for x in j]),
        "hard_candidates_mechanical": _n([(r.get("mechanical") or {}).get("hard_regression_candidate") for r in rows]),
        "pass_hat_k": (pass_k["overall"] or {}).get("pass_hat_k") if pass_k["overall"] else None,
        "pass_hat_k_capability": (pass_k["capability"] or {}).get("pass_hat_k") if pass_k["capability"] else None,
        "pass_hat_k_regression": (pass_k["regression"] or {}).get("pass_hat_k") if pass_k["regression"] else None,
        "cost_total": cost_total,
        "cost_per_run": round(cost_total / len(rows), 3) if rows else None,
        "cost_per_completed": round(cost_total / completed, 2) if completed else None,
        "cost_by_model": cost_by_model or None,
        "turns_mean": round(sum(turns) / len(turns), 1) if turns else None,
        "max_turns_hit": sum(1 for r in rows if r["run"].get("subtype") == "error_max_turns"),
        "timed_out": sum(1 for r in rows if r["run"].get("timed_out")),
        "envelope_filled": _n([g.get("envelope_filled") for g in gov]),
        "context_bundle_present": _n([g.get("context_bundle_present") for g in gov]),
        "gate_ok_review": _n([s.get("gate_ok") for s in std]),
        "verify_ok": _n([s.get("verify_ok") for s in std]),
        "evidence_rows_mean": round(sum(g.get("evidence_rows") or 0 for g in gov) / len(gov), 1) if gov else None,
        "pretool_block": sum((g.get("ledger") or {}).get("pretool_block", 0) for g in gov),
        "pretool_allow": sum((g.get("ledger") or {}).get("pretool_allow", 0) for g in gov),
        "stop_block": sum((g.get("ledger") or {}).get("stop_block", 0) for g in gov),
        "runs_with_stop_block": sum(1 for g in gov if (g.get("ledger") or {}).get("stop_block", 0) > 0),
        "runs_with_stop_record": sum(
            1
            for g in gov
            if ((g.get("ledger") or {}).get("stop_block", 0) + (g.get("ledger") or {}).get("stop_allow", 0)) > 0
        ),
    }


def verdict(t: dict[str, Any], r: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    out = []
    tp, rp = t["regressions_primary"], r["regressions_primary"]
    if tp is None or rp is None:
        out.append(("Régressions primaires", "non jugé", "≤ −30 % relatif", "NON CALCULABLE"))
    elif rp == 0:
        out.append(("Régressions primaires", f"{tp} vs {rp}", "≤ −30 % relatif", "ATTEINT" if tp == 0 else "ÉCHEC"))
    else:
        delta = (tp - rp) / rp * 100
        out.append(
            (
                "Régressions primaires",
                f"{tp} vs {rp} ({delta:+.1f} %)",
                "≤ −30 % relatif",
                "ATTEINT" if delta <= -30 else "ÉCHEC",
            )
        )
    out.append(
        (
            "Complétion",
            f"{t['completed']}/{t['runs']} vs {r['completed']}/{r['runs']}",
            "non dégradée",
            "ATTEINT" if t["completed"] >= r["completed"] else "ÉCHEC",
        )
    )
    if t["runs"] and t["completed"] / t["runs"] < 0.25:
        out.append(
            (
                "Coût par tâche complétée",
                f"{t['cost_per_completed']} vs {r['cost_per_completed']} USD",
                "≤ référence (complétion ≥ 25 %)",
                "ÉCHEC (complétion < 25 %)",
            )
        )
    elif t["cost_per_completed"] is None or r["cost_per_completed"] is None:
        out.append(
            (
                "Coût par tâche complétée",
                f"{t['cost_per_completed']} vs {r['cost_per_completed']} USD",
                "≤ référence",
                "NON CALCULABLE",
            )
        )
    else:
        out.append(
            (
                "Coût par tâche complétée",
                f"{t['cost_per_completed']} vs {r['cost_per_completed']} USD",
                "≤ référence",
                "ATTEINT" if t["cost_per_completed"] <= r["cost_per_completed"] else "ÉCHEC",
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", required=True)
    ap.add_argument("--tested", default="enforced")
    ap.add_argument("--reference", default="activated-v3")
    ap.add_argument("--reps", type=int, default=None, help="Ne garder que les répétitions ≤ N (blocs complets).")
    args = ap.parse_args()
    rows = load(args.date)
    if args.reps is not None:
        rows = [r for r in rows if int(r["rep"].split("-")[1]) <= args.reps]
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    arms = sorted(by_arm)
    summaries = {a: summarize(by_arm[a]) for a in arms}
    print("## Totaux par bras\n")
    print("| Métrique | " + " | ".join(arms) + " |")
    print("| --- | " + " | ".join("---" for _ in arms) + " |")
    for key in summaries[arms[0]]:
        print(f"| {key} | " + " | ".join(str(summaries[a][key]) for a in arms) + " |")
    print("\n## Par tâche (completed / runs)\n")
    tasks = sorted({r["task_id"] for r in rows})
    print("| Tâche | " + " | ".join(arms) + " |")
    print("| --- | " + " | ".join("---" for _ in arms) + " |")
    for t in tasks:
        cells = []
        for a in arms:
            sub = [r for r in by_arm[a] if r["task_id"] == t]
            cells.append(f"{_n([(r['judgment'] or {}).get('completed') for r in sub])}/{len(sub)}")
        print(f"| {t} | " + " | ".join(cells) + " |")
    if args.tested in summaries and args.reference in summaries:
        print(f"\n## Critère A1 — {args.tested} vs {args.reference}\n")
        print("| Composante | Valeur | Seuil | Résultat |\n| --- | --- | --- | --- |")
        for row in verdict(summaries[args.tested], summaries[args.reference]):
            print("| " + " | ".join(row) + " |")
    print("\n## Runs non jugés\n")
    for r in rows:
        if not r["judgment"]:
            print(f"- {r['task_id']}/{r['arm']}/{r['rep']}")
    print("\n## Runs invalidés (hors agrégation)\n")
    for rel, info in invalid(args.date):
        print(f"- {rel} : {info.get('reason')} — {info.get('cost_usd', 0):.2f} USD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
