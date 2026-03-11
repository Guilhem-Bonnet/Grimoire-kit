#!/usr/bin/env python3
"""
grimoire-setup.py — Configuration utilisateur BMAD.
====================================================

Synchronise les valeurs utilisateur (nom, langue, niveau) dans tous
les fichiers de configuration du projet BMAD.

Source de vérité : project-context.yaml

Fichiers mis à jour :
  - project-context.yaml            (si changement — mode interactif/CLI)
  - _bmad/{bmm,core,cis,tea,bmb}/config.yaml
  - _bmad/_memory/config.yaml
  - .github/copilot-instructions.md

Usage :
  grimoire setup                                                          # Interactif
  grimoire setup --check                                                  # Audit seulement
  grimoire setup --sync                                                   # Sync auto depuis project-context.yaml
  grimoire setup --user "Alice" --lang "EN"                               # Non-interactif
  grimoire setup --json                                                   # Sortie JSON

  python3 framework/tools/grimoire-setup.py --project-root .              # Direct

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SETUP_VERSION = "1.0.0"

# ── Data Classes ──────────────────────────────────────────────────────────────


@dataclass
class UserConfig:
    """Configuration utilisateur extraite de project-context.yaml."""

    project_name: str = ""
    user_name: str = ""
    communication_language: str = "Français"
    document_output_language: str = "Français"
    user_skill_level: str = "expert"


@dataclass
class ConfigDiff:
    """Différence détectée dans un fichier."""

    file: str
    key: str
    current: str
    expected: str


@dataclass
class SetupReport:
    """Résultat de l'opération setup."""

    diffs: list[ConfigDiff] = field(default_factory=list)
    updated_files: list[str] = field(default_factory=list)
    skipped_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_synced(self) -> bool:
        return len(self.diffs) == 0


# ── Parsing project-context.yaml (simple, no PyYAML) ─────────────────────────


def _parse_yaml_value(line: str) -> str:
    """Extract value from a simple YAML ``key: value`` line."""
    if ":" not in line:
        return ""
    val = line.split(":", 1)[1].strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        val = val[1:-1]
    return val


def load_project_context(path: Path) -> UserConfig:
    """Read project-context.yaml and extract user config fields."""
    text = path.read_text(encoding="utf-8")
    config = UserConfig()

    current_section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level sections: no leading whitespace
        if line and not line[0].isspace() and ":" in stripped:
            current_section = stripped.split(":")[0].strip()
            continue

        # Indented fields
        if current_section == "project" and stripped.startswith("name:"):
            config.project_name = _parse_yaml_value(stripped)
        elif current_section == "user":
            if stripped.startswith("name:"):
                config.user_name = _parse_yaml_value(stripped)
            elif stripped.startswith("language:"):
                config.communication_language = _parse_yaml_value(stripped)
            elif stripped.startswith("document_language:"):
                config.document_output_language = _parse_yaml_value(stripped)
            elif stripped.startswith("skill_level:"):
                config.user_skill_level = _parse_yaml_value(stripped)

    return config


# ── YAML config helpers ───────────────────────────────────────────────────────


def _read_yaml_key(text: str, key: str) -> str | None:
    """Read a simple ``key: value`` from flat YAML text."""
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+)$", re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        val = val[1:-1]
    return val


def _update_yaml_key(text: str, key: str, new_value: str) -> str:
    """Update a simple ``key: value`` in flat YAML text, preserving formatting."""
    pattern = re.compile(rf"^({re.escape(key)}:\s*).+$", re.MULTILINE)
    return pattern.sub(lambda m: m.group(1) + new_value, text)


# ── Copilot-instructions helpers ──────────────────────────────────────────────


def _update_copilot_instructions(text: str, config: UserConfig) -> str:
    """Update the ``## Project Configuration`` block in copilot-instructions.md."""
    replacements = [
        (r"(\*\*Project\*\*:\s*).+", config.project_name),
        (r"(\*\*User\*\*:\s*).+", config.user_name),
        (r"(\*\*Communication Language\*\*:\s*).+", config.communication_language),
        (r"(\*\*Document Output Language\*\*:\s*).+", config.document_output_language),
        (r"(\*\*User Skill Level\*\*:\s*).+", config.user_skill_level),
    ]
    for pattern, value in replacements:
        text = re.sub(pattern, lambda m, v=value: m.group(1) + v, text)
    return text


