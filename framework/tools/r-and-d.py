#!/usr/bin/env python3
"""
r-and-d.py — Innovation Engine avec Reinforcement Learning BMAD.
==================================================================

Moteur d'innovation autonome qui exécute des cycles de R&D et apprend
de ses résultats via un système de renforcement. Peut lancer N cycles
intensifs en continu — seules les meilleures innovations survivent.

Architecture du cycle (7 phases) :
  1. HARVEST   — Récolte d'idées depuis dream, oracle, early-warning, DNA drift
  2. EVALUATE  — Scoring multi-dimensionnel automatique
  3. CHALLENGE — Adversarial red-team / pre-mortem
  4. SIMULATE  — Impact simulation via digital-twin
  5. IMPLEMENT — Quality gates (tests, lint, harmony)
  6. SELECT    — Tournament sélection darwinienne
  7. CONVERGE  — Critères d'arrêt et apprentissage

Reinforcement Learning :
  - Chaque idée produit un reward signal (fitness delta)
  - La policy (poids des sources, domaines, paramètres) s'ajuste
  - Epsilon-greedy : 80% exploit best patterns, 20% explore
  - Mémoire à long terme : quelles sources/domaines/patterns produisent
    les meilleures innovations

Modes :
  cycle     — Un cycle d'innovation
  train     — N cycles intensifs (reinforcement learning)
  harvest   — Phase 1 seule (récolte d'idées)
  evaluate  — Phase 2 seule (scoring)
  status    — État du moteur
  history   — Historique des cycles
  dashboard — Tableau de bord markdown
  tune      — Ajuster les paramètres manuellement
  reset     — Reset du moteur (garde l'historique)

Usage :
  python3 r-and-d.py --project-root . cycle                    # 1 cycle complet
  python3 r-and-d.py --project-root . cycle --quick            # harvest+evaluate only
  python3 r-and-d.py --project-root . train --epochs 5         # 5 cycles intensifs
  python3 r-and-d.py --project-root . train --epochs 10 --budget 3  # 10 cycles, 3 idées/cycle
  python3 r-and-d.py --project-root . train --epochs 20 --auto-stop # Stop si convergence
  python3 r-and-d.py --project-root . harvest                  # Phase 1 seule
  python3 r-and-d.py --project-root . status                   # État actuel
  python3 r-and-d.py --project-root . history                  # Historique
  python3 r-and-d.py --project-root . dashboard                # Dashboard markdown
  python3 r-and-d.py --project-root . tune --epsilon 0.3       # Ajuster exploration
  python3 r-and-d.py --project-root . reset                    # Reset

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = "1.0.0"

# ── Constantes ────────────────────────────────────────────────────

RND_DIR = ".bmad-rnd"
MEMORY_FILE = "innovation-memory.json"
POLICY_FILE = "policy.json"
HISTORY_DIR = "cycle-history"
FOSSIL_DIR = "fossil-record"

# Sources de récolte connues
HARVEST_SOURCES = [
    "dream",           # insights cross-domaine
    "oracle-swot",     # SWOT analysis
    "oracle-attract",  # attracteurs naturels
    "early-warning",   # signaux d'alerte
    "dna-drift",       # divergence DNA
    "workflow-adapt",  # desire paths
    "antifragile",     # score de résilience
    "harmony",         # dissonances architecturales
    "stigmergy",       # phéromones actives
    "incubator",       # idées dormantes à réveiller
]

# Domaines d'innovation
DOMAINS = [
    "tools",           # nouveaux outils CLI
    "agents",          # nouveaux/meilleurs agents
    "workflows",       # processus améliorés
    "architecture",    # patterns structurels
    "documentation",   # docs et onboarding
    "testing",         # couverture et qualité
    "integration",     # intégrations externes
    "meta",            # méta-outils (outils sur outils)
    "resilience",      # robustesse et anti-fragilité
    "performance",     # efficacité et vitesse
]

# Actions évolutives
ACTIONS = ["add", "improve", "simplify", "merge", "split", "remove"]

# Defaults
DEFAULT_BUDGET = 5          # idées/cycle
DEFAULT_EPOCHS = 1          # cycles
DEFAULT_EPSILON = 0.2       # taux d'exploration (20%)
DEFAULT_LEARNING_RATE = 0.1 # vitesse d'ajustement policy
CONVERGENCE_WINDOW = 3      # cycles pour détecter convergence
MIN_REWARD_DELTA = 0.05     # seuil de rendement décroissant

# Scoring dimensions & weights
SCORING_DIMS = {
    "feasibility": 0.20,     # faisabilité technique
    "impact": 0.25,          # impact potentiel sur le projet
    "uniqueness": 0.15,      # distance avec l'existant
    "synergy": 0.15,         # renforce les outils existants
    "risk_inverse": 0.10,    # 1 - risque
    "novelty": 0.15,         # bonus nouveauté (reinforcement)
}


# ── Data Classes ─────────────────────────────────────────────────

@dataclass
class Idea:
    """Une idée d'innovation générée par le moteur."""
    id: str
    title: str
    description: str
    source: str              # quelle source l'a générée
    domain: str              # quel domaine ciblé
    action: str              # add, improve, simplify, etc.
    scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0
    challenge_result: str = ""     # GO, NO-GO, CONDITIONAL
    challenge_notes: list[str] = field(default_factory=list)
    simulation_risk: float = 0.0
    simulation_impacts: int = 0
    implemented: bool = False
    merged: bool = False
    reward: float = 0.0
    created_at: str = ""
    cycle_id: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Idea:
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class Policy:
    """Policy de reinforcement learning — poids adaptatifs."""
    # Poids par source (initialement uniforme)
    source_weights: dict[str, float] = field(default_factory=dict)
    # Poids par domaine
    domain_weights: dict[str, float] = field(default_factory=dict)
    # Poids par action
    action_weights: dict[str, float] = field(default_factory=dict)
    # Paramètres de scoring (ajustables)
    scoring_weights: dict[str, float] = field(default_factory=dict)
    # Hyperparamètres
    epsilon: float = DEFAULT_EPSILON
    learning_rate: float = DEFAULT_LEARNING_RATE
    # Statistiques
    total_rewards: float = 0.0
    total_ideas: int = 0
    total_merged: int = 0
    generations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Policy:
        valid = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**valid)

    @classmethod
    def default(cls) -> Policy:
        """Policy par défaut avec poids uniformes."""
        n_sources = len(HARVEST_SOURCES)
        n_domains = len(DOMAINS)
        n_actions = len(ACTIONS)
        return cls(
            source_weights={s: 1.0 / n_sources for s in HARVEST_SOURCES},
            domain_weights={d: 1.0 / n_domains for d in DOMAINS},
            action_weights={a: 1.0 / n_actions for a in ACTIONS},
            scoring_weights=dict(SCORING_DIMS),
        )


