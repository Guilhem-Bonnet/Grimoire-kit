#!/usr/bin/env python3
"""
concierge.py — Triage et routage intelligent des requêtes utilisateur.
======================================================================

Analyse une requête en langage naturel et suggère l'agent BMAD le
plus adapté, avec un score de confiance.

Usage :
  python3 concierge.py --project-root . triage --query "Écris des tests pour le module auth"
  python3 concierge.py --project-root . triage --query "Refonte de l'API payments" --json
  python3 concierge.py --project-root . agents
  python3 concierge.py --project-root . check-risk --query "migration base de données"

Stdlib only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.concierge")

# ── Version ──────────────────────────────────────────────────────────────────

CONCIERGE_VERSION = "1.0.0"

# ── Agent Registry ───────────────────────────────────────────────────────────


@dataclass
class AgentProfile:
    """Profil d'un agent pour le routage."""

    tag: str
    name: str
    keywords: list[str]
    use_when: str
    complexity_affinity: str = "any"  # simple | complex | any


AGENT_PROFILES: list[AgentProfile] = [
    AgentProfile(
        tag="dev", name="Amelia",
        keywords=["code", "implement", "bug", "fix", "test", "tdd", "refactor",
                   "function", "class", "module", "script", "debug", "erreur",
                   "coder", "écrire", "implémenter", "corriger", "développer"],
        use_when="Écrire du code, corriger un bug, implémenter, TDD",
        complexity_affinity="any",
    ),
    AgentProfile(
        tag="architect", name="Winston",
        keywords=["architecture", "design", "api", "infra", "pattern", "scale",
                   "microservice", "database", "schema", "migration", "refonte",
                   "système", "cloud", "aws", "docker", "kubernetes", "distribué"],
        use_when="Architecture, refonte système, choix tech, API design",
        complexity_affinity="complex",
    ),
    AgentProfile(
        tag="pm", name="John",
        keywords=["prd", "product", "requirement", "feature", "backlog",
                   "stakeholder", "priorité", "user story", "roadmap", "spec",
                   "produit", "fonctionnalité", "besoin", "cahier des charges"],
        use_when="Définir un produit, PRD, prioriser le backlog",
        complexity_affinity="complex",
    ),
    AgentProfile(
        tag="analyst", name="Mary",
        keywords=["market", "marché", "concurrent", "analyse", "research",
                   "benchmark", "veille", "étude", "compétiteur", "secteur",
                   "domaine", "industrie"],
        use_when="Étude de marché, analyse concurrentielle, veille",
        complexity_affinity="any",
    ),
    AgentProfile(
        tag="qa", name="Quinn",
        keywords=["test", "qualité", "coverage", "e2e", "integration",
                   "couverture", "regression", "automation", "ci", "pipeline",
                   "valider", "vérifier", "tester"],
        use_when="Stratégie de test, couverture, E2E, CI",
        complexity_affinity="any",
    ),
    AgentProfile(
        tag="sm", name="Bob",
        keywords=["sprint", "story", "agile", "scrum", "workflow",
                   "planifier", "découper", "estimer", "kanban", "backlog",
                   "cérémonie", "retrospective", "epic"],
        use_when="Sprint planning, stories, orchestration agile",
        complexity_affinity="complex",
    ),
    AgentProfile(
        tag="tech-writer", name="Paige",
        keywords=["doc", "documentation", "readme", "guide", "mermaid",
                   "diagramme", "rédiger", "documenter", "écrire doc",
                   "tutoriel", "onboarding", "changelog"],
        use_when="Documentation, guides, diagrammes, README",
        complexity_affinity="simple",
    ),
    AgentProfile(
        tag="ux-designer", name="Sally",
        keywords=["ux", "ui", "interface", "wireframe", "design",
                   "ergonomie", "parcours", "accessibilité", "wcag",
                   "composant", "utilisateur", "interaction"],
        use_when="Design d'interface, UX, accessibilité",
        complexity_affinity="any",
    ),
    AgentProfile(
        tag="art-director", name="Frida",
        keywords=["visuel", "identité", "charte", "graphique", "icône",
                   "emoji", "formatage", "style", "couleur", "palette",
                   "typographie", "esthétique"],
        use_when="Charte graphique, identité visuelle, formatage",
        complexity_affinity="simple",
    ),
    AgentProfile(
        tag="creative-toolsmith", name="Vulcan",
        keywords=["outil", "tool", "script", "framework", "automatiser",
                   "mcp", "extension", "plugin", "créer outil", "nouveau tool"],
        use_when="Créer/étendre des outils, framework, automatisation",
        complexity_affinity="any",
    ),
    AgentProfile(
        tag="quick-flow-solo-dev", name="Barry",
        keywords=["rapide", "quick", "prototype", "poc", "mvp",
                   "simple", "solo", "lean", "minimal", "vite"],
        use_when="Prototype rapide, petit projet, spec minimale",
        complexity_affinity="simple",
    ),
]

