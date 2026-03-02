#!/usr/bin/env python3
"""
nso.py — Nervous System Orchestrator BMAD.
============================================

Méta-outil qui orchestre l'ensemble du système nerveux en une seule commande :
  1. Dream (consolidation hors-session)
  2. Stigmergy evaporate (nettoyage des phéromones mortes)
  3. Antifragile score (évaluation de la résilience)
  4. Agent Darwinism (évaluation fitness des agents)
  5. Memory Lint (vérification de cohérence mémoire)

Produit un rapport unifié et optionnellement un JSON structuré.

Usage :
  python3 nso.py --project-root . run              # Exécution complète
  python3 nso.py --project-root . run --quick       # Mode rapide (dream quick)
  python3 nso.py --project-root . run --json        # Sortie JSON unifiée
  python3 nso.py --project-root . run --emit        # Émettre les résultats vers stigmergy
  python3 nso.py --project-root . run --since auto  # Depuis le dernier dream

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

NSO_VERSION = "1.0.0"

# Phases d'exécution dans l'ordre
PHASES = [
    "dream",
    "stigmergy",
    "antifragile",
    "darwinism",
    "memory-lint",
]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class PhaseResult:
    """Résultat d'une phase d'exécution."""
    name: str
    status: str           # ok | skip | error
    duration_ms: int = 0
    summary: str = ""
    data: dict = field(default_factory=dict)
    error: str = ""


@dataclass
class NSOReport:
    """Rapport unifié du Nervous System Orchestrator."""
    version: str = NSO_VERSION
    timestamp: str = ""
    total_duration_ms: int = 0
    phases: list[PhaseResult] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for p in self.phases if p.status == "ok")

    @property
    def error_count(self) -> int:
        return sum(1 for p in self.phases if p.status == "error")


# ── Module loader ─────────────────────────────────────────────────────────────

def _load_tool(name: str) -> object | None:
    """Charge dynamiquement un outil Python co-localisé.

    Utilise sys.modules comme cache pour éviter les rechargements
    répétés et la fuite mémoire associée.
    """
    mod_name = name.replace("-", "_")
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    tool_path = Path(__file__).parent / f"{name}.py"
    if not tool_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location(mod_name, tool_path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        # Register in sys.modules BEFORE exec — required for @dataclass resolution
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# ── Phase executors ───────────────────────────────────────────────────────────

def _run_dream(project_root: Path | str, since: str | None = None,
               quick: bool = False, emit: bool = False) -> PhaseResult:
    """Phase 1 : Dream Mode."""
    project_root = Path(project_root)
    start = time.monotonic()
    mod = _load_tool("dream")
    if mod is None:
        return PhaseResult(name="dream", status="skip",
                           summary="dream.py introuvable")

    try:
        # Résoudre --since auto
        effective_since = since
        if since == "auto":
            effective_since = mod.read_last_dream_timestamp(project_root)

        sources = mod.collect_sources(project_root, effective_since)
        if not sources:
            return PhaseResult(
                name="dream", status="ok",
                duration_ms=int((time.monotonic() - start) * 1000),
                summary="Aucune source mémoire",
                data={"insights": 0, "sources": 0},
            )

        if quick:
            insights = mod.dream_quick(project_root, effective_since,
                                       _sources=sources)
        else:
            insights = mod.dream(project_root, effective_since,
                                 do_validate=True, _sources=sources)

        # Dream memory
        dream_diff = None
        memory = mod.load_dream_memory(project_root)
        dream_diff = mod.update_dream_memory(insights, memory)
        mod.save_dream_memory(project_root, memory)

        # Emit
        emitted = 0
        if emit and insights:
            emitted = mod.emit_to_stigmergy(insights, project_root)

        # Journal
        if insights:
            journal = mod.render_journal(insights, sources, project_root,
                                         effective_since,
                                         dream_diff=dream_diff)
            mod.write_journal(journal, project_root)
            mod.save_last_dream_timestamp(project_root)

        diff_summary = ""
        if dream_diff:
            new_c = len(dream_diff.get("new", []))
            pers_c = len(dream_diff.get("persistent", []))
            res_c = len(dream_diff.get("resolved", []))
            diff_summary = f" (new: {new_c}, persistent: {pers_c}, resolved: {res_c})"

        return PhaseResult(
            name="dream", status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            summary=f"{len(insights)} insights{diff_summary}",
            data={
                "insights": len(insights),
                "sources": len(sources),
                "emitted": emitted,
                "diff": {
                    "new": len(dream_diff.get("new", [])) if dream_diff else 0,
                    "persistent": len(dream_diff.get("persistent", [])) if dream_diff else 0,
                    "resolved": len(dream_diff.get("resolved", [])) if dream_diff else 0,
                },
            },
        )
    except Exception as e:
        return PhaseResult(
            name="dream", status="error",
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(e),
        )


def _run_stigmergy(project_root: Path | str) -> PhaseResult:
    """Phase 2 : Stigmergy evaporation + stats."""
    project_root = Path(project_root)
    start = time.monotonic()
    mod = _load_tool("stigmergy")
    if mod is None:
        return PhaseResult(name="stigmergy", status="skip",
                           summary="stigmergy.py introuvable")

    try:
        board = mod.load_board(project_root)
        _board, evaporated = mod.evaporate(board)
        if evaporated > 0:
            mod.save_board(project_root, board)

        active = [p for p in board.pheromones if not p.resolved]
        by_type: dict[str, int] = {}
        for p in active:
            by_type[p.pheromone_type] = by_type.get(p.pheromone_type, 0) + 1

        return PhaseResult(
            name="stigmergy", status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            summary=f"{len(active)} actives, {evaporated} évaporées",
            data={
                "active": len(active),
                "evaporated": evaporated,
                "total_emitted": board.total_emitted,
                "by_type": by_type,
            },
        )
    except Exception as e:
        return PhaseResult(
            name="stigmergy", status="error",
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(e),
        )


def _run_antifragile(project_root: Path | str,
                     since: str | None = None) -> PhaseResult:
    """Phase 3 : Antifragile Score."""
    project_root = Path(project_root)
    start = time.monotonic()
    mod = _load_tool("antifragile-score")
    if mod is None:
        return PhaseResult(name="antifragile", status="skip",
                           summary="antifragile-score.py introuvable")

    try:
        result = mod.compute_antifragile_score(project_root, since=since)
        return PhaseResult(
            name="antifragile", status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            summary=f"Score: {result.global_score:.0f}/100 — {result.level}",
            data={
                "score": round(result.global_score, 1),
                "level": result.level,
                "dimensions": {
                    d.name: round(d.score * 100, 1) for d in result.dimensions
                },
            },
        )
    except Exception as e:
        return PhaseResult(
            name="antifragile", status="error",
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(e),
        )


def _run_darwinism(project_root: Path | str,
                   since: str | None = None) -> PhaseResult:
    """Phase 4 : Agent Darwinism."""
    project_root = Path(project_root)
    start = time.monotonic()
    mod = _load_tool("agent-darwinism")
    if mod is None:
        return PhaseResult(name="darwinism", status="skip",
                           summary="agent-darwinism.py introuvable")

    try:
        # Darwinism needs a trace file path
        trace_path = project_root / "_bmad-output" / "BMAD_TRACE.md"
        scores = mod.cmd_evaluate(project_root, trace_path,
                                  since=since, save=False)
        if not scores:
            return PhaseResult(
                name="darwinism", status="ok",
                duration_ms=int((time.monotonic() - start) * 1000),
                summary="Aucun agent évalué",
                data={"agents": {}},
            )

        agents_data = {}
        for sc in scores:
            agents_data[sc.agent_id] = {
                "fitness": round(sc.composite, 1),
                "level": sc.level,
            }

        top = max(scores, key=lambda s: s.composite)
        return PhaseResult(
            name="darwinism", status="ok",
            duration_ms=int((time.monotonic() - start) * 1000),
            summary=f"{len(scores)} agents — top: {top.agent_id} ({top.composite:.0f})",
            data={"agents": agents_data},
        )
    except Exception as e:
        return PhaseResult(
            name="darwinism", status="error",
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(e),
        )


def _run_memory_lint(project_root: Path | str,
                     emit: bool = False) -> PhaseResult:
    """Phase 5 : Memory Lint."""
    project_root = Path(project_root)
    start = time.monotonic()
    mod = _load_tool("memory-lint")
    if mod is None:
        return PhaseResult(name="memory-lint", status="skip",
                           summary="memory-lint.py introuvable")

    try:
        report = mod.lint_memory(project_root)

        emitted = 0
        if emit and report.error_count > 0:
            emitted = mod.emit_to_stigmergy(report, project_root)

        status = "ok" if report.error_count == 0 else "ok"
        return PhaseResult(
            name="memory-lint", status=status,
            duration_ms=int((time.monotonic() - start) * 1000),
            summary=(
                f"{report.error_count}E {report.warning_count}W "
                f"{report.info_count}I ({report.entries_scanned} entrées)"
            ),
            data={
                "errors": report.error_count,
                "warnings": report.warning_count,
                "info": report.info_count,
                "files_scanned": report.files_scanned,
                "entries_scanned": report.entries_scanned,
                "emitted": emitted,
            },
        )
    except Exception as e:
        return PhaseResult(
            name="memory-lint", status="error",
            duration_ms=int((time.monotonic() - start) * 1000),
            error=str(e),
        )


# ── Orchestration ─────────────────────────────────────────────────────────────

def run_nso(project_root: Path | str, since: str | None = None,
            quick: bool = False, emit: bool = False) -> NSOReport:
    """Exécute toutes les phases du Nervous System Orchestrator.

    Args:
        project_root: racine du projet BMAD (str ou Path)
        since: filtre temporel (YYYY-MM-DD ou 'auto')
        quick: mode rapide pour le dream
        emit: émettre les résultats vers stigmergy

    Returns:
        NSOReport avec les résultats de chaque phase
    """
    project_root = Path(project_root)
    total_start = time.monotonic()
    report = NSOReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Phase 1: Dream
    report.phases.append(_run_dream(project_root, since, quick, emit))

    # Phase 2: Stigmergy
    report.phases.append(_run_stigmergy(project_root))

    # Phase 3: Antifragile
    report.phases.append(_run_antifragile(project_root, since))

    # Phase 4: Darwinism
    report.phases.append(_run_darwinism(project_root, since))

    # Phase 5: Memory Lint
    report.phases.append(_run_memory_lint(project_root, emit))

    report.total_duration_ms = int((time.monotonic() - total_start) * 1000)
    return report


# ── Rendu ─────────────────────────────────────────────────────────────────────

STATUS_ICONS = {"ok": "✅", "skip": "⏭️", "error": "❌"}


def render_report(report: NSOReport) -> str:
    """Rend le rapport NSO en texte formaté."""
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════╗",
        "║        🧠 Nervous System Orchestrator — Report             ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
        f"  Timestamp : {report.timestamp}",
        f"  Durée totale : {report.total_duration_ms}ms",
        f"  Phases : {report.ok_count} OK, {report.error_count} erreurs",
        "",
        "┌──────────────┬────────┬──────────┬─────────────────────────────────┐",
        "│ Phase        │ Status │ Durée    │ Résumé                          │",
        "├──────────────┼────────┼──────────┼─────────────────────────────────┤",
    ]

    for phase in report.phases:
        icon = STATUS_ICONS.get(phase.status, "❓")
        name = phase.name[:12].ljust(12)
        dur = f"{phase.duration_ms}ms".rjust(8)
        summary = (phase.summary or phase.error)[:33].ljust(33)
        lines.append(f"│ {name} │ {icon}   │ {dur} │ {summary} │")

    lines.append(
        "└──────────────┴────────┴──────────┴─────────────────────────────────┘"
    )
    lines.append("")

    # Détails des erreurs
    errors = [p for p in report.phases if p.status == "error"]
    if errors:
        lines.append("⚠️  Erreurs détaillées :")
        for p in errors:
            lines.append(f"  ❌ {p.name}: {p.error}")
        lines.append("")

    return "\n".join(lines)


def report_to_dict(report: NSOReport) -> dict:
    """Convertit le rapport NSO en dict JSON."""
    return {
        "version": report.version,
        "timestamp": report.timestamp,
        "total_duration_ms": report.total_duration_ms,
        "summary": {
            "ok": report.ok_count,
            "errors": report.error_count,
            "phases": len(report.phases),
        },
        "phases": {
            p.name: {
                "status": p.status,
                "duration_ms": p.duration_ms,
                "summary": p.summary,
                "data": p.data,
                "error": p.error,
            }
            for p in report.phases
        },
    }


# ── Retrospective ────────────────────────────────────────────────────────────

@dataclass
class RetroItem:
    """Un élément de rétrospective."""
    category: str          # went_well | problem | action
    text: str
    source: str            # phase or file that produced this
    priority: int = 0      # 0=normal, 1=high


@dataclass
class RetroReport:
    """Rapport de rétrospective."""
    timestamp: str = ""
    went_well: list[RetroItem] = field(default_factory=list)
    problems: list[RetroItem] = field(default_factory=list)
    actions: list[RetroItem] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        return len(self.went_well) + len(self.problems) + len(self.actions)


def _collect_retro_learnings(project_root: Path,
                             since: str | None = None) -> list[RetroItem]:
    """Collecte les learnings récents → went_well."""
    items: list[RetroItem] = []
    memory_dir = project_root / "_bmad" / "_memory"

    # Agent learnings
    learnings_dir = memory_dir / "agent-learnings"
    if learnings_dir.is_dir():
        for f in sorted(learnings_dir.glob("*.md")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Filter by date if since is set
                if since and line[:10] < since:
                    continue
                if len(line) > 20:
                    items.append(RetroItem(
                        category="went_well",
                        text=line[:200],
                        source=f.name,
                    ))
    return items


def _collect_retro_failures(project_root: Path,
                            since: str | None = None) -> list[RetroItem]:
    """Collecte les failures récents → problems."""
    items: list[RetroItem] = []
    museum = project_root / "_bmad" / "_memory" / "failure-museum.md"
    if not museum.is_file():
        return items

    try:
        text = museum.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return items

    current_entry = ""
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("### "):
            if current_entry and len(current_entry) > 15:
                items.append(RetroItem(
                    category="problem",
                    text=current_entry[:200],
                    source="failure-museum.md",
                    priority=1,
                ))
            current_entry = line.lstrip("#").strip()
        elif line.strip():
            if len(current_entry) < 200:
                current_entry += " " + line.strip()

    if current_entry and len(current_entry) > 15:
        items.append(RetroItem(
            category="problem",
            text=current_entry[:200],
            source="failure-museum.md",
            priority=1,
        ))
    return items


def _collect_retro_pheromones(project_root: Path) -> list[RetroItem]:
    """Collecte les phéromones NEED non résolues → actions."""
    items: list[RetroItem] = []
    mod = _load_tool("stigmergy")
    if mod is None:
        return items

    try:
        board = mod.load_board(project_root)
        for p in board.pheromones:
            if not p.resolved and p.pheromone_type in ("NEED", "ALERT"):
                items.append(RetroItem(
                    category="action",
                    text=f"[{p.pheromone_type}] {p.location}: {p.text}"[:200],
                    source="stigmergy",
                    priority=1 if p.pheromone_type == "ALERT" else 0,
                ))
    except Exception:
        pass
    return items


def _collect_retro_lint(project_root: Path) -> list[RetroItem]:
    """Collecte les issues memory-lint → problems ou actions."""
    items: list[RetroItem] = []
    mod = _load_tool("memory-lint")
    if mod is None:
        return items

    try:
        report = mod.lint_memory(project_root)
        for issue in report.issues:
            if issue.severity == "error":
                items.append(RetroItem(
                    category="problem",
                    text=f"[LINT] {issue.title}: {issue.description}"[:200],
                    source="memory-lint",
                    priority=1,
                ))
            elif issue.severity == "warning":
                items.append(RetroItem(
                    category="action",
                    text=f"[LINT] {issue.title}"[:200],
                    source="memory-lint",
                ))
    except Exception:
        pass
    return items


def _collect_retro_antifragile(project_root: Path) -> list[RetroItem]:
    """Collecte les stresseurs antifragile → went_well ou problems."""
    items: list[RetroItem] = []
    mod = _load_tool("antifragile-score")
    if mod is None:
        return items

    try:
        result = mod.compute_antifragile_score(project_root)
        for d in result.dimensions:
            score_pct = d.score * 100
            if score_pct >= 70:
                items.append(RetroItem(
                    category="went_well",
                    text=f"Anti-fragile {d.name}: {score_pct:.0f}%",
                    source="antifragile",
                ))
            elif score_pct < 40:
                items.append(RetroItem(
                    category="problem",
                    text=f"Anti-fragile {d.name}: {score_pct:.0f}% (faible)",
                    source="antifragile",
                    priority=1,
                ))
    except Exception:
        pass
    return items


def run_retro(project_root: Path | str,
              since: str | None = None) -> RetroReport:
    """Exécute la rétrospective : croise learnings, failures, phéromones,
    lint et antifragile pour produire un rapport went_well/problems/actions."""
    project_root = Path(project_root)
    report = RetroReport(
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Collecter depuis toutes les sources
    all_items: list[RetroItem] = []
    all_items.extend(_collect_retro_learnings(project_root, since))
    all_items.extend(_collect_retro_failures(project_root, since))
    all_items.extend(_collect_retro_pheromones(project_root))
    all_items.extend(_collect_retro_lint(project_root))
    all_items.extend(_collect_retro_antifragile(project_root))

    # Trier et limiter
    for item in all_items:
        if item.category == "went_well":
            report.went_well.append(item)
        elif item.category == "problem":
            report.problems.append(item)
        elif item.category == "action":
            report.actions.append(item)

    # Trier par priorité (high first), puis limiter à 10 par catégorie
    report.went_well.sort(key=lambda x: -x.priority)
    report.problems.sort(key=lambda x: -x.priority)
    report.actions.sort(key=lambda x: -x.priority)
    report.went_well = report.went_well[:10]
    report.problems = report.problems[:10]
    report.actions = report.actions[:10]

    return report


def render_retro(report: RetroReport) -> str:
    """Rend le rapport de rétrospective en texte formaté."""
    lines = [
        "",
        "╔══════════════════════════════════════════════════════════════╗",
        "║        🔄 Rétrospective — Nervous System                   ║",
        "╚══════════════════════════════════════════════════════════════╝",
        "",
        f"  Timestamp : {report.timestamp}",
        f"  Éléments  : {report.total_items}",
        "",
    ]

    # Went well
    lines.append("  ✅ CE QUI A BIEN MARCHÉ")
    lines.append("  " + "─" * 55)
    if report.went_well:
        for item in report.went_well:
            lines.append(f"    • [{item.source}] {item.text}")
    else:
        lines.append("    (aucun élément)")
    lines.append("")

    # Problems
    lines.append("  ❌ PROBLÈMES DÉTECTÉS")
    lines.append("  " + "─" * 55)
    if report.problems:
        for item in report.problems:
            flag = "🔴" if item.priority > 0 else "🟡"
            lines.append(f"    {flag} [{item.source}] {item.text}")
    else:
        lines.append("    (aucun problème)")
    lines.append("")

    # Actions
    lines.append("  🎯 ACTIONS RECOMMANDÉES")
    lines.append("  " + "─" * 55)
    if report.actions:
        for i, item in enumerate(report.actions, 1):
            lines.append(f"    {i}. [{item.source}] {item.text}")
    else:
        lines.append("    (aucune action)")
    lines.append("")

    return "\n".join(lines)


def retro_to_dict(report: RetroReport) -> dict:
    """Convertit le rapport de rétrospective en dict JSON."""
    def _item_dict(item: RetroItem) -> dict:
        return {
            "text": item.text,
            "source": item.source,
            "priority": item.priority,
        }

    return {
        "timestamp": report.timestamp,
        "total_items": report.total_items,
        "went_well": [_item_dict(i) for i in report.went_well],
        "problems": [_item_dict(i) for i in report.problems],
        "actions": [_item_dict(i) for i in report.actions],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BMAD Nervous System Orchestrator — orchestre tout le système nerveux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".",
                        help="Racine du projet BMAD")

    sub = parser.add_subparsers(dest="command", help="Commande")

    # run
    run_p = sub.add_parser("run", help="Exécuter le cycle complet")
    run_p.add_argument("--since", default=None,
                       help="Date début (YYYY-MM-DD ou 'auto')")
    run_p.add_argument("--quick", action="store_true",
                       help="Mode rapide (dream quick)")
    run_p.add_argument("--json", action="store_true",
                       help="Sortie JSON unifiée")
    run_p.add_argument("--emit", action="store_true",
                       help="Émettre les résultats vers stigmergy")

    # retro
    retro_p = sub.add_parser("retro", help="Rétrospective croisée")
    retro_p.add_argument("--since", default=None,
                         help="Date début (YYYY-MM-DD)")
    retro_p.add_argument("--json", action="store_true",
                         help="Sortie JSON")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "run":
        report = run_nso(project_root, args.since, args.quick, args.emit)

        if args.json:
            print(json.dumps(report_to_dict(report), indent=2,
                             ensure_ascii=False))
        else:
            print(render_report(report))

        sys.exit(1 if report.error_count > 0 else 0)

    if args.command == "retro":
        retro = run_retro(project_root, args.since)

        if args.json:
            print(json.dumps(retro_to_dict(retro), indent=2,
                             ensure_ascii=False))
        else:
            print(render_retro(retro))

        sys.exit(0)


if __name__ == "__main__":
    main()
