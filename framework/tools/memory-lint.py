#!/usr/bin/env python3
"""
memory-lint.py — Linter de cohérence mémoire BMAD.
=====================================================

Valide la cohérence croisée des fichiers mémoire du projet :
  - Contradictions inter-fichiers (learnings vs decisions, decisions vs trace)
  - Entrées orphelines (décision référencée dans trace mais absente)
  - Doublons inter-fichiers (même entrée copiée dans plusieurs fichiers)
  - Références cassées (fichier source mentionné mais inexistant)
  - Incohérences chronologiques (entrée datée avant la création du fichier)

Émet un rapport structuré et peut optionnellement créer des phéromones
stigmergy pour les problèmes critiques.

Usage :
  python3 memory-lint.py --project-root .
  python3 memory-lint.py --project-root . --json
  python3 memory-lint.py --project-root . --emit     # émettre vers stigmergy
  python3 memory-lint.py --project-root . --fix       # suggestions de fix

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ── Constantes ────────────────────────────────────────────────────────────────

LINT_VERSION = "1.0.0"

# Seuil de similarité pour la détection de doublons
DUPLICATE_THRESHOLD = 0.75

# Seuil de similarité pour la détection de contradictions
CONTRADICTION_THRESHOLD = 0.30

# Marqueurs de contradiction (même logique que dream.py)
POSITIVE_MARKERS = frozenset({
    "toujours", "always", "must", "doit", "jamais", "never",
    "obligatoire", "required", "important", "critical",
})

NEGATIVE_MARKERS = frozenset({
    "éviter", "avoid", "ne pas", "danger",
    "risque", "problème", "échec", "fail", "broken", "cassé",
})

# Severity levels
SEVERITY_ERROR = "error"      # Contradiction avérée, référence cassée
SEVERITY_WARNING = "warning"  # Doublon probable, orphelin potentiel
SEVERITY_INFO = "info"        # Suggestion d'amélioration


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class LintIssue:
    """Un problème détecté par le linter mémoire."""
    issue_id: str
    severity: str          # error | warning | info
    category: str          # contradiction | orphan | duplicate | broken-ref | chrono
    title: str
    description: str
    files: list[str]       # fichiers impliqués
    entries: list[str] = field(default_factory=list)  # entrées concernées
    fix_suggestion: str = ""


@dataclass
class MemoryFile:
    """Un fichier mémoire parsé."""
    path: str              # chemin relatif depuis _bmad/_memory
    kind: str              # learnings | decisions | trace | failure-museum | shared-context | contradictions
    entries: list[tuple[str, str]] = field(default_factory=list)  # (date, text)


@dataclass
class LintReport:
    """Rapport complet du linter."""
    version: str = LINT_VERSION
    files_scanned: int = 0
    entries_scanned: int = 0
    issues: list[LintIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SEVERITY_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SEVERITY_WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == SEVERITY_INFO)


# ── Parsing ───────────────────────────────────────────────────────────────────

_DATE_PATTERN = re.compile(r'\[(\d{4}-\d{2}-\d{2})')
_TRACE_PATTERN = re.compile(
    r'\[(\d{4}-\d{2}-\d{2})[^\]]*\]\s*\[(\w+)\]\s*\[([^\]]+)\]\s*(.*)'
)


def _parse_markdown_entries(path: Path) -> list[tuple[str, str]]:
    """Parse un fichier markdown en (date, text)."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: list[tuple[str, str]] = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DATE_PATTERN.search(line)
        entry_date = match.group(1) if match else ""
        if line.startswith("- ") or line.startswith("* "):
            entries.append((entry_date, line[2:].strip()))
    return entries


