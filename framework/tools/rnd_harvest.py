#!/usr/bin/env python3
"""
rnd_harvest.py — R&D Innovation Engine : phase de récolte.
═══════════════════════════════════════════════════════════════════

Fonctions de récolte d'idées depuis les outils BMAD, générateurs
synthétiques (concept blending), mutations, et analyse de gaps.

Stdlib only — aucune dépendance externe.
"""
from __future__ import annotations

import logging
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from rnd_core import (
    DEFAULT_BUDGET,
    DOMAINS,
    Idea,
    Policy,
    _load_tool,
    load_memory,
    next_cycle_id,
)

_log = logging.getLogger("grimoire.rnd_harvest")

# ── Harvest from individual sources ─────────────────────────────

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
    except Exception as _exc:
        _log.debug("Exception suppressed: %s", _exc)
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
    except Exception as _exc:
        _log.debug("Exception suppressed: %s", _exc)
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
    except Exception as _exc:
        _log.debug("Exception suppressed: %s", _exc)
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
    except Exception as _exc:
        _log.debug("Exception suppressed: %s", _exc)
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
    except Exception as _exc:
        _log.debug("Exception suppressed: %s", _exc)
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
    except Exception as _exc:
        _log.debug("Exception suppressed: %s", _exc)
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
    except Exception as _exc:
        _log.debug("Exception suppressed: %s", _exc)
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


# ── Classifiers ──────────────────────────────────────────────────

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


# ── Générateur d'idées synthétiques (concept blending) ───────────

_INNOVATION_TEMPLATES = [
    # Croisements inter-outils
    ("Combiner {tool_a} et {tool_b} en un pipeline unifié",
     "meta", "merge"),
    ("Créer un mode interactif pour {tool_a}",
     "tools", "improve"),
    ("Ajouter export {format} à {tool_a}",
     "integration", "improve"),
    # Améliorations par domaine
    ("Ajouter métriques de {domain} au dashboard NSO",
     "meta", "improve"),
    ("Créer un hook pre-commit qui vérifie {domain}",
     "integration", "add"),
    ("Documenter les best practices pour {domain}",
     "documentation", "add"),
    # Patterns architecturaux
    ("Ajouter cache intelligent à {tool_a} pour accélérer les re-runs",
     "performance", "improve"),
    ("Créer un mode watch pour {tool_a} (détection de changements)",
     "tools", "add"),
    ("Ajouter scoring composite {domain} au oracle.py",
     "meta", "improve"),
    # Résilience
    ("Ajouter retry automatique quand {tool_a} échoue",
     "resilience", "improve"),
    ("Créer un health-check pour le domaine {domain}",
     "resilience", "add"),
    # Testing
    ("Ajouter property-based testing pour {tool_a}",
     "testing", "add"),
    ("Créer benchmark de performance pour {tool_a}",
     "testing", "add"),
    # Agent
    ("Créer un agent spécialisé en {domain}",
     "agents", "add"),
    ("Ajouter persona alternative à l'agent {agent}",
     "agents", "improve"),
    # Workflows
    ("Créer un workflow automatisé pour {domain}",
     "workflows", "add"),
    ("Simplifier le workflow existant en supprimant les étapes inutilisées",
     "workflows", "simplify"),
    # Architecture
    ("Créer un adapteur MCP pour {tool_a}",
     "integration", "add"),
    ("Ajouter support multi-projet à {tool_a}",
     "architecture", "improve"),
    # Simplification
    ("Fusionner {tool_a} et {tool_b} qui ont des responsabilités proches",
     "architecture", "merge"),
    ("Supprimer les fonctionnalités inutilisées de {tool_a}",
     "architecture", "simplify"),
]

_EXPORT_FORMATS = ["CSV", "HTML", "Mermaid", "SQLite", "SARIF"]


def _generate_synthetic_ideas(project_root: Path, policy: Policy,
                              max_ideas: int = 10) -> list[dict[str, str]]:
    """Génère des idées synthétiques par combinaison croisée."""
    ideas: list[dict[str, str]] = []

    tools_dir = project_root / "framework" / "tools"
    tools = sorted(f.stem for f in tools_dir.glob("*.py")) if tools_dir.exists() else []
    agent_files = []
    for adir in project_root.rglob("agents/*.md"):
        agent_files.append(adir.stem)
    agents = sorted(set(agent_files))[:10] if agent_files else ["dev", "pm", "qa"]

    if not tools:
        return []

    domain_pool = list(DOMAINS)
    if policy.domain_weights:
        domain_pool = sorted(policy.domain_weights.keys(),
                             key=lambda d: policy.domain_weights.get(d, 0),
                             reverse=True)

    rng = random.Random()

    for _ in range(max_ideas * 3):
        template, tpl_domain, tpl_action = rng.choice(_INNOVATION_TEMPLATES)
        tool_a = rng.choice(tools)
        tool_b = rng.choice([t for t in tools if t != tool_a] or tools)
        domain = rng.choice(domain_pool[:5])
        agent = rng.choice(agents)
        fmt = rng.choice(_EXPORT_FORMATS)

        title = template.format(
            tool_a=tool_a, tool_b=tool_b,
            domain=domain, agent=agent, format=fmt,
        )

        ideas.append({
            "title": title,
            "description": f"Innovation synthétique : {title}",
            "source": "synthetic",
            "domain": tpl_domain,
            "action": tpl_action,
        })

    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for idea in ideas:
        norm = idea["title"].lower()
        if norm not in seen:
            seen.add(norm)
            unique.append(idea)

    rng.shuffle(unique)
    return unique[:max_ideas]


