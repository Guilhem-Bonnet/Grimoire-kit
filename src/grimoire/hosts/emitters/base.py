"""Shared machinery for host emitters: plan, degrade, write.

An emitter never writes directly. It builds an :class:`EmitPlan` — files, JSON
merges, and the list of things this host cannot do — which the caller can print
(``--dry-run``), diff, or apply. Two properties matter:

*Idempotence.* Re-running a sync must be a no-op when nothing changed, so
``apply`` compares content before writing and reports what it left alone.

*Ownership.* Generated files carry a marker and the fingerprint of what was
emitted; only unmodified marked files are overwritten. A file the user wrote by
hand at a managed path, or edited after generation, is reported as ``skipped``
with its cause, never silently replaced — a host surface the kit rewrites
without asking is a surface nobody dares customise. ``AGENTS.md`` and
``GEMINI.md`` are shared: the kit owns one delimited block, the rest is the user's.

*Transaction.* Every decision is taken before the first byte is written, and the
writes are all-or-nothing: a failure part-way restores the tree.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.bridges.schemas import HostId
from grimoire.core.fsio import atomic_write
from grimoire.hosts.surface import AgentSpec, ProjectSurface, ToolVerb

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
    #: Fichier que l'utilisateur partage avec le kit : le kit n'en possède qu'un
    #: bloc délimité, le reste est préservé à l'octet près.
    block: bool = False


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
    #: Motifs (relatifs à la racine) des fichiers générés par ce plan dont la
    #: disparition de la source doit retirer le fichier : un fichier marqué qui
    #: correspond à un motif sans être dans ``files`` est périmé (règle retirée,
    #: portée renommée) et ``apply_plan`` le supprime. Jamais un fichier sans marqueur.
    owned_globs: tuple[str, ...] = ()

    def labels(self) -> list[str]:
        return [f.relpath.as_posix() for f in self.files] + [m.relpath.as_posix() for m in self.merges]


@dataclass
class EmitResult:
    host_id: HostId
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: Cause de chaque entrée de ``skipped`` : ``edited`` (fichier du kit modifié
    #: depuis son émission), ``foreign`` (jamais écrit par le kit) ou ``unreadable``.
    conflicts: dict[str, str] = field(default_factory=dict)
    removed: list[str] = field(default_factory=list)
    #: Fichiers que le projet a déclarés siens (``hosts.preserve``) : jamais écrits, jamais en conflit.
    preserved: list[str] = field(default_factory=list)
    degradations: list[Degradation] = field(default_factory=list)
    dry_run: bool = False

    def skip(self, label: str, cause: str) -> None:
        self.skipped.append(label)
        self.conflicts[label] = cause

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
            "conflicts": dict(self.conflicts),
            "removed": list(self.removed),
            "preserved": list(self.preserved),
            "degradations": [d.to_dict() for d in self.degradations],
        }


#: Empreinte posée par le kit à côté du marqueur : ``grimoire:managed sha256=<16 hex>``.
#: Elle couvre le fichier tel qu'il a été émis, empreinte retirée. Un fichier dont
#: le corps ne correspond plus a été édité à la main : c'est un conflit, pas un
#: fichier à réécrire. Un marqueur sans empreinte vient de la version précédente
#: du kit (avant D-05), qui n'en posait pas : il reste reconnu comme sien et la
#: première synchronisation le migre sans conflit.
_STAMP = re.compile(re.escape(MANAGED_MARKER) + r" sha256=([0-9a-f]{16})")

#: Bloc géré d'un fichier que l'utilisateur partage avec le kit (``AGENTS.md``,
#: ``GEMINI.md``) : tout ce qui est hors des bornes lui appartient.
BLOCK_BEGIN = "<!-- grimoire:begin sha256={digest} -->"
BLOCK_END = "<!-- grimoire:end -->"
#: Les fins de ligne ``\r\n`` sont acceptées : un fichier passé en CRLF par l'éditeur ou par git garde un bloc reconnu.
_BLOCK = re.compile(
    r"^<!-- grimoire:begin sha256=([0-9a-f]{16}) -->\r?\n(.*?)\r?\n<!-- grimoire:end -->(?:\r?\n|\Z)",
    re.DOTALL | re.MULTILINE,
)
#: Toute ligne qui ouvre un bloc, qu'il soit bien formé ou non : un ``BEGIN`` sans ``END`` exact est un bloc abîmé.
_BEGIN_LINE = re.compile(r"^<!-- grimoire:begin\b", re.MULTILINE)

#: Causes d'un conflit, telles que ``host status`` les expose.
CONFLICT_EDITED = "edited"  # fichier du kit modifié depuis son émission
CONFLICT_FOREIGN = "foreign"  # fichier que le kit n'a jamais écrit
CONFLICT_UNREADABLE = "unreadable"  # illisible ou JSON inexploitable : préservé


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _unstamped(text: str) -> str:
    return _STAMP.sub(MANAGED_MARKER, text, count=1)


def stamp(content: str) -> str:
    """*content* avec l'empreinte de son corps posée dans le premier marqueur ; inchangé sans marqueur."""
    bare = _unstamped(content)
    if MANAGED_MARKER not in bare:
        return content
    return bare.replace(MANAGED_MARKER, f"{MANAGED_MARKER} sha256={_digest(bare)}", 1)


