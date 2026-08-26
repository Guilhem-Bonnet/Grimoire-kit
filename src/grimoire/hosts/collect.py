"""Build the host-neutral surface from a project on disk.

One reader, one representation. Everything a host could offer is derived here
— from the project's own agent files, from the bundled skill and command
sources, from whether the project is enrolled in the governed standard — and
handed to the emitters as data.

Nothing in this module knows what a host is.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from grimoire.core.project import AGENT_DIR_NAMES
from grimoire.core.standard_state import active_profile_id, is_standard_enrolled
from grimoire.data import framework_path
from grimoire.hosts.surface import (
    AgentSpec,
    CommandSpec,
    Enforcement,
    HookEvent,
    HookSpec,
    McpServerSpec,
    ModelAffinity,
    PermissionSpec,
    ProjectSurface,
    SkillSpec,
    ToolVerb,
)

_FRONTMATTER_RE = re.compile(r"\A(?:﻿)?(?:<!--.*?-->\s*)?---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)

#: Body markers that justify widening an agent's tool boundary beyond reading.
#: Deliberately conservative: an over-granted boundary is a governance hole,
#: while a too-narrow one is a visible failure the operator can correct with an
#: explicit ``tools:`` key.
_EDIT_MARKERS = (
    "écrit",
    "écrire",
    "modifie",
    "modifier",
    "édite",
    "éditer",
    "implémente",
    "implémenter",
    "rédige",
    "rédiger",
    "génère",
    "générer",
    "refactor",
    "write",
    "edit",
    "implement",
    "generate",
)
_EXECUTE_MARKERS = (
    "exécute",
    "exécuter",
    "lance",
    "lancer",
    "commande",
    "terminal",
    "pytest",
    "build",
    "déploie",
    "déployer",
    "run ",
    "execute",
    "deploy",
    "test suite",
)


def _yaml() -> YAML:
    yaml = YAML(typ="safe")
    yaml.default_flow_style = False
    return yaml


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from body; ``({}, text)`` when there is none."""
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    try:
        data = _yaml().load(io.StringIO(match.group(1)))
    except Exception:
        return {}, match.group(2)
    return (data if isinstance(data, dict) else {}), match.group(2)


def _tool_verbs(raw: Any) -> tuple[ToolVerb, ...]:
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace(",", " ").split()]
    if not isinstance(raw, list):
        return ()
    verbs: list[ToolVerb] = []
    for item in raw:
        try:
            verb = ToolVerb(str(item).strip().lower())
        except ValueError:
            continue
        if verb not in verbs:
            verbs.append(verb)
    return tuple(verbs)


def infer_tools(body: str, description: str) -> tuple[ToolVerb, ...]:
    """Derive a tool boundary from what an agent says it does.

    Reading and searching are always granted — an agent that cannot read the
    project is useless. Writing and executing are granted only on an explicit
    signal in the persona's own text.
    """
    haystack = f"{description}\n{body}".lower()
    verbs = [ToolVerb.READ, ToolVerb.SEARCH]
    if any(marker in haystack for marker in _EDIT_MARKERS):
        verbs.append(ToolVerb.EDIT)
    if any(marker in haystack for marker in _EXECUTE_MARKERS):
        verbs.append(ToolVerb.EXECUTE)
    return tuple(verbs)


#: Blank agent templates shipped for the user to fill in. They still carry
#: unrendered ``{{placeholders}}``, so projecting them onto a host would
#: publish a persona that describes nothing.
_TEMPLATE_SUFFIX = ".tpl.md"

#: A host sub-agent name becomes a file name and an invocation token.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _agent_files(project_root: Path) -> list[Path]:
    seen: dict[str, Path] = {}
    for name in AGENT_DIR_NAMES:
        directory = project_root / "_grimoire" / name
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name.endswith(_TEMPLATE_SUFFIX):
                continue
            seen.setdefault(path.stem, path)
    return list(seen.values())


def _agent_name(declared: object, path: Path) -> str | None:
    """Usable host-side name for an agent, or ``None`` when there is none.

    The blank agent template ships with ``name: "{{agent_tag}}"`` and the
    scaffold leaves unknown placeholders intact on purpose, so a declared name
    is not necessarily a name. Falling back to the file stem is what the
    existing Copilot wrappers already do; an unusable stem means the file
    cannot become a sub-agent at all and is skipped rather than emitted as a
    persona called after a placeholder.
    """
    candidate = str(declared or "").strip()
    if candidate and _SAFE_NAME.match(candidate):
        return candidate
    return path.stem if _SAFE_NAME.match(path.stem) else None


