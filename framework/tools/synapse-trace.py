#!/usr/bin/env python3
"""
synapse-trace.py — Middleware de traçabilité Synapse Grimoire (BM-46 Story 7.2).
============================================================

Capture automatiquement chaque appel MCP et chaque opération inter-outils,
puis les écrit dans ``_grimoire-output/Grimoire_TRACE.md`` au format structuré
compatible avec les parseurs existants (cognitive-flywheel, dream,
workflow-adapt, memory-lint, dna-evolve).

Modes :
  status  — Résumé des traces enregistrées
  search  — Recherche dans les traces par outil/agent
  export  — Exporte en JSON
  clear   — Remet à zéro les traces Synapse

Usage :
  python3 synapse-trace.py --project-root . status
  python3 synapse-trace.py --project-root . search --tool orchestrator
  python3 synapse-trace.py --project-root . search --agent architect
  python3 synapse-trace.py --project-root . export --format json
  python3 synapse-trace.py --project-root . clear

Stdlib only.

Références :
  - Grimoire_TRACE.md format: framework/grimoire-trace.md
  - OpenTelemetry Python: https://opentelemetry.io/docs/languages/python/
"""

from __future__ import annotations

import argparse
import contextlib
import functools
import json
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

SYNAPSE_TRACE_VERSION = "1.1.0"

# ── Constants ────────────────────────────────────────────────────────────────

TRACE_DIR = "_grimoire-output"
TRACE_FILE = "Grimoire_TRACE.md"
SYNAPSE_TAG = "[SYNAPSE]"
MAX_TRACE_ENTRIES = 10000

# Estimation de coût par défaut (USD / 1k tokens). Surchargeable par tracer ou
# via la variable d'environnement Grimoire_COST_PER_1K. Ordre de grandeur d'un
# modèle de classe « sonnet » ; ce n'est qu'une estimation indicative.
DEFAULT_COST_PER_1K_TOKENS = 0.003


def estimate_cost(tokens: int, per_1k: float = DEFAULT_COST_PER_1K_TOKENS) -> float:
    """Estime un coût USD à partir d'un volume de tokens (estimation indicative)."""
    return round(max(0, int(tokens or 0)) / 1000.0 * per_1k, 6)


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class TraceEntry:
    """Une entrée de trace Synapse."""

    timestamp: str = ""
    tool: str = ""
    operation: str = ""
    agent: str = ""
    duration_ms: float = 0.0
    tokens_estimated: int = 0
    status: str = "ok"  # ok | error | timeout
    details: dict = field(default_factory=dict)
    # Causalité / coût (BM-46 v1.1) — capturés via tracer.span()
    span_id: str = ""          # identifiant unique de ce span
    parent_span_id: str = ""   # span déclencheur (arbre parent→enfant)
    trace_id: str = ""         # racine corrélant tout l'arbre d'opérations
    cost_usd: float = 0.0      # coût estimé (depuis tokens)
    retries: int = 0           # tentatives avant l'issue finale

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TraceEntry:
        valid = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in valid})

    def to_markdown(self) -> str:
        """Format Markdown compatible Grimoire_TRACE.md."""
        lines = [
            f"\n### [{self.timestamp}] {SYNAPSE_TAG} {self.tool}.{self.operation}",
            f"- **Agent** : {self.agent or 'system'}",
            f"- **Durée** : {self.duration_ms:.0f}ms",
            f"- **Tokens** : ~{self.tokens_estimated}",
            f"- **Statut** : {self.status}",
        ]
        if self.retries:
            lines.append(f"- **Retries** : {self.retries}")
        if self.cost_usd:
            lines.append(f"- **Coût** : ${self.cost_usd:.6f}")
        if self.span_id:
            lines.append(f"- **Span** : {self.span_id}")
        if self.parent_span_id:
            lines.append(f"- **Parent** : {self.parent_span_id}")
        if self.trace_id and self.trace_id != self.span_id:
            lines.append(f"- **Trace** : {self.trace_id}")
        if self.details:
            for k, v in self.details.items():
                lines.append(f"- **{k}** : {v}")
        return "\n".join(lines) + "\n"


@dataclass
class TraceStats:
    """Statistiques des traces."""

    total_entries: int = 0
    by_tool: dict[str, int] = field(default_factory=dict)
    by_agent: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)
    total_duration_ms: float = 0.0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_retries: int = 0
    oldest_entry: str = ""
    newest_entry: str = ""
    errors_count: int = 0