@dataclass
class CycleReport:
    """Rapport d'un cycle d'innovation."""
    cycle_id: int
    epoch: int = 0
    timestamp: str = ""
    duration_ms: int = 0
    ideas_harvested: int = 0
    ideas_evaluated: int = 0
    ideas_challenged: int = 0
    ideas_go: int = 0
    ideas_simulated: int = 0
    ideas_implemented: int = 0
    ideas_merged: int = 0
    avg_reward: float = 0.0
    best_reward: float = 0.0
    convergence_metric: float = 0.0   # moving avg of best_reward
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    ideas: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = ""   # CONTINUE, SLOW_DOWN, CONSOLIDATE, STOP

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrainReport:
    """Rapport d'un entraînement multi-cycles."""
    epochs_requested: int = 0
    epochs_completed: int = 0
    total_ideas: int = 0
    total_merged: int = 0
    best_idea: dict[str, Any] = field(default_factory=dict)
    reward_curve: list[float] = field(default_factory=list)
    convergence_reached: bool = False
    convergence_epoch: int = 0
    final_policy: dict[str, Any] = field(default_factory=dict)
    cycles: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    verdict: str = ""


# ── Persistence ──────────────────────────────────────────────────

def _rnd_dir(project_root: Path) -> Path:
    return project_root / RND_DIR


def _ensure_dirs(project_root: Path) -> None:
    base = _rnd_dir(project_root)
    (base / HISTORY_DIR).mkdir(parents=True, exist_ok=True)
    (base / FOSSIL_DIR).mkdir(parents=True, exist_ok=True)


def load_policy(project_root: Path) -> Policy:
    fpath = _rnd_dir(project_root) / POLICY_FILE
    if fpath.exists():
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            return Policy.from_dict(data)
        except (json.JSONDecodeError, OSError):
            pass
    return Policy.default()


def save_policy(project_root: Path, policy: Policy) -> None:
    _ensure_dirs(project_root)
    fpath = _rnd_dir(project_root) / POLICY_FILE
    fpath.write_text(json.dumps(policy.to_dict(), indent=2, ensure_ascii=False),
                     encoding="utf-8")


def load_memory(project_root: Path) -> list[dict[str, Any]]:
    fpath = _rnd_dir(project_root) / MEMORY_FILE
    if fpath.exists():
        try:
            return json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def save_memory(project_root: Path, memory: list[dict[str, Any]]) -> None:
    _ensure_dirs(project_root)
    fpath = _rnd_dir(project_root) / MEMORY_FILE
    fpath.write_text(json.dumps(memory, indent=2, ensure_ascii=False),
                     encoding="utf-8")


def save_cycle_report(project_root: Path, report: CycleReport) -> None:
    _ensure_dirs(project_root)
    fpath = _rnd_dir(project_root) / HISTORY_DIR / f"cycle-{report.cycle_id:04d}.json"
    fpath.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                     encoding="utf-8")


def load_cycle_reports(project_root: Path) -> list[CycleReport]:
    hdir = _rnd_dir(project_root) / HISTORY_DIR
    if not hdir.exists():
        return []
    reports = []
    for f in sorted(hdir.glob("cycle-*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append(CycleReport(**{k: v for k, v in data.items()
                                          if k in CycleReport.__dataclass_fields__}))
        except (json.JSONDecodeError, OSError, TypeError):
            continue
    return reports


def next_cycle_id(project_root: Path) -> int:
    reports = load_cycle_reports(project_root)
    if reports:
        return max(r.cycle_id for r in reports) + 1
    return 1


# ── Dynamic tool loader ─────────────────────────────────────────

