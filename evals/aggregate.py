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
from collections import defaultdict
from pathlib import Path
from typing import Any

RUNS = Path(__file__).resolve().parent / "runs"


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


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    j = [r["judgment"] or {} for r in rows]
    costs = [r["external"].get("tokens_cost") for r in rows]
    cost_total = round(sum(c for c in costs if c is not None), 2)
    completed = _n([x.get("completed") for x in j])
    turns = [r["run"].get("num_turns") for r in rows if r["run"].get("num_turns") is not None]
    gov = [r.get("governance") or {} for r in rows]
    std = [r.get("standard") or {} for r in rows]
    return {
        "runs": len(rows),
        "judged": sum(1 for x in j if x),
        "completed": completed,
        "tests_green": _n([r["external"].get("tests_green") for r in rows]),
        "regressions_primary": _sum([x.get("regressions_primary") for x in j]),
        "regressions_hard": _sum([x.get("regressions_hard") for x in j]),
        "regressions_adapted": _sum([x.get("regressions_adapted") for x in j]),
        "hard_candidates_mechanical": _n([(r.get("mechanical") or {}).get("hard_regression_candidate") for r in rows]),
        "cost_total": cost_total,
        "cost_per_run": round(cost_total / len(rows), 3) if rows else None,
        "cost_per_completed": round(cost_total / completed, 2) if completed else None,
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
