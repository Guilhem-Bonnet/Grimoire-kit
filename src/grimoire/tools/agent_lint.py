#!/usr/bin/env python3
"""
agent-lint.py — Linter structurel pour les agents Grimoire.
=======================================================

Vérifie l'intégrité structurelle de chaque fichier agent .md :
  - Commandes menu (cmd) uniques par agent (pas de doublon)
  - Blocs persona obligatoires présents (<voice>, <decision_framework>, etc.)
  - Synchronisation agent ↔ manifest CSV (nom, module, displayName)
  - Workflows/fichiers référencés existent sur disque
  - Handlers déclarés correspondent aux attributs utilisés dans le menu

Usage :
  python3 agent-lint.py --project-root .                    # Lint tous les agents
  python3 agent-lint.py --project-root . --agent analyst    # Lint un agent
  python3 agent-lint.py --project-root . --json             # Sortie JSON
  python3 agent-lint.py --project-root . --fix              # Suggestions auto-fix

Stdlib only — aucune dépendance externe.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("grimoire.agent_lint")

AGENT_LINT_VERSION = "1.5.0"

SURFACE_INDEX_CONFIG_PATH = "_config/agent-surface-index.csv"
SURFACE_INDEX_RELATIVE_PATH = f"_grimoire-runtime/{SURFACE_INDEX_CONFIG_PATH}"
WRAPPER_SPEC_CONFIG_PATH = "_config/agent-wrapper-spec.json"
WRAPPER_SPEC_RELATIVE_PATH = f"_grimoire-runtime/{WRAPPER_SPEC_CONFIG_PATH}"
FILES_MANIFEST_CONFIG_PATH = "_config/files-manifest.csv"
FILES_MANIFEST_RELATIVE_PATH = f"_grimoire-runtime/{FILES_MANIFEST_CONFIG_PATH}"
SURFACE_INDEX_FIELDS = [
    "name",
    "module",
    "runtimePath",
    "workspaceActivePath",
    "workspaceArchivedPath",
    "status",
    "lookupPriority",
    "routingClass",
    "catalogKind",
    "notes",
]

FILES_MANIFEST_FIELDS = ["type", "name", "module", "path", "hash"]

MASTER_FORBIDDEN_TOOLS = frozenset({
    "edit/createJupyterNotebook",
    "edit/editNotebook",
    "execute/runNotebookCell",
    "read/getNotebookSummary",
    "github/add_comment_to_pending_review",
    "github/add_issue_comment",
    "github/add_reply_to_pull_request_comment",
    "github/assign_copilot_to_issue",
    "github/create_or_update_file",
    "github/create_branch",
    "github/create_pull_request",
    "github/create_pull_request_with_copilot",
    "github/create_repository",
    "github/delete_file",
    "github/fork_repository",
    "github/issue_write",
    "github/merge_pull_request",
    "github/pull_request_review_write",
    "github/push_files",
    "github/request_copilot_review",
    "github/sub_issue_write",
    "github/update_pull_request",
    "github/update_pull_request_branch",
    "gitkraken/git_add_or_commit",
    "gitkraken/git_branch",
    "gitkraken/git_checkout",
    "gitkraken/git_push",
    "gitkraken/git_stash",
    "gitkraken/git_worktree",
    "gitkraken/gitlens_commit_composer",
    "gitkraken/gitlens_start_review",
    "gitkraken/gitlens_start_work",
    "gitkraken/issues_add_comment",
    "gitkraken/pull_request_create",
    "gitkraken/pull_request_create_review",
    "ms-python.python/installPythonPackage",
})

MASTER_FORBIDDEN_TOOL_PREFIXES = (
    "ms-toolsai.jupyter/",
    "vscjava.vscode-java-debug/",
)

# ── Sévérités ────────────────────────────────────────────────────────────────

class Severity:
    ERROR = "🔴 ERROR"
    WARNING = "🟡 WARNING"
    INFO = "🟢 INFO"


# ── Data Classes ─────────────────────────────────────────────────────────────

@dataclass
class Finding:
    """Un problème détecté."""
    agent: str
    severity: str
    rule: str
    message: str
    fix_hint: str = ""

    @property
    def is_error(self) -> bool:
        return "ERROR" in self.severity


@dataclass
class LintReport:
    """Rapport complet de lint."""
    findings: list[Finding] = field(default_factory=list)
    agents_checked: int = 0
    agents_clean: int = 0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.is_error]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if "WARNING" in f.severity]


@dataclass(frozen=True)
class AgentSurfaceRecord:
    """Index nominal des surfaces actives et archivées d'un agent."""

    name: str
    module: str
    runtime_path: str
    workspace_active_path: str
    workspace_archived_path: str
    status: str
    lookup_priority: int
    routing_class: str
    catalog_kind: str = ""
    notes: str = ""


# ── Discovery ────────────────────────────────────────────────────────────────

def discover_agents(project_root: Path) -> list[tuple[str, Path]]:
    """Découvre tous les fichiers agent .md dans _grimoire-runtime/*/agents/."""
    runtime_dir = project_root / "_grimoire-runtime"
    agents = []
    if not runtime_dir.is_dir():
        return agents

    for module_dir in sorted(runtime_dir.iterdir()):
        if not module_dir.is_dir() or module_dir.name.startswith("_"):
            continue
        agents_dir = module_dir / "agents"
        if not agents_dir.is_dir():
            continue
        # Direct .md files
        for md_file in sorted(agents_dir.glob("*.md")):
            agents.append((module_dir.name, md_file))
        # Subdirectory agents (e.g., tech-writer/tech-writer.md, storyteller/storyteller.md)
        for sub_dir in sorted(agents_dir.iterdir()):
            if sub_dir.is_dir():
                for md_file in sorted(sub_dir.glob("*.md")):
                    if md_file.stem == sub_dir.name:  # match dir name
                        agents.append((module_dir.name, md_file))
    return agents


