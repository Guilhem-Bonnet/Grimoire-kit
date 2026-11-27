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
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from grimoire.core import layout
from grimoire.core.exceptions import GrimoireAgentError
from grimoire.core.safe_sources import (
    FRONTMATTER_RE,
    Exclusion,
    Refused,
    SourceBudget,
    context_path_issue,
    describe_exclusions,
    list_sources,
    read_source_text,
    source_dir_issue,
)
from grimoire.core.standard_state import active_profile_id, is_standard_enrolled
from grimoire.data import framework_path
from grimoire.hosts.secrets import secret_read_globs
from grimoire.hosts.surface import (
    MAX_INLINE_PERSONA_BYTES,
    AgentHookSpec,
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
    body_fingerprint,
    format_bytes,
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


_FRONTMATTER_RE = FRONTMATTER_RE  # une seule forme, partagée avec le contrôle borné du front matter

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
    verbs, _rejected = _tool_verbs_with_rejects(raw)
    return verbs


def _tool_verbs_with_rejects(raw: Any) -> tuple[tuple[ToolVerb, ...], tuple[str, ...]]:
    """Same derivation as :func:`_tool_verbs`, plus the raw tokens that did
    not resolve to a :class:`ToolVerb` (deduplicated, first-seen order).

    The tool boundary itself is unchanged by this: an unknown verb is still
    never granted. What changes is that the rejection is no longer invisible
    — :func:`collect_agents` turns this into a :class:`ProjectSurface`
    note naming the agent and the unknown token(s), the same discipline as
    the distinction guard (visible, never blocking). See the module
    docstring of ``rust/grimoire-hosts-core/src/lib.rs`` for why this was
    not changed into a rejection instead: doing so would silently narrow
    the tool boundary of any existing agent file with a typo, at kit
    upgrade time.
    """
    if _use_rust_backend():
        assert _rust_core is not None  # guarded by _use_rust_backend
        rust_verbs, rust_rejected = _rust_core.tool_verbs_with_rejects(raw)
        return tuple(ToolVerb(v) for v in rust_verbs), tuple(rust_rejected)
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.replace(",", " ").split()]
    if not isinstance(raw, list):
        return (), ()
    verbs: list[ToolVerb] = []
    rejected_list: list[str] = []
    for item in raw:
        candidate = str(item).strip().lower()
        try:
            verb = ToolVerb(candidate)
        except ValueError:
            if candidate not in rejected_list:
                rejected_list.append(candidate)
            continue
        if verb not in verbs:
            verbs.append(verb)
    return tuple(verbs), tuple(rejected_list)


def _file_size(path: Path, root: Path | None = None) -> int:
    """Size in bytes, ``0`` when the file vanished between check and read.

    The context path was just verified to exist; a race against a concurrent
    delete must not fail the whole surface build for a size annotation.
    With *root*, a path that resolves outside the project is not measured
    (``0``) : the annotation is emitted into host files and must not describe
    anything but the project.
    """
    try:
        if root is not None and not path.resolve().is_relative_to(root.resolve()):
            return 0
        return path.stat().st_size
    except OSError:
        return 0


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


def _sink(excluded: list[Exclusion] | None) -> list[Exclusion]:
    """La liste où consigner les exclusions : celle de l'appelant, ou une liste jetée."""
    return excluded if excluded is not None else []


def _label(path: Path, root: Path | None = None) -> str:
    """Le chemin relatif à la racine du projet (la copie refusée), ou ``dossier/fichier`` hors projet.

    Jamais un chemin de la machine ; le texte est assaini et borné à l'exclusion (:meth:`Exclusion.of`).
    """
    if root is not None:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return f"{path.parent.name}/{path.name}"


def _project_listing(
    directory: Path, project_root: Path, excluded: list[Exclusion], *, skip: Callable[[str], bool] | None = None
) -> dict[str, Path]:
    """``nom -> chemin`` des ``*.md`` d'un dossier de sources du projet, sans lien en chemin, borné, sans lecture."""
    label = _label(directory, project_root)
    issue = source_dir_issue(directory, project_root)
    if issue is not None:
        excluded.append(Exclusion.of(label, issue))
        return {}
    found, issues = list_sources(directory, label, skip=skip)
    excluded.extend(issues)
    return found


def _read_candidate(
    path: Path, root: Path | None, excluded: list[Exclusion], budget: SourceBudget | None = None
) -> str | None:
    """Le texte d'un fichier de source par la lecture sûre, ou ``None`` et l'exclusion de CE fichier.

    La lecture sûre borne aussi le front matter (ni ancre ni alias, bloc borné) : le texte rendu peut être
    analysé, par n'importe quel backend, sans développement imprévu.
    """
    try:
        return read_source_text(path, root=root, budget=budget)
    except Refused as exc:
        excluded.append(Exclusion.of(_label(path, root), exc))
    except Exception as exc:  # une erreur sur UN fichier est l'exclusion de CE fichier
        excluded.append(Exclusion.of(_label(path, root), f"fichier illisible ({exc.__class__.__name__})"))
    return None


