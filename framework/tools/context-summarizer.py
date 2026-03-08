#!/usr/bin/env python3
"""
context-summarizer.py — Résumé automatique du contexte ancien Grimoire (BM-41 Story 3.1).
============================================================

Résume automatiquement les sections anciennes du contexte (decisions-log > 30j,
learnings > 60j) pour libérer du budget token. Les résumés remplacent le contenu
original dans le chargement P1 du Context Router.

Modes :
  summarize  — Génère des digests pour les sections anciennes
  status     — Affiche l'état des digests existants
  preview    — Prévisualise ce qui serait résumé (dry run)
  restore    — Restaure l'original depuis l'archive

Usage :
  python3 context-summarizer.py --project-root . summarize
  python3 context-summarizer.py --project-root . summarize --age-threshold 14
  python3 context-summarizer.py --project-root . status
  python3 context-summarizer.py --project-root . preview --agent dev
  python3 context-summarizer.py --project-root . restore --digest digest-2026-03-01.md

Stdlib only — aucune dépendance externe.

Références :
  - RTK Context Compression: https://github.com/rtk-ai/rtk
  - LLMLingua (Microsoft): https://github.com/microsoft/LLMLingua
  - Anthropic Long Context: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

# ── Version ──────────────────────────────────────────────────────────────────

CONTEXT_SUMMARIZER_VERSION = "1.1.0"

# ── Constants ────────────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4
DEFAULT_AGE_THRESHOLD_DAYS = 30
DEFAULT_MAX_SUMMARY_TOKENS = 500
DEFAULT_LEARNINGS_AGE_DAYS = 60
ARCHIVES_DIR = "_grimoire/_memory/archives"
MEMORY_DIR = "_grimoire/_memory"

# Sections à préserver intégralement même si anciennes
DEFAULT_PRESERVE_TAGS = ["critical", "architecture", "security", "breaking"]

# Patterns de date dans les headings
DATE_PATTERNS = [
    r"(\d{4}-\d{2}-\d{2})",                    # 2024-01-15
    r"(\d{2}/\d{2}/\d{4})",                    # 15/01/2024
    r"(\d{4}-\d{2})",                          # 2024-01 (month precision)
]


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Section:
    """Une section d'un fichier mémoire avec son heading et son contenu."""
    heading: str
    content: str
    source_file: str
    date: str = ""
    age_days: int = 0
    estimated_tokens: int = 0
    tags: list[str] = field(default_factory=list)
    preserved: bool = False


@dataclass
class Digest:
    """Un résumé généré à partir de sections anciennes."""
    source_file: str
    digest_file: str
    sections_summarized: int = 0
    original_tokens: int = 0
    digest_tokens: int = 0
    compression_ratio: float = 0.0
    created_at: str = ""
    sections: list[str] = field(default_factory=list)


@dataclass
class SummaryReport:
    """Rapport de résumé."""
    digests_created: int = 0
    sections_processed: int = 0
    sections_summarized: int = 0
    sections_preserved: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    compression_ratio: float = 0.0
    errors: list[str] = field(default_factory=list)
    digests: list[Digest] = field(default_factory=list)


@dataclass
class DigestStatus:
    """État d'un digest existant."""
    filename: str
    source_file: str
    sections_count: int = 0
    tokens: int = 0
    created_at: str = ""


# ── Section Parser ───────────────────────────────────────────────────────────