@dataclass
class TraceSearchResult:
    """Résultat de recherche dans les traces."""

    query: str = ""
    matches: list[TraceEntry] = field(default_factory=list)
    total_matches: int = 0


# ── Synapse Tracer ──────────────────────────────────────────────────────────


class SynapseTracer:
    """
    Traceur centralisé pour toutes les opérations Synapse.

    Écrit en append dans Grimoire_TRACE.md et maintient un index en mémoire
    pour les recherches rapides.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        enabled: bool = True,
        dry_run: bool = False,
        cost_per_1k: float = DEFAULT_COST_PER_1K_TOKENS,
    ):
        self._root = Path(project_root).resolve()
        self._enabled = enabled
        self._dry_run = dry_run
        self._entries: list[TraceEntry] = []
        self._trace_path = self._root / TRACE_DIR / TRACE_FILE
        self._loaded = False
        self.cost_per_1k = cost_per_1k
        # Pile de spans actifs — donne la causalité parent→enfant automatique.
        self._span_stack: list[_Span] = []

    def span(
        self,
        tool: str,
        operation: str,
        *,
        agent: str = "",
        retries: int = 0,
    ) -> _Span:
        """
        Ouvre un span tracé en context manager.

        Capture automatiquement, à la sortie du bloc ``with`` : la durée, l'issue
        (ok/error sur exception), le coût (depuis ``tokens_estimated``), le nombre
        de retries, et le lien causal parent→enfant (via la pile de spans).

        Usage::

            with tracer.span("orchestrator", "execute", agent="dev") as s:
                s.tokens_estimated = 1200
                with tracer.span("router", "classify") as child:
                    ...           # child.parent_span_id == s.span_id
        """
        return _Span(self, tool, operation, agent=agent, retries=retries)

    @property
    def trace_path(self) -> Path:
        return self._trace_path

    @property
    def entries(self) -> list[TraceEntry]:
        return list(self._entries)

    def record(self, entry: TraceEntry) -> None:
        """Enregistre une entrée de trace."""
        if not self._enabled:
            return
        self._entries.append(entry)
        if len(self._entries) > MAX_TRACE_ENTRIES:
            self._entries = self._entries[-MAX_TRACE_ENTRIES:]
        if not self._dry_run:
            self._append_to_file(entry)

    def _append_to_file(self, entry: TraceEntry) -> None:
        """Écrit l'entrée dans Grimoire_TRACE.md."""
        self._trace_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._trace_path, "a", encoding="utf-8") as f:
            f.write(entry.to_markdown())

    def load_from_file(self) -> int:
        """
        Charge les entrées Synapse existantes depuis Grimoire_TRACE.md.

        Ne charge que les entrées qui contiennent le tag [SYNAPSE].
        Retourne le nombre d'entrées chargées.
        """
        if not self._trace_path.exists():
            self._loaded = True
            return 0

        self._entries.clear()
        text = self._trace_path.read_text(encoding="utf-8")

        # Parse [SYNAPSE] blocks
        pattern = re.compile(
            r"###\s+\[([^\]]+)\]\s+\[SYNAPSE\]\s+(\S+)\.(\S+)\s*\n"
            r"(?:- \*\*(\w+)\*\* : (.+)\n)*",
            re.MULTILINE,
        )

        for match in pattern.finditer(text):
            ts = match.group(1)
            tool = match.group(2)
            operation = match.group(3)

            # Parse metadata lines — bornées au bloc courant (jusqu'au prochain
            # en-tête ###), sinon les champs conditionnels (retries, parent…)
            # d'un bloc suivant débordent sur le bloc courant.
            next_hdr = text.find("\n###", match.end())
            block_end = next_hdr if next_hdr != -1 else len(text)
            block_text = text[match.start():block_end]

            agent = ""
            duration = 0.0
            tokens = 0
            status = "ok"

            agent_m = re.search(r"\*\*Agent\*\*\s*:\s*(.+)", block_text)
            if agent_m:
                agent = agent_m.group(1).strip()
            dur_m = re.search(r"\*\*Durée\*\*\s*:\s*(\d+(?:\.\d+)?)", block_text)
            if dur_m:
                duration = float(dur_m.group(1))
            tok_m = re.search(r"\*\*Tokens\*\*\s*:\s*~?(\d+)", block_text)
            if tok_m:
                tokens = int(tok_m.group(1))
            stat_m = re.search(r"\*\*Statut\*\*\s*:\s*(\w+)", block_text)
            if stat_m:
                status = stat_m.group(1).strip()

            retries = 0
            cost = 0.0
            span_id = parent_span_id = trace_id = ""
            ret_m = re.search(r"\*\*Retries\*\*\s*:\s*(\d+)", block_text)
            if ret_m:
                retries = int(ret_m.group(1))
            cost_m = re.search(r"\*\*Coût\*\*\s*:\s*\$?([\d.]+)", block_text)
            if cost_m:
                cost = float(cost_m.group(1))
            span_m = re.search(r"\*\*Span\*\*\s*:\s*(\S+)", block_text)
            if span_m:
                span_id = span_m.group(1).strip()
            par_m = re.search(r"\*\*Parent\*\*\s*:\s*(\S+)", block_text)
            if par_m:
                parent_span_id = par_m.group(1).strip()
            tr_m = re.search(r"\*\*Trace\*\*\s*:\s*(\S+)", block_text)
            if tr_m:
                trace_id = tr_m.group(1).strip()

            entry = TraceEntry(
                timestamp=ts,
                tool=tool,
                operation=operation,
                agent=agent if agent != "system" else "",
                duration_ms=duration,
                tokens_estimated=tokens,
                status=status,
                span_id=span_id,
                parent_span_id=parent_span_id,
                trace_id=trace_id or span_id,
                cost_usd=cost,
                retries=retries,
            )
            self._entries.append(entry)

        self._loaded = True
        return len(self._entries)

    def get_stats(self) -> TraceStats:
        """Calcule les statistiques des traces."""
        if not self._loaded:
            self.load_from_file()

        stats = TraceStats()
        stats.total_entries = len(self._entries)

        for entry in self._entries:
            stats.by_tool[entry.tool] = stats.by_tool.get(entry.tool, 0) + 1
            if entry.agent:
                stats.by_agent[entry.agent] = stats.by_agent.get(entry.agent, 0) + 1
            stats.by_status[entry.status] = stats.by_status.get(entry.status, 0) + 1
            stats.total_duration_ms += entry.duration_ms
            stats.total_tokens += entry.tokens_estimated
            stats.total_cost_usd += entry.cost_usd
            stats.total_retries += entry.retries
            if entry.status == "error":
                stats.errors_count += 1

        if self._entries:
            stats.oldest_entry = self._entries[0].timestamp
            stats.newest_entry = self._entries[-1].timestamp

        return stats

    def search(
        self,
        tool: str = "",
        agent: str = "",
        status: str = "",
        limit: int = 50,
    ) -> TraceSearchResult:
        """Recherche dans les traces par critères."""
        if not self._loaded:
            self.load_from_file()

        query_parts = []
        if tool:
            query_parts.append(f"tool={tool}")
        if agent:
            query_parts.append(f"agent={agent}")
        if status:
            query_parts.append(f"status={status}")

        matches = []
        for entry in reversed(self._entries):
            if tool and tool.lower() not in entry.tool.lower():
                continue
            if agent and agent.lower() not in entry.agent.lower():
                continue
            if status and status.lower() != entry.status.lower():
                continue
            matches.append(entry)
            if len(matches) >= limit:
                break

        return TraceSearchResult(
            query=" AND ".join(query_parts) or "all",
            matches=matches,
            total_matches=len(matches),
        )

    def clear_synapse_entries(self) -> int:
        """
        Supprime les entrées [SYNAPSE] du fichier de trace.

        Les autres entrées (non-Synapse) sont préservées.
        Retourne le nombre d'entrées supprimées.
        """
        count = len(self._entries)
        self._entries.clear()

        if not self._trace_path.exists():
            return 0

        text = self._trace_path.read_text(encoding="utf-8")
        # Remove [SYNAPSE] blocks: from ### [timestamp] [SYNAPSE] to next ###
        cleaned = re.sub(
            r"\n?###\s+\[[^\]]+\]\s+\[SYNAPSE\]\s+[^\n]*\n(?:- [^\n]*\n)*",
            "",
            text,
        )

        self._trace_path.write_text(cleaned, encoding="utf-8")
        return count

    def export_json(self) -> list[dict]:
        """Exporte toutes les entrées en JSON."""
        if not self._loaded:
            self.load_from_file()
        return [e.to_dict() for e in self._entries]


