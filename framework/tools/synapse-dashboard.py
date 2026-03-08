#!/usr/bin/env python3
"""
synapse-dashboard.py — Dashboard unifié Synapse Intelligence Layer (Story 8.4).
===========================================================================

Agrège les données de tous les outils Synapse en un rapport Markdown unifié.
Chaque section est collectée indépendamment — un outil en erreur n'empêche pas
le reste du dashboard.

Sections :
  1. Header & version     — Versions de tous les outils
  2. LLM Router           — Stats de routage, distribution par modèle
  3. Semantic Cache        — Statistiques de cache (hit/miss ratio)
  4. Token Budget          — Budgets par agent/modèle
  5. Orchestrator          — Historique et stats d'exécution
  6. Message Bus           — État du bus, messages pending
  7. Traces                — Dernières traces Synapse
  8. Workers               — État des agents workers
  9. Tool Registry         — Outils enregistrés
  10. MCP Server           — Outils MCP exposés (legacy + discovered)

Usage :
  python3 synapse-dashboard.py --project-root .
  python3 synapse-dashboard.py --project-root . --format json
  python3 synapse-dashboard.py --project-root . --output _grimoire-output/bench-reports/synapse-dashboard.md
  python3 synapse-dashboard.py --project-root . --section router,cache,budget

Stdlib only — importe les outils Synapse par importlib.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

DASHBOARD_VERSION = "1.1.0"

# ── Constants ────────────────────────────────────────────────────────────────

ALL_SECTIONS = (
    "router", "cache", "budget", "orchestrator", "bus",
    "traces", "workers", "registry", "mcp",
)

# ── Tool Import ──────────────────────────────────────────────────────────────

TOOLS_DIR = Path(__file__).resolve().parent


def _import_tool(filename: str, module_name: str):
    """Import dynamique d'un outil Synapse."""
    tool_path = TOOLS_DIR / filename
    if not tool_path.exists():
        return None
    full_name = "_dash_" + module_name
    if full_name in sys.modules:
        return sys.modules[full_name]
    try:
        spec = importlib.util.spec_from_file_location(full_name, tool_path)
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[full_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        sys.modules.pop(full_name, None)
        return None


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class SectionResult:
    """Résultat d'une section du dashboard."""

    name: str
    status: str = "ok"  # ok, error, unavailable
    markdown: str = ""
    data: dict = field(default_factory=dict)
    error: str = ""
    collection_time_ms: float = 0.0


@dataclass
class DashboardReport:
    """Rapport complet du dashboard."""

    generated_at: str = ""
    project_root: str = ""
    dashboard_version: str = DASHBOARD_VERSION
    total_tools: int = 0
    sections: list[SectionResult] = field(default_factory=list)
    generation_time_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "project_root": self.project_root,
            "dashboard_version": self.dashboard_version,
            "total_tools": self.total_tools,
            "sections": [asdict(s) for s in self.sections],
            "generation_time_ms": self.generation_time_ms,
        }


# ── Section Collectors ───────────────────────────────────────────────────────


def _collect_router(project_root: Path) -> SectionResult:
    """Section LLM Router — stats et distribution."""
    start = time.monotonic()
    mod = _import_tool("llm-router.py", "llm_router_dash")
    if not mod:
        return SectionResult(name="router", status="unavailable", error="llm-router.py introuvable")

    try:
        router_cls = getattr(mod, "LLMRouter", None)
        if not router_cls:
            return SectionResult(name="router", status="error", error="LLMRouter class not found")

        router = router_cls(project_root)
        stats = router.get_stats()
        stats_dicts = [asdict(s) for s in stats] if stats else []

        lines = ["## 🔀 LLM Router\n"]
        if not stats_dicts:
            lines.append("_Aucune statistique disponible — pas encore de requêtes routées._\n")
        else:
            lines.append("| Modèle | Requêtes | Tokens | Coût estimé |")
            lines.append("|--------|----------|--------|-------------|")
            for s in stats_dicts:
                model = s.get("model", "?")
                reqs = s.get("request_count", 0)
                tokens = s.get("total_tokens", 0)
                cost = s.get("estimated_cost", 0.0)
                lines.append(f"| {model} | {reqs} | {tokens:,} | ${cost:.4f} |")
            lines.append("")

        recs = router.get_recommendations() if hasattr(router, "get_recommendations") else []
        if recs:
            lines.append("**Recommandations :**")
            for r in recs[:3]:
                lines.append(f"- {r}")
            lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="router", markdown="\n".join(lines),
            data={"stats": stats_dicts}, collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="router", status="error", error=str(e))


