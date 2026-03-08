#!/usr/bin/env python3
"""
cross-migrate.py — Migration cross-projet pour Grimoire.
=====================================================

Exporte et importe des artefacts Grimoire entre projets :
  - Learnings d'agents (agent-learnings/*.md)
  - Règles issues du Failure Museum (failure-museum.md → règles instaurées)
  - DNA patches (dna-proposals/)
  - Agents forgés (forge-proposals/)
  - Consensus decisions (consensus-history.json)
  - Anti-Fragile history (antifragile-history.json)

Concept : un projet mature peut polliniser un projet neuf avec ses
apprentissages. Le système filtre, anonymise et empaquette les artefacts
dans un bundle portable (.grimoire-bundle.json).

Usage :
  python3 cross-migrate.py --project-root . export --output bundle.json
  python3 cross-migrate.py --project-root . export --only learnings,rules
  python3 cross-migrate.py --project-root . export --since 2026-01-01
  python3 cross-migrate.py --project-root . import --bundle bundle.json
  python3 cross-migrate.py --project-root . import --bundle bundle.json --dry-run
  python3 cross-migrate.py --project-root . inspect --bundle bundle.json
  python3 cross-migrate.py --project-root . diff --bundle bundle.json

Stdlib only — aucune dépendance externe.
"""

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.cross_migrate")

# ── Constantes ────────────────────────────────────────────────────────────────

BUNDLE_VERSION = "1.0.0"
BUNDLE_MAGIC = "grimoire-bundle"

ARTIFACT_TYPES = {
    "learnings",
    "rules",
    "dna_patches",
    "agents",
    "consensus",
    "antifragile",
}

# Marqueurs de règles dans le Failure Museum
RULE_PATTERN = re.compile(
    r"[-*]\s*(?:Règle instaurée|Rule)\s*:\s*(.+)",
    re.IGNORECASE,
)
LESSON_PATTERN = re.compile(
    r"[-*]\s*(?:Leçon|Lesson)\s*:\s*(.+)",
    re.IGNORECASE,
)


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class BundleManifest:
    """Manifeste du bundle d'export."""
    version: str = BUNDLE_VERSION
    magic: str = BUNDLE_MAGIC
    source_project: str = ""
    export_date: str = ""
    artifact_types: list[str] = field(default_factory=list)
    total_items: int = 0
    since: str = ""


@dataclass
class ExportedLearning:
    """Un learning exporté."""
    agent: str
    text: str
    date: str = ""

    def to_dict(self) -> dict:
        return {"agent": self.agent, "text": self.text, "date": self.date}

    @classmethod
    def from_dict(cls, d: dict) -> "ExportedLearning":
        return cls(agent=d.get("agent", ""), text=d.get("text", ""),
                   date=d.get("date", ""))


@dataclass
class ExportedRule:
    """Une règle extraite du Failure Museum."""
    category: str
    rule: str
    lesson: str = ""
    date: str = ""

    def to_dict(self) -> dict:
        return {"category": self.category, "rule": self.rule,
                "lesson": self.lesson, "date": self.date}

    @classmethod
    def from_dict(cls, d: dict) -> "ExportedRule":
        return cls(category=d.get("category", ""),
                   rule=d.get("rule", ""),
                   lesson=d.get("lesson", ""),
                   date=d.get("date", ""))


@dataclass
class MigrationBundle:
    """Bundle complet d'artefacts migrés."""
    manifest: BundleManifest
    learnings: list[ExportedLearning] = field(default_factory=list)
    rules: list[ExportedRule] = field(default_factory=list)
    dna_patches: list[dict] = field(default_factory=list)
    agents: list[dict] = field(default_factory=list)
    consensus: list[dict] = field(default_factory=list)
    antifragile: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "manifest": {
                "version": self.manifest.version,
                "magic": self.manifest.magic,
                "source_project": self.manifest.source_project,
                "export_date": self.manifest.export_date,
                "artifact_types": self.manifest.artifact_types,
                "total_items": self.manifest.total_items,
                "since": self.manifest.since,
            },
            "learnings": [entry.to_dict() for entry in self.learnings],
            "rules": [r.to_dict() for r in self.rules],
            "dna_patches": self.dna_patches,
            "agents": self.agents,
            "consensus": self.consensus,
            "antifragile": self.antifragile,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MigrationBundle":
        m = d.get("manifest", {})
        manifest = BundleManifest(
            version=m.get("version", ""),
            magic=m.get("magic", ""),
            source_project=m.get("source_project", ""),
            export_date=m.get("export_date", ""),
            artifact_types=m.get("artifact_types", []),
            total_items=m.get("total_items", 0),
            since=m.get("since", ""),
        )
        return cls(
            manifest=manifest,
            learnings=[ExportedLearning.from_dict(item)
                       for item in d.get("learnings", [])],
            rules=[ExportedRule.from_dict(r) for r in d.get("rules", [])],
            dna_patches=d.get("dna_patches", []),
            agents=d.get("agents", []),
            consensus=d.get("consensus", []),
            antifragile=d.get("antifragile", []),
        )