# ── Global Tracer ────────────────────────────────────────────────────────────

_GLOBAL_TRACER: SynapseTracer | None = None


def get_global_tracer(project_root: str | Path = ".") -> SynapseTracer:
    """Retourne le traceur global (singleton par process)."""
    global _GLOBAL_TRACER
    if _GLOBAL_TRACER is None:
        _GLOBAL_TRACER = SynapseTracer(project_root)
    return _GLOBAL_TRACER


def set_global_tracer(tracer: SynapseTracer) -> None:
    """Remplace le traceur global (utile pour les tests)."""
    global _GLOBAL_TRACER
    _GLOBAL_TRACER = tracer


def reset_global_tracer() -> None:
    """Réinitialise le traceur global."""
    global _GLOBAL_TRACER
    _GLOBAL_TRACER = None


# ── Span (causalité parent→enfant) ───────────────────────────────────────────


class _Span:
    """
    Span tracé ouvert par ``SynapseTracer.span()``.

    À l'entrée, s'empile sur la pile de spans du traceur et hérite du parent
    courant (lien causal). À la sortie du bloc ``with``, finalise durée, issue,
    coût et enregistre une ``TraceEntry`` complète.

    Attributs réglables dans le bloc : ``tokens_estimated``, ``cost_usd``,
    ``retries``, ``status``, ``details``.
    """

    def __init__(self, tracer: SynapseTracer, tool: str, operation: str, *, agent: str = "", retries: int = 0):
        self._tracer = tracer
        self.tool = tool
        self.operation = operation
        self.agent = agent
        self.span_id = uuid.uuid4().hex[:12]
        self.parent_span_id = ""
        self.trace_id = self.span_id
        self.tokens_estimated = 0
        self.cost_usd = 0.0
        self.retries = retries
        self.status = "ok"
        self.details: dict = {}
        self._start = 0.0

    def __enter__(self) -> _Span:
        stack = self._tracer._span_stack
        if stack:
            self.parent_span_id = stack[-1].span_id
            self.trace_id = stack[-1].trace_id
        stack.append(self)
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = (time.monotonic() - self._start) * 1000
        if exc_type is not None and self.status == "ok":
            self.status = "error"
            self.details.setdefault("error", str(exc)[:200])
        if not self.cost_usd and self.tokens_estimated:
            self.cost_usd = estimate_cost(self.tokens_estimated, self._tracer.cost_per_1k)
        self._tracer.record(TraceEntry(
            tool=self.tool,
            operation=self.operation,
            agent=self.agent,
            duration_ms=round(elapsed_ms, 1),
            tokens_estimated=self.tokens_estimated,
            status=self.status,
            details=dict(self.details),
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            trace_id=self.trace_id,
            cost_usd=round(self.cost_usd, 6),
            retries=self.retries,
        ))
        stack = self._tracer._span_stack
        if stack and stack[-1] is self:
            stack.pop()
        return False  # ne jamais avaler l'exception