def _parse_trace_entries(path: Path) -> list[tuple[str, str]]:
    """Parse BMAD_TRACE.md en (date, text)."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return []

    entries: list[tuple[str, str]] = []
    for line in content.splitlines():
        m = _TRACE_PATTERN.match(line.strip())
        if m:
            date, level, agent, payload = m.groups()
            entries.append((date, f"[{agent}] [{level}] {payload}"))
    return entries


def collect_memory_files(project_root: Path) -> list[MemoryFile]:
    """Collecte tous les fichiers mémoire du projet."""
    files: list[MemoryFile] = []
    memory_dir = project_root / "_bmad" / "_memory"

    # Learnings
    learnings_dir = memory_dir / "agent-learnings"
    if learnings_dir.exists():
        for f in sorted(learnings_dir.glob("*.md")):
            entries = _parse_markdown_entries(f)
            if entries:
                files.append(MemoryFile(
                    path=f"learnings/{f.name}", kind="learnings",
                    entries=entries,
                ))

    # Decisions log
    decisions_file = memory_dir / "decisions-log.md"
    if decisions_file.exists():
        entries = _parse_markdown_entries(decisions_file)
        if entries:
            files.append(MemoryFile(
                path="decisions-log.md", kind="decisions",
                entries=entries,
            ))

    # BMAD_TRACE
    trace_file = project_root / "_bmad-output" / "BMAD_TRACE.md"
    if trace_file.exists():
        entries = _parse_trace_entries(trace_file)
        if entries:
            files.append(MemoryFile(
                path="BMAD_TRACE.md", kind="trace",
                entries=entries,
            ))

    # Failure Museum
    failure_file = memory_dir / "failure-museum.md"
    if failure_file.exists():
        entries = _parse_markdown_entries(failure_file)
        if entries:
            files.append(MemoryFile(
                path="failure-museum.md", kind="failure-museum",
                entries=entries,
            ))

    # Contradiction log
    contradiction_file = memory_dir / "contradiction-log.md"
    if contradiction_file.exists():
        entries = _parse_markdown_entries(contradiction_file)
        if entries:
            files.append(MemoryFile(
                path="contradiction-log.md", kind="contradictions",
                entries=entries,
            ))

    # Shared context
    shared_file = memory_dir / "shared-context.md"
    if shared_file.exists():
        entries = _parse_markdown_entries(shared_file)
        if entries:
            files.append(MemoryFile(
                path="shared-context.md", kind="shared-context",
                entries=entries,
            ))

    return files


# ── Analyse keywords ──────────────────────────────────────────────────────────

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
    """Extrait les mots-clés significatifs d'un texte."""
    words = re.findall(r'[a-zA-ZÀ-ÿ]{3,}', text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _similarity(text_a: str, text_b: str) -> float:
    """Similarité Jaccard sur les keywords."""
    ka = _extract_keywords(text_a)
    kb = _extract_keywords(text_b)
    if not ka or not kb:
        return 0.0
    intersection = ka & kb
    union = ka | kb
    return len(intersection) / len(union) if union else 0.0


def _has_polarity(text: str) -> tuple[bool, bool]:
    """Retourne (is_positive, is_negative) selon les marqueurs."""
    text_lower = text.lower()
    is_pos = any(m in text_lower for m in POSITIVE_MARKERS)
    is_neg = any(m in text_lower for m in NEGATIVE_MARKERS)
    return is_pos, is_neg


# ── Checks ────────────────────────────────────────────────────────────────────

_issue_counter = 0


def _next_id() -> str:
    """Génère un ID court pour les issues."""
    global _issue_counter  # noqa: PLW0603
    _issue_counter += 1
    return f"ML-{_issue_counter:03d}"


def check_contradictions(files: list[MemoryFile]) -> list[LintIssue]:
    """Détecte les contradictions entre fichiers mémoire.

    Une contradiction = deux entrées de fichiers DIFFÉRENTS sur le même sujet
    avec des polarités opposées (positive vs négative).
    """
    issues: list[LintIssue] = []

    positive_entries: list[tuple[str, str, str]] = []  # (file, date, text)
    negative_entries: list[tuple[str, str, str]] = []

    for mf in files:
        for date, text in mf.entries:
            is_pos, is_neg = _has_polarity(text)
            if is_pos:
                positive_entries.append((mf.path, date, text))
            if is_neg:
                negative_entries.append((mf.path, date, text))

    for pos_file, _pos_date, pos_text in positive_entries:
        for neg_file, _neg_date, neg_text in negative_entries:
            if pos_file == neg_file:
                continue
            sim = _similarity(pos_text, neg_text)
            if sim >= CONTRADICTION_THRESHOLD:
                issues.append(LintIssue(
                    issue_id=_next_id(),
                    severity=SEVERITY_ERROR,
                    category="contradiction",
                    title=f"Contradiction entre {pos_file} et {neg_file}",
                    description=(
                        f"Polarité positive/négative sur un sujet similaire "
                        f"(similarité : {sim:.0%})"
                    ),
                    files=[pos_file, neg_file],
                    entries=[pos_text[:120], neg_text[:120]],
                    fix_suggestion=(
                        "Vérifier si les deux entrées sont compatibles. "
                        "Si oui, clarifier le contexte. Si non, résoudre "
                        "et documenter dans contradiction-log.md."
                    ),
                ))

    return issues


def check_duplicates(files: list[MemoryFile]) -> list[LintIssue]:
    """Détecte les doublons inter-fichiers.

    Un doublon = même entrée (ou très similaire) dans 2+ fichiers différents.
    """
    issues: list[LintIssue] = []
    seen: list[tuple[str, str]] = []  # (file, text)

    for mf in files:
        for _date, text in mf.entries:
            for prev_file, prev_text in seen:
                if prev_file == mf.path:
                    continue
                if _similarity(text, prev_text) >= DUPLICATE_THRESHOLD:
                    issues.append(LintIssue(
                        issue_id=_next_id(),
                        severity=SEVERITY_WARNING,
                        category="duplicate",
                        title=f"Doublon entre {mf.path} et {prev_file}",
                        description="Entrées très similaires dans deux fichiers mémoire",
                        files=[mf.path, prev_file],
                        entries=[text[:120], prev_text[:120]],
                        fix_suggestion=(
                            "Garder l'entrée dans le fichier le plus approprié. "
                            "Supprimer le doublon de l'autre fichier."
                        ),
                    ))
            seen.append((mf.path, text))

    return issues


def check_orphan_decisions(files: list[MemoryFile]) -> list[LintIssue]:
    """Détecte les décisions référencées dans trace mais absentes du decisions-log.

    Une décision orpheline = BMAD_TRACE contient [DECISION] mais aucune entrée
    similaire dans decisions-log.md.
    """
    issues: list[LintIssue] = []

    # Trouver les fichiers concernés
    trace_file = next((f for f in files if f.kind == "trace"), None)
    decision_file = next((f for f in files if f.kind == "decisions"), None)

    if not trace_file or not decision_file:
        return issues

    # Extraire les décisions depuis la trace
    trace_decisions = [
        (date, text) for date, text in trace_file.entries
        if "[DECISION]" in text
    ]

    if not trace_decisions:
        return issues

    # Pour chaque décision de la trace, chercher une correspondance
    decision_texts = [text for _, text in decision_file.entries]

    for date, trace_text in trace_decisions:
        found = False
        for dec_text in decision_texts:
            if _similarity(trace_text, dec_text) >= 0.3:
                found = True
                break
        if not found:
            issues.append(LintIssue(
                issue_id=_next_id(),
                severity=SEVERITY_WARNING,
                category="orphan",
                title=f"Décision orpheline dans BMAD_TRACE [{date}]",
                description=(
                    "Une décision enregistrée dans la trace n'a pas "
                    "d'entrée correspondante dans decisions-log.md"
                ),
                files=["BMAD_TRACE.md", "decisions-log.md"],
                entries=[trace_text[:150]],
                fix_suggestion=(
                    "Ajouter cette décision dans decisions-log.md pour "
                    "assurer la traçabilité complète."
                ),
            ))

    return issues


def check_failure_without_lesson(files: list[MemoryFile]) -> list[LintIssue]:
    """Détecte les failures sans leçon associée dans les learnings.

    Un failure non capitalisé = entry dans failure-museum sans entrée
    correspondante dans aucun learnings/*.md.
    """
    issues: list[LintIssue] = []

    failure_file = next((f for f in files if f.kind == "failure-museum"), None)
    learning_files = [f for f in files if f.kind == "learnings"]

    if not failure_file or not learning_files:
        return issues

    all_learning_texts = [
        text for lf in learning_files for _, text in lf.entries
    ]

    for date, failure_text in failure_file.entries:
        found = False
        for learn_text in all_learning_texts:
            if _similarity(failure_text, learn_text) >= 0.25:
                found = True
                break
        if not found:
            issues.append(LintIssue(
                issue_id=_next_id(),
                severity=SEVERITY_INFO,
                category="orphan",
                title=f"Failure non capitalisé [{date}]",
                description=(
                    "Un échec dans le failure-museum n'a pas de leçon "
                    "correspondante dans les learnings."
                ),
                files=["failure-museum.md"],
                entries=[failure_text[:150]],
                fix_suggestion=(
                    "Extraire la leçon apprise de cet échec et l'ajouter "
                    "dans le fichier learnings de l'agent concerné."
                ),
            ))

    return issues


def check_chronological_consistency(files: list[MemoryFile]) -> list[LintIssue]:
    """Détecte les incohérences chronologiques dans chaque fichier.

    Les entrées avec dates devraient être en ordre décroissant (plus récente en haut)
    ou croissant. On détecte les sauts de date incohérents.
    """
    issues: list[LintIssue] = []

    for mf in files:
        dated_entries = [(d, t) for d, t in mf.entries if d]
        if len(dated_entries) < 2:
            continue

        dates = [d for d, _ in dated_entries]

        # Calculer si c'est ascendant ou descendant
        asc_count = sum(1 for i in range(1, len(dates)) if dates[i] >= dates[i - 1])
        desc_count = sum(1 for i in range(1, len(dates)) if dates[i] <= dates[i - 1])

        total = len(dates) - 1
        if total == 0:
            continue

        # Seuil : si ni clairement asc ni desc (< 70% d'un sens)
        max_dir = max(asc_count, desc_count)
        if max_dir / total < 0.7 and total >= 3:
            issues.append(LintIssue(
                issue_id=_next_id(),
                severity=SEVERITY_INFO,
                category="chrono",
                title=f"Dates désordonnées dans {mf.path}",
                description=(
                    f"{total + 1} entrées datées ne suivent pas un ordre "
                    f"chronologique cohérent (asc: {asc_count}, desc: {desc_count})"
                ),
                files=[mf.path],
                fix_suggestion="Réorganiser les entrées par date.",
            ))

    return issues


# ── Orchestration ─────────────────────────────────────────────────────────────

def lint_memory(project_root: Path) -> LintReport:
    """Exécute toutes les vérifications de cohérence mémoire.

    Retourne un LintReport avec tous les problèmes détectés.
    """
    global _issue_counter  # noqa: PLW0603
    _issue_counter = 0

    files = collect_memory_files(project_root)

    report = LintReport(
        files_scanned=len(files),
        entries_scanned=sum(len(f.entries) for f in files),
    )

    # Exécuter tous les checks
    report.issues.extend(check_contradictions(files))
    report.issues.extend(check_duplicates(files))
    report.issues.extend(check_orphan_decisions(files))
    report.issues.extend(check_failure_without_lesson(files))
    report.issues.extend(check_chronological_consistency(files))

    # Trier : errors > warnings > info
    severity_order = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}
    report.issues.sort(key=lambda i: severity_order.get(i.severity, 9))

    return report


