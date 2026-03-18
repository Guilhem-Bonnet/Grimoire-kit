#!/usr/bin/env python3
"""
antifragile-score.py — Score d'Anti-Fragilité du système Grimoire.
===============================================================

Mesure comment le système apprend et s'améliore à partir de ses échecs.
Croise Failure Museum, SIL signals, contradictions, learnings et decisions
pour produire un score composite 0-100 :

  - < 30 : FRAGILE   — le système casse et n'apprend pas
  - 30-60 : ROBUST   — le système survit mais ne s'améliore pas
  - 60-100: ANTIFRAGILE — le système s'améliore sous le stress

Usage :
  python3 antifragile-score.py --project-root .
  python3 antifragile-score.py --project-root . --since 2026-01-01
  python3 antifragile-score.py --project-root . --detail
  python3 antifragile-score.py --project-root . --trend
  python3 antifragile-score.py --project-root . --json

Stdlib only — aucune dépendance externe.
"""

import argparse
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.antifragile_score")

# ── Constantes ────────────────────────────────────────────────────────────────

FRAGILE_THRESHOLD = 30
ROBUST_THRESHOLD = 60
HISTORY_FILE = "antifragile-history.json"

# Pondération des dimensions (total = 1.0)
WEIGHTS = {
    "recovery": 0.25,       # Taux de récupération (failures → règles)
    "learning_velocity": 0.20,  # Vitesse d'apprentissage
    "contradiction_resolution": 0.15,  # Résolution des contradictions
    "signal_trend": 0.15,   # Tendance des signaux SIL
    "decision_quality": 0.10,  # Qualité des décisions
    "pattern_recurrence": 0.15,  # Non-récurrence des patterns d'échec
}

# Catégories du Failure Museum
FAILURE_CATEGORIES = [
    "CC-FAIL", "WRONG-ASSUMPTION", "CONTEXT-LOSS",
    "HALLUCINATION", "ARCH-MISTAKE", "PROCESS-SKIP",
]

# Marqueurs SIL
SIL_MARKERS = {
    "cc_fail": ["cc fail", "cc_fail", "sans vérif", "terminé sans"],
    "incomplete": ["manquant", "todo", "non implémenté", "incomplet", "oublié"],
    "contradiction": ["contradiction", "désaccord", "conflit"],
    "guardrail_miss": ["supprimé sans", "écrasé", "overwrite", "destroy"],
    "expertise_gap": ["correction", "en fait", "incorrect", "trompé"],
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class DimensionScore:
    """Score d'une dimension d'anti-fragilité."""
    name: str
    score: float           # 0.0 - 1.0
    weight: float
    weighted: float        # score * weight
    evidence_count: int    # nombre de signaux analysés
    details: str           # explication textuelle
    recommendations: list[str] = field(default_factory=list)


@dataclass
class AntifragileResult:
    """Résultat complet du scoring."""
    timestamp: str
    global_score: float    # 0-100
    level: str             # FRAGILE | ROBUST | ANTIFRAGILE
    dimensions: list[DimensionScore]
    total_evidence: int
    summary: str
    since: str | None = None


# ── Collecte des données ─────────────────────────────────────────────────────

def _count_entries(path: Path, since: str | None = None) -> list[tuple[str, str]]:
    """Parse un markdown et retourne (date, text) pour les entrées listées."""
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries = []
    date_pattern = re.compile(r'\[(\d{4}-\d{2}-\d{2})')
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("- ", "* ", "### [")):
            match = date_pattern.search(line)
            entry_date = match.group(1) if match else ""
            if since and entry_date and entry_date < since:
                continue
            entries.append((entry_date, line))
    return entries