def render_block(content: str) -> str:
    """Bloc délimité et signé qui porte *content* dans un fichier partagé avec l'utilisateur."""
    body = content.rstrip("\n")
    return f"{BLOCK_BEGIN.format(digest=_digest(body))}\n{body}\n{BLOCK_END}\n"


def _target(path: Path) -> Path:
    """Fichier réellement écrit pour *path* : la cible d'un lien symbolique, sinon *path*.

    Écrire « à travers » le lien garde le lien en place (``GEMINI.md`` -> ``AGENTS.md``,
    ``settings.json`` -> un dépôt de dotfiles) ; remplacer le lien par un fichier le casserait.
    """
    if not path.is_symlink():
        return path
    try:
        return path.resolve()
    except (OSError, RuntimeError):  # boucle de liens : on laisse l'écriture signaler l'erreur
        return path


def _read(path: Path, overlay: dict[Path, str], *, raw: bool = False) -> str | None:
    """Contenu courant de *path* (en tenant compte des écritures déjà décidées), ``None`` s'il n'existe pas.

    *raw* garde les fins de ligne telles quelles (fichiers à bloc : le contenu de l'utilisateur
    se préserve à l'octet près) ; sinon elles sont normalisées en LF.

    Lève ``OSError`` / ``ValueError`` pour un fichier présent mais illisible : l'appelant le préserve.
    """
    if path in overlay:
        return overlay[path]
    if not path.is_file():
        return None
    if raw:
        with path.open(encoding="utf-8", newline="") as fh:
            return fh.read()
    return path.read_text(encoding="utf-8")


def _block_body_digest(body: str) -> str:
    """Empreinte du corps d'un bloc, indépendante du style de fin de ligne du fichier."""
    return _digest(body.replace("\r\n", "\n"))


def _is_legacy_whole_file(text: str) -> bool:
    """Fichier entier de la version précédente : le marqueur ouvre le fichier et aucun bloc n'y figure."""
    if _BEGIN_LINE.search(text) is not None:
        return False
    first = next((line for line in text.splitlines() if line.strip()), "")
    return MANAGED_MARKER in first


def _file_ownership(text: str, emitted: EmittedFile) -> str | None:
    """``None`` quand le kit peut réécrire le fichier, sinon la cause du conflit."""
    head = text[:4096]
    if emitted.managed:
        if MANAGED_MARKER not in head:
            return CONFLICT_FOREIGN
        found = _STAMP.search(text)
        if found is None:
            return None  # version précédente : reconnu par le marqueur seul
        return None if _digest(_unstamped(text)) == found.group(1) else CONFLICT_EDITED
    if not emitted.owned_if_contains or any(marker in head for marker in emitted.owned_if_contains):
        return None
    return CONFLICT_FOREIGN