# ── Émission Stigmergy ───────────────────────────────────────────────────────

def emit_to_stigmergy(report: LintReport, project_root: Path) -> int:
    """Émet les issues critiques comme phéromones stigmergy.

    Seuls les errors sont émis (contradictions, refs cassées).
    Retourne le nombre de phéromones émises.
    """
    try:
        import importlib.util
        # Cache hit : réutiliser le module déjà chargé
        if "stigmergy" in sys.modules:
            sg = sys.modules["stigmergy"]
        else:
            sg_path = Path(__file__).parent / "stigmergy.py"
            if not sg_path.exists():
                return 0
            spec = importlib.util.spec_from_file_location("stigmergy", sg_path)
            if spec is None or spec.loader is None:
                return 0
            sg = importlib.util.module_from_spec(spec)
            sys.modules["stigmergy"] = sg
            spec.loader.exec_module(sg)
    except Exception:
        return 0

    board = sg.load_board(project_root)
    existing_texts = {p.text for p in board.pheromones if not p.resolved}
    emitted = 0

    for issue in report.issues:
        if issue.severity != SEVERITY_ERROR:
            continue

        text = f"[memory-lint] {issue.title}: {issue.description[:200]}"
        if text in existing_texts:
            continue

        sg.emit_pheromone(
            board,
            ptype="ALERT",
            location=issue.files[0] if issue.files else "memory",
            text=text,
            emitter="memory-lint",
            tags=["auto-lint", issue.category],
            intensity=0.8,
        )
        existing_texts.add(text)
        emitted += 1

    if emitted > 0:
        sg.save_board(project_root, board)

    return emitted