def _is_kit_package_root(project_root: Path) -> bool:
    """Retourne True si la racine ressemble au package grimoire-kit."""
    return (
        (project_root / "pyproject.toml").is_file()
        and (project_root / "framework" / "tools").is_dir()
        and (project_root / "src" / "grimoire").is_dir()
    )


def resolve_agent_project_root(project_root: Path) -> Path:
    """Résout la racine runtime effective quand l'outil tourne depuis grimoire-kit/."""
    if (project_root / "_grimoire-runtime").is_dir():
        return project_root

    parent = project_root.parent
    if _is_kit_package_root(project_root) and (parent / "_grimoire-runtime").is_dir():
        return parent

    return project_root


def load_manifest(project_root: Path) -> dict[str, dict]:
    """Charge le agent-manifest.csv en dict indexé par name."""
    manifest_path = project_root / "_grimoire-runtime" / "_config" / "agent-manifest.csv"
    if not manifest_path.is_file():
        return {}
    result = {}
    with manifest_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("name", "").strip().strip('"')
            result[name] = {k: v.strip().strip('"') for k, v in row.items()}
    return result


def _agent_slug_from_wrapper(path: Path) -> str:
    """Retourne le slug d'un wrapper .agent.md."""
    if path.name.endswith(".agent.md"):
        return path.name[:-len(".agent.md")]
    return path.stem


def discover_workspace_agents(project_root: Path, *, archived: bool = False) -> dict[str, str]:
    """Découvre les wrappers workspace actifs ou archivés."""
    base_dir = project_root / ".github" / "agents"
    if archived:
        base_dir = base_dir / "_archived"

    if not base_dir.is_dir():
        return {}

    discovered: dict[str, str] = {}
    for wrapper in sorted(base_dir.glob("*.agent.md")):
        discovered[_agent_slug_from_wrapper(wrapper)] = wrapper.relative_to(project_root).as_posix()
    return discovered


def _classify_surface_record(
    name: str,
    module: str,
    runtime_path: str,
    workspace_active_path: str,
    workspace_archived_path: str,
    catalog_kind: str = "",
) -> AgentSurfaceRecord:
    """Calcule le statut canonique d'un agent entre runtime, actif et archive."""
    if runtime_path and workspace_active_path:
        if workspace_archived_path:
            return AgentSurfaceRecord(
                name=name,
                module=module,
                runtime_path=runtime_path,
                workspace_active_path=workspace_active_path,
                workspace_archived_path=workspace_archived_path,
                status="active+archived",
                lookup_priority=1,
                routing_class="nominal",
                catalog_kind=catalog_kind,
                notes="Le wrapper actif est nominal; la copie archivee reste hors lookup par defaut.",
            )
        return AgentSurfaceRecord(
            name=name,
            module=module,
            runtime_path=runtime_path,
            workspace_active_path=workspace_active_path,
            workspace_archived_path=workspace_archived_path,
            status="active",
            lookup_priority=1,
            routing_class="nominal",
            catalog_kind=catalog_kind,
            notes="Runtime et wrapper actif alignes.",
        )

    if runtime_path and workspace_archived_path:
        return AgentSurfaceRecord(
            name=name,
            module=module,
            runtime_path=runtime_path,
            workspace_active_path=workspace_active_path,
            workspace_archived_path=workspace_archived_path,
            status="runtime+archived",
            lookup_priority=3,
            routing_class="degraded",
            catalog_kind=catalog_kind,
            notes="Le runtime existe, mais seul un wrapper archive est present dans le workspace.",
        )

    if runtime_path:
        return AgentSurfaceRecord(
            name=name,
            module=module,
            runtime_path=runtime_path,
            workspace_active_path=workspace_active_path,
            workspace_archived_path=workspace_archived_path,
            status="runtime-only",
            lookup_priority=2,
            routing_class="runtime",
            catalog_kind=catalog_kind,
            notes="Le runtime est canonique; aucun wrapper actif workspace n'est detecte.",
        )

    if workspace_active_path:
        notes = "Wrapper workspace sans entree runtime."
        if "master" in name:
            notes = "Wrapper de compatibilite workspace; ne pas traiter comme source de verite runtime."
        return AgentSurfaceRecord(
            name=name,
            module=module,
            runtime_path=runtime_path,
            workspace_active_path=workspace_active_path,
            workspace_archived_path=workspace_archived_path,
            status="workspace-only",
            lookup_priority=4,
            routing_class="compatibility",
            catalog_kind=catalog_kind,
            notes=notes,
        )

    return AgentSurfaceRecord(
        name=name,
        module=module,
        runtime_path=runtime_path,
        workspace_active_path=workspace_active_path,
        workspace_archived_path=workspace_archived_path,
        status="archived-only",
        lookup_priority=9,
        routing_class="archaeology",
        catalog_kind=catalog_kind,
        notes="Surface archivee uniquement; hors lookup nominal.",
    )


def build_surface_index(
    project_root: Path,
    manifest: dict[str, dict] | None = None,
    wrapper_spec: dict[str, object] | None = None,
) -> list[AgentSurfaceRecord]:
    """Construit l'index croise runtime/actif/archive pour les agents."""
    manifest = manifest or load_manifest(project_root)
    wrapper_spec = wrapper_spec or {}
    catalog_kinds = _catalog_kind_map_from_spec(wrapper_spec)
    active_wrappers = discover_workspace_agents(project_root, archived=False)
    archived_wrappers = discover_workspace_agents(project_root, archived=True)

    names = sorted(set(manifest) | set(active_wrappers) | set(archived_wrappers))
    records: list[AgentSurfaceRecord] = []
    for name in names:
        manifest_row = manifest.get(name, {})
        records.append(_classify_surface_record(
            name=name,
            module=manifest_row.get("module", ""),
            runtime_path=manifest_row.get("path", ""),
            workspace_active_path=active_wrappers.get(name, ""),
            workspace_archived_path=archived_wrappers.get(name, ""),
            catalog_kind=catalog_kinds.get(name, ""),
        ))

    return sorted(records, key=lambda record: (record.lookup_priority, record.name))