class SectionParser:
    """Parse les fichiers mémoire en sections datées."""

    @staticmethod
    def extract_date(text: str) -> str:
        """Extrait une date depuis un heading ou texte."""
        for pattern in DATE_PATTERNS:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def parse_date(date_str: str) -> datetime | None:
        """Parse une date string en datetime."""
        formats = ["%Y-%m-%d", "%d/%m/%Y", "%Y-%m"]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None

    @classmethod
    def compute_age_days(cls, date_str: str) -> int:
        """Calcule l'âge en jours."""
        dt = cls.parse_date(date_str)
        if not dt:
            return 0
        delta = datetime.now() - dt
        return max(0, delta.days)

    @staticmethod
    def extract_tags(text: str) -> list[str]:
        """Extrait les tags #hashtag d'un texte."""
        return re.findall(r"#(\w[\w-]*)", text)

    @classmethod
    def parse_file(cls, filepath: Path, project_root: Path) -> list[Section]:
        """Parse un fichier mémoire en sections."""
        try:
            content = filepath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []

        relative = str(filepath.relative_to(project_root))
        sections: list[Section] = []

        # Split par H2 (## heading)
        parts = re.split(r"^(##\s+.+)$", content, flags=re.MULTILINE)

        current_heading = ""
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue

            header_match = re.match(r"^##\s+(.+)$", stripped)
            if header_match:
                current_heading = header_match.group(1)
                continue

            if len(stripped) < 20:
                continue

            date_str = cls.extract_date(current_heading) or cls.extract_date(stripped[:200])
            age = cls.compute_age_days(date_str) if date_str else 0
            tags = cls.extract_tags(stripped)
            tokens = len(stripped) // CHARS_PER_TOKEN

            sections.append(Section(
                heading=current_heading,
                content=stripped,
                source_file=relative,
                date=date_str,
                age_days=age,
                estimated_tokens=tokens,
                tags=tags,
            ))

        return sections


# ── Context Summarizer ───────────────────────────────────────────────────────

