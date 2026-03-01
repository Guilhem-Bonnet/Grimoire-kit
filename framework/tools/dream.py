#!/usr/bin/env python3
"""
dream.py — BMAD Dream Mode : consolidation hors-session et insights émergents.
==============================================================================

Simule une phase de "rêve" : les agents relisent learnings, decisions, trace,
failure museum et shared-context, puis produisent des insights cross-domaine
qu'aucun agent n'aurait formulés en session.

Mode read-only : aucun fichier n'est modifié. Les insights sont écrits dans
_bmad-output/dream-journal.md pour review humain.

Usage :
  python3 dream.py --project-root .                   # Dream complet
  python3 dream.py --project-root . --since 2026-01-01 # Depuis une date
  python3 dream.py --project-root . --agent dev        # Focus un agent
  python3 dream.py --project-root . --validate         # Valider les insights (no hallucination)
  python3 dream.py --project-root . --dry-run          # Preview sans écrire

Stdlib only — aucune dépendance externe.
"""

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

MAX_INSIGHTS = 12          # Plafond d'insights par dream
MIN_SOURCES = 2            # Un insight doit croiser ≥ 2 sources
SIMILARITY_THRESHOLD = 0.6 # Seuil de détection doublon
STALENESS_DAYS = 7         # Insight plus ancien = moindre poids
QUICK_MAX_INSIGHTS = 5     # Plafond en mode quick (O(n) seulement)
PERSISTENCE_BOOST = 0.15   # Bonus confiance pour insight persistant
DECAY_HALFLIFE_DAYS = 14   # Demi-vie pour la pondération temporelle
DREAM_MEMORY_FILE = "dream-memory.json"  # Historique structuré des insights
MAX_OPPORTUNITIES_PER_SOURCE = 3  # Cap par source pour éviter la saturation


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DreamSource:
    """Une source de données pour le dream."""
    name: str          # ex. "learnings/dev.md"
    kind: str          # learnings | decisions | trace | failure-museum | shared-context
    entries: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


@dataclass
class DreamInsight:
    """Un insight émergent produit par le dream."""
    title: str
    description: str
    sources: list[str]        # noms des fichiers sources
    category: str             # pattern | tension | opportunity | connection
    confidence: float         # 0.0 - 1.0
    agents_relevant: list[str] = field(default_factory=list)
    actionable: bool = False


# ── Collecte des sources ──────────────────────────────────────────────────────

def collect_sources(project_root: Path, since: str | None = None,
                    agent_filter: str | None = None) -> list[DreamSource]:
    """Collecte toutes les sources de mémoire du projet."""
    sources: list[DreamSource] = []
    memory_dir = project_root / "_bmad" / "_memory"

    # 1. Learnings
    learnings_dir = memory_dir / "agent-learnings"
    if learnings_dir.exists():
        for f in sorted(learnings_dir.glob("*.md")):
            if agent_filter and agent_filter.lower() not in f.stem.lower():
                continue
            entries = _parse_markdown_entries(f, since)
            if entries:
                sources.append(DreamSource(
                    name=f"learnings/{f.name}",
                    kind="learnings",
                    entries=[e[1] for e in entries],
                    dates=[e[0] for e in entries],
                ))

    # 2. Decisions log
    decisions_file = memory_dir / "decisions-log.md"
    if decisions_file.exists():
        entries = _parse_markdown_entries(decisions_file, since)
        if entries:
            sources.append(DreamSource(
                name="decisions-log.md",
                kind="decisions",
                entries=[e[1] for e in entries],
                dates=[e[0] for e in entries],
            ))

    # 3. BMAD_TRACE
    trace_file = project_root / "_bmad-output" / "BMAD_TRACE.md"
    if trace_file.exists():
        entries = _parse_trace_entries(trace_file, since, agent_filter)
        if entries:
            sources.append(DreamSource(
                name="BMAD_TRACE.md",
                kind="trace",
                entries=[e[1] for e in entries],
                dates=[e[0] for e in entries],
            ))

    # 4. Failure Museum
    failure_file = memory_dir / "failure-museum.md"
    if failure_file.exists():
        entries = _parse_markdown_entries(failure_file, since)
        if entries:
            sources.append(DreamSource(
                name="failure-museum.md",
                kind="failure-museum",
                entries=[e[1] for e in entries],
                dates=[e[0] for e in entries],
            ))

    # 5. Shared context
    shared_file = memory_dir / "shared-context.md"
    if shared_file.exists():
        content = shared_file.read_text(encoding="utf-8")
        sections = _parse_shared_context_sections(content)
        if sections:
            sources.append(DreamSource(
                name="shared-context.md",
                kind="shared-context",
                entries=sections,
            ))

    # 6. Contradiction log
    contradiction_file = memory_dir / "contradiction-log.md"
    if contradiction_file.exists():
        entries = _parse_markdown_entries(contradiction_file, since)
        if entries:
            sources.append(DreamSource(
                name="contradiction-log.md",
                kind="contradictions",
                entries=[e[1] for e in entries],
                dates=[e[0] for e in entries],
            ))

    # 7. Pheromone board (feedback loop — dream lit les signaux stigmergy)
    pheromone_entries = _parse_pheromone_board(project_root, since)
    if pheromone_entries:
        sources.append(DreamSource(
            name="pheromone-board.json",
            kind="stigmergy",
            entries=[e[1] for e in pheromone_entries],
            dates=[e[0] for e in pheromone_entries],
        ))

    return sources


