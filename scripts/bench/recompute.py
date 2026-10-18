"""Recalcul d'un rapport du banc depuis ses données brutes, hash des données, puissance (B-02).

    python scripts/bench/recompute.py --results state/results.jsonl --report reports/<date>/report.json

Le rapport publié (``report.json``) est reconstruit par le MÊME ``build_report`` que celui qui l'a
écrit, à partir de ``results.jsonl`` seul. Toute valeur dérivée qui ne se recalcule pas à l'identique
est un écart nommé (chemin, valeur publiée, valeur recalculée) et la sortie est 1 : un rapport
retouché à la main, ou produit d'un autre jeu de lignes, ne passe pas.

Trois contrôles, un verdict :

1. ``recalcul`` : les champs dérivés (``DERIVED_KEYS``) se recalculent à l'identique (flottants à 1e-9).
2. ``hash`` : le rapport cite ``data.results_sha256`` (sha256 des octets de ``results.jsonl``, écrit par
   ``write_report``) ; un fichier modifié depuis donne ``mismatch``. Un rapport sans hash cité (antérieur à
   B-02) est « NON SCELLÉ » : ses chiffres se recalculent, mais rien ne prouve que le fichier est celui
   d'origine ; ``--require-seal`` en fait une erreur (barrière 3).
3. ``clés dupliquées`` : deux lignes pour la même (tâche, bras, rejeu) gonflent les effectifs sans qu'aucun
   recalcul ne le voie (il compte les mêmes lignes) : c'est une erreur, pas un détail.

Limite écrite : le hash cité dans le rapport est aussi réécrivable que le rapport. Il rend la
falsification visible à qui compare avec une copie versionnée des données (ou un hash publié ailleurs,
par exemple dans la description de la PR), il ne la rend pas impossible. Les entrées du rapport qui ne
se déduisent pas des lignes (``INPUT_KEYS`` : graine, étiquette, coût attendu, contrôle de toolchain,
bras repris) ne sont pas recalculées et sont listées comme telles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

# Modules voisins, aussi quand ce script est chargé par chemin.
sys.path.append(str(Path(__file__).resolve().parent))
import power_calc as power
from model_pin import ModelMismatchError
from results_io import read_results_rows

#: Champs de ``report.json`` qui se déduisent de ``results.jsonl`` seul : recalculés et comparés.
DERIVED_KEYS = (
    "k",
    "tasks_run",
    "per_arm",
    "by_task",
    "kit_runs",
    "kit_gov_runs",
    "invalid_runs",
    "unevaluated_runs",
    "auth_modes",
    "model_id",
    "claude_code_versions",
    "suite_saturation",
)
#: Champs fournis par l'exécution (pas par les lignes) : repris tels quels, non recalculés.
INPUT_KEYS = ("generated_at", "seed", "label", "total_tasks", "expected_cost", "agent_toolchain_check", "carried_over_notes")
REQUIRED_INPUTS = ("seed", "total_tasks")
FLOAT_TOLERANCE = 1e-9
DEFAULT_ARMS = ("nu", "kit-gov")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_keys(lines: Sequence[str]) -> Counter[tuple[Any, ...]]:
    keys: Counter[tuple[Any, ...]] = Counter()
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        keys[(row.get("task_id"), row.get("arm"), row.get("run_index")) if isinstance(row, dict) else ("(illisible)", line)] += 1
    return keys


def data_seal(results_path: Path, records: Sequence[Any] | None = None) -> dict[str, Any]:
    """Bloc ``data`` cité par le rapport : nom du fichier, nombre de lignes, sha256 des octets.

    Si ``records`` (les lignes que le rapport agrège vraiment) est donné et ne correspond pas au fichier
    (même nombre de lignes, mêmes clés tâche/bras/rejeu), le fichier ne peut pas être scellé comme source
    du rapport (pilote puis ``--full`` sans ``--resume``, ``--arms`` partiel...) : le bloc le dit
    (``sealed: false``) et ne cite aucun hash, au lieu d'un hash « ok » sur des données que le rapport
    n'a pas agrégées.
    """
    rows = [line for line in results_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if records is not None:
        report_keys = Counter((r.task_id, r.arm, r.run_index) for r in records)
        if _row_keys(rows) != report_keys:
            return {
                "results_file": results_path.name,
                "results_rows": len(rows),
                "sealed": False,
                "report_rows": len(records),
                "reason": "records ≠ fichier",
            }
    return {"results_file": results_path.name, "results_rows": len(rows), "results_sha256": file_sha256(results_path)}


def render_seal_lines(report: dict[str, Any]) -> list[str]:
    """Ligne Markdown qui cite le hash des données brutes (vide pour un rapport antérieur à B-02)."""
    seal = report.get("data")
    if not seal:
        return []
    if seal.get("sealed") is False:
        return [
            f"Données brutes : `{seal['results_file']}` ({seal['results_rows']} lignes) NON SCELLÉ : le rapport agrège "
            f"{seal['report_rows']} lignes qui ne correspondent pas au fichier ({seal['reason']}), aucun hash cité.",
            "",
        ]
    return [
        f"Données brutes : `{seal['results_file']}`, {seal['results_rows']} lignes, "
        f"sha256 `{seal['results_sha256']}` (`python scripts/bench/recompute.py` les recalcule).",
        "",
    ]


def _differences(
    published: Any, recomputed: Any, path: str, unpublished: list[str] | None = None
) -> list[dict[str, Any]]:
    """Écarts entre rapport publié et recalcul.

    Un champ absent du rapport publié, ou publié à ``null`` alors que le recalcul en donne une valeur,
    n'est pas un écart : le rapport est d'un schéma antérieur au champ (``cost_known_*``, ``n_invalid``,
    ``suite_saturation``...). Son chemin est ajouté à ``unpublished`` (s'il est fourni), le verdict le liste
    à part. Une valeur publiée non nulle qui diffère reste un écart.
    """
    if published is None and recomputed is not None:
        if unpublished is not None:
            unpublished.append(path)
        return []
    if isinstance(published, dict) and isinstance(recomputed, dict):
        found: list[dict[str, Any]] = []
        for key in sorted({*published, *recomputed}, key=str):
            if key not in published:
                if unpublished is not None:
                    unpublished.append(f"{path}.{key}")
            elif key not in recomputed:
                found.append({"path": f"{path}.{key}", "published": published[key], "recomputed": "(absent)"})
            else:
                found.extend(_differences(published[key], recomputed[key], f"{path}.{key}", unpublished))
        return found
    if isinstance(published, list) and isinstance(recomputed, list) and len(published) == len(recomputed):
        found = []
        for index, (a, b) in enumerate(zip(published, recomputed, strict=True)):
            found.extend(_differences(a, b, f"{path}[{index}]", unpublished))
        return found
    number = (int, float)
    if (
        isinstance(published, number)
        and isinstance(recomputed, number)
        and not isinstance(published, bool)
        and not isinstance(recomputed, bool)
    ):
        if math.isclose(published, recomputed, rel_tol=FLOAT_TOLERANCE, abs_tol=FLOAT_TOLERANCE):
            return []
    elif published == recomputed:
        return []
    return [{"path": path, "published": published, "recomputed": recomputed}]


def markdown_differences(published: str, rendered: str) -> list[dict[str, Any]]:
    """Lignes de ``report.md`` qui diffèrent du Markdown régénéré (numéro, publié, recalculé)."""
    a, b = published.splitlines(), rendered.splitlines()
    found = [
        {"line": i + 1, "published": a[i] if i < len(a) else "(absente)", "recomputed": b[i] if i < len(b) else "(absente)"}
        for i in range(max(len(a), len(b)))
        if (a[i] if i < len(a) else None) != (b[i] if i < len(b) else None)
    ]
    return found


def _arm_rate(by_task: dict[str, Any], arm: str) -> float | None:
    values = [row[arm]["pass_hat_k"] for row in by_task.values() if row.get(arm, {}).get("runs")]
    return sum(values) / len(values) if values else None


def _load_three_arms() -> ModuleType:
    import three_arms  # import tardif : three_arms importe ce module (hash cité dans write_report)

    return three_arms


def recompute_report(
    results_path: Path,
    report_path: Path,
    *,
    ta: ModuleType | None = None,
    target_mde: float = power.DEFAULT_TARGET_MDE,
    alpha: float = power.DEFAULT_ALPHA,
    power_level: float = power.DEFAULT_POWER,
    sides: int = power.DEFAULT_SIDES,
    arms: Sequence[str] = DEFAULT_ARMS,
    require_seal: bool = False,
    report_md_path: Path | None = None,
) -> dict[str, Any]:
    """Verdict de recalcul. ``ok`` n'est vrai que si recalcul, hash, clés et Markdown sont sans écart.

    ``report_md_path`` : le ``report.md`` publié, régénéré par ``render_report_markdown`` depuis le rapport
    recalculé et comparé ligne à ligne (``generated_at`` repris du rapport publié). ``None`` = non vérifié.
    """
    ta = ta or _load_three_arms()
    published = json.loads(report_path.read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_INPUTS if published.get(key) is None]
    if missing:
        return {"ok": False, "error": f"rapport illisible : entrée(s) absente(s) {missing}", "mismatches": []}
    rows = read_results_rows(results_path)
    seal = published.get("data")
    if isinstance(seal, dict) and seal.get("sealed") is False:
        # Le rapport dit lui-même que ce fichier n'est pas la source de ses lignes : rien à recalculer ici,
        # et le dire vaut mieux qu'un REFUSÉ sur des écarts qui ne viennent pas d'une falsification.
        return {
            "ok": not require_seal,
            "recomputed": False,
            "mismatches": [],
            "unpublished": [],
            "hash": {
                "status": "non scellé",
                "reason": seal.get("reason"),
                "cited": seal,
                "actual_sha256": file_sha256(results_path),
                "actual_rows": len(rows),
            },
            "duplicate_keys": [],
            "markdown": {"status": "non vérifié", "differences": []},
            "seal_required_but_missing": require_seal,
            "not_recomputed": list(DERIVED_KEYS) + [key for key in INPUT_KEYS if key in published],
            "cost_known_ratio": {},
            "power": None,
            "arms_compared": list(arms[:2]),
        }
    try:
        records = [ta.RunRecord(**row) for row in rows]
    except TypeError as exc:  # champ inconnu ou absent : ligne qui n'est pas de ce banc
        return {"ok": False, "error": f"ligne de {results_path.name} illisible : {exc}", "mismatches": []}
    keys = Counter((r.task_id, r.arm, r.run_index) for r in records)
    duplicates = sorted(f"{task}/{arm}/{index}" for (task, arm, index), n in keys.items() if n > 1)

    try:
        rebuilt = ta.build_report(
            records,
            total_tasks=published["total_tasks"],
            expected_cost=published.get("expected_cost"),
            seed=published["seed"],
            label=published.get("label"),
            toolchain_check=published.get("agent_toolchain_check"),
        )
    except ModelMismatchError as exc:
        return {"ok": False, "error": str(exc), "mismatches": []}
    rebuilt = json.loads(json.dumps(rebuilt))  # mêmes types qu'après relecture du JSON

    mismatches: list[dict[str, Any]] = []
    unpublished: list[str] = []
    for key in DERIVED_KEYS:
        mismatches.extend(_differences(published.get(key), rebuilt.get(key), key, unpublished))

    markdown: dict[str, Any] = {"status": "non vérifié", "differences": []}
    if report_md_path is not None:
        inputs = {key: published[key] for key in INPUT_KEYS if key in published}
        rendered = ta.render_report_markdown({**rebuilt, **inputs, "data": seal} if seal else {**rebuilt, **inputs})
        md_diffs = markdown_differences(report_md_path.read_text(encoding="utf-8"), rendered)
        markdown = {"status": "écart" if md_diffs else "ok", "differences": md_diffs}

    actual_sha = file_sha256(results_path)
    if not seal:
        hash_status = "non scellé"
    elif seal.get("results_sha256") == actual_sha and seal.get("results_rows") == len(rows):
        hash_status = "ok"
    else:
        hash_status = "mismatch"

    rates = [_arm_rate(rebuilt["by_task"], arm) for arm in arms[:2]]
    capacity = power.power_report(
        tasks_run=rebuilt["tasks_run"],
        rate_a=rates[0] if rates else None,
        rate_b=rates[1] if len(rates) > 1 else None,
        target_mde=target_mde,
        alpha=alpha,
        power=power_level,
        sides=sides,
    )
    seal_missing = require_seal and hash_status == "non scellé"
    return {
        "ok": not mismatches
        and hash_status != "mismatch"
        and not duplicates
        and not seal_missing
        and markdown["status"] != "écart",
        "recomputed": True,
        "mismatches": mismatches,
        "unpublished": unpublished,
        "markdown": markdown,
        "hash": {"status": hash_status, "cited": seal, "actual_sha256": actual_sha, "actual_rows": len(rows)},
        "duplicate_keys": duplicates,
        "seal_required_but_missing": seal_missing,
        "not_recomputed": [key for key in INPUT_KEYS if key in published],
        "cost_known_ratio": {arm: stats.get("cost_known_ratio") for arm, stats in rebuilt["per_arm"].items()},
        "power": capacity,
        "arms_compared": list(arms[:2]),
    }


def render_verdict_lines(verdict: dict[str, Any]) -> list[str]:
    if verdict.get("error"):
        return [f"ÉCHEC : {verdict['error']}"]
    if verdict.get("recomputed") is False:
        hash_info = verdict["hash"]
        return [
            "RECALCUL : NON EFFECTUÉ, le rapport déclare que le fichier n'est pas la source de ses lignes "
            f"({hash_info['reason']}) : {hash_info['cited']['report_rows']} lignes agrégées, "
            f"{hash_info['actual_rows']} dans le fichier.",
            f"HASH : NON SCELLÉ — sha256 du fichier {hash_info['actual_sha256']}, non cité par le rapport.",
            "VERDICT : " + ("NON SCELLÉ (rien n'a été recalculé)" if verdict["ok"] else "REFUSÉ (--require-seal)"),
        ]
    lines = ["RECALCUL : " + ("OK, tous les champs dérivés se recalculent." if not verdict["mismatches"] else "ÉCART.")]
    for diff in verdict["mismatches"][:50]:
        lines.append(f"  - {diff['path']} : publié {diff['published']!r}, recalculé {diff['recomputed']!r}")
    if len(verdict["mismatches"]) > 50:
        lines.append(f"  - ... et {len(verdict['mismatches']) - 50} autre(s) écart(s)")
    if verdict["unpublished"]:
        shown = ", ".join(verdict["unpublished"][:8])
        more = f" (+{len(verdict['unpublished']) - 8})" if len(verdict["unpublished"]) > 8 else ""
        lines.append(f"NON PUBLIÉS (schéma antérieur, pas un écart) : {len(verdict['unpublished'])} champ(s) : {shown}{more}.")
    md = verdict["markdown"]
    if md["status"] == "non vérifié":
        lines.append("MARKDOWN : NON VÉRIFIÉ (aucun report.md).")
    else:
        lines.append("MARKDOWN : " + ("OK, report.md identique au rendu recalculé." if md["status"] == "ok" else "ÉCART."))
        for diff in md["differences"][:20]:
            lines.append(f"  - ligne {diff['line']} : publié {diff['published']!r}, recalculé {diff['recomputed']!r}")
        if len(md["differences"]) > 20:
            lines.append(f"  - ... et {len(md['differences']) - 20} autre(s) ligne(s)")
    hash_info = verdict["hash"]
    lines.append(f"HASH : {hash_info['status'].upper()} — sha256 des données {hash_info['actual_sha256']} ({hash_info['actual_rows']} lignes).")
    if hash_info["status"] == "non scellé":
        lines.append("  Le rapport ne cite pas le hash de ses données : rien ne prouve que le fichier est celui d'origine.")
    if hash_info["status"] == "mismatch":
        lines.append(f"  Cité par le rapport : {hash_info['cited']}")
    if verdict["duplicate_keys"]:
        lines.append(f"CLÉS DUPLIQUÉES : {', '.join(verdict['duplicate_keys'][:20])} (effectifs gonflés).")
    ratios = ", ".join(
        f"{arm} {'inconnu' if ratio is None else f'{ratio:.2f}'}" for arm, ratio in verdict["cost_known_ratio"].items()
    )
    lines.append(f"cost_known_ratio : {ratios} (sous 1, les coûts du rapport sont des bornes basses).")
    lines.append(f"Entrées non recalculées : {', '.join(verdict['not_recomputed']) or 'aucune'}.")
    lines.append(f"Comparaison de puissance : {' vs '.join(verdict['arms_compared'])}.")
    lines.extend(power.render_power_lines(verdict["power"]))
    lines.append("VERDICT : " + ("OK" if verdict["ok"] else "REFUSÉ"))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", type=Path, required=True, help="results.jsonl (données brutes)")
    parser.add_argument("--report", type=Path, required=True, help="report.json publié")
    parser.add_argument(
        "--report-md", type=Path, default=None, help="report.md publié (défaut : report.md à côté de report.json, s'il existe)"
    )
    parser.add_argument("--mde", type=float, default=power.DEFAULT_TARGET_MDE, help="effet visé (0.10 = 10 points)")
    parser.add_argument("--alpha", type=float, default=power.DEFAULT_ALPHA)
    parser.add_argument("--power", type=float, default=power.DEFAULT_POWER, dest="power_level")
    parser.add_argument("--sides", type=int, choices=(1, 2), default=power.DEFAULT_SIDES)
    parser.add_argument("--arms", default=",".join(DEFAULT_ARMS), help="deux bras comparés pour la variance observée")
    parser.add_argument("--require-seal", action="store_true", help="un rapport qui ne cite pas le hash de ses données est refusé")
    parser.add_argument("--json", action="store_true", help="verdict complet en JSON")
    args = parser.parse_args(argv)
    for path in (args.results, args.report):
        if not path.is_file():
            print(f"[recompute] fichier introuvable : {path}", file=sys.stderr)
            return 1
    report_md = args.report_md
    if report_md is None:
        sibling = args.report.with_name("report.md")
        report_md = sibling if sibling.is_file() else None
    elif not report_md.is_file():
        print(f"[recompute] fichier introuvable : {report_md}", file=sys.stderr)
        return 1
    arms = tuple(a.strip() for a in args.arms.split(",") if a.strip())
    if len(arms) != 2:
        parser.error("--arms attend exactement deux bras séparés par une virgule")
    try:
        verdict = recompute_report(
            args.results,
            args.report,
            target_mde=args.mde,
            alpha=args.alpha,
            power_level=args.power_level,
            sides=args.sides,
            arms=arms,
            require_seal=args.require_seal,
            report_md_path=report_md,
        )
    except ValueError as exc:  # ligne tronquée de results.jsonl
        print(f"[recompute] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(verdict, indent=2, ensure_ascii=False) if args.json else "\n".join(render_verdict_lines(verdict)))
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