# ── Rendu ─────────────────────────────────────────────────────────────────────

SEVERITY_ICONS = {
    SEVERITY_ERROR: "🔴",
    SEVERITY_WARNING: "🟡",
    SEVERITY_INFO: "🔵",
}

CATEGORY_ICONS = {
    "contradiction": "⚡",
    "duplicate": "📋",
    "orphan": "👻",
    "broken-ref": "🔗",
    "chrono": "📅",
}


def render_report(report: LintReport, show_fix: bool = False) -> str:
    """Rend le rapport en texte formaté."""
    lines = [
        "🔍 BMAD Memory Lint Report",
        f"   Fichiers scannés : {report.files_scanned}",
        f"   Entrées analysées : {report.entries_scanned}",
        "",
    ]

    if not report.issues:
        lines.append("✅ Aucun problème détecté — mémoire cohérente.")
        return "\n".join(lines)

    lines.append(
        f"   Problèmes : {report.error_count} erreurs, "
        f"{report.warning_count} warnings, {report.info_count} infos"
    )
    lines.extend(["", "---", ""])

    for issue in report.issues:
        sev_icon = SEVERITY_ICONS.get(issue.severity, "❓")
        cat_icon = CATEGORY_ICONS.get(issue.category, "")
        lines.append(
            f"{sev_icon} {cat_icon} [{issue.issue_id}] {issue.title}"
        )
        lines.append(f"   {issue.description}")
        if issue.entries:
            for entry in issue.entries[:2]:
                lines.append(f"     → {entry}")
        if show_fix and issue.fix_suggestion:
            lines.append(f"   💡 Fix : {issue.fix_suggestion}")
        lines.append("")

    return "\n".join(lines)