def _block_ownership(text: str) -> str | None:
    """Même question pour un fichier à bloc géré ; seul le contenu *du bloc* compte."""
    found = _BLOCK.search(text)
    if found is not None:
        return None if _block_body_digest(found.group(2)) == found.group(1) else CONFLICT_EDITED
    if _BEGIN_LINE.search(text) is not None:
        return CONFLICT_EDITED  # un BEGIN sans END exact : bloc abîmé, rien de ce qui suit n'est au kit
    return None if _is_legacy_whole_file(text) else CONFLICT_FOREIGN


def _kit_owns(path: Path, emitted: EmittedFile) -> bool:
    """Le kit a-t-il écrit, sans que personne y touche, le fichier déjà en place ?"""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    if not emitted.block:
        return _file_ownership(text, emitted) is None
    # Un fichier à bloc n'est « au kit » que s'il n'y a rien d'autre que le bloc : on ne le supprime jamais sinon.
    found = _BLOCK.fullmatch(text)
    if found is not None:
        return _block_body_digest(found.group(2)) == found.group(1)
    return _is_legacy_whole_file(text)


def owned_managed_paths(plan: EmitPlan, project_root: Path) -> list[Path]:
    """Fichiers de *plan* qui existent sur disque ET que le kit reconnaît comme siens.

    Utilisé par ``grimoire host status``/``sync --prune-disabled`` (issue
    #177) pour repérer les fichiers orphelins d'un hôte désactivé. Ne couvre
    que ``plan.files`` (fichiers entiers marqués ou reconnus par contenu) —
    jamais ``plan.merges`` (JSON fusionné, ex. ``settings.json``), qui porte
    aussi la configuration propre du projet et n'est jamais supprimé
    automatiquement. Un fichier édité à la main, ou un fichier à bloc qui porte
    du contenu d'utilisateur, n'est pas au kit : on ne le propose pas à la suppression.
    """
    root = project_root.resolve()
    owned: list[Path] = []
    for emitted in plan.files:
        dest = root / emitted.relpath
        if dest.is_file() and _kit_owns(dest, emitted):
            owned.append(dest)
    return owned


def _decide_block(current: str | None, emitted: EmittedFile, force: bool) -> tuple[str | None, str]:
    """``(nouveau contenu, verdict)`` pour un fichier à bloc ; verdict : written, unchanged ou une cause de conflit."""
    block = render_block(emitted.content)
    if current is None:
        return block, "written"
    cause = _block_ownership(current)
    if cause is not None and not force:
        return None, cause
    nl = "\r\n" if "\r\n" in current else "\n"
    if nl != "\n":  # le bloc suit le style du fichier ; ce qui l'entoure n'est jamais retouché
        block = block.replace("\n", nl)
    found = _BLOCK.search(current)
    if found is not None:  # le bloc seul est remplacé : le reste est à l'utilisateur, même avec --force
        new = current[: found.start()] + block + current[found.end() :]
    elif _is_legacy_whole_file(current):  # version précédente : tout le fichier était au kit
        new = block
    elif _BEGIN_LINE.search(current) is not None:  # bloc abîmé : même --force ne devine pas où il finit
        return None, CONFLICT_EDITED
    else:  # fichier de l'utilisateur, --force : on ajoute le bloc sans rien retirer
        tail = current.rstrip("\r\n")
        new = f"{tail}{nl}{nl}{block}" if tail else block
    return new, "unchanged" if new == current else "written"


