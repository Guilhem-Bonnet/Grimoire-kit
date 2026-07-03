#!/usr/bin/env python3
"""
preflight-check.py — Vérification pré-exécution Grimoire.
========================================================

Scanne l'environnement avant qu'un agent commence une tâche/story.
Détecte les problèmes AVANT qu'ils ne causent des échecs :
  - Dépendances manquantes (outils CLI requis par le DNA)
  - Fichiers référencés mais inexistants
  - Conflits de branches Git
  - État de la mémoire (contradictions, session périmée)
  - Requêtes inter-agents en attente
  - Tokens budget estimé vs disponible

Inspiré de la "mise en place" en cuisine : tout préparer avant de cuisiner.

Usage :
  python3 preflight-check.py --project-root .                       # Check global
  python3 preflight-check.py --project-root . --agent forge         # Pour un agent
  python3 preflight-check.py --project-root . --story STORY-42.md   # Pour une story
  python3 preflight-check.py --project-root . --fix                 # Tenter l'auto-correction
  python3 preflight-check.py --project-root . --json                # Sortie JSON

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_log = logging.getLogger("grimoire.preflight_check")

# ── Constantes ────────────────────────────────────────────────────────────────

PREFLIGHT_VERSION = "1.3.1"

# Sévérité
class Severity:
    BLOCKER = "🔴 BLOCKER"
    WARNING = "🟡 WARNING"
    INFO = "🟢 INFO"


DURABLE_CODE_SUFFIXES = frozenset({
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".sh",
})

ROOT_WORKSPACE_MARKERS = frozenset({
    "package.json",
    "pnpm-workspace.yaml",
    "tsconfig.json",
    "vite.config.ts",
    "vitest.config.ts",
    "pyproject.toml",
})

ALLOWED_DURABLE_CODE_PREFIXES = (
    "grimoire-kit/",
    "grimoire-game-assets/tools/",
    ".github/hooks/scripts/",
)

KIT_PACKAGE_DURABLE_CODE_PREFIXES = (
    "framework/",
    "src/",
    "tests/",
    "apps/",
)

ALLOWED_DURABLE_CODE_FILES = frozenset({"grimoire.sh"})

KIT_PACKAGE_DURABLE_CODE_FILES = frozenset({
    "pyproject.toml",
    "grimoire-init.sh",
})

CANONICAL_EXEMPT_PREFIXES = (
    "_grimoire/",
    "_grimoire-runtime/",
    "_grimoire-output/",
    "_grimoire-runtime-output/",
    "docs/",
    ".github/",
    "contracts/",
    "team-build/",
    "team-ops/",
    "team-vision/",
    "site/",
    "icon/",
)

ARCHIVED_AGENT_PREFIX = ".github/agents/_archived/"
SURFACE_INDEX_RELATIVE_PATH = "_grimoire-runtime/_config/agent-surface-index.csv"
WRAPPER_SPEC_RELATIVE_PATH = "_grimoire-runtime/_config/agent-wrapper-spec.json"

KIT_AGENT_BRIDGE_REQUIREMENTS = {
    "AGENTS.md": (
        "../.github/agents/grimoire-master.agent.md",
        "../_grimoire-runtime/core/agents/grimoire-master.md",
    ),
    "CLAUDE.md": (
        "../.github/copilot-instructions.md",
        "../.github/agents/grimoire-master.agent.md",
    ),
}


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Check:
    """Résultat d'une vérification unitaire."""
    name: str
    severity: str
    message: str
    fix_hint: str = ""
    auto_fixable: bool = False
    fixed: bool = False

    @property
    def is_blocker(self) -> bool:
        return "BLOCKER" in self.severity


@dataclass
class PreflightReport:
    """Rapport complet de pre-flight."""
    agent: str = ""
    story: str = ""
    checks: list[Check] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.is_blocker]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if "WARNING" in c.severity]

    @property
    def infos(self) -> list[Check]:
        return [c for c in self.checks if "INFO" in c.severity]

    @property
    def go_nogo(self) -> str:
        if self.blockers:
            return "🔴 NO-GO"
        if self.warnings:
            return "🟡 GO (avec réserves)"
        return "🟢 GO"


# ── Checks ───────────────────────────────────────────────────────────────────

def check_grimoire_structure(project_root: Path) -> list[Check]:
    """Vérifie la structure Grimoire minimale."""
    checks = []
    required = [
        ("_grimoire", "Dossier _grimoire"),
        ("_grimoire/_config", "Dossier config"),
        ("_grimoire/_memory", "Dossier mémoire"),
    ]

    for path_str, label in required:
        if not (project_root / path_str).exists():
            checks.append(Check(
                name="structure",
                severity=Severity.BLOCKER,
                message=f"{label} manquant : {path_str}",
                fix_hint="Exécuter grimoire-init.sh pour initialiser le projet",
            ))

    # Fichiers critiques
    critical_files = [
        ("_grimoire/_memory/shared-context.md", "Shared context"),
    ]
    custom_dir = project_root / "_grimoire" / "_config" / "custom"
    if custom_dir.exists():
        critical_files.append(
            ("_grimoire/_config/custom/agent-base.md", "Agent base protocol")
        )

    for path_str, label in critical_files:
        fpath = project_root / path_str
        if not fpath.exists():
            checks.append(Check(
                name="critical-file",
                severity=Severity.WARNING,
                message=f"{label} manquant : {path_str}",
                fix_hint="Créer le fichier via grimoire-init.sh ou manuellement",
            ))
        elif fpath.stat().st_size == 0:
            checks.append(Check(
                name="empty-file",
                severity=Severity.WARNING,
                message=f"{label} est vide : {path_str}",
            ))

    return checks