def _first_readable(
    candidates: list[Path], root: Path | None, excluded: list[Exclusion], budget: SourceBudget | None = None
) -> tuple[Path, str] | None:
    """Le premier fichier lisible parmi les copies d'un même nom (l'override d'abord), les refus étant consignés."""
    start = len(excluded)
    for path in candidates:
        text = _read_candidate(path, root, excluded, budget)
        if text is not None:
            _say_fallback(excluded, start, path, root)
            return path, text
    return None


def _say_fallback(excluded: list[Exclusion], start: int, served: Path, root: Path | None) -> None:
    """Complète les refus faits depuis *start* : une autre copie du même nom est servie à leur place."""
    if len(excluded) == start:
        return
    kit_tier = root is not None and _is_kit_copy(served, root)
    suffix = "copie du kit servie" if kit_tier else "copie suivante servie"
    for i in range(start, len(excluded)):
        item = excluded[i]
        if item.reason != "budget de collecte atteint":
            excluded[i] = Exclusion.of(item.name, f"{item.reason} ; {suffix}")


def _checked_context(project_root: Path, context: tuple[str, ...], agent: str, excluded: list[Exclusion]) -> tuple[str, ...]:
    """Les chemins de ``context:`` qui restent dans le projet ; les autres sont retirés et dits.

    Un chemin déclaré doit être relatif, rester sous la racine, ne passer par aucun lien symbolique et
    désigner un fichier ordinaire. L'agent reste collecté, sans ce contexte. Un chemin simplement absent
    du projet n'est pas traité ici : il reste l'erreur de build de :func:`collect_agents`.
    """
    kept: list[str] = []
    for rel in context:
        issue = context_path_issue(project_root, rel)
        if issue is None:
            kept.append(rel)
        else:
            excluded.append(Exclusion.of(f"contexte de l'agent {agent} : {rel}", issue))
    return tuple(kept)


def _is_kit_copy(path: Path, root: Path) -> bool:
    """La copie servie est-elle du kit (son tier sous ``_grimoire/kit``) et non une surcharge du projet ?"""
    try:
        path.relative_to(layout.kit_dir(root))
    except ValueError:
        return False
    return True


def _agent_candidates(project_root: Path, excluded: list[Exclusion]) -> list[list[Path]]:
    """Définitions d'agents à projeter : par nom, les copies par ordre de priorité (l'override avant le kit).

    La résolution passe par :func:`layout.agent_dirs` et non par un balayage
    de répertoires : la frontière kit/overrides est ce qui rend possible
    « personnaliser un agent sans forker les trente autres », et un émetteur
    qui lirait le tier kit en direct projetterait la persona livrée par-dessus
    celle du projet. Les emplacements d'avant la frontière restent lus, en
    dernier, pour qu'un projet non migré continue de fonctionner.

    Aucun dossier ne passe par un lien et aucun n'est lu au-delà de ses bornes ; une copie refusée à
    la lecture laisse la suivante servir le même nom (:func:`_first_readable`).
    """
    seen: dict[str, list[Path]] = {}
    for directory in layout.agent_dirs(project_root):
        names = _project_listing(directory, project_root, excluded, skip=lambda n: n.endswith(_TEMPLATE_SUFFIX))
        for stem, path in names.items():
            seen.setdefault(stem, []).append(path)
    return list(seen.values())


def _agent_name(path: Path, text: str) -> str | None:
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
    identity = layout.agent_identity_of_text(text, path.stem)
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


def _inline_persona_enabled(project_root: Path) -> bool:
    """``hosts.inline_persona``, ``True`` when absent or unreadable.

    A config that fails to parse is reported by ``lint``/``doctor``; host sync
    falls back to its default rather than turning a surface build into a crash
    — same tolerance as :func:`entry_agent_name`.
    """
    path = project_root / "project-context.yaml"
    if not path.is_file():
        return True
    from grimoire.core.config import GrimoireConfig
    from grimoire.core.exceptions import GrimoireConfigError

    try:
        return GrimoireConfig.from_yaml(path).hosts.inline_persona
    except GrimoireConfigError:
        return True


#: Frontmatter keys a partial override (``extends: kit``) may redefine — the
#: rest is inherited from the kit file of the same name, body included
#: (issue #427). ``tool_boundary`` is not part of the issue's own list (it is
#: prose the cockpit displays, never read structurally — see
#: ``grimoire.tools.workspace_api``) but is included here for the same reason
#: it is in ``workspace_routes._AGENT_STRING_FIELDS``: leaving it out would
#: make the cockpit's own clause editor silently drop a field it has always
#: been able to write.
PARTIAL_OVERRIDE_FIELDS = frozenset(
    {
        "model_affinity",
        "context",
        "skills",
        "tools",
        "use_when",
        "dont_use_when",
        "max_turns",
        "description",
        "tool_boundary",
        "hooks",
    }
)


