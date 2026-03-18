#!/usr/bin/env python3
"""
Grimoire DNA Evolution Engine — BM-56
===================================
La DNA d'un archétype est définie à l'init et reste statique.
Ce tool analyse ce que le projet a réellement livré et proposé
pour faire évoluer la DNA vers ce qui est réellement pratiqué.

Philosophie :
  DNA actuelle = ce qu'on *voulait* que le projet soit
  DNA évoluée  = ce que le projet *est vraiment* en ce moment

Sources analysées :
  1. Grimoire_TRACE.md — outils réellement exécutés, agents actifs, patterns
  2. decisions-log.md — décisions architecturales récurrentes
  3. agent-learnings-*.md — apprentissages cumulés par les agents
  4. archetype.dna.yaml courant — pour calculer le diff

Sorties :
  _grimoire-output/dna-proposals/archetype.dna.patch.yaml  (propositions diff)
  _grimoire-output/dna-proposals/dna-evolution-report.md   (rapport lisible)

Usage:
    python3 dna-evolve.py                               # Analyser + proposer
    python3 dna-evolve.py --apply                       # Appliquer le dernier patch
    python3 dna-evolve.py --report                      # Rapport seul (sans patch)
    python3 dna-evolve.py --since 2026-01-01            # Analyser depuis une date
    python3 dna-evolve.py --dna archetypes/web-app/archetype.dna.yaml
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_log = logging.getLogger("grimoire.dna_evolve")

# ── Structures ────────────────────────────────────────────────────────────────

@dataclass
class ObservedTool:
    """Outil observé dans le TRACE."""
    name: str
    count: int = 0
    agents: set[str] = field(default_factory=set)
    last_seen: str = ""


@dataclass
class ObservedPattern:
    """Pattern comportemental observé dans les sources."""
    pattern_id: str
    source: str         # "trace" | "decisions" | "learnings"
    description: str
    occurrences: int = 1
    evidence: list[str] = field(default_factory=list)


@dataclass
class DNAMutation:
    """Une mutation proposée pour la DNA."""
    mutation_type: str  # "add_tool" | "deprecate_tool" | "add_trait" | "add_constraint" | "add_value"
    target_section: str # "tools_required" | "traits" | "constraints" | "values"
    item_id: str
    description: str
    rationale: str
    evidence_count: int = 0
    confidence: str = "medium"  # "low" | "medium" | "high"


@dataclass
class DNASnapshot:
    """Snapshot de la DNA courante."""
    source_path: Path
    archetype_id: str
    version: str
    tools: list[str] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    values: list[str] = field(default_factory=list)
    raw_content: str = ""


# ── Parsing DNA YAML (minimal, sans dépendances) ─────────────────────────────

def parse_dna(dna_path: Path) -> DNASnapshot:
    """Parse minimalement un fichier archetype.dna.yaml."""
    if not dna_path.exists():
        return DNASnapshot(source_path=dna_path, archetype_id="unknown", version="1.0.0")

    raw = dna_path.read_text(encoding="utf-8", errors="replace")
    snap = DNASnapshot(source_path=dna_path, archetype_id="unknown", version="1.0.0", raw_content=raw)

    # Extraire archetype_id
    m = re.search(r"^id:\s*['\"]?([^'\"\n]+)['\"]?", raw, re.MULTILINE)
    if m:
        snap.archetype_id = m.group(1).strip()

    m = re.search(r"^version:\s*['\"]?([^'\"\n]+)['\"]?", raw, re.MULTILINE)
    if m:
        snap.version = m.group(1).strip()

    # Extraire tools (name: sous tools_required:)
    tools_section = re.search(r"tools_required:\s*\n((?:(?:  - |\s{4,}).*\n)*)", raw)
    if tools_section:
        snap.tools = re.findall(r"name:\s*['\"]?([^'\"\n]+)['\"]?", tools_section.group(0))

    # Extraire traits (name: dans la section traits:)
    traits_section = re.search(r"^traits:\s*\n((?:\s+-.*\n(?:\s+\S.*\n)*)*)", raw, re.MULTILINE)
    if traits_section:
        snap.traits = re.findall(r"name:\s*['\"]?([^'\"\n]+)['\"]?", traits_section.group(0))

    # Extraire constraints
    constraints_section = re.search(r"^constraints:\s*\n((?:\s+-.*\n(?:\s+\S.*\n)*)*)", raw, re.MULTILINE)
    if constraints_section:
        snap.constraints = re.findall(r"id:\s*['\"]?([^'\"\n]+)['\"]?", constraints_section.group(0))

    # Extraire values
    values_section = re.search(r"^values:\s*\n((?:\s+-.*\n(?:\s+\S.*\n)*)*)", raw, re.MULTILINE)
    if values_section:
        snap.values = re.findall(r"name:\s*['\"]?([^'\"\n]+)['\"]?", values_section.group(0))

    return snap


# ── Analyse Grimoire_TRACE ────────────────────────────────────────────────────────

# Commandes/outils courants à détecter dans le TRACE
KNOWN_TOOLS_PATTERNS = {
    # Conteneurs
    "docker":      r"\bdocker\b(?:\s+\w+)?",
    "podman":      r"\bpodman\b",
    "kubectl":     r"\bkubectl\b",
    "helm":        r"\bhelm\b(?:\s+\w+)?",
    # CI/CD
    "gh":          r"\bgh\b(?:\s+\w+)?",
    "act":         r"\bact\b(?:\s+-)",
    "terraform":   r"\bterraform\b",
    "ansible":     r"\bansible\b",
    # Tests
    "pytest":      r"\bpytest\b",
    "jest":        r"\bjest\b",
    "vitest":      r"\bvitest\b",
    "playwright":  r"\bplaywright\b",
    "k6":          r"\bk6\b",
    # Qualité code
    "eslint":      r"\beslint\b",
    "ruff":        r"\bruff\b",
    "black":       r"\bblack\b(?:\s+\w+)?",
    "mypy":        r"\bmypy\b",
    "shellcheck":  r"\bshellcheck\b",
    "golangci-lint": r"\bgolangci-lint\b",
    # Sécurité
    "trivy":       r"\btrivy\b",
    "grype":       r"\bgrype\b",
    "semgrep":     r"\bsemgrep\b",
    "gitleaks":    r"\bgitleaks\b",
    # Bases de données
    "psql":        r"\bpsql\b",
    "redis-cli":   r"\bredis-cli\b",
    "mongosh":     r"\bmongosh\b",
    # Data
    "dbt":         r"\bdbt\b",
    # Infra
    "packer":      r"\bpacker\b",
    "vault":       r"\bvault\b",
    # Python tools
    "uv":          r"\buv\b(?:\s+\w+)?",
    "pip":         r"\bpip\b(?:\s+\w+)?",
    "poetry":      r"\bpoetry\b",
    # Node
    "pnpm":        r"\bpnpm\b",
    "bun":         r"\bbun\b(?:\s+\w+)?",
    # Go
    "go":          r"\bgo\b(?:\s+(?:build|test|run|mod|get))\b",
    # Rust
    "cargo":       r"\bcargo\b",
    # Monitoring
    "prometheus":  r"\bprometheus\b",
    "grafana":     r"\bgrafana\b",
    # Builder
    "make":        r"\bmake\b(?:\s+\w+)?",
    "just":        r"\bjust\b(?:\s+\w+)?",
    "task":        r"\btask\b(?:\s+\w+)?",
}

# Patterns de traits comportementaux dans le TRACE
BEHAVIORAL_PATTERNS = {
    "tdd-first": (r"\[TDD\]|\bwrite tests? first\b|test.first.*implement", "Test-Driven Development observé"),
    "adr-usage": (r"\[ADR\]|Architecture Decision Record|decisions-log", "ADR régulièrement produits"),
    "plan-act-frequent": (r"\[PLAN\].*\[ACT\]|\[ACT\].*\[PLAN\]", "Mode Plan/Act fréquemment utilisé"),
    "agent-handoff": (r"\[.*→.*\]|inter-agent|handoff", "Handoffs inter-agents fréquents"),
    "checkpoint-heavy": (r"\[CHECKPOINT\]|ckpt-", "Checkpoints fréquents — sessions longues"),
    "failure-recovery": (r"\[FAILURE\].*\[RETRY\]|\[RETRY\]", "Patterns de retry — robustesse nécessaire"),
    "semantic-search": (r"qdrant|semantic|vector|embedding", "Recherche sémantique active"),
}

def analyze_trace(
    trace_path: Path,
    since: str | None = None,
) -> tuple[dict[str, ObservedTool], list[ObservedPattern]]:
    """
    Analyse Grimoire_TRACE.md et retourne :
    - Outils observés avec fréquences
    - Patterns comportementaux
    """
    tools: dict[str, ObservedTool] = {}
    patterns: list[ObservedPattern] = []

    if not trace_path.exists():
        return tools, patterns

    content = trace_path.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()

    # Filtrer par date si --since
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            filtered_lines = []
            include = False
            for line in lines:
                dm = re.search(r"\d{4}-\d{2}-\d{2}", line)
                if dm:
                    try:
                        line_dt = datetime.fromisoformat(dm.group(0))
                        include = line_dt >= since_dt
                    except ValueError as _exc:
                        _log.debug("ValueError suppressed: %s", _exc)
                        # Silent exception — add logging when investigating issues
                if include:
                    filtered_lines.append(line)
            lines = filtered_lines or lines
        except ValueError as _exc:
            _log.debug("ValueError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    # Extraire le contexte d'agent courant
    current_agent = "unknown"

    for line in lines:
        # Détecter l'agent actif
        agent_m = re.search(r"\[AGENT:([^\]]+)\]|agent:\s*([a-z-]+)", line, re.IGNORECASE)
        if agent_m:
            current_agent = (agent_m.group(1) or agent_m.group(2) or current_agent).strip()

        # Détecter les outils
        for tool_name, pattern in KNOWN_TOOLS_PATTERNS.items():
            if re.search(pattern, line, re.IGNORECASE):
                if tool_name not in tools:
                    tools[tool_name] = ObservedTool(name=tool_name)
                tools[tool_name].count += 1
                tools[tool_name].agents.add(current_agent)
                # Mémorise la date de dernière utilisation
                date_m = re.search(r"\d{4}-\d{2}-\d{2}", line)
                if date_m:
                    tools[tool_name].last_seen = date_m.group(0)

    # Analyser les patterns comportementaux sur le contenu complet
    for pattern_id, (regex, description) in BEHAVIORAL_PATTERNS.items():
        matches = re.findall(regex, content, re.IGNORECASE)
        if len(matches) >= 3:  # Seuil : au moins 3 occurrences
            evidence = [m if isinstance(m, str) else m[0] for m in matches[:3]]
            patterns.append(ObservedPattern(
                pattern_id=pattern_id,
                source="trace",
                description=description,
                occurrences=len(matches),
                evidence=evidence,
            ))

    return tools, patterns


# ── Analyse decisions-log.md ──────────────────────────────────────────────────

def analyze_decisions_log(decisions_path: Path) -> list[ObservedPattern]:
    """Extrait les patterns de décisions récurrentes."""
    patterns: list[ObservedPattern] = []

    if not decisions_path.exists():
        return patterns

    content = decisions_path.read_text(encoding="utf-8", errors="replace")

    # Catégories de décisions récurrentes
    decision_categories = {
        "security-first": (r"sécurité|security|vulnérabilité|CVE", "Décisions orientées sécurité fréquentes"),
        "performance-focus": (r"perf|latence|throughput|optimis", "Optimisation perf décision récurrente"),
        "api-contract": (r"contrat|contract|versioning|breaking.change|OpenAPI", "Contrats API thème récurrent"),
        "observability": (r"tracing|observabilit|métriques|alertes", "Observabilité décision récurrente"),
        "data-quality": (r"qualité.données|data.quality|validation.schema|dbt", "Qualité données thème récurrent"),
        "cost-control": (r"coût|cost|budget|\$|tarification", "Contrôle des coûts décision récurrente"),
    }

    for cat_id, (regex, description) in decision_categories.items():
        matches = re.findall(regex, content, re.IGNORECASE)
        if len(matches) >= 2:
            patterns.append(ObservedPattern(
                pattern_id=cat_id,
                source="decisions",
                description=description,
                occurrences=len(matches),
            ))

    return patterns


# ── Analyse agent-learnings ───────────────────────────────────────────────────

def analyze_learnings(memory_dir: Path) -> list[ObservedPattern]:
    """Extrait les patterns récurrents dans les fichiers agent-learnings."""
    patterns: list[ObservedPattern] = []
    if not memory_dir.exists():
        return patterns

    all_learnings = "\n".join(
        f.read_text(encoding="utf-8", errors="replace")
        for f in memory_dir.glob("*learnings*.md")
        if f.exists()
    )
    if not all_learnings:
        return patterns

    # Patterns de frustration récurrents = opportunités DNA
    frustration_patterns = {
        "missing-tool": (r"outil.manquant|tool.not.found|command not found|manque.*outil", "Outils manquants récurrents → ajouter dans DNA tools_required"),
        "context-loss": (r"contexte.perdu|context.loss|recharg|reload.context", "Pertes de contexte → renforcer mémoire"),
        "scope-unclear": (r"scope.flou|hors.périmètre|pas.mon.rôle|out of scope", "Scope d'agents peu clair → contraintes DNA"),
        "ac-ambiguous": (r"AC.*flou|critère.*vague|undefined.*acceptance", "Acceptance criteria ambigus → traits DNA"),
    }

    for pat_id, (regex, description) in frustration_patterns.items():
        matches = re.findall(regex, all_learnings, re.IGNORECASE)
        if matches:
            patterns.append(ObservedPattern(
                pattern_id=pat_id,
                source="learnings",
                description=description,
                occurrences=len(matches),
            ))

    return patterns


# ── Génération des mutations proposées ───────────────────────────────────────

MIN_OCCURRENCES_FOR_TOOL = 5   # Un outil doit apparaître ≥5x pour être proposé en DNA
MIN_OCCURRENCES_FOR_TRAIT = 3  # Un pattern doit apparaître ≥3x pour être proposé en trait


def generate_mutations(
    dna: DNASnapshot,
    observed_tools: dict[str, ObservedTool],
    patterns: list[ObservedPattern],
) -> list[DNAMutation]:
    """Génère les mutations DNA proposées."""
    mutations: list[DNAMutation] = []
    existing_tools_lower = {t.lower() for t in dna.tools}
    existing_traits_lower = {t.lower() for t in dna.traits}
    existing_constraints_lower = {c.lower() for c in dna.constraints}

    # ── 1. Outils fréquents non présents dans la DNA ──────────────────────
    for tool_name, tool_data in sorted(observed_tools.items(), key=lambda x: x[1].count, reverse=True):
        if tool_name.lower() in existing_tools_lower:
            continue
        if tool_data.count < MIN_OCCURRENCES_FOR_TOOL:
            continue

        confidence = "high" if tool_data.count >= 15 else ("medium" if tool_data.count >= 8 else "low")
        agents_str = ", ".join(sorted(tool_data.agents)[:3])

        mutations.append(DNAMutation(
            mutation_type="add_tool",
            target_section="tools_required",
            item_id=tool_name,
            description=f"{tool_name} — utilisé {tool_data.count}x par [{agents_str}]",
            rationale=f"Observé {tool_data.count} fois dans Grimoire_TRACE "
                      f"(dernière utilisation: {tool_data.last_seen or 'récent'}). "
                      f"Non déclaré dans la DNA — rend l'install implicite.",
            evidence_count=tool_data.count,
            confidence=confidence,
        ))

    # ── 2. Outils déclarés dans DNA mais jamais vus dans TRACE (candidates à dépréciation) ──
    if observed_tools:  # Seulement si on a assez de données TRACE
        trace_total_entries = sum(t.count for t in observed_tools.values())
        if trace_total_entries >= 20:  # Assez de TRACE pour déprécier
            for dna_tool in dna.tools:
                if dna_tool.lower() not in observed_tools and dna_tool.lower() not in {
                    "bash", "git", "python3"
                }:
                    mutations.append(DNAMutation(
                        mutation_type="deprecate_tool",
                        target_section="tools_required",
                        item_id=dna_tool,
                        description=f"{dna_tool} — déclaré en DNA mais absent des {trace_total_entries} entrées TRACE",
                        rationale="L'outil est déclaré comme requis mais n'apparaît jamais dans l'historique d'activité. "
                                  "Soit il n'est pas utilisé, soit il n'est pas tracé. "
                                  "Envisager de déplacer en 'optional' ou de supprimer.",
                        evidence_count=0,
                        confidence="low",  # Prudent par défaut
                    ))

    # ── 3. Patterns comportementaux → nouveaux traits ─────────────────────
    trait_map = {
        "tdd-first": ("tdd-enforced", "traits",
                      "TDD comme pratique par défaut",
                      "Tests écrits avant l'implémentation — pattern observé systématiquement"),
        "adr-usage": ("adr-required", "traits",
                      "ADRs obligatoires pour les décisions architecturales",
                      "Toute décision ayant un impact >1 semaine produit un ADR dans decisions-log.md"),
        "checkpoint-heavy": ("checkpoint-dense", "traits",
                             "Checkpoints Grimoire denses pour longues sessions",
                             "Sessions longues → checkpoint toutes les 90 minutes ou après chaque étape majeure"),
        "failure-recovery": ("retry-protocol", "constraints",
                             "Protocole de retry obligatoire sur les failures",
                             "Après [FAILURE] : analyser la cause, documenter dans failure-museum.md, retenter"),
        "security-first": ("security-gate", "traits",
                           "Gate sécurité systématique",
                           "Toute feature exposant une surface réseau passe par un scan sécurité avant merge"),
        "api-contract": ("contract-first", "traits",
                         "Contract-first pour les APIs",
                         "OpenAPI/gRPC schema défini AVANT l'implémentation — jamais l'inverse"),
        "observability": ("observability-required", "constraints",
                          "Observabilité obligatoire en production",
                          "Toute feature prod inclut : traces, métriques, alertes dans son AC"),
    }

    for pattern in patterns:
        if pattern.occurrences < MIN_OCCURRENCES_FOR_TRAIT:
            continue
        if pattern.pattern_id not in trait_map:
            continue

        trait_id, section, name, _rule = trait_map[pattern.pattern_id]

        if trait_id.lower() in existing_traits_lower or trait_id.lower() in existing_constraints_lower:
            continue

        confidence = "high" if pattern.occurrences >= 10 else ("medium" if pattern.occurrences >= 5 else "low")

        mutations.append(DNAMutation(
            mutation_type=f"add_{'trait' if section == 'traits' else 'constraint'}",
            target_section=section,
            item_id=trait_id,
            description=f"{name}",
            rationale=f"{pattern.description}. Observé {pattern.occurrences}x dans {pattern.source}. "
                      + (f"Exemples: {', '.join(pattern.evidence[:2])}" if pattern.evidence else ""),
            evidence_count=pattern.occurrences,
            confidence=confidence,
        ))

    # ── 4. Triées par confiance puis fréquence ────────────────────────────
    order = {"high": 0, "medium": 1, "low": 2}
    mutations.sort(key=lambda m: (order[m.confidence], -m.evidence_count))

    return mutations


# ── Génération des fichiers de sortie ─────────────────────────────────────────

PATCH_HEADER = """\
# Grimoire DNA Evolution Patch — Généré par dna-evolve.py / BM-56
# Source DNA : {dna_path}
# Archétype  : {archetype_id} v{version}
# Généré le  : {date}
#
# Révision OBLIGATOIRE avant application.
# Appliquer via : python3 dna-evolve.py --apply
#
# Confiance : high = fortement recommandé | medium = à évaluer | low = à confirmer

