#!/usr/bin/env python3
"""
token-budget.py — Enforcement automatique du budget token Grimoire (BM-41 Story 3.3).
============================================================

Étend le Context Router (BM-07) avec un enforcement actif du budget token:
  - 60% : warning + suggestions de réduction
  - 80% : auto-summarize P2/P3 via ContextSummarizer
  - 95% : drop P3/P4 + alerte utilisateur

Modes :
  check   — Vérifie l'utilisation budget et retourne le statut
  enforce — Applique les actions correctives si nécessaire
  report  — Rapport détaillé d'utilisation token par priorité

Usage :
  python3 token-budget.py --project-root . check --model claude-sonnet-4-20250514
  python3 token-budget.py --project-root . enforce --agent dev --model claude-sonnet-4-20250514
  python3 token-budget.py --project-root . report --json

Stdlib only — importe context-router.py et context-summarizer.py par importlib.

Références :
  - MemGPT/Letta: https://github.com/letta-ai/letta — tiered memory & eviction
  - LlamaIndex Memory: https://docs.llamaindex.ai/en/stable/module_guides/deploying/agents/memory/
  - Context Router BM-07: framework/tools/context-router.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import time as _time_mod
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.token_budget")

# ── Version ──────────────────────────────────────────────────────────────────

TOKEN_BUDGET_VERSION = "1.2.0"

# ── Constants ────────────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4

# Thresholds — aligned with context-router.py
WARNING_THRESHOLD = 0.60
CRITICAL_THRESHOLD = 0.80
EMERGENCY_THRESHOLD = 0.95

# Usage history tracking
TOKEN_USAGE_LOG = "_grimoire/_memory/token-usage.jsonl"
TOKEN_USAGE_MAX_ENTRIES = 1000

# Priority names for display
PRIORITY_NAMES = {
    0: "P0 — System (non-droppable)",
    1: "P1 — Agent Core Memory",
    2: "P2 — Shared Context",
    3: "P3 — Project Knowledge",
    4: "P4 — Supplementary",
}

# Model windows (subset — full list in context-router.py)
MODEL_WINDOWS: dict[str, int] = {
    "claude-sonnet-4-20250514": 200_000,
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gemini-2.0-flash": 1_000_000,
    "gemini-2.0-pro": 2_000_000,
    "deepseek-v3": 128_000,
    "deepseek-r1": 128_000,
    "llama-3.3-70b": 128_000,
    "mistral-large": 128_000,
    "codestral": 256_000,
    "qwen-2.5-coder-32b": 32_000,
}

DEFAULT_MODEL = "claude-sonnet-4-20250514"


# ── Token Counter Abstraction (Story 7.4) ──────────────────────────────────


class TokenCounter(ABC):
    """Interface abstraite pour le comptage de tokens."""

    @abstractmethod
    def count(self, text: str) -> int:
        """Compte le nombre de tokens dans un texte."""
        ...

    @abstractmethod
    def count_file(self, path: Path) -> int:
        """Compte le nombre de tokens dans un fichier."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Nom du compteur."""
        ...