def _kit_counterpart(project_root: Path, override_path: Path) -> Path:
    """Kit agent file *override_path* would extend, same file name, kit tier."""
    return kit_dir_agents(project_root) / override_path.name


def kit_dir_agents(project_root: Path) -> Path:
    """``_grimoire/kit/agents/`` for *project_root* — named once for reuse."""
    return layout.kit_dir(project_root) / layout.AGENTS_SUBDIR


def _is_override_tier_path(project_root: Path, path: Path) -> bool:
    """Whether *path* sits in the project's overrides tier (any subdir)."""
    try:
        path.resolve().relative_to(layout.overrides_dir(project_root).resolve())
    except ValueError:
        return False
    return True


class UnreadableSourceError(GrimoireAgentError):
    """Le fichier kit d'un override ``extends: kit`` existe mais la lecture sûre le refuse.

    Distinct d'un kit absent (qui reste une erreur de build, :class:`GrimoireAgentError`) : un fichier
    refusé n'est que l'exclusion de CET agent, jamais la perte de la surface.
    """


def resolve_extends_kit(
    project_root: Path,
    override_path: Path,
    override_meta: dict[str, Any],
    *,
    kit_path: Path | None = None,
) -> tuple[dict[str, Any], str, Path]:
    """Merge a ``extends: kit`` override onto its kit counterpart.

    Returns ``(effective_meta, effective_body, kit_path)`` — *effective_meta*
    is the kit file's own frontmatter with every key in
    :data:`PARTIAL_OVERRIDE_FIELDS` that *override_meta* declares applied on
    top; *effective_body* is always the kit file's body, unchanged (a partial
    override never carries its own body — that is the point of it staying
    thin across kit upgrades). Raises :class:`GrimoireAgentError` when no kit
    agent of the same name exists: ``extends: kit`` promises a base to extend,
    and a promise with nothing behind it is a build error, not a silent
    fallback to an empty persona.

    *kit_path* names the kit file to extend when the caller knows it better
    than the installed tier does — the scaffolder, which merges at plan time,
    before the regenerated kit tier is on disk (issue #679). Default: the
    installed kit counterpart of the same name.
    """
    # Le fichier kit du projet est lu comme tout fichier du projet : régulier, sans lien, borné. Un
    # *kit_path* fourni par l'appelant (le scaffolder, qui fusionne avant que le tier kit soit sur disque)
    # est une source du kit lui-même, hors du projet : il n'est pas confiné.
    confine = kit_path is None
    if kit_path is None:
        kit_path = _kit_counterpart(project_root, override_path)
    if not os.path.lexists(kit_path):
        try:
            override_ref = override_path.relative_to(project_root).as_posix()
        except ValueError:
            override_ref = override_path.as_posix()
        raise GrimoireAgentError(
            f"L'override « {override_path.stem} » ({override_ref}) déclare "
            f"`extends: kit` mais aucun agent kit de même nom n'existe sous "
            f"{layout.KIT_DIR}/{layout.AGENTS_SUBDIR}/{override_path.name}."
        )
    try:
        kit_text = read_source_text(kit_path, root=project_root if confine else None)
    except Refused as exc:
        raise UnreadableSourceError(
            f"L'override « {override_path.stem} » déclare `extends: kit` mais son agent kit n'est pas lisible : {exc}."
        ) from exc
    kit_meta, kit_body = parse_frontmatter(kit_text)
    effective_meta = dict(kit_meta)
    for key in PARTIAL_OVERRIDE_FIELDS:
        if key in override_meta:
            effective_meta[key] = override_meta[key]
    return effective_meta, kit_body, kit_path


def effective_agent_frontmatter(project_root: Path, agent: AgentSpec) -> dict[str, Any]:
    """Re-read *agent*'s frontmatter as :func:`collect_agents` effectively saw it.

    For a plain (non-extending) agent this is just its own frontmatter. For a
    partial override (``agent.override_kind == "partial"``) it is the merge
    :func:`resolve_extends_kit` computes — the same rule, applied again on a
    fresh read, for callers that only have the built :class:`AgentSpec` and
    need a field it does not carry (``use_when``, ``dont_use_when``,
    ``tool_boundary`` — see the module docstring of
    :mod:`grimoire.tools.workspace_api`). Never raises: a file that vanished
    between the original collection and this call yields ``{}``, the same
    tolerance :func:`grimoire.tools.workspace_api._agent_clause` already had.
    """
    root = project_root.resolve()
    if agent.override_kind == "partial" and agent.override_ref:
        override_path = root / agent.override_ref
        try:
            override_text = read_source_text(override_path, root=root)
        except Refused:
            return {}
        override_meta, _ = parse_frontmatter(override_text)
        try:
            effective_meta, _body, _kit_path = resolve_extends_kit(root, override_path, override_meta)
        except GrimoireAgentError:
            return override_meta
        return effective_meta
    try:
        text = read_source_text(root / agent.definition_ref, root=root)
    except Refused:
        return {}
    meta, _ = parse_frontmatter(text)
    return meta