def _decide_files(
    plan: EmitPlan,
    root: Path,
    result: EmitResult,
    overlay: dict[Path, str],
    writes: list[tuple[Path, str]],
    force: bool,
    preserved: frozenset[str] = frozenset(),
) -> None:
    for emitted in plan.files:
        dest = _target(root / emitted.relpath)
        # POSIX form on purpose: a label is an identifier reported to the user
        # and asserted in tests, so it must not change shape with the host OS.
        label = emitted.relpath.as_posix()
        if label in preserved:
            result.preserved.append(label)
            continue
        try:
            current = _read(dest, overlay, raw=emitted.block)
        except (OSError, ValueError):
            result.skip(label, CONFLICT_UNREADABLE)
            continue
        if emitted.block:
            new, verdict = _decide_block(current, emitted, force)
        else:
            new, verdict = stamp(emitted.content), "written"
            if current == new:
                verdict = "unchanged"
            elif current is not None and not force:
                cause = _file_ownership(current, emitted)
                if cause is not None:
                    new, verdict = None, cause
        if new is None:
            result.skip(label, verdict)
        elif verdict == "unchanged":
            result.unchanged.append(label)
        else:
            overlay[dest] = new
            writes.append((dest, new))
            result.written.append(label)


def _decide_merges(
    plan: EmitPlan,
    root: Path,
    result: EmitResult,
    overlay: dict[Path, str],
    writes: list[tuple[Path, str]],
    preserved: frozenset[str] = frozenset(),
) -> None:
    for merge in plan.merges:
        dest = _target(root / merge.relpath)
        label = merge.label or merge.relpath.as_posix()
        if merge.relpath.as_posix() in preserved:
            result.preserved.append(merge.relpath.as_posix())
            continue
        try:
            text = _read(dest, overlay)
            loaded = json.loads(text) if text is not None else {}
        except (OSError, ValueError):
            result.skip(f"{label} (JSON illisible — fichier préservé)", CONFLICT_UNREADABLE)
            continue
        if not isinstance(loaded, dict):
            result.skip(f"{label} (racine JSON inattendue — fichier préservé)", CONFLICT_UNREADABLE)
            continue
        merged = merge.merge(json.loads(json.dumps(loaded)))
        rendered = json.dumps(merged, indent=2, ensure_ascii=False) + "\n"
        if text is not None and text == rendered:
            result.unchanged.append(label)
            continue
        overlay[dest] = rendered
        writes.append((dest, rendered))
        result.written.append(label)


def _is_managed(path: Path) -> bool:
    try:
        head = path.read_text(encoding="utf-8")[:4096]
    except (OSError, ValueError):
        return False
    return MANAGED_MARKER in head


def _stale_files(plan: EmitPlan, root: Path) -> list[Path]:
    """Fichiers marqués que ``plan.owned_globs`` désigne et que le plan n'émet plus (jamais un fichier sans marqueur)."""
    planned = {f.relpath.as_posix() for f in plan.files}
    stale: list[Path] = []
    for pattern in plan.owned_globs:
        for path in sorted(root.glob(pattern)):
            label = path.relative_to(root).as_posix()
            if label in planned or path.is_symlink() or not path.is_file() or not _is_managed(path):
                continue
            stale.append(path)
    return stale


def _missing_dirs(parent: Path) -> list[Path]:
    """Dossiers à créer pour que *parent* existe, du plus profond au plus proche de la racine."""
    missing: list[Path] = []
    while not parent.exists() and parent != parent.parent:
        missing.append(parent)
        parent = parent.parent
    return missing


def _rollback(originals: dict[Path, bytes | None], made_dirs: list[Path], failure: BaseException) -> None:
    """Remet l'arbre dans son état initial ; ce qui ne se laisse pas défaire s'attache à *failure*."""
    for dest, original in originals.items():
        try:
            if original is None:
                dest.unlink(missing_ok=True)
            else:
                atomic_write(dest, original)
        except OSError as exc:
            failure.add_note(f"retour arrière de {dest} impossible ({exc}) : à vérifier à la main")
    for directory in sorted(set(made_dirs), key=lambda d: len(d.parts), reverse=True):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            failure.add_note(f"dossier {directory} non retiré ({exc})")


