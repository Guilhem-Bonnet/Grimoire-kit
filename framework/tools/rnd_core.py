#!/usr/bin/env python3
"""
rnd_core.py — R&D Innovation Engine : constantes, data classes, persistence.
═══════════════════════════════════════════════════════════════════

Module noyau extrait de r-and-d.py pour améliorer la lisibilité.
Contient les constantes, structures de données et fonctions de persistance.

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time  # noqa: F401 — re-exported for rnd_engine
from collections import defaultdict  # noqa: F401 — re-exported
from dataclasses import asdict, dataclass, field
from datetime import datetime  # noqa: F401 — re-exported
from pathlib import Path
from typing import Any
import logging

_log = logging.getLogger("grimoire.rnd_core")

VERSION = "2.1.0"

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
    "synthetic",       # idées générées par concept blending
    "mutation",        # mutations des gagnants passés
    "gap-analysis",    # gaps détectés dans le projet réel
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
GO_THRESHOLD = 0.60         # seuil GO pour challenge (relevé de 0.5)
CONDITIONAL_THRESHOLD = 0.40  # seuil CONDITIONAL (relevé de 0.3)
MIN_REJECT_RATIO = 0.20     # au moins 20% d'idées rejetées par cycle
PROTOTYPE_DIR = "prototypes"  # squelettes générés
MAX_MEMORY_SIZE = 500        # cap mémoire : ne garder que les N dernières entrées
MAX_CYCLE_REPORTS_LOAD = 50  # charger au max N rapports historiques

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
    mutation_depth: int = 0        # profondeur de chaîne de mutations (0 = original)

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
    # Poids par scoring (ajustables)
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
    ideas_rejected: int = 0
    avg_reward: float = 0.0
    best_reward: float = 0.0
    convergence_metric: float = 0.0   # moving avg of best_reward
    policy_snapshot: dict[str, Any] = field(default_factory=dict)
    ideas: list[dict[str, Any]] = field(default_factory=list)
    verdict: str = ""   # CONTINUE, SLOW_DOWN, CONSOLIDATE, STOP
    health_before: dict[str, Any] = field(default_factory=dict)
    health_after: dict[str, Any] = field(default_factory=dict)
    health_delta: float = 0.0
    prototypes_generated: int = 0

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
    (base / PROTOTYPE_DIR).mkdir(parents=True, exist_ok=True)


def load_policy(project_root: Path) -> Policy:
    fpath = _rnd_dir(project_root) / POLICY_FILE
    if fpath.exists():
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            return Policy.from_dict(data)
        except (json.JSONDecodeError, OSError) as _exc:
            _log.debug("json.JSONDecodeError, OSError suppressed: %s", _exc)
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
    # Cap mémoire : tronquer aux N entrées les plus récentes
    if len(memory) > MAX_MEMORY_SIZE:
        memory = memory[-MAX_MEMORY_SIZE:]
    fpath = _rnd_dir(project_root) / MEMORY_FILE
    fpath.write_text(json.dumps(memory, indent=2, ensure_ascii=False),
                     encoding="utf-8")


def save_cycle_report(project_root: Path, report: CycleReport) -> None:
    _ensure_dirs(project_root)
    fpath = _rnd_dir(project_root) / HISTORY_DIR / f"cycle-{report.cycle_id:04d}.json"
    fpath.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False),
                     encoding="utf-8")


def load_cycle_reports(project_root: Path,
                       last_n: int = 0) -> list[CycleReport]:
    """Charge les rapports de cycles historiques.

    Args:
        last_n: Si >0, ne charger que les N derniers fichiers (performance).
                Si 0, charger tous (comportement par défaut).
    """
    hdir = _rnd_dir(project_root) / HISTORY_DIR
    if not hdir.exists():
        return []
    files = sorted(hdir.glob("cycle-*.json"))
    if last_n > 0:
        files = files[-last_n:]
    reports = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            reports.append(CycleReport(**{k: v for k, v in data.items()
                                          if k in CycleReport.__dataclass_fields__}))
        except (json.JSONDecodeError, OSError, TypeError):
            continue
    return reports


def next_cycle_id(project_root: Path) -> int:
    """Détermine le prochain cycle ID sans charger tous les rapports."""
    hdir = _rnd_dir(project_root) / HISTORY_DIR
    if not hdir.exists():
        return 1
    files = sorted(hdir.glob("cycle-*.json"))
    if not files:
        return 1
    # Extraire l'ID du dernier fichier : cycle-0042.json → 42
    last = files[-1].stem  # "cycle-0042"
    try:
        return int(last.split("-")[1]) + 1
    except (IndexError, ValueError):
        return len(files) + 1


# ── Dynamic tool loader ─────────────────────────────────────────

def _load_tool(name: str) -> Any:
    """Charge dynamiquement un outil Python co-localisé.

    Utilise sys.modules comme cache — ne recharge le module que s'il
    n'est pas déjà présent, évitant ainsi la fuite mémoire par
    création répétée d'objets module.
    """
    mod_name = name.replace("-", "_")
    # Cache hit : retourner le module déjà chargé
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
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None
