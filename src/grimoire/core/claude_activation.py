"""Claude Code session activation for the governed standard.

The 2026-07-09 evals campaign (evals/reports/2026-07-09/report.md)
validated the mechanism 40/40: a Claude Code ``SessionStart`` hook that
prints an activation directive turns passive standard artifacts into
engaged protocol (task envelope filled, evidence inventory, gates run).
The 2026-07-03 campaign measured the counterfactual: without the hook,
0/40 runs engaged the standard.

``grimoire standard init`` installs the hook by default:

- ``.claude/activation-context.md`` — the directive, editable per
  project (never overwritten if present);
- ``.claude/settings.json`` — a ``SessionStart`` command hook running
  ``grimoire standard activation-context``, which prints the project
  file when present and the built-in directive otherwise. Using the CLI
  as the hook command keeps the mechanism portable (no ``cat`` on
  Windows) and versioned with the kit.

The settings merge is fail-safe: an unreadable or unexpectedly shaped
``settings.json`` is left byte-for-byte untouched.

``.claude/activation-context.md`` is a kit-owned file, tracked the same way
as the other standard artifacts: :mod:`grimoire.core.standard_generation`'s
generation manifest (``_grimoire/standard/.generated.json``) records its
digest, so an install predating that tracking — or a rendering from any past
kit version, see :data:`_HISTORICAL_DIRECTIVE_TEMPLATES` — is recognised as
the kit's and refreshed by ``grimoire up`` instead of being frozen forever
at whatever wording was current on the day the project enrolled (issue
#582, lot G4).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from grimoire.core import standard_generation as gen

ACTIVATION_CONTEXT_RELPATH = Path(".claude") / "activation-context.md"
SETTINGS_RELPATH = Path(".claude") / "settings.json"
HOOK_COMMAND = "grimoire standard activation-context"

#: Issue #582 lot G3 : le banc à trois bras du 2026-09-17
#: (``_scratch/bench-f/analyse-tours-kit-gov.md``, 21 runs) a mesuré que le
#: rituel mandaté en trois étapes (enveloppe, pack, ``gate run-tests`` puis
#: ``gate check --strict`` puis ``standard verify .``) coûtait une médiane de
#: 31 tours par run contre 6 pour Claude nu — l'agent exécute à la main ce
#: que le kit sait déjà faire lui-même : le lot G1 scaffolde les artefacts par
#: tâche au ``SessionStart`` (``standard_task_scaffold.py``) et ``gate check
#: --strict`` lance déjà ``gate run-tests`` pour son compte
#: (``standard_checks/gate_test_run.py::ensure_fresh_test_run``) quand une
#: commande de test est connue. La directive n'a donc plus qu'une seule
#: commande à mandater ; ``gate check --strict`` reste la seule porte de
#: sortie, ``verify`` n'apportant rien sur le chemin d'une tâche que ce gate
#: ne couvre déjà (voir ``check_evidence_gates`` : task-envelope, pack de
#: preuve, claim-ledger, acceptance-record, run de test).
_DIRECTIVE_TEMPLATE = """[Grimoire Standard] La tâche {task_id} est gouvernée : ses artefacts sont déjà en place sous `_grimoire-output/evidence/{task_id}/` et tes actions sont journalisées. Fais le travail demandé. Avant de conclure, exécute `grimoire standard gate check --task-id {task_id} --strict` et corrige ce qu'il rapporte : il lance les tests, lit tes actions et nomme le fichier à compléter s'il en manque un.
"""


#: Raw wordings of every past revision of ``_DIRECTIVE_TEMPLATE`` (``{task_id}``
#: placeholder literal, as written to disk by :func:`install_claude_activation`
#: at that kit version) — oldest first. A project's file matching one of these
#: exactly predates today's wording untouched: the kit's to refresh, not a
#: project edit. Append one entry per revision here; never edit or drop a past
#: one, or projects enrolled at that version stop being recognised.
#:
#: - v1 (761 chars, #73 dc8fea9c -> #266 b5cb6bb9): before the ``{task_id}``
#:   placeholder mechanism existed, this version was written to disk already
#:   substituted (``bootstrap`` by default, or whatever ``--task-id`` was
#:   passed at install) — the regex recognition below still matches it, the
#:   captured "task id" group just isn't the literal placeholder.
#: - v2 (824 chars, #585 lot B): + `gate run-tests` mandated before `gate check`.
#: - v3 (947 chars, #597 lot G1): + pointer to `task scaffold` for the envelope.
#: - current (396 chars, #602 lot G3): reduced to `gate check --strict` alone,
#:   which now runs `gate run-tests` itself when a fresh run is missing.
_HISTORICAL_DIRECTIVE_TEMPLATES: tuple[str, ...] = (
    (
        "[Grimoire Standard — activation]\n"
        "Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font\n"
        "partie de la tâche demandée :\n"
        "1. AVANT toute modification de code : remplis\n"
        "   `_grimoire-output/evidence/{task_id}/task-envelope.md` — objectif,\n"
        "   périmètre outillé (tool boundary) concret, critères de sortie.\n"
        "2. PENDANT le travail : consigne chaque preuve (commande exécutée, test\n"
        "   vert, diff clé) comme ligne concrète de l'inventaire dans\n"
        "   `_grimoire-output/evidence/{task_id}/evidence-pack.md`, et remplace le\n"
        "   résumé placeholder.\n"
        "3. AVANT de conclure : exécute\n"
        "   `grimoire standard gate check --task-id {task_id} --strict` puis\n"
        "   `grimoire standard verify .` et corrige tout échec.\n"
        "Une clôture sans gates verts est une tâche non terminée.\n"
    ),
    (
        "[Grimoire Standard — activation]\n"
        "Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font\n"
        "partie de la tâche demandée :\n"
        "1. AVANT toute modification de code : remplis\n"
        "   `_grimoire-output/evidence/{task_id}/task-envelope.md` — objectif,\n"
        "   périmètre outillé (tool boundary) concret, critères de sortie.\n"
        "2. PENDANT le travail : consigne chaque preuve (commande exécutée, test\n"
        "   vert, diff clé) comme ligne concrète de l'inventaire dans\n"
        "   `_grimoire-output/evidence/{task_id}/evidence-pack.md`, et remplace le\n"
        "   résumé placeholder.\n"
        "3. AVANT de conclure : exécute\n"
        "   `grimoire standard gate run-tests --task-id {task_id}` puis\n"
        "   `grimoire standard gate check --task-id {task_id} --strict` puis\n"
        "   `grimoire standard verify .` et corrige tout échec.\n"
        "Une clôture sans gates verts est une tâche non terminée.\n"
    ),
    (
        "[Grimoire Standard — activation]\n"
        "Ce projet est gouverné par le standard agentique Grimoire. Ces étapes font\n"
        "partie de la tâche demandée :\n"
        "1. AVANT toute modification de code : complète\n"
        "   `_grimoire-output/evidence/{task_id}/task-envelope.md` — objectif,\n"
        "   périmètre outillé (tool boundary) concret, critères de sortie. Le\n"
        "   squelette existe déjà (hook SessionStart) ; sinon\n"
        "   `grimoire standard task scaffold --task-id {task_id}` le crée.\n"
        "2. PENDANT le travail : consigne chaque preuve (commande exécutée, test\n"
        "   vert, diff clé) comme ligne concrète de l'inventaire dans\n"
        "   `_grimoire-output/evidence/{task_id}/evidence-pack.md`, et remplace le\n"
        "   résumé placeholder.\n"
        "3. AVANT de conclure : exécute\n"
        "   `grimoire standard gate run-tests --task-id {task_id}` puis\n"
        "   `grimoire standard gate check --task-id {task_id} --strict` puis\n"
        "   `grimoire standard verify .` et corrige tout échec.\n"
        "Une clôture sans gates verts est une tâche non terminée.\n"
    ),
)


#: Ce que le fichier de projet écrit à la place de l'identifiant : la tâche
#: change à chaque session, le fichier non.
TASK_ID_PLACEHOLDER = "{task_id}"


def default_activation_directive(task_id: str = "bootstrap") -> str:
    """Directive validated by the 2026-07-09 activated-arm campaign."""
    return _DIRECTIVE_TEMPLATE.replace(TASK_ID_PLACEHOLDER, task_id)


def activation_directive_template() -> str:
    """The directive as installed in a project: ``{task_id}`` left for the session to fill."""
    return _DIRECTIVE_TEMPLATE


def _known_rendering_pattern(template: str) -> re.Pattern[str]:
    """Compile *template* into a pattern matching it filled with any single
    task id (every ``{task_id}`` occurrence holding the same value — a task id
    never spans a line), or left as the literal placeholder."""
    parts = template.split(TASK_ID_PLACEHOLDER)
    pattern = re.escape(parts[0])
    for index, part in enumerate(parts[1:], start=1):
        pattern += r"(?P<task_id>[^\n]+?)" if index == 1 else r"(?P=task_id)"
        pattern += re.escape(part)
    return re.compile(pattern)


#: Compiled once: current wording first (the common case), then every past
#: one — order only matters for the (negligible) cost of a miss.
_KNOWN_RENDERING_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    _known_rendering_pattern(template)
    for template in (_DIRECTIVE_TEMPLATE, *_HISTORICAL_DIRECTIVE_TEMPLATES)
)


def _is_known_directive_rendering(text: str) -> bool:
    """True when *text* is an unedited kit rendering — current wording or any
    past one, raw placeholder or filled with a task id — never a project edit."""
    return any(pattern.fullmatch(text) for pattern in _KNOWN_RENDERING_PATTERNS)


def activation_context_text(project_root: Path, task_id: str = "bootstrap") -> str:
    """Project-level directive if present, built-in default otherwise — for *task_id*.

    The project file is a template: ``{task_id}`` is filled with the task the
    session resolved. A file matching any known kit rendering — current
    wording or a past one (:data:`_HISTORICAL_DIRECTIVE_TEMPLATES`), including
    files installed before the placeholder existed and carrying a literal
    task id — is rendered as the *current* default: a directive frozen on an
    old kit version, or naming the wrong task, was always the defect this
    fixes, not a customisation to preserve. A file a team actually edited is
    returned as written, placeholder filled if it kept one.
    """
    context_path = project_root / ACTIVATION_CONTEXT_RELPATH
    if context_path.is_file():
        text = context_path.read_text(encoding="utf-8")
        if _is_known_directive_rendering(text):
            return default_activation_directive(task_id)
        return text.replace(TASK_ID_PLACEHOLDER, task_id)
    return default_activation_directive(task_id)


def activation_context_needs_refresh(project_root: Path) -> bool:
    """True when ``.claude/activation-context.md`` is a stale kit rendering
    :func:`install_claude_activation` would refresh — without writing
    anything. Used by ``grimoire up --dry-run`` to report the pending change."""
    context_path = project_root / ACTIVATION_CONTEXT_RELPATH
    if not context_path.is_file():
        return False
    try:
        text = context_path.read_text(encoding="utf-8")
    except OSError:
        return False
    if text == activation_directive_template():
        return False
    if _is_known_directive_rendering(text):
        return True
    context_key = str(ACTIVATION_CONTEXT_RELPATH)
    manifest = gen.load_generation_manifest(project_root)
    return not gen.is_project_owned(project_root, context_key, manifest)


@dataclass(frozen=True)
class ClaudeActivationResult:
    """Outcome of an activation install attempt."""

    status: str  # installed | already-installed | skipped-invalid-settings
    written: list[Path] = field(default_factory=list)
    message: str = ""
    #: True when a refresh was requested (``grimoire up`` on an already
    #: initialized project) but ``.claude/activation-context.md`` did not
    #: match any known kit rendering — a real project edit, left untouched.
    #: Surfaced so `up`'s output flags it "à revoir" instead of staying silent.
    context_needs_review: bool = False


def _hook_entry() -> dict[str, object]:
    return {"hooks": [{"type": "command", "command": HOOK_COMMAND}]}


def _hook_already_present(session_start: list[object]) -> bool:
    for entry in session_start:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        for hook in hooks:
            if isinstance(hook, dict) and hook.get("command") == HOOK_COMMAND:
                return True
    return False


def _sync_activation_context(
    project_root: Path, *, force: bool, refresh: bool
) -> tuple[list[Path], bool]:
    """Write, refresh or leave ``.claude/activation-context.md`` alone.

    Returns the (possibly empty) list of written paths and whether a refresh
    was asked for but blocked by a genuine project edit — see
    :attr:`ClaudeActivationResult.context_needs_review`.
    """
    written: list[Path] = []
    context_path = project_root / ACTIVATION_CONTEXT_RELPATH
    context_key = str(ACTIVATION_CONTEXT_RELPATH)
    template = activation_directive_template()

    try:
        existing_text = context_path.read_text(encoding="utf-8") if context_path.is_file() else None
    except OSError:
        existing_text = None

    manifest = gen.load_generation_manifest(project_root)
    if (
        existing_text is not None
        and context_key not in manifest
        and _is_known_directive_rendering(existing_text)
    ):
        # Predates the manifest (or the recognition it now gets): an unedited
        # rendering from any past kit version is the kit's to refresh from now
        # on — the same "adopt what predates tracking" move
        # `standard_generation.decide` makes for its own artifacts.
        gen.save_generation_manifest(project_root, {context_key: gen.digest(context_path)})
        manifest = gen.load_generation_manifest(project_root)

    if existing_text is None:
        # Le fichier reste un gabarit : la tâche est résolue à chaque session
        # (claim du ledger, board, ``GRIMOIRE_TASK_ID``), pas figée à l'install.
        context_path.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(template, encoding="utf-8")
        written.append(ACTIVATION_CONTEXT_RELPATH)
        gen.save_generation_manifest(project_root, {context_key: gen.digest(context_path)})
        return written, False

    action = gen.decide(project_root, context_key, template, force=force, refresh=refresh, manifest=manifest)
    needs_review = False
    if action == "write":
        context_path.write_text(template, encoding="utf-8")
        written.append(ACTIVATION_CONTEXT_RELPATH)
        gen.save_generation_manifest(project_root, {context_key: gen.digest(context_path)})
    elif action == "adopt":
        gen.save_generation_manifest(project_root, {context_key: gen.digest(context_path)})
    elif refresh and gen.is_project_owned(project_root, context_key, manifest):
        # A refresh was requested (``grimoire up`` on an initialized project)
        # but this is a real project edit, not a stale kit rendering — leave
        # it, but do not stay silent about it either.
        needs_review = True
    return written, needs_review


def install_claude_activation(
    project_root: Path,
    task_id: str = "bootstrap",
    *,
    force: bool = False,
    refresh: bool = False,
) -> ClaudeActivationResult:
    """Install, or refresh, the SessionStart activation hook into *project_root*.

    Idempotent; never destroys existing settings content. On a malformed
    ``settings.json`` the file is left untouched and the caller gets a
    ``skipped-invalid-settings`` status to surface.

    ``.claude/activation-context.md`` is tracked like any other kit-owned
    standard artifact (see the module docstring): *refresh* updates it when it
    is a stale but untouched kit rendering, leaving a real project edit alone
    — flagged via :attr:`ClaudeActivationResult.context_needs_review` rather
    than silently kept. *force* overwrites it unconditionally, same as the
    other standard artifacts. Neither flag ever touches ``settings.json``
    beyond the usual idempotent hook merge.
    """
    written, context_needs_review = _sync_activation_context(project_root, force=force, refresh=refresh)

    settings_path = project_root / SETTINGS_RELPATH
    data: dict[str, object] = {}
    if settings_path.exists():
        try:
            loaded: object = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ClaudeActivationResult(
                status="skipped-invalid-settings",
                written=written,
                message=f"{SETTINGS_RELPATH} illisible — hook non installé, fichier préservé",
                context_needs_review=context_needs_review,
            )
        if not isinstance(loaded, dict):
            return ClaudeActivationResult(
                status="skipped-invalid-settings",
                written=written,
                message=f"{SETTINGS_RELPATH} n'est pas un objet JSON — fichier préservé",
                context_needs_review=context_needs_review,
            )
        data = loaded

    hooks = data.get("hooks")
    if hooks is None:
        hooks = {}
        data["hooks"] = hooks
    if not isinstance(hooks, dict):
        return ClaudeActivationResult(
            status="skipped-invalid-settings",
            written=written,
            message=f"champ 'hooks' inattendu dans {SETTINGS_RELPATH} — fichier préservé",
            context_needs_review=context_needs_review,
        )

    session_start = hooks.get("SessionStart")
    if session_start is None:
        session_start = []
        hooks["SessionStart"] = session_start
    if not isinstance(session_start, list):
        return ClaudeActivationResult(
            status="skipped-invalid-settings",
            written=written,
            message=f"champ 'hooks.SessionStart' inattendu dans {SETTINGS_RELPATH} — fichier préservé",
            context_needs_review=context_needs_review,
        )

    if _hook_already_present(session_start):
        status = "installed" if written else "already-installed"
        return ClaudeActivationResult(status=status, written=written, context_needs_review=context_needs_review)

    session_start.append(_hook_entry())
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    written.append(SETTINGS_RELPATH)
    return ClaudeActivationResult(status="installed", written=written, context_needs_review=context_needs_review)