def _commit(writes: list[tuple[Path, str]]) -> None:
    """Applique toutes les écritures, ou aucune : la première panne défait ce qui a été fait, puis se propage."""
    originals: dict[Path, bytes | None] = {}
    made_dirs: list[Path] = []
    try:
        for dest, text in writes:
            original = dest.read_bytes() if dest.is_file() else None
            missing = _missing_dirs(dest.parent)
            made_dirs.extend(missing)
            if missing:
                dest.parent.mkdir(parents=True, exist_ok=True)
            originals.setdefault(dest, original)
            atomic_write(dest, text)
    except BaseException as exc:
        _rollback(originals, made_dirs, exc)
        raise


def apply_plans(
    plans: Sequence[EmitPlan],
    project_root: Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    preserve: frozenset[str] | None = None,
) -> list[EmitResult]:
    """Write every plan into *project_root* as one transaction, leaving hand-written files alone.

    Deux temps. D'abord on décide tout sans rien écrire (un plan voit les
    écritures déjà décidées par les précédents) ; puis on écrit, fichier par
    fichier de façon atomique. Une panne en cours de route — au troisième
    fichier, dans le dernier hôte — rétablit l'arbre tel qu'il était, hôtes
    précédents compris, puis se propage.

    Limite : la transaction vit en mémoire. Un processus tué en pleine écriture
    laisse les fichiers déjà remplacés ; chacun est entier (écriture atomique),
    mais l'ensemble n'est pas rétabli.

    *preserve* : fichiers que le projet garde pour lui (``hosts.preserve``), jamais touchés, y
    compris par ``force``. Omis, il vient de la config du projet — un seul écrivain, une seule règle.
    """
    from grimoire.hosts.detection import preserved_paths

    root = project_root.resolve()
    kept = preserve if preserve is not None else preserved_paths(root)
    overlay: dict[Path, str] = {}
    writes: list[tuple[Path, str]] = []
    stale: list[Path] = []
    results: list[EmitResult] = []
    for plan in plans:
        result = EmitResult(host_id=plan.host_id, degradations=list(plan.degradations), dry_run=dry_run)
        _decide_files(plan, root, result, overlay, writes, force, kept)
        _decide_merges(plan, root, result, overlay, writes, kept)
        for path in _stale_files(plan, root):
            if path.relative_to(root).as_posix() in kept:
                continue
            result.removed.append(path.relative_to(root).as_posix())
            stale.append(path)
        results.append(result)
    if not dry_run:
        _commit(writes)
        for path in stale:
            path.unlink(missing_ok=True)
    return results


def apply_plan(plan: EmitPlan, project_root: Path, *, dry_run: bool = False, force: bool = False) -> EmitResult:
    """Write *plan* into *project_root*, leaving hand-written files alone (see :func:`apply_plans`)."""
    return apply_plans([plan], project_root, dry_run=dry_run, force=force)[0]


def conflict_path(label: str) -> str:
    """Chemin d'un libellé de conflit (``.claude/settings.json (JSON illisible — …)`` -> ``.claude/settings.json``)."""
    return label.partition(" (")[0]


def conflict_exits(labels: list[str] | tuple[str, ...]) -> str:
    """Les deux issues d'un conflit, dites ensemble : l'une garde le fichier de l'utilisateur, l'autre l'écrase."""
    paths = ", ".join(f'"{conflict_path(label)}"' for label in labels)
    return (
        f"issues : garder le fichier (non destructif, durable) en le déclarant dans project-context.yaml "
        f"(`hosts: {{preserve: [{paths}]}}`), ou laisser le kit l'écraser avec `grimoire host sync --force`"
    )