def _count_failure_sections(path: Path, since: str | None = None) -> dict:
    """Compte les sections dans le Failure Museum par catégorie et sévérité."""
    if not path.exists():
        return {"total": 0, "critical": 0, "important": 0, "micro": 0,
                "with_rule": 0, "with_lesson": 0, "categories": {}}

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {"total": 0, "critical": 0, "important": 0, "micro": 0,
                "with_rule": 0, "with_lesson": 0, "categories": {}}

    date_pattern = re.compile(r'\[(\d{4}-\d{2}-\d{2})\]')
    result = {"total": 0, "critical": 0, "important": 0, "micro": 0,
              "with_rule": 0, "with_lesson": 0, "categories": {}}

    current_severity = ""
    in_entry = False
    has_rule = False
    has_lesson = False
    entry_date = ""

    for line in content.splitlines():
        # Détecter la section sévérité
        if "Top Erreurs Critiques" in line or "🔴" in line:
            current_severity = "critical"
        elif "Erreurs Importantes" in line or "🟡" in line:
            current_severity = "important"
        elif "Micro-Erreurs" in line or "🟢" in line:
            current_severity = "micro"

        # Détecter une entrée
        if line.startswith("### ["):
            if in_entry:
                # Finaliser l'entrée précédente
                if has_rule:
                    result["with_rule"] += 1
                if has_lesson:
                    result["with_lesson"] += 1

            match = date_pattern.search(line)
            entry_date = match.group(1) if match else ""
            if since and entry_date and entry_date < since:
                in_entry = False
                continue

            in_entry = True
            has_rule = False
            has_lesson = False
            result["total"] += 1
            if current_severity:
                result[current_severity] = result.get(current_severity, 0) + 1

            # Catégoriser
            for cat in FAILURE_CATEGORIES:
                if cat in line:
                    result["categories"][cat] = result["categories"].get(cat, 0) + 1

        if in_entry:
            line_lower = line.lower()
            if "règle instaurée" in line_lower or "rule" in line_lower:
                has_rule = True
            if "leçon" in line_lower or "lesson" in line_lower:
                has_lesson = True

    # Finaliser la dernière entrée
    if in_entry:
        if has_rule:
            result["with_rule"] += 1
        if has_lesson:
            result["with_lesson"] += 1

    return result


def _count_contradictions(path: Path) -> dict:
    """Compte les contradictions par statut."""
    if not path.exists():
        return {"total": 0, "active": 0, "resolved": 0}

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return {"total": 0, "active": 0, "resolved": 0}

    result = {"total": 0, "active": 0, "resolved": 0}
    for line in content.splitlines():
        if "|" in line and not line.startswith("|--"):
            result["total"] += 1
            if "⏳" in line or "⚠️" in line:
                result["active"] += 1
            elif "✅" in line or "resolved" in line.lower():
                result["resolved"] += 1
    return result


def _count_sil_signals(memory_dir: Path, since: str | None = None) -> dict:
    """Compte les signaux SIL dans les sources mémoire."""
    signals = dict.fromkeys(SIL_MARKERS, 0)

    decisions_path = memory_dir / "decisions-log.md"
    learnings_dir = memory_dir / "agent-learnings"

    # Scan decisions-log
    if decisions_path.exists():
        entries = _count_entries(decisions_path, since)
        for _, text in entries:
            text_lower = text.lower()
            for cat, markers in SIL_MARKERS.items():
                if any(m in text_lower for m in markers):
                    signals[cat] += 1

    # Scan learnings
    if learnings_dir.exists():
        for f in learnings_dir.glob("*.md"):
            entries = _count_entries(f, since)
            for _, text in entries:
                text_lower = text.lower()
                for cat, markers in SIL_MARKERS.items():
                    if any(m in text_lower for m in markers):
                        signals[cat] += 1

    return signals


def _count_learnings(memory_dir: Path, since: str | None = None) -> dict:
    """Compte les entrées de learnings par agent."""
    learnings_dir = memory_dir / "agent-learnings"
    result = {"total": 0, "agents": {}, "per_agent": []}

    if not learnings_dir.exists():
        return result

    for f in sorted(learnings_dir.glob("*.md")):
        entries = _count_entries(f, since)
        count = len(entries)
        if count > 0:
            agent = f.stem
            result["agents"][agent] = count
            result["per_agent"].append((agent, count))
            result["total"] += count

    return result


def _count_decisions(memory_dir: Path, since: str | None = None) -> dict:
    """Compte les décisions et les reversals."""
    decisions_path = memory_dir / "decisions-log.md"
    result = {"total": 0, "reversals": 0}

    if not decisions_path.exists():
        return result

    entries = _count_entries(decisions_path, since)
    result["total"] = len(entries)

    reversal_markers = ["annulé", "reverté", "inversé", "cancel", "revert",
                        "rollback", "en fait non", "revenir sur", "abandonné"]
    for _, text in entries:
        if any(m in text.lower() for m in reversal_markers):
            result["reversals"] += 1

    return result