def collect_agents(
    project_root: Path,
    *,
    entry_point: str | None = None,
    known_skills: frozenset[str] | None = None,
    notes: list[str] | None = None,
    inline_persona: bool = True,
    excluded: list[Exclusion] | None = None,
    tolerate_refused_skills: bool = False,
    refused_skills: Mapping[str, str] | None = None,
) -> tuple[AgentSpec, ...]:
    """Read the project's personas into host-neutral specs.

    *refused_skills* is ``slug -> reason`` for the skills :func:`collect_skills` refused (its *refused*
    out-parameter); only these are tolerated, and named as refused in the build error. Collected on its own
    when *known_skills* is left ``None``.

    With *tolerate_refused_skills* (a read-only view, never a host sync), a declared skill that the safe
    reading refused is left off the agent and stays reported in *excluded* instead of failing every agent;
    a slug that resolves to nothing and was never refused remains a build error.

    *entry_point* defaults to what the project declares (``agents.entry``);
    pass it explicitly to override.

    *known_skills* is the set of slugs :func:`collect_skills` already
    resolved for this project. An agent's ``skills:`` frontmatter is checked
    against it fail-closed: a slug that resolves to nothing is a build error
    (:class:`~grimoire.core.exceptions.GrimoireAgentError`), the same
    treatment as an import that names a module which does not exist — not a
    warning, and not a silent drop.

    Left ``None`` (the default), this function resolves its own inventory
    via :func:`collect_skills` — no caller can fall back into the gap that
    caused issue #423: passing no inventory used to silently become an
    *empty* one, so every agent with a ``skills:`` frontmatter (the default
    shape of archetype agents since #377/#387) failed fail-closed as if its
    skills did not exist, even though :func:`collect_skills` would have
    found every one of them. Pass an explicit inventory only to reuse one
    already collected for another purpose (e.g. :func:`build_surface`, which
    needs the same :class:`SkillSpec` objects, not just their slugs) or to
    deliberately test the fail-closed path with a narrower set.

    *notes* is an optional out-parameter (appended to in place, never
    replaced): when given, an agent declaring a ``tools:`` token that is not
    a member of :class:`ToolVerb` (a frontmatter typo, e.g. ``tools: [read,
    reed]``) appends one note naming the agent and the rejected token(s) —
    the tool boundary itself is unchanged (an unknown verb still grants
    nothing), only the silence is removed. Left ``None`` by default so
    existing callers that do not care about diagnostics see no behaviour
    change; :func:`build_surface` passes a list and folds it into
    :attr:`ProjectSurface.notes`.

    *inline_persona* carries the persona body into
    :attr:`AgentSpec.definition_body` so emitters can fold it into the host
    definition instead of asking every session to read it (#1179). ``True``
    by default; ``False`` (``hosts.inline_persona: false``) restores the
    pointer-only definition. The size cap itself lives in
    :meth:`AgentSpec.inline_body`, so this flag only decides whether the body
    is available at all.
    """
    sink = _sink(excluded)
    if entry_point is None:
        entry_point = entry_agent_name(project_root)
    refused_skills = dict(refused_skills or {})
    if known_skills is None:
        known_skills = frozenset(spec.slug for spec in collect_skills(project_root, excluded=sink, refused=refused_skills))
    specs: list[AgentSpec] = []
    budget = SourceBudget()
    for candidates in _agent_candidates(project_root, sink):
        # Lecture sûre : fichier régulier, sans lien, borné, UTF-8. Un fichier refusé est exclu (et dit),
        # la copie suivante du même nom sert, les autres agents restent collectés.
        loaded = _first_readable(candidates, project_root, sink, budget)
        if loaded is None:
            continue
        path, text = loaded
        meta, body = parse_frontmatter(text)
        name = _agent_name(path, text)
        if name is None:
            continue

        # Override partiel (issue #427) : `extends: kit` ne redéfinit que les
        # clés de `PARTIAL_OVERRIDE_FIELDS`, tout le reste — corps compris —
        # vient du fichier kit de même nom. La fusion est un dict Python pur,
        # faite avant tout appel aux fonctions ci-dessous qui savent déjà
        # basculer sur `grimoire_hosts_core` : le port Rust n'a donc rien à
        # changer, il continue de recevoir le (meta, body) déjà résolu.
        override_ref: str | None = None
        override_kind: str | None = None
        body_ref = path
        if _is_override_tier_path(project_root, path):
            try:
                override_ref = path.relative_to(project_root).as_posix()
            except ValueError:
                override_ref = path.as_posix()
            if str(meta.get("extends", "")).strip().lower() == "kit":
                try:
                    meta, body, body_ref = resolve_extends_kit(project_root, path, meta)
                except UnreadableSourceError as exc:
                    sink.append(Exclusion.of(_label(path, project_root), exc))
                    continue
                override_kind = "partial"
            else:
                override_kind = "full"

        description = str(meta.get("description") or f"Grimoire agent {name}").strip()
        declared, rejected_verbs = _tool_verbs_with_rejects(meta.get("tools"))
        tools = declared or infer_tools(body, description)
        if rejected_verbs and notes is not None:
            expected = ", ".join(v.value for v in ToolVerb)
            notes.append(
                f"Agent « {name} » : verbe(s) d'outil inconnu(s) ignoré(s) : "
                f"{', '.join(rejected_verbs)} (attendus : {expected})."
            )
        try:
            # POSIX separators: this path is written into a generated
            # instruction telling an agent which file to read, and a Windows
            # backslash there is both wrong in Markdown and unreadable.
            # `body_ref` is the kit file for a partial override (it is the
            # one carrying the body a host must read in full — see the
            # module docstring of `grimoire.hosts.emitters.claude_code`) and
            # *path* everywhere else, unchanged from before this issue.
            definition_ref = body_ref.relative_to(project_root).as_posix()
        except ValueError:
            definition_ref = body_ref.as_posix()
        skills = _str_tuple(meta.get("skills"))
        unknown = [slug for slug in skills if slug not in known_skills]
        if unknown and tolerate_refused_skills:
            skills = tuple(slug for slug in skills if slug not in refused_skills)
            unknown = [slug for slug in unknown if slug not in refused_skills]
        if unknown:
            refused = "".join(
                f" Le skill « {slug} » a été refusé à la collecte : {refused_skills[slug]}."
                for slug in unknown
                if slug in refused_skills
            )
            raise GrimoireAgentError(
                f"L'agent « {name} » ({definition_ref}) déclare des skills introuvables : "
                f"{', '.join(unknown)}. Un identifiant de skill doit résoudre contre "
                f"l'inventaire collecté par collect_skills(), comme un import cassé.{refused}"
            )
        context = _checked_context(project_root, _str_tuple(meta.get("context")), name, sink)
        missing_context = [c for c in context if not (project_root / c).exists()]
        if missing_context:
            raise GrimoireAgentError(
                f"L'agent « {name} » ({definition_ref}) déclare un contexte inexistant sur "
                f"disque : {', '.join(missing_context)}."
            )
        # #1179 : la définition émise annonce la taille de chaque document
        # déclaré, pour que le sous-agent décide de l'ouvrir ou non au lieu de
        # recevoir un ordre de lecture indifférencié.
        context_sizes = tuple((c, _file_size(project_root / c, project_root)) for c in context)
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
                hooks=_agent_hooks(meta.get("hooks"), name, definition_ref),
                definition_body=body.strip() if inline_persona else "",
                context_sizes=context_sizes,
                override_ref=override_ref,
                override_kind=override_kind,
                body_fingerprint=body_fingerprint(body),
            )
        )
    return tuple(specs)