class HeuristicCounter(TokenCounter):
    """Compteur heuristique basé sur chars/token (rapide, moins précis)."""

    def __init__(self, chars_per_token: int = CHARS_PER_TOKEN):
        self._cpt = chars_per_token

    def count(self, text: str) -> int:
        return max(1, len(text) // self._cpt)

    def count_file(self, path: Path) -> int:
        try:
            size = path.stat().st_size
            return max(1, size // self._cpt)
        except OSError:
            return 0

    @property
    def name(self) -> str:
        return "heuristic"


class TiktokenCounter(TokenCounter):
    """Compteur précis basé sur tiktoken (lent, nécessite pip install tiktoken)."""

    def __init__(self, model: str = "gpt-4o"):
        self._model = model
        self._encoding = None
        try:
            import tiktoken
            try:
                self._encoding = tiktoken.encoding_for_model(model)
            except KeyError:
                self._encoding = tiktoken.get_encoding("cl100k_base")
        except ImportError as _exc:
            _log.debug("ImportError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    def count(self, text: str) -> int:
        if self._encoding is None:
            return max(1, len(text) // CHARS_PER_TOKEN)
        return len(self._encoding.encode(text))

    def count_file(self, path: Path) -> int:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            return self.count(text)
        except OSError:
            return 0

    @property
    def name(self) -> str:
        return "tiktoken" if self._encoding else "heuristic-fallback"


def get_token_counter(mode: str = "auto", model: str = DEFAULT_MODEL) -> TokenCounter:
    """
    Factory pour obtenir un compteur de tokens.

    Args:
        mode: "auto" | "heuristic" | "tiktoken"
        model: modèle LLM pour tiktoken

    En mode "auto", tente tiktoken puis fallback heuristique.
    En mode "tiktoken" sans le package, fallback heuristique avec warning.
    """
    if mode == "heuristic":
        return HeuristicCounter()
    if mode == "tiktoken":
        counter = TiktokenCounter(model)
        return counter
    # auto
    try:
        import tiktoken  # noqa: F401
        return TiktokenCounter(model)
    except ImportError:
        return HeuristicCounter()


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class PriorityBucket:
    """Budget utilisé par priorité."""
    priority: int
    name: str
    files_count: int = 0
    tokens: int = 0
    percentage: float = 0.0
    files: list[str] = field(default_factory=list)


@dataclass
class BudgetStatus:
    """Statut courant du budget token."""
    model: str = DEFAULT_MODEL
    window_tokens: int = 200_000
    used_tokens: int = 0
    usage_pct: float = 0.0
    level: str = "ok"  # ok | warning | critical | emergency
    agent: str = ""
    buckets: list[PriorityBucket] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


@dataclass
class EnforcementAction:
    """Action corrective appliquée."""
    action_type: str  # "warning" | "summarize" | "drop" | "alert"
    target: str       # f"P{n}" or file path
    detail: str = ""
    tokens_freed: int = 0


@dataclass
class EnforcementReport:
    """Rapport d'enforcement."""
    status_before: BudgetStatus | None = None
    status_after: BudgetStatus | None = None
    actions: list[EnforcementAction] = field(default_factory=list)
    total_tokens_freed: int = 0
    errors: list[str] = field(default_factory=list)


# ── Context Router Bridge ──────────────────────────────────────────────────

def _load_context_router():
    """Importe context-router.py par importlib."""
    mod_name = "context_router"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    router_path = Path(__file__).parent / "context-router.py"
    if not router_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(mod_name, router_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_context_summarizer():
    """Importe context-summarizer.py par importlib."""
    mod_name = "context_summarizer"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    summarizer_path = Path(__file__).parent / "context-summarizer.py"
    if not summarizer_path.exists():
        return None
    spec = importlib.util.spec_from_file_location(mod_name, summarizer_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ── Token Budget Enforcer ──────────────────────────────────────────────────

class TokenBudgetEnforcer:
    """
    Enforcement automatique du budget token.

    Utilise le Context Router pour découvrir les fichiers et calculer
    les plans de chargement, puis applique des actions correctives
    selon le niveau d'utilisation.
    """

    def __init__(
        self,
        project_root: Path,
        model: str = DEFAULT_MODEL,
        agent: str = "",
        warning_threshold: float = WARNING_THRESHOLD,
        critical_threshold: float = CRITICAL_THRESHOLD,
        emergency_threshold: float = EMERGENCY_THRESHOLD,
    ):
        self.project_root = project_root
        self.model = model
        self.agent = agent
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.emergency_threshold = emergency_threshold
        self._router_mod = _load_context_router()
        self._summarizer_mod = _load_context_summarizer()

    def _get_window(self) -> int:
        """Récupère la fenêtre token du modèle."""
        if self._router_mod and hasattr(self._router_mod, "MODEL_WINDOWS"):
            return self._router_mod.MODEL_WINDOWS.get(self.model, 200_000)
        return MODEL_WINDOWS.get(self.model, 200_000)

    def _discover_files(self) -> list[tuple[str, int, int]]:
        """
        Découvre les fichiers contexte et estime leurs tokens.
        Retourne: list[(filepath, tokens, priority)]
        """
        files: list[tuple[str, int, int]] = []

        if self._router_mod:
            try:
                # Use context router's discovery
                ctx_files = self._router_mod.discover_context_files(
                    self.project_root, self.agent
                )
                for cf in ctx_files:
                    tokens = getattr(cf, "estimated_tokens", 0) or getattr(cf, "tokens", 0)
                    prio = getattr(cf, "priority", 4)
                    if hasattr(prio, "value"):
                        prio = prio.value
                    path = getattr(cf, "path", str(cf))
                    files.append((str(path), tokens, prio))
                return files
            except Exception as _exc:
                _log.debug("Exception suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        # Fallback: scan memory / agent files manually
        for pattern, prio in [
            ("_grimoire/_memory/*.md", 1),
            ("_grimoire/_memory/agent-learnings/*.md", 1),
            ("docs/*.md", 3),
            ("_grimoire-output/planning-artifacts/*.md", 3),
        ]:
            for f in sorted(self.project_root.glob(pattern)):
                try:
                    size = f.stat().st_size
                    tokens = size // CHARS_PER_TOKEN
                    files.append((str(f.relative_to(self.project_root)), tokens, prio))
                except OSError:
                    continue

        return files

    def check(self) -> BudgetStatus:
        """Vérifie l'utilisation courante du budget token."""
        window = self._get_window()
        files = self._discover_files()

        # Build priority buckets
        buckets_data: dict[int, PriorityBucket] = {}
        for prio in range(5):
            buckets_data[prio] = PriorityBucket(
                priority=prio,
                name=PRIORITY_NAMES.get(prio, f"P{prio}"),
            )

        total_tokens = 0
        for filepath, tokens, prio in files:
            prio = min(4, max(0, prio))
            buckets_data[prio].files_count += 1
            buckets_data[prio].tokens += tokens
            buckets_data[prio].files.append(filepath)
            total_tokens += tokens

        # Calculate percentages
        for bucket in buckets_data.values():
            bucket.percentage = round(bucket.tokens / window, 4) if window > 0 else 0.0

        usage_pct = round(total_tokens / window, 4) if window > 0 else 0.0

        # Determine level
        if usage_pct >= self.emergency_threshold:
            level = "emergency"
        elif usage_pct >= self.critical_threshold:
            level = "critical"
        elif usage_pct >= self.warning_threshold:
            level = "warning"
        else:
            level = "ok"

        # Generate recommendations
        recommendations = []
        if level == "warning":
            recommendations.append(
                f"Budget à {usage_pct:.0%} — envisagez de résumer les sections anciennes"
            )
            if buckets_data[3].tokens > 0:
                recommendations.append(
                    f"P3 (Project Knowledge) utilise {buckets_data[3].tokens:,} tokens — "
                    f"candidat au résumé"
                )
        elif level == "critical":
            recommendations.append(
                f"Budget à {usage_pct:.0%} — résumé automatique P2/P3 recommandé"
            )
            recommendations.append("Lancez: token-budget.py enforce")
        elif level == "emergency":
            recommendations.append(
                f"⚠️ Budget à {usage_pct:.0%} — drop P3/P4 nécessaire"
            )
            recommendations.append("Lancez: token-budget.py enforce --force")

        status = BudgetStatus(
            model=self.model,
            window_tokens=window,
            used_tokens=total_tokens,
            usage_pct=usage_pct,
            level=level,
            agent=self.agent,
            buckets=[buckets_data[p] for p in range(5)],
            recommendations=recommendations,
        )
        # Auto-log usage snapshot
        _log_usage(self.project_root, status)
        return status

    def enforce(self, force: bool = False, dry_run: bool = False) -> EnforcementReport:
        """
        Applique les actions correctives selon le niveau de budget.

        Actions :
          - warning (60-80%):  log warning + suggestions
          - critical (80-95%): auto-summarize P2/P3
          - emergency (95%+):  drop P3/P4 + alert
        """
        report = EnforcementReport()
        status = self.check()
        report.status_before = status

        if status.level == "ok":
            report.status_after = status
            return report

        # ── Warning actions ─────────────────────────────────────────────
        if status.level in ("warning", "critical", "emergency"):
            report.actions.append(EnforcementAction(
                action_type="warning",
                target="all",
                detail=f"Budget à {status.usage_pct:.0%} ({status.level})",
            ))

        # ── Critical: auto-summarize P2/P3 ──────────────────────────────
        if status.level in ("critical", "emergency"):
            if self._summarizer_mod:
                try:
                    summarizer = self._summarizer_mod.ContextSummarizer(
                        project_root=self.project_root,
                        age_threshold_days=14,  # More aggressive at critical
                        max_summary_tokens=300,
                    )

                    if not dry_run:
                        summary_report = summarizer.summarize(dry_run=False)
                    else:
                        summary_report = summarizer.summarize(dry_run=True)

                    tokens_freed = summary_report.tokens_before - summary_report.tokens_after
                    report.actions.append(EnforcementAction(
                        action_type="summarize",
                        target="P2/P3",
                        detail=(
                            f"Résumé {summary_report.sections_summarized} sections, "
                            f"{summary_report.digests_created} digests"
                        ),
                        tokens_freed=tokens_freed,
                    ))
                    report.total_tokens_freed += tokens_freed
                except Exception as e:
                    report.errors.append(f"Summarization failed: {e}")
            else:
                report.errors.append(
                    "context-summarizer.py non trouvé — impossible de résumer"
                )

        # ── Emergency: drop P3/P4 ────────────────────────────────────────
        if status.level == "emergency" or (status.level == "critical" and force):
            for bucket in status.buckets:
                if bucket.priority >= 3 and bucket.tokens > 0:
                    report.actions.append(EnforcementAction(
                        action_type="drop",
                        target=f"P{bucket.priority}",
                        detail=(
                            f"Drop {bucket.files_count} fichiers "
                            f"({bucket.tokens:,} tokens)"
                        ),
                        tokens_freed=bucket.tokens,
                    ))
                    report.total_tokens_freed += bucket.tokens

            report.actions.append(EnforcementAction(
                action_type="alert",
                target="user",
                detail=(
                    f"⚠️ Budget critique ({status.usage_pct:.0%}) — "
                    f"P3/P4 exclus du contexte"
                ),
            ))

        # Recalculate after enforcement
        if not dry_run and report.total_tokens_freed > 0:
            report.status_after = self.check()
        else:
            # Simulate new status
            simulated = BudgetStatus(
                model=status.model,
                window_tokens=status.window_tokens,
                used_tokens=max(0, status.used_tokens - report.total_tokens_freed),
                usage_pct=max(0, (status.used_tokens - report.total_tokens_freed) / status.window_tokens)
                if status.window_tokens > 0 else 0.0,
                level="ok",
                agent=status.agent,
            )
            # Recalculate level
            if simulated.usage_pct >= self.emergency_threshold:
                simulated.level = "emergency"
            elif simulated.usage_pct >= self.critical_threshold:
                simulated.level = "critical"
            elif simulated.usage_pct >= self.warning_threshold:
                simulated.level = "warning"
            report.status_after = simulated

        return report


# ── Usage History Tracking ───────────────────────────────────────────────────


def _log_usage(project_root: Path, status: BudgetStatus) -> None:
    """Append usage snapshot to JSONL history file."""
    log_path = project_root / TOKEN_USAGE_LOG
    entry = {
        "ts": _time_mod.strftime("%Y-%m-%dT%H:%M:%SZ", _time_mod.gmtime()),
        "model": status.model,
        "used": status.used_tokens,
        "window": status.window_tokens,
        "pct": round(status.usage_pct, 4),
        "level": status.level,
        "agent": status.agent or "",
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        _log.debug("Failed to write usage log: %s", exc)


def _prune_usage_log(project_root: Path, max_entries: int = TOKEN_USAGE_MAX_ENTRIES) -> None:
    """Keep only the last *max_entries* in the usage log."""
    log_path = project_root / TOKEN_USAGE_LOG
    if not log_path.exists():
        return
    try:
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) > max_entries:
            log_path.write_text(
                "\n".join(lines[-max_entries:]) + "\n", encoding="utf-8"
            )
    except OSError as exc:
        _log.debug("Failed to prune usage log: %s", exc)


def _load_usage_history(project_root: Path, last_n: int = 100) -> list[dict]:
    """Load the last *last_n* usage snapshots from JSONL."""
    log_path = project_root / TOKEN_USAGE_LOG
    if not log_path.exists():
        return []
    entries: list[dict] = []
    try:
        for raw in log_path.read_text(encoding="utf-8").strip().splitlines():
            if not raw.strip():
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return entries[-last_n:]


def usage_trend(project_root: Path, last_n: int = 50) -> dict:
    """
    Compute token usage trend from history.

    Returns dict with: entries (count), avg_pct, max_pct, min_pct,
    direction (↑ ↓ →), latest.
    """
    history = _load_usage_history(project_root, last_n)
    if not history:
        return {"entries": 0, "avg_pct": 0, "max_pct": 0, "min_pct": 0,
                "direction": "→", "latest": None}

    pcts = [h.get("pct", 0) for h in history]
    avg_pct = round(sum(pcts) / len(pcts), 4)
    max_pct = max(pcts)
    min_pct = min(pcts)

    # Direction: compare first half vs second half
    mid = len(pcts) // 2
    if mid > 0:
        first_avg = sum(pcts[:mid]) / mid
        second_avg = sum(pcts[mid:]) / (len(pcts) - mid)
        if second_avg > first_avg * 1.1:
            direction = "↑"
        elif second_avg < first_avg * 0.9:
            direction = "↓"
        else:
            direction = "→"
    else:
        direction = "→"

    return {
        "entries": len(history),
        "avg_pct": avg_pct,
        "max_pct": max_pct,
        "min_pct": min_pct,
        "direction": direction,
        "latest": history[-1] if history else None,
    }


# ── MCP Tool Interface ──────────────────────────────────────────────────────

def mcp_context_budget(project_root: str, model: str = "", agent: str = "") -> dict:
    """
    MCP tool `bmad_context_budget` — retourne le statut budget token.

    Appelable depuis grimoire-mcp-tools.py.
    """
    root = Path(project_root).resolve()
    enforcer = TokenBudgetEnforcer(
        project_root=root,
        model=model or DEFAULT_MODEL,
        agent=agent,
    )
    status = enforcer.check()
    result = asdict(status)
    result["trend"] = usage_trend(root)
    return result


# ── Config Loading ──────────────────────────────────────────────────────────

def load_budget_config(project_root: Path) -> dict:
    """Charge la config depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [project_root / "project-context.yaml", project_root / "grimoire.yaml"]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("token_budget", {})
    return {}


# ── CLI ─────────────────────────────────────────────────────────────────────

def _level_icon(level: str) -> str:
    icons = {"ok": "✅", "warning": "⚠️", "critical": "🔶", "emergency": "🔴"}
    return icons.get(level, "❓")


def _print_status(status: BudgetStatus) -> None:
    icon = _level_icon(status.level)
    print(f"\n  {icon} Token Budget — {status.level.upper()}")
    print(f"  {'─' * 55}")
    print(f"  Modèle     : {status.model}")
    print(f"  Fenêtre    : {status.window_tokens:,} tokens")
    print(f"  Utilisé    : {status.used_tokens:,} tokens ({status.usage_pct:.1%})")
    if status.agent:
        print(f"  Agent      : {status.agent}")
    print()

    # Bar chart
    bar_width = 50
    filled = int(status.usage_pct * bar_width)
    bar = "█" * filled + "░" * (bar_width - filled)
    print(f"  [{bar}] {status.usage_pct:.1%}")

    # Thresholds markers
    w_pos = int(WARNING_THRESHOLD * bar_width)
    c_pos = int(CRITICAL_THRESHOLD * bar_width)
    e_pos = int(EMERGENCY_THRESHOLD * bar_width)
    markers = [" "] * (bar_width + 2)
    markers[w_pos] = "⚠"
    markers[c_pos] = "🔶"
    markers[e_pos] = "🔴"
    print(f"  {''.join(markers)}")
    print()

    # Buckets
    print("  Par priorité :")
    for bucket in status.buckets:
        if bucket.tokens > 0 or bucket.files_count > 0:
            pct = f"{bucket.percentage:.1%}" if bucket.percentage > 0 else "0%"
            print(f"    {bucket.name:30s} │ {bucket.tokens:>8,} tok "
                  f"({pct:>5s}) │ {bucket.files_count} fichiers")
    print()

    if status.recommendations:
        print("  Recommandations :")
        for rec in status.recommendations:
            print(f"    → {rec}")
        print()


def _print_enforcement(report: EnforcementReport) -> None:
    if report.status_before:
        icon = _level_icon(report.status_before.level)
        print(f"\n  {icon} Enforcement — Avant : "
              f"{report.status_before.usage_pct:.1%} ({report.status_before.level})")

    if report.actions:
        print(f"\n  Actions appliquées ({len(report.actions)}) :")
        for action in report.actions:
            icons = {"warning": "⚠️", "summarize": "📦", "drop": "🗑️", "alert": "🔔"}
            icon = icons.get(action.action_type, "•")
            freed = f" [{action.tokens_freed:,} tok libérés]" if action.tokens_freed else ""
            print(f"    {icon} [{action.action_type}] {action.target}: {action.detail}{freed}")
    else:
        print("\n  ✅ Aucune action nécessaire")

    if report.total_tokens_freed > 0:
        print(f"\n  Total libéré : {report.total_tokens_freed:,} tokens")

    if report.status_after:
        icon = _level_icon(report.status_after.level)
        print(f"  {icon} Après : {report.status_after.usage_pct:.1%} "
              f"({report.status_after.level})")

    if report.errors:
        print("\n  Erreurs :")
        for err in report.errors:
            print(f"    ❌ {err}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Token Budget Enforcer — Enforcement automatique du budget token Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Modèle LLM cible (défaut: {DEFAULT_MODEL})")
    parser.add_argument("--agent", default="", help="Agent ID pour filtrer le contexte")
    parser.add_argument("--version", action="version",
                        version=f"token-budget {TOKEN_BUDGET_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # check
    sub.add_parser("check", help="Vérifier le budget token")

    # enforce
    enf_p = sub.add_parser("enforce", help="Appliquer les actions correctives")
    enf_p.add_argument("--force", action="store_true",
                       help="Forcer le drop P3/P4 même en mode critical")
    enf_p.add_argument("--dry-run", action="store_true",
                       help="Simulation sans écriture")
    enf_p.add_argument("--json", action="store_true", help="Output JSON")

    # report
    rep_p = sub.add_parser("report", help="Rapport détaillé")
    rep_p.add_argument("--json", action="store_true", help="Output JSON")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()

    enforcer = TokenBudgetEnforcer(
        project_root=project_root,
        model=args.model,
        agent=args.agent,
    )

    if args.command == "check":
        status = enforcer.check()
        _print_status(status)

    elif args.command == "enforce":
        report = enforcer.enforce(
            force=getattr(args, "force", False),
            dry_run=getattr(args, "dry_run", False),
        )
        if getattr(args, "json", False):
            out = {
                "before": asdict(report.status_before) if report.status_before else None,
                "after": asdict(report.status_after) if report.status_after else None,
                "actions": [asdict(a) for a in report.actions],
                "total_tokens_freed": report.total_tokens_freed,
                "errors": report.errors,
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            if getattr(args, "dry_run", False):
                print("  ℹ️  Mode dry-run — aucune écriture")
            _print_enforcement(report)

    elif args.command == "report":
        status = enforcer.check()
        if getattr(args, "json", False):
            print(json.dumps(asdict(status), ensure_ascii=False, indent=2))
        else:
            _print_status(status)


if __name__ == "__main__":
    main()