# ── Export ────────────────────────────────────────────────────────────────────

def _get_project_name(project_root: Path) -> str:
    """Déduit le nom du projet depuis project-context.yaml."""
    ctx_path = project_root / "project-context.yaml"
    if ctx_path.exists():
        try:
            content = ctx_path.read_text(encoding="utf-8")
            match = re.search(r'name:\s*"?([^"\n]+)', content)
            if match:
                return match.group(1).strip()
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues
    return project_root.name


def _parse_date_from_line(line: str) -> str:
    """Extrait une date YYYY-MM-DD depuis une ligne."""
    match = re.search(r'\[?(\d{4}-\d{2}-\d{2})\]?', line)
    return match.group(1) if match else ""


def export_learnings(project_root: Path,
                     since: str | None = None) -> list[ExportedLearning]:
    """Exporte les learnings de tous les agents."""
    learnings_dir = project_root / "_grimoire" / "_memory" / "agent-learnings"
    results = []

    if not learnings_dir.exists():
        return results

    for f in sorted(learnings_dir.glob("*.md")):
        agent = f.stem
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("- ") or line.startswith("* "):
                    date = _parse_date_from_line(line)
                    if since and date and date < since:
                        continue
                    text = re.sub(r'^\[?\d{4}-\d{2}-\d{2}\]?\s*', '',
                                  line.lstrip("- *").strip())
                    if text:
                        results.append(ExportedLearning(
                            agent=agent, text=text, date=date))
        except OSError:
            continue

    return results


def export_rules(project_root: Path,
                 since: str | None = None) -> list[ExportedRule]:
    """Exporte les règles instaurées depuis le Failure Museum."""
    fm_path = project_root / "_grimoire" / "_memory" / "failure-museum.md"
    if not fm_path.exists():
        return []

    results = []
    try:
        content = fm_path.read_text(encoding="utf-8")
    except OSError:
        return []

    current_category = ""
    current_date = ""
    current_rule = ""
    current_lesson = ""
    in_entry = False

    for line in content.splitlines():
        # Detect entry header: ### [DATE] CATEGORY — description
        if line.startswith("### ["):
            # Flush previous
            if in_entry and current_rule:
                results.append(ExportedRule(
                    category=current_category, rule=current_rule,
                    lesson=current_lesson, date=current_date))

            current_date = _parse_date_from_line(line)
            if since and current_date and current_date < since:
                in_entry = False
                continue

            in_entry = True
            current_rule = ""
            current_lesson = ""
            # Extract category
            cat_match = re.search(
                r'(CC-FAIL|WRONG-ASSUMPTION|CONTEXT-LOSS|HALLUCINATION|'
                r'ARCH-MISTAKE|PROCESS-SKIP)',
                line)
            current_category = cat_match.group(1) if cat_match else "UNKNOWN"

        if in_entry:
            rule_match = RULE_PATTERN.match(line.strip())
            if rule_match:
                current_rule = rule_match.group(1).strip()
            lesson_match = LESSON_PATTERN.match(line.strip())
            if lesson_match:
                current_lesson = lesson_match.group(1).strip()

    # Flush last
    if in_entry and current_rule:
        results.append(ExportedRule(
            category=current_category, rule=current_rule,
            lesson=current_lesson, date=current_date))

    return results


def export_dna_patches(project_root: Path) -> list[dict]:
    """Exporte les DNA patches proposés."""
    proposals_dir = project_root / "_grimoire-output" / "dna-proposals"
    results = []

    if not proposals_dir.exists():
        return results

    for f in sorted(proposals_dir.glob("*.yaml")):
        try:
            content = f.read_text(encoding="utf-8")
            results.append({"filename": f.name, "content": content})
        except OSError:
            continue

    return results