#: Événements qu'un hook d'agent peut viser (noms PascalCase du fichier d'agent Copilot).
AGENT_HOOK_EVENTS = frozenset(
    {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "PreCompact", "SubagentStart", "SubagentStop", "Stop"}
)

#: Ce qui désigne la garde du kit : un hook d'agent qui l'appellerait la doublerait.
_GUARD_MARKERS = ("grimoire-hook", "grimoire host hook", "grimoire standard activation-context")


def _agent_hooks(raw: Any, name: str, ref: str) -> tuple[AgentHookSpec, ...]:
    """Lit la clé ``hooks:`` d'un fichier d'agent : ``{Événement: [{command, windows?, timeout?}]}`` (#1280 P3).

    Un hook qui relance la garde du kit est refusé : elle vit dans les fichiers de hooks
    du projet, la répéter ici la ferait tourner deux fois (ou la ferait croire retirée).
    """
    if raw in (None, {}):
        return ()
    where = f"L'agent « {name} » ({ref}) : `hooks:`"
    if not isinstance(raw, dict):
        raise GrimoireAgentError(f"{where} doit être un mapping événement → liste de hooks.")
    out: list[AgentHookSpec] = []
    for event, entries in raw.items():
        if event not in AGENT_HOOK_EVENTS:
            raise GrimoireAgentError(
                f"{where} événement inconnu « {event} » (attendus : {', '.join(sorted(AGENT_HOOK_EVENTS))})."
            )
        if isinstance(entries, dict):
            entries = [entries]
        if not isinstance(entries, list) or not entries:
            raise GrimoireAgentError(f"{where} « {event} » doit être une liste non vide de hooks.")
        for entry in entries:
            command = entry.get("command") if isinstance(entry, dict) else None
            if not isinstance(command, str) or not command.strip():
                raise GrimoireAgentError(f"{where} « {event} » : chaque hook porte une `command` non vide.")
            windows = entry.get("windows") or ""
            if not isinstance(windows, str):
                raise GrimoireAgentError(f"{where} « {event} » : `windows` est une chaîne.")
            if any(marker in f"{command} {windows}" for marker in _GUARD_MARKERS):
                raise GrimoireAgentError(
                    f"{where} « {event} » : la garde du kit est globale, un hook d'agent ne la relance pas."
                )
            timeout = entry.get("timeout")
            if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0):
                raise GrimoireAgentError(f"{where} « {event} » : `timeout` est un entier de secondes positif.")
            out.append(AgentHookSpec(str(event), command.strip(), windows.strip(), timeout))
    return tuple(out)


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


