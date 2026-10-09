"""Le journal d'actions observées par les hooks, projeté dans le pack de preuve (issue #582 lot G2).

Avant ce lot, ``evidence-pack.md`` n'avait qu'une section « Evidence inventory »
éditée à la main : l'agent recopiait systématiquement ce que le hook
``PostToolUse`` venait de lui rappeler (« Écriture enregistrée… ») en une ligne
de tableau. Le rapport du banc à trois bras (2026-09-17) montre que cette
recopie coûte des tours pour une information que le hook connaît déjà.

Ce module tient deux rôles :

1. **Écriture** (:func:`append_evidence_event`) — le hook ``PostToolUse``
   (:mod:`grimoire.hosts.decisions.evidence_trace`) y ajoute une ligne JSON par
   commande Bash exécutée, fichier écrit/édité, ou run de test reconnu.
   Append-only, ``encoding="utf-8"``, jamais de lecture ni de ré-écriture du
   fichier entier : le coût doit rester sous la barre des 30 ms mesurée pour
   ce hook.
2. **Projection** (:func:`regenerate_observed_inventory_section`) — ``gate
   check`` et le hook ``Stop`` (via ``check_evidence_gates``) et ``standard
   verify`` (via ``_verify_evidence_pack``) régénèrent depuis ce journal une
   section « Inventaire observé » dans ``evidence-pack.md``, sous la section
   manuelle « Evidence inventory » qui reste éditable. Idempotent : la section
   est délimitée par des marqueurs HTML et entièrement remplacée à chaque
   appel, jamais dupliquée.

Garde fermée : un journal absent, illisible, ou dont aucune ligne ne parse en
JSON valide se lit comme « rien observé » (:func:`read_evidence_log` renvoie
``[]``) — jamais comme une erreur qui remonterait, et jamais comme une preuve
fabriquée. C'est délibérément permissif ligne à ligne (une ligne corrompue au
milieu d'un fichier par ailleurs valide n'invalide pas les autres) : le risque
que ça couvre est la corruption partielle après une coupure d'écriture, pas un
faux journal construit à la main pour tromper le gate — falsifier un JSONL
plausible du premier au dernier caractère reste, comme recopier un rapport de
tests, un choix qui engage celui qui le fait.

La détection de run de test est un motif de commande (voir
``_TEST_COMMAND_PATTERN``), pas un appel à
:func:`grimoire.core.execution_needs.resolve_need` : ce dernier lit
``project-context.yaml`` et teste plusieurs marqueurs sur disque à chaque
invocation, un coût que ce hook — appelé sur *chaque* outil de *chaque* tour —
ne doit pas payer. Le lot G3 (détection étendue des commandes de test) élargit
``resolve_need`` lui-même ; ce module n'en a pas besoin pour journaliser une
commande qui, motif ou non, est de toute façon déjà consignée comme
``"bash"``.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.standard_generation import EVIDENCE_DIR, RUNS_DIR, normalize_task_id

__all__ = [
    "EVIDENCE_LOG_FILENAME",
    "GUARD_ERROR_EVENT",
    "GUARD_EVENTS_FILENAME",
    "GUARD_EVENTS_MAX",
    "append_evidence_event",
    "append_guard_event",
    "describe_guard_errors",
    "evidence_log_relpath",
    "guard_events_relpath",
    "has_observed_inventory",
    "read_evidence_log",
    "read_guard_events",
    "record_guard_error",
    "regenerate_observed_inventory_section",
]

#: Nom du fichier journal, un par tâche, sous ``RUNS_DIR / "evidence" / <task_id>/``.
EVIDENCE_LOG_FILENAME = "evidence-log.jsonl"

#: Journal des événements de garde (``guard.error``, W1-06), à côté du journal
#: d'actions mais DISTINCT : ``evidence-log.jsonl`` est l'inventaire des actions
#: observées (:func:`has_observed_inventory`) — y écrire une panne de garde
#: ferait croire qu'une action a été observée.
GUARD_EVENTS_FILENAME = "guard-events.jsonl"

#: Nombre maximal d'événements conservés par tâche dans le journal de garde.
GUARD_EVENTS_MAX = 200

#: Type d'un événement de garde en panne (fail-open évité, W1-06).
GUARD_ERROR_EVENT = "guard.error"

#: Longueur maximale d'une commande consignée ; au-delà, tronquée avec un
#: marqueur explicite plutôt que de laisser grossir le journal sans borne.
_COMMAND_TRUNCATE_AT = 240

#: Motif de commande de run de test reconnu (voir docstring du module) —
#: distinct, volontairement, du besoin ``resolve_need("test-runner", ...)``.
#: Issue #644 : ``vitest``/``jest``/``tsc``/``eslint``/``ruff`` rejoignent la
#: liste — le gate « fini » (:mod:`grimoire.hosts.decisions.done_gate`) doit
#: reconnaître un check JS/TS ou un lint comme un check au même titre qu'un
#: run pytest, et c'est ce motif qui décide, ici comme là-bas, ce que le
#: journal marque ``"test_run"`` plutôt que ``"bash"``.
_TEST_COMMAND_PATTERN = re.compile(
    r"\bpytest\b|\bnpm\s+(?:run\s+)?test\b|\bcargo\s+test\b|\bgo\s+test\b|\bctest\b"
    r"|\bmvn(?:\.cmd)?\s+(?:\S+\s+)*test\b|\bgradlew?\s+test\b"
    r"|\bvitest\b|\bjest\b|\btsc\b|\beslint\b|\bruff\b",
    re.IGNORECASE,
)

#: Nombre maximal de lignes rendues dans la section projetée — un journal de
#: session longue ne doit pas rendre le pack de preuve illisible ; le total
#: réel est toujours indiqué.
_MAX_RENDERED_ROWS = 40

_SECTION_START = "<!-- grimoire:observed-inventory:start -->"
_SECTION_END = "<!-- grimoire:observed-inventory:end -->"
_MANUAL_HEADING = "## Evidence inventory"


def evidence_log_relpath(task_id: str) -> Path:
    """Chemin, relatif à la racine du projet, du journal de *task_id*.

    Sous ``RUNS_DIR`` (``_grimoire-output/.runs/``), pas sous ``EVIDENCE_DIR``
    : un fichier append-only qui grossit à chaque appel d'outil n'est pas un
    artefact de preuve versionné (celui-là reste ``evidence-pack.md``, dans
    ``EVIDENCE_DIR``) — il polluerait ``git status`` et l'historique de tout
    projet gouverné. ``RUNS_DIR`` est déjà ignoré par convention (voir
    :func:`grimoire.core.standard_generation.ensure_grimoire_gitignore`).
    """
    return RUNS_DIR / "evidence" / normalize_task_id(task_id) / EVIDENCE_LOG_FILENAME


def guard_events_relpath(task_id: str) -> Path:
    """Chemin (relatif à la racine projet) du journal d'événements de garde de *task_id*."""
    return RUNS_DIR / "evidence" / normalize_task_id(task_id) / GUARD_EVENTS_FILENAME


