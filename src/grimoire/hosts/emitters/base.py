"""Shared machinery for host emitters: plan, degrade, write.

An emitter never writes directly. It builds an :class:`EmitPlan` — files, JSON
merges, and the list of things this host cannot do — which the caller can print
(``--dry-run``), diff, or apply. Two properties matter:

*Idempotence.* Re-running a sync must be a no-op when nothing changed, so
``apply`` compares content before writing and reports what it left alone.

*Ownership.* Generated files carry a marker; only marked files are overwritten.
A file the user wrote by hand at a managed path is reported as ``skipped``,
never silently replaced — a host surface the kit rewrites without asking is a
surface nobody dares customise.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.hosts.surface import ProjectSurface, ToolVerb

#: Marker identifying a kit-generated artifact. Present in every managed file.
MANAGED_MARKER = "grimoire:managed"

#: Un JSON n'a pas de commentaires : un fichier de hook ne peut pas porter
#: ``MANAGED_MARKER``. C'est alors ce qu'il invoque qui dit à qui il
#: appartient. Toute commande contenant l'un de ces fragments a été écrite par
#: le kit et peut être réécrite ; tout le reste est la chaîne du projet et se
#: préserve. Les invocations dépassées restent dans la liste : les oublier ne
#: serait pas cosmétique — la reconnaissance cesserait, le fichier passerait
#: pour étranger, et le sync le préserverait au lieu de le migrer.
OWNED_COMMAND_MARKERS: tuple[str, ...] = (
    "grimoire-hook",
    "grimoire host hook",
    "grimoire standard activation-context",
)

_MARKER_COMMENTS = {
    ".md": f"<!-- {MANAGED_MARKER} — régénéré par `grimoire host sync`; éditez la source, pas ce fichier. -->",
}


def managed_header(suffix: str) -> str:
    return _MARKER_COMMENTS.get(suffix, f"# {MANAGED_MARKER}")


@dataclass(frozen=True, slots=True)
class Degradation:
    """A surface this host cannot execute, and what was done instead."""

    surface: str
    reason: str
    fallback: str

    def to_dict(self) -> dict[str, str]:
        return {"surface": self.surface, "reason": self.reason, "fallback": self.fallback}


@dataclass(frozen=True, slots=True)
class EmittedFile:
    """One file to write, relative to the project root."""

    relpath: Path
    content: str
    managed: bool = True
    #: Pour un fichier qui ne peut pas porter de marqueur : fragments dont la
    #: présence dans le fichier existant prouve que le kit l'a écrit. Vide,
    #: l'ancien comportement est conservé — le fichier est réécrit sans test.
    owned_if_contains: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class JsonMerge:
    """A surgical merge into a JSON file the project also owns.

    ``settings.json`` and ``.mcp.json`` carry the user's configuration next to
    ours. Rewriting them wholesale would delete it, so an emitter describes the
    merge as a function and the file keeps everything it is not about.
    """

    relpath: Path
    merge: Callable[[dict[str, Any]], dict[str, Any]]
    label: str = ""


@dataclass(frozen=True, slots=True)
class EmitPlan:
    host_id: HostId
    files: tuple[EmittedFile, ...] = ()
    merges: tuple[JsonMerge, ...] = ()
    degradations: tuple[Degradation, ...] = ()

    def labels(self) -> list[str]:
        return [f.relpath.as_posix() for f in self.files] + [m.relpath.as_posix() for m in self.merges]


@dataclass
class EmitResult:
    host_id: HostId
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    degradations: list[Degradation] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.skipped

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host_id.value,
            "dry_run": self.dry_run,
            "written": list(self.written),
            "unchanged": list(self.unchanged),
            "skipped": list(self.skipped),
            "degradations": [d.to_dict() for d in self.degradations],
        }


def _is_managed(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8")[:4096]
    except OSError:
        return False
    return MANAGED_MARKER in head


def _kit_owns(path: Path, emitted: EmittedFile) -> bool:
    """Le kit a-t-il écrit le fichier déjà en place ?

    Deux preuves d'appartenance, selon ce que le format autorise : un marqueur
    en commentaire quand le fichier en accepte un, sinon la commande que le
    fichier invoque.
    """
    if emitted.managed:
        return _is_managed(path)
    if not emitted.owned_if_contains:
        return True
    try:
        head = path.read_text(encoding="utf-8")[:4096]
    except OSError:
        return False
    return any(marker in head for marker in emitted.owned_if_contains)


def apply_plan(plan: EmitPlan, project_root: Path, *, dry_run: bool = False, force: bool = False) -> EmitResult:
    """Write *plan* into *project_root*, leaving hand-written files alone."""
    result = EmitResult(host_id=plan.host_id, degradations=list(plan.degradations), dry_run=dry_run)
    root = project_root.resolve()

    for emitted in plan.files:
        dest = root / emitted.relpath
        # POSIX form on purpose: a label is an identifier reported to the user
        # and asserted in tests, so it must not change shape with the host OS.
        label = emitted.relpath.as_posix()
        if dest.is_file():
            current = dest.read_text(encoding="utf-8")
            if current == emitted.content:
                result.unchanged.append(label)
                continue
            if not force and not _kit_owns(dest, emitted):
                result.skipped.append(label)
                continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(emitted.content, encoding="utf-8")
        result.written.append(label)

    for merge in plan.merges:
        dest = root / merge.relpath
        label = merge.label or merge.relpath.as_posix()
        existing: dict[str, Any] = {}
        if dest.is_file():
            try:
                loaded = json.loads(dest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result.skipped.append(f"{label} (JSON illisible — fichier préservé)")
                continue
            if not isinstance(loaded, dict):
                result.skipped.append(f"{label} (racine JSON inattendue — fichier préservé)")
                continue
            existing = loaded
        merged = merge.merge(json.loads(json.dumps(existing)))
        rendered = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
        if dest.is_file() and dest.read_text(encoding="utf-8") == rendered:
            result.unchanged.append(label)
            continue
        if not dry_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(rendered, encoding="utf-8")
        result.written.append(label)

    return result


class Emitter:
    """Base class: a host id, a profile, and a plan."""

    host_id: HostId

    def plan(self, surface: ProjectSurface, project_root: Path) -> EmitPlan:  # pragma: no cover - interface
        raise NotImplementedError

    # ── helpers shared by concrete emitters ──────────────────────────────

    @staticmethod
    def hook_command(host_alias: str, wire_event: str) -> str:
        """Invocation a generated hook configuration runs.

        A console script rather than a shell snippet keeps the hook portable
        (no ``cat``, no ``$(…)`` on Windows) and versioned with the kit that
        generated it.

        ``grimoire-hook`` rather than ``grimoire host hook``: the second builds
        the entire Typer command tree — importing every ``cmd_*`` module — to
        resolve one subcommand, measured at 391 ms per call against 116 ms for
        the dedicated entry point. A hook runs once per tool call, so that
        difference is paid on every action of every session. The subcommand
        stays available for humans.
        """
        return f"grimoire-hook --host {host_alias} --event {wire_event}"

    @staticmethod
    def frontmatter(fields: dict[str, Any]) -> str:
        lines = ["---"]
        for key, value in fields.items():
            if value in (None, "", [], ()):
                continue
            if isinstance(value, bool):
                lines.append(f"{key}: {'true' if value else 'false'}")
            elif isinstance(value, (list, tuple)):
                rendered = ", ".join(f"'{v!s}'" for v in value)
                lines.append(f"{key}: [{rendered}]")
            else:
                text = str(value).replace("'", "''")
                lines.append(f"{key}: '{text}'")
        lines.append("---")
        return "\n".join(lines)


def map_verbs(verbs: tuple[ToolVerb, ...], table: dict[ToolVerb, tuple[str, ...]]) -> tuple[str, ...]:
    """Expand neutral verbs into a host's tool names, order-preserving."""
    names: list[str] = []
    for verb in verbs:
        for name in table.get(verb, ()):
            if name not in names:
                names.append(name)
    return tuple(names)