def _parse_markdown_entries(path: Path, since: str | None = None) -> list[tuple[str, str]]:
    """Parse un fichier markdown et retourne [(date, text), ...]."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: list[tuple[str, str]] = []
    date_pattern = re.compile(r'\[(\d{4}-\d{2}-\d{2})')

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Chercher une date dans la ligne
        match = date_pattern.search(line)
        entry_date = match.group(1) if match else ""
        if since and entry_date and entry_date < since:
            continue
        if line.startswith("- ") or line.startswith("* "):
            entries.append((entry_date, line[2:].strip()))

    return entries


def _parse_trace_entries(path: Path, since: str | None = None,
                         agent_filter: str | None = None) -> list[tuple[str, str]]:
    """Parse BMAD_TRACE.md pour les entrées pertinentes."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: list[tuple[str, str]] = []
    trace_pattern = re.compile(
        r'\[(\d{4}-\d{2}-\d{2})[^\]]*\]\s*\[(\w+)\]\s*\[([^\]]+)\]\s*(.*)'
    )

    for line in content.splitlines():
        match = trace_pattern.match(line.strip())
        if not match:
            continue
        entry_date, level, agent, payload = match.groups()
        if since and entry_date < since:
            continue
        if agent_filter and agent_filter.lower() not in agent.lower():
            continue
        # Focus sur DECISION, CHECKPOINT, FAILURE
        if level in ("DECISION", "CHECKPOINT", "FAILURE", "REMEMBER"):
            entries.append((entry_date, f"[{agent}] [{level}] {payload}"))

    return entries