"""

def render_patch_yaml(dna: DNASnapshot, mutations: list[DNAMutation]) -> str:
    """Génère le fichier de patch YAML."""
    lines = [PATCH_HEADER.format(
        dna_path=dna.source_path,
        archetype_id=dna.archetype_id,
        version=dna.version,
        date=datetime.now(UTC).strftime("%Y-%m-%d"),
    )]

    # Grouper par section
    by_section: dict[str, list[DNAMutation]] = {}
    for m in mutations:
        by_section.setdefault(m.target_section, []).append(m)

    for section, section_mutations in by_section.items():
        adds = [m for m in section_mutations if "add" in m.mutation_type]
        deprecates = [m for m in section_mutations if "deprecate" in m.mutation_type]

        if adds:
            lines.append(f"\n# ── Ajouts proposés : {section} {'─' * 40}\n")
            lines.append(f"{section}_ADD:\n")
            for m in adds:
                lines.append(f"  # [{m.confidence.upper()}] confidence — {m.evidence_count} occurrences")
                lines.append(f"  - id: {m.item_id}")
                lines.append(f'    description: "{m.description}"')
                if section == "tools_required":
                    lines.append("    required: true")
                    lines.append(f'    check_command: "which {m.item_id}"')
                elif section == "traits":
                    lines.append(f'    rule: "[TODO] Affiner la règle : {m.description}"')
                    lines.append("    agents_affected: \"*\"")
                elif section == "constraints":
                    lines.append("    enforcement: soft")
                    lines.append("    checked_by: agent-optimizer")
                lines.append(f"    # Rationale: {m.rationale[:100]}")
                lines.append("")

        if deprecates:
            lines.append(f"\n# ── Dépréciations proposées : {section} {'─' * 35}\n")
            lines.append(f"{section}_DEPRECATE:\n")
            for m in deprecates:
                lines.append(f"  # [{m.confidence.upper()}] confidence — {m.rationale[:80]}")
                lines.append(f"  - id: {m.item_id}")
                lines.append('    action: "move_to_optional  # ou: remove"')
                lines.append("")

    return "\n".join(lines)


def render_report_md(
    dna: DNASnapshot,
    mutations: list[DNAMutation],
    observed_tools: dict[str, ObservedTool],
    patterns: list[ObservedPattern],
) -> str:
    """Génère le rapport Markdown lisible."""
    date = datetime.now(UTC).strftime("%Y-%m-%d")
    adds = [m for m in mutations if "add" in m.mutation_type]
    deps = [m for m in mutations if "deprecate" in m.mutation_type]

    lines = [
        f"# DNA Evolution Report — {dna.archetype_id}",
        f"\n> Généré le {date} par `dna-evolve.py` (BM-56)\n",
        f"**DNA source** : `{dna.source_path}`  ",
        f"**Version actuelle** : {dna.version}  ",
        f"**Mutations proposées** : {len(adds)} ajouts, {len(deps)} dépréciations  ",
        "",
        "---",
        "",
        "## Résumé",
        "",
        "| Metric | Valeur |",
        "|--------|--------|",
        f"| Outils observés dans TRACE | {len(observed_tools)} |",
        f"| Patterns comportementaux | {len(patterns)} |",
        f"| Ajouts proposés (HIGH) | {sum(1 for m in adds if m.confidence == 'high')} |",
        f"| Ajouts proposés (MEDIUM) | {sum(1 for m in adds if m.confidence == 'medium')} |",
        f"| Dépréciations proposées | {len(deps)} |",
        "",
        "---",
        "",
    ]

    if adds:
        lines += ["## Ajouts proposés", ""]
        for m in adds:
            conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(m.confidence, "⚪")
            lines.append(f"### {conf_icon} `{m.item_id}` → `{m.target_section}`")
            lines.append(f"\n**{m.description}**\n")
            lines.append(f"> {m.rationale}\n")
            lines.append(f"- Confiance : **{m.confidence}**  ")
            lines.append(f"- Occurrences : **{m.evidence_count}**  ")
            lines.append("")

    if deps:
        lines += ["## Dépréciations proposées", ""]
        for m in deps:
            lines.append(f"### ⚠️ `{m.item_id}` — à déprécier")
            lines.append(f"\n> {m.rationale}\n")
            lines.append("")

    if observed_tools:
        lines += ["## Top outils observés (TRACE)", ""]
        lines.append("| Outil | Occurrences | Agents | Dernière utilisation |")
        lines.append("|-------|------------|--------|----------------------|")
        for tool in sorted(observed_tools.values(), key=lambda t: t.count, reverse=True)[:15]:
            agents = ", ".join(sorted(tool.agents)[:3])
            in_dna = "✅" if tool.name in [t.lower() for t in dna.tools] else "➕"
            lines.append(f"| {in_dna} `{tool.name}` | {tool.count} | {agents} | {tool.last_seen or '-'} |")
        lines.append("")

    if patterns:
        lines += ["## Patterns comportementaux détectés", ""]
        for p in patterns:
            source_icon = {"trace": "📋", "decisions": "📝", "learnings": "🧠"}.get(p.source, "📄")
            lines.append(f"- {source_icon} **{p.pattern_id}** ({p.occurrences}x) — {p.description}")
        lines.append("")

    lines += [
        "---",
        "",
        "## Prochaines étapes",
        "",
        "1. Réviser ce rapport — valider chaque mutation",
        "2. Éditer `archetype.dna.patch.yaml` si besoin",
        "3. Appliquer : `python3 dna-evolve.py --apply`",
        "4. Commit : `git add archetypes/*/archetype.dna.yaml && git commit -m 'feat: DNA evolution'`",
        "",
        "> Cette évolution rend la DNA **honnête** vis-à-vis de ce que le projet pratique réellement.",
    ]

    return "\n".join(lines)


# ── Application d'un patch ────────────────────────────────────────────────────

def apply_patch(patch_path: Path, dna: DNASnapshot) -> None:
    """
    Applique un patch DNA après review humain.
    Stratégie : append les nouvelles sections en YAML commenté à approuver manuellement.
    """
    if not patch_path.exists():
        print(f"❌ Patch introuvable : {patch_path}", file=sys.stderr)
        sys.exit(1)

    patch_content = patch_path.read_text(encoding="utf-8", errors="replace")

    # Extraire les items ADD validés (non commentés avec '#')
    add_tools = re.findall(r"^  - id: ([\w-]+)$.*?check_command:", patch_content, re.MULTILINE | re.DOTALL)

    if not add_tools:
        print("ℹ️  Aucun outil ADD non-commenté trouvé dans le patch.")
        print("   Éditer le patch et retirer les '#' des items à appliquer.")
        return

    print(f"✅ {len(add_tools)} outil(s) à ajouter à {dna.source_path}")
    for t in add_tools:
        if t in dna.tools:
            print(f"   ⚠️  {t} déjà dans la DNA — ignoré")
        else:
            print(f"   ➕ {t}")

    # Backup
    backup = dna.source_path.with_suffix(".dna.yaml.bak")
    backup.write_text(dna.raw_content, encoding="utf-8")
    print(f"\n   Backup : {backup}")
    print(f"   Éditez manuellement {dna.source_path} pour appliquer les changements.")
    print("   Conseil : ouvrir patch + DNA côte à côte dans VS Code.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grimoire DNA Evolution Engine — fait évoluer la DNA d'un archétype depuis l'usage réel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  python3 dna-evolve.py
  python3 dna-evolve.py --report
  python3 dna-evolve.py --since 2026-01-01
  python3 dna-evolve.py --dna archetypes/infra-ops/archetype.dna.yaml
  python3 dna-evolve.py --apply
        """,
    )

    parser.add_argument("--dna", metavar="PATH",
                        help="Chemin vers archetype.dna.yaml (auto-détection si absent)")
    parser.add_argument("--trace", metavar="PATH",
                        default="_grimoire-output/Grimoire_TRACE.md")
    parser.add_argument("--decisions", metavar="PATH",
                        default="_grimoire/_memory/decisions-log.md")
    parser.add_argument("--memory-dir", metavar="PATH",
                        default="_grimoire/_memory")
    parser.add_argument("--out-dir", metavar="PATH",
                        default="_grimoire-output/dna-proposals")
    parser.add_argument("--project-root", metavar="PATH", default=".")
    parser.add_argument("--since", metavar="YYYY-MM-DD",
                        help="Analyser seulement depuis cette date")
    parser.add_argument("--report", action="store_true",
                        help="Générer seulement le rapport (pas de patch)")
    parser.add_argument("--apply", action="store_true",
                        help="Appliquer le dernier patch généré")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    # ── Auto-détecter la DNA ──────────────────────────────────────────────
    dna_path: Path | None = None
    if args.dna:
        dna_path = Path(args.dna)
    else:
        # Chercher la DNA active (priorité au dernier archétype installé)
        candidates = list(project_root.glob("archetypes/*/archetype.dna.yaml"))
        if not candidates:
            candidates = list((project_root / "archetypes").glob("**/*.dna.yaml"))
        if candidates:
            # Exclure les archetypes stack/ multi-fichiers — prendre le plus récent
            main_candidates = [c for c in candidates if "stack" not in str(c.parent)]
            dna_path = (main_candidates or candidates)[0]

    if not dna_path or not dna_path.exists():
        print("❌ Aucun fichier archetype.dna.yaml trouvé.", file=sys.stderr)
        print("   Spécifier avec --dna ou initialiser un archétype d'abord.")
        sys.exit(1)

    dna = parse_dna(dna_path)
    print(f"\n  📐 DNA source : {dna_path.relative_to(project_root) if dna_path.is_relative_to(project_root) else dna_path}")
    print(f"     Archétype  : {dna.archetype_id} v{dna.version}")
    print(f"     Outils DNA : {len(dna.tools)}  |  Traits: {len(dna.traits)}  |  Contraintes: {len(dna.constraints)}")

    # ── apply mode ────────────────────────────────────────────────────────
    if args.apply:
        out_dir = project_root / args.out_dir
        patches = sorted(out_dir.glob("archetype.dna.patch*.yaml")) if out_dir.exists() else []
        if not patches:
            print("❌ Aucun patch trouvé dans", out_dir)
            sys.exit(1)
        apply_patch(patches[-1], dna)
        return

    # ── Analyse ──────────────────────────────────────────────────────────
    print()
    print("  Analyse en cours...")

    trace_path = project_root / args.trace
    decisions_path = project_root / args.decisions
    memory_dir = project_root / args.memory_dir

    observed_tools, trace_patterns = analyze_trace(trace_path, args.since)
    decision_patterns = analyze_decisions_log(decisions_path)
    learning_patterns = analyze_learnings(memory_dir)

    all_patterns = trace_patterns + decision_patterns + learning_patterns
    print(f"  → {len(observed_tools)} outils observés dans TRACE")
    print(f"  → {len(all_patterns)} patterns comportementaux")

    # ── Générer les mutations ──────────────────────────────────────────────
    mutations = generate_mutations(dna, observed_tools, all_patterns)
    adds = [m for m in mutations if "add" in m.mutation_type]
    deps = [m for m in mutations if "deprecate" in m.mutation_type]

    print(f"  → {len(adds)} ajouts proposés, {len(deps)} dépréciations")

    if not mutations:
        print()
        print("  ✅ Aucune mutation nécessaire — la DNA reflète bien l'usage actuel.")
        print()
        return

    # ── Sauvegarder les outputs ────────────────────────────────────────────
    out_dir = project_root / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now(UTC).strftime("%Y%m%d")

    if not args.report:
        patch_content = render_patch_yaml(dna, mutations)
        patch_path = out_dir / f"archetype.dna.patch.{date_str}.yaml"
        patch_path.write_text(patch_content, encoding="utf-8")
        print(f"\n  📋 Patch : {patch_path.relative_to(project_root)}")

    report_content = render_report_md(dna, mutations, observed_tools, all_patterns)
    report_path = out_dir / f"dna-evolution-report.{date_str}.md"
    report_path.write_text(report_content, encoding="utf-8")
    print(f"  📄 Rapport : {report_path.relative_to(project_root)}")

    # ── Résumé ────────────────────────────────────────────────────────────
    print()
    print("  ──────────────────────────────────────────────────")
    high = [m for m in adds if m.confidence == "high"]
    if high:
        print(f"  🟢 HIGH confidence ({len(high)}) — fortement recommandés :")
        for m in high[:4]:
            print(f"     + {m.item_id} → {m.target_section}  ({m.evidence_count}x dans TRACE)")
    med = [m for m in adds if m.confidence == "medium"]
    if med:
        print(f"  🟡 MEDIUM confidence ({len(med)}) — à évaluer")
    if deps:
        print(f"  ⚠️  {len(deps)} outil(s) à déprécier (absents du TRACE)")
    print()
    print("  Étapes suivantes :")
    if not args.report:
        print(f"  1. Réviser : {out_dir.relative_to(project_root)}/archetype.dna.patch.{date_str}.yaml")
    print(f"  2. Rapport détaillé : {out_dir.relative_to(project_root)}/dna-evolution-report.{date_str}.md")
    print("  3. Appliquer : python3 framework/tools/dna-evolve.py --apply")
    print(f"  4. Commit : git commit -m 'feat: DNA evolution {dna.archetype_id} — {len(adds)} ajouts'")
    print()


if __name__ == "__main__":
    main()