def _surface_record_to_row(record: AgentSurfaceRecord) -> dict[str, str]:
    """Transforme un AgentSurfaceRecord en ligne CSV sérialisable."""
    return {
        "name": record.name,
        "module": record.module,
        "runtimePath": record.runtime_path,
        "workspaceActivePath": record.workspace_active_path,
        "workspaceArchivedPath": record.workspace_archived_path,
        "status": record.status,
        "lookupPriority": str(record.lookup_priority),
        "routingClass": record.routing_class,
        "catalogKind": record.catalog_kind,
        "notes": record.notes,
    }


def read_surface_index(project_root: Path) -> list[dict[str, str]]:
    """Charge l'index de surface existant si present."""
    index_path = project_root / SURFACE_INDEX_RELATIVE_PATH
    if not index_path.is_file():
        return []

    with index_path.open(encoding="utf-8", newline="") as handle:
        return [
            {key: (value or "") for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]


def write_surface_index(project_root: Path, records: list[AgentSurfaceRecord]) -> Path:
    """Ecrit l'index de surface agentique dans _config/."""
    index_path = project_root / SURFACE_INDEX_RELATIVE_PATH
    index_path.parent.mkdir(parents=True, exist_ok=True)

    with index_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(SURFACE_INDEX_FIELDS) + "\n")
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
        for record in records:
            row = _surface_record_to_row(record)
            writer.writerow([row[field] for field in SURFACE_INDEX_FIELDS])

    return index_path


def _normalize_surface_rows(rows: list[dict[str, str]]) -> list[tuple[str, ...]]:
    """Normalise l'index pour comparer deux versions indépendamment de l'ordre."""
    normalized = []
    for row in rows:
        normalized.append(tuple((row.get(field, "") or "") for field in SURFACE_INDEX_FIELDS))
    return sorted(normalized)


def _compute_sha256(path: Path) -> str:
    """Calcule le hash SHA-256 d'un fichier."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def update_files_manifest(project_root: Path, surface_index_path: Path | None = None) -> Path:
    """Met à jour files-manifest.csv avec les artefacts agentiques dérivés."""
    files_manifest_path = project_root / FILES_MANIFEST_RELATIVE_PATH
    files_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    surface_index_path = surface_index_path or (project_root / SURFACE_INDEX_RELATIVE_PATH)
    wrapper_spec_path = project_root / WRAPPER_SPEC_RELATIVE_PATH

    rows: list[dict[str, str]] = []
    if files_manifest_path.is_file():
        with files_manifest_path.open(encoding="utf-8", newline="") as handle:
            rows = [
                {key: (value or "") for key, value in row.items()}
                for row in csv.DictReader(handle)
            ]

    derived_entries: list[dict[str, str]] = []
    if surface_index_path.is_file():
        derived_entries.append({
            "type": "csv",
            "name": "agent-surface-index",
            "module": "_config",
            "path": SURFACE_INDEX_CONFIG_PATH,
            "hash": _compute_sha256(surface_index_path),
        })
    if wrapper_spec_path.is_file():
        derived_entries.append({
            "type": "json",
            "name": "agent-wrapper-spec",
            "module": "_config",
            "path": WRAPPER_SPEC_CONFIG_PATH,
            "hash": _compute_sha256(wrapper_spec_path),
        })

    for entry in derived_entries:
        replaced = False
        for index, row in enumerate(rows):
            if row.get("path") == entry["path"]:
                rows[index] = entry
                replaced = True
                break

        if replaced:
            continue

        insert_after = {
            SURFACE_INDEX_CONFIG_PATH: "_config/agent-manifest.csv",
            WRAPPER_SPEC_CONFIG_PATH: SURFACE_INDEX_CONFIG_PATH,
        }.get(entry["path"], "_config/agent-manifest.csv")

        insert_at = None
        for index, row in enumerate(rows):
            if row.get("path") == insert_after:
                insert_at = index + 1
                break
        if insert_at is None:
            rows.append(entry)
        else:
            rows.insert(insert_at, entry)

    with files_manifest_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(",".join(FILES_MANIFEST_FIELDS) + "\n")
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL, lineterminator="\n")
        for row in rows:
            writer.writerow([row.get(field, "") for field in FILES_MANIFEST_FIELDS])

    return files_manifest_path


def lint_surface_index_sync(
    project_root: Path,
    records: list[AgentSurfaceRecord],
    target_agent: str | None = None,
) -> list[Finding]:
    """Vérifie la présence et la fraîcheur de l'index de surface agentique."""
    findings: list[Finding] = []
    existing_rows = read_surface_index(project_root)
    expected_rows = [_surface_record_to_row(record) for record in records]

    if target_agent:
        records = [record for record in records if record.name == target_agent]
        expected_rows = [_surface_record_to_row(record) for record in records]
        existing_rows = [row for row in existing_rows if row.get("name") == target_agent]

    if not existing_rows:
        findings.append(Finding(
            agent="surface-index",
            severity=Severity.WARNING,
            rule="surface-index-sync",
            message=f"{SURFACE_INDEX_RELATIVE_PATH} manquant ou vide",
            fix_hint="Exécuter agent-lint.py --project-root . --write-surface-index pour générer l'index.",
        ))
        return findings

    if _normalize_surface_rows(existing_rows) != _normalize_surface_rows(expected_rows):
        findings.append(Finding(
            agent="surface-index",
            severity=Severity.WARNING,
            rule="surface-index-sync",
            message=f"{SURFACE_INDEX_RELATIVE_PATH} n'est pas synchronisé avec le runtime et les wrappers workspace",
            fix_hint="Régénérer l'index avec agent-lint.py --project-root . --write-surface-index.",
        ))

    files_manifest_path = project_root / FILES_MANIFEST_RELATIVE_PATH
    if not files_manifest_path.is_file():
        findings.append(Finding(
            agent="surface-index",
            severity=Severity.WARNING,
            rule="surface-index-files-manifest",
            message=f"{FILES_MANIFEST_RELATIVE_PATH} manquant — hash de l'index non traçable",
            fix_hint="Mettre à jour files-manifest.csv après génération de l'index.",
        ))
        return findings

    with files_manifest_path.open(encoding="utf-8", newline="") as handle:
        rows = [
            {key: (value or "") for key, value in row.items()}
            for row in csv.DictReader(handle)
        ]

    index_path = project_root / SURFACE_INDEX_RELATIVE_PATH
    manifest_row = next((row for row in rows if row.get("path") == SURFACE_INDEX_CONFIG_PATH), None)
    if not manifest_row:
        findings.append(Finding(
            agent="surface-index",
            severity=Severity.WARNING,
            rule="surface-index-files-manifest",
            message="agent-surface-index.csv absent de files-manifest.csv",
            fix_hint="Ajouter l'entrée agent-surface-index dans files-manifest.csv.",
        ))
    elif index_path.is_file() and manifest_row.get("hash") != _compute_sha256(index_path):
        findings.append(Finding(
            agent="surface-index",
            severity=Severity.WARNING,
            rule="surface-index-files-manifest",
            message="Hash de agent-surface-index.csv obsolète dans files-manifest.csv",
            fix_hint="Régénérer l'index et mettre à jour files-manifest.csv.",
        ))

    return findings