def _optional_bool(meta: dict[str, Any], *keys: str) -> bool | None:
    """Un booléen de frontmatter, ou ``None`` quand la source ne le déclare pas (ou pas en booléen)."""
    for key in keys:
        value = meta.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
    return None


def _bundled(kind: str) -> list[Path]:
    directory = framework_path() / "hosts" / kind
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.md"))


def _skill_candidates(project_root: Path, excluded: list[Exclusion]) -> list[tuple[str, list[Path]]]:
    """Archetype-shipped skill definitions: per slug, the copies by priority (overrides before the kit tier).

    Mirrors :func:`_agent_candidates`: a skill an archetype attaches to one of its
    agents (issue #375) is copied by the scaffolder into the project's own
    skill directory, not bundled with the package, so it must be read from
    disk the same way agents are — through the safe reading (no link followed,
    bounded), a refused copy leaving the next one to serve the slug.
    """
    seen: dict[str, list[Path]] = {}
    for directory in layout.skill_dirs(project_root):
        for stem, path in _project_listing(directory, project_root, excluded).items():
            seen.setdefault(stem, []).append(path)
    return list(seen.items())


def _read_markdown(
    path: Path, root: Path | None, excluded: list[Exclusion], budget: SourceBudget | None = None
) -> tuple[dict[str, Any], str] | None:
    """Frontmatter et corps d'un fichier de source, ou ``None`` et l'exclusion de CE fichier.

    La lecture sûre (et le contrôle borné du front matter) passe AVANT l'analyse, quel que soit le backend
    (Python ou Rust) de cette dernière. Seule la configuration invalide du backend
    (:class:`GrimoireAgentError`) remonte.
    """
    text = _read_candidate(path, root, excluded, budget)
    if text is None:
        return None
    try:
        return parse_frontmatter(text)
    except GrimoireAgentError:
        raise
    except Exception as exc:
        excluded.append(Exclusion.of(_label(path, root), f"fichier illisible ({exc.__class__.__name__})"))
        return None


def _skill_spec(
    slug: str, path: Path, root: Path | None, excluded: list[Exclusion], budget: SourceBudget | None = None
) -> SkillSpec | None:
    """Le skill de ce fichier, ou ``None`` (et l'exclusion de CE fichier)."""
    parsed = _read_markdown(path, root, excluded, budget)
    if parsed is None:
        return None
    meta, body = parsed
    try:
        return SkillSpec(
            slug=slug,
            name=str(meta.get("name") or slug),
            description=str(meta.get("description") or slug),
            body=body.strip() + "\n",
            tools=_tool_verbs(meta.get("tools")),
            license=str(meta.get("license") or ""),
            compatibility=str(meta.get("compatibility") or ""),
            user_invocable=_optional_bool(meta, "user-invocable", "user_invocable"),
            disable_model_invocation=_optional_bool(meta, "disable-model-invocation", "disable_model_invocation"),
            argument_hint=str(meta.get("argument-hint") or meta.get("argument_hint") or ""),
        )
    except Exception as exc:  # un champ de frontmatter inattendu exclut CE skill, pas la surface
        excluded.append(Exclusion.of(_label(path, root), f"fichier illisible ({exc.__class__.__name__})"))
        return None