def check_tools_available(project_root: Path) -> list[Check]:
    """Vérifie que les outils CLI requis sont disponibles."""
    checks = []

    # Outils toujours requis
    core_tools = ["git", "python3"]
    for tool in core_tools:
        if not shutil.which(tool):
            checks.append(Check(
                name="tool-missing",
                severity=Severity.BLOCKER,
                message=f"Outil requis manquant : {tool}",
                fix_hint=f"Installer {tool} via le gestionnaire de paquets",
            ))

    # Outils du DNA actif (si archetype-dna.yaml existe)
    dna_files = list(project_root.glob("_grimoire/**/archetype.dna.yaml"))
    for dna in dna_files:
        try:
            content = dna.read_text(encoding="utf-8")
            # Parse simple des tools_required
            in_tools = False
            for line in content.split("\n"):
                if "tools_required:" in line:
                    in_tools = True
                    continue
                if in_tools:
                    if line.strip().startswith("- "):
                        # Extraire le nom de la commande
                        m = re.search(r'check_command:\s*"?([^"\s]+)', line)
                        if m:
                            cmd = m.group(1)
                            if not shutil.which(cmd):
                                checks.append(Check(
                                    name="dna-tool-missing",
                                    severity=Severity.WARNING,
                                    message=f"Outil DNA requis manquant : {cmd} (dans {dna.name})",
                                    fix_hint=f"Installer {cmd} avant utilisation",
                                ))
                    elif not line.startswith(" ") and not line.startswith("\t"):
                        in_tools = False
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return checks


def check_git_state(project_root: Path) -> list[Check]:
    """Vérifie l'état Git."""
    checks = []

    if not (project_root / ".git").exists():
        checks.append(Check(
            name="no-git",
            severity=Severity.INFO,
            message="Pas de dépôt Git détecté",
        ))
        return checks

    try:
        # Vérifier si des conflits de merge existent
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if result.stdout.strip():
            conflicted = result.stdout.strip().split("\n")
            checks.append(Check(
                name="merge-conflict",
                severity=Severity.BLOCKER,
                message=f"{len(conflicted)} fichier(s) en conflit de merge : {', '.join(conflicted[:3])}",
                fix_hint="Résoudre les conflits avant de continuer",
            ))

        # Vérifier les modifications non committées dans _grimoire
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", "_grimoire/"],
            capture_output=True, text=True, cwd=project_root, timeout=10,
        )
        if result.stdout.strip():
            lines = result.stdout.strip().split("\n")
            checks.append(Check(
                name="uncommitted-grimoire",
                severity=Severity.WARNING,
                message=f"{len(lines)} modification(s) non committée(s) dans _grimoire/",
                fix_hint="git add _grimoire/ && git commit -m 'chore: update grimoire config'",
            ))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        checks.append(Check(
            name="git-error",
            severity=Severity.INFO,
            message="Impossible d'exécuter les commandes git",
        ))

    return checks


def _parse_porcelain_paths(output: str) -> list[str]:
    """Extrait les chemins depuis `git status --porcelain`."""
    paths: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        payload = line[3:].strip()
        if " -> " in payload:
            _old, new_path = payload.split(" -> ", 1)
            payload = new_path.strip()
        if payload:
            paths.append(payload)
    return paths


def _looks_like_durable_code_path(path_str: str) -> bool:
    """Retourne True si le chemin ressemble à de la logique durable."""
    path = Path(path_str)
    return path.name in ROOT_WORKSPACE_MARKERS or path.suffix.lower() in DURABLE_CODE_SUFFIXES


def _is_kit_package_root(project_root: Path) -> bool:
    """Retourne True si le preflight s'exécute depuis la racine package de grimoire-kit."""
    return (
        (project_root / "pyproject.toml").is_file()
        and (project_root / "framework" / "tools").is_dir()
        and (project_root / "src" / "grimoire").is_dir()
    )


def _durable_code_prefixes_for_root(project_root: Path) -> tuple[str, ...]:
    """Résout les prefixes de landing zone autorisés selon le contexte racine."""
    if _is_kit_package_root(project_root):
        return ALLOWED_DURABLE_CODE_PREFIXES + KIT_PACKAGE_DURABLE_CODE_PREFIXES
    return ALLOWED_DURABLE_CODE_PREFIXES


def _durable_code_files_for_root(project_root: Path) -> frozenset[str]:
    """Résout les fichiers canoniques autorisés selon le contexte racine."""
    if _is_kit_package_root(project_root):
        return ALLOWED_DURABLE_CODE_FILES | KIT_PACKAGE_DURABLE_CODE_FILES
    return ALLOWED_DURABLE_CODE_FILES