# ── Workspace Wrapper Spec ──────────────────────────────────────────────────

def load_wrapper_spec(project_root: Path) -> dict[str, object]:
    """Charge la source canonique des wrappers workspace."""
    spec_path = project_root / WRAPPER_SPEC_RELATIVE_PATH
    if not spec_path.is_file():
        return {}

    with spec_path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _catalog_kind_map_from_spec(wrapper_spec: dict[str, object]) -> dict[str, str]:
    """Retourne la classification canonique du catalogue d'agents."""
    raw_mapping = wrapper_spec.get("catalogKinds", {})
    if not isinstance(raw_mapping, dict):
        return {}

    mapping: dict[str, str] = {}
    for name, kind in raw_mapping.items():
        if not isinstance(name, str) or not isinstance(kind, str):
            continue
        mapping[name] = kind
    return mapping


def _render_yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_yaml_list(values: list[str]) -> str:
    return "[" + ", ".join(_render_yaml_scalar(value) for value in values) + "]"


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _manifest_wrapper_paths(name: str, manifest: dict[str, dict]) -> tuple[str, str]:
    manifest_entry = manifest.get(name)
    if not manifest_entry:
        raise KeyError(f"Wrapper '{name}' absent du agent-manifest.csv")

    module = manifest_entry.get("module", "")
    runtime_path = manifest_entry.get("path", "")
    if not module or not runtime_path:
        raise KeyError(f"Wrapper '{name}' incomplet dans agent-manifest.csv")

    return runtime_path, f"_grimoire-runtime/{module}/config.yaml"


def _replace_wrapper_tokens(line: str, runtime_path: str, config_path: str) -> str:
    return (
        line.replace("{{RUNTIME_PATH}}", runtime_path)
        .replace("{{CONFIG_PATH}}", config_path)
    )


def _render_simple_wrapper_body(
    summary: str,
    runtime_path: str,
    config_path: str,
    extra_steps: list[str],
) -> list[str]:
    body_lines = [
        summary,
        "",
        f"1. Load {{project-root}}/{config_path} and store ALL fields as session variables",
        f"2. Load the full agent file from {{project-root}}/{runtime_path}",
        "3. Follow ALL activation instructions in the agent file",
    ]
    for index, step in enumerate(extra_steps, start=4):
        body_lines.append(f"{index}. {step}")
    return body_lines


def render_managed_wrapper(
    name: str,
    wrapper_spec: dict[str, object],
    manifest: dict[str, dict],
    root_spec: dict[str, object],
) -> str:
    """Rend le contenu canonique d'un wrapper workspace géré."""
    template = str(wrapper_spec.get("template", "simple"))
    runtime_path = ""
    config_path = ""
    if template != "alias":
        runtime_path, config_path = _manifest_wrapper_paths(name, manifest)

    description = str(wrapper_spec["description"])
    tools = _dedupe_preserve_order([str(tool) for tool in wrapper_spec.get("tools", [])])
    if wrapper_spec.get("toolsFrom") == "master":
        tools = _dedupe_preserve_order([
            str(tool) for tool in root_spec.get("master", {}).get("tools", [])
        ])
    handoffs = _dedupe_preserve_order([str(agent) for agent in wrapper_spec.get("handoffs", [])])
    agents = _dedupe_preserve_order([str(agent) for agent in wrapper_spec.get("agents", [])])
    if wrapper_spec.get("agentsFrom") == "master":
        agents = _dedupe_preserve_order([
            str(agent) for agent in root_spec.get("master", {}).get("expectedAgents", [])
        ])
    user_invocable = bool(wrapper_spec.get("userInvocable", False))
    catalog_kind = _catalog_kind_map_from_spec(root_spec).get(name, "")

    frontmatter_lines = [
        "---",
        f"name: {_render_yaml_scalar(name)}",
        f"description: {_render_yaml_scalar(description)}",
    ]
    if catalog_kind:
        frontmatter_lines.append(f"catalog-kind: {_render_yaml_scalar(catalog_kind)}")
    frontmatter_lines.extend([
        f"tools: {_render_yaml_list(tools)}",
    ])
    if handoffs:
        frontmatter_lines.append(f"handoffs: {_render_yaml_list(handoffs)}")
    if agents:
        frontmatter_lines.append(f"agents: {_render_yaml_list(agents)}")
    frontmatter_lines.append(f"user-invocable: {'true' if user_invocable else 'false'}")
    frontmatter_lines.append("---")
    frontmatter_lines.append("")

    if template == "custom":
        body_lines = [
            _replace_wrapper_tokens(str(line), runtime_path, config_path)
            for line in wrapper_spec.get("body", [])
        ]
    elif template == "alias":
        body_lines = [str(line) for line in wrapper_spec.get("body", [])]
    else:
        body_lines = _render_simple_wrapper_body(
            summary=str(wrapper_spec["summary"]),
            runtime_path=runtime_path,
            config_path=config_path,
            extra_steps=[str(step) for step in wrapper_spec.get("extraSteps", [])],
        )

    return "\n".join(frontmatter_lines + body_lines).rstrip() + "\n"