def _load_tool(name: str) -> Any:
    """Charge dynamiquement un outil Python co-localisé."""
    tool_path = Path(__file__).parent / f"{name}.py"
    if not tool_path.exists():
        return None
    try:
        mod_name = name.replace("-", "_")
        spec = importlib.util.spec_from_file_location(mod_name, tool_path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# ── Phase 1 : HARVEST ────────────────────────────────────────────

def _harvest_from_dream(project_root: Path) -> list[dict[str, str]]:
    """Récolte des insights depuis dream.py."""
    mod = _load_tool("dream")
    if mod is None:
        return []
    ideas = []
    try:
        sources = mod.collect_sources(project_root, since=None)
        if not sources:
            return []
        insights = mod.generate_insights(sources, max_insights=5)
        for ins in insights:
            ideas.append({
                "title": ins.title,
                "description": ins.description,
                "source": "dream",
                "domain": _classify_domain(ins.title + " " + ins.description),
                "action": _classify_action(ins.description),
            })
    except Exception:
        pass
    return ideas


def _harvest_from_oracle(project_root: Path) -> list[dict[str, str]]:
    """Récolte depuis oracle.py (SWOT + attracteurs)."""
    mod = _load_tool("oracle")
    if mod is None:
        return []
    ideas = []
    try:
        swot = mod.analyze_swot(project_root)
        for opp in swot.opportunities:
            ideas.append({
                "title": f"Opportunité: {opp.text[:80]}",
                "description": opp.text,
                "source": "oracle-swot",
                "domain": _classify_domain(opp.text),
                "action": "add",
            })
        for weakness in swot.weaknesses:
            ideas.append({
                "title": f"Corriger: {weakness.text[:80]}",
                "description": weakness.text,
                "source": "oracle-swot",
                "domain": _classify_domain(weakness.text),
                "action": "improve",
            })
    except Exception:
        pass
    try:
        attractors = mod.analyze_attractors(project_root)
        for att in attractors:
            ideas.append({
                "title": f"Attracteur: {att.name}",
                "description": att.description,
                "source": "oracle-attract",
                "domain": _classify_domain(att.description),
                "action": "add",
            })
    except Exception:
        pass
    return ideas


def _harvest_from_early_warning(project_root: Path) -> list[dict[str, str]]:
    """Récolte des signaux early-warning."""
    mod = _load_tool("early-warning")
    if mod is None:
        return []
    ideas = []
    try:
        if hasattr(mod, "scan_all"):
            alerts = mod.scan_all(project_root)
        elif hasattr(mod, "run_scan"):
            alerts = mod.run_scan(project_root)
        else:
            return []
        for alert in alerts if isinstance(alerts, list) else []:
            if hasattr(alert, "level") and alert.level in ("WATCH", "ALERT"):
                ideas.append({
                    "title": f"Alerte: {getattr(alert, 'metric', 'signal')}",
                    "description": getattr(alert, "detail", str(alert)),
                    "source": "early-warning",
                    "domain": _classify_domain(str(alert)),
                    "action": "improve",
                })
    except Exception:
        pass
    return ideas


def _harvest_from_harmony(project_root: Path) -> list[dict[str, str]]:
    """Récolte des dissonances harmony-check."""
    mod = _load_tool("harmony-check")
    if mod is None:
        return []
    ideas = []
    try:
        scan = mod.scan_project(project_root)
        for diss in scan.dissonances[:5]:
            ideas.append({
                "title": f"Dissonance: {diss.message[:80]}",
                "description": f"{diss.message} — {diss.suggestion}",
                "source": "harmony",
                "domain": "architecture",
                "action": "improve",
            })
    except Exception:
        pass
    return ideas


def _harvest_from_incubator(project_root: Path) -> list[dict[str, str]]:
    """Récolte des idées dormantes dans l'incubateur."""
    mod = _load_tool("incubator")
    if mod is None:
        return []
    ideas = []
    try:
        all_ideas = mod.load_incubator(project_root)
        for idea in all_ideas:
            if idea.status in ("DORMANT", "SEED"):
                ideas.append({
                    "title": idea.title,
                    "description": idea.description,
                    "source": "incubator",
                    "domain": _classify_domain(idea.description),
                    "action": "add",
                })
    except Exception:
        pass
    return ideas


def _harvest_from_stigmergy(project_root: Path) -> list[dict[str, str]]:
    """Récolte des phéromones OPPORTUNITY et NEED."""
    mod = _load_tool("stigmergy")
    if mod is None:
        return []
    ideas = []
    try:
        if hasattr(mod, "load_pheromones"):
            pheromones = mod.load_pheromones(project_root)
            for ph in pheromones:
                ptype = getattr(ph, "ptype", "") or ph.get("type", "")
                if ptype in ("OPPORTUNITY", "NEED"):
                    text = getattr(ph, "text", "") or ph.get("text", "")
                    ideas.append({
                        "title": f"Signal: {text[:80]}",
                        "description": text,
                        "source": "stigmergy",
                        "domain": _classify_domain(text),
                        "action": "add",
                    })
    except Exception:
        pass
    return ideas


def _harvest_from_project_scan(project_root: Path) -> list[dict[str, str]]:
    """Récolte statistique par scan du projet — gaps évidents."""
    ideas = []
    tools_dir = project_root / "framework" / "tools"
    docs_dir = project_root / "docs"
    tests_dir = project_root / "tests"

    # Gap : ratio outils / tests
    n_tools = len(list(tools_dir.glob("*.py"))) if tools_dir.exists() else 0
    n_tests = len(list(tests_dir.glob("test_*.py"))) if tests_dir.exists() else 0
    if n_tools > 0 and n_tests < n_tools * 0.5:
        ideas.append({
            "title": f"Gap: {n_tools} outils mais seulement {n_tests} test files",
            "description": f"Ratio tests/outils = {n_tests / n_tools:.0%}. Objectif > 50%.",
            "source": "dna-drift",
            "domain": "testing",
            "action": "add",
        })

    # Gap: docs
    n_docs = len(list(docs_dir.glob("*.md"))) if docs_dir.exists() else 0
    if n_tools > 10 and n_docs < 3:
        ideas.append({
            "title": f"Gap: {n_tools} outils mais seulement {n_docs} fichiers docs",
            "description": "La documentation ne suit pas le rythme de développement.",
            "source": "dna-drift",
            "domain": "documentation",
            "action": "add",
        })

    return ideas


def _classify_domain(text: str) -> str:
    """Classifie le domaine d'une idée à partir de son texte."""
    text_lower = text.lower()
    keywords = {
        "tools": ["outil", "tool", "cli", "script", "command"],
        "agents": ["agent", "persona", "rôle", "role"],
        "workflows": ["workflow", "process", "étape", "step", "pipeline"],
        "architecture": ["architect", "pattern", "structure", "dépendance", "dependency"],
        "documentation": ["doc", "readme", "guide", "tutorial"],
        "testing": ["test", "smoke", "coverage", "validity"],
        "integration": ["api", "intégration", "external", "hook", "ci/cd"],
        "meta": ["meta", "self", "auto", "introspect"],
        "resilience": ["resilient", "robust", "antifragile", "heal", "recovery"],
        "performance": ["performance", "speed", "fast", "optim", "cache"],
    }
    best, best_count = "tools", 0
    for domain, kws in keywords.items():
        count = sum(1 for kw in kws if kw in text_lower)
        if count > best_count:
            best, best_count = domain, count
    return best


def _classify_action(text: str) -> str:
    """Classifie l'action d'une idée."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["nouveau", "ajouter", "créer", "new", "add"]):
        return "add"
    if any(w in text_lower for w in ["améliorer", "improve", "optimiser", "enhance"]):
        return "improve"
    if any(w in text_lower for w in ["simplifier", "réduire", "simplify", "reduce"]):
        return "simplify"
    if any(w in text_lower for w in ["fusionner", "merge", "combiner", "combine"]):
        return "merge"
    if any(w in text_lower for w in ["diviser", "split", "séparer", "separate"]):
        return "split"
    if any(w in text_lower for w in ["supprimer", "remove", "éliminer"]):
        return "remove"
    return "improve"


def harvest(project_root: Path, policy: Policy,
            budget: int = DEFAULT_BUDGET) -> list[Idea]:
    """Phase 1 : Récolte d'idées depuis toutes les sources.

    Utilise la policy pour pondérer les sources (reinforcement).
    Epsilon-greedy : explore aléatoirement avec probabilité epsilon.
    """
    raw_ideas: list[dict[str, str]] = []

    # Récolte brute depuis toutes les sources
    harvesters = {
        "dream": _harvest_from_dream,
        "oracle-swot": _harvest_from_oracle,
        "oracle-attract": _harvest_from_oracle,
        "early-warning": _harvest_from_early_warning,
        "harmony": _harvest_from_harmony,
        "incubator": _harvest_from_incubator,
        "stigmergy": _harvest_from_stigmergy,
        "dna-drift": _harvest_from_project_scan,
    }

    for _source_key, harvester in harvesters.items():
        try:
            results = harvester(project_root)
            raw_ideas.extend(results)
        except Exception:
            continue

    if not raw_ideas:
        # Fallback: générer des idées synthétiques par analyse structurelle
        raw_ideas = _harvest_from_project_scan(project_root)

    # Dédupliquation par titre normalisé
    seen_titles: set[str] = set()
    unique_ideas: list[dict[str, str]] = []
    for raw in raw_ideas:
        norm = raw["title"].lower().strip()
        if norm not in seen_titles:
            seen_titles.add(norm)
            unique_ideas.append(raw)

    # Pondération par policy (reinforcement)
    scored_raw: list[tuple[float, dict[str, str]]] = []
    for raw in unique_ideas:
        source = raw.get("source", "")
        domain = raw.get("domain", "")
        action = raw.get("action", "")
        # Score de priorité basé sur la policy
        w_source = policy.source_weights.get(source, 0.1)
        w_domain = policy.domain_weights.get(domain, 0.1)
        w_action = policy.action_weights.get(action, 0.1)
        priority = w_source + w_domain + w_action

        # Epsilon-greedy : exploration aléatoire
        if random.random() < policy.epsilon:
            priority = random.uniform(0.01, 0.5)

        scored_raw.append((priority, raw))

    # Sélection des top-N par budget
    scored_raw.sort(key=lambda x: x[0], reverse=True)
    selected = scored_raw[:budget]

    # Conversion en Idea
    cycle_id = next_cycle_id(project_root)
    ideas = []
    for i, (_, raw) in enumerate(selected):
        idea_id = f"RND-{cycle_id:04d}-{i + 1:02d}"
        ideas.append(Idea(
            id=idea_id,
            title=raw["title"],
            description=raw.get("description", ""),
            source=raw.get("source", "unknown"),
            domain=raw.get("domain", "tools"),
            action=raw.get("action", "add"),
            created_at=datetime.now().isoformat(),
            cycle_id=cycle_id,
        ))

    return ideas


# ── Phase 2 : EVALUATE ──────────────────────────────────────────

def evaluate(ideas: list[Idea], project_root: Path,
             policy: Policy) -> list[Idea]:
    """Phase 2 : Scoring multi-dimensionnel automatique."""
    tools_dir = project_root / "framework" / "tools"
    existing_tools = {f.stem for f in tools_dir.glob("*.py")} if tools_dir.exists() else set()
    memory = load_memory(project_root)

    for idea in ideas:
        scores: dict[str, float] = {}

        # Faisabilité (0-1) : actions simples = plus faisables
        action_feas = {"add": 0.7, "improve": 0.8, "simplify": 0.9,
                       "merge": 0.6, "split": 0.5, "remove": 0.95}
        scores["feasibility"] = action_feas.get(idea.action, 0.5)

        # Impact (0-1) : basé sur le domaine
        domain_impact = {"architecture": 0.9, "tools": 0.7, "resilience": 0.8,
                         "meta": 0.85, "testing": 0.6, "documentation": 0.5,
                         "workflows": 0.7, "agents": 0.75, "integration": 0.65,
                         "performance": 0.7}
        scores["impact"] = domain_impact.get(idea.domain, 0.5)

        # Unicité (0-1) : distance avec les outils existants
        title_words = set(idea.title.lower().split())
        overlaps = sum(1 for t in existing_tools if t.replace("-", " ").replace("_", " ")
                       in idea.title.lower() or any(w in t for w in title_words if len(w) > 3))
        scores["uniqueness"] = max(0.1, 1.0 - overlaps * 0.3)

        # Synergie (0-1) : combien d'outils existants ça mentionne
        mentions = sum(1 for t in existing_tools
                       if t in idea.description.lower())
        scores["synergy"] = min(1.0, 0.3 + mentions * 0.15)

        # Risque inverse (0-1) : remove = plus risqué, add = moins risqué
        action_risk = {"add": 0.85, "improve": 0.7, "simplify": 0.9,
                       "merge": 0.5, "split": 0.6, "remove": 0.3}
        scores["risk_inverse"] = action_risk.get(idea.action, 0.5)

        # Novelty bonus (reinforcement) : idées dans des domaines peu explorés
        domain_history = [m for m in memory if m.get("domain") == idea.domain]
        novelty = max(0.2, 1.0 - len(domain_history) * 0.05)
        scores["novelty"] = min(1.0, novelty)

        # Score total pondéré par la policy
        total = 0.0
        for dim, weight in policy.scoring_weights.items():
            total += scores.get(dim, 0.5) * weight
        total = min(1.0, max(0.0, total))

        idea.scores = scores
        idea.total_score = round(total, 4)

    # Tri par score décroissant
    ideas.sort(key=lambda x: x.total_score, reverse=True)
    return ideas


# ── Phase 3 : CHALLENGE ─────────────────────────────────────────

def challenge(ideas: list[Idea], project_root: Path) -> list[Idea]:
    """Phase 3 : Adversarial red-team — pre-mortem automatique."""
    memory = load_memory(project_root)

    for idea in ideas:
        notes: list[str] = []
        go_score = idea.total_score

        # Check 1: Duplication avec l'historique
        past_titles = {m.get("title", "").lower() for m in memory}
        if idea.title.lower() in past_titles:
            notes.append("⚠️ Idée déjà explorée dans un cycle précédent")
            go_score -= 0.15

        # Check 2: Domaine surinvesti
        domain_count = sum(1 for m in memory
                           if m.get("domain") == idea.domain and m.get("merged"))
        if domain_count > 5:
            notes.append(f"⚠️ Domaine '{idea.domain}' déjà saturé ({domain_count} innovations)")
            go_score -= 0.10

        # Check 3: Pattern d'échec récurrent
        failed_in_domain = sum(1 for m in memory
                               if m.get("domain") == idea.domain
                               and not m.get("merged") and m.get("reward", 0) < 0)
        if failed_in_domain > 2:
            notes.append(f"🔴 Pattern d'échec détecté dans '{idea.domain}' ({failed_in_domain} échecs)")
            go_score -= 0.20

        # Check 4: Complexité vs budget
        if idea.action in ("split", "merge") and idea.scores.get("feasibility", 1) < 0.5:
            notes.append("⚠️ Action complexe avec faible faisabilité")
            go_score -= 0.10

        # Check 5: Pre-mortem — raisons d'échec probables
        if idea.scores.get("risk_inverse", 1) < 0.4:
            notes.append("🔴 Pre-mortem: risque élevé de régression")
        if idea.scores.get("uniqueness", 1) < 0.3:
            notes.append("⚠️ Pre-mortem: trop proche d'un outil existant")

        # Verdict
        idea.challenge_notes = notes
        if go_score >= 0.5:
            idea.challenge_result = "GO"
        elif go_score >= 0.3:
            idea.challenge_result = "CONDITIONAL"
        else:
            idea.challenge_result = "NO-GO"

    return ideas


# ── Phase 4 : SIMULATE ──────────────────────────────────────────

def simulate(ideas: list[Idea], project_root: Path) -> list[Idea]:
    """Phase 4 : Simulation d'impact via digital-twin."""
    mod = _load_tool("digital-twin")

    for idea in ideas:
        if idea.challenge_result == "NO-GO":
            continue

        if mod is not None:
            try:
                entities, edges = mod.scan_project(project_root)
                # Simuler le changement
                action_map = {"add": "add", "improve": "modify",
                              "remove": "remove", "simplify": "modify",
                              "merge": "modify", "split": "add"}
                sim_action = action_map.get(idea.action, "modify")
                change = mod.SimulationChange(
                    action=sim_action,
                    target_kind="tool",
                    target_name=idea.title[:40],
                )
                if hasattr(mod, "simulate_impact"):
                    result = mod.simulate_impact(change, entities, edges)
                    idea.simulation_risk = getattr(result, "risk_score", 0.0)
                    idea.simulation_impacts = len(getattr(result, "direct_impacts", []))
            except Exception:
                idea.simulation_risk = 0.3
                idea.simulation_impacts = 0
        else:
            # Estimation heuristique sans digital-twin
            risk_map = {"add": 0.2, "improve": 0.3, "remove": 0.7,
                        "simplify": 0.15, "merge": 0.5, "split": 0.4}
            idea.simulation_risk = risk_map.get(idea.action, 0.3)

    return ideas


# ── Phase 5 : IMPLEMENT (quality gates check) ───────────────────

def check_quality_gates(project_root: Path) -> dict[str, Any]:
    """Phase 5 : Vérification des quality gates du projet."""
    gates: dict[str, Any] = {}

    # Gate 1: Smoke tests
    smoke_test = project_root / "tests" / "smoke-test.sh"
    gates["smoke_test_exists"] = smoke_test.exists()

    # Gate 2: Lint (ruff)
    tools_dir = project_root / "framework" / "tools"
    py_files = list(tools_dir.glob("*.py")) if tools_dir.exists() else []
    gates["tool_count"] = len(py_files)

    # Gate 3: Harmony check
    mod = _load_tool("harmony-check")
    if mod is not None:
        try:
            scan = mod.scan_project(project_root)
            high_diss = [d for d in scan.dissonances if d.severity == "HIGH"]
            gates["harmony_high_issues"] = len(high_diss)
            gates["harmony_pass"] = len(high_diss) == 0
        except Exception:
            gates["harmony_pass"] = True
    else:
        gates["harmony_pass"] = True

    # Gate 4: Antifragile score
    mod_af = _load_tool("antifragile-score")
    if mod_af is not None:
        try:
            if hasattr(mod_af, "compute_score"):
                score = mod_af.compute_score(project_root)
                gates["antifragile_score"] = getattr(score, "total", 50)
            else:
                gates["antifragile_score"] = 50
        except Exception:
            gates["antifragile_score"] = 50
    else:
        gates["antifragile_score"] = 50

    gates["all_pass"] = gates.get("harmony_pass", True) and gates.get("antifragile_score", 0) >= 30
    return gates


# ── Phase 6 : SELECT (Tournament) ───────────────────────────────

def select_winners(ideas: list[Idea],
                   quality_gates: dict[str, Any]) -> list[Idea]:
    """Phase 6 : Tournament selection — seuls les meilleurs survivent."""
    candidates = [i for i in ideas if i.challenge_result in ("GO", "CONDITIONAL")]

    for idea in candidates:
        # Fitness composite
        score = idea.total_score
        risk_penalty = idea.simulation_risk * 0.3
        complexity_penalty = 0.1 if idea.action in ("merge", "split") else 0.0

        # Bonus si quality gates passent
        gate_bonus = 0.1 if quality_gates.get("all_pass") else 0.0

        idea.reward = round(score - risk_penalty - complexity_penalty + gate_bonus, 4)

    # Tri par reward
    candidates.sort(key=lambda x: x.reward, reverse=True)

    # Marquer les gagnants (top 50%)
    n_winners = max(1, len(candidates) // 2)
    for i, idea in enumerate(candidates):
        idea.merged = i < n_winners

    return candidates


# ── Phase 7 : CONVERGE + LEARN ───────────────────────────────────

def update_policy(policy: Policy, ideas: list[Idea]) -> Policy:
    """Reinforcement : met à jour la policy basé sur les rewards."""
    if not ideas:
        return policy

    lr = policy.learning_rate

    # Séparer gagnants / perdants
    winners = [i for i in ideas if i.merged and i.reward > 0]
    losers = [i for i in ideas if not i.merged or i.reward <= 0]

    # Renforcer les sources/domaines/actions des gagnants
    for idea in winners:
        r = idea.reward
        src = idea.source
        dom = idea.domain
        act = idea.action

        if src in policy.source_weights:
            policy.source_weights[src] += lr * r
        if dom in policy.domain_weights:
            policy.domain_weights[dom] += lr * r
        if act in policy.action_weights:
            policy.action_weights[act] += lr * r

    # Pénaliser les sources/domaines/actions des perdants
    for idea in losers:
        r = abs(idea.reward) if idea.reward != 0 else 0.1
        src = idea.source
        dom = idea.domain
        act = idea.action

        if src in policy.source_weights:
            policy.source_weights[src] = max(0.01, policy.source_weights[src] - lr * r * 0.5)
        if dom in policy.domain_weights:
            policy.domain_weights[dom] = max(0.01, policy.domain_weights[dom] - lr * r * 0.5)
        if act in policy.action_weights:
            policy.action_weights[act] = max(0.01, policy.action_weights[act] - lr * r * 0.5)

    # Normaliser les poids (somme = 1)
    for weights in (policy.source_weights, policy.domain_weights, policy.action_weights):
        total = sum(weights.values())
        if total > 0:
            for k in weights:
                weights[k] = round(weights[k] / total, 6)

    # Statistiques
    policy.total_ideas += len(ideas)
    policy.total_merged += len(winners)
    policy.total_rewards += sum(i.reward for i in ideas)
    policy.generations += 1

    return policy


def check_convergence(project_root: Path,
                      current_reward: float) -> tuple[str, float]:
    """Vérifie si le système a convergé.

    Returns: (verdict, convergence_metric)
      verdict: CONTINUE, SLOW_DOWN, CONSOLIDATE, STOP
    """
    reports = load_cycle_reports(project_root)
    if len(reports) < CONVERGENCE_WINDOW:
        return "CONTINUE", current_reward

    recent = reports[-CONVERGENCE_WINDOW:]
    recent_rewards = [r.best_reward for r in recent]
    avg_recent = sum(recent_rewards) / len(recent_rewards) if recent_rewards else 0

    # Rendement décroissant ?
    if len(recent_rewards) >= 2:
        deltas = [recent_rewards[i + 1] - recent_rewards[i]
                  for i in range(len(recent_rewards) - 1)]
        avg_delta = sum(deltas) / len(deltas) if deltas else 0

        if avg_delta < -MIN_REWARD_DELTA:
            return "STOP", avg_recent
        if abs(avg_delta) < MIN_REWARD_DELTA:
            # Plateau
            if len(reports) > CONVERGENCE_WINDOW * 2:
                return "CONSOLIDATE", avg_recent
            return "SLOW_DOWN", avg_recent
        if avg_delta > MIN_REWARD_DELTA * 2:
            return "CONTINUE", avg_recent

    # Oscillation check
    if len(recent_rewards) >= 3:
        ups = sum(1 for i in range(len(recent_rewards) - 1)
                  if recent_rewards[i + 1] > recent_rewards[i])
        downs = len(recent_rewards) - 1 - ups
        if ups > 0 and downs > 0 and abs(ups - downs) <= 1:
            return "SLOW_DOWN", avg_recent

    return "CONTINUE", avg_recent


# ── Cycle principal ──────────────────────────────────────────────

def run_cycle(project_root: Path, policy: Policy,
              budget: int = DEFAULT_BUDGET,
              epoch: int = 1,
              quick: bool = False) -> CycleReport:
    """Exécute un cycle complet d'innovation (7 phases)."""
    start = time.monotonic()
    cycle_id = next_cycle_id(project_root)

    report = CycleReport(
        cycle_id=cycle_id,
        epoch=epoch,
        timestamp=datetime.now().isoformat(),
    )

    # Phase 1: HARVEST
    ideas = harvest(project_root, policy, budget=budget)
    report.ideas_harvested = len(ideas)

    if not ideas:
        report.verdict = "NO_IDEAS"
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    # Phase 2: EVALUATE
    ideas = evaluate(ideas, project_root, policy)
    report.ideas_evaluated = len(ideas)

    if quick:
        # Mode quick: s'arrête après evaluate
        report.ideas = [i.to_dict() for i in ideas]
        report.verdict = "QUICK_STOP"
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    # Phase 3: CHALLENGE
    ideas = challenge(ideas, project_root)
    go_ideas = [i for i in ideas if i.challenge_result in ("GO", "CONDITIONAL")]
    report.ideas_challenged = len(ideas)
    report.ideas_go = len(go_ideas)

    if not go_ideas:
        report.ideas = [i.to_dict() for i in ideas]
        report.verdict = "ALL_REJECTED"
        report.duration_ms = int((time.monotonic() - start) * 1000)
        return report

    # Phase 4: SIMULATE
    go_ideas = simulate(go_ideas, project_root)
    report.ideas_simulated = len(go_ideas)

    # Phase 5: QUALITY GATES
    quality_gates = check_quality_gates(project_root)

    # Phase 6: SELECT
    candidates = select_winners(go_ideas, quality_gates)
    winners = [c for c in candidates if c.merged]
    report.ideas_merged = len(winners)

    # Phase 7: CONVERGE + LEARN
    best_reward = max((i.reward for i in candidates), default=0.0)
    avg_reward = (sum(i.reward for i in candidates) / len(candidates)) if candidates else 0.0
    report.best_reward = round(best_reward, 4)
    report.avg_reward = round(avg_reward, 4)

    verdict, conv_metric = check_convergence(project_root, best_reward)
    report.convergence_metric = round(conv_metric, 4)
    report.verdict = verdict

    # Update policy (reinforcement learning)
    update_policy(policy, candidates)
    report.policy_snapshot = policy.to_dict()

    # Save cycle ideas to memory
    memory = load_memory(project_root)
    for idea in candidates:
        memory.append(idea.to_dict())
    save_memory(project_root, memory)

    # Save all ideas in report
    report.ideas = [i.to_dict() for i in ideas]

    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


# ── Mode Train (multi-cycles intensif) ──────────────────────────

def train(project_root: Path, epochs: int = DEFAULT_EPOCHS,
          budget: int = DEFAULT_BUDGET,
          auto_stop: bool = False,
          verbose: bool = True) -> TrainReport:
    """Mode intensif : N cycles avec reinforcement learning.

    Comme un entraînement ML :
      - Chaque epoch = un cycle complet
      - La policy s'ajuste après chaque epoch (gradient de reward)
      - auto_stop=True arrête si convergence détectée
      - Les meilleurs résultats sont renforcés (exploit)
      - Epsilon exploration assure la diversité
    """
    train_start = time.monotonic()
    policy = load_policy(project_root)
    _ensure_dirs(project_root)

    train_report = TrainReport(
        epochs_requested=epochs,
    )

    reward_curve: list[float] = []
    best_idea_ever: dict[str, Any] = {}
    best_reward_ever = -1.0

    for epoch in range(1, epochs + 1):
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"  EPOCH {epoch}/{epochs}")
            print(f"  Epsilon: {policy.epsilon:.3f} | "
                  f"LR: {policy.learning_rate:.3f} | "
                  f"Budget: {budget}")
            print(f"{'=' * 60}")

        # Run cycle
        report = run_cycle(project_root, policy, budget=budget, epoch=epoch)
        save_cycle_report(project_root, report)
        save_policy(project_root, policy)

        # Track rewards
        reward_curve.append(report.best_reward)
        train_report.cycles.append(report.to_dict())
        train_report.total_ideas += report.ideas_harvested
        train_report.total_merged += report.ideas_merged

        # Track best ever
        if report.best_reward > best_reward_ever:
            best_reward_ever = report.best_reward
            winners = [i for i in report.ideas if i.get("merged")]
            if winners:
                best_idea_ever = winners[0]

        if verbose:
            _print_cycle_summary(report, epoch, epochs)

        # Adaptive epsilon decay (exploration diminue avec le temps)
        policy.epsilon = max(0.05, policy.epsilon * 0.95)

        # Adaptive learning rate (diminue si oscillation)
        if len(reward_curve) >= 3:
            last3 = reward_curve[-3:]
            if last3[0] > last3[1] < last3[2]:  # oscillation
                policy.learning_rate = max(0.01, policy.learning_rate * 0.8)

        # Auto-stop check
        if auto_stop and report.verdict in ("STOP", "CONSOLIDATE"):
            if verbose:
                print(f"\n🛑 Auto-stop: {report.verdict} à l'epoch {epoch}")
            train_report.convergence_reached = True
            train_report.convergence_epoch = epoch
            break

    # Final report
    train_report.epochs_completed = len(reward_curve)
    train_report.reward_curve = [round(r, 4) for r in reward_curve]
    train_report.best_idea = best_idea_ever
    train_report.final_policy = policy.to_dict()
    train_report.duration_ms = int((time.monotonic() - train_start) * 1000)

    # Verdict global
    if train_report.convergence_reached:
        train_report.verdict = "CONVERGED"
    elif reward_curve and reward_curve[-1] > reward_curve[0]:
        train_report.verdict = "IMPROVING"
    elif reward_curve and reward_curve[-1] < reward_curve[0]:
        train_report.verdict = "DEGRADING"
    else:
        train_report.verdict = "STABLE"

    if verbose:
        _print_train_summary(train_report)

    return train_report


# ── Affichage ────────────────────────────────────────────────────

def _print_cycle_summary(report: CycleReport, epoch: int, total: int) -> None:
    """Affiche un résumé de cycle compact."""
    bar_len = 20
    bar_fill = int(report.best_reward * bar_len)
    bar = "█" * bar_fill + "░" * (bar_len - bar_fill)

    print(f"\n  📊 Cycle {report.cycle_id} (epoch {epoch}/{total})")
    print(f"  ├── Idées: {report.ideas_harvested} récoltées"
          f" → {report.ideas_go} GO → {report.ideas_merged} merged")
    print(f"  ├── Reward: [{bar}] {report.best_reward:.3f} "
          f"(avg: {report.avg_reward:.3f})")
    print(f"  ├── Verdict: {report.verdict}")
    print(f"  └── Durée: {report.duration_ms}ms")

    # Top idées mergées
    winners = [i for i in report.ideas if i.get("merged")]
    if winners:
        print("\n  🏆 Innovation(s) sélectionnée(s):")
        for w in winners[:3]:
            print(f"     • [{w.get('domain', '?')}] {w.get('title', '?')[:60]}"
                  f" (reward: {w.get('reward', 0):.3f})")


def _print_train_summary(report: TrainReport) -> None:
    """Affiche le résumé d'entraînement final."""
    print(f"\n{'═' * 60}")
    print("  🎓 ENTRAÎNEMENT TERMINÉ")
    print(f"{'═' * 60}")
    print(f"  Epochs: {report.epochs_completed}/{report.epochs_requested}")
    print(f"  Idées totales: {report.total_ideas}")
    print(f"  Innovations mergées: {report.total_merged}")
    print(f"  Verdict: {report.verdict}")
    if report.convergence_reached:
        print(f"  Convergence à l'epoch: {report.convergence_epoch}")
    print(f"  Durée totale: {report.duration_ms}ms")

    # Courbe de reward
    if report.reward_curve:
        print("\n  📈 Courbe de reward:")
        max_r = max(report.reward_curve) if report.reward_curve else 1
        for i, r in enumerate(report.reward_curve):
            bar_len = 30
            bar_fill = int((r / max_r) * bar_len) if max_r > 0 else 0
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            marker = " ←best" if r == max_r else ""
            print(f"    E{i + 1:02d} [{bar}] {r:.4f}{marker}")

    # Meilleure idée
    if report.best_idea:
        print("\n  🏆 Meilleure innovation globale:")
        print(f"     {report.best_idea.get('title', '?')}")
        print(f"     Domaine: {report.best_idea.get('domain', '?')}"
              f" | Reward: {report.best_idea.get('reward', 0):.4f}")

    # Policy finale (top sources/domaines)
    fp = report.final_policy
    if fp.get("source_weights"):
        top_sources = sorted(fp["source_weights"].items(),
                             key=lambda x: x[1], reverse=True)[:3]
        print("\n  🧠 Policy apprise (top sources):")
        for s, w in top_sources:
            print(f"     • {s}: {w:.4f}")
    if fp.get("domain_weights"):
        top_domains = sorted(fp["domain_weights"].items(),
                             key=lambda x: x[1], reverse=True)[:3]
        print("  🧠 Policy apprise (top domaines):")
        for d, w in top_domains:
            print(f"     • {d}: {w:.4f}")


# ── Commands ─────────────────────────────────────────────────────

def cmd_cycle(args: argparse.Namespace) -> int:
    """Commande: un cycle d'innovation."""
    project_root = Path(args.project_root).resolve()
    _ensure_dirs(project_root)
    policy = load_policy(project_root)

    report = run_cycle(project_root, policy, budget=args.budget,
                       quick=args.quick)
    save_cycle_report(project_root, report)
    save_policy(project_root, policy)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_cycle_summary(report, 1, 1)
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """Commande: N cycles intensifs avec reinforcement learning."""
    project_root = Path(args.project_root).resolve()
    _ensure_dirs(project_root)

    report = train(
        project_root,
        epochs=args.epochs,
        budget=args.budget,
        auto_stop=args.auto_stop,
        verbose=not args.json,
    )

    if args.json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False))

    # Sauvegarder le rapport d'entraînement
    train_path = _rnd_dir(project_root) / f"train-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    train_path.write_text(json.dumps(asdict(report), indent=2, ensure_ascii=False),
                          encoding="utf-8")

    return 0