def _collect_cache(project_root: Path) -> SectionResult:
    """Section Semantic Cache."""
    start = time.monotonic()
    mod = _import_tool("semantic-cache.py", "semantic_cache_dash")
    if not mod:
        return SectionResult(name="cache", status="unavailable", error="semantic-cache.py introuvable")

    try:
        cache_cls = getattr(mod, "SemanticCache", None)
        if not cache_cls:
            return SectionResult(name="cache", status="error", error="SemanticCache class not found")

        cache = cache_cls(project_root)
        stats = cache.get_stats() if hasattr(cache, "get_stats") else None

        lines = ["## 💾 Semantic Cache\n"]
        if stats:
            s = asdict(stats) if hasattr(stats, "__dataclass_fields__") else stats
            hits = s.get("hits", 0)
            misses = s.get("misses", 0)
            total = hits + misses
            ratio = round(hits / total * 100, 1) if total > 0 else 0
            lines.append(f"- **Hit ratio** : {ratio}% ({hits}/{total})")
            lines.append(f"- **Entries** : {s.get('entries', '?')}")
            lines.append(f"- **Evictions** : {s.get('evictions', 0)}")
        else:
            lines.append("_Statistiques non disponibles._")
        lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="cache", markdown="\n".join(lines),
            data={"stats": stats if isinstance(stats, dict) else {}},
            collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="cache", status="error", error=str(e))


def _collect_budget(project_root: Path) -> SectionResult:
    """Section Token Budget — enhanced with trend and visual bar."""
    start = time.monotonic()
    mod = _import_tool("token-budget.py", "token_budget_dash")
    if not mod:
        return SectionResult(name="budget", status="unavailable", error="token-budget.py introuvable")

    try:
        mcp_fn = getattr(mod, "mcp_context_budget", None)
        if not mcp_fn:
            return SectionResult(name="budget", status="error", error="mcp_context_budget not found")

        result = mcp_fn(str(project_root))
        lines = ["## 📊 Token Budget\n"]

        if isinstance(result, dict):
            model = result.get("model", "?")
            used = result.get("used_tokens", 0)
            window = result.get("window_tokens", 0)
            pct = result.get("usage_pct", 0)
            level = result.get("level", "?")
            level_icons = {"ok": "✅", "warning": "⚠️", "critical": "🔶", "emergency": "🔴"}
            icon = level_icons.get(level, "❓")

            lines.append(f"- **Modèle** : {model}")
            lines.append(f"- **Utilisation** : {used:,} / {window:,} tokens ({pct:.1%}) {icon}")
            lines.append(f"- **Niveau** : {level.upper()}")

            # Visual bar
            bar_w = 30
            filled = int(min(1.0, pct) * bar_w)
            bar = "█" * filled + "░" * (bar_w - filled)
            lines.append(f"\n`[{bar}]` {pct:.1%}\n")

            # Buckets breakdown
            buckets = result.get("buckets", [])
            if buckets:
                lines.append("| Priorité | Tokens | % Fenêtre | Fichiers |")
                lines.append("|----------|--------|-----------|----------|")
                for b in buckets:
                    if isinstance(b, dict) and b.get("tokens", 0) > 0:
                        lines.append(
                            f"| {b.get('name', '?')} | {b.get('tokens', 0):,} "
                            f"| {b.get('percentage', 0):.1%} | {b.get('files_count', 0)} |"
                        )
                lines.append("")

            # Trend data
            trend = result.get("trend", {})
            if isinstance(trend, dict) and trend.get("entries", 0) > 0:
                direction = trend.get("direction", "→")
                avg = trend.get("avg_pct", 0)
                max_p = trend.get("max_pct", 0)
                min_p = trend.get("min_pct", 0)
                lines.append(f"**Tendance** {direction} : moy {avg:.1%} | "
                             f"min {min_p:.1%} | max {max_p:.1%} "
                             f"({trend.get('entries', 0)} mesures)")
                lines.append("")

            # Recommendations
            recs = result.get("recommendations", [])
            if recs:
                lines.append("**Recommandations :**")
                for r in recs:
                    lines.append(f"- {r}")
                lines.append("")
        else:
            lines.append(f"_{result}_")
            lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="budget", markdown="\n".join(lines),
            data=result if isinstance(result, dict) else {},
            collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="budget", status="error", error=str(e))


