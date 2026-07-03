#!/usr/bin/env python3
"""
vscode-stability-guard.py
=========================

Hardening pragmatique de VS Code pour les sessions longues:
- applique un profil de stabilite sur `.vscode/settings.json`
- inspecte l'etat runtime via `code --status` (optionnel)

Usage:
  python3 framework/tools/vscode-stability-guard.py --project-root . --check
  python3 framework/tools/vscode-stability-guard.py --project-root . --apply
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

VSCODE_STABILITY_GUARD_VERSION = "1.1.0"

# Ces cles sont volontairement simples (scalaires) pour rester compatibles
# avec des settings JSONC deja commentes.
STABILITY_SETTINGS: dict[str, str | int | bool] = {
    "terminal.integrated.enablePersistentSessions": False,
    "terminal.integrated.persistentSessionReviveProcess": "never",
    "terminal.integrated.scrollback": 2000,
    "terminal.integrated.persistentSessionScrollback": 200,
    "terminal.integrated.hideOnStartup": "whenEmpty",
    "terminal.integrated.gpuAcceleration": "off",
    "typescript.tsserver.maxTsServerMemory": 1024,
}


def _expected_stability_settings(project_root: Path) -> dict[str, str | int | bool]:
    settings = dict(STABILITY_SETTINGS)
    tasks_path = project_root / ".vscode" / "tasks.json"
    allow_automatic_tasks = False

    if tasks_path.exists():
        try:
            tasks_text = tasks_path.read_text(encoding="utf-8")
        except OSError:
            tasks_text = ""
        allow_automatic_tasks = '"runOn": "folderOpen"' in tasks_text

    settings["task.allowAutomaticTasks"] = "on" if allow_automatic_tasks else "off"
    return settings


@dataclass(frozen=True, slots=True)
class SettingDelta:
    key: str
    action: str  # inserted | updated
    old_value: str | None
    new_value: str


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    pty_hosts: int = 0
    zsh_total: int = 0
    zsh_interactive: int = 0
    zsh_safe: int = 0
    extension_hosts: int = 0


@dataclass(slots=True)
class GuardReport:
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    settings_file: str = ""
    created_settings_file: bool = False
    settings_updates: list[SettingDelta] = field(default_factory=list)
    missing_keys: list[str] = field(default_factory=list)
    runtime_snapshot: RuntimeSnapshot | None = None
    runtime_warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _to_json_literal(value: str | int | bool) -> str:
    return json.dumps(value, ensure_ascii=False)


def _extract_setting_value(text: str, key: str) -> str | None:
    pattern = re.compile(rf'(?m)^\s*"{re.escape(key)}"\s*:\s*([^,\n]+)')
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1).strip()


def _upsert_setting_line(text: str, key: str, value: str | int | bool) -> tuple[str, SettingDelta | None]:
    literal = _to_json_literal(value)
    pattern = re.compile(
        rf'(?m)^(?P<indent>\s*)"{re.escape(key)}"\s*:\s*(?P<value>[^,\n]+)(?P<suffix>\s*,?\s*(?://.*)?)$'
    )

    match = pattern.search(text)
    if match:
        current = match.group("value").strip()
        if current == literal:
            return text, None
        replacement = f'{match.group("indent")}"{key}": {literal}{match.group("suffix")}'
        updated = pattern.sub(replacement, text, count=1)
        return updated, SettingDelta(key=key, action="updated", old_value=current, new_value=literal)

    if re.match(r"^\s*\{\s*\}\s*$", text):
        created = f'{{\n  "{key}": {literal}\n}}\n'
        return created, SettingDelta(key=key, action="inserted", old_value=None, new_value=literal)

    obj_start = text.find("{")
    if obj_start == -1:
        raise ValueError(".vscode/settings.json n'est pas un objet JSON/JSONC valide")

    newline = "\r\n" if "\r\n" in text else "\n"
    insertion = f'{newline}  "{key}": {literal},'
    updated = text[: obj_start + 1] + insertion + text[obj_start + 1 :]
    return updated, SettingDelta(key=key, action="inserted", old_value=None, new_value=literal)


def apply_stability_settings(project_root: Path) -> tuple[Path, bool, list[SettingDelta]]:
    vscode_dir = project_root / ".vscode"
    settings_path = vscode_dir / "settings.json"
    expected_settings = _expected_stability_settings(project_root)

    created_file = False
    if settings_path.exists():
        text = settings_path.read_text(encoding="utf-8")
    else:
        vscode_dir.mkdir(parents=True, exist_ok=True)
        text = "{}\n"
        created_file = True

    deltas: list[SettingDelta] = []
    for key, value in expected_settings.items():
        text, delta = _upsert_setting_line(text, key, value)
        if delta is not None:
            deltas.append(delta)

    if created_file or deltas:
        settings_path.write_text(text, encoding="utf-8")

    return settings_path, created_file, deltas


def check_stability_settings(project_root: Path) -> tuple[Path, list[str]]:
    settings_path = project_root / ".vscode" / "settings.json"
    expected_settings = _expected_stability_settings(project_root)
    if not settings_path.exists():
        return settings_path, list(expected_settings.keys())

    text = settings_path.read_text(encoding="utf-8")
    missing: list[str] = []

    for key, expected in expected_settings.items():
        current = _extract_setting_value(text, key)
        if current != _to_json_literal(expected):
            missing.append(key)

    return settings_path, missing


def parse_code_status_text(status_text: str) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        pty_hosts=len(re.findall(r"\bpty-host\b", status_text)),
        zsh_total=len(re.findall(r"/usr/bin/zsh(?:\s|$)", status_text)),
        zsh_interactive=len(re.findall(r"/usr/bin/zsh -i\b", status_text)),
        zsh_safe=len(re.findall(r"/usr/bin/zsh -f\b", status_text)),
        extension_hosts=len(re.findall(r"\bextension-host\b", status_text)),
    )


def collect_runtime_snapshot() -> tuple[RuntimeSnapshot | None, list[str]]:
    warnings: list[str] = []

    if shutil.which("code") is None:
        warnings.append("Commande 'code' indisponible: diagnostic runtime ignore.")
        return None, warnings

    proc = subprocess.run(
        ["code", "--status"],
        check=False,
        capture_output=True,
        text=True,
    )
    raw = (proc.stdout or "") + (proc.stderr or "")

    if proc.returncode != 0 or not raw.strip():
        warnings.append("Impossible de lire 'code --status' pour le diagnostic runtime.")
        return None, warnings

    snapshot = parse_code_status_text(raw)

    if snapshot.zsh_interactive >= 12:
        warnings.append(
            f"Accumulation de shells interactifs detectee ({snapshot.zsh_interactive} '/usr/bin/zsh -i'). "
            "Fermer les terminaux inactifs dans VS Code pour soulager le pty-host."
        )
    if snapshot.zsh_safe >= 6:
        warnings.append(
            f"Accumulation de shells d'agent detectee ({snapshot.zsh_safe} '/usr/bin/zsh -f'). "
            "Lancer la tache 'grimoire: vscode-agent-terminals-prune' (ou '...-maintenance') pour nettoyer les shells inactifs."
        )
    if snapshot.extension_hosts >= 3:
        warnings.append(
            f"Plusieurs extension-host actifs ({snapshot.extension_hosts}). "
            "Verifier les extensions lourdes avec 'Developer: Show Running Extensions'."
        )

    return snapshot, warnings


def _print_human_report(report: GuardReport, check_mode: bool, quiet: bool) -> None:
    if quiet:
        return

    print("\nVS Code Stability Guard")
    print("=======================")
    print(f"Settings file: {report.settings_file}")

    if check_mode:
        if report.missing_keys:
            print(f"Missing or drifted keys: {len(report.missing_keys)}")
            for key in report.missing_keys:
                print(f"  - {key}")
        else:
            print("Settings profile: OK")
    else:
        if report.created_settings_file:
            print("settings.json created.")
        if report.settings_updates:
            print(f"Settings updated: {len(report.settings_updates)}")
            for delta in report.settings_updates:
                print(f"  - {delta.action}: {delta.key}")
        else:
            print("No settings changes needed.")

    if report.runtime_snapshot is not None:
        snap = report.runtime_snapshot
        print(
            "Runtime snapshot: "
            f"pty-host={snap.pty_hosts}, zsh={snap.zsh_total}, zsh -i={snap.zsh_interactive}, "
            f"zsh -f={snap.zsh_safe}, extension-host={snap.extension_hosts}"
        )

    if report.runtime_warnings:
        print("Runtime warnings:")
        for warning in report.runtime_warnings:
            print(f"  - {warning}")

    if report.errors:
        print("Errors:")
        for error in report.errors:
            print(f"  - {error}")

    print()


def _print_json_report(report: GuardReport) -> None:
    payload = {
        "timestamp": report.timestamp,
        "settings_file": report.settings_file,
        "created_settings_file": report.created_settings_file,
        "settings_updates": [
            {
                "key": delta.key,
                "action": delta.action,
                "old_value": delta.old_value,
                "new_value": delta.new_value,
            }
            for delta in report.settings_updates
        ],
        "missing_keys": report.missing_keys,
        "runtime_snapshot": None
        if report.runtime_snapshot is None
        else {
            "pty_hosts": report.runtime_snapshot.pty_hosts,
            "zsh_total": report.runtime_snapshot.zsh_total,
            "zsh_interactive": report.runtime_snapshot.zsh_interactive,
            "zsh_safe": report.runtime_snapshot.zsh_safe,
            "extension_hosts": report.runtime_snapshot.extension_hosts,
        },
        "runtime_warnings": report.runtime_warnings,
        "errors": report.errors,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vscode-stability-guard",
        description="Applique et verifie un profil de stabilite VS Code (sessions longues).",
    )
    parser.add_argument("--project-root", type=Path, required=True, help="Racine du projet")
    parser.add_argument("--check", action="store_true", help="Verifie le profil sans modifier")
    parser.add_argument("--apply", action="store_true", help="Applique le profil dans .vscode/settings.json")
    parser.add_argument("--skip-runtime", action="store_true", help="Ignore le diagnostic runtime via code --status")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--quiet", action="store_true", help="Sortie humaine minimale")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VSCODE_STABILITY_GUARD_VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.check and args.apply:
        parser.error("--check et --apply sont mutuellement exclusifs")

    # Mode par defaut: check (non destructif)
    check_mode = not args.apply

    report = GuardReport()
    project_root = args.project_root.resolve()

    try:
        if check_mode:
            settings_path, missing = check_stability_settings(project_root)
            report.settings_file = str(settings_path)
            report.missing_keys = missing
        else:
            settings_path, created, updates = apply_stability_settings(project_root)
            report.settings_file = str(settings_path)
            report.created_settings_file = created
            report.settings_updates = updates
    except Exception as exc:  # pragma: no cover - protection CLI
        report.errors.append(str(exc))

    if not args.skip_runtime:
        snapshot, runtime_warnings = collect_runtime_snapshot()
        report.runtime_snapshot = snapshot
        report.runtime_warnings.extend(runtime_warnings)

    if args.json:
        _print_json_report(report)
    else:
        _print_human_report(report, check_mode=check_mode, quiet=args.quiet)

    if report.errors:
        return 1
    if check_mode and report.missing_keys:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