def render_master_wrapper(root_spec: dict[str, object]) -> str:
    """Rend le wrapper master workspace depuis la spec canonique."""
    master_spec = root_spec.get("master")
    if not isinstance(master_spec, dict):
        raise KeyError("Section 'master' absente de agent-wrapper-spec.json")

    name = str(master_spec["name"])
    description = str(master_spec["description"])
    tools = _dedupe_preserve_order([str(tool) for tool in master_spec.get("tools", [])])
    agents = _dedupe_preserve_order([
        str(agent) for agent in master_spec.get("expectedAgents", [])
    ])
    user_invocable = bool(master_spec.get("userInvocable", True))
    catalog_kind = _catalog_kind_map_from_spec(root_spec).get(name, "")
    frontmatter_lines = [
        "---",
        f"description: {_render_yaml_scalar(description)}",
        f"name: {_render_yaml_scalar(name)}",
    ]
    if catalog_kind:
        frontmatter_lines.append(f"catalog-kind: {_render_yaml_scalar(catalog_kind)}")
    frontmatter_lines.extend(
        str(line) for line in master_spec.get("frontmatterLines", [])
    )
    frontmatter_lines.append(f"tools: {_render_yaml_list(tools)}")
    frontmatter_lines.append(f"agents: {_render_yaml_list(agents)}")
    frontmatter_lines.append(f"user-invocable: {'true' if user_invocable else 'false'}")
    frontmatter_lines.append("---")
    frontmatter_lines.append("")

    body_lines = [str(line) for line in master_spec.get("body", [])]
    return "\n".join(frontmatter_lines + body_lines).rstrip() + "\n"


def lint_master_tool_policy(root_spec: dict[str, object]) -> list[Finding]:
    """Vérifie que le master n'embarque pas d'outils hors périmètre nominal."""
    master_spec = root_spec.get("master")
    if not isinstance(master_spec, dict):
        return []

    master_name = str(master_spec.get("name", "grimoire-master"))
    tools = _dedupe_preserve_order([str(tool) for tool in master_spec.get("tools", [])])
    forbidden_tools = [
        tool for tool in tools
        if tool in MASTER_FORBIDDEN_TOOLS
        or any(tool.startswith(prefix) for prefix in MASTER_FORBIDDEN_TOOL_PREFIXES)
    ]
    if not forbidden_tools:
        return []

    preview = ", ".join(forbidden_tools[:6])
    if len(forbidden_tools) > 6:
        preview += f" (+{len(forbidden_tools) - 6})"

    return [Finding(
        agent=master_name,
        severity=Severity.WARNING,
        rule="wrapper-master-tool-policy",
        message=f"Surface master trop large — outils hors politique détectés : {preview}",
        fix_hint="Retirer du master les outils notebook, Java debug, mutation GitHub/GitKraken et installation Python; déléguer ces capacités hors surface nominale si elles redeviennent nécessaires.",
    )]


def _iter_managed_wrapper_specs(
    wrapper_spec: dict[str, object],
) -> list[tuple[str, dict[str, object]]]:
    entries: list[tuple[str, dict[str, object]]] = []
    for section_name in ("wrappers", "aliases"):
        section = wrapper_spec.get(section_name, {})
        if isinstance(section, dict):
            for name, spec in section.items():
                if isinstance(spec, dict):
                    entries.append((str(name), spec))
    return entries


def write_managed_wrappers(
    project_root: Path,
    manifest: dict[str, dict],
    *,
    target_agent: str | None = None,
) -> list[Path]:
    """Régénère les wrappers workspace gérés par la spec canonique."""
    wrapper_spec = load_wrapper_spec(project_root)
    if not wrapper_spec:
        return []

    wrappers_dir = project_root / ".github" / "agents"
    wrappers_dir.mkdir(parents=True, exist_ok=True)

    written_paths: list[Path] = []
    master_name = str(wrapper_spec.get("master", {}).get("name", "grimoire-master"))
    if not target_agent or target_agent == master_name:
        path = wrappers_dir / f"{master_name}.agent.md"
        rendered = render_master_wrapper(wrapper_spec)
        if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
            path.write_text(rendered, encoding="utf-8")
        written_paths.append(path)

    for name, spec in _iter_managed_wrapper_specs(wrapper_spec):
        if target_agent and name != target_agent:
            continue
        path = wrappers_dir / f"{name}.agent.md"
        rendered = render_managed_wrapper(name, spec, manifest, wrapper_spec)
        if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
            path.write_text(rendered, encoding="utf-8")
        written_paths.append(path)

    return written_paths