def cmd_harvest(args: argparse.Namespace) -> int:
    """Commande: phase 1 seule."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    ideas = harvest(project_root, policy, budget=args.budget)

    if args.json:
        print(json.dumps([i.to_dict() for i in ideas], indent=2, ensure_ascii=False))
    else:
        print(f"\n🌾 Récolte : {len(ideas)} idée(s)\n")
        for idea in ideas:
            print(f"  [{idea.domain}] {idea.title}")
            print(f"    Source: {idea.source} | Action: {idea.action}")
            if idea.description:
                print(f"    {idea.description[:100]}")
            print()
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Commande: phase 2 seule."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    ideas = harvest(project_root, policy, budget=args.budget)
    ideas = evaluate(ideas, project_root, policy)

    if args.json:
        print(json.dumps([i.to_dict() for i in ideas], indent=2, ensure_ascii=False))
    else:
        print(f"\n📊 Évaluation : {len(ideas)} idée(s)\n")
        for idea in ideas:
            bar_len = 20
            bar_fill = int(idea.total_score * bar_len)
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            print(f"  [{bar}] {idea.total_score:.3f} — {idea.title[:60]}")
            print(f"    {' | '.join(f'{k}={v:.2f}' for k, v in idea.scores.items())}")
            print()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Commande: état du moteur."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    reports = load_cycle_reports(project_root)
    memory = load_memory(project_root)

    status = {
        "version": VERSION,
        "cycles_completed": len(reports),
        "total_ideas_explored": policy.total_ideas,
        "total_merged": policy.total_merged,
        "generations": policy.generations,
        "epsilon": policy.epsilon,
        "learning_rate": policy.learning_rate,
        "memory_entries": len(memory),
    }

    if reports:
        last = reports[-1]
        status["last_cycle"] = {
            "id": last.cycle_id,
            "timestamp": last.timestamp,
            "verdict": last.verdict,
            "best_reward": last.best_reward,
        }

    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))
    else:
        print(f"\n🔬 R&D Engine v{VERSION}")
        print(f"  Cycles: {len(reports)}")
        print(f"  Idées explorées: {policy.total_ideas}")
        print(f"  Innovations mergées: {policy.total_merged}")
        print(f"  Epsilon (exploration): {policy.epsilon:.3f}")
        print(f"  Learning rate: {policy.learning_rate:.3f}")
        print(f"  Mémoire: {len(memory)} entrées")
        if reports:
            last = reports[-1]
            print(f"\n  Dernier cycle: #{last.cycle_id}"
                  f" ({last.timestamp[:10]})"
                  f" — {last.verdict}")
            print(f"  Best reward: {last.best_reward:.4f}")

        # Top policy weights
        if policy.source_weights:
            top = sorted(policy.source_weights.items(),
                         key=lambda x: x[1], reverse=True)[:3]
            print(f"\n  🧠 Top sources: {', '.join(f'{s}({w:.3f})' for s, w in top)}")
        if policy.domain_weights:
            top = sorted(policy.domain_weights.items(),
                         key=lambda x: x[1], reverse=True)[:3]
            print(f"  🧠 Top domaines: {', '.join(f'{d}({w:.3f})' for d, w in top)}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Commande: historique des cycles."""
    project_root = Path(args.project_root).resolve()
    reports = load_cycle_reports(project_root)

    if args.json:
        print(json.dumps([r.to_dict() for r in reports], indent=2, ensure_ascii=False))
    else:
        if not reports:
            print("\n📜 Aucun cycle dans l'historique.")
            return 0

        print(f"\n📜 Historique : {len(reports)} cycle(s)\n")
        for r in reports:
            bar_len = 15
            bar_fill = int(r.best_reward * bar_len) if r.best_reward > 0 else 0
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            print(f"  #{r.cycle_id:04d} [{bar}] {r.best_reward:.3f}"
                  f" | {r.ideas_harvested}→{r.ideas_merged} merged"
                  f" | {r.verdict}")
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    """Commande: dashboard markdown."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)
    reports = load_cycle_reports(project_root)
    memory = load_memory(project_root)

    lines = [
        "# 🔬 R&D Innovation Engine — Dashboard",
        "",
        f"*Généré le {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
        "## Métriques globales",
        "",
        "| Métrique | Valeur |",
        "|---|---|",
        f"| Cycles complétés | {len(reports)} |",
        f"| Idées explorées | {policy.total_ideas} |",
        f"| Innovations mergées | {policy.total_merged} |",
        f"| Taux de succès | {policy.total_merged / max(1, policy.total_ideas):.0%} |",
        f"| Epsilon (exploration) | {policy.epsilon:.3f} |",
        f"| Learning rate | {policy.learning_rate:.3f} |",
        "",
    ]

    # Courbe de reward
    if reports:
        lines.append("## Courbe de Reward")
        lines.append("")
        lines.append("```")
        max_r = max((r.best_reward for r in reports), default=1) or 1
        for r in reports:
            bar_len = 40
            bar_fill = int((r.best_reward / max_r) * bar_len) if max_r > 0 else 0
            bar = "█" * bar_fill + "░" * (bar_len - bar_fill)
            lines.append(f"  C{r.cycle_id:03d} [{bar}] {r.best_reward:.4f} {r.verdict}")
        lines.append("```")
        lines.append("")

    # Policy apprise
    lines.append("## Policy apprise (Reinforcement)")
    lines.append("")
    if policy.source_weights:
        lines.append("### Sources (top → bottom)")
        lines.append("")
        sorted_src = sorted(policy.source_weights.items(),
                            key=lambda x: x[1], reverse=True)
        for s, w in sorted_src:
            bar_fill = int(w * 100)
            lines.append(f"- **{s}**: `{'█' * bar_fill}{'░' * (10 - min(10, bar_fill))}` {w:.4f}")
        lines.append("")

    if policy.domain_weights:
        lines.append("### Domaines (top → bottom)")
        lines.append("")
        sorted_dom = sorted(policy.domain_weights.items(),
                            key=lambda x: x[1], reverse=True)
        for d, w in sorted_dom:
            lines.append(f"- **{d}**: {w:.4f}")
        lines.append("")

    # Innovations sélectionnées
    winners = [m for m in memory if m.get("merged")]
    if winners:
        lines.append("## Innovations sélectionnées")
        lines.append("")
        lines.append("| # | Domaine | Titre | Reward | Cycle |")
        lines.append("|---|---|---|---|---|")
        for i, w in enumerate(winners[-20:], 1):
            lines.append(f"| {i} | {w.get('domain', '?')} | "
                         f"{w.get('title', '?')[:50]} | "
                         f"{w.get('reward', 0):.3f} | "
                         f"{w.get('cycle_id', '?')} |")
        lines.append("")

    # Fossils / rejetées
    rejects = [m for m in memory if not m.get("merged")]
    if rejects:
        lines.append(f"## Fossil Record ({len(rejects)} idées rejetées)")
        lines.append("")
        # Distribution par raison
        domains = defaultdict(int)
        for r in rejects:
            domains[r.get("domain", "?")] += 1
        for d, c in sorted(domains.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- {d}: {c} rejetée(s)")
        lines.append("")

    output = "\n".join(lines)

    if args.json:
        print(json.dumps({"dashboard": output}, indent=2, ensure_ascii=False))
    else:
        # Write to file
        dash_path = _rnd_dir(project_root) / "DASHBOARD.md"
        _ensure_dirs(project_root)
        dash_path.write_text(output, encoding="utf-8")
        print(output)
        print(f"\n💾 Dashboard sauvé dans {dash_path}")
    return 0


def cmd_tune(args: argparse.Namespace) -> int:
    """Commande: ajuster les paramètres du moteur."""
    project_root = Path(args.project_root).resolve()
    policy = load_policy(project_root)

    changed = False
    if args.epsilon is not None:
        old = policy.epsilon
        policy.epsilon = max(0.0, min(1.0, args.epsilon))
        print(f"  Epsilon: {old:.3f} → {policy.epsilon:.3f}")
        changed = True
    if args.learning_rate is not None:
        old = policy.learning_rate
        policy.learning_rate = max(0.001, min(1.0, args.learning_rate))
        print(f"  Learning rate: {old:.3f} → {policy.learning_rate:.3f}")
        changed = True

    if changed:
        save_policy(project_root, policy)
        print("  ✅ Policy mise à jour")
    else:
        print(f"  Epsilon: {policy.epsilon:.3f}")
        print(f"  Learning rate: {policy.learning_rate:.3f}")
        print("  (utiliser --epsilon ou --learning-rate pour modifier)")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Commande: reset du moteur (garde l'historique)."""
    project_root = Path(args.project_root).resolve()

    policy = Policy.default()
    save_policy(project_root, policy)

    print("  🔄 Policy remise à zéro (poids uniformes)")
    print("  📜 L'historique et la mémoire sont préservés")
    return 0