def _mutate_past_winners(project_root: Path,
                         max_ideas: int = 5) -> list[dict[str, str]]:
    """Génère des idées par mutation des gagnants passés."""
    memory = load_memory(project_root)
    winners = [m for m in memory if m.get("merged") and m.get("reward", 0) > 0]
    if not winners:
        return []

    winners.sort(key=lambda m: m.get("reward", 0), reverse=True)

    rng = random.Random()
    ideas: list[dict[str, str]] = []
    mutations = [
        ("transposition", lambda w: {
            **w,
            "domain": rng.choice([d for d in DOMAINS if d != w.get("domain")]),
            "title": f"Transposer '{w.get('title', '')[:40]}' au domaine "
                     f"{rng.choice([d for d in DOMAINS if d != w.get('domain')])}",
        }),
        ("escalade", lambda w: {
            **w,
            "action": "merge" if w.get("action") == "add" else "add",
            "title": f"Escalader: {w.get('title', '')[:40]} → version avancée",
        }),
        ("inverse", lambda w: {
            **w,
            "action": "simplify" if w.get("action") in ("add", "merge") else "add",
            "title": f"Inverser: simplifier ce que '{w.get('title', '')[:40]}' complexifie",
        }),
        ("fusion", lambda w: {
            **w,
            "action": "merge",
            "title": f"Fusionner '{w.get('title', '')[:30]}' avec un outil adjacent",
        }),
    ]

    past_titles = {m.get("title", "").lower().strip() for m in memory}

    for winner in winners[:max_ideas * 2]:
        mut_name, mutator = rng.choice(mutations)
        try:
            mutated = mutator(winner)
            mutated["source"] = "mutation"
            parent_depth = winner.get("mutation_depth", 0)
            if winner.get("source") == "mutation" and parent_depth == 0:
                parent_depth = 1
            mutated["mutation_depth"] = parent_depth + 1
            mutated["description"] = (
                f"Mutation ({mut_name}, depth={parent_depth + 1}) de l'innovation "
                f"'{winner.get('title', '')[:50]}' (reward: {winner.get('reward', 0):.3f})"
            )
            if mutated["title"].lower().strip() not in past_titles:
                ideas.append(mutated)
        except Exception:
            continue

    return ideas[:max_ideas]