# ── Classification ───────────────────────────────────────────────────────────

COMPLEXITY_KEYWORDS = {
    "complex": ["refonte", "migration", "architecture", "système", "multi",
                "stratégie", "roadmap", "plan", "analyse complète", "audit"],
    "simple": ["corriger", "fix", "ajouter", "créer", "écrire", "petit",
               "un fichier", "une fonction", "rapide", "quick"],
}

AMBIGUITY_SIGNALS = [
    "améliorer", "mieux", "optimiser", "revoir", "changer",
    "improve", "better", "refactor", "change",
]


@dataclass
class TriageResult:
    """Résultat du triage d'une requête."""

    query: str
    classification: str  # simple | complex | ambiguous
    suggested_agent: str
    agent_name: str
    confidence: float
    reasoning: str
    alternatives: list[dict] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    """Normalise le texte pour le matching."""
    return re.sub(r'[^\w\s]', ' ', text.lower())


def _classify_complexity(query: str) -> str:
    """Classifie la complexité d'une requête."""
    normalized = _normalize(query)
    words = set(normalized.split())

    complex_score = sum(1 for kw in COMPLEXITY_KEYWORDS["complex"]
                        if kw in normalized)
    simple_score = sum(1 for kw in COMPLEXITY_KEYWORDS["simple"]
                       if kw in normalized)
    ambiguity_score = sum(1 for kw in AMBIGUITY_SIGNALS
                          if kw in normalized)

    if ambiguity_score >= 2 and complex_score == 0 and simple_score == 0:
        return "ambiguous"
    if complex_score > simple_score:
        return "complex"
    if simple_score > complex_score:
        return "simple"
    if len(words) < 5:
        return "ambiguous"
    return "simple"


def _score_agent(agent: AgentProfile, query: str) -> float:
    """Score de pertinence d'un agent pour une requête (0.0—1.0)."""
    normalized = _normalize(query)
    words = set(normalized.split())

    # Keyword matching
    matched = 0
    for kw in agent.keywords:
        if " " in kw:
            if kw in normalized:
                matched += 2  # Multi-word match = bonus
        elif kw in words:
            matched += 1

    if not agent.keywords:
        return 0.0

    raw_score = matched / len(agent.keywords)
    # Cap at 1.0 but allow multi-word bonuses
    return min(1.0, raw_score * 3)  # Scale so 1/3 match = 1.0


def triage(query: str, project_root: Path | None = None) -> TriageResult:
    """Analyse une requête et retourne l'agent recommandé.

    Args:
        query: La requête utilisateur.
        project_root: Racine du projet (pour consulter failure-museum).

    Returns:
        TriageResult avec l'agent recommandé et la confiance.
    """
    classification = _classify_complexity(query)

    # Score all agents
    scores: list[tuple[AgentProfile, float]] = []
    for agent in AGENT_PROFILES:
        score = _score_agent(agent, query)
        # Boost if complexity affinity matches
        if agent.complexity_affinity == classification:
            score *= 1.2
        scores.append((agent, min(1.0, score)))

    scores.sort(key=lambda x: -x[1])

    best_agent, best_score = scores[0]
    alternatives = [
        {"agent": a.tag, "name": a.name, "score": round(s, 2), "use_when": a.use_when}
        for a, s in scores[1:4] if s > 0.1
    ]

    # Determine confidence
    if best_score < 0.1:
        confidence = 0.1
        reasoning = "Aucun agent ne correspond clairement — demander des précisions."
    elif best_score < 0.3:
        confidence = 0.25
        reasoning = f"Faible correspondance avec {best_agent.name} ({best_agent.tag}). Clarification recommandée."
    else:
        confidence = best_score
        reasoning = f"{best_agent.name} ({best_agent.tag}) : {best_agent.use_when}"

    # Check risk via failure-museum if available
    risk_warnings: list[str] = []
    if project_root:
        risk_warnings = _check_failure_museum(query, project_root)

    return TriageResult(
        query=query,
        classification=classification,
        suggested_agent=best_agent.tag,
        agent_name=best_agent.name,
        confidence=round(confidence, 2),
        reasoning=reasoning,
        alternatives=alternatives,
        risk_warnings=risk_warnings,
    )