# ── Calcul des dimensions ─────────────────────────────────────────────────────

def score_recovery(failures: dict) -> DimensionScore:
    """Taux de récupération : failures → leçons → règles instaurées."""
    total = failures["total"]
    if total == 0:
        return DimensionScore(
            name="Récupération", score=0.5, weight=WEIGHTS["recovery"],
            weighted=0.5 * WEIGHTS["recovery"], evidence_count=0,
            details="Aucune failure enregistrée — score neutre",
            recommendations=["Commencer à documenter les échecs dans failure-museum.md"],
        )

    # Score basé sur : leçons extraites + règles instaurées
    lesson_rate = failures["with_lesson"] / total
    rule_rate = failures["with_rule"] / total

    # Une bonne récupération = règles instaurées (poids 0.6) + leçons (0.4)
    score = rule_rate * 0.6 + lesson_rate * 0.4

    recs = []
    if rule_rate < 0.5:
        recs.append(f"Seulement {failures['with_rule']}/{total} failures ont "
                     "une règle instaurée — systématiser les règles post-incident")
    if lesson_rate < 0.7:
        recs.append(f"Seulement {failures['with_lesson']}/{total} failures ont "
                     "une leçon — documenter chaque incident")

    return DimensionScore(
        name="Récupération", score=min(1.0, score), weight=WEIGHTS["recovery"],
        weighted=min(1.0, score) * WEIGHTS["recovery"],
        evidence_count=total,
        details=f"{total} failures, {failures['with_lesson']} leçons, "
                f"{failures['with_rule']} règles ({rule_rate:.0%})",
        recommendations=recs,
    )


def score_learning_velocity(learnings: dict) -> DimensionScore:
    """Vitesse d'apprentissage : volume et distribution des learnings."""
    total = learnings["total"]
    agents_count = len(learnings["agents"])

    if total == 0:
        return DimensionScore(
            name="Vélocité d'apprentissage", score=0.0,
            weight=WEIGHTS["learning_velocity"],
            weighted=0.0, evidence_count=0,
            details="Aucun learning enregistré",
            recommendations=["Les agents doivent commencer à documenter leurs apprentissages"],
        )

    # Score basé sur : volume (plafond 50 pour 1.0) + distribution (plus d'agents = mieux)
    volume_score = min(1.0, total / 50)
    distribution_score = min(1.0, agents_count / 5)
    score = volume_score * 0.6 + distribution_score * 0.4

    recs = []
    if agents_count < 3:
        recs.append(f"Seulement {agents_count} agent(s) écrivent des learnings — "
                     "encourager plus d'agents")
    if total < 10:
        recs.append(f"Seulement {total} learnings — objectif minimum 10 pour "
                     "une base d'apprentissage utile")

    return DimensionScore(
        name="Vélocité d'apprentissage", score=min(1.0, score),
        weight=WEIGHTS["learning_velocity"],
        weighted=min(1.0, score) * WEIGHTS["learning_velocity"],
        evidence_count=total,
        details=f"{total} learnings de {agents_count} agent(s)",
        recommendations=recs,
    )


def score_contradiction_resolution(contradictions: dict) -> DimensionScore:
    """Résolution des contradictions : indique la capacité à résoudre les tensions."""
    total = contradictions["total"]
    if total == 0:
        return DimensionScore(
            name="Résolution contradictions", score=0.5,
            weight=WEIGHTS["contradiction_resolution"],
            weighted=0.5 * WEIGHTS["contradiction_resolution"],
            evidence_count=0,
            details="Aucune contradiction enregistrée — score neutre",
        )

    resolved = contradictions["resolved"]
    active = contradictions["active"]
    resolution_rate = resolved / total if total > 0 else 0

    score = resolution_rate
    recs = []
    if active > 0:
        recs.append(f"{active} contradiction(s) active(s) non résolues — "
                     "prioriser la résolution")
    if resolution_rate < 0.5:
        recs.append("Taux de résolution < 50% — les tensions s'accumulent")

    return DimensionScore(
        name="Résolution contradictions", score=min(1.0, score),
        weight=WEIGHTS["contradiction_resolution"],
        weighted=min(1.0, score) * WEIGHTS["contradiction_resolution"],
        evidence_count=total,
        details=f"{resolved}/{total} résolues ({resolution_rate:.0%}), "
                f"{active} actives",
        recommendations=recs,
    )