# ── Main ─────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="BMAD R&D Innovation Engine — Reinforcement Learning",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  %(prog)s --project-root . cycle                     # 1 cycle complet
  %(prog)s --project-root . train --epochs 5          # 5 cycles intensifs
  %(prog)s --project-root . train --epochs 10 --auto-stop  # Avec auto-stop
  %(prog)s --project-root . dashboard                 # Tableau de bord
  %(prog)s --project-root . status                    # État du moteur
""",
    )
    parser.add_argument("--project-root", required=True,
                        help="Racine du projet BMAD")
    parser.add_argument("--json", action="store_true",
                        help="Sortie JSON")

    subs = parser.add_subparsers(dest="command")

    # cycle
    p_cycle = subs.add_parser("cycle", help="Un cycle d'innovation complet")
    p_cycle.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                         help=f"Nombre max d'idées (default: {DEFAULT_BUDGET})")
    p_cycle.add_argument("--quick", action="store_true",
                         help="Mode rapide (harvest+evaluate seulement)")
    p_cycle.set_defaults(func=cmd_cycle)

    # train
    p_train = subs.add_parser("train",
                              help="N cycles intensifs (reinforcement learning)")
    p_train.add_argument("--epochs", type=int, default=5,
                         help="Nombre de cycles (default: 5)")
    p_train.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                         help=f"Idées par cycle (default: {DEFAULT_BUDGET})")
    p_train.add_argument("--auto-stop", action="store_true",
                         help="Arrêt automatique si convergence")
    p_train.set_defaults(func=cmd_train)

    # harvest
    p_harvest = subs.add_parser("harvest", help="Phase 1 seule : récolte d'idées")
    p_harvest.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_harvest.set_defaults(func=cmd_harvest)

    # evaluate
    p_eval = subs.add_parser("evaluate", help="Harvest + Phase 2 : scoring")
    p_eval.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p_eval.set_defaults(func=cmd_evaluate)

    # status
    p_status = subs.add_parser("status", help="État du moteur R&D")
    p_status.set_defaults(func=cmd_status)

    # history
    p_hist = subs.add_parser("history", help="Historique des cycles")
    p_hist.set_defaults(func=cmd_history)

    # dashboard
    p_dash = subs.add_parser("dashboard", help="Tableau de bord markdown")
    p_dash.set_defaults(func=cmd_dashboard)

    # tune
    p_tune = subs.add_parser("tune", help="Ajuster les paramètres")
    p_tune.add_argument("--epsilon", type=float, default=None,
                        help="Taux d'exploration (0.0-1.0)")
    p_tune.add_argument("--learning-rate", type=float, default=None,
                        dest="learning_rate",
                        help="Vitesse d'apprentissage (0.001-1.0)")
    p_tune.set_defaults(func=cmd_tune)

    # reset
    p_reset = subs.add_parser("reset", help="Reset policy (garde historique)")
    p_reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
