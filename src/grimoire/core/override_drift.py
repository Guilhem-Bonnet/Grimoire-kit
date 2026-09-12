"""Detect and describe agent overrides the kit has moved past (issue #427).

An override is the sanctioned way to customise an agent. Before this module
existed, it was also the surest way to stop receiving that agent's kit
updates: a full copy shadows the kit file forever, and nothing ever compared
it back against what the kit now ships. Four overrides from a real project's
3.38.0 -> 3.44.2 migration proved it — ``doctor`` was 22/22 while all four
silently ran on six-version-old personas.

This module answers one question for every agent override on disk: has the
kit file it was taken from moved since? It never edits anything — that is
what ``grimoire agent override convert`` (:mod:`grimoire.cli.cmd_agent`) and
the operator's own judgment are for.

``kit_source_hash``
    A truncated SHA-256 of the kit file's bytes, recorded in an override's own
    frontmatter at write time (or ``"none"`` when no kit agent of that name
    existed yet). Comparing it against the kit file's *current* digest is
    deliberately independent of whether the override's own text was hand
    edited afterwards — that is normal customisation, not drift. Only a
    project's own writers stamp this key (:mod:`grimoire.tools.workspace_routes`,
    ``grimoire agent override convert``); the generic
    ``/api/workspace/file/override`` copy (any kit file, not just agents) does
    not, because its contract is a byte-identical copy the moment it is taken
    (``tests/unit/test_workspace_api.py::test_prendre_un_override_rend_le_fichier_comparable_puis_editable``)
    — such an override simply reports ``"unknown"`` here forever, same as any
    override written before this issue.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grimoire.core import layout
from grimoire.core.exceptions import GrimoireAgentError

if TYPE_CHECKING:
    from grimoire.hosts.surface import AgentSpec

__all__ = [
    "KIT_SOURCE_HASH_KEY",
    "OverrideConversionRefusedError",
    "OverrideDrift",
    "compute_kit_source_hash",
    "convert_override",
    "describe_drift",
    "project_override_drift",
]

#: Frontmatter key an override records its kit source's digest under.
KIT_SOURCE_HASH_KEY = "kit_source_hash"

#: Hex characters kept from the SHA-256 digest — enough to make a collision
#: between two different kit file revisions practically impossible for a
#: drift signal (not a security boundary), short enough to read in a diff.
_HASH_LENGTH = 16

#: Value recorded when the agent had no kit counterpart at write time.
NO_KIT_SOURCE = "none"

#: Value recorded (never written, only compared against) when the override
#: predates this issue and carries no ``kit_source_hash`` at all.
_UNKNOWN = "unknown"


def compute_kit_source_hash(kit_path: Path) -> str:
    """Truncated SHA-256 of *kit_path*'s current bytes, or :data:`NO_KIT_SOURCE`."""
    try:
        digest = hashlib.sha256(kit_path.read_bytes()).hexdigest()
    except OSError:
        return NO_KIT_SOURCE
    return digest[:_HASH_LENGTH]


@dataclass(frozen=True, slots=True)
class OverrideDrift:
    """What :func:`project_override_drift` found for one overridden agent."""

    name: str
    override_ref: str
    override_kind: str
    """``"full"`` or ``"partial"`` — see ``AgentSpec.override_kind``."""
    kit_ref: str
    """Project-relative path of the kit counterpart, real or not (yet)."""
    recorded_hash: str
    """``kit_source_hash`` read from the override, or :data:`_UNKNOWN`."""
    current_hash: str
    """:func:`compute_kit_source_hash` of the kit file right now."""
    status: str
    """``"fresh"`` (matches), ``"stale"`` (kit moved), or ``"unknown"``
    (the override predates ``kit_source_hash``)."""
    added_sections: tuple[str, ...] = ()
    """Frontmatter keys the kit has gained since the override was taken —
    only computed for a ``"full"`` override, whose frontmatter is a literal
    snapshot of the kit's at write time."""
    removed_sections: tuple[str, ...] = ()
    """Frontmatter keys the kit has dropped since — same restriction."""
    body_line_delta: int = 0
    """``len(current kit body lines) - len(override body lines)`` — 0 for a
    partial override (it carries no body to compare)."""
    frozen_fields: tuple[str, ...] = field(default_factory=tuple)
    """For a ``"partial"`` override: the fields it pins, which the kit's own
    change on those specific fields (if any) will never reach."""


def _agent_files_dir(project_root: Path) -> Path:
    return layout.overrides_dir(project_root) / layout.AGENTS_SUBDIR