def _collect_orchestrator(project_root: Path) -> SectionResult:
    """Section Orchestrator — historique et stats."""
    start = time.monotonic()
    mod = _import_tool("orchestrator.py", "orchestrator_dash")
    if not mod:
        return SectionResult(name="orchestrator", status="unavailable", error="orchestrator.py introuvable")

    try:
        orch_cls = getattr(mod, "Orchestrator", None)
        if not orch_cls:
            return SectionResult(name="orchestrator", status="error", error="Orchestrator class not found")

        orch = orch_cls(project_root)
        stats = orch.get_stats()
        history = orch.get_history(last_n=5)

        lines = ["## 🎯 Orchestrator\n"]

        s = asdict(stats)
        lines.append(f"- **Exécutions totales** : {s.get('total_executions', 0)}")
        lines.append(f"- **Tokens totaux** : {s.get('total_tokens', 0):,}")

        by_mode = s.get("by_mode", {})
        if by_mode:
            mode_str = ", ".join(f"{m}: {c}" for m, c in by_mode.items())
            lines.append(f"- **Par mode** : {mode_str}")
        lines.append("")

        if history:
            lines.append("### Dernières exécutions\n")
            lines.append("| Workflow | Mode | Status | Durée |")
            lines.append("|----------|------|--------|-------|")
            for h in history:
                d = h.to_dict() if hasattr(h, "to_dict") else asdict(h)
                wf = d.get("workflow", "?")
                mode = d.get("mode", "?")
                status = d.get("status", "?")
                dur = d.get("total_duration_seconds", 0)
                lines.append(f"| {wf} | {mode} | {status} | {dur}s |")
            lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="orchestrator", markdown="\n".join(lines),
            data=s, collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="orchestrator", status="error", error=str(e))


def _collect_bus(project_root: Path) -> SectionResult:
    """Section Message Bus."""
    start = time.monotonic()
    mod = _import_tool("message-bus.py", "message_bus_dash")
    if not mod:
        return SectionResult(name="bus", status="unavailable", error="message-bus.py introuvable")

    try:
        mcp_fn = getattr(mod, "mcp_message_bus_status", None)
        if not mcp_fn:
            return SectionResult(name="bus", status="error", error="mcp_message_bus_status not found")

        result = mcp_fn()
        lines = ["## 📡 Message Bus\n"]
        if isinstance(result, dict):
            backend = result.get("backend", "in-process")
            pending = result.get("pending", 0)
            delivered = result.get("delivered", 0)
            lines.append(f"- **Backend** : {backend}")
            lines.append(f"- **Pending** : {pending}")
            lines.append(f"- **Delivered** : {delivered}")
            channels = result.get("channels", [])
            if channels:
                lines.append(f"- **Channels** : {', '.join(channels[:10])}")
        else:
            lines.append(f"_{result}_")
        lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="bus", markdown="\n".join(lines),
            data=result if isinstance(result, dict) else {},
            collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="bus", status="error", error=str(e))


def _collect_traces(project_root: Path) -> SectionResult:
    """Section Synapse Traces."""
    start = time.monotonic()
    mod = _import_tool("synapse-trace.py", "synapse_trace_dash")
    if not mod:
        return SectionResult(name="traces", status="unavailable", error="synapse-trace.py introuvable")

    try:
        mcp_fn = getattr(mod, "mcp_synapse_trace", None)
        if not mcp_fn:
            return SectionResult(name="traces", status="error", error="mcp_synapse_trace not found")

        result = mcp_fn(str(project_root), action="status")
        lines = ["## 🔍 Synapse Traces\n"]
        if isinstance(result, dict):
            total = result.get("total_traces", result.get("count", 0))
            lines.append(f"- **Total traces** : {total}")
            by_tool = result.get("by_tool", {})
            if by_tool:
                lines.append("- **Par outil** :")
                for tool, count in list(by_tool.items())[:8]:
                    lines.append(f"  - {tool}: {count}")
            recent = result.get("recent", [])
            if recent:
                lines.append(f"- **Dernière trace** : {recent[0].get('timestamp', '?')} — {recent[0].get('tool', '?')}")
        else:
            lines.append(f"_{result}_")
        lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="traces", markdown="\n".join(lines),
            data=result if isinstance(result, dict) else {},
            collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="traces", status="error", error=str(e))