# ── Check / Audit ─────────────────────────────────────────────────────────────

MODULE_CONFIGS = ["bmm", "core", "cis", "tea", "bmb"]
MEMORY_CONFIG = "_memory"

# Fields present in ALL module configs
COMMON_FIELDS = {
    "user_name": "user_name",
    "communication_language": "communication_language",
    "document_output_language": "document_output_language",
}

# Extra fields only in bmm/config.yaml
BMM_EXTRA_FIELDS = {
    "project_name": "project_name",
    "user_skill_level": "user_skill_level",
}


def check_config_file(
    path: Path, config: UserConfig, module: str,
) -> list[ConfigDiff]:
    """Compare a ``config.yaml`` against the expected values."""
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    diffs: list[ConfigDiff] = []
    rel = str(path)

    for attr, yaml_key in COMMON_FIELDS.items():
        current = _read_yaml_key(text, yaml_key)
        expected = getattr(config, attr)
        if current is not None and current != expected:
            diffs.append(ConfigDiff(file=rel, key=yaml_key, current=current, expected=expected))

    if module == "bmm":
        for attr, yaml_key in BMM_EXTRA_FIELDS.items():
            current = _read_yaml_key(text, yaml_key)
            expected = getattr(config, attr)
            if current is not None and current != expected:
                diffs.append(ConfigDiff(file=rel, key=yaml_key, current=current, expected=expected))

    return diffs


def check_copilot_instructions(
    path: Path, config: UserConfig,
) -> list[ConfigDiff]:
    """Compare ``.github/copilot-instructions.md`` against expected values."""
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    diffs: list[ConfigDiff] = []
    rel = str(path)

    checks = [
        (r"\*\*Project\*\*:\s*(.+)", "Project", config.project_name),
        (r"\*\*User\*\*:\s*(.+)", "User", config.user_name),
        (r"\*\*Communication Language\*\*:\s*(.+)", "Communication Language", config.communication_language),
        (r"\*\*Document Output Language\*\*:\s*(.+)", "Document Output Language", config.document_output_language),
        (r"\*\*User Skill Level\*\*:\s*(.+)", "User Skill Level", config.user_skill_level),
    ]

    for pattern, field_name, expected in checks:
        m = re.search(pattern, text)
        if m:
            current = m.group(1).strip()
            if current != expected:
                diffs.append(ConfigDiff(file=rel, key=field_name, current=current, expected=expected))

    return diffs


# ── Apply (update files) ─────────────────────────────────────────────────────


def apply_config_file(path: Path, config: UserConfig, module: str) -> bool:
    """Update a ``config.yaml`` with the expected values. Returns True if changed."""
    if not path.exists():
        return False

    text = path.read_text(encoding="utf-8")
    original = text

    for attr, yaml_key in COMMON_FIELDS.items():
        current = _read_yaml_key(text, yaml_key)
        expected = getattr(config, attr)
        if current is not None and current != expected:
            text = _update_yaml_key(text, yaml_key, expected)

    if module == "bmm":
        for attr, yaml_key in BMM_EXTRA_FIELDS.items():
            current = _read_yaml_key(text, yaml_key)
            expected = getattr(config, attr)
            if current is not None and current != expected:
                text = _update_yaml_key(text, yaml_key, expected)

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def apply_copilot_instructions(path: Path, config: UserConfig) -> bool:
    """Update ``.github/copilot-instructions.md``. Returns True if changed."""
    if not path.exists():
        return False

    text = path.read_text(encoding="utf-8")
    updated = _update_copilot_instructions(text, config)

    if updated != text:
        path.write_text(updated, encoding="utf-8")
        return True
    return False


def apply_project_context(path: Path, config: UserConfig) -> bool:
    """Update ``project-context.yaml`` with new user values. Returns True if changed."""
    if not path.exists():
        return False

    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    new_lines: list[str] = []
    current_section = ""

    for line in lines:
        stripped = line.strip()

        # Section detection: non-indented, non-empty, non-comment lines
        if stripped and not stripped.startswith("#") and not line[0:1].isspace() and ":" in stripped:
            current_section = stripped.split(":")[0].strip()
            new_lines.append(line)
            continue

        # Rewrite indented fields
        indent = len(line) - len(line.lstrip()) if stripped else 0

        if current_section == "project" and stripped.startswith("name:"):
            line = f'{" " * indent}name: "{config.project_name}"'
        elif current_section == "user":
            if stripped.startswith("name:"):
                line = f'{" " * indent}name: "{config.user_name}"'
            elif stripped.startswith("language:"):
                line = f'{" " * indent}language: "{config.communication_language}"'
            elif stripped.startswith("document_language:"):
                line = f'{" " * indent}document_language: "{config.document_output_language}"'
            elif stripped.startswith("skill_level:"):
                line = f'{" " * indent}skill_level: "{config.user_skill_level}"'

        new_lines.append(line)

    result = "\n".join(new_lines)
    if result != text:
        path.write_text(result, encoding="utf-8")
        return True
    return False


