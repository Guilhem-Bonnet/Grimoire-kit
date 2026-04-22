#!/usr/bin/env python3
"""
llm-router.py — Routeur LLM intelligent Grimoire (BM-40).
============================================================

Dirige chaque requête agent vers le modèle LLM optimal selon :
  - La complexité de la tâche (trivial / standard / complex / expert)
  - Le type de contenu (code, raisonnement, formatage, embedding)
  - Le coût acceptable (budget per-agent configurable)
  - La disponibilité du modèle (fallback chain)

Usage :
  python3 llm-router.py --project-root . route --agent architect --prompt "Design auth system"
  python3 llm-router.py --project-root . route --agent qa --prompt "Format this list"
  python3 llm-router.py --project-root . classify --prompt "Refactor the auth module using CQRS"
  python3 llm-router.py --project-root . models                    # Liste les modèles configurés
  python3 llm-router.py --project-root . stats                     # Stats d'utilisation
  python3 llm-router.py --project-root . stats --recommend          # Recommandations d'optimisation
  python3 llm-router.py --project-root . config                    # Affiche la config active

Stdlib only — aucune dépendance externe.

Références :
  - RouteLLM (LMSYS)  : https://github.com/lm-sys/RouteLLM
  - Semantic Router    : https://github.com/aurelio-labs/semantic-router
  - LiteLLM            : https://github.com/BerriAI/litellm
  - OpenRouter         : https://openrouter.ai/docs/model-routing
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import ClassVar

_log = logging.getLogger("grimoire.llm_router")

# ── Version ──────────────────────────────────────────────────────────────────

LLM_ROUTER_VERSION = "1.1.0"

# ── Complexity Classification ────────────────────────────────────────────────

EXPERT_INDICATORS: list[str] = [
    "architecture", "distributed", "migration strategy", "system design",
    "trade-off analysis", "security audit", "threat model", "scalability",
    "consensus protocol", "formal verification", "proof", "mathematical",
    "adversarial", "zero-knowledge", "cryptograph",
]

COMPLEX_INDICATORS: list[str] = [
    "design", "refactor", "compare", "evaluate", "analyze dependencies",
    "performance", "optimize", "review", "debug complex", "integration",
    "api design", "schema design", "data model", "workflow", "pipeline",
    "orchestrat", "multi-", "cross-", "end-to-end",
]

STANDARD_INDICATORS: list[str] = [
    "implement", "create", "add feature", "write test", "fix bug",
    "update", "modify", "extend", "add endpoint", "handle error",
    "validate", "parse", "transform", "convert", "generate",
]

TRIVIAL_INDICATORS: list[str] = [
    "format", "rename", "list", "count", "sort", "template", "lint",
    "typo", "indent", "spacing", "comment", "docstring", "readme",
    "changelog", "version bump", "move file", "copy", "delete",
    "boilerplate", "stub", "placeholder", "simple",
]

# Task type detection keywords
TASK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "reasoning": [
        "why", "analyze", "evaluate", "compare", "trade-off", "decision",
        "should we", "pros and cons", "architecture", "design", "strategy",
    ],
    "coding": [
        "implement", "code", "function", "class", "method", "endpoint",
        "test", "fix", "bug", "refactor", "write", "create file",
    ],
    "formatting": [
        "format", "markdown", "table", "list", "template", "convert",
        "pretty print", "restructure text", "organize",
    ],
    "summarization": [
        "summarize", "summary", "digest", "tl;dr", "key points",
        "consolidate", "recap",
    ],
    "embedding": [
        "embed", "vector", "similarity", "semantic search", "index",
    ],
}


class Complexity:
    """Niveaux de complexité de tâche."""
    TRIVIAL = "trivial"
    STANDARD = "standard"
    COMPLEX = "complex"
    EXPERT = "expert"

    COST_MULTIPLIER: ClassVar[dict[str, float]] = {
        "trivial": 0.1,
        "standard": 0.5,
        "complex": 1.0,
        "expert": 1.5,
    }


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class ModelSpec:
    """Spécification d'un modèle LLM."""
    id: str
    provider: str
    api: str = "copilot"  # copilot | direct | ollama | openrouter
    cost_per_1m_tokens: float = 0.0
    max_tokens: int = 200_000
    capabilities: list[str] = field(default_factory=list)

    @property
    def cost_label(self) -> str:
        if self.cost_per_1m_tokens == 0:
            return "FREE (local)"
        elif self.cost_per_1m_tokens < 1.0:
            return f"${self.cost_per_1m_tokens}/M — CHEAP"
        elif self.cost_per_1m_tokens < 5.0:
            return f"${self.cost_per_1m_tokens}/M — MODERATE"
        else:
            return f"${self.cost_per_1m_tokens}/M — PREMIUM"