def _collect_workers(project_root: Path) -> SectionResult:
    """Section Agent Workers."""
    start = time.monotonic()
    mod = _import_tool("agent-worker.py", "agent_worker_dash")
    if not mod:
        return SectionResult(name="workers", status="unavailable", error="agent-worker.py introuvable")

    try:
        mcp_fn = getattr(mod, "mcp_agent_worker", None)
        if not mcp_fn:
            return SectionResult(name="workers", status="error", error="mcp_agent_worker not found")

        result = mcp_fn(str(project_root), action="list")
        lines = ["## 👷 Agent Workers\n"]
        if isinstance(result, dict):
            workers = result.get("workers", result.get("agents", []))
            if isinstance(workers, list):
                lines.append(f"- **Workers disponibles** : {len(workers)}")
                for w in workers[:10]:
                    if isinstance(w, dict):
                        name = w.get("id", w.get("name", "?"))
                        status = w.get("status", "idle")
                        lines.append(f"  - {name}: {status}")
                    else:
                        lines.append(f"  - {w}")
            else:
                lines.append(f"- Workers: {workers}")
        else:
            lines.append(f"_{result}_")
        lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="workers", markdown="\n".join(lines),
            data=result if isinstance(result, dict) else {},
            collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="workers", status="error", error=str(e))


def _collect_registry(project_root: Path) -> SectionResult:
    """Section Tool Registry."""
    start = time.monotonic()
    mod = _import_tool("tool-registry.py", "tool_registry_dash")
    if not mod:
        return SectionResult(name="registry", status="unavailable", error="tool-registry.py introuvable")

    try:
        registry_cls = getattr(mod, "ToolRegistry", None)
        if not registry_cls:
            return SectionResult(name="registry", status="error", error="ToolRegistry class not found")

        registry = registry_cls(project_root)
        entries = registry.list_tools() if hasattr(registry, "list_tools") else []

        lines = ["## 🧰 Tool Registry\n"]
        if entries:
            lines.append(f"- **Outils enregistrés** : {len(entries)}")
            lines.append("")
            lines.append("| Outil | Version | Catégorie |")
            lines.append("|-------|---------|-----------|")
            for e in entries[:15]:
                if isinstance(e, dict):
                    name = e.get("name", "?")
                    ver = e.get("version", "?")
                    cat = e.get("category", "—")
                    lines.append(f"| {name} | {ver} | {cat} |")
                elif hasattr(e, "name"):
                    lines.append(f"| {e.name} | {getattr(e, 'version', '?')} | {getattr(e, 'category', '—')} |")
        else:
            lines.append("_Aucun outil enregistré._")
        lines.append("")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="registry", markdown="\n".join(lines),
            data={"count": len(entries)},
            collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="registry", status="error", error=str(e))


def _collect_mcp(project_root: Path) -> SectionResult:
    """Section MCP Server — outils exposés."""
    start = time.monotonic()
    mod = _import_tool("grimoire-mcp-tools.py", "bmad_mcp_dash")
    if not mod:
        return SectionResult(name="mcp", status="unavailable", error="grimoire-mcp-tools.py introuvable")

    try:
        discover_fn = getattr(mod, "discover_synapse_tools", None)
        get_all_fn = getattr(mod, "get_all_tool_names", None)

        lines = ["## 🔌 MCP Server\n"]

        if get_all_fn:
            all_tools = get_all_fn()
            lines.append(f"- **Total tools MCP** : {len(all_tools)}")
        elif discover_fn:
            discovered = discover_fn()
            all_tools = list(discovered.keys())
            lines.append(f"- **Discovered tools** : {len(discovered)}")
        else:
            all_tools = []
            lines.append("_Auto-discovery non disponible._")

        version = getattr(mod, "Grimoire_MCP_TOOLS_VERSION", "?")
        lines.append(f"- **Version** : {version}")

        if all_tools:
            lines.append(f"\n<details><summary>Liste des {len(all_tools)} outils</summary>\n")
            for t in all_tools:
                lines.append(f"- `{t}`")
            lines.append("</details>\n")

        elapsed = round((time.monotonic() - start) * 1000, 1)
        return SectionResult(
            name="mcp", markdown="\n".join(lines),
            data={"tools": all_tools, "version": version},
            collection_time_ms=elapsed,
        )
    except Exception as e:
        return SectionResult(name="mcp", status="error", error=str(e))


# ── Section Dispatcher ───────────────────────────────────────────────────────