def lint_wrapper_sync(
    project_root: Path,
    manifest: dict[str, dict],
    target_agent: str | None = None,
) -> list[Finding]:
    """Vérifie la synchro des wrappers workspace gérés."""
    findings: list[Finding] = []
    wrapper_spec = load_wrapper_spec(project_root)
    if not wrapper_spec:
        return [Finding(
            agent="wrapper-spec",
            severity=Severity.WARNING,
            rule="wrapper-spec",
            message=f"{WRAPPER_SPEC_RELATIVE_PATH} manquant — aucune source canonique des wrappers workspace",
            fix_hint="Ajouter agent-wrapper-spec.json puis lancer agent-lint.py --project-root . --write-wrappers.",
        )]

    active_wrappers = discover_workspace_agents(project_root, archived=False)
    master_name = str(wrapper_spec.get("master", {}).get("name", "grimoire-master"))
    managed_specs = _iter_managed_wrapper_specs(wrapper_spec)
    expected_names = {name for name, _spec in managed_specs} | {master_name}

    for name, spec in managed_specs:
        if target_agent and name != target_agent:
            continue
        path = project_root / ".github" / "agents" / f"{name}.agent.md"
        if not path.is_file():
            findings.append(Finding(
                agent=name,
                severity=Severity.WARNING,
                rule="wrapper-sync",
                message=f"Wrapper workspace manquant : {path.relative_to(project_root).as_posix()}",
                fix_hint="Lancer agent-lint.py --project-root . --write-wrappers.",
            ))
            continue

        try:
            rendered = render_managed_wrapper(name, spec, manifest, wrapper_spec)
        except KeyError as exc:
            findings.append(Finding(
                agent=name,
                severity=Severity.ERROR,
                rule="wrapper-spec",
                message=str(exc),
                fix_hint="Aligner agent-wrapper-spec.json avec agent-manifest.csv.",
            ))
            continue

        if path.read_text(encoding="utf-8") != rendered:
            findings.append(Finding(
                agent=name,
                severity=Severity.WARNING,
                rule="wrapper-sync",
                message=f"Wrapper workspace non synchronisé : {path.relative_to(project_root).as_posix()}",
                fix_hint="Lancer agent-lint.py --project-root . --write-wrappers.",
            ))

    if not target_agent or target_agent == master_name:
        master_path = project_root / ".github" / "agents" / f"{master_name}.agent.md"
        if not master_path.is_file():
            findings.append(Finding(
                agent=master_name,
                severity=Severity.WARNING,
                rule="wrapper-master-sync",
                message=f"Wrapper master manquant : {master_path.relative_to(project_root).as_posix()}",
                fix_hint="Restaurer le wrapper master workspace.",
            ))
        else:
            try:
                rendered_master = render_master_wrapper(wrapper_spec)
            except KeyError as exc:
                findings.append(Finding(
                    agent=master_name,
                    severity=Severity.ERROR,
                    rule="wrapper-master-sync",
                    message=str(exc),
                    fix_hint="Compléter la section `master` de agent-wrapper-spec.json.",
                ))
            else:
                if master_path.read_text(encoding="utf-8") != rendered_master:
                    findings.append(Finding(
                        agent=master_name,
                        severity=Severity.WARNING,
                        rule="wrapper-master-sync",
                        message="Le wrapper master n'est pas aligné avec agent-wrapper-spec.json",
                        fix_hint="Lancer agent-lint.py --project-root . --write-wrappers pour régénérer le wrapper master.",
                    ))

        findings.extend(lint_master_tool_policy(wrapper_spec))

    if not target_agent:
        unexpected = sorted(set(active_wrappers) - expected_names)
        for name in unexpected:
            findings.append(Finding(
                agent=name,
                severity=Severity.WARNING,
                rule="wrapper-spec",
                message=f"Wrapper workspace non géré par {WRAPPER_SPEC_RELATIVE_PATH}",
                fix_hint="Ajouter le wrapper à la spec canonique ou archiver la surface si elle n'est plus active.",
            ))

    return findings


# ── Parsing ──────────────────────────────────────────────────────────────────

def extract_frontmatter_block(content: str) -> str:
    """Extrait le bloc frontmatter YAML initial."""
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    return match.group(1) if match else ""

def extract_frontmatter_name(content: str) -> str:
    """Extrait le 'name' du frontmatter YAML."""
    m = re.search(r'^name:\s*["\']?([^"\'\n]+)', content, re.MULTILINE)
    return m.group(1).strip() if m else ""


def extract_frontmatter_array(content: str, key: str) -> list[str]:
    """Extrait une liste YAML inline ou block du frontmatter pour un champ donné."""
    frontmatter = extract_frontmatter_block(content)
    if not frontmatter:
        return []

    inline_match = re.search(rf"^{re.escape(key)}:\s*\[(.*?)\]\s*$", frontmatter, re.MULTILINE | re.DOTALL)
    if inline_match:
        raw_values = inline_match.group(1).strip()
        if not raw_values:
            return []
        return [
            item.strip().strip("\"'")
            for item in raw_values.split(",")
            if item.strip()
        ]

    lines = frontmatter.splitlines()
    for index, line in enumerate(lines):
        if not re.match(rf"^{re.escape(key)}:\s*$", line):
            continue

        values: list[str] = []
        for nested_line in lines[index + 1:]:
            if not nested_line.strip():
                if values:
                    break
                continue

            stripped = nested_line.lstrip()
            if not stripped.startswith("- "):
                break

            values.append(stripped[2:].strip().strip("\"'"))

        return values

    return []


def extract_agent_id(content: str) -> str:
    """Extrait l'agent id de la balise <agent>."""
    m = re.search(r'<agent\s+id="([^"]+)"', content)
    return m.group(1) if m else ""


def extract_agent_name(content: str) -> str:
    """Extrait le name= de la balise <agent>."""
    m = re.search(r'<agent\s[^>]*name="([^"]+)"', content)
    return m.group(1) if m else ""


def extract_config_path(content: str) -> str:
    """Extrait le chemin config.yaml de l'activation step 2."""
    m = re.search(r'Load and read \{project-root\}/([^\s]+config\.yaml)', content)
    return m.group(1) if m else ""


def extract_menu_cmds(content: str) -> list[tuple[str, str, dict]]:
    """Extrait toutes les commandes menu avec leurs attributs.
    
    Retourne: [(cmd_code, description, {attr: value, ...}), ...]
    """
    items = []
    # Match <item cmd="..." ...>[CODE] Description</item>
    pattern = re.compile(
        r'<item\s+([^>]+)>\s*\[([A-Z]{2,3})\]\s*(.*?)</item>',
        re.DOTALL
    )
    for m in pattern.finditer(content):
        attrs_str = m.group(1)
        cmd_code = m.group(2)
        description = m.group(3).strip()

        # Parse attributes
        attrs = {}
        for attr_m in re.finditer(r'(\w+)="([^"]*)"', attrs_str):
            attrs[attr_m.group(1)] = attr_m.group(2)

        items.append((cmd_code, description, attrs))
    return items


def extract_persona_blocks(content: str) -> dict[str, bool]:
    """Vérifie la présence des blocs persona obligatoires."""
    required = [
        "role", "identity", "voice", "decision_framework",
        "weaknesses", "output_preferences", "communication_style", "principles"
    ]
    result = {}
    for block in required:
        # Match both <block> and <block ...>
        pattern = rf'<{block}[\s>]'
        result[block] = bool(re.search(pattern, content))
    return result