@dataclass
class TaskClassification:
    """Résultat de la classification d'une tâche."""
    complexity: str
    task_type: str
    confidence: float
    indicators_matched: list[str] = field(default_factory=list)
    prompt_length: int = 0
    suggested_model: str = ""


@dataclass
class RoutingRule:
    """Règle de routing : match → modèle."""
    match_agent: str | None = None
    match_task_type: str | None = None
    match_complexity: str | None = None
    match_keywords: list[str] = field(default_factory=list)
    model: str = ""
    fallback: str = ""


@dataclass
class RoutingDecision:
    """Décision finale de routing."""
    agent: str
    prompt_summary: str
    classification: TaskClassification
    selected_model: str
    fallback_model: str
    rule_matched: str
    estimated_cost: float = 0.0


@dataclass
class UsageStat:
    """Stat d'utilisation d'un modèle."""
    model: str
    request_count: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    avg_complexity: str = "standard"


# ── Default Models ───────────────────────────────────────────────────────────

DEFAULT_MODELS: list[ModelSpec] = [
    ModelSpec(
        id="claude-opus",
        provider="anthropic",
        api="copilot",
        cost_per_1m_tokens=15.0,
        max_tokens=200_000,
        capabilities=["reasoning", "architecture", "complex-analysis", "coding"],
    ),
    ModelSpec(
        id="claude-sonnet",
        provider="anthropic",
        api="copilot",
        cost_per_1m_tokens=3.0,
        max_tokens=200_000,
        capabilities=["coding", "review", "general", "reasoning"],
    ),
    ModelSpec(
        id="claude-haiku",
        provider="anthropic",
        api="copilot",
        cost_per_1m_tokens=0.25,
        max_tokens=200_000,
        capabilities=["formatting", "simple-qa", "summarization"],
    ),
    ModelSpec(
        id="gpt-4o",
        provider="openai",
        api="copilot",
        cost_per_1m_tokens=2.5,
        max_tokens=128_000,
        capabilities=["coding", "reasoning", "general"],
    ),
    ModelSpec(
        id="gpt-4o-mini",
        provider="openai",
        api="copilot",
        cost_per_1m_tokens=0.15,
        max_tokens=128_000,
        capabilities=["coding", "formatting", "simple-qa"],
    ),
    ModelSpec(
        id="deepseek-coder",
        provider="deepseek",
        api="ollama",
        cost_per_1m_tokens=0.0,
        max_tokens=128_000,
        capabilities=["coding", "refactoring"],
    ),
    ModelSpec(
        id="nomic-embed",
        provider="nomic",
        api="ollama",
        cost_per_1m_tokens=0.0,
        max_tokens=8_192,
        capabilities=["embedding"],
    ),
]


# ── Task Classifier ─────────────────────────────────────────────────────────


class TaskClassifier:
    """
    Classifie la complexité et le type d'une tâche par heuristiques.

    Pas de ML — analyse par mots-clés pondérés, longueur du prompt,
    et contexte agent. Overridable dans project-context.yaml.
    """

    def __init__(
        self,
        custom_expert: list[str] | None = None,
        custom_complex: list[str] | None = None,
        custom_standard: list[str] | None = None,
        custom_trivial: list[str] | None = None,
    ):
        self.expert = EXPERT_INDICATORS + (custom_expert or [])
        self.complex = COMPLEX_INDICATORS + (custom_complex or [])
        self.standard = STANDARD_INDICATORS + (custom_standard or [])
        self.trivial = TRIVIAL_INDICATORS + (custom_trivial or [])

    def classify(self, prompt: str, agent_id: str = "") -> TaskClassification:
        """Classifie une requête en complexité et type de tâche."""
        lower = prompt.lower()
        prompt_len = len(prompt)

        # Score par niveau
        scores: dict[str, float] = {
            Complexity.EXPERT: 0.0,
            Complexity.COMPLEX: 0.0,
            Complexity.STANDARD: 0.0,
            Complexity.TRIVIAL: 0.0,
        }
        matched: list[str] = []

        for keyword in self.expert:
            if keyword in lower:
                scores[Complexity.EXPERT] += 2.0
                matched.append(f"expert:{keyword}")

        for keyword in self.complex:
            if keyword in lower:
                scores[Complexity.COMPLEX] += 1.5
                matched.append(f"complex:{keyword}")

        for keyword in self.standard:
            if keyword in lower:
                scores[Complexity.STANDARD] += 1.0
                matched.append(f"standard:{keyword}")

        for keyword in self.trivial:
            if keyword in lower:
                scores[Complexity.TRIVIAL] += 1.5
                matched.append(f"trivial:{keyword}")

        # Boost par longueur de prompt
        if prompt_len > 2000:
            scores[Complexity.COMPLEX] += 1.0
        elif prompt_len > 5000:
            scores[Complexity.EXPERT] += 1.0
        elif prompt_len < 100:
            scores[Complexity.TRIVIAL] += 0.5

        # Boost par agent — certains agents impliquent de la complexité
        agent_boost: dict[str, str] = {
            "architect": Complexity.COMPLEX,
            "analyst": Complexity.COMPLEX,
            "pm": Complexity.STANDARD,
            "dev": Complexity.STANDARD,
            "qa": Complexity.STANDARD,
            "tech-writer": Complexity.TRIVIAL,
        }
        if agent_id in agent_boost:
            scores[agent_boost[agent_id]] += 1.0

        # Déterminer le gagnant
        if not matched and sum(scores.values()) == 0:
            complexity = Complexity.STANDARD
            confidence = 0.3
        else:
            complexity = max(scores, key=scores.get)  # type: ignore[arg-type]
            total = sum(scores.values())
            confidence = round(scores[complexity] / total, 2) if total > 0 else 0.5

        # Classifier le type de tâche
        task_type = self._classify_task_type(lower)

        return TaskClassification(
            complexity=complexity,
            task_type=task_type,
            confidence=confidence,
            indicators_matched=matched[:10],  # Limit for readability
            prompt_length=prompt_len,
        )

    def _classify_task_type(self, lower_prompt: str) -> str:
        """Classifie le type de tâche (reasoning, coding, formatting, etc)."""
        type_scores: dict[str, float] = {}
        for task_type, keywords in TASK_TYPE_KEYWORDS.items():
            score = sum(1.0 for kw in keywords if kw in lower_prompt)
            if score > 0:
                type_scores[task_type] = score

        if not type_scores:
            return "general"
        return max(type_scores, key=type_scores.get)  # type: ignore[arg-type]