def score_signal_trend(sil_signals: dict) -> DimensionScore:
    """Tendance des signaux SIL : moins = mieux (le système corrige)."""
    total = sum(sil_signals.values())

    if total == 0:
        return DimensionScore(
            name="Tendance signaux SIL", score=0.7,
            weight=WEIGHTS["signal_trend"],
            weighted=0.7 * WEIGHTS["signal_trend"],
            evidence_count=0,
            details="Aucun signal SIL détecté — bon signe ou projet neuf",
        )

    # Moins de signaux = meilleur score (inversé)
    # 0 signaux → 1.0, 20+ → ~0.1
    score = max(0.1, 1.0 - (total / 25))

    # Pondération par gravité
    cc_fail = sil_signals.get("cc_fail", 0)
    guardrail = sil_signals.get("guardrail_miss", 0)
    critical_count = cc_fail + guardrail
    if critical_count > 3:
        score *= 0.7  # Pénalité critique

    recs = []
    if cc_fail > 0:
        recs.append(f"{cc_fail} CC_FAIL détecté(s) — renforcer le Completion Contract")
    if guardrail > 0:
        recs.append(f"{guardrail} GUARDRAIL_MISS — ajouter des gardes automatiques")
    if sil_signals.get("expertise_gap", 0) > 2:
        recs.append("Expertise gaps récurrents — envisager Agent Forge pour spécialiser")

    details_parts = [f"{k}:{v}" for k, v in sil_signals.items() if v > 0]
    return DimensionScore(
        name="Tendance signaux SIL", score=max(0.0, min(1.0, score)),
        weight=WEIGHTS["signal_trend"],
        weighted=max(0.0, min(1.0, score)) * WEIGHTS["signal_trend"],
        evidence_count=total,
        details=f"{total} signaux ({', '.join(details_parts) or 'aucun'})",
        recommendations=recs,
    )


def score_decision_quality(decisions: dict) -> DimensionScore:
    """Qualité des décisions : taux de reversal."""
    total = decisions["total"]
    if total == 0:
        return DimensionScore(
            name="Qualité des décisions", score=0.5,
            weight=WEIGHTS["decision_quality"],
            weighted=0.5 * WEIGHTS["decision_quality"],
            evidence_count=0,
            details="Aucune décision enregistrée — score neutre",
        )

    reversal_rate = decisions["reversals"] / total
    # Peu de reversals = good quality
    score = max(0.1, 1.0 - reversal_rate * 3)

    recs = []
    if reversal_rate > 0.2:
        recs.append(f"{decisions['reversals']}/{total} décisions reversées "
                     f"({reversal_rate:.0%}) — utiliser le consensus adversarial "
                     "pour les décisions critiques")

    return DimensionScore(
        name="Qualité des décisions", score=min(1.0, score),
        weight=WEIGHTS["decision_quality"],
        weighted=min(1.0, score) * WEIGHTS["decision_quality"],
        evidence_count=total,
        details=f"{total} décisions, {decisions['reversals']} reversals "
                f"({reversal_rate:.0%})",
        recommendations=recs,
    )


def score_pattern_recurrence(failures: dict, sil_signals: dict) -> DimensionScore:
    """Non-récurrence des patterns d'échec."""
    # Vérifier si les mêmes catégories de failure reviennent
    categories = failures.get("categories", {})
    total_cats = sum(categories.values())

    if total_cats == 0:
        return DimensionScore(
            name="Non-récurrence patterns", score=0.5,
            weight=WEIGHTS["pattern_recurrence"],
            weighted=0.5 * WEIGHTS["pattern_recurrence"],
            evidence_count=0,
            details="Aucun pattern de failure détecté — score neutre",
        )

    # Diversité des catégories (max entropy = mieux que concentration)
    unique_cats = len(categories)
    total_possible = len(FAILURE_CATEGORIES)
    diversity = unique_cats / total_possible if total_possible > 0 else 0

    # Concentration = pire (une seule catégorie domine)
    max_cat_count = max(categories.values()) if categories else 0
    concentration = max_cat_count / total_cats if total_cats > 0 else 0

    # Score : haute diversité + basse concentration = anti-fragile
    # Basse diversité + haute concentration = fragile (toujours la même erreur)
    score = (1.0 - concentration) * 0.6 + diversity * 0.4

    recs = []
    if concentration > 0.6 and max_cat_count > 2:
        worst_cat = max(categories, key=categories.get)
        recs.append(f"Le pattern '{worst_cat}' domine ({max_cat_count}/{total_cats}) "
                     "— créer un guardrail spécialisé")

    return DimensionScore(
        name="Non-récurrence patterns", score=min(1.0, score),
        weight=WEIGHTS["pattern_recurrence"],
        weighted=min(1.0, score) * WEIGHTS["pattern_recurrence"],
        evidence_count=total_cats,
        details=f"{total_cats} failures, {unique_cats} catégories, "
                f"concentration: {concentration:.0%}",
        recommendations=recs,
    )