class ContextSummarizer:
    """
    Résume les sections anciennes du contexte pour économiser des tokens.

    Stratégie de résumé :
    1. Parser les fichiers mémoire en sections datées
    2. Identifier les sections > age_threshold jours
    3. Préserver les sections avec des tags critiques
    4. Générer un digest extractif (bullet points clés)
    5. Archiver l'original + stocker le digest
    """

    def __init__(
        self,
        project_root: Path,
        age_threshold_days: int = DEFAULT_AGE_THRESHOLD_DAYS,
        learnings_age_days: int = DEFAULT_LEARNINGS_AGE_DAYS,
        max_summary_tokens: int = DEFAULT_MAX_SUMMARY_TOKENS,
        preserve_tags: list[str] | None = None,
    ):
        self.project_root = project_root
        self.age_threshold = age_threshold_days
        self.learnings_age = learnings_age_days
        self.max_summary_tokens = max_summary_tokens
        self.preserve_tags = preserve_tags or DEFAULT_PRESERVE_TAGS
        self.memory_dir = project_root / MEMORY_DIR
        self.archives_dir = project_root / ARCHIVES_DIR

    def _should_summarize(self, section: Section, file_type: str) -> bool:
        """Détermine si une section doit être résumée."""
        # Pas de date = on ne peut pas déterminer l'âge → garder
        if not section.date or section.age_days == 0:
            return False

        # Tags préservés → toujours garder intégralement
        for tag in section.tags:
            if tag.lower() in [t.lower() for t in self.preserve_tags]:
                section.preserved = True
                return False

        # Seuil d'âge selon le type
        if file_type == "learnings":
            return section.age_days > self.learnings_age
        return section.age_days > self.age_threshold

    def _extractive_summary(self, section: Section) -> str:
        """
        Résumé extractif — extrait les phrases clés sans LLM.

        Stratégie : garder la première phrase, les items de liste,
        les décisions (mots-clés), et les conclusions.
        """
        content = section.content
        lines = content.split("\n")
        summary_lines: list[str] = []
        max_chars = self.max_summary_tokens * CHARS_PER_TOKEN

        # Première phrase (souvent le TL;DR)
        first_sentence = ""
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not stripped.startswith(">"):
                first_sentence = stripped[:200]
                break

        if first_sentence:
            summary_lines.append(first_sentence)

        # Items de liste (contiennent souvent l'info clé)
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- ") or stripped.startswith("* "):
                summary_lines.append(stripped[:150])

        # Phrases avec mots-clés décisionnels
        decision_keywords = {
            "décidé", "choisi", "adopté", "rejeté", "validé", "approuvé",
            "decided", "chose", "adopted", "rejected", "approved",
            "raison", "because", "parce que", "conclusion",
        }
        for line in lines:
            stripped = line.strip()
            lower = stripped.lower()
            if any(kw in lower for kw in decision_keywords):
                if stripped not in summary_lines:
                    summary_lines.append(stripped[:150])

        # Tronquer au budget
        result = "\n".join(summary_lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "…"

        return result

    def _detect_file_type(self, filename: str) -> str:
        """Détecte le type de fichier mémoire."""
        if "decision" in filename.lower():
            return "decisions"
        elif "failure" in filename.lower() or "museum" in filename.lower():
            return "failures"
        elif "learning" in filename.lower():
            return "learnings"
        elif "shared-context" in filename.lower():
            return "shared-context"
        return "generic"

    def _discover_memory_files(self) -> list[Path]:
        """Découvre les fichiers mémoire éligibles au résumé."""
        files: list[Path] = []
        if not self.memory_dir.exists():
            return files

        # Fichiers principaux
        for name in ["decisions-log.md", "failure-museum.md", "shared-context.md"]:
            f = self.memory_dir / name
            if f.exists():
                files.append(f)

        # Learnings par agent
        learnings_dir = self.memory_dir / "agent-learnings"
        if learnings_dir.exists():
            files.extend(sorted(learnings_dir.glob("*.md")))

        return files

    def preview(self, agent_filter: str = "") -> list[Section]:
        """Prévisualise les sections qui seraient résumées."""
        files = self._discover_memory_files()
        to_summarize: list[Section] = []

        for filepath in files:
            if agent_filter and agent_filter.lower() not in filepath.name.lower():
                continue

            file_type = self._detect_file_type(filepath.name)
            sections = SectionParser.parse_file(filepath, self.project_root)

            for section in sections:
                if self._should_summarize(section, file_type):
                    to_summarize.append(section)

        return to_summarize

    def summarize(self, dry_run: bool = False) -> SummaryReport:
        """Exécute le résumé des sections anciennes."""
        report = SummaryReport()
        files = self._discover_memory_files()
        now_str = time.strftime("%Y-%m-%d")

        for filepath in files:
            file_type = self._detect_file_type(filepath.name)
            sections = SectionParser.parse_file(filepath, self.project_root)

            if not sections:
                continue

            to_summarize: list[Section] = []
            to_keep: list[Section] = []

            for section in sections:
                report.sections_processed += 1
                if self._should_summarize(section, file_type):
                    to_summarize.append(section)
                    report.tokens_before += section.estimated_tokens
                else:
                    to_keep.append(section)
                    if section.preserved:
                        report.sections_preserved += 1

            if not to_summarize:
                continue

            # Générer le digest
            digest_filename = f"digest-{filepath.stem}-{now_str}.md"
            digest = Digest(
                source_file=str(filepath.relative_to(self.project_root)),
                digest_file=f"{ARCHIVES_DIR}/{digest_filename}",
                created_at=now_str,
            )

            digest_lines = [
                f"# Digest — {filepath.stem}",
                f"> Généré le {now_str} | "
                f"{len(to_summarize)} sections résumées | "
                f"Seuil : {self.age_threshold}j",
                "",
                "---",
                "",
            ]

            for section in to_summarize:
                summary = self._extractive_summary(section)
                digest.sections_summarized += 1
                digest.original_tokens += section.estimated_tokens
                digest.sections.append(section.heading)

                digest_lines.append(f"## {section.heading}")
                if section.date:
                    digest_lines.append(f"> Date : {section.date} | Âge : {section.age_days}j")
                digest_lines.append("")
                digest_lines.append(summary)
                digest_lines.append("")
                digest_lines.append("---")
                digest_lines.append("")

            digest_content = "\n".join(digest_lines)
            digest.digest_tokens = len(digest_content) // CHARS_PER_TOKEN
            digest.compression_ratio = round(
                1 - (digest.digest_tokens / digest.original_tokens), 2
            ) if digest.original_tokens > 0 else 0.0

            report.sections_summarized += digest.sections_summarized
            report.tokens_after += digest.digest_tokens
            report.digests.append(digest)

            if not dry_run:
                # Écrire le digest
                self.archives_dir.mkdir(parents=True, exist_ok=True)
                digest_path = self.project_root / digest.digest_file
                digest_path.write_text(digest_content, encoding="utf-8")

                # Archiver l'original complet
                archive_orig = self.archives_dir / f"original-{filepath.stem}-{now_str}.md"
                try:
                    original_content = filepath.read_text(encoding="utf-8")
                    archive_orig.write_text(original_content, encoding="utf-8")
                except OSError as e:
                    report.errors.append(f"Archive original failed: {e}")

                # Réécrire le fichier sans les sections résumées,
                # avec référence au digest
                try:
                    original_content = filepath.read_text(encoding="utf-8")
                    # Ajouter la référence au digest en haut
                    ref_line = (
                        f"\n> 📦 Sections anciennes ({len(to_summarize)}) "
                        f"résumées dans [{digest_filename}]"
                        f"({ARCHIVES_DIR}/{digest_filename})\n"
                    )

                    # Reconstruire avec les sections gardées
                    new_lines = []
                    # Garder l'en-tête H1
                    h1_match = re.match(r"(#\s+.+\n)", original_content)
                    if h1_match:
                        new_lines.append(h1_match.group(1))
                    new_lines.append(ref_line)

                    for section in to_keep:
                        new_lines.append(f"\n## {section.heading}\n")
                        new_lines.append(section.content)
                        new_lines.append("")

                    filepath.write_text("\n".join(new_lines), encoding="utf-8")
                except OSError as e:
                    report.errors.append(f"Rewrite failed for {filepath.name}: {e}")

            report.digests_created += 1

        # Calcul global
        total_before = report.tokens_before
        total_after = report.tokens_after
        report.compression_ratio = round(
            1 - (total_after / total_before), 2
        ) if total_before > 0 else 0.0

        return report

    def status(self) -> list[DigestStatus]:
        """Liste les digests existants."""
        digests: list[DigestStatus] = []
        if not self.archives_dir.exists():
            return digests

        for digest_file in sorted(self.archives_dir.glob("digest-*.md")):
            try:
                content = digest_file.read_text(encoding="utf-8")
            except OSError:
                continue

            # Extraire les metadata
            source_match = re.search(r"^# Digest — (.+)$", content, re.MULTILINE)
            source = source_match.group(1) if source_match else digest_file.stem
            sections_count = content.count("\n## ")
            tokens = len(content) // CHARS_PER_TOKEN
            date_match = re.search(r"Généré le (\d{4}-\d{2}-\d{2})", content)
            created = date_match.group(1) if date_match else ""

            digests.append(DigestStatus(
                filename=digest_file.name,
                source_file=source,
                sections_count=sections_count,
                tokens=tokens,
                created_at=created,
            ))

        return digests

    def restore(self, digest_filename: str) -> bool:
        """Restaure l'original depuis l'archive."""
        # Trouver l'original correspondant
        original_name = digest_filename.replace("digest-", "original-")
        original_path = self.archives_dir / original_name

        if not original_path.exists():
            return False

        # Trouver le fichier cible
        # digest-decisions-log-2026-03-01.md → decisions-log.md
        stem_match = re.match(r"digest-(.+)-\d{4}-\d{2}-\d{2}\.md$", digest_filename)
        if not stem_match:
            return False

        target_stem = stem_match.group(1)
        target_path = self.memory_dir / f"{target_stem}.md"

        try:
            original_content = original_path.read_text(encoding="utf-8")
            target_path.write_text(original_content, encoding="utf-8")
            return True
        except OSError:
            return False


# ── Config Loading ──────────────────────────────────────────────────────────

def load_summarizer_config(project_root: Path) -> dict:
    """Charge la config depuis project-context.yaml."""
    try:
        import yaml
    except ImportError:
        return {}

    for candidate in [
        project_root / "project-context.yaml",
        project_root / "grimoire.yaml",
    ]:
        if candidate.exists():
            with open(candidate, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("token_optimizer", data.get("summarizer", {}))
    return {}


def build_summarizer_from_config(project_root: Path) -> ContextSummarizer:
    """Construit un ContextSummarizer depuis la config."""
    config = load_summarizer_config(project_root)
    return ContextSummarizer(
        project_root=project_root,
        age_threshold_days=config.get("age_threshold_days", DEFAULT_AGE_THRESHOLD_DAYS),
        learnings_age_days=config.get("learnings_age_days", DEFAULT_LEARNINGS_AGE_DAYS),
        max_summary_tokens=config.get("max_summary_tokens", DEFAULT_MAX_SUMMARY_TOKENS),
        preserve_tags=config.get("preserve_tags", DEFAULT_PRESERVE_TAGS),
    )


# ── Auto-Prune Trigger ────────────────────────────────────────────────────────────

AUTO_PRUNE_THRESHOLD = 0.80   # trigger summarize when budget >= 80%


def _import_token_budget():
    """Import dynamique de token-budget.py."""
    import importlib.util as _ilu
    mod_name = "_cs_token_budget"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    tool_path = Path(__file__).resolve().parent / "token-budget.py"
    if not tool_path.exists():
        return None
    try:
        spec = _ilu.spec_from_file_location(mod_name, tool_path)
        if not spec or not spec.loader:
            return None
        mod = _ilu.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        sys.modules.pop(mod_name, None)
        return None


def auto_prune(
    project_root: Path,
    model: str = "",
    threshold: float = AUTO_PRUNE_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    """
    Auto-prune: checks token budget and summarizes if usage exceeds threshold.

    Returns dict with: triggered (bool), budget_pct, report (SummaryReport or None).
    """
    tb_mod = _import_token_budget()
    if not tb_mod:
        return {"triggered": False, "reason": "token-budget.py unavailable"}

    enforcer_cls = getattr(tb_mod, "TokenBudgetEnforcer", None)
    if not enforcer_cls:
        return {"triggered": False, "reason": "TokenBudgetEnforcer not found"}

    default_model = getattr(tb_mod, "DEFAULT_MODEL", "claude-sonnet-4-20250514")
    enforcer = enforcer_cls(project_root, model=model or default_model)
    status = enforcer.check()

    if status.usage_pct < threshold:
        return {
            "triggered": False,
            "budget_pct": round(status.usage_pct, 4),
            "level": status.level,
        }

    # Budget above threshold — trigger summarize with aggressive settings
    summarizer = ContextSummarizer(
        project_root=project_root,
        age_threshold_days=14,     # more aggressive than default 30
        max_summary_tokens=300,
    )
    report = summarizer.summarize(dry_run=dry_run)

    return {
        "triggered": True,
        "budget_pct": round(status.usage_pct, 4),
        "level": status.level,
        "sections_summarized": report.sections_summarized,
        "tokens_before": report.tokens_before,
        "tokens_after": report.tokens_after,
        "compression_ratio": report.compression_ratio,
        "dry_run": dry_run,
    }


def mcp_context_auto_prune(
    project_root: str,
    model: str = "",
    threshold: float = AUTO_PRUNE_THRESHOLD,
    dry_run: bool = False,
) -> dict:
    """MCP tool for auto-pruning context when token budget exceeds threshold."""
    return auto_prune(
        Path(project_root).resolve(),
        model=model,
        threshold=threshold,
        dry_run=dry_run,
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def _print_report(report: SummaryReport) -> None:
    """Affiche le rapport de résumé."""
    status = "✅" if not report.errors else "⚠️"
    print(f"\n  {status} Context Summarizer — Rapport")
    print(f"  {'─' * 55}")
    print(f"  Sections traitées  : {report.sections_processed}")
    print(f"  Sections résumées  : {report.sections_summarized}")
    print(f"  Sections préservées: {report.sections_preserved}")
    print(f"  Digests créés      : {report.digests_created}")
    print(f"  Tokens avant       : {report.tokens_before:,}")
    print(f"  Tokens après       : {report.tokens_after:,}")
    print(f"  Compression        : {report.compression_ratio:.0%}")

    if report.digests:
        print("\n  Digests :")
        for d in report.digests:
            print(f"    📦 {d.digest_file}")
            print(f"       {d.sections_summarized} sections | "
                  f"{d.original_tokens:,} → {d.digest_tokens:,} tok "
                  f"({d.compression_ratio:.0%} compression)")

    if report.errors:
        print("\n  ⚠️  Erreurs :")
        for err in report.errors:
            print(f"     → {err}")
    print()


def _print_status(digests: list[DigestStatus]) -> None:
    """Affiche l'état des digests existants."""
    print(f"\n  📦 Digests existants — {len(digests)} trouvés")
    print(f"  {'─' * 60}")

    if not digests:
        print("  Aucun digest trouvé. Lancer: summarize\n")
        return

    total_tokens = 0
    for d in digests:
        total_tokens += d.tokens
        print(f"  📄 {d.filename}")
        print(f"     Source: {d.source_file} | {d.sections_count} sections | "
              f"{d.tokens:,} tok | {d.created_at}")

    print(f"\n  Total: {total_tokens:,} tokens dans {len(digests)} digests\n")


def _print_preview(sections: list[Section]) -> None:
    """Affiche la prévisualisation des sections à résumer."""
    print(f"\n  🔍 Preview — {len(sections)} sections à résumer")
    print(f"  {'─' * 60}")

    if not sections:
        print("  Aucune section éligible au résumé.\n")
        return

    total_tokens = 0
    for s in sections:
        total_tokens += s.estimated_tokens
        preserved = " 🛡️ PRESERVED" if s.preserved else ""
        print(f"  📝 [{s.source_file}] {s.heading}")
        print(f"     Date: {s.date or '?'} | Âge: {s.age_days}j | "
              f"{s.estimated_tokens:,} tok{preserved}")
        preview = s.content[:100].replace("\n", " ").strip()
        if len(s.content) > 100:
            preview += "..."
        print(f"     {preview}")
        print()

    print(f"  📊 Total: {total_tokens:,} tokens récupérables\n")


def main() -> None:
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="Context Summarizer — Résumé automatique du contexte ancien Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="Racine du projet (défaut: .)")
    parser.add_argument("--version", action="version",
                        version=f"context-summarizer {CONTEXT_SUMMARIZER_VERSION}")

    sub = parser.add_subparsers(dest="command", help="Commande à exécuter")

    # summarize
    sum_p = sub.add_parser("summarize", help="Résumer les sections anciennes")
    sum_p.add_argument("--age-threshold", type=int, default=DEFAULT_AGE_THRESHOLD_DAYS,
                       help=f"Seuil d'âge en jours (défaut: {DEFAULT_AGE_THRESHOLD_DAYS})")
    sum_p.add_argument("--max-summary-tokens", type=int, default=DEFAULT_MAX_SUMMARY_TOKENS,
                       help=f"Tokens max par résumé (défaut: {DEFAULT_MAX_SUMMARY_TOKENS})")
    sum_p.add_argument("--dry-run", action="store_true", help="Simulation sans écriture")
    sum_p.add_argument("--json", action="store_true", help="Output JSON")

    # status
    sub.add_parser("status", help="État des digests existants")

    # preview
    prev_p = sub.add_parser("preview", help="Prévisualiser les sections à résumer")
    prev_p.add_argument("--agent", default="", help="Filtrer par agent")

    # restore
    rest_p = sub.add_parser("restore", help="Restaurer un fichier original")
    rest_p.add_argument("--digest", required=True, help="Fichier digest à restaurer")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    project_root = args.project_root.resolve()

    if args.command == "summarize":
        summarizer = ContextSummarizer(
            project_root=project_root,
            age_threshold_days=args.age_threshold,
            max_summary_tokens=args.max_summary_tokens,
        )
        report = summarizer.summarize(dry_run=args.dry_run)
        if getattr(args, "json", False):
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        else:
            if args.dry_run:
                print("  ℹ️  Mode dry-run — aucune écriture")
            _print_report(report)

    elif args.command == "status":
        summarizer = build_summarizer_from_config(project_root)
        digests = summarizer.status()
        _print_status(digests)

    elif args.command == "preview":
        summarizer = build_summarizer_from_config(project_root)
        sections = summarizer.preview(agent_filter=getattr(args, "agent", ""))
        _print_preview(sections)

    elif args.command == "restore":
        summarizer = build_summarizer_from_config(project_root)
        success = summarizer.restore(args.digest)
        if success:
            print(f"\n  ✅ Restauré avec succès depuis {args.digest}\n")
        else:
            print(f"\n  ❌ Impossible de restaurer {args.digest} — archive original introuvable\n")
            sys.exit(1)


if __name__ == "__main__":
    main()