# ── Orchestrators ─────────────────────────────────────────────────────────────


def run_check(project_root: Path, config: UserConfig) -> SetupReport:
    """Audit all config files against *config* — no changes."""
    report = SetupReport()
    bmad_dir = project_root / "_bmad"

    for module in MODULE_CONFIGS:
        cfg_path = bmad_dir / module / "config.yaml"
        report.diffs.extend(check_config_file(cfg_path, config, module))

    mem_cfg = bmad_dir / MEMORY_CONFIG / "config.yaml"
    report.diffs.extend(check_config_file(mem_cfg, config, MEMORY_CONFIG))

    ci_path = project_root / ".github" / "copilot-instructions.md"
    report.diffs.extend(check_copilot_instructions(ci_path, config))

    return report


def run_apply(project_root: Path, config: UserConfig) -> SetupReport:
    """Apply *config* values to every target file and return a report."""
    report = SetupReport()
    bmad_dir = project_root / "_bmad"

    # project-context.yaml
    pcy = project_root / "project-context.yaml"
    if apply_project_context(pcy, config):
        report.updated_files.append("project-context.yaml")

    # Module configs
    for module in MODULE_CONFIGS:
        cfg_path = bmad_dir / module / "config.yaml"
        if not cfg_path.exists():
            report.skipped_files.append(f"_bmad/{module}/config.yaml")
            continue
        if apply_config_file(cfg_path, config, module):
            report.updated_files.append(f"_bmad/{module}/config.yaml")

    # Memory config
    mem_cfg = bmad_dir / MEMORY_CONFIG / "config.yaml"
    if mem_cfg.exists():
        if apply_config_file(mem_cfg, config, MEMORY_CONFIG):
            report.updated_files.append(f"_bmad/{MEMORY_CONFIG}/config.yaml")
    else:
        report.skipped_files.append(f"_bmad/{MEMORY_CONFIG}/config.yaml")

    # Copilot instructions
    ci_path = project_root / ".github" / "copilot-instructions.md"
    if ci_path.exists():
        if apply_copilot_instructions(ci_path, config):
            report.updated_files.append(".github/copilot-instructions.md")
    else:
        report.skipped_files.append(".github/copilot-instructions.md")

    # Post-apply verification
    report.diffs = run_check(project_root, config).diffs
    return report


# ── Interactive mode ──────────────────────────────────────────────────────────


def prompt_user(current: UserConfig) -> UserConfig:
    """Interactively prompt for configuration values."""
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║       Grimoire Setup — Configuration Projet         ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    print("  Valeurs actuelles (project-context.yaml) :")
    print(f"    Project          : {current.project_name}")
    print(f"    User             : {current.user_name}")
    print(f"    Language         : {current.communication_language}")
    print(f"    Doc Language     : {current.document_output_language}")
    print(f"    Skill Level      : {current.user_skill_level}")
    print()

    def ask(prompt: str, default: str, valid: list[str] | None = None) -> str:
        hint = f" [{default}]" if default else ""
        while True:
            val = input(f"  {prompt}{hint}: ").strip()
            if not val:
                return default
            if valid and val not in valid:
                print(f"    ⚠ Choix valides : {', '.join(valid)}")
                continue
            return val

    new = UserConfig()
    new.project_name = ask("Nom du projet", current.project_name)
    new.user_name = ask("Votre nom", current.user_name)
    new.communication_language = ask("Langue de communication", current.communication_language)
    new.document_output_language = ask("Langue des documents", current.document_output_language)
    new.user_skill_level = ask(
        "Niveau (beginner/intermediate/expert)",
        current.user_skill_level,
        ["beginner", "intermediate", "expert"],
    )
    return new


# ── Output formatting ────────────────────────────────────────────────────────