def collect_skills(
    project_root: Path,
    *,
    governed: bool | None = None,
    excluded: list[Exclusion] | None = None,
    refused: dict[str, str] | None = None,
) -> tuple[SkillSpec, ...]:
    """Bundled, host-wide skills plus the skills this project's archetypes ship.

    *refused* is an optional out-parameter: ``slug -> reason`` for each project skill whose every copy the
    safe reading refused (and that no other copy serves). Only skill folders feed it.

    Bundled skills (``grimoire-agent-dispatch``, ``grimoire-memory``, …) are
    always in scope; project skills come from the archetype(s) the project
    installed and are what an agent's ``skills:`` frontmatter usually
    references (issue #375). A slug present in both wins from the project
    copy — the same override-over-kit precedence as agents.

    Project files are untrusted: each is read by :mod:`grimoire.core.safe_sources`
    (regular file only, no link followed, bounded size and count, strict UTF-8).
    A file that cannot be read is left out *alone* and appended to *excluded*
    (the optional out-parameter, like ``notes`` in :func:`collect_agents`):
    this function never raises for one bad file, and a refused project copy
    leaves the kit's skill of the same slug served.
    """
    sink = _sink(excluded)
    _use_rust_backend()  # une configuration de backend invalide reste une erreur, pas une exclusion
    enrolled = is_standard_enrolled(project_root) if governed is None else governed
    by_slug: dict[str, SkillSpec] = {}
    for path in _bundled("skills"):
        slug = path.stem
        if slug == "grimoire-evidence" and not enrolled:
            # A protocol for gates a project does not have is noise in the
            # skill list, and noise is what makes a skill list unusable.
            continue
        spec = _skill_spec(slug, path, None, sink)
        if spec is not None:
            by_slug[slug] = spec
    budget = SourceBudget()  # plafond global de la collecte : au-delà, les sources restantes sont exclues
    for slug, candidates in _skill_candidates(project_root, sink):
        if slug == "grimoire-evidence" and not enrolled:
            continue
        start = len(sink)
        served: Path | None = None
        for path in candidates:
            spec = _skill_spec(slug, path, project_root, sink, budget)
            if spec is not None:
                by_slug[slug] = spec
                served = path
                break
        if served is not None:
            _say_fallback(sink, start, served, project_root)
        elif slug not in by_slug:
            if refused is not None and len(sink) > start:
                refused[slug] = sink[start].reason
        elif slug in by_slug:  # toutes les copies du projet refusées : celle du kit livré reste servie
            for i in range(start, len(sink)):
                if sink[i].reason != "budget de collecte atteint":
                    sink[i] = Exclusion.of(sink[i].name, f"{sink[i].reason} ; copie du kit servie")
    return tuple(by_slug.values())


def _prompt_commands(excluded: list[Exclusion] | None = None) -> list[CommandSpec]:
    """The kit's existing Copilot prompts, read as host-neutral commands."""
    sink = _sink(excluded)
    directory = framework_path() / "copilot" / "prompts"
    if not directory.is_dir():
        return []
    commands: list[CommandSpec] = []
    for path in sorted(directory.glob("*.prompt.md")):
        parsed = _read_markdown(path, None, sink)
        if parsed is None:
            continue
        meta, body = parsed
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


def collect_commands(
    project_root: Path, *, governed: bool | None = None, excluded: list[Exclusion] | None = None
) -> tuple[CommandSpec, ...]:
    """Bundled commands plus the kit's workflow prompts, deduplicated by slug.

    Same contract as :func:`collect_skills` : each file goes through the safe
    reading, an unreadable one is left out alone (and appended to *excluded*),
    and this function never raises for one bad file — an MCP server builds its
    prompts from it, and one broken file must not empty them all.
    """
    sink = _sink(excluded)
    _use_rust_backend()
    enrolled = is_standard_enrolled(project_root) if governed is None else governed
    by_slug: dict[str, CommandSpec] = {}
    for path in _bundled("commands"):
        slug = path.stem
        if slug in {"grimoire-gate", "grimoire-proof", "grimoire-verify"} and not enrolled:
            continue
        parsed = _read_markdown(path, None, sink)
        if parsed is None:
            continue
        meta, body = parsed
        by_slug[slug] = CommandSpec(
            slug=slug,
            description=str(meta.get("description") or slug),
            body=body.strip() + "\n",
            argument_hint=str(meta.get("argument-hint") or meta.get("argument_hint") or ""),
            tools=_tool_verbs(meta.get("tools")) or (ToolVerb.READ, ToolVerb.EXECUTE),
        )
    for command in _prompt_commands(sink):
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
                matcher=("write", "delegation", "execute"),
                timeout=10,
                rationale=(
                    "Rappelle qu'une écriture doit laisser une ligne de preuve, compte les "
                    "mutations faites en shell (sans ``execute``, le gate Stop laissait clore "
                    "hors tâche après un ``sed -i``) et journalise, sans contexte ajouté, tout "
                    "appel de délégation vers un sous-agent (#657)."
                ),
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
                    url=str(spec.get("url", "")) if isinstance(spec, dict) else "",
                )
                for name, spec in servers.items()
            )
    from grimoire.hosts.hook_launcher import mcp_command

    return (McpServerSpec(name="grimoire", command=mcp_command(project_root)),)


def _is_override(agent: AgentSpec) -> bool:
    """Un agent vit-il dans la couche de personnalisation du projet ?

    Lit ``agent.override_ref``, pas ``agent.definition_ref`` : depuis l'issue
    #427, un override partiel (``extends: kit``) pointe son ``definition_ref``
    vers le fichier kit (celui qui porte le corps — voir
    :func:`collect_agents`), donc un sniff de préfixe sur ``definition_ref``
    dirait à tort qu'il n'y a pas de personnalisation.
    """
    return agent.override_ref is not None


