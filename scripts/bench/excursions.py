#!/usr/bin/env python3
"""Classe les runs d'un bras du banc en « mandat pur » ou « avec excursion » (issue #642).

Un run est « mandat pur » si son seul appel réel ``grimoire ... standard gate
check`` est le dernier appel outil de la session ; sinon (plusieurs appels gate,
ou tout appel outil après le premier) il est « avec excursion ».

Entrées : ``<workspace>/state/results.jsonl`` et les transcriptions
``<workspace>/tasks/<langue>__<tâche>/<bras>/run<k>.stream.jsonl``.

Rejeu : ``python scripts/bench/excursions.py --workspace <dossier du banc>``.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

GATE = re.compile(r"grimoire\s+(?:\S+\s+)*standard\s+gate\s+check")


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content if isinstance(x, dict))
    return ""


def gate_calls(lines: Sequence[str]) -> tuple[int, list[int]]:
    """Retourne (nombre d'appels outils, rangs 1-based des appels gate check réels)."""
    tool_calls = 0
    ranks: list[int] = []
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        for block in obj.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            tool_calls += 1
            if block.get("name") == "Bash":
                command = str(block.get("input", {}).get("command", ""))
                # une mention dans un heredoc n'est pas un appel
                if GATE.search(command.split("<<")[0]):
                    ranks.append(tool_calls)
    return tool_calls, ranks


def is_pure_mandate(tool_calls: int, ranks: Sequence[int]) -> bool:
    return len(ranks) == 1 and ranks[0] == tool_calls


def _load_results(path: Path, arm: str) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            if rec["arm"] == arm:
                out[(rec["task_id"], int(rec["run_index"]))] = rec
    return out


def classify(workspace: Path, arm: str, reference_arm: str) -> dict[str, Any]:
    results = _load_results(workspace / "state" / "results.jsonl", arm)
    reference = _load_results(workspace / "state" / "results.jsonl", reference_arm)
    runs: list[dict[str, Any]] = []
    missing: list[str] = []
    for (task, k), rec in sorted(results.items()):
        path = workspace / "tasks" / task.replace("/", "__") / arm / f"run{k}.stream.jsonl"
        if not path.is_file():
            missing.append(f"{task}#{k}")
            continue
        calls, ranks = gate_calls(path.read_text(encoding="utf-8", errors="replace").splitlines())
        ref = reference.get((task, k))
        runs.append(
            {
                "task": task,
                "run": k,
                "tool_calls": calls,
                "gate_calls": len(ranks),
                "pure_mandate": is_pure_mandate(calls, ranks),
                "cost": rec["total_cost_usd"],
                "reference_cost": ref["total_cost_usd"] if ref else None,
            }
        )
    exc = [r for r in runs if not r["pure_mandate"]]
    paired = [r for r in runs if r["reference_cost"] is not None]
    over = {id(r): r["cost"] - r["reference_cost"] for r in paired}
    total = sum(over.values())
    exc_over = sum(v for r in paired if not r["pure_mandate"] for v in [over[id(r)]])
    pure_over = [over[id(r)] for r in paired if r["pure_mandate"]]
    return {
        "arm": arm,
        "reference_arm": reference_arm,
        "runs": len(runs),
        "excursions": len(exc),
        "excursion_share": len(exc) / len(runs) if runs else 0.0,
        "overcost_total": total,
        "overcost_excursions": exc_over,
        "overcost_excursion_share": exc_over / total if total else None,
        "overcost_median_pure": statistics.median(pure_over) if pure_over else None,
        "missing_transcripts": missing,
        "detail": runs,
    }


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--workspace", type=Path, required=True, help="dossier du banc (state/results.jsonl, tasks/)")
    p.add_argument("--arm", default="kit-gov")
    p.add_argument("--reference-arm", default="nu")
    p.add_argument("--json", action="store_true", help="sortie JSON complète")
    args = p.parse_args(argv)
    if not (args.workspace / "state" / "results.jsonl").is_file():
        print(f"results.jsonl introuvable sous {args.workspace}/state", file=sys.stderr)
        return 2
    rep = classify(args.workspace, args.arm, args.reference_arm)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    share = rep["overcost_excursion_share"]
    print(f"{rep['arm']} : {rep['excursions']}/{rep['runs']} runs avec excursion ({rep['excursion_share']:.1%})")
    if share is not None:
        print(
            f"surcoût vs {rep['reference_arm']} : {rep['overcost_total']:+.2f} $ au total, "
            f"{rep['overcost_excursions']:+.2f} $ portés par les excursions ({share:.0%}), "
            f"médiane mandat pur {rep['overcost_median_pure']:+.3f} $"
        )
    if rep["missing_transcripts"]:
        print(f"{len(rep['missing_transcripts'])} run(s) sans transcription ignoré(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