# ── Orchestration ─────────────────────────────────────────────────────────────

def compute_antifragile_score(project_root: Path,
                              since: str | None = None) -> AntifragileResult:
    """Calcule le score d'anti-fragilité global."""
    memory_dir = project_root / "_grimoire" / "_memory"
    timestamp = datetime.now().isoformat()

    # Collecter les données
    failures = _count_failure_sections(memory_dir / "failure-museum.md", since)
    contradictions = _count_contradictions(memory_dir / "contradiction-log.md")
    sil_signals = _count_sil_signals(memory_dir, since)
    learnings = _count_learnings(memory_dir, since)
    decisions = _count_decisions(memory_dir, since)

    # Scorer chaque dimension
    dimensions = [
        score_recovery(failures),
        score_learning_velocity(learnings),
        score_contradiction_resolution(contradictions),
        score_signal_trend(sil_signals),
        score_decision_quality(decisions),
        score_pattern_recurrence(failures, sil_signals),
    ]

    # Score global (0-100)
    global_score = sum(d.weighted for d in dimensions) * 100
    global_score = max(0, min(100, round(global_score, 1)))

    # Niveau
    if global_score < FRAGILE_THRESHOLD:
        level = "FRAGILE"
    elif global_score < ROBUST_THRESHOLD:
        level = "ROBUST"
    else:
        level = "ANTIFRAGILE"

    total_evidence = sum(d.evidence_count for d in dimensions)

    # Résumé
    if total_evidence == 0:
        summary = ("Projet neuf ou peu actif — score neutre. "
                   "Accumulez des données pour un scoring significatif.")
    elif level == "FRAGILE":
        summary = ("Le système est FRAGILE — les échecs ne produisent pas "
                   "d'apprentissage systématique. Actions urgentes requises.")
    elif level == "ROBUST":
        summary = ("Le système est ROBUST — il survit aux échecs mais n'en "
                   "tire pas assez de bénéfices. Potentiel d'amélioration.")
    else:
        summary = ("Le système est ANTI-FRAGILE — il s'améliore activement "
                   "avec chaque stress. Continuer sur cette trajectoire.")

    return AntifragileResult(
        timestamp=timestamp,
        global_score=global_score,
        level=level,
        dimensions=dimensions,
        total_evidence=total_evidence,
        summary=summary,
        since=since,
    )


# ── Persistance ───────────────────────────────────────────────────────────────

def save_score(result: AntifragileResult, project_root: Path) -> Path:
    """Sauvegarde le score dans l'historique."""
    output_dir = project_root / "_grimoire-output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / HISTORY_FILE

    history = []
    if path.exists():
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as _exc:
            _log.debug("json.JSONDecodeError, OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    entry = {
        "timestamp": result.timestamp,
        "score": result.global_score,
        "level": result.level,
        "evidence": result.total_evidence,
        "dimensions": {
            d.name: {"score": round(d.score * 100, 1), "evidence": d.evidence_count}
            for d in result.dimensions
        },
    }
    history.append(entry)
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_history(project_root: Path) -> list[dict]:
    """Charge l'historique des scores."""
    path = project_root / "_grimoire-output" / HISTORY_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


# ── Rendu ─────────────────────────────────────────────────────────────────────

LEVEL_ICONS = {"FRAGILE": "🔴", "ROBUST": "🟡", "ANTIFRAGILE": "🟢"}
LEVEL_BARS = {"FRAGILE": "█", "ROBUST": "▓", "ANTIFRAGILE": "░"}