def collect_agents(project_root: Path, *, entry_point: str = "concierge") -> tuple[AgentSpec, ...]:
    """Read the project's personas into host-neutral specs."""
    specs: list[AgentSpec] = []
    for path in _agent_files(project_root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        name = _agent_name(meta.get("name"), path)
        if name is None:
            continue
        description = str(meta.get("description") or f"Grimoire agent {name}").strip()
        declared = _tool_verbs(meta.get("tools"))
        tools = declared or infer_tools(body, description)
        try:
            # POSIX separators: this path is written into a generated
            # instruction telling an agent which file to read, and a Windows
            # backslash there is both wrong in Markdown and unreadable.
            definition_ref = path.relative_to(project_root).as_posix()
        except ValueError:
            definition_ref = path.as_posix()
        specs.append(
            AgentSpec(
                name=name,
                description=description,
                definition_ref=definition_ref,
                tools=tools,
                affinity=ModelAffinity.from_frontmatter(meta.get("model_affinity")),
                entry_point=name == entry_point,
                tools_origin="declared" if declared else "inferred",
            )
        )
    return tuple(specs)


def _bundled(kind: str) -> list[Path]:
    directory = framework_path() / "hosts" / kind
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.md"))


def collect_skills(project_root: Path, *, governed: bool | None = None) -> tuple[SkillSpec, ...]:
    """Bundled skills, minus the ones this project has no use for."""
    enrolled = is_standard_enrolled(project_root) if governed is None else governed
    skills: list[SkillSpec] = []
    for path in _bundled("skills"):
        slug = path.stem
        if slug == "grimoire-evidence" and not enrolled:
            # A protocol for gates a project does not have is noise in the
            # skill list, and noise is what makes a skill list unusable.
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        skills.append(
            SkillSpec(
                slug=slug,
                name=str(meta.get("name") or slug),
                description=str(meta.get("description") or slug),
                body=body.strip() + "\n",
                tools=_tool_verbs(meta.get("tools")),
            )
        )
    return tuple(skills)


def _prompt_commands() -> list[CommandSpec]:
    """The kit's existing Copilot prompts, read as host-neutral commands."""
    directory = framework_path() / "copilot" / "prompts"
    if not directory.is_dir():
        return []
    commands: list[CommandSpec] = []
    for path in sorted(directory.glob("*.prompt.md")):
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        commands.append(
            CommandSpec(
                slug=path.name.removesuffix(".prompt.md"),
                description=str(meta.get("description") or path.stem),
                body=body.strip() + "\n",
                tools=_tool_verbs(meta.get("tools")) or (ToolVerb.READ, ToolVerb.SEARCH),
                source="workflow",
            )
        )
    return commands


def collect_commands(project_root: Path, *, governed: bool | None = None) -> tuple[CommandSpec, ...]:
    """Bundled commands plus the kit's workflow prompts, deduplicated by slug."""
    enrolled = is_standard_enrolled(project_root) if governed is None else governed
    by_slug: dict[str, CommandSpec] = {}
    for path in _bundled("commands"):
        slug = path.stem
        if slug in {"grimoire-gate", "grimoire-proof", "grimoire-verify"} and not enrolled:
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        by_slug[slug] = CommandSpec(
            slug=slug,
            description=str(meta.get("description") or slug),
            body=body.strip() + "\n",
            argument_hint=str(meta.get("argument-hint") or meta.get("argument_hint") or ""),
            tools=_tool_verbs(meta.get("tools")) or (ToolVerb.READ, ToolVerb.EXECUTE),
        )
    for command in _prompt_commands():
        by_slug.setdefault(command.slug, command)
    return tuple(by_slug[slug] for slug in sorted(by_slug))


def governance_hooks(*, governed: bool) -> tuple[HookSpec, ...]:
    """The lifecycle contract of the governed standard.

    Activation runs everywhere — it is how a project states its rules at all.
    Everything that reads or enforces gates is emitted only for an enrolled
    project, because a gate that does not exist cannot be evaluated.
    """
    hooks = [
        HookSpec(
            event=HookEvent.SESSION_START,
            decision="grimoire.activation",
            enforcement=Enforcement.ADVISORY,
            rationale="Directive de session — mécanisme mesuré 40/40 contre 0/40 sans lui (campagne 2026-07-09).",
        ),
        HookSpec(
            event=HookEvent.PRE_TOOL_USE,
            decision="grimoire.tool-policy",
            enforcement=Enforcement.BLOCKING,
            matcher=("execute", "write", "secret"),
            timeout=10,
            rationale="Refus des mutations destructrices et des accès secrets, selon le profil de risque.",
        ),
    ]
    if not governed:
        return tuple(hooks)
    hooks.extend(
        [
            HookSpec(
                event=HookEvent.USER_PROMPT_SUBMIT,
                decision="grimoire.task-context",
                enforcement=Enforcement.ADVISORY,
                timeout=10,
                rationale="Nomme la tâche courante avant que le modèle ne choisisse où écrire ses preuves.",
            ),
            HookSpec(
                event=HookEvent.POST_TOOL_USE,
                decision="grimoire.evidence-trace",
                enforcement=Enforcement.ADVISORY,
                matcher=("write",),
                timeout=10,
                rationale="Rappelle qu'une écriture doit laisser une ligne de preuve.",
            ),
            HookSpec(
                event=HookEvent.PRE_COMPACT,
                decision="grimoire.context-capsule",
                enforcement=Enforcement.ADVISORY,
                rationale="Sauvegarde la tâche et les gates ouverts avant une remise à zéro du contexte.",
            ),
            HookSpec(
                event=HookEvent.SUBAGENT_STOP,
                decision="grimoire.subagent-gate",
                enforcement=Enforcement.ADVISORY,
                rationale="Remonte l'état des gates sans bloquer un sous-agent qui ne clôt pas la tâche.",
            ),
            HookSpec(
                event=HookEvent.STOP,
                decision="grimoire.evidence-gate",
                enforcement=Enforcement.BLOCKING,
                timeout=60,
                rationale="Une clôture sans gates verts est une tâche non terminée — la règle devient contrainte ici.",
            ),
        ]
    )
    return tuple(hooks)


def default_permissions(profile: str) -> PermissionSpec:
    """Declarative rules mirroring what the pre-tool-use decision enforces.

    Hosts with a native permission table get the rule twice — declared and
    enforced — which is deliberate: the table refuses without spawning a
    process, the hook catches what a glob cannot express.
    """
    deny = (
        "read:**/.env",
        "read:**/.env.*",
        "read:**/id_rsa",
        "read:**/id_ed25519",
        "read:**/*.pem",
        "read:**/secrets/**",
    )
    ask = (
        "execute:rm -rf *",
        "execute:git push --force*",
        "execute:git reset --hard*",
        "execute:terraform destroy*",
        "execute:kubectl delete*",
    )
    allow: tuple[str, ...] = (
        "execute:grimoire *",
        "execute:git status*",
        "execute:git diff*",
        "execute:git log*",
    )
    if profile == "production":
        # A production project pays for its ceremony: nothing is pre-approved.
        allow = ("execute:grimoire standard *",)
    return PermissionSpec(deny=deny, ask=ask, allow=allow)


def collect_mcp_servers(project_root: Path) -> tuple[McpServerSpec, ...]:
    """MCP servers the project declares, falling back to the kit's own."""
    config = project_root / ".mcp.json"
    if config.is_file():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        if isinstance(servers, dict) and servers:
            return tuple(
                McpServerSpec(
                    name=str(name),
                    command=str(spec.get("command", "")) if isinstance(spec, dict) else "",
                    args=tuple(str(a) for a in spec.get("args", [])) if isinstance(spec, dict) else (),
                )
                for name, spec in servers.items()
            )
    return (McpServerSpec(name="grimoire", command="grimoire-mcp"),)


def build_surface(project_root: Path, *, project_name: str | None = None) -> ProjectSurface:
    """Read *project_root* into the surface every emitter renders from."""
    root = project_root.resolve()
    governed = is_standard_enrolled(root)
    return ProjectSurface(
        project_name=project_name or root.name,
        agents=collect_agents(root),
        skills=collect_skills(root, governed=governed),
        commands=collect_commands(root, governed=governed),
        hooks=governance_hooks(governed=governed),
        permissions=default_permissions(active_profile_id(root) if governed else "starter"),
        mcp_servers=collect_mcp_servers(root),
        governed=governed,
    )