def build_surface(
    project_root: Path, *, project_name: str | None = None, write_cache: bool = True
) -> ProjectSurface:
    """Read *project_root* into the surface every emitter renders from.

    Skills are collected before agents on purpose: an agent's ``skills:``
    frontmatter resolves against the skill inventory, so the inventory must
    exist first.

    *write_cache* is forwarded to :func:`grimoire.core.standard_state.active_profile_id`
    — ``False`` for a caller with a no-side-effects contract (``grimoire_host_status``,
    a ``readOnlyHint`` MCP tool); ``True`` (default) for ``grimoire host sync``, which is
    allowed to warm the cache for the hook calls that follow.
    """
    root = project_root.resolve()
    governed = is_standard_enrolled(root)
    excluded: list[Exclusion] = []
    refused_skills: dict[str, str] = {}
    skills = collect_skills(root, governed=governed, excluded=excluded, refused=refused_skills)
    tool_notes: list[str] = []
    try:
        agents = collect_agents(
            root,
            refused_skills=refused_skills,
            known_skills=frozenset(s.slug for s in skills),
            notes=tool_notes,
            inline_persona=_inline_persona_enabled(root),
            excluded=excluded,
        )
    except GrimoireAgentError as exc:
        # L'erreur de build garde sa nature ; les fichiers ignorés qui l'expliquent peut-être lui sont joints.
        header, lines = describe_exclusions(excluded)
        if header:
            raise type(exc)(f"{exc} Fichiers ignorés à la collecte — {header} {', '.join(lines)}") from exc
        raise
    commands = collect_commands(root, governed=governed, excluded=excluded)
    # Deux régimes, parce que deux responsabilités. Un agent créé dans les
    # overrides du projet qui partage le faisceau *et* la fiche d'un autre est
    # une erreur de l'utilisateur, et le système émergent repose sur ce refus :
    # on lève. Depuis #1180 le faisceau seul ne suffit plus à condamner deux
    # agents : deux personas peuvent déclarer les mêmes outils, contexte et
    # skills tant que leur corps (ou, à défaut de corps, leur description)
    # diffère — sans quoi il fallait garder un document de contexte propre à
    # chacune juste pour rester distinctes. Deux agents livrés par le kit au
    # même faisceau et à la même fiche sont une dette du kit (#375) ; la faire
    # porter à chaque projet en bloquant son `init` reviendrait à punir
    # l'utilisateur pour notre retard. On la garde visible, sans bloquer.
    # `_partition_duplicate_pairs` (grimoire.hosts.surface) porte la logique
    # a deux regimes elle-meme (issue #354) : delegue a
    # `grimoire_hosts_core.partition_duplicate_pairs` en un seul appel quand
    # le backend Rust est actif, compose sinon `duplicate_agent_fingerprints`
    # et le filtre `_is_override` ci-dessous, a l'identique du comportement
    # d'avant ce port.
    strict, duplicates = _partition_duplicate_pairs(agents, is_override=_is_override)
    if strict:
        pairs = ", ".join(f"{a} == {b}" for a, b in strict)
        raise GrimoireAgentError(
            f"Agents au même faisceau et à la même fiche (outils, contexte, skills + corps) : {pairs}. "
            "Deux agents avec le même faisceau et la même fiche sont le même agent sous deux noms — "
            "fusionnez-les ou distinguez leur périmètre réel (outils, contexte, skills ou corps)."
        )
    notes: tuple[str, ...] = tuple(tool_notes)
    # Les fichiers du projet que la lecture sûre a refusés : dits, jamais silencieux, jamais sans borne.
    header, lines = describe_exclusions(excluded)
    if header:
        notes = (*notes, header, *(f"  {line}" for line in lines))
    # #1179 : une persona au-delà du plafond garde le pointeur. Ce n'est pas
    # une erreur — la définition reste utilisable — mais l'utilisateur doit
    # savoir pourquoi celle-là seule n'est pas intégrée.
    for agent in agents:
        if agent.definition_body and agent.inline_body() is None:
            notes = (
                *notes,
                f"Persona « {agent.name} » : corps de {format_bytes(agent.body_size())} au-delà du "
                f"plafond d'intégration ({format_bytes(MAX_INLINE_PERSONA_BYTES)}) — la définition "
                f"garde un pointeur vers `{agent.definition_ref}`.",
            )
    if duplicates:
        pairs = ", ".join(f"{a} == {b}" for a, b in duplicates)
        notes = (
            *notes,
            f"Agents livrés par le kit au même faisceau et à la même fiche (outils, contexte, skills + corps) : "
            f"{pairs}. Dette connue du kit (Grimoire-kit#375), sans effet sur ce projet.",
        )
    return ProjectSurface(
        notes=notes,
        project_name=project_name or root.name,
        agents=agents,
        skills=skills,
        commands=commands,
        hooks=governance_hooks(governed=governed),
        permissions=default_permissions(
            active_profile_id(root, write_cache=write_cache) if governed else "starter"
        ),
        mcp_servers=collect_mcp_servers(root),
        governed=governed,
    )