def print_report(report: SetupReport, *, json_mode: bool = False) -> None:
    """Pretty-print (or JSON-dump) a setup report."""
    if json_mode:
        data = {
            "synced": report.is_synced,
            "diffs": [
                {"file": d.file, "key": d.key, "current": d.current, "expected": d.expected}
                for d in report.diffs
            ],
            "updated": report.updated_files,
            "skipped": report.skipped_files,
            "errors": report.errors,
            "timestamp": report.timestamp,
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return

    if report.updated_files:
        print("\n  ✅ Fichiers mis à jour :")
        for f in report.updated_files:
            print(f"     • {f}")

    if report.skipped_files:
        print("\n  ⏭  Fichiers absents (ignorés) :")
        for f in report.skipped_files:
            print(f"     • {f}")

    if report.errors:
        print("\n  ❌ Erreurs :")
        for e in report.errors:
            print(f"     • {e}")

    if report.diffs:
        print("\n  🔶 Différences restantes :")
        for d in report.diffs:
            print(f"     • {d.file} → {d.key}: '{d.current}' ≠ '{d.expected}'")
    elif not report.errors:
        print("\n  ✅ Tous les fichiers sont synchronisés.")

    print()


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grimoire setup",
        description=(
            "Configuration utilisateur BMAD — synchronise les valeurs "
            "dans tous les fichiers config."
        ),
    )
    p.add_argument("--project-root", type=Path, required=True, help="Racine du projet BMAD")
    p.add_argument("--check", action="store_true", help="Audit seulement — aucune modification")
    p.add_argument("--sync", action="store_true", help="Sync auto depuis project-context.yaml")
    p.add_argument("--json", action="store_true", help="Sortie JSON")

    g = p.add_argument_group("Valeurs utilisateur (mode non-interactif)")
    g.add_argument("--project", type=str, help="Nom du projet")
    g.add_argument("--user", type=str, help="Nom utilisateur")
    g.add_argument("--lang", type=str, help="Langue de communication")
    g.add_argument("--doc-lang", type=str, help="Langue des documents")
    g.add_argument(
        "--skill-level",
        type=str,
        choices=["beginner", "intermediate", "expert"],
        help="Niveau de compétence",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {SETUP_VERSION}")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = args.project_root.resolve()
    pcy_path = root / "project-context.yaml"

    if not pcy_path.exists():
        msg = f"❌ project-context.yaml introuvable dans {root}"
        if args.json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
            print("   Lancez d'abord grimoire-init.sh ou créez le fichier manuellement.", file=sys.stderr)
        return 1

    current = load_project_context(pcy_path)

    # ── --check : audit only ──────────────────────────────────────────────
    if args.check:
        report = run_check(root, current)
        print_report(report, json_mode=args.json)
        return 0 if report.is_synced else 1

    # ── --sync : re-sync from project-context.yaml ────────────────────────
    if args.sync:
        report = run_apply(root, current)
        print_report(report, json_mode=args.json)
        return 0 if not report.errors else 1

    # ── CLI overrides (non-interactive) ───────────────────────────────────
    has_cli = any([args.project, args.user, args.lang, args.doc_lang, args.skill_level])
    if has_cli:
        target = UserConfig(
            project_name=args.project or current.project_name,
            user_name=args.user or current.user_name,
            communication_language=args.lang or current.communication_language,
            document_output_language=args.doc_lang or current.document_output_language,
            user_skill_level=args.skill_level or current.user_skill_level,
        )
        report = run_apply(root, target)
        print_report(report, json_mode=args.json)
        return 0 if not report.errors else 1

    # ── Interactive mode ──────────────────────────────────────────────────
    target = prompt_user(current)

    # Show preview
    pre_report = run_check(root, target)
    if pre_report.is_synced:
        print("\n  ✅ Rien à changer — config déjà synchronisée.\n")
        return 0

    print(f"\n  📝 {len(pre_report.diffs)} modification(s) à appliquer :")
    for d in pre_report.diffs:
        print(f"     • {d.key}: '{d.current}' → '{d.expected}'")

    confirm = input("\n  Appliquer ? [O/n] : ").strip().lower()
    if confirm and confirm not in ("o", "oui", "y", "yes", ""):
        print("  Annulé.")
        return 0

    report = run_apply(root, target)
    print_report(report, json_mode=args.json)
    return 0 if not report.errors else 1


if __name__ == "__main__":
    sys.exit(main())
