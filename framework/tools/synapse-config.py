#!/usr/bin/env python3
"""
synapse-config.py — Configuration centralisée Synapse Grimoire (BM-46 Story 7.5).
============================================================

Point d'entrée unique pour la configuration de tous les outils Synapse.
Charge et valide les paramètres depuis la section ``synapse:`` de
``project-context.yaml`` (ou ``grimoire.yaml``).

Modes :
  show      — Affiche la configuration active (JSON pretty)
  validate  — Vérifie la cohérence de la configuration
  generate  — Génère un template synapse-config dans project-context.yaml
  generate-mcp — Génère la config MCP pour l'IDE

Usage :
  python3 synapse-config.py --project-root . show
  python3 synapse-config.py --project-root . validate
  python3 synapse-config.py --project-root . generate --output project-context.yaml
  python3 synapse-config.py --project-root . generate-mcp

Stdlib only.

Références :
  - 12 Factor App — Config: https://12factor.net/config
  - Pydantic Settings: https://docs.pydantic.dev/latest/concepts/pydantic_settings/
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.synapse_config")

# ── Version ──────────────────────────────────────────────────────────────────

SYNAPSE_CONFIG_VERSION = "1.0.0"

# ── Config Data Classes ──────────────────────────────────────────────────────


@dataclass
class TraceConfig:
    """Configuration du tracing Synapse."""

    enabled: bool = True
    output: str = "_grimoire-output/Grimoire_TRACE.md"
    include_tokens: bool = True
    include_duration: bool = True
    max_entries: int = 10000


@dataclass
class LLMRouterConfig:
    """Configuration du LLM Router."""

    default_model: str = "claude-sonnet-4-20250514"
    fallback_chain: list[str] = field(default_factory=lambda: ["claude-haiku", "deepseek-v3"])
    budget_per_session: int = 100000
    classify_by_heuristics: bool = True


@dataclass
class RAGConfig:
    """Configuration du pipeline RAG."""

    embedding_model: str = "all-MiniLM-L6-v2"
    qdrant_path: str = ".qdrant_data"
    chunk_size: int = 512
    chunk_overlap: int = 50
    top_k: int = 5
    similarity_threshold: float = 0.5


@dataclass
class TokenBudgetConfig:
    """Configuration du token budget enforcer."""

    warning_threshold: float = 0.60
    auto_summarize_threshold: float = 0.80
    critical_threshold: float = 0.95
    counter: str = "auto"  # auto | heuristic | tiktoken


@dataclass
class SemanticCacheConfig:
    """Configuration du semantic cache."""

    similarity_threshold: float = 0.90
    ttl_hours: int = 168
    max_entries: int = 10000


@dataclass
class MessageBusConfig:
    """Configuration du message bus."""

    backend: str = "in-process"  # in-process | redis | nats
    redis_url: str = "redis://localhost:6379"
    max_queue_size: int = 1000


@dataclass
class OrchestratorConfig:
    """Configuration de l'orchestrateur."""

    default_mode: str = "auto"  # auto | simulated | sequential | parallel
    budget_cap: int = 500000
    max_concurrent: int = 3
    worker_timeout: int = 120