def _truncate_command(command: str) -> str:
    command = " ".join(command.split())
    if len(command) <= _COMMAND_TRUNCATE_AT:
        return command
    return command[:_COMMAND_TRUNCATE_AT] + "… (tronqué)"


def is_test_command(command: str) -> bool:
    """Whether *command* matches a recognised test-runner invocation shape."""
    return bool(command) and bool(_TEST_COMMAND_PATTERN.search(command))


def build_bash_event(command: str, *, exit_code: int | None) -> dict[str, Any]:
    """Build the JSONL event for one observed Bash-shaped tool call."""
    kind = "test_run" if is_test_command(command) else "bash"
    event: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "type": kind,
        "command": _truncate_command(command),
    }
    if exit_code is not None:
        event["exit_code"] = exit_code
    return event


def build_file_write_event(path: str) -> dict[str, Any]:
    """Build the JSONL event for one observed file write/edit target."""
    return {"ts": datetime.now(UTC).isoformat(), "type": "file_write", "path": path}


def append_evidence_event(project_root: Path, task_id: str, event: dict[str, Any]) -> None:
    """Append one JSON line to the task's evidence log. Best-effort: never raises.

    Callers (the ``PostToolUse`` hook) run on every tool call of every
    session; a read-only project mount or a permission error here must
    degrade to "nothing logged this call", never to a broken hook.
    """
    try:
        rel_path = evidence_log_relpath(task_id)
        full_path = project_root / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False)
        with open(full_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        return


def append_guard_event(project_root: Path, task_id: str, event: dict[str, Any]) -> None:
    """Append one guard event (``guard.error``…) to the task's guard journal. Best-effort: never raises.

    The journal is bounded (W1-06) : ``error_message`` is cut at the same
    length as a command in ``evidence-log.jsonl``. An event identical to the
    previous one (same fields, ``ts``/``count``/``last_ts`` aside) is not
    written as a new line — a gate that crashes on every ``Stop`` leaves one
    line, not one per turn — but it is *counted* : the last line is rewritten
    with ``count`` (occurrences) and ``last_ts``, so "N panne(s)" stays true.
    At :data:`GUARD_EVENTS_MAX` lines the oldest are dropped (rotation), so the
    journal always ends on the most recent failure.
    """
    try:
        message = event.get("error_message")
        if isinstance(message, str):
            event = {**event, "error_message": _truncate_command(message)}
        full_path = project_root / guard_events_relpath(task_id)
        existing = read_guard_events(project_root, task_id)
        if existing and _without_ts(existing[-1]) == _without_ts(event):
            last = existing[-1]
            last["count"] = _event_count(last) + 1
            last["last_ts"] = event.get("ts", last.get("ts"))
            _write_guard_events(full_path, existing)
            return
        full_path.parent.mkdir(parents=True, exist_ok=True)
        if len(existing) >= GUARD_EVENTS_MAX:
            _write_guard_events(full_path, [*existing[len(existing) - GUARD_EVENTS_MAX + 1 :], event])
            return
        with open(full_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        return


def _event_count(event: dict[str, Any]) -> int:
    """Occurrences carried by one journal line : ``count`` when it is a positive int, else ``1``."""
    count = event.get("count", 1)
    return count if isinstance(count, int) and not isinstance(count, bool) and count > 0 else 1


def _write_guard_events(path: Path, events: list[dict[str, Any]]) -> None:
    """Rewrite the whole guard journal atomically (temp file + ``replace``). Raises ``OSError`` on failure."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise


def record_guard_error(project_root: Path, task_id: str, guard_id: str, error_type: str, error_message: str) -> None:
    """Trace a ``guard.error`` (W1-06) in the guard journal. Best-effort: never raises."""
    append_guard_event(
        project_root,
        task_id,
        {
            "ts": datetime.now(UTC).isoformat(),
            "type": GUARD_ERROR_EVENT,
            "guard_id": guard_id,
            "error_type": error_type,
            "error_message": error_message,
        },
    )


def describe_guard_errors(project_root: Path, task_id: str) -> str:
    """The ``guard.error_recorded`` message for *task_id*, with its remedy. ``""`` when none.

    The count is the sum of the occurrences carried by each line (``count``,
    see :func:`append_guard_event`), not the number of lines.
    """
    events = read_guard_events(project_root, task_id)
    errors = [e for e in events if e.get("type") == GUARD_ERROR_EVENT]
    if not errors:
        return ""
    last = errors[-1]
    total = sum(_event_count(e) for e in errors)
    rotated = " Journal plein : les pannes les plus anciennes sont écartées." if len(events) >= GUARD_EVENTS_MAX else ""
    return (
        f"{total} panne(s) de garde tracée(s) pour {task_id} (dernière : garde « {last.get('guard_id', '?')} », "
        f"{last.get('error_type', '?')}) : la garde n'a pas pu juger, ce n'est pas un vert.{rotated} "
        f"Corrige la cause puis relance `grimoire standard gate check --task-id {task_id}` ; "
        f"journal : {guard_events_relpath(task_id).as_posix()} (le supprimer acquitte les pannes)."
    )


def _without_ts(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in {"ts", "count", "last_ts"}}


def read_guard_events(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    """Every well-formed guard event logged for *task_id*. ``[]`` when none or unreadable."""
    full_path = project_root / guard_events_relpath(task_id)
    try:
        raw = full_path.read_text(encoding="utf-8")
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type"):
            events.append(parsed)
    return events


def read_evidence_log(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    """Every well-formed JSON object logged for *task_id*. ``[]`` when there is none.

    Malformed individually (a truncated last line after a crash mid-write,
    for instance): that one line is skipped, not the rest of the file — see
    the module docstring for why this is the closed-guard behaviour, not a
    loophole.
    """
    full_path = project_root / evidence_log_relpath(task_id)
    if not full_path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        raw = full_path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and parsed.get("type"):
            entries.append(parsed)
    return entries


def has_observed_inventory(project_root: Path, task_id: str) -> bool:
    """Whether at least one concrete action was observed for *task_id*."""
    return bool(read_evidence_log(project_root, task_id))


@dataclass(frozen=True, slots=True)
class _Row:
    proof: str
    detail: str
    source: str
    result: str


def _row_for(entry: dict[str, Any]) -> _Row | None:
    kind = entry.get("type")
    if kind == "file_write":
        path = str(entry.get("path") or "")
        if not path:
            return None
        return _Row("Fichier modifié", path, "hook PostToolUse (observé)", "—")
    if kind in {"bash", "test_run"}:
        command = str(entry.get("command") or "")
        if not command:
            return None
        exit_code = entry.get("exit_code")
        result = f"exit {exit_code}" if exit_code is not None else "code de sortie inconnu"
        label = "Run de test" if kind == "test_run" else "Commande"
        return _Row(label, f"`{command}`", "hook PostToolUse (observé)", result)
    return None


def render_observed_inventory(entries: list[dict[str, Any]]) -> str:
    """Render the ``## Inventaire observé`` block for *entries* (possibly empty)."""
    rows = [row for row in (_row_for(entry) for entry in entries) if row is not None]
    lines = [
        _SECTION_START,
        "## Inventaire observé",
        "",
        "Régénéré automatiquement depuis `evidence-log.jsonl` (hooks du host) — "
        "ne pas éditer à la main ; la section manuelle « Evidence inventory » "
        "ci-dessus reste éditable et prime en cas de doute.",
        "",
        "| Preuve | Détail | Source | Résultat |",
        "|---|---|---|---|",
    ]
    if not rows:
        lines.append("| Aucune preuve observée pour l'instant |  |  |  |")
    else:
        shown = rows[-_MAX_RENDERED_ROWS:]
        if len(rows) > len(shown):
            lines.append(f"| … {len(rows) - len(shown)} entrée(s) plus ancienne(s) omise(s) |  |  |  |")
        for row in shown:
            lines.append(f"| {row.proof} | {row.detail} | {row.source} | {row.result} |")
    lines.append(_SECTION_END)
    return "\n".join(lines) + "\n"


def _insert_after_manual_section(text: str, block: str) -> str:
    heading_at = text.find(_MANUAL_HEADING)
    if heading_at == -1:
        # Gabarit personnalisé sans la section attendue : ajoute à la fin
        # plutôt que d'échouer silencieusement à projeter quoi que ce soit.
        separator = "\n" if text.endswith("\n") else "\n\n"
        return text + separator + block
    next_heading_at = text.find("\n## ", heading_at + len(_MANUAL_HEADING))
    if next_heading_at == -1:
        return text.rstrip("\n") + "\n\n" + block
    insertion_point = next_heading_at + 1
    return text[:insertion_point] + block + "\n" + text[insertion_point:]


def regenerate_observed_inventory_section(project_root: Path, task_id: str) -> bool:
    """Regenerate the observed-inventory section of ``evidence-pack.md``.

    Returns whether at least one entry was observed (so callers can reuse the
    result instead of re-reading the journal). A missing ``evidence-pack.md``
    is a no-op (``False``): there is nothing to project into, and creating it
    here would step on ``standard_task_scaffold``'s job.
    """
    rel_path = EVIDENCE_DIR / normalize_task_id(task_id) / "evidence-pack.md"
    full_path = project_root / rel_path
    if not full_path.is_file():
        return False
    entries = read_evidence_log(project_root, task_id)
    try:
        text = full_path.read_text(encoding="utf-8")
    except OSError:
        return bool(entries)
    block = render_observed_inventory(entries)
    if _SECTION_START in text and _SECTION_END in text:
        start = text.index(_SECTION_START)
        end = text.index(_SECTION_END) + len(_SECTION_END)
        new_text = text[:start] + block.rstrip("\n") + text[end:]
    else:
        new_text = _insert_after_manual_section(text, block)
    if new_text != text:
        with contextlib.suppress(OSError):
            full_path.write_text(new_text, encoding="utf-8")
    return bool(entries)