def _check_kit_agent_bridge(project_root: Path) -> list[Check]:
    """Vérifie que grimoire-kit pointe bien vers l'orchestrateur parent."""
    checks: list[Check] = []

    for bridge_name, required_refs in KIT_AGENT_BRIDGE_REQUIREMENTS.items():
        bridge_path = project_root / bridge_name
        if not bridge_path.is_file():
            checks.append(Check(
                name="kit-agent-bridge",
                severity=Severity.WARNING,
                message=f"Bridge agentique manquant dans grimoire-kit : {bridge_name}",
                fix_hint="Ajouter le bridge vers ../.github/agents/grimoire-master.agent.md et le runtime parent pour conserver le même comportement qu'à la racine.",
            ))
            continue

        try:
            content = bridge_path.read_text(encoding="utf-8")
        except OSError:
            checks.append(Check(
                name="kit-agent-bridge",
                severity=Severity.WARNING,
                message=f"Impossible de lire le bridge agentique {bridge_name}",
                fix_hint="Vérifier les permissions et le contenu du fichier de bridge.",
            ))
            continue

        missing_refs = [ref for ref in required_refs if ref not in content]
        if missing_refs:
            preview = ", ".join(missing_refs)
            checks.append(Check(
                name="kit-agent-bridge",
                severity=Severity.WARNING,
                message=f"Bridge agentique incomplet dans {bridge_name} : référence(s) absente(s) {preview}",
                fix_hint="Aligner le bridge sur l'orchestrateur parent pour garantir le même comportement dans grimoire-kit.",
            ))

    if not checks:
        checks.append(Check(
            name="kit-agent-bridge",
            severity=Severity.INFO,
            message="Bridges AGENTS.md / CLAUDE.md de grimoire-kit alignés avec l'orchestrateur parent",
        ))

    return checks


def check_structure_governance(project_root: Path) -> list[Check]:
    """Vérifie les écarts de landing zone et l'usage des surfaces archivées."""
    checks: list[Check] = []
    is_kit_root = _is_kit_package_root(project_root)
    allowed_prefixes = _durable_code_prefixes_for_root(project_root)
    allowed_files = _durable_code_files_for_root(project_root)

    if is_kit_root:
        checks.extend(_check_kit_agent_bridge(project_root))

    root_markers = [marker for marker in sorted(ROOT_WORKSPACE_MARKERS) if (project_root / marker).exists()]
    if root_markers and not is_kit_root:
        preview = ", ".join(root_markers[:3])
        suffix = "" if len(root_markers) <= 3 else " ..."
        checks.append(Check(
            name="root-workspace-marker",
            severity=Severity.WARNING,
            message=f"Toolchain durable détectée à la racine : {preview}{suffix}",
            fix_hint="Déplacer la toolchain produit vers grimoire-kit/ sauf justification explicite de wrapper local.",
        ))

    if not (project_root / ".git").exists():
        return checks

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=10,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return checks

    changed_paths = _parse_porcelain_paths(result.stdout)
    if not changed_paths:
        return checks

    archived_changes = sorted(path for path in changed_paths if path.startswith(ARCHIVED_AGENT_PREFIX))
    if archived_changes:
        preview = ", ".join(archived_changes[:3])
        suffix = "" if len(archived_changes) <= 3 else " ..."
        checks.append(Check(
            name="archived-agent-change",
            severity=Severity.WARNING,
            message=f"Modification(s) dans la zone archivée : {preview}{suffix}",
            fix_hint="Vérifier si le changement doit viser l'artefact actif sous .github/agents/ plutôt que _archived/.",
        ))

    offzone_paths: list[str] = []
    for path_str in changed_paths:
        if path_str in allowed_files:
            continue
        if any(path_str.startswith(prefix) for prefix in allowed_prefixes):
            continue
        if any(path_str.startswith(prefix) for prefix in CANONICAL_EXEMPT_PREFIXES):
            continue
        if _looks_like_durable_code_path(path_str):
            offzone_paths.append(path_str)

    if offzone_paths:
        preview = ", ".join(offzone_paths[:3])
        suffix = "" if len(offzone_paths) <= 3 else " ..."
        checks.append(Check(
            name="landing-zone-drift",
            severity=Severity.WARNING,
            message=f"Logique durable hors landing zone canonique : {preview}{suffix}",
            fix_hint="Déplacer la logique durable vers grimoire-kit/ ou limiter le fichier à un wrapper local explicitement justifié.",
        ))

    return checks