def _drift_for(project_root: Path, agent: AgentSpec) -> OverrideDrift | None:
    """Build the :class:`OverrideDrift` for one collected agent, or ``None``."""
    from grimoire.hosts.collect import PARTIAL_OVERRIDE_FIELDS, parse_frontmatter

    if agent.override_ref is None:
        return None
    root = project_root.resolve()
    override_path = root / agent.override_ref
    try:
        override_text = override_path.read_text(encoding="utf-8")
    except OSError:
        return None
    override_meta, override_body = parse_frontmatter(override_text)
    kit_path = layout.kit_dir(root) / layout.AGENTS_SUBDIR / override_path.name
    try:
        kit_ref = kit_path.relative_to(root).as_posix()
    except ValueError:
        kit_ref = kit_path.as_posix()
    current_hash = compute_kit_source_hash(kit_path)
    recorded_raw = override_meta.get(KIT_SOURCE_HASH_KEY)
    recorded_hash = str(recorded_raw).strip() if recorded_raw not in (None, "") else _UNKNOWN

    if recorded_hash == _UNKNOWN:
        status = "unknown"
    elif recorded_hash == current_hash:
        status = "fresh"
    else:
        status = "stale"

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    body_delta = 0
    frozen: tuple[str, ...] = ()
    if status == "stale":
        if agent.override_kind == "full" and kit_path.is_file():
            kit_text = kit_path.read_text(encoding="utf-8")
            kit_meta, kit_body = parse_frontmatter(kit_text)
            # `KIT_SOURCE_HASH_KEY` est une clé que *nous* ajoutons à la copie
            # au moment de l'override, jamais présente côté kit — l'exclure
            # évite un faux positif « section retirée » permanent qui ne dit
            # rien du contenu réel du kit.
            added = tuple(sorted(set(kit_meta) - set(override_meta) - {KIT_SOURCE_HASH_KEY}))
            removed = tuple(sorted(set(override_meta) - set(kit_meta) - {KIT_SOURCE_HASH_KEY}))
            body_delta = len(kit_body.splitlines()) - len(override_body.splitlines())
        elif agent.override_kind == "partial":
            frozen = tuple(sorted(k for k in PARTIAL_OVERRIDE_FIELDS if k in override_meta))

    return OverrideDrift(
        name=agent.name,
        override_ref=agent.override_ref,
        override_kind=agent.override_kind or "full",
        kit_ref=kit_ref,
        recorded_hash=recorded_hash,
        current_hash=current_hash,
        status=status,
        added_sections=added,
        removed_sections=removed,
        body_line_delta=body_delta,
        frozen_fields=frozen,
    )


def project_override_drift(project_root: Path) -> list[OverrideDrift]:
    """Every agent override's drift status, in :func:`collect_agents` order.

    Never raises on a broken project: a project whose agents fail to collect
    (unknown skill, missing context…) yields an empty list, the same
    tolerance :mod:`grimoire.core.agent_freshness` has for the same failure —
    this check only ever adds a WARN/INFO line to `doctor`, it never blocks
    it, and `doctor`'s own kit-integrity checks already name that failure.
    """
    from grimoire.hosts import collect

    root = project_root.resolve()
    try:
        skills = collect.collect_skills(root)
        agents = collect.collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    except GrimoireAgentError:
        return []
    drifts = [_drift_for(root, agent) for agent in agents]
    return [d for d in drifts if d is not None]


def describe_drift(drift: OverrideDrift) -> tuple[str, str]:
    """``(level, detail)`` for `doctor`/the cockpit — never ``"fail"``.

    *level* is ``"warn"`` for a stale override (the kit moved) or ``"info"``
    for an override whose empreinte is simply unknown (predates this issue) —
    exactly the two levels the issue's stopping criterion names, in that
    wording, so a project's own tooling (and this PR's tests) can match on
    the literal text.
    """
    if drift.status == "unknown":
        return "info", (
            f"Override « {drift.name} » ({drift.override_ref}) : empreinte inconnue, "
            "revoir à la main."
        )
    if drift.status == "fresh":
        return "info", f"Override « {drift.name} » ({drift.override_ref}) : à jour avec le kit."

    if drift.override_kind == "partial":
        frozen = ", ".join(drift.frozen_fields) or "aucun"
        return "warn", (
            f"Override « {drift.name} » ({drift.override_ref}) : le kit "
            f"({drift.kit_ref}) a changé depuis — champs figés par l'override : {frozen}. "
            "Le corps et les autres champs suivent déjà le kit."
        )

    sections = []
    if drift.added_sections:
        sections.append(f"section(s) ajoutée(s) côté kit : {', '.join(drift.added_sections)}")
    if drift.removed_sections:
        sections.append(f"section(s) retirée(s) côté kit : {', '.join(drift.removed_sections)}")
    sections.append(f"corps : Δ{drift.body_line_delta:+d} ligne(s)")
    return "warn", (
        f"Override « {drift.name} » ({drift.override_ref}) obsolète : le kit "
        f"({drift.kit_ref}) a changé depuis sa copie — {'; '.join(sections)}. "
        f"Choix : conserver, `grimoire agent override convert {drift.name}`, ou retirer."
    )