def _parse_shared_context_sections(content: str) -> list[str]:
    """Extrait les sections non-vides du shared-context."""
    sections: list[str] = []
    current = ""
    for line in content.splitlines():
        if line.startswith("## "):
            if current.strip():
                sections.append(current.strip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        sections.append(current.strip())
    return sections


def _parse_pheromone_board(project_root: Path,
                          since: str | None = None) -> list[tuple[str, str]]:
    """Parse le pheromone-board.json comme source pour le dream.

    Retourne [(date, text), ...] pour chaque phéromone active non-résolue.
    Filtre les phéromones émises par dream-mode pour éviter l'auto-référence,
    sauf celles qui ont été amplifiées (= feedback humain/agent).
    """
    board_path = project_root / "_bmad-output" / "pheromone-board.json"
    if not board_path.exists():
        return []
    try:
        data = json.loads(board_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    entries: list[tuple[str, str]] = []
    for p in data.get("pheromones", []):
        if p.get("resolved", False):
            continue

        # Skip dream's own pheromones UNLESS they got reinforced (= feedback signal)
        if p.get("emitter") == "dream-mode" and p.get("reinforcements", 0) == 0:
            continue

        # Date filter
        ts = p.get("timestamp", "")
        entry_date = ts[:10] if len(ts) >= 10 else ""
        if since and entry_date and entry_date < since:
            continue

        ptype = p.get("pheromone_type", "NEED")
        location = p.get("location", "?")
        text = p.get("text", "")
        emitter = p.get("emitter", "?")
        reinforced = p.get("reinforcements", 0)

        label = f"[{ptype}] @{location} by {emitter}"
        if reinforced > 0:
            label += f" (+{reinforced} reinforcements)"
        entry_text = f"{label}: {text}"
        entries.append((entry_date, entry_text))

    return entries


# ── Analyse et génération d'insights ──────────────────────────────────────────

_STOPWORDS = frozenset({
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "ou", "en",
    "à", "au", "aux", "pour", "par", "sur", "dans", "avec", "que", "qui",
    "est", "sont", "a", "ont", "sera", "seront", "pas", "ne", "ni", "mais",
    "the", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "of", "to", "in", "for",
    "on", "with", "at", "by", "from", "as", "into", "about", "between",
    "after", "before", "not", "no", "but", "or", "and", "if", "then",
    "than", "too", "very", "just", "don", "it", "its", "this", "that",
})


def _extract_keywords(text: str) -> set[str]:
    """Extrait les mots-clés significatifs d'un texte (unigrams + bigrams)."""
    words = re.findall(r'[a-zA-ZÀ-ÿ]{3,}', text.lower())
    significant = [w for w in words if w not in _STOPWORDS]

    # Unigrams
    result = set(significant)

    # Bigrams — paires de mots significatifs consécutifs dans le texte original
    # On itère sur les mots bruts pour garder la co-localité
    prev_sig: str | None = None
    for w in words:
        if w in _STOPWORDS:
            prev_sig = None
            continue
        if prev_sig is not None:
            result.add(f"{prev_sig}_{w}")
        prev_sig = w

    return result


# Agents connus pour l'attribution automatique
_KNOWN_AGENTS = frozenset({
    "dev", "architect", "pm", "qa", "sm", "analyst",
    "tech-writer", "ux-designer", "bmad-master",
})

_AGENT_PATTERN = re.compile(r'\[([a-z][a-z0-9_-]*)\]', re.IGNORECASE)


def _extract_agents(text: str) -> list[str]:
    """Extrait les noms d'agents mentionnés dans un texte.

    Cherche des patterns [agent-name] et les valide contre la liste connue.
    Aussi détecte les noms d'agents dans les noms de fichiers (learnings/dev.md).
    """
    agents: set[str] = set()

    # Pattern [agent] dans le texte (traces, pheromones)
    for m in _AGENT_PATTERN.finditer(text):
        candidate = m.group(1).lower()
        if candidate in _KNOWN_AGENTS:
            agents.add(candidate)

    # Nom de fichier agent (learnings/dev.md → dev)
    file_pattern = re.search(r'learnings/([a-z_-]+)\.md', text.lower())
    if file_pattern:
        candidate = file_pattern.group(1)
        if candidate in _KNOWN_AGENTS:
            agents.add(candidate)

    return sorted(agents)


def _similarity(text_a: str, text_b: str) -> float:
    """Similarité cosine simplifiée par overlap de keywords."""
    ka = _extract_keywords(text_a)
    kb = _extract_keywords(text_b)
    if not ka or not kb:
        return 0.0
    intersection = ka & kb
    union = ka | kb
    return len(intersection) / len(union) if union else 0.0


def find_cross_connections(sources: list[DreamSource]) -> list[DreamInsight]:
    """Trouve les connexions croisées entre sources différentes."""
    insights: list[DreamInsight] = []

    # Comparer chaque paire de sources de types DIFFÉRENTS
    for i, src_a in enumerate(sources):
        for j, src_b in enumerate(sources):
            if j <= i or src_a.kind == src_b.kind:
                continue
            for entry_a in src_a.entries:
                for entry_b in src_b.entries:
                    sim = _similarity(entry_a, entry_b)
                    if sim >= SIMILARITY_THRESHOLD:
                        # Connexion détectée !
                        agents = sorted(set(
                            _extract_agents(entry_a) +
                            _extract_agents(entry_b) +
                            _extract_agents(src_a.name) +
                            _extract_agents(src_b.name)
                        ))
                        insights.append(DreamInsight(
                            title=f"Connexion {src_a.kind} ↔ {src_b.kind}",
                            description=(
                                f"Pattern partagé entre [{src_a.name}] et [{src_b.name}] :\n"
                                f"  • {entry_a[:120]}...\n"
                                f"  • {entry_b[:120]}..."
                            ),
                            sources=[src_a.name, src_b.name],
                            category="connection",
                            confidence=round(sim, 2),
                            agents_relevant=agents,
                        ))

    return insights


def find_recurring_patterns(sources: list[DreamSource]) -> list[DreamInsight]:
    """Détecte les patterns qui reviennent fréquemment."""
    insights: list[DreamInsight] = []

    # Compter les keywords globalement
    keyword_freq: dict[str, list[str]] = {}  # keyword → [source_names]
    keyword_entries: dict[str, list[str]] = {}  # keyword → [entries]

    for src in sources:
        for entry in src.entries:
            keywords = _extract_keywords(entry)
            for kw in keywords:
                keyword_freq.setdefault(kw, []).append(src.name)
                keyword_entries.setdefault(kw, []).append(entry)

    # Trouver les keywords qui apparaissent dans ≥ MIN_SOURCES sources différentes
    for kw, src_names in keyword_freq.items():
        unique_sources = list(set(src_names))
        if len(unique_sources) >= MIN_SOURCES and len(src_names) >= 3:
            sample_entries = keyword_entries[kw][:3]
            # Bigrams (contiennent '_') sont plus significatifs → bonus confiance
            is_bigram = "_" in kw
            base_conf = 0.4 if is_bigram else 0.3
            label = kw.replace("_", " ") if is_bigram else kw
            # Agents mentionnés dans les entrées échantillons
            agents = sorted(set(
                a for e in sample_entries for a in _extract_agents(e)
            ))
            insights.append(DreamInsight(
                title=f"Pattern récurrent : '{label}'",
                description=(
                    f"Le terme '{label}' apparaît dans {len(unique_sources)} sources "
                    f"({len(src_names)} occurrences) :\n" +
                    "\n".join(f"  • {e[:100]}..." for e in sample_entries)
                ),
                sources=unique_sources,
                category="pattern",
                confidence=min(0.9, base_conf + 0.1 * len(unique_sources)),
                agents_relevant=agents,
            ))

    return insights


def find_tensions(sources: list[DreamSource]) -> list[DreamInsight]:
    """Détecte les tensions et contradictions potentielles."""
    insights: list[DreamInsight] = []

    # Mots indicateurs de tension
    # Note: "never"/"jamais" = assertions fortes (positive) uniquement.
    # Les garder dans negative aussi créerait des tensions auto-référentielles.
    tension_markers = {
        "positive": ["toujours", "always", "must", "doit", "jamais", "never",
                      "obligatoire", "required", "important", "critical"],
        "negative": ["éviter", "avoid", "ne pas", "danger",
                      "risque", "problème", "échec", "fail", "broken", "cassé"],
    }

    positive_entries: list[tuple[str, str]] = []  # (source, entry)
    negative_entries: list[tuple[str, str]] = []

    for src in sources:
        for entry in src.entries:
            entry_lower = entry.lower()
            if any(m in entry_lower for m in tension_markers["positive"]):
                positive_entries.append((src.name, entry))
            if any(m in entry_lower for m in tension_markers["negative"]):
                negative_entries.append((src.name, entry))

    # Croiser positifs et négatifs sur les mêmes sujets
    for pos_src, pos_entry in positive_entries:
        for neg_src, neg_entry in negative_entries:
            if pos_src == neg_src:
                continue
            sim = _similarity(pos_entry, neg_entry)
            if sim >= 0.3:  # Seuil plus bas pour les tensions
                agents = sorted(set(
                    _extract_agents(pos_entry) +
                    _extract_agents(neg_entry)
                ))
                insights.append(DreamInsight(
                    title=f"Tension détectée entre {pos_src} et {neg_src}",
                    description=(
                        f"Possible contradiction sur le même sujet :\n"
                        f"  ✅ [{pos_src}] {pos_entry[:120]}...\n"
                        f"  ❌ [{neg_src}] {neg_entry[:120]}..."
                    ),
                    sources=[pos_src, neg_src],
                    category="tension",
                    confidence=round(sim + 0.1, 2),
                    agents_relevant=agents,
                ))

    return insights


def find_opportunities(sources: list[DreamSource]) -> list[DreamInsight]:
    """Identifie les opportunités d'amélioration non exploitées."""
    insights: list[DreamInsight] = []

    # Chercher les patterns "TODO", "à améliorer", "could be better"
    opportunity_markers = [
        "todo", "à améliorer", "could be better", "improvement", "optimiser",
        "refactorer", "simplifier", "automatiser", "manque", "missing",
        "pas encore", "not yet", "futur", "future", "éventuellement",
    ]

    source_counts: dict[str, int] = {}

    for src in sources:
        for entry in src.entries:
            if source_counts.get(src.name, 0) >= MAX_OPPORTUNITIES_PER_SOURCE:
                break
            entry_lower = entry.lower()
            for marker in opportunity_markers:
                if marker in entry_lower:
                    agents = _extract_agents(entry) + _extract_agents(src.name)
                    insights.append(DreamInsight(
                        title=f"Opportunité dans {src.name}",
                        description=f"Signal d'amélioration : {entry[:150]}",
                        sources=[src.name],
                        category="opportunity",
                        confidence=0.5,
                        actionable=True,
                        agents_relevant=sorted(set(agents)),
                    ))
                    source_counts[src.name] = source_counts.get(src.name, 0) + 1
                    break  # Un seul marker suffit par entry

    return insights


# ── Validation ────────────────────────────────────────────────────────────────

def validate_insight(insight: DreamInsight, sources: list[DreamSource]) -> bool:
    """Vérifie qu'un insight est ancré dans les sources (pas d'hallucination)."""
    # Règle 1 : doit avoir ≥ 1 source existante
    if not insight.sources:
        return False

    # Règle 2 : les sources référencées doivent exister dans la collecte
    source_names = {s.name for s in sources}
    for ref in insight.sources:
        if ref not in source_names:
            return False

    # Règle 3 : confiance > 0
    if insight.confidence <= 0:
        return False

    # Règle 4 : description non vide
    if not insight.description or len(insight.description) < 10:
        return False

    return True


def deduplicate_insights(insights: list[DreamInsight]) -> list[DreamInsight]:
    """Supprime les insights trop similaires."""
    unique: list[DreamInsight] = []
    for ins in insights:
        is_dupe = False
        for existing in unique:
            if _similarity(ins.description, existing.description) > 0.7:
                # Garder celui avec la meilleure confiance
                if ins.confidence > existing.confidence:
                    unique.remove(existing)
                    unique.append(ins)
                is_dupe = True
                break
        if not is_dupe:
            unique.append(ins)
    return unique


# ── Orchestration principale ──────────────────────────────────────────────────

def dream(project_root: Path, since: str | None = None,
          agent_filter: str | None = None,
          do_validate: bool = True,
          quick: bool = False,
          _sources: list[DreamSource] | None = None) -> list[DreamInsight]:
    """Exécute un cycle de dream.

    Args:
        quick: si True, mode rapide O(n) — patterns + opportunités seulement.
        _sources: sources pré-collectées (évite double parsing en mode CLI).
    """

    # 1. Collecte (réutiliser _sources si fourni)
    sources = _sources if _sources is not None else collect_sources(
        project_root, since, agent_filter)
    if not sources:
        return []

    # 2. Analyse
    all_insights: list[DreamInsight] = []
    if not quick:
        all_insights.extend(find_cross_connections(sources))
    all_insights.extend(find_recurring_patterns(sources))
    if not quick:
        all_insights.extend(find_tensions(sources))
    all_insights.extend(find_opportunities(sources))

    # 3. Validation
    if do_validate:
        all_insights = [i for i in all_insights if validate_insight(i, sources)]

    # 4. Temporal decay — entrées récentes pèsent plus
    apply_temporal_decay(all_insights, sources)

    # 5. Déduplication
    all_insights = deduplicate_insights(all_insights)

    # 6. Tri par confiance décroissante
    all_insights.sort(key=lambda i: i.confidence, reverse=True)

    # 7. Plafonnement
    cap = QUICK_MAX_INSIGHTS if quick else MAX_INSIGHTS
    return all_insights[:cap]


def dream_quick(project_root: Path, since: str | None = None,
                agent_filter: str | None = None,
                _sources: list[DreamSource] | None = None) -> list[DreamInsight]:
    """Mode rapide O(n) — patterns récurrents + opportunités seulement.

    Utilisé par le post-commit auto-trigger pour ne pas ralentir le workflow.
    Skip les cross-connections O(n²) et les tensions O(n²).
    Délègue à dream(quick=True) pour ne pas dupliquer la logique.
    """
    return dream(project_root, since, agent_filter,
                 do_validate=True, quick=True, _sources=_sources)


# ── Dream → Stigmergy Bridge ─────────────────────────────────────────────────

# Mapping insight category → pheromone type
_INSIGHT_TO_PHEROMONE = {
    "tension":     "ALERT",
    "opportunity": "OPPORTUNITY",
    "connection":  "PROGRESS",
    "pattern":     "NEED",
}


def emit_to_stigmergy(insights: list[DreamInsight],
                       project_root: Path) -> int:
    """Convertit les insights dream en phéromones stigmergy.

    Returns le nombre de phéromones émises.
    """
    # Import dynamique pour éviter les dépendances circulaires
    try:
        import importlib.util
        # Chemin co-localisé d'abord (dream.py et stigmergy.py dans le même dossier)
        sg_path = Path(__file__).parent / "stigmergy.py"
        if not sg_path.exists():
            # Fallback : chemin relatif au project_root (projet installé)
            sg_path = project_root / "framework" / "tools" / "stigmergy.py"
        if not sg_path.exists():
            return 0

        spec = importlib.util.spec_from_file_location("stigmergy", sg_path)
        if spec is None or spec.loader is None:
            return 0
        sg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(sg)
    except Exception:
        return 0

    board = sg.load_board(project_root)
    emitted = 0

    # Index des textes existants pour déduplication cross-session
    existing_texts = {p.text for p in board.pheromones if not p.resolved}

    for ins in insights:
        ptype = _INSIGHT_TO_PHEROMONE.get(ins.category, "NEED")
        # Location = première source ou "system"
        location = ins.sources[0] if ins.sources else "system/dream"
        text = f"[dream] {ins.title}: {ins.description[:200]}"

        # Skip si une phéromone identique est déjà active sur le board
        if text in existing_texts:
            continue

        sg.emit_pheromone(
            board,
            ptype=ptype,
            location=location,
            text=text,
            emitter="dream-mode",
            tags=["auto-dream", ins.category],
            intensity=min(ins.confidence, 0.9),
        )
        existing_texts.add(text)
        emitted += 1

    if emitted > 0:
        sg.save_board(project_root, board)

    return emitted


# ── Rendu ─────────────────────────────────────────────────────────────────────

CATEGORY_ICONS = {
    "connection": "🔗",
    "pattern": "🔄",
    "tension": "⚡",
    "opportunity": "💡",
}

# ── Timestamp incrémental ─────────────────────────────────────────────────────

DREAM_TIMESTAMP_FILE = "dream-last-run"


def save_last_dream_timestamp(project_root: Path) -> None:
    """Sauvegarde le timestamp du dernier dream pour le mode incrémental."""
    ts_dir = project_root / "_bmad" / "_memory"
    ts_dir.mkdir(parents=True, exist_ok=True)
    ts_file = ts_dir / DREAM_TIMESTAMP_FILE
    ts_file.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")


def read_last_dream_timestamp(project_root: Path) -> str | None:
    """Lit le timestamp du dernier dream. Retourne None si aucun."""
    ts_file = project_root / "_bmad" / "_memory" / DREAM_TIMESTAMP_FILE
    if not ts_file.exists():
        return None
    try:
        ts = ts_file.read_text(encoding="utf-8").strip()
        # Valider le format YYYY-MM-DD
        if len(ts) == 10 and ts[4] == "-" and ts[7] == "-":
            return ts
    except OSError:
        pass
    return None


# ── Temporal Decay ────────────────────────────────────────────────────────────

def _temporal_weight(date_str: str, now: datetime | None = None) -> float:
    """Pondération temporelle : récent = 1.0, décroît avec l'âge.

    Utilise une demi-vie exponentielle (DECAY_HALFLIFE_DAYS).
    Plancher à 0.3 pour ne jamais ignorer complètement une entrée.
    Retourne 1.0 si date invalide ou vide (pas de pénalité).
    """
    if not date_str or len(date_str) < 10:
        return 1.0
    try:
        entry_date = datetime(int(date_str[:4]), int(date_str[5:7]),
                              int(date_str[8:10]))
    except (ValueError, IndexError):
        return 1.0
    ref = now or datetime.now()
    age_days = max(0, (ref - entry_date).days)
    if age_days == 0:
        return 1.0
    # Décroissance exponentielle : weight = 2^(-age/halflife)
    weight = math.pow(2.0, -age_days / DECAY_HALFLIFE_DAYS)
    return max(0.3, round(weight, 3))


def apply_temporal_decay(insights: list[DreamInsight],
                         sources: list[DreamSource],
                         now: datetime | None = None) -> None:
    """Applique un facteur de décroissance temporelle à la confiance des insights.

    Pour chaque insight, calcule le poids moyen des dates de ses sources
    contributrices, puis multiplie la confiance.
    Modifie les insights in-place.
    """
    # Index source_name → dates
    source_dates: dict[str, list[str]] = {}
    for src in sources:
        source_dates[src.name] = src.dates

    for ins in insights:
        weights: list[float] = []
        for src_name in ins.sources:
            dates = source_dates.get(src_name, [])
            if dates:
                # Poids moyen des entrées de cette source
                src_weights = [_temporal_weight(d, now) for d in dates if d]
                if src_weights:
                    weights.append(sum(src_weights) / len(src_weights))
        if weights:
            avg_weight = sum(weights) / len(weights)
            ins.confidence = round(ins.confidence * avg_weight, 3)


# ── Dream Memory — persistence tracking ──────────────────────────────────────

def _insight_signature(insight: DreamInsight) -> str:
    """Signature stable d'un insight pour tracking cross-session.

    Combine catégorie + titre normalisé. Les variations mineures de
    description ne changent pas la signature.
    """
    norm_title = re.sub(r'[^a-z0-9]', '', insight.title.lower())
    return f"{insight.category}:{norm_title}"


def load_dream_memory(project_root: Path) -> dict:
    """Charge dream-memory.json. Retourne {} si inexistant."""
    mem_path = project_root / "_bmad-output" / DREAM_MEMORY_FILE
    if not mem_path.exists():
        return {}
    try:
        return json.loads(mem_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_dream_memory(project_root: Path, memory: dict) -> None:
    """Sauvegarde dream-memory.json."""
    mem_path = project_root / "_bmad-output" / DREAM_MEMORY_FILE
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    mem_path.write_text(json.dumps(memory, indent=2, ensure_ascii=False),
                        encoding="utf-8")


def update_dream_memory(insights: list[DreamInsight],
                        memory: dict) -> dict[str, list[DreamInsight]]:
    """Met à jour la mémoire et classifie les insights.

    Retourne {"new": [...], "persistent": [...], "resolved": [sig, ...]}
    - new : insights jamais vus
    - persistent : insights vus dans ≥2 sessions consécutives (boost confiance)
    - resolved : signatures qui étaient dans la mémoire mais plus dans ce dream
    """
    now_str = datetime.now().strftime("%Y-%m-%d")
    seen_sigs: set[str] = set()
    new_insights: list[DreamInsight] = []
    persistent_insights: list[DreamInsight] = []

    entries = memory.get("insights", {})

    for ins in insights:
        sig = _insight_signature(ins)
        seen_sigs.add(sig)

        if sig in entries:
            # Déjà vu — incrémenter
            entries[sig]["seen_count"] += 1
            entries[sig]["last_seen"] = now_str
            entries[sig]["confidence"] = ins.confidence
            # Boost confiance pour la persistence
            ins.confidence = min(1.0, round(
                ins.confidence + PERSISTENCE_BOOST, 3))
            persistent_insights.append(ins)
        else:
            # Nouveau
            entries[sig] = {
                "title": ins.title,
                "category": ins.category,
                "first_seen": now_str,
                "last_seen": now_str,
                "seen_count": 1,
                "confidence": ins.confidence,
            }
            new_insights.append(ins)

    # Insights résolus : étaient dans la mémoire (seen_count > 1), absents maintenant
    resolved_sigs: list[str] = []
    for sig, entry in list(entries.items()):
        if sig not in seen_sigs:
            if entry.get("seen_count", 0) >= 2:
                resolved_sigs.append(sig)
            # Garder en mémoire mais marquer comme stale
            entry["stale"] = True

    memory["insights"] = entries
    memory["last_dream"] = now_str
    memory["total_dreams"] = memory.get("total_dreams", 0) + 1

    return {
        "new": new_insights,
        "persistent": persistent_insights,
        "resolved": resolved_sigs,
    }


def render_journal(insights: list[DreamInsight], sources: list[DreamSource],
                   project_root: Path, since: str | None = None,
                   dream_diff: dict[str, list] | None = None) -> str:
    """Génère le dream-journal.md en Markdown."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_entries = sum(len(s.entries) for s in sources)

    lines = [
        f"# 🌙 BMAD Dream Journal — {now}",
        "",
        f"> Consolidation hors-session — {len(sources)} sources, {total_entries} entrées analysées",
    ]
    if since:
        lines.append(f"> Période : depuis {since}")
    lines.extend(["", "---", ""])

    # Dream Diff (si disponible)
    if dream_diff:
        new_count = len(dream_diff.get("new", []))
        persist_count = len(dream_diff.get("persistent", []))
        resolved_count = len(dream_diff.get("resolved", []))
        if new_count or persist_count or resolved_count:
            lines.append("## 🔀 Dream Diff")
            lines.append("")
            if persist_count:
                lines.append(f"**🔁 Persistants** ({persist_count}) — insights confirmés sur plusieurs sessions :")
                for ins in dream_diff["persistent"]:
                    lines.append(f"- ⬆️ {ins.title} ({ins.confidence:.0%})")
                lines.append("")
            if new_count:
                lines.append(f"**🆕 Nouveaux** ({new_count}) :")
                for ins in dream_diff["new"]:
                    lines.append(f"- {ins.title}")
                lines.append("")
            if resolved_count:
                lines.append(f"**✅ Résolus** ({resolved_count}) — n'apparaissent plus :")
                for sig in dream_diff["resolved"]:
                    lines.append(f"- ~{sig}~")
                lines.append("")
            lines.extend(["---", ""])

    # Résumé par catégorie
    by_cat: dict[str, list[DreamInsight]] = {}
    for ins in insights:
        by_cat.setdefault(ins.category, []).append(ins)

    lines.append("## 📊 Résumé")
    lines.append("")
    lines.append("| Catégorie | Count | Confiance moy. |")
    lines.append("|-----------|-------|----------------|")
    for cat, cat_insights in sorted(by_cat.items()):
        icon = CATEGORY_ICONS.get(cat, "❓")
        avg_conf = sum(i.confidence for i in cat_insights) / len(cat_insights)
        lines.append(f"| {icon} {cat} | {len(cat_insights)} | {avg_conf:.0%} |")
    lines.extend(["", "---", ""])

    # Détail des insights
    lines.append("## 🧠 Insights")
    lines.append("")
    for idx, ins in enumerate(insights, 1):
        icon = CATEGORY_ICONS.get(ins.category, "❓")
        conf_bar = "█" * int(ins.confidence * 10) + "░" * (10 - int(ins.confidence * 10))
        lines.append(f"### {icon} {idx}. {ins.title}")
        lines.append("")
        lines.append(f"**Confiance** : `{conf_bar}` {ins.confidence:.0%}")
        lines.append(f"**Sources** : {', '.join(ins.sources)}")
        if ins.agents_relevant:
            lines.append(f"**Agents** : {', '.join(ins.agents_relevant)}")
        if ins.actionable:
            lines.append("**🎯 Actionable**")
        lines.append("")
        lines.append(ins.description)
        lines.append("")

    # Sources analysées
    lines.extend(["---", "", "## 📚 Sources analysées", ""])
    for src in sources:
        lines.append(f"- **{src.name}** ({src.kind}) — {len(src.entries)} entrées")
    lines.append("")

    return "\n".join(lines)


def write_journal(content: str, project_root: Path, dry_run: bool = False) -> Path:
    """Écrit le journal dans _bmad-output/dream-journal.md."""
    output_dir = project_root / "_bmad-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    journal_path = output_dir / "dream-journal.md"

    if dry_run:
        print(content)
        return journal_path

    # Archiver le journal précédent s'il existe
    if journal_path.exists():
        archive_dir = output_dir / "dream-archives"
        archive_dir.mkdir(exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M")
        archive_path = archive_dir / f"dream-journal-{ts}.md"
        journal_path.rename(archive_path)

    journal_path.write_text(content, encoding="utf-8")
    return journal_path


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BMAD Dream Mode — consolidation hors-session et insights émergents",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".", help="Racine du projet BMAD")
    parser.add_argument("--since", default=None,
                        help="Date début (YYYY-MM-DD) ou 'auto' pour depuis le dernier dream")
    parser.add_argument("--agent", default=None, help="Filtrer par agent")
    parser.add_argument("--validate", action="store_true", help="Valider les insights")
    parser.add_argument("--dry-run", action="store_true", help="Afficher sans écrire")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--quick", action="store_true",
                        help="Mode rapide O(n) — patterns + opportunités seulement")
    parser.add_argument("--emit", action="store_true",
                        help="Émettre les insights comme phéromones stigmergy")
    parser.add_argument("--multi-project", nargs="+", metavar="DIR",
                        help="Croiser les insights entre plusieurs projets")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    # Mode multi-projet : croiser les insights entre projets
    if args.multi_project:
        projects = [Path(d).resolve() for d in args.multi_project]
        if project_root not in projects:
            projects.insert(0, project_root)

        all_project_insights: dict[str, list[DreamInsight]] = {}
        all_project_sources: dict[str, list[DreamSource]] = {}

        since = args.since
        if since == "auto":
            since = read_last_dream_timestamp(project_root)

        for proj in projects:
            if not (proj / "_bmad" / "_memory").exists():
                print(f"⚠️  {proj.name}: pas de mémoire BMAD — ignoré")
                continue
            sources = collect_sources(proj, since, args.agent)
            if not sources:
                continue
            # Taguer les sources avec le nom du projet
            for s in sources:
                s.name = f"[{proj.name}] {s.name}"
            all_project_sources[proj.name] = sources

            fn = dream_quick if args.quick else dream
            insights = fn(proj, since, args.agent, _sources=sources)
            all_project_insights[proj.name] = insights

        if not all_project_insights:
            print("💤 Aucun projet avec des insights — rien à croiser.")
            sys.exit(0)

        # Croiser : trouver les patterns communs et divergents
        print("# 🔀 Dream Multi-Projet — Insights Croisés\n")
        total_insights = sum(len(v) for v in all_project_insights.values())
        print(f"  {len(all_project_insights)} projets, "
              f"{total_insights} insights totaux\n")

        # Table comparative
        print("| Projet | Insights | Actionnables | Top catégorie |")
        print("|--------|----------|-------------|----------------|")
        for proj_name, insights in sorted(all_project_insights.items()):
            actionable = sum(1 for i in insights if i.actionable)
            cats = {}
            for i in insights:
                cats[i.category] = cats.get(i.category, 0) + 1
            top_cat = max(cats, key=cats.get) if cats else "-"
            print(f"| {proj_name} | {len(insights)} | {actionable} | {top_cat} |")

        # Thèmes communs (titres similaires entre projets)
        if len(all_project_insights) >= 2:
            print("\n## 🔗 Thèmes communs\n")
            proj_names = list(all_project_insights.keys())
            found_common = False
            for i, p1 in enumerate(proj_names):
                for p2 in proj_names[i + 1:]:
                    for ins1 in all_project_insights[p1]:
                        for ins2 in all_project_insights[p2]:
                            # Similarité simple : mots partagés dans le titre
                            words1 = set(ins1.title.lower().split())
                            words2 = set(ins2.title.lower().split())
                            common = words1 & words2 - {"le", "la", "les", "de", "du",
                                                         "des", "un", "une", "et", "ou",
                                                         "à", "en", "par", "pour", "dans"}
                            if len(common) >= 3:
                                found_common = True
                                print(f"- **{p1}**: {ins1.title}")
                                print(f"  **{p2}**: {ins2.title}")
                                print(f"  Mots communs: {', '.join(sorted(common))}\n")
            if not found_common:
                print("  Aucun thème commun détecté — les projets divergent.\n")

            # Insights uniques à chaque projet
            print("## 🌟 Insights uniques\n")
            for proj_name, insights in all_project_insights.items():
                unique = [i for i in insights if i.confidence >= 0.6]
                if unique:
                    print(f"### {proj_name}")
                    for ins in unique[:3]:
                        icon = CATEGORY_ICONS.get(ins.category, "❓")
                        print(f"  {icon} {ins.title} ({ins.confidence:.0%})")
                    print()

        if args.json:
            data = {
                proj_name: [
                    {
                        "title": i.title,
                        "category": i.category,
                        "confidence": i.confidence,
                        "actionable": i.actionable,
                    }
                    for i in insights
                ]
                for proj_name, insights in all_project_insights.items()
            }
            print(json.dumps(data, indent=2, ensure_ascii=False))

        sys.exit(0)
    # Résoudre --since auto
    since = args.since
    if since == "auto":
        since = read_last_dream_timestamp(project_root)

    # Collecte unique (partagée entre affichage et dream)
    sources = collect_sources(project_root, since, args.agent)
    if not sources:
        print("💤 Aucune source de mémoire trouvée — rien à rêver.")
        sys.exit(0)

    total_entries = sum(len(s.entries) for s in sources)
    mode_label = "Quick" if args.quick else "Dream"
    print(f"🌙 {mode_label} Mode — {len(sources)} sources, {total_entries} entrées")
    if since:
        print(f"   Depuis : {since}")
    print()

    # Dream (quick ou full) — réutiliser les sources pré-collectées
    if args.quick:
        insights = dream_quick(project_root, since, args.agent,
                               _sources=sources)
    else:
        insights = dream(project_root, since, args.agent, args.validate,
                         _sources=sources)

    if not insights:
        print("😴 Aucun insight émergent détecté. Le système est cohérent.")
        sys.exit(0)

    # Dream Memory — tracking persistence cross-session
    dream_diff = None
    if not args.dry_run:
        memory = load_dream_memory(project_root)
        dream_diff = update_dream_memory(insights, memory)
        save_dream_memory(project_root, memory)

        new_count = len(dream_diff.get("new", []))
        persist_count = len(dream_diff.get("persistent", []))
        resolved_count = len(dream_diff.get("resolved", []))
        if persist_count:
            print(f"🔁 {persist_count} insight(s) persistant(s) (confiance boostée)")
        if new_count:
            print(f"🆕 {new_count} nouvel(s) insight(s)")
        if resolved_count:
            print(f"✅ {resolved_count} insight(s) résolu(s) (disparus)")
        if persist_count or new_count or resolved_count:
            print()

    # Emit → stigmergy
    if args.emit:
        count = emit_to_stigmergy(insights, project_root)
        if count > 0:
            print(f"🐜 {count} insight(s) émis comme phéromones stigmergy")
            print()

    # Sortie JSON (enrichie avec dream diff et agents)
    if args.json:
        data = {
            "insights": [
                {
                    "title": i.title,
                    "description": i.description,
                    "sources": i.sources,
                    "category": i.category,
                    "confidence": i.confidence,
                    "actionable": i.actionable,
                    "agents_relevant": i.agents_relevant,
                }
                for i in insights
            ],
        }
        if dream_diff:
            data["dream_diff"] = {
                "new": [i.title for i in dream_diff.get("new", [])],
                "persistent": [i.title for i in dream_diff.get("persistent", [])],
                "resolved": dream_diff.get("resolved", []),
            }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        sys.exit(0)

    # Rendu Markdown (avec dream diff)
    journal = render_journal(insights, sources, project_root, since,
                             dream_diff=dream_diff)
    output_path = write_journal(journal, project_root, args.dry_run)

    if not args.dry_run:
        print(f"✅ {len(insights)} insights écrits dans {output_path}")
        # Sauver le timestamp pour le mode incrémental
        save_last_dream_timestamp(project_root)
        print()
        # Preview compact
        for idx, ins in enumerate(insights[:5], 1):
            icon = CATEGORY_ICONS.get(ins.category, "❓")
            print(f"  {icon} {idx}. {ins.title} ({ins.confidence:.0%})")
        if len(insights) > 5:
            print(f"  ... et {len(insights) - 5} de plus dans le journal")


if __name__ == "__main__":
    main()