def _check_failure_museum(query: str, project_root: Path) -> list[str]:
    """Consulte le failure-museum pour détecter les risques."""
    museum_path = project_root / "framework" / "tools" / "failure-museum.py"
    if not museum_path.exists():
        return []
    try:
        spec = importlib.util.spec_from_file_location("failure_museum", museum_path)
        if not spec or not spec.loader:
            return []
        mod = importlib.util.module_from_spec(spec)
        sys.modules["failure_museum"] = mod
        spec.loader.exec_module(mod)

        entries = mod.load_failures(project_root)
        if not entries:
            return []

        desc_words = set(_normalize(query).split())
        warnings = []
        for entry in entries:
            corpus = _normalize(f"{entry.description} {entry.root_cause} {entry.rule_added}")
            corpus_words = set(corpus.split())
            overlap = len(desc_words & corpus_words)
            if overlap >= 2:
                warnings.append(f"⚠️ [{entry.failure_id}] {entry.title} — {entry.rule_added}")
        return warnings[:3]
    except Exception as exc:
        _log.debug("failure-museum check failed: %s", exc)
        return []


# ── Display ──────────────────────────────────────────────────────────────────


def display_triage(result: TriageResult) -> None:
    """Affiche le résultat du triage."""
    icons = {"simple": "🟢", "complex": "🟠", "ambiguous": "🔵"}
    conf_icon = "🟢" if result.confidence >= 0.5 else "🟡" if result.confidence >= 0.3 else "🔴"

    print("\n🎩 Concierge — Triage")
    print("=" * 50)
    print(f"  Requête    : {result.query}")
    print(f"  Complexité : {icons.get(result.classification, '⚪')} {result.classification}")
    print(f"  Confiance  : {conf_icon} {result.confidence:.0%}")
    print()
    print(f"  ➡️  Agent recommandé : {result.agent_name} ({result.suggested_agent})")
    print(f"     {result.reasoning}")

    if result.alternatives:
        print("\n  Alternatives :")
        for alt in result.alternatives:
            print(f"    - {alt['name']} ({alt['agent']}) — score {alt['score']:.0%}")

    if result.risk_warnings:
        print("\n  ⚠️  Risques connus :")
        for warn in result.risk_warnings:
            print(f"    {warn}")

    if result.confidence < 0.3:
        print("\n  💡 Confiance basse — je recommande de préciser la demande.")


def display_agents() -> None:
    """Affiche la liste des agents disponibles."""
    print("\n🎩 Agents disponibles")
    print("=" * 60)
    for agent in AGENT_PROFILES:
        print(f"  {agent.tag:25s} {agent.name:10s} — {agent.use_when}")


# ── MCP Tool Interface ──────────────────────────────────────────────────────


def mcp_concierge_triage(
    project_root: str,
    query: str = "",
) -> dict:
    """MCP tool ``bmad_concierge_triage`` — trie une requête et suggère un agent.

    Args:
        project_root: Racine du projet.
        query: La requête utilisateur à analyser.

    Returns:
        dict avec ``suggested_agent``, ``confidence``, ``classification``, etc.
    """
    if not query:
        return {"status": "error", "error": "query required"}
    root = Path(project_root)
    result = triage(query, root)
    return {"status": "ok", **asdict(result)}


def mcp_concierge_agents(project_root: str) -> dict:
    """MCP tool ``bmad_concierge_agents`` — liste les agents et leurs forces.

    Returns:
        dict avec la liste des agents.
    """
    agents = [
        {"tag": a.tag, "name": a.name, "use_when": a.use_when}
        for a in AGENT_PROFILES
    ]
    return {"status": "ok", "agents": agents}


# ── CLI ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="concierge",
        description="Concierge — Triage et routage intelligent BMAD",
    )
    p.add_argument("--project-root", type=Path, default=Path("."))
    p.add_argument("--json", action="store_true")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {CONCIERGE_VERSION}")

    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("triage", help="Analyser et router une requête")
    t.add_argument("--query", required=True, help="La requête à trier")

    sub.add_parser("agents", help="Lister les agents disponibles")

    cr = sub.add_parser("check-risk", help="Consulter le failure-museum")
    cr.add_argument("--query", required=True, help="Description de la tâche")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = args.project_root.resolve()

    if args.command == "triage":
        result = triage(args.query, root)
        if getattr(args, "json", False):
            print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
        else:
            display_triage(result)
        return 0

    if args.command == "agents":
        if getattr(args, "json", False):
            data = [asdict(a) for a in AGENT_PROFILES]
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            display_agents()
        return 0

    if args.command == "check-risk":
        warnings = _check_failure_museum(args.query, root)
        if warnings:
            print("⚠️  Risques détectés :")
            for w in warnings:
                print(f"  {w}")
            return 1
        print("✅ Aucun risque historique détecté.")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