def _gap_driven_ideas(project_root: Path,
                      max_ideas: int = 5) -> list[dict[str, str]]:
    """Génère des idées basées sur les vrais gaps du projet."""
    ideas: list[dict[str, str]] = []
    tools_dir = project_root / "framework" / "tools"
    tests_dir = project_root / "tests"
    docs_dir = project_root / "docs"

    tools = sorted(f.stem for f in tools_dir.glob("*.py")) if tools_dir.exists() else []
    tests = {f.stem.replace("test_", "").replace("_", "-")
             for f in tests_dir.glob("test_*.py")} if tests_dir.exists() else set()

    # Gap 1: Outils sans tests
    untested = [t for t in tools if t not in tests and t.replace("-", "_") not in
                {f.stem.replace("test_", "") for f in tests_dir.glob("test_*.py")}
                ] if tests_dir.exists() else []
    for tool in untested[:3]:
        ideas.append({
            "title": f"Ajouter tests pour {tool}.py",
            "description": f"L'outil {tool}.py n'a pas de fichier test dédié. "
                          f"Créer test_{tool.replace('-', '_')}.py",
            "source": "gap-analysis",
            "domain": "testing",
            "action": "add",
        })

    # Gap 2: Outils > 500 lignes sans doc spécifique
    for tool_file in tools_dir.glob("*.py") if tools_dir.exists() else []:
        try:
            with tool_file.open(encoding="utf-8") as fh:
                n_lines = sum(1 for _ in fh)
            if n_lines > 500:
                doc_exists = any(
                    tool_file.stem in d.stem
                    for d in docs_dir.glob("*.md")
                ) if docs_dir.exists() else False
                if not doc_exists:
                    ideas.append({
                        "title": f"Documenter {tool_file.stem}.py ({n_lines} lignes)",
                        "description": f"Outil complexe ({n_lines} lignes) sans doc dédiée.",
                        "source": "gap-analysis",
                        "domain": "documentation",
                        "action": "add",
                    })
        except Exception:
            continue

    # Gap 3: Domaines sous-représentés dans les outils
    memory = load_memory(project_root)
    domain_counts: dict[str, int] = defaultdict(int)
    for m in memory:
        if m.get("merged"):
            domain_counts[m.get("domain", "?")] += 1
    underserved = [d for d in DOMAINS if domain_counts.get(d, 0) == 0]
    for domain in underserved[:2]:
        ideas.append({
            "title": f"Explorer le domaine '{domain}' — aucune innovation à ce jour",
            "description": f"Le domaine '{domain}' n'a produit aucune innovation mergée. "
                          f"Rechercher des opportunités spécifiques.",
            "source": "gap-analysis",
            "domain": domain,
            "action": "add",
        })

    # Gap 4: Outils qui importent beaucoup d'autres → point de fragilité
    for tool_file in tools_dir.glob("*.py") if tools_dir.exists() else []:
        try:
            content = tool_file.read_text(encoding="utf-8", errors="ignore")
            imports = [line for line in content.split("\n")
                       if line.strip().startswith(("import ", "from ")) and "tools" in line]
            if len(imports) > 5:
                ideas.append({
                    "title": f"Découpler {tool_file.stem}.py ({len(imports)} dépendances internes)",
                    "description": f"Outil avec {len(imports)} imports internes = point de fragilité.",
                    "source": "gap-analysis",
                    "domain": "architecture",
                    "action": "simplify",
                })
        except Exception:
            continue

    random.shuffle(ideas)
    return ideas[:max_ideas]


# ── Phase 1 : HARVEST (fonction principale) ─────────────────────

def harvest(project_root: Path, policy: Policy,
            budget: int = DEFAULT_BUDGET) -> list[Idea]:
    """Phase 1 : Récolte d'idées depuis toutes les sources.

    Utilise la policy pour pondérer les sources (reinforcement).
    Epsilon-greedy : explore aléatoirement avec probabilité epsilon.
    """
    raw_ideas: list[dict[str, str]] = []

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
        raw_ideas = _harvest_from_project_scan(project_root)

    raw_ideas.extend(_generate_synthetic_ideas(project_root, policy, budget * 2))
    raw_ideas.extend(_mutate_past_winners(project_root, max_ideas=budget))
    raw_ideas.extend(_gap_driven_ideas(project_root, max_ideas=budget))

    # Déduplication par titre normalisé (intra-cycle)
    seen_titles: set[str] = set()
    unique_ideas: list[dict[str, str]] = []
    for raw in raw_ideas:
        norm = raw["title"].lower().strip()
        if norm not in seen_titles:
            seen_titles.add(norm)
            unique_ideas.append(raw)

    # Déduplication inter-cycles
    memory = load_memory(project_root)
    past_titles = {m.get("title", "").lower().strip() for m in memory}
    fresh_ideas: list[dict[str, str]] = []
    recycled_count = 0
    for raw in unique_ideas:
        norm = raw["title"].lower().strip()
        if norm in past_titles:
            recycled_count += 1
            continue
        fresh_ideas.append(raw)

    if recycled_count > 0 and fresh_ideas:
        unique_ideas = fresh_ideas
    elif recycled_count > 0 and not fresh_ideas:
        past_by_title: dict[str, dict] = {}
        for m in memory:
            t = m.get("title", "").lower().strip()
            past_by_title[t] = m
        candidates = [raw for raw in unique_ideas
                      if not past_by_title.get(raw["title"].lower().strip(), {}).get("merged")]
        if candidates:
            unique_ideas = candidates

    # Pondération par policy (reinforcement)
    scored_raw: list[tuple[float, dict[str, str]]] = []
    for raw in unique_ideas:
        source = raw.get("source", "")
        domain = raw.get("domain", "")
        action = raw.get("action", "")
        w_source = policy.source_weights.get(source, 0.1)
        w_domain = policy.domain_weights.get(domain, 0.1)
        w_action = policy.action_weights.get(action, 0.1)
        priority = w_source + w_domain + w_action

        if random.random() < policy.epsilon:
            priority = random.uniform(0.01, 0.5)

        scored_raw.append((priority, raw))

    scored_raw.sort(key=lambda x: x[0], reverse=True)
    selected = scored_raw[:budget]

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
            mutation_depth=int(raw.get("mutation_depth", 0)),
        ))

    return ideas