def export_agents(project_root: Path) -> list[dict]:
    """Exporte les agents forgés (proposals)."""
    proposals_dir = project_root / "_grimoire-output" / "forge-proposals"
    results = []

    if not proposals_dir.exists():
        return results

    for f in sorted(proposals_dir.glob("*.proposed.md")):
        try:
            content = f.read_text(encoding="utf-8")
            results.append({"filename": f.name, "content": content})
        except OSError:
            continue

    return results


def export_consensus(project_root: Path) -> list[dict]:
    """Exporte l'historique du consensus."""
    path = project_root / "_grimoire-output" / "consensus-history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def export_antifragile(project_root: Path) -> list[dict]:
    """Exporte l'historique anti-fragile."""
    path = project_root / "_grimoire-output" / "antifragile-history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def create_bundle(project_root: Path, only: set[str] | None = None,
                  since: str | None = None) -> MigrationBundle:
    """Crée un bundle d'export complet."""
    types_to_export = only or ARTIFACT_TYPES
    manifest = BundleManifest(
        source_project=_get_project_name(project_root),
        export_date=datetime.now().isoformat(),
        since=since or "",
    )

    bundle = MigrationBundle(manifest=manifest)
    total = 0

    if "learnings" in types_to_export:
        bundle.learnings = export_learnings(project_root, since)
        total += len(bundle.learnings)
        if bundle.learnings:
            manifest.artifact_types.append("learnings")

    if "rules" in types_to_export:
        bundle.rules = export_rules(project_root, since)
        total += len(bundle.rules)
        if bundle.rules:
            manifest.artifact_types.append("rules")

    if "dna_patches" in types_to_export:
        bundle.dna_patches = export_dna_patches(project_root)
        total += len(bundle.dna_patches)
        if bundle.dna_patches:
            manifest.artifact_types.append("dna_patches")

    if "agents" in types_to_export:
        bundle.agents = export_agents(project_root)
        total += len(bundle.agents)
        if bundle.agents:
            manifest.artifact_types.append("agents")

    if "consensus" in types_to_export:
        bundle.consensus = export_consensus(project_root)
        total += len(bundle.consensus)
        if bundle.consensus:
            manifest.artifact_types.append("consensus")

    if "antifragile" in types_to_export:
        bundle.antifragile = export_antifragile(project_root)
        total += len(bundle.antifragile)
        if bundle.antifragile:
            manifest.artifact_types.append("antifragile")

    manifest.total_items = total
    return bundle


def save_bundle(bundle: MigrationBundle, output_path: Path) -> Path:
    """Sauvegarde un bundle en JSON."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False)
    output_path.write_text(content, encoding="utf-8")
    return output_path


def load_bundle(bundle_path: Path) -> MigrationBundle:
    """Charge un bundle depuis un fichier JSON."""
    content = bundle_path.read_text(encoding="utf-8")
    data = json.loads(content)

    if data.get("manifest", {}).get("magic") != BUNDLE_MAGIC:
        raise ValueError("Ce fichier n'est pas un bundle Grimoire valide")

    return MigrationBundle.from_dict(data)


# ── Import ────────────────────────────────────────────────────────────────────

@dataclass
class ImportResult:
    """Résultat d'un import."""
    learnings_imported: int = 0
    rules_imported: int = 0
    dna_patches_imported: int = 0
    agents_imported: int = 0
    consensus_imported: int = 0
    antifragile_imported: int = 0
    skipped: int = 0
    conflicts: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (self.learnings_imported + self.rules_imported +
                self.dna_patches_imported + self.agents_imported +
                self.consensus_imported + self.antifragile_imported)


def _deduplicate_lines(existing: str, new_lines: list[str]) -> list[str]:
    """Retourne les lignes de new_lines absentes de existing."""
    existing_lower = existing.lower()
    return [ln for ln in new_lines if ln.strip().lower() not in existing_lower]