def host_conflict_detail(plan: EmitPlan, project_root: Path, alias: str) -> str:
    """Fichiers de *plan* que le kit préserve sans que le projet l'ait déclaré ; vide s'il n'y en a pas (lu à blanc)."""
    skipped = apply_plan(plan, project_root, dry_run=True).skipped
    if not skipped:
        return ""
    return f"Hôte {alias} : {', '.join(skipped)} préservé(s), non synchronisé(s) — {conflict_exits(skipped)}"


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
            elif isinstance(value, int):
                # Order matters: bool is an int subclass in Python, so the
                # bool branch above must run first or True/False would land
                # here as 1/0.
                lines.append(f"{key}: {value}")
            elif isinstance(value, (list, tuple)):
                rendered = ", ".join(f"'{v!s}'" for v in value)
                lines.append(f"{key}: [{rendered}]")
            else:
                text = str(value).replace("'", "''")
                lines.append(f"{key}: '{text}'")
        lines.append("---")
        return "\n".join(lines)


#: Étape par défaut, envoyée aux agents qui ne déclarent rien — texte inchangé
#: depuis avant #379, pour ne rien amputer d'un agent qui n'a rien demandé.
DEFAULT_CONTEXT_INSTRUCTION = "Lis `_grimoire/_memory/shared-context.md` s'il existe, pour l'état courant du projet."


def context_load_instruction(agent: AgentSpec) -> str:
    """L'étape « charge ton contexte » émise dans le fichier d'agent.

    Un agent qui déclare ``context:`` charge ces chemins-là, et eux seuls —
    plus le contexte partagé par défaut qu'il n'a pas demandé. Un agent qui ne
    déclare rien reçoit exactement :data:`DEFAULT_CONTEXT_INSTRUCTION`, à
    l'identique d'avant #379 : la déclaration est un rétrécissement
    volontaire, jamais une amputation par défaut (issue #379, critère 2).
    """
    if not agent.context:
        return DEFAULT_CONTEXT_INSTRUCTION
    if len(agent.context) == 1:
        return f"Lis `{agent.context[0]}` s'il existe : c'est le seul contexte que tu déclares."
    paths = ", ".join(f"`{p}`" for p in agent.context)
    return f"Lis ton contexte déclaré, et lui seul, s'il existe : {paths}."


def map_verbs(verbs: tuple[ToolVerb, ...], table: dict[ToolVerb, tuple[str, ...]]) -> tuple[str, ...]:
    """Expand neutral verbs into a host's tool names, order-preserving."""
    names: list[str] = []
    for verb in verbs:
        for name in table.get(verb, ()):
            if name not in names:
                names.append(name)
    return tuple(names)


def parallel_brainstorm_section(per_angle_clause: str) -> str:
    """Sous-section « Quand déléguer en parallèle » (#687), commune aux émetteurs.

    Le texte vit ici, une seule fois : Claude Code l'injecte aussi à chaque
    ``SessionStart`` (injecté à chaque session, donc court). *per_angle_clause*
    est la seule partie propre à l'hôte — comment l'angle est calibré —, pour que
    la variante Copilot ne nomme aucun modèle.
    """
    return f"""### Quand déléguer en parallèle (brainstorm)

Sans qu'on te le demande, lance 2 à 4 sous-agents indépendants dans UN SEUL message quand la demande :
- est ouverte (« qu'est-ce que tu proposes », « comment aborder », « quelles options ») ;
- porte un arbitrage d'architecture, ou un choix coûteux à défaire ;
- couvre plusieurs axes indépendants, ou une recherche sur plusieurs sources.
Ne le fais PAS pour une correction ciblée, une tâche mécanique (V0), une question factuelle,
ni quand l'utilisateur a déjà désigné le fichier ou l'action.
Annonce en une ligne « Je brainstorme sur N angles : … » avant de lancer ; 4 angles maximum, un seul tour.
Un angle = un brief autonome (pas le contexte des autres angles), {per_angle_clause},
bloc grimoire-uncertainties exigé. Fais toi-même la synthèse : accords, désaccords, coût de chaque
option ; ne moyenne pas. Relance un second tour uniquement sur les désaccords réels.
Si tu hésites entre déléguer et agir seul, agis seul.
"""