SECTION_COLLECTORS = {
    "router": _collect_router,
    "cache": _collect_cache,
    "budget": _collect_budget,
    "orchestrator": _collect_orchestrator,
    "bus": _collect_bus,
    "traces": _collect_traces,
    "workers": _collect_workers,
    "registry": _collect_registry,
    "mcp": _collect_mcp,
}


# ── Dashboard Builder ────────────────────────────────────────────────────────


def build_dashboard(
    project_root: Path,
    sections: tuple[str, ...] | None = None,
) -> DashboardReport:
    """
    Construit le rapport dashboard complet.

    Chaque section est collectée indépendamment.
    """
    start = time.monotonic()
    report = DashboardReport(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        project_root=str(project_root),
    )

    target_sections = sections or ALL_SECTIONS

    for section_name in target_sections:
        collector = SECTION_COLLECTORS.get(section_name)
        if not collector:
            report.sections.append(SectionResult(
                name=section_name, status="error",
                error=f"Section inconnue: {section_name}",
            ))
            continue

        try:
            result = collector(project_root)
            report.sections.append(result)
        except Exception as e:
            report.sections.append(SectionResult(
                name=section_name, status="error", error=str(e),
            ))

    report.total_tools = len(SECTION_COLLECTORS)
    report.generation_time_ms = round((time.monotonic() - start) * 1000, 1)

    return report


def render_markdown(report: DashboardReport) -> str:
    """Rendu Markdown du rapport."""
    lines = [
        "# 🧠 Synapse Intelligence Dashboard",
        "",
        f"_Généré le {report.generated_at} — v{report.dashboard_version}_  ",
        f"_Projet : `{report.project_root}`_  ",
        f"_Temps de génération : {report.generation_time_ms}ms_",
        "",
        "---",
        "",
    ]

    ok_count = sum(1 for s in report.sections if s.status == "ok")
    err_count = sum(1 for s in report.sections if s.status == "error")
    na_count = sum(1 for s in report.sections if s.status == "unavailable")

    lines.append(f"**Résumé** : {ok_count} ✅ | {err_count} ❌ | {na_count} ⚠️ indisponibles")
    lines.append("")

    for section in report.sections:
        if section.status == "ok" and section.markdown:
            lines.append(section.markdown)
        elif section.status == "unavailable":
            lines.append(f"## ⚠️ {section.name.title()}\n")
            lines.append(f"_Section indisponible : {section.error}_\n")
        elif section.status == "error":
            lines.append(f"## ❌ {section.name.title()}\n")
            lines.append(f"_Erreur : {section.error}_\n")

    lines.append("---")
    lines.append(f"_Dashboard généré par synapse-dashboard.py v{DASHBOARD_VERSION}_")
    return "\n".join(lines)


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_synapse_dashboard(
    project_root: str,
    sections: str = "",
    output_format: str = "markdown",
) -> dict:
    """
    Dashboard unifié Synapse Intelligence Layer.

    Args:
        project_root: Racine du projet Grimoire.
        sections: Sections à inclure (virgule-séparées). Vide = toutes.
        output_format: 'markdown' ou 'json'.

    Returns:
        dict avec le rapport complet.
    """
    root = Path(project_root)
    target = tuple(s.strip() for s in sections.split(",") if s.strip()) if sections else None

    report = build_dashboard(root, target)

    if output_format == "json":
        return report.to_dict()

    md = render_markdown(report)
    return {
        "format": "markdown",
        "content": md,
        "generated_at": report.generated_at,
        "sections_ok": sum(1 for s in report.sections if s.status == "ok"),
        "sections_total": len(report.sections),
        "generation_time_ms": report.generation_time_ms,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="Synapse Intelligence Dashboard",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".", help="Racine du projet Grimoire")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown", help="Format de sortie")
    parser.add_argument("--section", default="", help="Sections à inclure (virgule-séparées)")
    parser.add_argument("--output", default="", help="Fichier de sortie (optionnel)")
    parser.add_argument("--version", action="store_true", help="Affiche la version")

    args = parser.parse_args()

    if args.version:
        print(f"synapse-dashboard {DASHBOARD_VERSION}")
        sys.exit(0)

    project_root = Path(args.project_root).resolve()

    target_sections = tuple(s.strip() for s in args.section.split(",") if s.strip()) if args.section else None
    report = build_dashboard(project_root, target_sections)

    if args.format == "json":
        output = json.dumps(report.to_dict(), ensure_ascii=False, indent=2)
    else:
        output = render_markdown(report)

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"✅ Dashboard sauvegardé dans {out_path}")
    else:
        print(output)


if __name__ == "__main__":
    main()