def render_report(result: AntifragileResult) -> str:
    """Génère le rapport d'anti-fragilité en Markdown."""
    icon = LEVEL_ICONS.get(result.level, "❓")
    lines = [
        f"# {icon} Score Anti-Fragilité — {result.global_score}/100 ({result.level})",
        "",
        f"> {result.summary}",
        f"> **Date** : {result.timestamp[:19]}",
    ]
    if result.since:
        lines.append(f"> **Période** : depuis {result.since}")
    lines.append(f"> **Signaux analysés** : {result.total_evidence}")
    lines.extend(["", "---", ""])

    # Score visuel
    filled = int(result.global_score / 5)
    empty = 20 - filled
    bar = "█" * filled + "░" * empty
    lines.append(f"## 📊 Score Global : `{bar}` {result.global_score}/100")
    lines.extend(["", "---", ""])

    # Détail des dimensions
    lines.append("## 🔍 Dimensions")
    lines.append("")
    lines.append("| Dimension | Score | Poids | Pondéré | Signaux |")
    lines.append("|-----------|-------|-------|---------|---------|")
    for d in sorted(result.dimensions, key=lambda x: x.weighted, reverse=True):
        d_bar = "█" * int(d.score * 10) + "░" * (10 - int(d.score * 10))
        lines.append(
            f"| {d.name} | `{d_bar}` {d.score:.0%} | "
            f"{d.weight:.0%} | {d.weighted:.2f} | {d.evidence_count} |"
        )
    lines.extend(["", "---", ""])

    # Détails par dimension
    lines.append("## 📋 Détails par dimension")
    lines.append("")
    for d in result.dimensions:
        status = "🟢" if d.score >= 0.6 else "🟡" if d.score >= 0.3 else "🔴"
        lines.append(f"### {status} {d.name}")
        lines.append(f"**Score** : {d.score:.0%} — {d.details}")
        if d.recommendations:
            lines.append("")
            lines.append("**Recommandations** :")
            for rec in d.recommendations:
                lines.append(f"- {rec}")
        lines.append("")

    # Recommandations globales
    all_recs = []
    for d in result.dimensions:
        all_recs.extend(d.recommendations)

    if all_recs:
        lines.extend(["---", "", "## 🎯 Plan d'action", ""])
        for i, rec in enumerate(all_recs, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")

    return "\n".join(lines)


def render_trend(history: list[dict]) -> str:
    """Génère un rapport de tendance."""
    if not history:
        return "Aucun historique disponible. Lancez `grimoire-init.sh antifragile` pour commencer."

    lines = [
        "## 📈 Tendance Anti-Fragilité",
        "",
        "| # | Date | Score | Niveau | Signaux |",
        "|---|------|-------|--------|---------|",
    ]
    for i, entry in enumerate(reversed(history), 1):
        ts = entry.get("timestamp", "?")[:10]
        score = entry.get("score", 0)
        level = entry.get("level", "?")
        icon = LEVEL_ICONS.get(level, "❓")
        evidence = entry.get("evidence", 0)
        lines.append(f"| {i} | {ts} | {score}/100 | {icon} {level} | {evidence} |")

    # Tendance
    if len(history) >= 2:
        scores = [h.get("score", 0) for h in history]
        last = scores[-1]
        prev = scores[-2]
        delta = last - prev
        trend = "📈 +" if delta > 0 else "📉 " if delta < 0 else "➡️ "
        lines.extend(["", f"**Tendance** : {trend}{delta:+.1f} points depuis le dernier run"])

    if len(history) >= 3:
        scores = [h.get("score", 0) for h in history]
        avg = sum(scores) / len(scores)
        lines.append(f"**Moyenne** : {avg:.1f}/100 sur {len(scores)} runs")

    lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Grimoire Anti-Fragile Score — mesure la résilience adaptative du système",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".", help="Racine du projet Grimoire")
    parser.add_argument("--since", default=None, help="Date début (YYYY-MM-DD)")
    parser.add_argument("--detail", action="store_true", help="Rapport détaillé")
    parser.add_argument("--trend", action="store_true", help="Tendance historique")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--dry-run", action="store_true", help="Ne pas sauvegarder")
    parser.add_argument("--multi-project", nargs="+", metavar="DIR",
                        help="Comparer le score entre plusieurs projets")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    # Mode multi-projet : comparer le score entre plusieurs projets
    if args.multi_project:
        projects = [Path(d).resolve() for d in args.multi_project]
        # Inclure aussi le project-root principal s'il n'est pas déjà dans la liste
        if project_root not in projects:
            projects.insert(0, project_root)

        results: list[tuple[str, AntifragileResult]] = []
        for proj in projects:
            if not (proj / "_grimoire" / "_memory").exists():
                print(f"⚠️  {proj.name}: pas de mémoire Grimoire — ignoré")
                continue
            result = compute_antifragile_score(proj, args.since)
            results.append((proj.name, result))

        if not results:
            print("❌ Aucun projet valide trouvé.")
            return

        if args.json:
            data = {
                name: {
                    "score": r.global_score,
                    "level": r.level,
                    "evidence": r.total_evidence,
                    "dimensions": {
                        d.name: round(d.score * 100, 1)
                        for d in r.dimensions
                    },
                }
                for name, r in results
            }
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print("# 🔀 Comparaison Multi-Projet — Anti-Fragilité\n")
            print("| Projet | Score | Niveau | Signaux | Meilleure dim. | Plus faible dim. |")
            print("|--------|-------|--------|---------|----------------|-----------------|")
            for name, r in sorted(results, key=lambda x: x[1].global_score, reverse=True):
                icon = LEVEL_ICONS.get(r.level, "❓")
                best = max(r.dimensions, key=lambda d: d.score)
                worst = min(r.dimensions, key=lambda d: d.score)
                print(
                    f"| {name} | {r.global_score}/100 | {icon} {r.level} | "
                    f"{r.total_evidence} | {best.name} ({best.score:.0%}) | "
                    f"{worst.name} ({worst.score:.0%}) |"
                )

            # Recommandations croisées
            if len(results) >= 2:
                print("\n## 💡 Insights croisés\n")
                all_results = [(n, r) for n, r in results]
                best_proj = max(all_results, key=lambda x: x[1].global_score)
                worst_proj = min(all_results, key=lambda x: x[1].global_score)
                delta = best_proj[1].global_score - worst_proj[1].global_score
                print(f"- **Écart** : {delta:.1f} points entre {best_proj[0]} et {worst_proj[0]}")

                # Trouver les dimensions où un projet excelle et l'autre non
                for dim_idx, dim in enumerate(best_proj[1].dimensions):
                    other_dim = worst_proj[1].dimensions[dim_idx]
                    if dim.score - other_dim.score > 0.3:
                        print(
                            f"- **{dim.name}** : {best_proj[0]} ({dim.score:.0%}) "
                            f">> {worst_proj[0]} ({other_dim.score:.0%}) — "
                            f"transférer les bonnes pratiques"
                        )
        return

    # Mode tendance
    if args.trend:
        history = load_history(project_root)
        print(render_trend(history))
        return

    # Calcul du score
    result = compute_antifragile_score(project_root, args.since)

    # Sortie JSON
    if args.json:
        data = {
            "score": result.global_score,
            "level": result.level,
            "evidence": result.total_evidence,
            "summary": result.summary,
            "dimensions": {
                d.name: {
                    "score": round(d.score * 100, 1),
                    "weight": d.weight,
                    "evidence": d.evidence_count,
                    "recommendations": d.recommendations,
                }
                for d in result.dimensions
            },
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
    elif args.detail:
        print(render_report(result))
    else:
        # Sortie compacte
        icon = LEVEL_ICONS.get(result.level, "❓")
        filled = int(result.global_score / 5)
        bar = "█" * filled + "░" * (20 - filled)
        print(f"{icon} Anti-Fragile Score : {bar} {result.global_score}/100 ({result.level})")
        print(f"   {result.summary}")
        print()
        for d in sorted(result.dimensions, key=lambda x: x.score):
            d_icon = "🟢" if d.score >= 0.6 else "🟡" if d.score >= 0.3 else "🔴"
            print(f"   {d_icon} {d.name}: {d.score:.0%} ({d.evidence_count} signaux)")
        # Recommandations top 3
        all_recs = [r for d in result.dimensions for r in d.recommendations]
        if all_recs:
            print()
            print("   🎯 Actions prioritaires :")
            for rec in all_recs[:3]:
                print(f"      → {rec}")

    # Sauvegarder
    if not args.dry_run:
        save_score(result, project_root)
        icon = LEVEL_ICONS.get(result.level, "❓")
        print(f"\n{icon} Score enregistré dans {HISTORY_FILE}")


if __name__ == "__main__":
    main()