class OverrideConversionRefusedError(GrimoireAgentError):
    """A named refusal to convert an override — never a silent no-op."""


def _dump_frontmatter(data: dict[str, Any], body: str) -> str:
    import io

    from ruamel.yaml import YAML

    yaml = YAML()
    yaml.default_flow_style = False
    buf = io.StringIO()
    yaml.dump(data, buf)
    return f"---\n{buf.getvalue()}---\n{body}"


@dataclass(frozen=True, slots=True)
class ConvertResult:
    """What :func:`convert_override` produced, written or not."""

    override_ref: str
    fields: tuple[str, ...]
    rendered: str
    written: bool


def convert_override(project_root: Path, name: str, *, dry_run: bool = False) -> ConvertResult:
    """Turn a full-copy override of *name* into the equivalent partial one.

    Refuses (:class:`OverrideConversionRefusedError`) rather than merging text
    when the override's body no longer matches the kit's — that is a real
    customisation of the persona itself, and this ticket refuses to guess at
    merging prose (see the issue's own "ce que ce ticket refuse"). Only the
    frontmatter fields :data:`grimoire.hosts.collect.PARTIAL_OVERRIDE_FIELDS`
    lists are ever carried into the resulting partial override.
    """
    from grimoire.hosts.collect import PARTIAL_OVERRIDE_FIELDS, parse_frontmatter

    root = project_root.resolve()
    override_path = _agent_files_dir(root) / f"{name}.md"
    if not override_path.is_file():
        raise FileNotFoundError(f"aucun override pour « {name} » — rien à convertir : {override_path}")
    override_text = override_path.read_text(encoding="utf-8")
    override_meta, override_body = parse_frontmatter(override_text)
    if str(override_meta.get("extends", "")).strip().lower() == "kit":
        raise OverrideConversionRefusedError(
            f"« {name} » est déjà un override partiel (`extends: kit`) — rien à convertir."
        )

    kit_path = layout.kit_dir(root) / layout.AGENTS_SUBDIR / f"{name}.md"
    if not kit_path.is_file():
        raise OverrideConversionRefusedError(
            f"« {name} » n'a pas de contrepartie kit ({kit_path}) — rien vers quoi convertir."
        )
    kit_text = kit_path.read_text(encoding="utf-8")
    kit_meta, kit_body = parse_frontmatter(kit_text)

    if override_body.strip() != kit_body.strip():
        import difflib

        diff = list(
            difflib.unified_diff(
                kit_body.splitlines(), override_body.splitlines(), fromfile="kit", tofile="override", lineterm=""
            )
        )[:40]
        raise OverrideConversionRefusedError(
            f"« {name} » : le corps de l'override diffère de celui du kit — "
            "convertir fusionnerait du texte, ce que ce ticket refuse. "
            f"Lignes qui diffèrent :\n" + "\n".join(diff)
        )

    diff_fields = tuple(
        sorted(k for k in PARTIAL_OVERRIDE_FIELDS if override_meta.get(k) != kit_meta.get(k))
    )
    new_meta: dict[str, Any] = {"extends": "kit"}
    for key in diff_fields:
        new_meta[key] = override_meta[key]
    new_meta[KIT_SOURCE_HASH_KEY] = compute_kit_source_hash(kit_path)
    rendered = _dump_frontmatter(new_meta, "")

    if dry_run:
        return ConvertResult(override_ref=str(override_path), fields=diff_fields, rendered=rendered, written=False)

    original = override_text
    override_path.write_text(rendered, encoding="utf-8")
    try:
        from grimoire.hosts import collect

        skills = collect.collect_skills(root)
        collect.collect_agents(root, known_skills=frozenset(s.slug for s in skills))
    except GrimoireAgentError:
        override_path.write_text(original, encoding="utf-8")
        raise
    return ConvertResult(override_ref=str(override_path), fields=diff_fields, rendered=rendered, written=True)