# ── LLM Router ──────────────────────────────────────────────────────────────


class LLMRouter:
    """
    Routeur LLM intelligent — dirige chaque requête vers le modèle optimal.

    Ordre de résolution :
    1. Rules par mots-clés explicites (match.keywords)
    2. Rules par agent explicites (match.agent)
    3. Rules par type de tâche (match.task_type)
    4. Rules par complexité (match.complexity)
    5. Modèle par défaut
    6. Fallback chain

    Configuration dans project-context.yaml > llm_router.
    """

    def __init__(
        self,
        models: list[ModelSpec] | None = None,
        rules: list[RoutingRule] | None = None,
        default_model: str = "claude-sonnet",
        classifier: TaskClassifier | None = None,
        stats_file: Path | None = None,
    ):
        self.models = {m.id: m for m in (models or DEFAULT_MODELS)}
        self.rules = rules or []
        self.default_model = default_model
        self.classifier = classifier or TaskClassifier()
        self.stats_file = stats_file

    def route(self, prompt: str, agent_id: str = "") -> RoutingDecision:
        """Route une requête vers le modèle optimal."""
        classification = self.classifier.classify(prompt, agent_id)
        prompt_lower = prompt.lower()

        # 1. Explicit keyword rule
        for rule in self.rules:
            matched_keyword = next(
                (
                    keyword for keyword in rule.match_keywords
                    if keyword and keyword.lower() in prompt_lower
                ),
                None,
            )
            if matched_keyword:
                model_id = rule.model
                fallback = rule.fallback or self.default_model
                matched_rule = f"keywords:{matched_keyword}"
                return self._build_decision(
                    agent_id, prompt, classification, model_id, fallback, matched_rule,
                )

        # 2. Explicit agent rule
        for rule in self.rules:
            if rule.match_agent and rule.match_agent == agent_id:
                model_id = rule.model
                fallback = rule.fallback or self.default_model
                matched_rule = f"agent:{agent_id}"
                return self._build_decision(
                    agent_id, prompt, classification, model_id, fallback, matched_rule,
                )

        # 3. Task type rule
        for rule in self.rules:
            if rule.match_task_type and rule.match_task_type == classification.task_type:
                model_id = rule.model
                fallback = rule.fallback or self.default_model
                matched_rule = f"task_type:{classification.task_type}"
                return self._build_decision(
                    agent_id, prompt, classification, model_id, fallback, matched_rule,
                )

        # 4. Complexity rule
        for rule in self.rules:
            if rule.match_complexity and rule.match_complexity == classification.complexity:
                model_id = rule.model
                fallback = rule.fallback or self.default_model
                matched_rule = f"complexity:{classification.complexity}"
                return self._build_decision(
                    agent_id, prompt, classification, model_id, fallback, matched_rule,
                )

        # 4. Capability-based selection
        model_id = self._select_by_capability(classification)
        matched_rule = f"capability:{classification.task_type}/{classification.complexity}"
        fallback = self.default_model

        return self._build_decision(
            agent_id, prompt, classification, model_id, fallback, matched_rule,
        )

    def _select_by_capability(self, classification: TaskClassification) -> str:
        """Sélectionne le meilleur modèle par capabilities et coût."""
        task_type = classification.task_type
        complexity = classification.complexity

        # Filtrer les modèles qui supportent le task_type
        candidates = [
            m for m in self.models.values()
            if task_type in m.capabilities or "general" in m.capabilities
        ]

        if not candidates:
            candidates = list(self.models.values())

        # Trier par coût
        candidates.sort(key=lambda m: m.cost_per_1m_tokens)

        # Pour les tâches complexes/expert, préférer les modèles premium
        if complexity in (Complexity.EXPERT, Complexity.COMPLEX):
            candidates.sort(key=lambda m: m.cost_per_1m_tokens, reverse=True)
            return candidates[0].id if candidates else self.default_model

        # Pour les tâches triviales, préférer les modèles cheap
        if complexity == Complexity.TRIVIAL:
            return candidates[0].id if candidates else self.default_model

        # Standard : milieu de gamme
        mid = len(candidates) // 2
        return candidates[mid].id if candidates else self.default_model

    def _build_decision(
        self,
        agent_id: str,
        prompt: str,
        classification: TaskClassification,
        model_id: str,
        fallback: str,
        rule: str,
    ) -> RoutingDecision:
        """Construit la décision de routing finale."""
        # Vérifier que le modèle existe
        if model_id not in self.models:
            model_id = self.default_model
        if fallback not in self.models:
            fallback = self.default_model

        model = self.models.get(model_id)
        estimated_tokens = len(prompt) // 4  # approximation grossière
        estimated_cost = 0.0
        if model:
            estimated_cost = (estimated_tokens / 1_000_000) * model.cost_per_1m_tokens

        classification.suggested_model = model_id

        # Tronquer le résumé du prompt
        summary = prompt[:120].replace("\n", " ").strip()
        if len(prompt) > 120:
            summary += "..."

        decision = RoutingDecision(
            agent=agent_id or "unknown",
            prompt_summary=summary,
            classification=classification,
            selected_model=model_id,
            fallback_model=fallback,
            rule_matched=rule,
            estimated_cost=estimated_cost,
        )

        # Log stats
        self._log_usage(decision)

        return decision

    def _log_usage(self, decision: RoutingDecision) -> None:
        """Log l'utilisation dans le fichier stats."""
        if not self.stats_file:
            return
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "agent": decision.agent,
            "model": decision.selected_model,
            "selected_model": decision.selected_model,
            "fallback_model": decision.fallback_model,
            "rule_matched": decision.rule_matched,
            "complexity": decision.classification.complexity,
            "task_type": decision.classification.task_type,
            "confidence": decision.classification.confidence,
            "estimated_cost": decision.estimated_cost,
            "prompt_tokens_est": decision.classification.prompt_length // 4,
            "prompt_summary": decision.prompt_summary,
        }
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    def _read_usage_entries(self) -> list[dict[str, object]]:
        """Lit les entrées brutes de télémétrie depuis le fichier de stats."""
        if not self.stats_file or not self.stats_file.exists():
            return []

        entries: list[dict[str, object]] = []
        try:
            with open(self.stats_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    if isinstance(entry, dict):
                        entries.append(entry)
        except (OSError, json.JSONDecodeError) as _exc:
            _log.debug("OSError, json.JSONDecodeError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

        return entries

    def get_stats(self) -> list[UsageStat]:
        """Lit et agrège les stats d'utilisation."""
        model_stats: dict[str, UsageStat] = {}
        try:
            for entry in self._read_usage_entries():
                    model = entry.get("model", "unknown")
                    if not isinstance(model, str):
                        model = "unknown"
                    if model not in model_stats:
                        model_stats[model] = UsageStat(model=model)
                    stat = model_stats[model]
                    stat.request_count += 1
                    tokens = entry.get("prompt_tokens_est", 0)
                    if isinstance(tokens, int):
                        stat.total_tokens += tokens
                    cost = entry.get("estimated_cost", 0.0)
                    if isinstance(cost, int | float):
                        stat.estimated_cost += float(cost)
        except TypeError as _exc:
            _log.debug("TypeError suppressed while aggregating router stats: %s", _exc)
            # Silent exception — add logging when investigating issues

        return sorted(model_stats.values(), key=lambda s: s.request_count, reverse=True)

    def get_recent_decisions(self, limit: int = 5) -> list[dict[str, object]]:
        """Retourne les dernières décisions de routing, de la plus récente à la plus ancienne."""
        if limit <= 0:
            return []
        entries = self._read_usage_entries()
        return list(reversed(entries[-limit:]))

    def get_policy_snapshot(self) -> dict[str, object]:
        """Expose la politique active du routeur pour les dashboards et audits."""
        return {
            "default_model": self.default_model,
            "model_count": len(self.models),
            "rule_count": len(self.rules),
            "keyword_rule_count": sum(1 for rule in self.rules if rule.match_keywords),
            "agent_rule_count": sum(1 for rule in self.rules if rule.match_agent),
            "task_type_rule_count": sum(1 for rule in self.rules if rule.match_task_type),
            "complexity_rule_count": sum(1 for rule in self.rules if rule.match_complexity),
            "stats_file": str(self.stats_file) if self.stats_file else None,
        }

    def get_telemetry_summary(self) -> dict[str, object]:
        """Agrège une vue compacte de télémétrie pour le pilotage."""
        entries = self._read_usage_entries()
        if not entries:
            return {
                "total_requests": 0,
                "total_prompt_tokens": 0,
                "avg_prompt_tokens": 0.0,
                "total_estimated_cost": 0.0,
                "last_routed_at": None,
                "alternate_path_count": 0,
                "rule_distribution": {},
                "top_agents": {},
            }

        total_tokens = 0
        total_cost = 0.0
        alternate_path_count = 0
        rule_distribution: dict[str, int] = {}
        agent_counts: dict[str, int] = {}

        for entry in entries:
            tokens = entry.get("prompt_tokens_est", 0)
            if isinstance(tokens, int):
                total_tokens += tokens

            cost = entry.get("estimated_cost", 0.0)
            if isinstance(cost, int | float):
                total_cost += float(cost)

            selected_model = entry.get("model")
            fallback_model = entry.get("fallback_model")
            if (
                isinstance(selected_model, str)
                and isinstance(fallback_model, str)
                and fallback_model
                and fallback_model != selected_model
            ):
                alternate_path_count += 1

            rule_matched = entry.get("rule_matched", "")
            rule_family = rule_matched.split(":", 1)[0] if isinstance(rule_matched, str) and rule_matched else "unknown"
            rule_distribution[rule_family] = rule_distribution.get(rule_family, 0) + 1

            agent = entry.get("agent", "unknown")
            if not isinstance(agent, str) or not agent:
                agent = "unknown"
            agent_counts[agent] = agent_counts.get(agent, 0) + 1

        top_agents = dict(sorted(agent_counts.items(), key=lambda item: (-item[1], item[0]))[:5])

        return {
            "total_requests": len(entries),
            "total_prompt_tokens": total_tokens,
            "avg_prompt_tokens": round(total_tokens / len(entries), 1),
            "total_estimated_cost": round(total_cost, 6),
            "last_routed_at": entries[-1].get("timestamp"),
            "alternate_path_count": alternate_path_count,
            "rule_distribution": dict(sorted(rule_distribution.items())),
            "top_agents": top_agents,
        }

    def get_recommendations(self) -> list[str]:
        """Analyse les stats et produit des recommandations d'optimisation."""
        stats = self.get_stats()
        if not stats:
            return ["Aucune donnée de routing. Commencez par utiliser le router."]

        recs: list[str] = []
        total_cost = sum(s.estimated_cost for s in stats)
        total_requests = sum(s.request_count for s in stats)

        # Chercher les modèles premium sur-utilisés
        for stat in stats:
            model = self.models.get(stat.model)
            if model and model.cost_per_1m_tokens >= 10.0 and stat.request_count > total_requests * 0.3:
                recs.append(
                    f"⚠️  {stat.model} (${model.cost_per_1m_tokens}/M) gère {stat.request_count}/{total_requests} "
                    f"requêtes ({stat.request_count / total_requests * 100:.0f}%). "
                    f"Envisager de router les tâches simples vers un modèle moins cher."
                )

        # Chercher les modèles gratuits sous-utilisés
        for model_id, model in self.models.items():
            if model.cost_per_1m_tokens == 0.0:
                stat = next((s for s in stats if s.model == model_id), None)
                if not stat or stat.request_count < total_requests * 0.1:
                    recs.append(
                        f"💡 {model_id} est gratuit (local) mais sous-utilisé. "
                        f"Configurer des rules pour les tâches triviales/formatting."
                    )

        if total_cost > 0:
            recs.append(f"📊 Coût estimé total : ${total_cost:.4f} sur {total_requests} requêtes.")

        if not recs:
            recs.append("✅ Routing semble optimal. Pas de recommandation.")

        return recs


# ── Config Loading ──────────────────────────────────────────────────────────


def load_config(project_root: Path) -> dict:
    """Charge la config llm_router depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        # Fallback: parse YAML basique (stdlib)
        return _load_yaml_basic(project_root)

    for candidate in [
        project_root / "project-context.yaml",
        project_root / "grimoire.yaml",
    ]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("llm_router", {})
    return {}


def _load_yaml_basic(project_root: Path) -> dict:
    """Parse basique YAML sans dépendances (pour llm_router seulement)."""
    for candidate in [
        project_root / "project-context.yaml",
        project_root / "grimoire.yaml",
    ]:
        if candidate.exists():
            try:
                content = candidate.read_text(encoding="utf-8")
                # Chercher le bloc llm_router
                match = re.search(r"^llm_router:\s*\n((?:  .+\n)*)", content, re.MULTILINE)
                if match:
                    return {"_raw": match.group(1)}  # Signal qu'on a trouvé le bloc
            except OSError as _exc:
                _log.debug("OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues
    return {}


def _parse_model_spec(raw_model: dict) -> ModelSpec:
    """Construit un ModelSpec depuis un bloc YAML."""
    return ModelSpec(
        id=raw_model.get("id", "unknown"),
        provider=raw_model.get("provider", "unknown"),
        api=raw_model.get("api", "copilot"),
        cost_per_1m_tokens=float(raw_model.get("cost_per_1m_tokens", 0.0)),
        max_tokens=int(raw_model.get("max_tokens", 200_000)),
        capabilities=raw_model.get("capabilities", []),
    )


def _dedupe_strings(values: list[str]) -> list[str]:
    """Déduplique une liste de chaînes en préservant l'ordre."""
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _infer_model_spec(model_id: str) -> ModelSpec:
    """Infère une spec minimale quand le profil canonique ne fournit qu'un ID."""
    lower = model_id.lower()
    provider = "unknown"
    api = "copilot"
    max_tokens = 200_000

    if lower.startswith("gpt"):
        provider = "openai"
        max_tokens = 128_000
    elif lower.startswith("claude"):
        provider = "anthropic"
    elif lower.startswith("gemini"):
        provider = "google"
        api = "direct"
        max_tokens = 1_000_000
    elif "deepseek" in lower:
        provider = "deepseek"
        api = "ollama"
        max_tokens = 128_000
    elif "nomic" in lower:
        provider = "nomic"
        api = "ollama"
        max_tokens = 8_192

    if "embed" in lower:
        return ModelSpec(
            id=model_id,
            provider=provider,
            api=api,
            cost_per_1m_tokens=0.0,
            max_tokens=max_tokens,
            capabilities=["embedding"],
        )

    if any(tag in lower for tag in ("haiku", "mini", "flash")):
        cost = 0.25
        capabilities = ["formatting", "simple-qa", "summarization", "general"]
    elif "codex" in lower or "coder" in lower:
        cost = 3.0
        capabilities = ["coding", "review", "refactoring", "reasoning"]
    elif any(tag in lower for tag in ("opus", "pro")) or "5.4" in lower:
        cost = 15.0
        capabilities = ["reasoning", "architecture", "complex-analysis", "general"]
    else:
        cost = 3.0
        capabilities = ["coding", "review", "general", "reasoning"]

    return ModelSpec(
        id=model_id,
        provider=provider,
        api=api,
        cost_per_1m_tokens=cost,
        max_tokens=max_tokens,
        capabilities=capabilities,
    )


def _profile_candidates(profile: dict) -> list[str]:
    """Retourne les IDs candidats d'un profil, dans l'ordre."""
    candidates: list[str] = []
    primary = profile.get("primary")
    if isinstance(primary, str) and primary and primary.lower() != "auto":
        candidates.append(primary)

    preferred_models = profile.get("preferred_models", [])
    if isinstance(preferred_models, list):
        for model_id in preferred_models:
            if isinstance(model_id, str) and model_id and model_id.lower() != "auto":
                candidates.append(model_id)

    return _dedupe_strings(candidates)


def _resolve_profile_models(
    profile_name: str,
    profiles: dict[str, dict],
    models_by_id: dict[str, ModelSpec],
    default_model: str,
) -> tuple[str, str]:
    """Résout le couple modèle/fallback d'un profil canonique."""
    profile = profiles.get(profile_name, {})
    if not isinstance(profile, dict):
        return default_model, default_model

    candidates = _profile_candidates(profile)
    if not candidates:
        return default_model, default_model

    for model_id in candidates:
        models_by_id.setdefault(model_id, _infer_model_spec(model_id))

    primary = candidates[0]
    fallback = next((model_id for model_id in candidates[1:] if model_id != primary), default_model)
    if fallback and fallback not in models_by_id:
        models_by_id[fallback] = _infer_model_spec(fallback)
    return primary, fallback or primary


def _resolve_default_model(config: dict, profiles: dict[str, dict], models_by_id: dict[str, ModelSpec]) -> str:
    """Choisit le modèle par défaut en restant compatible avec le canon runtime."""
    explicit_default = config.get("default_model")
    if isinstance(explicit_default, str) and explicit_default and explicit_default.lower() != "auto":
        models_by_id.setdefault(explicit_default, _infer_model_spec(explicit_default))
        return explicit_default

    for profile_name in ("general_code", "writing_structured", "deep_reasoning", "fast_iter"):
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            continue
        candidates = _profile_candidates(profile)
        if candidates:
            models_by_id.setdefault(candidates[0], _infer_model_spec(candidates[0]))
            return candidates[0]

    return "claude-sonnet"


def _parse_legacy_rules(raw_rules: list[dict], default_model: str) -> list[RoutingRule]:
    """Parse les rules legacy et les éventuels mots-clés explicites."""
    rules: list[RoutingRule] = []
    for raw_rule in raw_rules:
        if not isinstance(raw_rule, dict):
            continue
        match_block = raw_rule.get("match", {})
        keywords = match_block.get("keywords", []) if isinstance(match_block, dict) else []
        rules.append(RoutingRule(
            match_agent=match_block.get("agent") if isinstance(match_block, dict) else None,
            match_task_type=match_block.get("task_type") if isinstance(match_block, dict) else None,
            match_complexity=match_block.get("complexity") if isinstance(match_block, dict) else None,
            match_keywords=[keyword for keyword in keywords if isinstance(keyword, str)],
            model=raw_rule.get("model", default_model),
            fallback=raw_rule.get("fallback", default_model),
        ))
    return rules


def build_router_from_config(project_root: Path) -> LLMRouter:
    """Construit un LLMRouter depuis la config du projet."""
    config = load_config(project_root)
    stats_file = project_root / "_grimoire-output" / ".router-stats.jsonl"

    models: list[ModelSpec] = []
    rules: list[RoutingRule] = []
    profiles = config.get("profiles", {})
    profiles_dict = profiles if isinstance(profiles, dict) else {}

    if any(key in config for key in ("profiles", "routing_defaults", "task_overrides")):
        models_by_id = {model.id: model for model in DEFAULT_MODELS}

        for raw_model in config.get("models", []):
            if isinstance(raw_model, dict):
                spec = _parse_model_spec(raw_model)
                models_by_id[spec.id] = spec

        for profile in profiles_dict.values():
            if not isinstance(profile, dict):
                continue
            for model_id in _profile_candidates(profile):
                models_by_id.setdefault(model_id, _infer_model_spec(model_id))

        default = _resolve_default_model(config, profiles_dict, models_by_id)

        for override in config.get("task_overrides", []):
            if not isinstance(override, dict):
                continue
            profile_name = override.get("profile")
            if not isinstance(profile_name, str):
                continue
            model_id, fallback_id = _resolve_profile_models(
                profile_name, profiles_dict, models_by_id, default,
            )
            keywords = [
                task for task in override.get("tasks", [])
                if isinstance(task, str) and task
            ]
            if keywords:
                rules.append(RoutingRule(
                    match_keywords=keywords,
                    model=model_id,
                    fallback=fallback_id,
                ))

        routing_defaults = config.get("routing_defaults", {})
        if isinstance(routing_defaults, dict):
            for agent_id, profile_name in routing_defaults.items():
                if not isinstance(agent_id, str) or not isinstance(profile_name, str):
                    continue
                model_id, fallback_id = _resolve_profile_models(
                    profile_name, profiles_dict, models_by_id, default,
                )
                rules.append(RoutingRule(
                    match_agent=agent_id,
                    model=model_id,
                    fallback=fallback_id,
                ))

        rules.extend(_parse_legacy_rules(config.get("rules", []), default))
        models = list(models_by_id.values())
    else:
        default = config.get("default_model", "claude-sonnet")

        for raw_model in config.get("models", []):
            if isinstance(raw_model, dict):
                models.append(_parse_model_spec(raw_model))

        rules = _parse_legacy_rules(config.get("rules", []), default)

    # Custom classifier keywords
    custom_kw = config.get("custom_keywords", {})
    classifier = TaskClassifier(
        custom_expert=custom_kw.get("expert"),
        custom_complex=custom_kw.get("complex"),
        custom_standard=custom_kw.get("standard"),
        custom_trivial=custom_kw.get("trivial"),
    )

    return LLMRouter(
        models=models or None,  # None → use defaults
        rules=rules,
        default_model=default,
        classifier=classifier,
        stats_file=stats_file,
    )


# ── CLI ─────────────────────────────────────────────────────────────────────


def _print_classification(tc: TaskClassification) -> None:
    """Affiche une classification joliment."""
    complexity_icons = {
        "trivial": "🟢",
        "standard": "🔵",
        "complex": "🟡",
        "expert": "🔴",
    }
    icon = complexity_icons.get(tc.complexity, "⚪")
    print(f"\n  {icon} Complexity : {tc.complexity.upper()} (confidence: {tc.confidence})")
    print(f"  📋 Task type  : {tc.task_type}")
    print(f"  📏 Prompt len : {tc.prompt_length} chars (~{tc.prompt_length // 4} tokens)")
    if tc.indicators_matched:
        print(f"  🔍 Indicators : {', '.join(tc.indicators_matched[:5])}")
    if tc.suggested_model:
        print(f"  🎯 Model      : {tc.suggested_model}")


def _print_decision(dec: RoutingDecision) -> None:
    """Affiche une décision de routing joliment."""
    print(f"\n{'=' * 60}")
    print("  🤖 LLM ROUTER — Routing Decision")
    print(f"{'=' * 60}")
    print(f"  Agent   : {dec.agent}")
    print(f"  Prompt  : {dec.prompt_summary}")
    _print_classification(dec.classification)
    print(f"\n  ➡️  Selected : {dec.selected_model}")
    print(f"  🔄 Fallback : {dec.fallback_model}")
    print(f"  📐 Rule     : {dec.rule_matched}")
    if dec.estimated_cost > 0:
        print(f"  💰 Est.cost : ${dec.estimated_cost:.6f}")
    print(f"{'=' * 60}\n")


def _print_models(router: LLMRouter) -> None:
    """Affiche la liste des modèles configurés."""
    print(f"\n  📦 Modèles configurés ({len(router.models)})")
    print(f"  {'─' * 50}")
    for m in sorted(router.models.values(), key=lambda x: x.cost_per_1m_tokens, reverse=True):
        caps = ", ".join(m.capabilities[:4]) if m.capabilities else "general"
        print(f"  {m.id:20s} │ {m.cost_label:28s} │ {caps}")
    print()


def _print_stats(router: LLMRouter, recommend: bool = False) -> None:
    """Affiche les stats d'utilisation."""
    stats = router.get_stats()
    if not stats:
        print("\n  📊 Aucune stat de routing encore. Commencez par utiliser 'route'.\n")
        return

    print("\n  📊 Statistiques d'utilisation LLM Router")
    print(f"  {'─' * 55}")
    print(f"  {'Modèle':20s} │ {'Requêtes':>10s} │ {'Tokens':>10s} │ {'Coût':>10s}")
    print(f"  {'─' * 55}")
    for s in stats:
        print(f"  {s.model:20s} │ {s.request_count:>10d} │ {s.total_tokens:>10d} │ ${s.estimated_cost:>9.4f}")
    total_cost = sum(s.estimated_cost for s in stats)
    total_req = sum(s.request_count for s in stats)
    print(f"  {'─' * 55}")
    print(f"  {'TOTAL':20s} │ {total_req:>10d} │ {'':>10s} │ ${total_cost:>9.4f}")
    print()

    if recommend:
        recs = router.get_recommendations()
        print("  💡 Recommandations")
        print(f"  {'─' * 55}")
        for r in recs:
            print(f"  {r}")
        print()


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="LLM Router — Route les requêtes agents vers le modèle LLM optimal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, default=Path(),
        help="Racine du projet (défaut: .)",
    )
    parser.add_argument("--version", action="version", version=f"llm-router {LLM_ROUTER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # route
    route_p = sub.add_parser("route", help="Route une requête vers le meilleur modèle")
    route_p.add_argument("--agent", required=True, help="ID de l'agent")
    route_p.add_argument("--prompt", required=True, help="Prompt ou description de la tâche")
    route_p.add_argument("--json", action="store_true", help="Output JSON")

    # classify
    classify_p = sub.add_parser("classify", help="Classifie la complexité d'une requête")
    classify_p.add_argument("--prompt", required=True, help="Prompt à classifier")
    classify_p.add_argument("--agent", default="", help="Agent ID (optionnel, pour boost)")
    classify_p.add_argument("--json", action="store_true", help="Output JSON")

    # models
    sub.add_parser("models", help="Liste les modèles configurés")

    # stats
    stats_p = sub.add_parser("stats", help="Statistiques d'utilisation")
    stats_p.add_argument("--recommend", action="store_true", help="Afficher les recommandations")
    stats_p.add_argument("--json", action="store_true", help="Output JSON")

    # config
    sub.add_parser("config", help="Affiche la configuration active")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    router = build_router_from_config(args.project_root)

    if args.command == "route":
        decision = router.route(args.prompt, args.agent)
        if getattr(args, "json", False):
            print(json.dumps(asdict(decision), ensure_ascii=False, indent=2))
        else:
            _print_decision(decision)

    elif args.command == "classify":
        classifier = router.classifier
        result = classifier.classify(args.prompt, args.agent)
        if getattr(args, "json", False):
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        else:
            print("\n  🧠 Task Classification")
            _print_classification(result)
            print()

    elif args.command == "models":
        _print_models(router)

    elif args.command == "stats":
        if getattr(args, "json", False):
            stats = router.get_stats()
            print(json.dumps([asdict(s) for s in stats], ensure_ascii=False, indent=2))
        else:
            _print_stats(router, recommend=getattr(args, "recommend", False))

    elif args.command == "config":
        config = load_config(args.project_root)
        print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
