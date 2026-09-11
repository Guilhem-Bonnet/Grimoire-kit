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
import os
import re
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from grimoire.core import layout
from grimoire.core.exceptions import GrimoireAgentError
from grimoire.core.standard_state import active_profile_id, is_standard_enrolled
from grimoire.data import framework_path
from grimoire.hosts.secrets import secret_read_globs
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
    _partition_duplicate_pairs,
)

try:
    import grimoire_hosts_core as _rust_core
except ImportError:  # pragma: no cover - exercised by the dedicated Rust CI job
    _rust_core = None


def _use_rust_backend() -> bool:
    """Resolve which backend this module's Rust-optional functions should use.

    Own copy of :func:`grimoire.hosts.surface._use_rust_backend` (same
    precedent as ``grimoire.core.schema``/``grimoire.core.validator``, two
    independent Python modules backed by one crate that each read their
    shared env var independently rather than cross-importing a private
    module-level variable). Reads ``GRIMOIRE_HOSTS_BACKEND`` fresh every
    call so tests can flip it with ``monkeypatch.setenv``.
    """
    override = os.environ.get("GRIMOIRE_HOSTS_BACKEND", "auto").strip().lower()
    if override == "python":
        return False
    if override == "rust":
        if _rust_core is None:
            raise GrimoireAgentError(
                "GRIMOIRE_HOSTS_BACKEND=rust demande le coeur Rust, mais "
                "grimoire_hosts_core est introuvable. Construire l'extension "
                "localement (voir CONTRIBUTING.md, `maturin develop` dans "
                "rust/grimoire-hosts-core/) ou revenir a auto/python."
            )
        return True
    if override not in ("auto", ""):
        raise GrimoireAgentError(f"GRIMOIRE_HOSTS_BACKEND invalide: {override!r} (attendu auto/python/rust)")
    return _rust_core is not None


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
    """Split YAML frontmatter from body; ``({}, text)`` when there is none.

    Delegates to ``grimoire_hosts_core.parse_frontmatter`` (``rust/grimoire-hosts-core/``,
    issue #354) when the Rust backend is active — see the module docstring
    of :mod:`grimoire.hosts.surface` for ``GRIMOIRE_HOSTS_BACKEND``. The
    pure-Python path below is the reference implementation and is what runs
    when the compiled module is absent.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return _rust_core.parse_frontmatter(text)  # type: ignore[no-any-return]
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    try:
        data = _yaml().load(io.StringIO(match.group(1)))
    except Exception:
        return {}, match.group(2)
    return (data if isinstance(data, dict) else {}), match.group(2)


def _tool_verbs(raw: Any) -> tuple[ToolVerb, ...]:
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return tuple(ToolVerb(v) for v in _rust_core.tool_verbs(raw))
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


def _str_tuple(raw: Any) -> tuple[str, ...]:
    """Read a frontmatter list-of-strings key, tolerant of a single string."""
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return tuple(_rust_core.str_tuple(raw))
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return ()
    return tuple(str(item).strip() for item in raw if str(item).strip())


def infer_tools(body: str, description: str) -> tuple[ToolVerb, ...]:
    """Derive a tool boundary from what an agent says it does.

    Reading and searching are always granted — an agent that cannot read the
    project is useless. Writing and executing are granted only on an explicit
    signal in the persona's own text.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return tuple(ToolVerb(v) for v in _rust_core.infer_tools(body, description))
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
    """Définitions d'agents à projeter, l'override l'emportant sur le kit.

    La résolution passe par :func:`layout.agent_dirs` et non par un balayage
    de répertoires : la frontière kit/overrides est ce qui rend possible
    « personnaliser un agent sans forker les trente autres », et un émetteur
    qui lirait le tier kit en direct projetterait la persona livrée par-dessus
    celle du projet. Les emplacements d'avant la frontière restent lus, en
    dernier, pour qu'un projet non migré continue de fonctionner.
    """
    seen: dict[str, Path] = {}
    for directory in layout.agent_dirs(project_root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            if path.name.endswith(_TEMPLATE_SUFFIX):
                continue
            seen.setdefault(path.stem, path)
    return list(seen.values())


def _agent_name(path: Path) -> str | None:
    """Usable host-side name for an agent, or ``None`` when there is none.

    Delegates to :func:`layout.agent_identity` — the same frontmatter tag
    reading ``layout.installed_agents()`` uses — rather than parsing the
    ``name:`` field a second time. The blank agent template ships with
    ``name: "{{agent_tag}}"`` and the scaffold leaves unknown placeholders
    intact on purpose; ``agent_identity`` finds no tag there, so this returns
    ``None`` and the file is skipped rather than projected under its file
    stem. That fallback used to make an unrendered template a different
    "agent" for :func:`collect_agents` than for ``installed_agents`` — the
    same file, two names, exactly the divergence issue #381 closes. A gabarit
    en attente n'est pas un agent installé pour l'un des deux lecteurs : il ne
    doit l'être pour aucun.
    """
    identity = layout.agent_identity(path)
    if identity is None:
        return None
    tag, _persona = identity
    return tag if _SAFE_NAME.match(tag) else None


DEFAULT_ENTRY_AGENT = "concierge"


def entry_agent_name(project_root: Path) -> str:
    """The persona ``project-context.yaml`` designates as entry point.

    ``agents.entry`` — ``concierge`` when the file or the key is absent, and
    the empty string when the project declares it brings its own entry point.
    A config that fails to parse is reported by ``lint`` and ``doctor``; here
    it falls back to the default rather than turning a hook into a crash.
    """
    path = project_root / "project-context.yaml"
    if not path.is_file():
        return DEFAULT_ENTRY_AGENT
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError

    try:
        return GrimoireConfig.from_yaml(path).agents.entry
    except GrimoireConfigError:
        return DEFAULT_ENTRY_AGENT


def collect_agents(
    project_root: Path,
    *,
    entry_point: str | None = None,
    known_skills: frozenset[str] | None = None,
) -> tuple[AgentSpec, ...]:
    """Read the project's personas into host-neutral specs.

    *entry_point* defaults to what the project declares (``agents.entry``);
    pass it explicitly to override.

    *known_skills* is the set of slugs :func:`collect_skills` already
    resolved for this project. An agent's ``skills:`` frontmatter is checked
    against it fail-closed: a slug that resolves to nothing is a build error
    (:class:`~grimoire.core.exceptions.GrimoireAgentError`), the same
    treatment as an import that names a module which does not exist — not a
    warning, and not a silent drop.
    """
    if entry_point is None:
        entry_point = entry_agent_name(project_root)
    known_skills = known_skills or frozenset()
    specs: list[AgentSpec] = []
    for path in _agent_files(project_root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = parse_frontmatter(text)
        name = _agent_name(path)
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
        skills = _str_tuple(meta.get("skills"))
        unknown = [slug for slug in skills if slug not in known_skills]
        if unknown:
            raise GrimoireAgentError(
                f"L'agent « {name} » ({definition_ref}) déclare des skills introuvables : "
                f"{', '.join(unknown)}. Un identifiant de skill doit résoudre contre "
                "l'inventaire collecté par collect_skills(), comme un import cassé."
            )
        context = _str_tuple(meta.get("context"))
        missing_context = [c for c in context if not (project_root / c).exists()]
        if missing_context:
            raise GrimoireAgentError(
                f"L'agent « {name} » ({definition_ref}) déclare un contexte inexistant sur "
                f"disque : {', '.join(missing_context)}."
            )
        specs.append(
            AgentSpec(
                name=name,
                description=description,
                definition_ref=definition_ref,
                tools=tools,
                affinity=ModelAffinity.from_frontmatter(meta.get("model_affinity")),
                entry_point=bool(entry_point) and name == entry_point,
                tools_origin="declared" if declared else "inferred",
                max_turns=_max_turns(meta.get("max_turns")),
                skills=skills,
                context=context,
            )
        )
    return tuple(specs)


def _max_turns(value: Any) -> int | None:
    """Parse an agent file's ``max_turns:`` override — a positive int, or ``None``.

    Excludes ``bool`` explicitly: it is an ``int`` subclass, and ``max_turns: true``
    is a malformed override, not a turn budget of 1.

    ``str.isascii()`` is checked alongside ``str.isdigit()``: some Unicode
    characters (``"²"``, superscript two) satisfy ``isdigit()`` but make
    ``int()`` raise ``ValueError`` — found by the Rust oracle in
    ``rust/grimoire-hosts-core/`` (issue #354), which only ever considers
    ASCII digits and so never had this crash to begin with. Without the
    ``isascii()`` guard, ``max_turns: ²`` used to crash ``collect_agents``
    (and everything built on it — ``build_surface``, ``grimoire host sync``)
    with an unhandled exception on an otherwise valid agent file.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        return _rust_core.max_turns(value)  # type: ignore[no-any-return]
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isascii() and stripped.isdigit():
            return int(stripped)
    return None


def _bundled(kind: str) -> list[Path]:
    directory = framework_path() / "hosts" / kind
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.md"))


def _skill_files(project_root: Path) -> list[Path]:
    """Archetype-shipped skill definitions, overrides winning over the kit tier.

    Mirrors :func:`_agent_files`: a skill an archetype attaches to one of its
    agents (issue #375) is copied by the scaffolder into the project's own
    skill directory, not bundled with the package, so it must be read from
    disk the same way agents are.
    """
    seen: dict[str, Path] = {}
    for directory in layout.skill_dirs(project_root):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            seen.setdefault(path.stem, path)
    return list(seen.values())


def collect_skills(project_root: Path, *, governed: bool | None = None) -> tuple[SkillSpec, ...]:
    """Bundled, host-wide skills plus the skills this project's archetypes ship.

    Bundled skills (``grimoire-agent-dispatch``, ``grimoire-memory``, …) are
    always in scope; project skills come from the archetype(s) the project
    installed and are what an agent's ``skills:`` frontmatter usually
    references (issue #375). A slug present in both wins from the project
    copy — the same override-over-kit precedence as agents.
    """
    enrolled = is_standard_enrolled(project_root) if governed is None else governed
    by_slug: dict[str, SkillSpec] = {}
    for path in [*_bundled("skills"), *_skill_files(project_root)]:
        slug = path.stem
        if slug == "grimoire-evidence" and not enrolled:
            # A protocol for gates a project does not have is noise in the
            # skill list, and noise is what makes a skill list unusable.
            continue
        meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
        by_slug[slug] = SkillSpec(
            slug=slug,
            name=str(meta.get("name") or slug),
            description=str(meta.get("description") or slug),
            body=body.strip() + "\n",
            tools=_tool_verbs(meta.get("tools")),
        )
    return tuple(by_slug.values())


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
    deny = tuple(f"read:{glob}" for glob in secret_read_globs())
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


def _is_override(definition_ref: str) -> bool:
    """Un agent vit-il dans la couche de personnalisation du projet ?

    ``definition_ref`` est le chemin du fichier relatif à la racine du projet
    (voir :func:`collect_agents`) ; la couche est donc lisible dans son préfixe.
    """
    return definition_ref.replace("\\", "/").startswith(f"{layout.OVERRIDES_DIR}/")


def build_surface(project_root: Path, *, project_name: str | None = None) -> ProjectSurface:
    """Read *project_root* into the surface every emitter renders from.

    Skills are collected before agents on purpose: an agent's ``skills:``
    frontmatter resolves against the skill inventory, so the inventory must
    exist first.
    """
    root = project_root.resolve()
    governed = is_standard_enrolled(root)
    skills = collect_skills(root, governed=governed)
    agents = collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    # Deux régimes, parce que deux responsabilités. Un agent créé dans les
    # overrides du projet qui a le même faisceau qu'un autre est une erreur de
    # l'utilisateur, et le système émergent repose sur ce refus : on lève. Deux
    # agents livrés par le kit au même faisceau sont une dette du kit (#375) ;
    # la faire porter à chaque projet en bloquant son `init` reviendrait à
    # punir l'utilisateur pour notre retard. On la garde visible, sans bloquer.
    # `_partition_duplicate_pairs` (grimoire.hosts.surface) porte la logique
    # a deux regimes elle-meme (issue #354) : delegue a
    # `grimoire_hosts_core.partition_duplicate_pairs` en un seul appel quand
    # le backend Rust est actif, compose sinon `duplicate_agent_fingerprints`
    # et le filtre `_is_override` ci-dessous, a l'identique du comportement
    # d'avant ce port.
    strict, duplicates = _partition_duplicate_pairs(
        agents, is_override=lambda agent: _is_override(agent.definition_ref)
    )
    if strict:
        pairs = ", ".join(f"{a} == {b}" for a, b in strict)
        raise GrimoireAgentError(
            f"Agents au faisceau identique (outils, contexte, skills) : {pairs}. "
            "Deux agents avec le même faisceau sont le même agent sous deux noms — "
            "fusionnez-les ou distinguez leur périmètre réel."
        )
    notes: tuple[str, ...] = ()
    if duplicates:
        pairs = ", ".join(f"{a} == {b}" for a, b in duplicates)
        notes = (
            f"Agents livrés par le kit au faisceau identique (outils, contexte, skills) : {pairs}. "
            "Dette connue du kit (Grimoire-kit#375), sans effet sur ce projet.",
        )
    return ProjectSurface(
        notes=notes,
        project_name=project_name or root.name,
        agents=agents,
        skills=skills,
        commands=collect_commands(root, governed=governed),
        hooks=governance_hooks(governed=governed),
        permissions=default_permissions(active_profile_id(root) if governed else "starter"),
        mcp_servers=collect_mcp_servers(root),
        governed=governed,
    )
