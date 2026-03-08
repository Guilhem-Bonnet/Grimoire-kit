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

_log = logging.getLogger("grimoire.llm_router")

# ── Version ──────────────────────────────────────────────────────────────────

LLM_ROUTER_VERSION = "1.0.0"

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

    COST_MULTIPLIER = {
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
    1. Rules par agent explicites (match.agent)
    2. Rules par type de tâche (match.task_type)
    3. Rules par complexité (match.complexity)
    4. Modèle par défaut
    5. Fallback chain

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

        # 1. Explicit agent rule
        for rule in self.rules:
            if rule.match_agent and rule.match_agent == agent_id:
                model_id = rule.model
                fallback = rule.fallback or self.default_model
                matched_rule = f"agent:{agent_id}"
                return self._build_decision(
                    agent_id, prompt, classification, model_id, fallback, matched_rule,
                )

        # 2. Task type rule
        for rule in self.rules:
            if rule.match_task_type and rule.match_task_type == classification.task_type:
                model_id = rule.model
                fallback = rule.fallback or self.default_model
                matched_rule = f"task_type:{classification.task_type}"
                return self._build_decision(
                    agent_id, prompt, classification, model_id, fallback, matched_rule,
                )

        # 3. Complexity rule
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
            "complexity": decision.classification.complexity,
            "task_type": decision.classification.task_type,
            "estimated_cost": decision.estimated_cost,
            "prompt_tokens_est": decision.classification.prompt_length // 4,
        }
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    def get_stats(self) -> list[UsageStat]:
        """Lit et agrège les stats d'utilisation."""
        if not self.stats_file or not self.stats_file.exists():
            return []

        model_stats: dict[str, UsageStat] = {}
        try:
            with open(self.stats_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    model = entry.get("model", "unknown")
                    if model not in model_stats:
                        model_stats[model] = UsageStat(model=model)
                    stat = model_stats[model]
                    stat.request_count += 1
                    stat.total_tokens += entry.get("prompt_tokens_est", 0)
                    stat.estimated_cost += entry.get("estimated_cost", 0.0)
        except (OSError, json.JSONDecodeError) as _exc:
            _log.debug("OSError, json.JSONDecodeError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

        return sorted(model_stats.values(), key=lambda s: s.request_count, reverse=True)

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


def build_router_from_config(project_root: Path) -> LLMRouter:
    """Construit un LLMRouter depuis la config du projet."""
    config = load_config(project_root)
    stats_file = project_root / "_grimoire-output" / ".router-stats.jsonl"

    models: list[ModelSpec] = []
    rules: list[RoutingRule] = []
    default = config.get("default_model", "claude-sonnet")

    # Parser les modèles
    for m in config.get("models", []):
        models.append(ModelSpec(
            id=m.get("id", "unknown"),
            provider=m.get("provider", "unknown"),
            api=m.get("api", "copilot"),
            cost_per_1m_tokens=float(m.get("cost_per_1m_tokens", 0.0)),
            max_tokens=int(m.get("max_tokens", 200_000)),
            capabilities=m.get("capabilities", []),
        ))

    # Parser les rules
    for r in config.get("rules", []):
        match_block = r.get("match", {})
        rules.append(RoutingRule(
            match_agent=match_block.get("agent"),
            match_task_type=match_block.get("task_type"),
            match_complexity=match_block.get("complexity"),
            model=r.get("model", default),
            fallback=r.get("fallback", default),
        ))

    # Custom classifier keywords
    custom_kw = config.get("custom_keywords", {})
    classifier = TaskClassifier(
        custom_expert=custom_kw.get("expert"),
        custom_complex=custom_kw.get("complex"),
        custom_standard=custom_kw.get("standard"),
        custom_trivial=custom_kw.get("trivial"),
    )

    return LLMRouter(
        models=models if models else None,  # None → use defaults
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
        "--project-root", type=Path, default=Path("."),
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