def extract_handler_types(content: str) -> set[str]:
    """Extrait les types de handlers déclarés."""
    types = set()
    for m in re.finditer(r'<handler\s+type="(\w+)"', content):
        types.add(m.group(1))
    return types


# ── Lint Rules ───────────────────────────────────────────────────────────────

def lint_unique_cmds(agent_name: str, cmds: list[tuple[str, str, dict]]) -> list[Finding]:
    """Règle: chaque cmd code doit être unique dans un agent."""
    findings = []
    seen: dict[str, int] = {}
    for code, _desc, _ in cmds:
        seen[code] = seen.get(code, 0) + 1

    for code, count in seen.items():
        if count > 1:
            findings.append(Finding(
                agent=agent_name,
                severity=Severity.ERROR,
                rule="unique-cmd",
                message=f"Commande [{code}] dupliquée ({count}x)",
                fix_hint=f"Renommer l'une des commandes [{code}] avec un code unique"
            ))
    return findings


def lint_persona_completeness(agent_name: str, blocks: dict[str, bool]) -> list[Finding]:
    """Règle: tous les blocs persona obligatoires doivent être présents."""
    findings = []
    for block, present in blocks.items():
        if not present:
            findings.append(Finding(
                agent=agent_name,
                severity=Severity.ERROR if block in ("voice", "decision_framework") else Severity.WARNING,
                rule="persona-complete",
                message=f"Bloc <{block}> manquant dans la persona",
                fix_hint=f"Ajouter le bloc <{block}> dans la section <persona>"
            ))
    return findings


def lint_manifest_sync(
    agent_name: str,
    frontmatter_name: str,
    agent_xml_name: str,
    module: str,
    manifest: dict[str, dict],
) -> list[Finding]:
    """Règle: l'agent doit être dans le manifest avec des données cohérentes."""
    findings = []

    # Trouver l'entrée manifest correspondante
    # Le manifest utilise le frontmatter name ou une version slug
    slug = frontmatter_name.lower().replace(" ", "-")
    manifest_entry = manifest.get(slug) or manifest.get(frontmatter_name)

    if not manifest_entry:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.ERROR,
            rule="manifest-sync",
            message=f"Agent '{slug}' absent du agent-manifest.csv",
            fix_hint="Ajouter une entrée dans _grimoire-runtime/_config/agent-manifest.csv"
        ))
        return findings

    # Check module match
    manifest_module = manifest_entry.get("module", "")
    if manifest_module and manifest_module != module:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="manifest-sync",
            message=f"Module mismatch: agent dans '{module}' mais manifest dit '{manifest_module}'",
            fix_hint="Corriger le module dans agent-manifest.csv"
        ))

    # Check displayName match
    manifest_display = manifest_entry.get("displayName", "")
    if manifest_display and agent_xml_name and manifest_display != agent_xml_name:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="manifest-sync",
            message=f"DisplayName mismatch: agent='{agent_xml_name}' vs manifest='{manifest_display}'",
            fix_hint="Synchroniser displayName dans agent-manifest.csv"
        ))

    return findings


def lint_referenced_files(
    agent_name: str,
    cmds: list[tuple[str, str, dict]],
    project_root: Path,
) -> list[Finding]:
    """Règle: les fichiers référencés (exec, workflow, data) doivent exister."""
    findings = []
    file_attrs = ("exec", "workflow", "data")

    for code, _desc, attrs in cmds:
        for attr in file_attrs:
            if attr not in attrs:
                continue
            path_str = attrs[attr]
            if path_str == "todo":
                findings.append(Finding(
                    agent=agent_name,
                    severity=Severity.WARNING,
                    rule="file-exists",
                    message=f"[{code}] {attr}=\"todo\" — workflow non implémenté",
                    fix_hint=f"Implémenter le workflow pour [{code}]"
                ))
                continue

            # Resolve {project-root}
            resolved = path_str.replace("{project-root}/", "")
            full_path = project_root / resolved
            if not full_path.is_file():
                findings.append(Finding(
                    agent=agent_name,
                    severity=Severity.ERROR,
                    rule="file-exists",
                    message=f"[{code}] {attr}=\"{resolved}\" — fichier introuvable",
                    fix_hint="Vérifier le chemin ou créer le fichier manquant"
                ))

    return findings


def lint_handler_coverage(
    agent_name: str,
    cmds: list[tuple[str, str, dict]],
    declared_handlers: set[str],
) -> list[Finding]:
    """Règle: les handlers déclarés doivent couvrir les attributs utilisés dans le menu."""
    findings = []
    used_types: set[str] = set()

    for _code, _desc, attrs in cmds:
        for attr_type in ("exec", "workflow", "data", "action"):
            if attr_type in attrs:
                used_types.add(attr_type)

    missing = used_types - declared_handlers
    for handler_type in missing:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="handler-coverage",
            message=f"Menu utilise '{handler_type}=' mais aucun <handler type=\"{handler_type}\"> déclaré",
            fix_hint=f"Ajouter un handler type=\"{handler_type}\" dans <menu-handlers>"
        ))

    return findings


def lint_config_path(
    agent_name: str,
    config_path: str,
    module: str,
    project_root: Path,
) -> list[Finding]:
    """Règle: le config.yaml référencé doit exister et correspondre au module."""
    findings = []
    if not config_path:
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.ERROR,
            rule="config-path",
            message="Pas de chargement config.yaml trouvé dans l'activation step 2",
            fix_hint="Ajouter le chargement de config.yaml dans l'étape 2 d'activation"
        ))
        return findings

    # Check file exists
    full_path = project_root / config_path
    if not full_path.is_file():
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.ERROR,
            rule="config-path",
            message=f"Config '{config_path}' introuvable",
            fix_hint="Vérifier le chemin du config.yaml"
        ))

    # Check module match
    expected_prefix = f"_grimoire-runtime/{module}/"
    if not config_path.startswith(expected_prefix):
        findings.append(Finding(
            agent=agent_name,
            severity=Severity.WARNING,
            rule="config-path",
            message=f"Config path '{config_path}' ne correspond pas au module '{module}'",
            fix_hint=f"Utiliser _grimoire-runtime/{module}/config.yaml"
        ))

    return findings