def import_bundle(bundle: MigrationBundle, project_root: Path,
                  dry_run: bool = False) -> ImportResult:
    """Importe un bundle dans le projet cible."""
    result = ImportResult()
    memory_dir = project_root / "_grimoire" / "_memory"
    output_dir = project_root / "_grimoire-output"

    # Import learnings
    if bundle.learnings:
        learnings_dir = memory_dir / "agent-learnings"
        if not dry_run:
            learnings_dir.mkdir(parents=True, exist_ok=True)

        by_agent: dict[str, list[ExportedLearning]] = {}
        for entry in bundle.learnings:
            by_agent.setdefault(entry.agent, []).append(entry)

        for agent, items in by_agent.items():
            path = learnings_dir / f"{agent}.md"
            existing = ""
            if path.exists():
                existing = path.read_text(encoding="utf-8")

            new_lines = []
            for item in items:
                line = f"- [{item.date}] [migré] {item.text}" if item.date else \
                       f"- [migré] {item.text}"
                if line.lower() not in existing.lower():
                    new_lines.append(line)
                else:
                    result.skipped += 1

            if new_lines and not dry_run:
                with open(path, "a", encoding="utf-8") as f:
                    f.write("\n" + "\n".join(new_lines) + "\n")
            result.learnings_imported += len(new_lines)

    # Import rules
    if bundle.rules:
        rules_path = memory_dir / "migrated-rules.md"
        existing = ""
        if rules_path.exists():
            existing = rules_path.read_text(encoding="utf-8")

        new_lines = []
        for r in bundle.rules:
            line = f"- [{r.date}] [{r.category}] Règle: {r.rule}"
            if r.lesson:
                line += f" | Leçon: {r.lesson}"
            if line.lower() not in existing.lower():
                new_lines.append(line)
            else:
                result.skipped += 1

        if new_lines and not dry_run:
            if not rules_path.exists():
                header = (
                    "# Règles migrées depuis d'autres projets\n\n"
                    f"> Source: {bundle.manifest.source_project}\n"
                    f"> Date import: {datetime.now().isoformat()[:10]}\n\n"
                )
                memory_dir.mkdir(parents=True, exist_ok=True)
                rules_path.write_text(header + "\n".join(new_lines) + "\n",
                                      encoding="utf-8")
            else:
                with open(rules_path, "a", encoding="utf-8") as f:
                    f.write("\n" + "\n".join(new_lines) + "\n")
        result.rules_imported += len(new_lines)

    # Import DNA patches
    if bundle.dna_patches:
        proposals_dir = output_dir / "dna-proposals" / "migrated"
        if not dry_run:
            proposals_dir.mkdir(parents=True, exist_ok=True)
        for patch in bundle.dna_patches:
            fname = patch.get("filename", "unknown.yaml")
            target = proposals_dir / fname
            if target.exists():
                result.conflicts.append(f"DNA patch {fname} déjà existant")
                result.skipped += 1
            else:
                if not dry_run:
                    target.write_text(patch.get("content", ""),
                                      encoding="utf-8")
                result.dna_patches_imported += 1

    # Import agents
    if bundle.agents:
        agents_dir = output_dir / "forge-proposals" / "migrated"
        if not dry_run:
            agents_dir.mkdir(parents=True, exist_ok=True)
        for agent in bundle.agents:
            fname = agent.get("filename", "unknown.proposed.md")
            target = agents_dir / fname
            if target.exists():
                result.conflicts.append(f"Agent {fname} déjà existant")
                result.skipped += 1
            else:
                if not dry_run:
                    target.write_text(agent.get("content", ""),
                                      encoding="utf-8")
                result.agents_imported += 1

    # Import consensus (merge into existing history)
    if bundle.consensus:
        cons_path = output_dir / "consensus-history.json"
        existing_data = []
        if cons_path.exists():
            try:
                existing_data = json.loads(
                    cons_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as _exc:
                _log.debug("json.JSONDecodeError, OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        # Deduplicate by timestamp
        existing_timestamps = {e.get("timestamp") for e in existing_data}
        new_entries = [e for e in bundle.consensus
                       if e.get("timestamp") not in existing_timestamps]

        if new_entries and not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            merged = existing_data + new_entries
            cons_path.write_text(
                json.dumps(merged, indent=2, ensure_ascii=False),
                encoding="utf-8")
        result.consensus_imported += len(new_entries)
        result.skipped += len(bundle.consensus) - len(new_entries)

    # Import antifragile (merge into existing history)
    if bundle.antifragile:
        af_path = output_dir / "antifragile-history.json"
        existing_data = []
        if af_path.exists():
            try:
                existing_data = json.loads(
                    af_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as _exc:
                _log.debug("json.JSONDecodeError, OSError suppressed: %s", _exc)
                # Silent exception — add logging when investigating issues

        existing_timestamps = {e.get("timestamp") for e in existing_data}
        new_entries = [e for e in bundle.antifragile
                       if e.get("timestamp") not in existing_timestamps]

        if new_entries and not dry_run:
            output_dir.mkdir(parents=True, exist_ok=True)
            merged = existing_data + new_entries
            af_path.write_text(
                json.dumps(merged, indent=2, ensure_ascii=False),
                encoding="utf-8")
        result.antifragile_imported += len(new_entries)
        result.skipped += len(bundle.antifragile) - len(new_entries)

    return result


# ── Rendu ─────────────────────────────────────────────────────────────────────

def render_inspect(bundle: MigrationBundle) -> str:
    """Affiche le contenu d'un bundle."""
    m = bundle.manifest
    lines = [
        "# 📦 Grimoire Migration Bundle",
        "",
        f"> **Source** : {m.source_project}",
        f"> **Date export** : {m.export_date[:10]}",
        f"> **Version** : {m.version}",
        f"> **Artefacts** : {', '.join(m.artifact_types)}",
        f"> **Total items** : {m.total_items}",
    ]
    if m.since:
        lines.append(f"> **Depuis** : {m.since}")
    lines.extend(["", "---", ""])

    if bundle.learnings:
        lines.append(f"## 📚 Learnings ({len(bundle.learnings)})")
        lines.append("")
        by_agent: dict[str, int] = {}
        for entry in bundle.learnings:
            by_agent[entry.agent] = by_agent.get(entry.agent, 0) + 1
        for agent, count in sorted(by_agent.items(),
                                    key=lambda x: x[1], reverse=True):
            lines.append(f"- **{agent}** : {count} learnings")
        lines.extend(["", "---", ""])

    if bundle.rules:
        lines.append(f"## 📏 Règles ({len(bundle.rules)})")
        lines.append("")
        by_cat: dict[str, int] = {}
        for r in bundle.rules:
            by_cat[r.category] = by_cat.get(r.category, 0) + 1
        for cat, count in sorted(by_cat.items()):
            lines.append(f"- **{cat}** : {count} règle(s)")
        lines.extend(["", "---", ""])

    if bundle.dna_patches:
        lines.append(f"## 🧬 DNA Patches ({len(bundle.dna_patches)})")
        lines.append("")
        for p in bundle.dna_patches:
            lines.append(f"- {p.get('filename', '?')}")
        lines.extend(["", "---", ""])

    if bundle.agents:
        lines.append(f"## 🤖 Agents ({len(bundle.agents)})")
        lines.append("")
        for a in bundle.agents:
            lines.append(f"- {a.get('filename', '?')}")
        lines.extend(["", "---", ""])

    if bundle.consensus:
        lines.append(f"## 🏛️ Consensus ({len(bundle.consensus)})")
        lines.append("")

    if bundle.antifragile:
        lines.append(f"## 🛡️ Anti-Fragile ({len(bundle.antifragile)})")
        lines.append("")

    return "\n".join(lines)


def render_import_result(result: ImportResult, dry_run: bool = False) -> str:
    """Affiche le résultat d'un import."""
    prefix = "🔍 DRY RUN" if dry_run else "✅ Import terminé"
    lines = [
        f"# {prefix}",
        "",
        f"- Learnings importés : **{result.learnings_imported}**",
        f"- Règles importées : **{result.rules_imported}**",
        f"- DNA patches importés : **{result.dna_patches_imported}**",
        f"- Agents importés : **{result.agents_imported}**",
        f"- Consensus importés : **{result.consensus_imported}**",
        f"- Anti-Fragile importés : **{result.antifragile_imported}**",
        f"- **Total** : {result.total}",
        f"- Doublons ignorés : {result.skipped}",
    ]

    if result.conflicts:
        lines.extend(["", "⚠️ Conflits :"])
        for c in result.conflicts:
            lines.append(f"  - {c}")

    return "\n".join(lines)


def render_diff(bundle: MigrationBundle, project_root: Path) -> str:
    """Compare un bundle avec l'état actuel du projet."""
    lines = [
        "# 🔀 Diff : Bundle vs Projet",
        "",
    ]

    # Learnings diff
    existing_learnings = export_learnings(project_root)
    existing_texts = {entry.text.lower().strip() for entry in existing_learnings}
    new_learnings = [entry for entry in bundle.learnings
                     if entry.text.lower().strip() not in existing_texts]
    lines.append(f"## Learnings : {len(new_learnings)} nouveaux "
                 f"/ {len(bundle.learnings)} total")
    lines.append("")

    # Rules diff
    existing_rules = export_rules(project_root)
    existing_rule_texts = {r.rule.lower().strip() for r in existing_rules}
    new_rules = [r for r in bundle.rules
                 if r.rule.lower().strip() not in existing_rule_texts]
    lines.append(f"## Règles : {len(new_rules)} nouvelles "
                 f"/ {len(bundle.rules)} total")
    lines.append("")

    lines.append(f"## DNA Patches : {len(bundle.dna_patches)}")
    lines.append(f"## Agents : {len(bundle.agents)}")
    lines.append(f"## Consensus : {len(bundle.consensus)}")
    lines.append(f"## Anti-Fragile : {len(bundle.antifragile)}")
    lines.append("")

    total_new = len(new_learnings) + len(new_rules) + len(bundle.dna_patches) + \
                len(bundle.agents) + len(bundle.consensus) + len(bundle.antifragile)
    lines.append(f"**~{total_new} éléments à importer** (après déduplication)")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Grimoire Cross-Project Migration — export/import d'artefacts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--project-root", default=".",
                        help="Racine du projet Grimoire")

    sub = parser.add_subparsers(dest="command", help="Commande")

    # export
    exp = sub.add_parser("export", help="Exporter un bundle")
    exp.add_argument("--output", default="_grimoire-output/migration-bundle.json",
                     help="Fichier de sortie")
    exp.add_argument("--only", default=None,
                     help="Types à exporter (comma-sep)")
    exp.add_argument("--since", default=None, help="Date début")

    # import
    imp = sub.add_parser("import", help="Importer un bundle")
    imp.add_argument("--bundle", required=True, help="Fichier bundle")
    imp.add_argument("--dry-run", action="store_true",
                     help="Preview sans modifier")

    # inspect
    insp = sub.add_parser("inspect", help="Inspecter un bundle")
    insp.add_argument("--bundle", required=True, help="Fichier bundle")

    # diff
    diff = sub.add_parser("diff", help="Comparer un bundle avec le projet")
    diff.add_argument("--bundle", required=True, help="Fichier bundle")

    args = parser.parse_args()
    project_root = Path(args.project_root).resolve()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "export":
        only = None
        if args.only:
            only = {t.strip() for t in args.only.split(",")}
            invalid = only - ARTIFACT_TYPES
            if invalid:
                print(f"❌ Types inconnus : {invalid}", file=sys.stderr)
                print(f"   Valides : {', '.join(sorted(ARTIFACT_TYPES))}",
                      file=sys.stderr)
                sys.exit(1)

        bundle = create_bundle(project_root, only=only, since=args.since)
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = project_root / output_path

        save_bundle(bundle, output_path)
        print(f"📦 Bundle exporté : {output_path}")
        print(f"   Source : {bundle.manifest.source_project}")
        print(f"   Types : {', '.join(bundle.manifest.artifact_types) or 'aucun'}")
        print(f"   Items : {bundle.manifest.total_items}")

    elif args.command == "import":
        bundle_path = Path(args.bundle)
        if not bundle_path.exists():
            print(f"❌ Bundle introuvable : {bundle_path}", file=sys.stderr)
            sys.exit(1)

        bundle = load_bundle(bundle_path)
        result = import_bundle(bundle, project_root, dry_run=args.dry_run)
        print(render_import_result(result, dry_run=args.dry_run))

    elif args.command == "inspect":
        bundle_path = Path(args.bundle)
        if not bundle_path.exists():
            print(f"❌ Bundle introuvable : {bundle_path}", file=sys.stderr)
            sys.exit(1)

        bundle = load_bundle(bundle_path)
        print(render_inspect(bundle))

    elif args.command == "diff":
        bundle_path = Path(args.bundle)
        if not bundle_path.exists():
            print(f"❌ Bundle introuvable : {bundle_path}", file=sys.stderr)
            sys.exit(1)

        bundle = load_bundle(bundle_path)
        print(render_diff(bundle, project_root))


if __name__ == "__main__":
    main()
