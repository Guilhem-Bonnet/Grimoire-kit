#!/usr/bin/env python3
"""
cc-feedback.py — Boucle de feedback après vérification du Completion Contract.
===============================================================================

Après un CC PASS ou CC FAIL, capture le résultat et enrichit automatiquement :
- En cas de FAIL → propose un ajout au Failure Museum
- En cas de PASS → incrémente les stats de qualité
- Historise chaque vérification pour le suivi long terme

Usage :
  python3 cc-feedback.py --project-root . record --result pass --stack python --details "42 tests, ruff clean"
  python3 cc-feedback.py --project-root . record --result fail --stack go --details "TestAuth FAIL" --root-cause "nil pointer"
  python3 cc-feedback.py --project-root . history
  python3 cc-feedback.py --project-root . history --last 5
  python3 cc-feedback.py --project-root . stats
  python3 cc-feedback.py --project-root . trend

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger("grimoire.cc_feedback")

CC_FEEDBACK_VERSION = "1.0.0"
FEEDBACK_DIR = "_grimoire/_memory"
FEEDBACK_FILE = "cc-feedback.jsonl"

# ── Data Model ───────────────────────────────────────────────────────────────


@dataclass
class CCRecord:
    """Un enregistrement de vérification CC."""

    record_id: str = ""
    timestamp: str = ""
    result: str = ""        # pass | fail
    stack: str = ""         # python, go, ts, terraform, etc.
    details: str = ""       # Résumé des résultats
    root_cause: str = ""    # Si FAIL, cause racine identifiée
    fix_applied: str = ""   # Correctif appliqué
    duration_s: float = 0.0
    agent: str = ""
    museum_entry: str = ""  # FM-XXX si enregistré dans failure-museum


# ── Persistence ──────────────────────────────────────────────────────────────


def _feedback_path(root: Path) -> Path:
    d = root / FEEDBACK_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / FEEDBACK_FILE


def load_records(root: Path) -> list[CCRecord]:
    """Charge l'historique des vérifications CC."""
    path = _feedback_path(root)
    if not path.exists():
        return []
    records: list[CCRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            records.append(CCRecord(**data))
        except (json.JSONDecodeError, TypeError) as exc:
            _log.debug("Skipping malformed record: %s", exc)
    return records


def _next_id(records: list[CCRecord]) -> str:
    """Génère le prochain ID CC-XXX."""
    if not records:
        return "CC-001"
    max_seq = 0
    for r in records:
        if r.record_id.startswith("CC-"):
            try:
                seq = int(r.record_id[3:])
                max_seq = max(max_seq, seq)
            except ValueError:
                pass
    return f"CC-{max_seq + 1:03d}"


def save_record(root: Path, record: CCRecord) -> None:
    """Ajoute un enregistrement CC au journal."""
    path = _feedback_path(root)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")


# ── Core Logic ───────────────────────────────────────────────────────────────


def record_cc(
    root: Path,
    result: str,
    stack: str,
    details: str = "",
    root_cause: str = "",
    fix_applied: str = "",
    duration_s: float = 0.0,
    agent: str = "",
) -> CCRecord:
    """Enregistre un résultat CC et enrichit les learnings.

    Args:
        root: Racine du projet.
        result: "pass" ou "fail".
        stack: Stack vérifié (python, go, ts, etc.).
        details: Résumé des résultats.
        root_cause: Cause racine si FAIL.
        fix_applied: Correctif appliqué si FAIL.
        duration_s: Durée de la vérification.
        agent: Agent qui a exécuté la vérification.

    Returns:
        CCRecord enregistré.
    """
    records = load_records(root)
    record = CCRecord(
        record_id=_next_id(records),
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        result=result.lower(),
        stack=stack,
        details=details,
        root_cause=root_cause,
        fix_applied=fix_applied,
        duration_s=duration_s,
        agent=agent,
    )

    # Si FAIL avec root_cause → proposer au failure-museum
    if record.result == "fail" and root_cause:
        museum_id = _register_to_museum(root, record)
        if museum_id:
            record.museum_entry = museum_id

    save_record(root, record)
    return record


def _register_to_museum(root: Path, record: CCRecord) -> str:
    """Tente d'enregistrer l'échec dans le failure-museum."""
    try:
        import importlib.util
        import sys as _sys

        museum_path = root / "framework" / "tools" / "failure-museum.py"
        if not museum_path.exists():
            return ""

        spec = importlib.util.spec_from_file_location("failure_museum", museum_path)
        if not spec or not spec.loader:
            return ""
        mod = importlib.util.module_from_spec(spec)
        _sys.modules["failure_museum"] = mod
        spec.loader.exec_module(mod)

        entries = mod.load_failures(root)
        fid, seq = mod.next_failure_id(entries)

        failure = mod.Failure(
            failure_id=fid,
            sequence=seq,
            timestamp=record.timestamp,
            title=f"CC FAIL [{record.stack}] — {record.details[:80]}",
            severity="medium",
            agents=[record.agent] if record.agent else ["dev"],
            description=record.details,
            root_cause=record.root_cause,
            fix=record.fix_applied or "En attente de correction",
            rule_added=f"Vérifier {record.stack} : {record.root_cause[:100]}",
            tags=["cc-feedback", record.stack],
            status="open" if not record.fix_applied else "resolved",
        )

        mod.save_failure(root, failure)
        return fid
    except Exception as exc:
        _log.debug("Failed to register to failure-museum: %s", exc)
        return ""


# ── Analytics ────────────────────────────────────────────────────────────────


def compute_stats(records: list[CCRecord]) -> dict:
    """Calcule les statistiques CC."""
    if not records:
        return {"total": 0, "pass": 0, "fail": 0, "pass_rate": 0.0, "by_stack": {}}

    total = len(records)
    passes = sum(1 for r in records if r.result == "pass")
    fails = total - passes

    by_stack: dict[str, dict] = {}
    for r in records:
        s = r.stack or "unknown"
        if s not in by_stack:
            by_stack[s] = {"pass": 0, "fail": 0, "total": 0}
        by_stack[s]["total"] += 1
        by_stack[s][r.result] = by_stack[s].get(r.result, 0) + 1

    return {
        "total": total,
        "pass": passes,
        "fail": fails,
        "pass_rate": round(passes / total, 3) if total else 0.0,
        "by_stack": by_stack,
    }


def compute_trend(records: list[CCRecord], window: int = 10) -> dict:
    """Calcule la tendance récente (dernières N vérifications)."""
    recent = records[-window:]
    if not recent:
        return {"window": window, "pass_rate": 0.0, "trend": "neutral", "count": 0}

    rate = sum(1 for r in recent if r.result == "pass") / len(recent)

    # Compare avec la période précédente
    prev = records[-2 * window:-window]
    if prev:
        prev_rate = sum(1 for r in prev if r.result == "pass") / len(prev)
        if rate > prev_rate + 0.1:
            trend = "improving"
        elif rate < prev_rate - 0.1:
            trend = "degrading"
        else:
            trend = "stable"
    else:
        trend = "neutral"

    return {
        "window": window,
        "pass_rate": round(rate, 3),
        "trend": trend,
        "count": len(recent),
    }


# ── MCP Interface ───────────────────────────────────────────────────────────


def mcp_cc_feedback(
    project_root: str,
    action: str = "stats",
    result: str = "",
    stack: str = "",
    details: str = "",
    root_cause: str = "",
    fix_applied: str = "",
    agent: str = "",
) -> dict:
    """MCP tool ``bmad_cc_feedback`` — enregistre et consulte les résultats CC.

    Args:
        project_root: Racine du projet.
        action: record | stats | history | trend.
        result: pass ou fail (pour action=record).
        stack: Stack vérifié (pour action=record).
        details: Détails optionnels.
        root_cause: Cause racine (pour FAIL).
        fix_applied: Correctif appliqué.
        agent: Agent exécutant.

    Returns:
        dict avec le résultat de l'action.
    """
    root = Path(project_root)

    if action == "record":
        if not result or not stack:
            return {"status": "error", "error": "result and stack required"}
        rec = record_cc(root, result, stack, details, root_cause, fix_applied, agent=agent)
        return {"status": "ok", "record": asdict(rec)}

    if action == "stats":
        records = load_records(root)
        return {"status": "ok", **compute_stats(records)}

    if action == "history":
        records = load_records(root)
        return {"status": "ok", "records": [asdict(r) for r in records[-20:]]}

    if action == "trend":
        records = load_records(root)
        return {"status": "ok", **compute_trend(records)}

    return {"status": "error", "error": f"Unknown action: {action}"}


# ── Display ──────────────────────────────────────────────────────────────────


def display_history(records: list[CCRecord], last: int = 20) -> None:
    """Affiche l'historique CC."""
    shown = records[-last:]
    if not shown:
        print("Aucun enregistrement CC.")
        return

    print("\n📋 Historique CC")
    print("=" * 70)
    for r in shown:
        icon = "✅" if r.result == "pass" else "🔴"
        museum = f" → {r.museum_entry}" if r.museum_entry else ""
        print(f"  {r.record_id}  {icon} {r.result:4s}  [{r.stack:12s}]  {r.timestamp[:16]}  {r.details[:40]}{museum}")
    print()


def display_stats(stats: dict) -> None:
    """Affiche les statistiques CC."""
    print("\n📊 Statistiques CC")
    print("=" * 50)
    print(f"  Total : {stats['total']}")
    print(f"  Pass  : {stats['pass']}  ({stats['pass_rate']:.0%})")
    print(f"  Fail  : {stats['fail']}")
    if stats.get("by_stack"):
        print("\n  Par stack :")
        for stack, data in stats["by_stack"].items():
            rate = data["pass"] / data["total"] if data["total"] else 0
            print(f"    {stack:12s}  {data['total']:3d} total  {rate:.0%} pass")
    print()


def display_trend(trend: dict) -> None:
    """Affiche la tendance CC."""
    icons = {"improving": "📈", "degrading": "📉", "stable": "➡️", "neutral": "⚪"}
    print(f"\n{icons.get(trend['trend'], '⚪')} Tendance ({trend['window']} dernières) : {trend['trend']} — {trend['pass_rate']:.0%} pass rate\n")


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cc-feedback",
        description="CC Feedback — Boucle de feedback Completion Contract",
    )
    p.add_argument("--project-root", type=Path, default=Path())
    p.add_argument("--json", action="store_true")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {CC_FEEDBACK_VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    rec = sub.add_parser("record", help="Enregistrer un résultat CC")
    rec.add_argument("--result", required=True, choices=["pass", "fail"])
    rec.add_argument("--stack", required=True)
    rec.add_argument("--details", default="")
    rec.add_argument("--root-cause", default="")
    rec.add_argument("--fix-applied", default="")
    rec.add_argument("--agent", default="")

    hist = sub.add_parser("history", help="Afficher l'historique CC")
    hist.add_argument("--last", type=int, default=20)

    sub.add_parser("stats", help="Statistiques agrégées")
    sub.add_parser("trend", help="Tendance récente")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    if args.command == "record":
        rec = record_cc(
            root, args.result, args.stack,
            details=args.details,
            root_cause=args.root_cause,
            fix_applied=args.fix_applied,
            agent=args.agent,
        )
        if getattr(args, "json", False):
            print(json.dumps(asdict(rec), indent=2, ensure_ascii=False))
        else:
            icon = "✅" if rec.result == "pass" else "🔴"
            print(f"{icon} {rec.record_id} enregistré — {rec.result} [{rec.stack}]")
            if rec.museum_entry:
                print(f"   → Ajouté au Failure Museum : {rec.museum_entry}")
        return 0

    if args.command == "history":
        records = load_records(root)
        if getattr(args, "json", False):
            print(json.dumps([asdict(r) for r in records[-args.last:]], indent=2, ensure_ascii=False))
        else:
            display_history(records, args.last)
        return 0

    if args.command == "stats":
        records = load_records(root)
        stats = compute_stats(records)
        if getattr(args, "json", False):
            print(json.dumps(stats, indent=2, ensure_ascii=False))
        else:
            display_stats(stats)
        return 0

    if args.command == "trend":
        records = load_records(root)
        trend = compute_trend(records)
        if getattr(args, "json", False):
            print(json.dumps(trend, indent=2, ensure_ascii=False))
        else:
            display_trend(trend)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