# ── Main Lint ────────────────────────────────────────────────────────────────

def lint_agent(
    module: str,
    agent_path: Path,
    project_root: Path,
    manifest: dict[str, dict],
) -> list[Finding]:
    """Lint complet d'un fichier agent."""
    content = agent_path.read_text(encoding="utf-8")
    agent_name = agent_path.stem
    if agent_path.parent.name != "agents":
        # subdirectory agent (tech-writer/tech-writer.md)
        agent_name = agent_path.parent.name

    findings: list[Finding] = []

    # Extractions
    frontmatter_name = extract_frontmatter_name(content)
    agent_xml_name = extract_agent_name(content)
    config_path = extract_config_path(content)
    cmds = extract_menu_cmds(content)
    persona_blocks = extract_persona_blocks(content)
    handler_types = extract_handler_types(content)

    # Rules
    findings.extend(lint_unique_cmds(agent_name, cmds))
    findings.extend(lint_persona_completeness(agent_name, persona_blocks))
    findings.extend(lint_manifest_sync(agent_name, frontmatter_name, agent_xml_name, module, manifest))
    findings.extend(lint_referenced_files(agent_name, cmds, project_root))
    findings.extend(lint_handler_coverage(agent_name, cmds, handler_types))
    findings.extend(lint_config_path(agent_name, config_path, module, project_root))

    return findings


def run_lint(project_root: Path, target_agent: str | None = None) -> LintReport:
    """Exécute le lint sur tous les agents (ou un seul)."""
    report = LintReport()
    manifest = load_manifest(project_root)
    wrapper_spec = load_wrapper_spec(project_root)
    agents = discover_agents(project_root)

    for module, agent_path in agents:
        agent_name = agent_path.stem
        if agent_path.parent.name != "agents":
            agent_name = agent_path.parent.name

        if target_agent and agent_name != target_agent:
            continue

        report.agents_checked += 1
        findings = lint_agent(module, agent_path, project_root, manifest)
        report.findings.extend(findings)

        if not any(f.agent == agent_name for f in findings):
            report.agents_clean += 1

    surface_records = build_surface_index(project_root, manifest, wrapper_spec)
    report.findings.extend(lint_wrapper_sync(project_root, manifest, target_agent=target_agent))
    report.findings.extend(lint_surface_index_sync(project_root, surface_records, target_agent=target_agent))

    return report


# ── Output ───────────────────────────────────────────────────────────────────

def format_text(report: LintReport) -> str:
    """Formatage texte du rapport."""
    lines = [
        f"{'=' * 60}",
        f"  Agent Lint Report — v{AGENT_LINT_VERSION}",
        f"  Agents: {report.agents_checked} checked, {report.agents_clean} clean",
        f"  Findings: {len(report.errors)} errors, {len(report.warnings)} warnings, "
        f"{len(report.findings) - len(report.errors) - len(report.warnings)} info",
        f"{'=' * 60}",
    ]

    if not report.findings:
        lines.append("\n  ✅ Tous les agents sont conformes !")
        return "\n".join(lines)

    # Group by agent
    by_agent: dict[str, list[Finding]] = {}
    for f in report.findings:
        by_agent.setdefault(f.agent, []).append(f)

    for agent, findings in sorted(by_agent.items()):
        error_count = sum(1 for f in findings if f.is_error)
        warn_count = len(findings) - error_count
        status = "❌" if error_count else "⚠️"
        lines.append(f"\n{status} {agent} ({error_count} errors, {warn_count} warnings)")
        for f in findings:
            lines.append(f"  {f.severity} [{f.rule}] {f.message}")
            if f.fix_hint:
                lines.append(f"    💡 {f.fix_hint}")

    return "\n".join(lines)


def format_json(report: LintReport) -> str:
    """Formatage JSON du rapport."""
    return json.dumps({
        "version": AGENT_LINT_VERSION,
        "agents_checked": report.agents_checked,
        "agents_clean": report.agents_clean,
        "error_count": len(report.errors),
        "warning_count": len(report.warnings),
        "findings": [
            {
                "agent": f.agent,
                "severity": f.severity,
                "rule": f.rule,
                "message": f.message,
                "fix_hint": f.fix_hint,
            }
            for f in report.findings
        ],
    }, indent=2, ensure_ascii=False)


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint structurel des agents Grimoire",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project-root", type=Path, required=True,
        help="Racine du projet Grimoire",
    )
    parser.add_argument(
        "--agent", type=str, default=None,
        help="Nom de l'agent à linter (par défaut: tous)",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Sortie en format JSON",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Logs détaillés",
    )
    parser.add_argument(
        "--write-surface-index", action="store_true",
        help="Génère _grimoire-runtime/_config/agent-surface-index.csv et met à jour files-manifest.csv",
    )
    parser.add_argument(
        "--write-wrappers", action="store_true",
        help="Régénère les wrappers workspace gérés par agent-wrapper-spec.json",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    project_root = resolve_agent_project_root(args.project_root.resolve())
    if not (project_root / "_grimoire-runtime").is_dir():
        print(f"❌ Pas de répertoire _grimoire-runtime/ trouvé dans {project_root}", file=sys.stderr)
        return 1

    manifest = load_manifest(project_root)

    if args.write_wrappers:
        write_managed_wrappers(project_root, manifest, target_agent=args.agent)
        update_files_manifest(project_root)

    if args.write_surface_index:
        wrapper_spec = load_wrapper_spec(project_root)
        surface_records = build_surface_index(project_root, manifest, wrapper_spec)
        index_path = write_surface_index(project_root, surface_records)
        update_files_manifest(project_root, index_path)
        _log.info("Surface index written to %s", index_path)

    report = run_lint(project_root, args.agent)

    if args.json:
        print(format_json(report))
    else:
        print(format_text(report))

    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