# ── Decorator ────────────────────────────────────────────────────────────────


def synapse_traced(tool_name: str, operation: str, agent: str = ""):
    """
    Decorator qui trace automatiquement les appels de fonctions Synapse.

    Usage :
        @synapse_traced("orchestrator", "execute")
        def mcp_orchestrate(project_root, ...):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_global_tracer()
            eff_agent = agent or kwargs.get("agent", kwargs.get("agent_id", ""))
            with tracer.span(tool_name, operation, agent=eff_agent) as span:
                result = func(*args, **kwargs)
                if isinstance(result, dict):
                    span.details["result_status"] = result.get("status", "")
                    with contextlib.suppress(TypeError, ValueError):
                        if result.get("tokens") is not None:
                            span.tokens_estimated = int(result["tokens"])
                    with contextlib.suppress(TypeError, ValueError):
                        if result.get("retries") is not None:
                            span.retries = int(result["retries"])
                return result
        return wrapper
    return decorator


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_synapse_trace(
    project_root: str,
    action: str = "status",
    tool: str = "",
    agent: str = "",
    query: str = "",
    output_format: str = "json",
) -> dict:
    """
    MCP tool ``grimoire_synapse_trace`` — gère la traçabilité Synapse.

    Actions: status, search, export, clear
    """
    root = Path(project_root).resolve()
    tracer = SynapseTracer(root)

    if action == "status":
        stats = tracer.get_stats()
        return {"status": "ok", "stats": asdict(stats)}

    if action == "search":
        results = tracer.search(tool=tool or query, agent=agent)
        return {
            "status": "ok",
            "query": results.query,
            "total_matches": results.total_matches,
            "matches": [m.to_dict() for m in results.matches[:20]],
        }

    if action == "export":
        entries = tracer.export_json()
        return {"status": "ok", "format": output_format, "entries": entries, "count": len(entries)}

    if action == "clear":
        count = tracer.clear_synapse_entries()
        return {"status": "ok", "cleared": count}

    return {"status": "error", "message": f"Action inconnue: {action}"}


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synapse-trace",
        description="Middleware de traçabilité Synapse Grimoire",
    )
    parser.add_argument("--project-root", type=Path, default=Path(),
                        help="Racine du projet Grimoire")
    parser.add_argument("--version", action="version", version=f"%(prog)s {SYNAPSE_TRACE_VERSION}")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="Résumé des traces")

    s_p = sub.add_parser("search", help="Recherche dans les traces")
    s_p.add_argument("--tool", default="", help="Filtrer par outil")
    s_p.add_argument("--agent", default="", help="Filtrer par agent")
    s_p.add_argument("--status", default="", help="Filtrer par statut")
    s_p.add_argument("--limit", type=int, default=20, help="Nombre max de résultats")
    s_p.add_argument("--json", action="store_true", help="Sortie JSON")

    e_p = sub.add_parser("export", help="Exporte les traces")
    e_p.add_argument("--format", choices=["json", "markdown"], default="json")

    sub.add_parser("clear", help="Supprime les traces Synapse")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    root = Path(args.project_root).resolve()
    tracer = SynapseTracer(root)

    if args.command == "status":
        stats = tracer.get_stats()
        print(f"Synapse Trace — {stats.total_entries} entrées")
        if stats.total_entries > 0:
            print(f"  Période : {stats.oldest_entry} → {stats.newest_entry}")
            print(f"  Durée totale : {stats.total_duration_ms:,.0f}ms")
            print(f"  Tokens totaux : ~{stats.total_tokens:,}")
            if stats.total_cost_usd:
                print(f"  Coût estimé : ${stats.total_cost_usd:,.4f}")
            if stats.total_retries:
                print(f"  Retries : {stats.total_retries}")
            print(f"  Erreurs : {stats.errors_count}")
            print("\n  Par outil :")
            for t, c in sorted(stats.by_tool.items(), key=lambda x: -x[1]):
                print(f"    {t}: {c}")
            if stats.by_agent:
                print("\n  Par agent :")
                for a, c in sorted(stats.by_agent.items(), key=lambda x: -x[1]):
                    print(f"    {a}: {c}")
        return 0

    if args.command == "search":
        results = tracer.search(
            tool=args.tool,
            agent=args.agent,
            status=args.status,
            limit=args.limit,
        )
        if getattr(args, "json", False):
            print(json.dumps({
                "query": results.query,
                "total": results.total_matches,
                "matches": [m.to_dict() for m in results.matches],
            }, indent=2, ensure_ascii=False))
        else:
            print(f"{results.total_matches} résultat(s) pour '{results.query}'")
            for m in results.matches:
                status_icon = "[OK]" if m.status == "ok" else "[x]"
                print(f"  {status_icon} [{m.timestamp}] {m.tool}.{m.operation} "
                      f"({m.duration_ms:.0f}ms, ~{m.tokens_estimated}tok)")
        return 0

    if args.command == "export":
        entries = tracer.export_json()
        if args.format == "json":
            print(json.dumps(entries, indent=2, ensure_ascii=False))
        else:
            for e in entries:
                entry = TraceEntry.from_dict(e)
                print(entry.to_markdown())
        return 0

    if args.command == "clear":
        count = tracer.clear_synapse_entries()
        print(f"{count} entrée(s) Synapse supprimée(s)")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