@dataclass
class SynapseConfig:
    """Configuration globale Synapse."""

    enabled: bool = True
    version: str = SYNAPSE_CONFIG_VERSION
    trace: TraceConfig = field(default_factory=TraceConfig)
    llm_router: LLMRouterConfig = field(default_factory=LLMRouterConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    token_budget: TokenBudgetConfig = field(default_factory=TokenBudgetConfig)
    semantic_cache: SemanticCacheConfig = field(default_factory=SemanticCacheConfig)
    message_bus: MessageBusConfig = field(default_factory=MessageBusConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> SynapseConfig:
        """Parse un dictionnaire (section synapse: du yaml) en SynapseConfig."""
        cfg = cls()
        if not isinstance(data, dict):
            return cfg

        cfg.enabled = data.get("enabled", cfg.enabled)

        if "trace" in data and isinstance(data["trace"], dict):
            td = data["trace"]
            cfg.trace = TraceConfig(
                enabled=td.get("enabled", cfg.trace.enabled),
                output=td.get("output", cfg.trace.output),
                include_tokens=td.get("include_tokens", cfg.trace.include_tokens),
                include_duration=td.get("include_duration", cfg.trace.include_duration),
                max_entries=td.get("max_entries", cfg.trace.max_entries),
            )

        if "llm_router" in data and isinstance(data["llm_router"], dict):
            lr = data["llm_router"]
            cfg.llm_router = LLMRouterConfig(
                default_model=lr.get("default_model", cfg.llm_router.default_model),
                fallback_chain=lr.get("fallback_chain", cfg.llm_router.fallback_chain),
                budget_per_session=lr.get("budget_per_session", cfg.llm_router.budget_per_session),
                classify_by_heuristics=lr.get("classify_by_heuristics", cfg.llm_router.classify_by_heuristics),
            )

        if "rag" in data and isinstance(data["rag"], dict):
            rr = data["rag"]
            cfg.rag = RAGConfig(
                embedding_model=rr.get("embedding_model", cfg.rag.embedding_model),
                qdrant_path=rr.get("qdrant_path", cfg.rag.qdrant_path),
                chunk_size=rr.get("chunk_size", cfg.rag.chunk_size),
                chunk_overlap=rr.get("chunk_overlap", cfg.rag.chunk_overlap),
                top_k=rr.get("top_k", cfg.rag.top_k),
                similarity_threshold=rr.get("similarity_threshold", cfg.rag.similarity_threshold),
            )

        if "token_budget" in data and isinstance(data["token_budget"], dict):
            tb = data["token_budget"]
            cfg.token_budget = TokenBudgetConfig(
                warning_threshold=tb.get("warning_threshold", cfg.token_budget.warning_threshold),
                auto_summarize_threshold=tb.get("auto_summarize_threshold", cfg.token_budget.auto_summarize_threshold),
                critical_threshold=tb.get("critical_threshold", cfg.token_budget.critical_threshold),
                counter=tb.get("counter", cfg.token_budget.counter),
            )

        if "semantic_cache" in data and isinstance(data["semantic_cache"], dict):
            sc = data["semantic_cache"]
            cfg.semantic_cache = SemanticCacheConfig(
                similarity_threshold=sc.get("similarity_threshold", cfg.semantic_cache.similarity_threshold),
                ttl_hours=sc.get("ttl_hours", cfg.semantic_cache.ttl_hours),
                max_entries=sc.get("max_entries", cfg.semantic_cache.max_entries),
            )

        if "message_bus" in data and isinstance(data["message_bus"], dict):
            mb = data["message_bus"]
            cfg.message_bus = MessageBusConfig(
                backend=mb.get("backend", cfg.message_bus.backend),
                redis_url=mb.get("redis_url", cfg.message_bus.redis_url),
                max_queue_size=mb.get("max_queue_size", cfg.message_bus.max_queue_size),
            )

        if "orchestrator" in data and isinstance(data["orchestrator"], dict):
            oc = data["orchestrator"]
            cfg.orchestrator = OrchestratorConfig(
                default_mode=oc.get("default_mode", cfg.orchestrator.default_mode),
                budget_cap=oc.get("budget_cap", cfg.orchestrator.budget_cap),
                max_concurrent=oc.get("max_concurrent", cfg.orchestrator.max_concurrent),
                worker_timeout=oc.get("worker_timeout", cfg.orchestrator.worker_timeout),
            )

        return cfg


# ── Config Cache ─────────────────────────────────────────────────────────────

_CONFIG_CACHE: dict[str, SynapseConfig] = {}


def clear_config_cache() -> None:
    """Vide le cache de configuration (utile pour les tests)."""
    _CONFIG_CACHE.clear()


# ── YAML Loader (stdlib) ────────────────────────────────────────────────────


def _parse_yaml_simple(text: str) -> dict:
    """
    Parseur YAML minimal (stdlib) — supporte les scalaires, listes et dicts
    imbriqués au format clé: valeur. Suffisant pour project-context.yaml.
    """
    try:
        import yaml
        return yaml.safe_load(text) or {}
    except ImportError as _exc:
        _log.debug("ImportError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues

    # Fallback: mini parser ligne par ligne
    result: dict = {}
    stack: list[tuple[dict, int]] = [(result, -1)]

    for raw_line in text.split("\n"):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip())

        # Pop stack to correct level
        while len(stack) > 1 and stack[-1][1] >= indent:
            stack.pop()

        parent = stack[-1][0]

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            if val == "":
                # New dict
                new_dict: dict = {}
                parent[key] = new_dict
                stack.append((new_dict, indent))
            elif val.startswith("[") and val.endswith("]"):
                # Inline list
                items = [v.strip().strip('"').strip("'") for v in val[1:-1].split(",") if v.strip()]
                parent[key] = [_coerce_value(i) for i in items]
            else:
                parent[key] = _coerce_value(val)
        elif stripped.startswith("- "):
            # List item
            item_val = stripped[2:].strip()
            # Find parent key for list
            for pk in reversed(list(parent.keys())):
                if isinstance(parent[pk], list):
                    parent[pk].append(_coerce_value(item_val))
                    break
            else:
                parent.setdefault("_items", []).append(_coerce_value(item_val))

    return result


def _coerce_value(val: str):
    """Coerce une valeur string vers le bon type Python."""
    if val in ("true", "True", "yes"):
        return True
    if val in ("false", "False", "no"):
        return False
    if val in ("null", "None", "~"):
        return None
    try:
        return int(val)
    except ValueError as _exc:
        _log.debug("ValueError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues
    try:
        return float(val)
    except ValueError as _exc:
        _log.debug("ValueError suppressed: %s", _exc)
        # Silent exception — add logging when investigating issues
    # Strip quotes
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    return val


# ── Config Loader ────────────────────────────────────────────────────────────


def load_synapse_config(project_root: str | Path) -> SynapseConfig:
    """
    Point d'entrée unique pour charger la configuration Synapse.

    Cherche la section ``synapse:`` dans project-context.yaml ou grimoire.yaml.
    Retourne les defaults si aucune config trouvée (graceful degradation).
    Les résultats sont cachés par project_root.
    """
    root = Path(project_root).resolve()
    cache_key = str(root)

    if cache_key in _CONFIG_CACHE:
        return _CONFIG_CACHE[cache_key]

    config = SynapseConfig()

    for candidate in [root / "project-context.yaml", root / "grimoire.yaml"]:
        if candidate.exists():
            try:
                text = candidate.read_text(encoding="utf-8")
                parsed = _parse_yaml_simple(text)
                synapse_data = parsed.get("synapse", {})
                if synapse_data:
                    config = SynapseConfig.from_dict(synapse_data)
                break
            except Exception as _exc:
                _log.debug("Exception suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

    _CONFIG_CACHE[cache_key] = config
    return config


# ── Validation ──────────────────────────────────────────────────────────────


@dataclass
class ValidationIssue:
    """Issue rencontrée lors de la validation."""

    level: str  # "error" | "warning"
    section: str
    field: str
    message: str


def validate_config(config: SynapseConfig) -> list[ValidationIssue]:
    """Valide la cohérence de la configuration."""
    issues: list[ValidationIssue] = []

    # Trace
    if config.trace.max_entries < 1:
        issues.append(ValidationIssue("error", "trace", "max_entries", "Doit être ≥ 1"))

    # LLM Router
    if not config.llm_router.default_model:
        issues.append(ValidationIssue("error", "llm_router", "default_model", "Modèle par défaut requis"))
    if config.llm_router.budget_per_session < 0:
        issues.append(ValidationIssue("error", "llm_router", "budget_per_session", "Doit être ≥ 0"))

    # RAG
    if config.rag.chunk_size < 64:
        issues.append(ValidationIssue("warning", "rag", "chunk_size", "Chunk trop petit (< 64), risque de perte"))
    if config.rag.chunk_size > 4096:
        issues.append(ValidationIssue("warning", "rag", "chunk_size", "Chunk très grand (> 4096)"))
    if config.rag.chunk_overlap >= config.rag.chunk_size:
        issues.append(ValidationIssue("error", "rag", "chunk_overlap", "Overlap doit être < chunk_size"))
    if not 0 < config.rag.similarity_threshold <= 1:
        issues.append(ValidationIssue("error", "rag", "similarity_threshold", "Doit être dans ]0, 1]"))
    if config.rag.top_k < 1:
        issues.append(ValidationIssue("error", "rag", "top_k", "Doit être ≥ 1"))

    # Token budget
    tb = config.token_budget
    if not 0 < tb.warning_threshold < 1:
        issues.append(ValidationIssue("error", "token_budget", "warning_threshold", "Doit être dans ]0, 1["))
    if not 0 < tb.auto_summarize_threshold < 1:
        issues.append(ValidationIssue("error", "token_budget", "auto_summarize_threshold", "Doit être dans ]0, 1["))
    if not 0 < tb.critical_threshold <= 1:
        issues.append(ValidationIssue("error", "token_budget", "critical_threshold", "Doit être dans ]0, 1]"))
    if tb.warning_threshold >= tb.auto_summarize_threshold:
        issues.append(ValidationIssue("warning", "token_budget", "warning_threshold",
                                       "warning devrait être < auto_summarize"))
    if tb.auto_summarize_threshold >= tb.critical_threshold:
        issues.append(ValidationIssue("warning", "token_budget", "auto_summarize_threshold",
                                       "auto_summarize devrait être < critical"))
    if tb.counter not in ("auto", "heuristic", "tiktoken"):
        issues.append(ValidationIssue("error", "token_budget", "counter",
                                       f"Valeur invalide '{tb.counter}', attendu: auto|heuristic|tiktoken"))

    # Semantic cache
    sc = config.semantic_cache
    if not 0 < sc.similarity_threshold <= 1:
        issues.append(ValidationIssue("error", "semantic_cache", "similarity_threshold", "Doit être dans ]0, 1]"))
    if sc.ttl_hours < 1:
        issues.append(ValidationIssue("error", "semantic_cache", "ttl_hours", "Doit être ≥ 1"))
    if sc.max_entries < 1:
        issues.append(ValidationIssue("error", "semantic_cache", "max_entries", "Doit être ≥ 1"))

    # Message bus
    if config.message_bus.backend not in ("in-process", "redis", "nats"):
        issues.append(ValidationIssue("error", "message_bus", "backend",
                                       f"Backend invalide '{config.message_bus.backend}'"))
    if config.message_bus.max_queue_size < 1:
        issues.append(ValidationIssue("error", "message_bus", "max_queue_size", "Doit être ≥ 1"))

    # Orchestrator
    oc = config.orchestrator
    if oc.default_mode not in ("auto", "simulated", "sequential", "parallel"):
        issues.append(ValidationIssue("error", "orchestrator", "default_mode",
                                       f"Mode invalide '{oc.default_mode}'"))
    if oc.budget_cap < 0:
        issues.append(ValidationIssue("error", "orchestrator", "budget_cap", "Doit être ≥ 0"))
    if oc.max_concurrent < 1:
        issues.append(ValidationIssue("error", "orchestrator", "max_concurrent", "Doit être ≥ 1"))
    if oc.worker_timeout < 1:
        issues.append(ValidationIssue("error", "orchestrator", "worker_timeout", "Doit être ≥ 1"))

    return issues


# ── Template Generator ──────────────────────────────────────────────────────

SYNAPSE_YAML_TEMPLATE = """\
# ── Synapse Intelligence Layer Configuration ────────────────────────────────
synapse:
  enabled: true

  trace:
    enabled: true
    output: _grimoire-output/Grimoire_TRACE.md
    include_tokens: true
    include_duration: true
    max_entries: 10000

  llm_router:
    default_model: claude-sonnet-4-20250514
    fallback_chain: [claude-haiku, deepseek-v3]
    budget_per_session: 100000
    classify_by_heuristics: true

  rag:
    embedding_model: all-MiniLM-L6-v2
    qdrant_path: .qdrant_data
    chunk_size: 512
    chunk_overlap: 50
    top_k: 5
    similarity_threshold: 0.5

  token_budget:
    warning_threshold: 0.6
    auto_summarize_threshold: 0.8
    critical_threshold: 0.95
    counter: auto

  semantic_cache:
    similarity_threshold: 0.9
    ttl_hours: 168
    max_entries: 10000

  message_bus:
    backend: in-process
    redis_url: redis://localhost:6379
    max_queue_size: 1000

  orchestrator:
    default_mode: auto
    budget_cap: 500000
    max_concurrent: 3
    worker_timeout: 120
"""

MCP_CONFIG_TEMPLATE = """\
{
  "mcpServers": {
    "grimoire-synapse": {
      "command": "python3",
      "args": ["{tools_dir}/grimoire-mcp-tools.py"],
      "env": {
        "Grimoire_PROJECT_ROOT": "{project_root}"
      }
    }
  }
}
"""


def generate_template() -> str:
    """Retourne le template YAML pour la section synapse."""
    return SYNAPSE_YAML_TEMPLATE


def generate_mcp_config(project_root: Path) -> str:
    """Génère la configuration MCP pour l'IDE."""
    tools_dir = project_root / "framework" / "tools"
    return MCP_CONFIG_TEMPLATE.replace("{tools_dir}", str(tools_dir)).replace(
        "{project_root}", str(project_root)
    )


# ── MCP Interface ────────────────────────────────────────────────────────────


def mcp_synapse_config(
    project_root: str,
    action: str = "show",
) -> dict:
    """
    MCP tool ``bmad_synapse_config`` — gère la configuration Synapse.

    Actions: show, validate, generate, generate-mcp
    """
    root = Path(project_root).resolve()

    if action == "show":
        config = load_synapse_config(root)
        return {"status": "ok", "config": config.to_dict()}

    if action == "validate":
        config = load_synapse_config(root)
        issues = validate_config(config)
        errors = [asdict(i) for i in issues if i.level == "error"]
        warnings = [asdict(i) for i in issues if i.level == "warning"]
        return {
            "status": "valid" if not errors else "invalid",
            "errors": errors,
            "warnings": warnings,
            "total_issues": len(issues),
        }

    if action == "generate":
        return {"status": "ok", "template": generate_template()}

    if action == "generate-mcp":
        return {"status": "ok", "config": generate_mcp_config(root)}

    return {"status": "error", "message": f"Action inconnue: {action}"}


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synapse-config",
        description="Configuration centralisée Synapse Grimoire",
    )
    parser.add_argument("--project-root", type=Path, default=Path(),
                        help="Racine du projet Grimoire")
    parser.add_argument("--version", action="version", version=f"%(prog)s {SYNAPSE_CONFIG_VERSION}")

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("show", help="Affiche la configuration active")
    sub.add_parser("validate", help="Valide la configuration")

    gen_p = sub.add_parser("generate", help="Génère un template de configuration")
    gen_p.add_argument("--output", type=Path, help="Fichier de sortie")

    sub.add_parser("generate-mcp", help="Génère la config MCP pour l'IDE")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    root = Path(args.project_root).resolve()

    if args.command == "show":
        config = load_synapse_config(root)
        print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
        return 0

    if args.command == "validate":
        config = load_synapse_config(root)
        issues = validate_config(config)
        errors = [i for i in issues if i.level == "error"]
        warnings = [i for i in issues if i.level == "warning"]

        if not issues:
            print("✅ Configuration valide — aucune issue.")
            return 0

        for i in errors:
            print(f"❌ [{i.section}] {i.field}: {i.message}")
        for i in warnings:
            print(f"⚠️  [{i.section}] {i.field}: {i.message}")
        print(f"\n{len(errors)} erreur(s), {len(warnings)} avertissement(s)")
        return 1 if errors else 0

    if args.command == "generate":
        template = generate_template()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(template, encoding="utf-8")
            print(f"✅ Template écrit dans {args.output}")
        else:
            print(template)
        return 0

    if args.command == "generate-mcp":
        config = generate_mcp_config(root)
        print(config)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