def _load_agent_lint_module():
    """Charge agent-lint.py comme module compagnon pour les checks de synchro."""
    module_key = "_grimoire_agent_lint_preflight"
    if module_key in sys.modules:
        return sys.modules[module_key]

    tool_path = Path(__file__).parent / "agent-lint.py"
    if not tool_path.is_file():
        return None

    spec = importlib.util.spec_from_file_location(module_key, tool_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


def _load_hook_safety_gate_module():
    """Charge hook-safety-gate.py comme module compagnon pour les checks hooks."""
    module_key = "_grimoire_hook_safety_gate_preflight"
    if module_key in sys.modules:
        return sys.modules[module_key]

    tool_path = Path(__file__).parent / "hook-safety-gate.py"
    if not tool_path.is_file():
        return None

    spec = importlib.util.spec_from_file_location(module_key, tool_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


def _load_guardrail_rules_module():
    """Charge guardrail-policy-rules.py pour exposer l'état réel des règles."""
    module_key = "_grimoire_guardrail_rules_preflight"
    if module_key in sys.modules:
        return sys.modules[module_key]

    tool_path = Path(__file__).parent / "guardrail-policy-rules.py"
    if not tool_path.is_file():
        return None

    spec = importlib.util.spec_from_file_location(module_key, tool_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


def _resolve_agent_runtime_root(project_root: Path, agent_lint) -> Path:
    """Résout la racine agentique effective sans perdre le contexte du package courant."""
    resolver = getattr(agent_lint, "resolve_agent_project_root", None)
    if callable(resolver):
        resolved = resolver(project_root)
        if isinstance(resolved, Path):
            return resolved
    return project_root


def check_surface_index_health(project_root: Path) -> list[Check]:
    """Vérifie la présence et la synchro de l'index de surface agentique."""
    checks: list[Check] = []

    agent_lint = _load_agent_lint_module()
    if agent_lint is None:
        checks.append(Check(
            name="surface-index-loader",
            severity=Severity.WARNING,
            message="Impossible de charger agent-lint.py pour vérifier agent-surface-index.csv",
            fix_hint="Vérifier la présence de grimoire-kit/framework/tools/agent-lint.py",
        ))
        return checks

    runtime_root = _resolve_agent_runtime_root(project_root, agent_lint)
    runtime_dir = runtime_root / "_grimoire-runtime"
    if not runtime_dir.is_dir():
        return checks

    try:
        manifest = agent_lint.load_manifest(runtime_root)
        records = agent_lint.build_surface_index(runtime_root, manifest)
        findings = agent_lint.lint_surface_index_sync(runtime_root, records)
    except Exception as exc:  # pragma: no cover - defensive, should not happen in nominal flow
        checks.append(Check(
            name="surface-index-sync",
            severity=Severity.WARNING,
            message=f"Vérification agent-surface-index.csv échouée : {exc}",
            fix_hint="Relancer agent-lint.py --project-root . --write-surface-index pour régénérer l'index.",
        ))
        return checks

    for finding in findings:
        checks.append(Check(
            name=finding.rule,
            severity=Severity.WARNING,
            message=finding.message,
            fix_hint=finding.fix_hint,
        ))

    surface_index_path = runtime_root / SURFACE_INDEX_RELATIVE_PATH
    if surface_index_path.is_file() and not findings:
        checks.append(Check(
            name="surface-index-sync",
            severity=Severity.INFO,
            message="agent-surface-index.csv présent et synchronisé",
        ))

    return checks


def check_wrapper_health(project_root: Path) -> list[Check]:
    """Vérifie la présence et la synchro des wrappers workspace gérés."""
    checks: list[Check] = []

    agent_lint = _load_agent_lint_module()
    if agent_lint is None:
        checks.append(Check(
            name="wrapper-sync",
            severity=Severity.WARNING,
            message="Impossible de charger agent-lint.py pour vérifier les wrappers workspace",
            fix_hint="Vérifier la présence de grimoire-kit/framework/tools/agent-lint.py",
        ))
        return checks

    runtime_root = _resolve_agent_runtime_root(project_root, agent_lint)
    runtime_dir = runtime_root / "_grimoire-runtime"
    if not runtime_dir.is_dir():
        return checks

    try:
        manifest = agent_lint.load_manifest(runtime_root)
        findings = agent_lint.lint_wrapper_sync(runtime_root, manifest)
    except Exception as exc:  # pragma: no cover - defensive, should not happen in nominal flow
        checks.append(Check(
            name="wrapper-sync",
            severity=Severity.WARNING,
            message=f"Vérification des wrappers workspace échouée : {exc}",
            fix_hint="Relancer agent-lint.py --project-root . --write-wrappers pour régénérer les wrappers gérés.",
        ))
        return checks

    for finding in findings:
        checks.append(Check(
            name=finding.rule,
            severity=Severity.WARNING,
            message=finding.message,
            fix_hint=finding.fix_hint,
        ))

    wrapper_spec_path = runtime_root / WRAPPER_SPEC_RELATIVE_PATH
    if wrapper_spec_path.is_file() and not findings:
        checks.append(Check(
            name="wrapper-sync",
            severity=Severity.INFO,
            message="Wrappers workspace présents et synchronisés avec agent-wrapper-spec.json",
        ))

    return checks


def check_hook_safety_state(project_root: Path) -> list[Check]:
    """Vérifie l'état réel de la safety gate des hooks."""
    checks: list[Check] = []

    hook_safety_gate = _load_hook_safety_gate_module()
    if hook_safety_gate is None:
        checks.append(Check(
            name="hook-safety-loader",
            severity=Severity.WARNING,
            message="Impossible de charger hook-safety-gate.py pour vérifier les hooks.",
            fix_hint="Vérifier la présence de grimoire-kit/framework/tools/hook-safety-gate.py",
        ))
        return checks

    try:
        registry = hook_safety_gate.load_registry(hook_safety_gate.registry_path(project_root))
        statuses = hook_safety_gate.collect_statuses(project_root, registry)
        issues = hook_safety_gate.audit_manifest_bindings(project_root, registry)
    except Exception as exc:  # pragma: no cover - defensive
        checks.append(Check(
            name="hook-safety-state",
            severity=Severity.WARNING,
            message=f"Vérification hook safety échouée : {exc}",
            fix_hint="Relancer grimoire-kit/framework/tools/hook-safety-gate.py status --strict pour diagnostiquer.",
        ))
        return checks

    for issue in issues:
        checks.append(Check(
            name="hook-safety-manifest",
            severity=Severity.BLOCKER,
            message=(
                f"Manifest hook incohérent : {issue.manifest_path} ({issue.event}[{issue.entry_index}]) — "
                f"{issue.reason}"
            ),
            fix_hint="Synchroniser le registre hooks et les manifests avant d'exécuter une tâche critique.",
        ))

    degraded_states = {"pending", "modified", "shadow", "canary", "disabled"}
    invalid_statuses = [status for status in statuses if status.state == "invalid"]
    degraded_statuses = [status for status in statuses if status.state in degraded_states]

    for status in invalid_statuses:
        checks.append(Check(
            name="hook-safety-invalid",
            severity=Severity.BLOCKER,
            message=f"Hook {status.hook_id} invalide : {status.reason}",
            fix_hint="Corriger les chemins hook manquants ou le registre avant exécution.",
        ))

    for status in degraded_statuses:
        checks.append(Check(
            name="hook-safety-degraded",
            severity=Severity.WARNING,
            message=(
                f"Hook {status.hook_id} en état {status.state} "
                f"(mode configuré: {status.configured_mode}) — {status.reason}"
            ),
            fix_hint="Promouvoir ou resynchroniser les hooks avant une exécution à risque élevé.",
        ))

    if not issues and not invalid_statuses and not degraded_statuses:
        enforced_count = sum(1 for status in statuses if status.state == "enforced")
        checks.append(Check(
            name="hook-safety-state",
            severity=Severity.INFO,
            message=f"Hook safety OK : {enforced_count}/{len(statuses)} hooks enforced, aucun drift manifest.",
        ))

    return checks


def check_guardrail_rules_state(project_root: Path) -> list[Check]:
    """Expose clairement si les règles guardrail tournent en fallback silencieux."""
    checks: list[Check] = []

    guardrail_rules = _load_guardrail_rules_module()
    if guardrail_rules is None:
        checks.append(Check(
            name="guardrail-rules-loader",
            severity=Severity.WARNING,
            message="Impossible de charger guardrail-policy-rules.py pour vérifier les règles guardrail.",
            fix_hint="Vérifier la présence de grimoire-kit/framework/tools/guardrail-policy-rules.py",
        ))
        return checks

    status_getter = getattr(guardrail_rules, "guardrail_rules_status", None)
    if not callable(status_getter):
        checks.append(Check(
            name="guardrail-rules-status",
            severity=Severity.WARNING,
            message="guardrail-policy-rules.py ne publie pas l'état de chargement des règles.",
            fix_hint="Mettre à jour guardrail-policy-rules.py pour exposer le statut de fallback.",
        ))
        return checks

    try:
        status = status_getter(project_root)
    except Exception as exc:  # pragma: no cover - defensive
        checks.append(Check(
            name="guardrail-rules-status",
            severity=Severity.WARNING,
            message=f"Impossible d'inspecter les règles guardrail : {exc}",
            fix_hint="Vérifier guardrail-policy-rules.yaml et la disponibilité de PyYAML.",
        ))
        return checks

    source = str(status.get("source") or "unknown")
    rules_file = str(status.get("rulesFile") or "guardrail-policy-rules.yaml")
    warning = str(status.get("warning") or "").strip()

    if source == "merged":
        checks.append(Check(
            name="guardrail-rules-status",
            severity=Severity.INFO,
            message=f"Règles guardrail chargées depuis {rules_file}.",
        ))
        return checks

    checks.append(Check(
        name="guardrail-rules-fallback",
        severity=Severity.WARNING,
        message=warning or f"Règles guardrail en fallback par défaut ({rules_file}).",
        fix_hint="Vérifier PyYAML, le YAML des règles et sa validité avant exécution.",
    ))
    return checks


def check_memory_state(project_root: Path) -> list[Check]:
    """Vérifie l'état de la mémoire."""
    checks = []
    memory_dir = project_root / "_grimoire" / "_memory"

    if not memory_dir.exists():
        return checks

    # Contradictions non résolues
    contradiction_log = memory_dir / "contradiction-log.md"
    if contradiction_log.exists():
        try:
            content = contradiction_log.read_text(encoding="utf-8")
            unresolved = content.count("- [ ]") or content.count("⚠️")
            if unresolved > 0:
                checks.append(Check(
                    name="contradictions",
                    severity=Severity.WARNING,
                    message=f"{unresolved} contradiction(s) non résolue(s) dans contradiction-log.md",
                    fix_hint="Activer Mnemo pour résoudre les contradictions",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    # Session state périmé
    session_state = memory_dir / "session-state.md"
    if session_state.exists():
        try:
            mtime = datetime.fromtimestamp(session_state.stat().st_mtime)
            age_hours = (datetime.now() - mtime).total_seconds() / 3600
            if age_hours > 168:  # > 1 semaine
                checks.append(Check(
                    name="stale-session",
                    severity=Severity.INFO,
                    message=f"session-state.md date de {age_hours:.0f}h — potentiellement obsolète",
                    fix_hint="Re-briefer l'agent via [BR] pour rafraîchir le contexte",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    # Requêtes inter-agents en attente
    shared_context = memory_dir / "shared-context.md"
    if shared_context.exists():
        try:
            content = shared_context.read_text(encoding="utf-8")
            pending = len(re.findall(r"- \[ \].*\[.*→.*\]", content))
            if pending > 0:
                checks.append(Check(
                    name="pending-requests",
                    severity=Severity.WARNING,
                    message=f"{pending} requête(s) inter-agents en attente dans shared-context.md",
                    fix_hint="Résoudre les requêtes avant de commencer une nouvelle tâche",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return checks


def check_story_readiness(project_root: Path, story_path: str) -> list[Check]:
    """Vérifie qu'une story est prête à être exécutée."""
    checks = []

    story_file = project_root / story_path
    if not story_file.exists():
        checks.append(Check(
            name="story-missing",
            severity=Severity.BLOCKER,
            message=f"Story introuvable : {story_path}",
        ))
        return checks

    try:
        content = story_file.read_text(encoding="utf-8")

        # Placeholders non remplis
        placeholders = re.findall(r'\{\{[^}]+\}\}', content)
        if placeholders:
            checks.append(Check(
                name="story-placeholders",
                severity=Severity.BLOCKER,
                message=f"{len(placeholders)} placeholder(s) non rempli(s) : {', '.join(placeholders[:5])}",
                fix_hint="Remplir les placeholders avant de commencer",
            ))

        # Acceptance criteria vides
        if "acceptance" in content.lower() and "- [ ]" not in content:
            checks.append(Check(
                name="no-acceptance-criteria",
                severity=Severity.WARNING,
                message="Pas de critères d'acceptation checkable (- [ ]) trouvés",
                fix_hint="Ajouter des critères d'acceptation cochables",
            ))

    except OSError:
        checks.append(Check(
            name="story-read-error",
            severity=Severity.BLOCKER,
            message=f"Impossible de lire la story : {story_path}",
        ))

    return checks


def _parse_major_version(raw_version: str) -> int | None:
    """Extrait la version majeure depuis une sortie de type v20.11.0."""
    match = re.search(r"(\d+)", raw_version)
    if not match:
        return None
    return int(match.group(1))


def check_vscode_getting_started_readiness(project_root: Path) -> list[Check]:
    """Checks d'environnement inspirés de VS Code Getting Started."""
    checks: list[Check] = []

    project_path = str(project_root)
    if " " in project_path:
        checks.append(Check(
            name="workspace-path-space",
            severity=Severity.WARNING,
            message=(
                "Le chemin du workspace contient des espaces — risque de comportements "
                "instables sur les builds natifs Node"
            ),
            fix_hint="Déplacer le projet vers un chemin sans espace ou utiliser un lien symbolique",
        ))

    has_node = bool(shutil.which("node"))
    has_npm = bool(shutil.which("npm"))

    if not has_node:
        checks.append(Check(
            name="node-missing",
            severity=Severity.WARNING,
            message="Node.js introuvable dans le PATH",
            fix_hint="Installer Node.js LTS (>= 20) pour les workflows VS Code côté JavaScript",
        ))
    else:
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            node_major = _parse_major_version(result.stdout.strip())
            if node_major is not None and node_major < 20:
                checks.append(Check(
                    name="node-version",
                    severity=Severity.WARNING,
                    message=f"Node.js {result.stdout.strip()} détecté — recommandé: >= v20",
                    fix_hint="Mettre à jour Node.js vers la version LTS actuelle",
                ))
            else:
                checks.append(Check(
                    name="node-version",
                    severity=Severity.INFO,
                    message=f"Node.js {result.stdout.strip()} détecté",
                ))

            # VS Code supporte uniquement x64/ARM64 pour le build natif.
            arch_result = subprocess.run(
                ["node", "-p", "process.arch"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            node_arch = arch_result.stdout.strip()
            if node_arch and node_arch not in {"x64", "arm64"}:
                checks.append(Check(
                    name="node-unsupported-arch",
                    severity=Severity.WARNING,
                    message=(
                        f"Architecture Node.js non recommandée pour VS Code: {node_arch} "
                        "(attendu: x64 ou arm64)"
                    ),
                    fix_hint="Utiliser une distribution Node.js x64/arm64 (cf .nvmrc)",
                ))
        except (subprocess.TimeoutExpired, OSError):
            checks.append(Check(
                name="node-version-check",
                severity=Severity.WARNING,
                message="Impossible de vérifier la version Node.js",
            ))

    if not has_npm:
        checks.append(Check(
            name="npm-missing",
            severity=Severity.WARNING,
            message="npm introuvable dans le PATH",
            fix_hint="Installer npm via Node.js officiel ou un gestionnaire de version (nvm/asdf)",
        ))

    if not shutil.which("code"):
        checks.append(Check(
            name="code-cli-missing",
            severity=Severity.INFO,
            message="CLI `code` indisponible dans ce shell",
            fix_hint="Dans VS Code: Command Palette > 'Shell Command: Install code command in PATH'",
        ))

    if sys.platform.startswith("linux") and has_node:
        for build_tool in ["g++", "make"]:
            if not shutil.which(build_tool):
                checks.append(Check(
                    name="native-build-tool-missing",
                    severity=Severity.WARNING,
                    message=f"Outil de build natif manquant: {build_tool}",
                    fix_hint="Installer build-essential pour compiler les dépendances natives Node",
                ))

        # node-gyp utilise souvent la commande "python" (pas seulement python3).
        if not shutil.which("python"):
            checks.append(Check(
                name="python-command-missing",
                severity=Severity.WARNING,
                message="Commande `python` introuvable (node-gyp peut échouer)",
                fix_hint="Installer python-is-python3 (Debian/Ubuntu) ou créer un alias python -> python3",
            ))

        # Bibliothèques natives VS Code (Debian/Ubuntu)
        # Source: DeepWiki Getting Started — Platform-Specific Setup / Linux
        if shutil.which("pkg-config"):
            vscode_libs = {
                "x11": "libx11-dev",
                "xkbfile": "libxkbfile-dev",
                "libsecret-1": "libsecret-1-dev",
                "krb5": "libkrb5-dev",
            }
            missing_libs = []
            for pc_name, pkg_name in vscode_libs.items():
                try:
                    r = subprocess.run(
                        ["pkg-config", "--exists", pc_name],
                        capture_output=True,
                        timeout=5,
                        check=False,
                    )
                    if r.returncode != 0:
                        missing_libs.append(pkg_name)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            if missing_libs:
                checks.append(Check(
                    name="vscode-native-libs-missing",
                    severity=Severity.WARNING,
                    message=f"Bibliothèques natives VS Code manquantes : {', '.join(missing_libs)}",
                    fix_hint=(
                        f"sudo apt-get install {' '.join(missing_libs)}"
                    ),
                ))

        # ENOSPC / inotify — limite de file watchers (Linux)
        # Source: DeepWiki Getting Started — Troubleshooting Build Issues
        inotify_path = Path("/proc/sys/fs/inotify/max_user_watches")
        if inotify_path.exists():
            try:
                limit = int(inotify_path.read_text(encoding="utf-8").strip())
                if limit < 524288:
                    checks.append(Check(
                        name="inotify-limit-low",
                        severity=Severity.WARNING,
                        message=(
                            f"Limite inotify trop basse : max_user_watches={limit} "
                            f"(recommandé : >= 524288) — risque ENOSPC sur gros projets"
                        ),
                        fix_hint=(
                            "echo fs.inotify.max_user_watches=524288 | "
                            "sudo tee -a /etc/sysctl.conf && sudo sysctl -p"
                        ),
                    ))
            except (OSError, ValueError):
                pass

    # WSL1 — non supporté (DeepWiki Getting Started — Additional Resources)
    if sys.platform.startswith("linux"):
        proc_version = Path("/proc/version")
        if proc_version.exists():
            try:
                content = proc_version.read_text(encoding="utf-8").lower()
                if "microsoft" in content and "wsl2" not in content and "WSL_DISTRO_NAME" in os.environ:
                    checks.append(Check(
                        name="wsl1-not-supported",
                        severity=Severity.WARNING,
                        message="WSL1 détecté — non supporté pour les builds VS Code natifs",
                        fix_hint=(
                            "Migrer vers WSL2 : wsl --set-version <distro> 2 "
                            "ou utiliser le .devcontainer"
                        ),
                    ))
            except OSError:
                pass

    if (project_root / ".devcontainer").exists():
        checks.append(Check(
            name="devcontainer-available",
            severity=Severity.INFO,
            message=".devcontainer détecté — fallback reproductible disponible",
        ))
        if not shutil.which("docker"):
            checks.append(Check(
                name="devcontainer-no-docker",
                severity=Severity.WARNING,
                message=(
                    ".devcontainer présent mais Docker CLI introuvable — "
                    "Remote-Containers local indisponible"
                ),
                fix_hint="Installer Docker Desktop/Engine ou utiliser GitHub Codespaces",
            ))

    return checks


# ── Module Wuwei (#107) : Non-interruption ───────────────────────────────────

def check_wuwei(project_root: Path, agent: str) -> list[Check]:
    """Vérifie si l'agent est en mode flow (wuwei) — ne pas interrompre."""
    checks = []
    memory_dir = project_root / "_grimoire" / "_memory"
    session_state = memory_dir / "session-state.md"

    if session_state.exists():
        try:
            content = session_state.read_text(encoding="utf-8")
            # Chercher des tâches in-progress pour cet agent
            in_progress = re.findall(
                rf"\b{re.escape(agent)}\b.*(?:in-progress|en_cours|running)",
                content, re.IGNORECASE
            )
            if in_progress:
                checks.append(Check(
                    name="wuwei-flow",
                    severity=Severity.INFO,
                    message=f"Agent {agent} a {len(in_progress)} tâche(s) en cours — "
                            f"mode flow actif, minimiser les interruptions",
                ))
        except OSError as _exc:
            _log.debug("OSError suppressed: %s", _exc)
            # Silent exception — add logging when investigating issues

    return checks


# ── Report Generation ────────────────────────────────────────────────────────

def run_all_checks(
    project_root: Path,
    agent: str = "",
    story: str = "",
) -> PreflightReport:
    """Exécute toutes les vérifications."""
    report = PreflightReport(agent=agent, story=story)

    report.checks.extend(check_grimoire_structure(project_root))
    report.checks.extend(check_tools_available(project_root))
    report.checks.extend(check_vscode_getting_started_readiness(project_root))
    report.checks.extend(check_git_state(project_root))
    report.checks.extend(check_structure_governance(project_root))
    report.checks.extend(check_surface_index_health(project_root))
    report.checks.extend(check_wrapper_health(project_root))
    report.checks.extend(check_hook_safety_state(project_root))
    report.checks.extend(check_guardrail_rules_state(project_root))
    report.checks.extend(check_memory_state(project_root))

    if story:
        report.checks.extend(check_story_readiness(project_root, story))

    if agent:
        report.checks.extend(check_wuwei(project_root, agent))

    return report


def format_report(report: PreflightReport) -> str:
    """Formate le rapport pour affichage terminal."""
    lines = [
        "✈️  Pre-flight Check — Grimoire",
        f"   {report.go_nogo}",
    ]
    if report.agent:
        lines.append(f"   Agent : {report.agent}")
    if report.story:
        lines.append(f"   Story : {report.story}")
    lines.append(f"   Checks : {len(report.checks)} "
                 f"({len(report.blockers)} blockers, "
                 f"{len(report.warnings)} warnings, "
                 f"{len(report.infos)} infos)")
    lines.append("")

    if report.blockers:
        lines.append("   🔴 BLOCKERS :")
        for c in report.blockers:
            lines.append(f"      {c.message}")
            if c.fix_hint:
                lines.append(f"         💡 {c.fix_hint}")
        lines.append("")

    if report.warnings:
        lines.append("   🟡 WARNINGS :")
        for c in report.warnings:
            lines.append(f"      {c.message}")
            if c.fix_hint:
                lines.append(f"         💡 {c.fix_hint}")
        lines.append("")

    if report.infos:
        lines.append("   🟢 INFO :")
        for c in report.infos:
            lines.append(f"      {c.message}")
        lines.append("")

    return "\n".join(lines)


# ── Stigmergy Integration ────────────────────────────────────────────────────

def _load_stigmergy():
    """Charge stigmergy.py avec cache sys.modules."""
    import importlib.util as _ilu
    _key = "_grimoire_stigmergy"
    if _key in sys.modules:
        return sys.modules[_key]
    sg_path = Path(__file__).parent / "stigmergy.py"
    if not sg_path.exists():
        return None
    spec = _ilu.spec_from_file_location(_key, sg_path)
    if not spec or not spec.loader:
        return None
    sg = _ilu.module_from_spec(spec)
    sys.modules[_key] = sg
    spec.loader.exec_module(sg)
    return sg


def emit_blockers_to_stigmergy(project_root: Path, report: PreflightReport) -> int:
    """Émet les blockers preflight comme phéromones ALERT."""
    if not report.blockers:
        return 0
    try:
        sg = _load_stigmergy()
        if sg is None or not hasattr(sg, "deposit_pheromone"):
            return 0
        emitted = 0
        for check in report.blockers:
            sg.deposit_pheromone(
                project_root,
                ptype="ALERT",
                location=f"preflight/{check.name}",
                text=check.message[:200],
                emitter="preflight-check",
                tags=["blocker", check.name],
            )
            emitted += 1
        return emitted
    except Exception:
        return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grimoire Pre-flight Check — Vérification pré-exécution",
    )
    parser.add_argument("--project-root", type=str, default=".")
    parser.add_argument("--agent", type=str, help="Agent cible")
    parser.add_argument("--story", type=str, help="Chemin vers la story à vérifier")
    parser.add_argument("--fix", action="store_true", help="Tenter l'auto-correction")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--quiet", action="store_true", help="N'afficher que les blockers")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    report = run_all_checks(project_root, agent=args.agent or "", story=args.story or "")
    emit_blockers_to_stigmergy(project_root, report)

    if args.json:
        result = {
            "go_nogo": report.go_nogo,
            "agent": report.agent,
            "story": report.story,
            "timestamp": report.timestamp,
            "blockers": [{"name": c.name, "message": c.message, "fix": c.fix_hint} for c in report.blockers],
            "warnings": [{"name": c.name, "message": c.message, "fix": c.fix_hint} for c in report.warnings],
            "infos": [{"name": c.name, "message": c.message} for c in report.infos],
        }
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.quiet:
        if report.blockers:
            for c in report.blockers:
                print(f"🔴 {c.message}")
            return 1
    else:
        print(format_report(report))

    return 1 if report.blockers else 0


if __name__ == "__main__":
    sys.exit(main())