def report_to_dict(report: LintReport) -> dict:
    """Convertit le rapport en dict JSON."""
    return {
        "version": report.version,
        "files_scanned": report.files_scanned,
        "entries_scanned": report.entries_scanned,
        "summary": {
            "errors": report.error_count,
            "warnings": report.warning_count,
            "info": report.info_count,
            "total": len(report.issues),
        },
        "issues": [
            {
                "issue_id": i.issue_id,
                "severity": i.severity,
                "category": i.category,
                "title": i.title,
                "description": i.description,
                "files": i.files,
                "entries": i.entries,
                "fix_suggestion": i.fix_suggestion,
            }
            for i in report.issues
        ],
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="BMAD Memory Lint — vérification de cohérence mémoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".",
                        help="Racine du projet BMAD")
    parser.add_argument("--json", action="store_true",
                        help="Sortie JSON")
    parser.add_argument("--fix", action="store_true",
                        help="Afficher les suggestions de fix")
    parser.add_argument("--emit", action="store_true",
                        help="Émettre les erreurs comme phéromones stigmergy")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    report = lint_memory(project_root)

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2, ensure_ascii=False))
        sys.exit(0)

    print(render_report(report, show_fix=args.fix))

    if args.emit and report.error_count > 0:
        count = emit_to_stigmergy(report, project_root)
        if count > 0:
            print(f"🐜 {count} erreur(s) émise(s) comme phéromones stigmergy")

    # Exit code : 1 si erreurs, 0 sinon
    sys.exit(1 if report.error_count > 0 else 0)


if __name__ == "__main__":
    main()
