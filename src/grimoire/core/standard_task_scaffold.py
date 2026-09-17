"""``grimoire standard task scaffold`` : les artefacts qu'une tâche doit au gate, créés s'ils manquent (issue #582 lot G1).

Avant ce lot, ``standard init`` ne scaffoldait les artefacts par tâche
(enveloppe, pack de preuve, registre d'affirmations, dossier d'acceptance) que
pour la tâche ``bootstrap`` ; toute tâche ouverte ensuite par ``grimoire task
add`` arrivait devant ``gate check`` sans rien, et le message d'échec ne disait
ni où créer les fichiers ni quoi mettre dedans. Le banc du 2026-09-17
(``docs/bench/diagnostic-surcout-kit-2026-09-17.md``) a chiffré ce que ça coûte :
une médiane de 11 tours par run passés à fouiller le source installé du kit
pour reconstituer ces conventions.

Ce module est idempotent et ne réécrit jamais un fichier existant : un
artefact qu'un agent a commencé à remplir n'est pas un artefact à régénérer.
Il pré-remplit chaque squelette avec ce que le kit sait déjà sans poser de
question — identifiant et titre de la tâche, critères d'acceptation lus dans le
Mission Ledger (ADR-007 : le ledger est la source, le board sa projection),
profil actif, ``HEAD`` git, commande de test résolue par
:func:`grimoire.core.execution_needs.resolve_need`, date du jour — de sorte
qu'un squelette fraîchement scaffoldé n'est jamais refusé par ``gate check``
pour un motif de forme (placeholder détecté), seulement pour un motif de fond
(tests rouges, critère déclaré passé sans preuve).

Extrait de :mod:`grimoire.core.agentic_standard` dans son propre module, comme
``acceptance_test_run`` et ``task_board_ledger`` avant lui : ce fichier est
grandfathered au ratchet de taille (``scripts/code-ratchet-baseline.json``) et
ne peut plus grossir.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from grimoire.core.standard_checks.gate_remedy import gate_artifact_relpath
from grimoire.core.standard_generation import normalize_task_id

__all__ = [
    "ScaffoldResult",
    "TaskFacts",
    "missing_task_artifacts",
    "scaffold_task_artifacts",
    "task_artifact_relpaths",
]

#: Ordre de création : l'enveloppe d'abord (c'est elle que la directive nomme
#: en premier), les artefacts runtime (bundle, trace) en dernier — ils lisent
#: le board, pas les fichiers Markdown.
_TASK_ARTIFACT_ORDER: tuple[str, ...] = (
    "task_envelope",
    "evidence_pack",
    "claim_ledger",
    "acceptance_record",
    "context_bundle",
    "decision_trace",
)
#: Clés dont le contenu vient d'un template ``framework/agentic-standard/templates/``.
_TEMPLATED_KEYS: frozenset[str] = frozenset({"task_envelope", "evidence_pack", "claim_ledger", "acceptance_record"})

#: Colonne du board → « Current state » de l'enveloppe (vocabulaire du template).
_ENVELOPE_STATE_BY_BOARD_STATUS: dict[str, str] = {
    "proposed": "intake",
    "ready": "planned",
    "in_progress": "executing",
    "blocked": "blocked",
    "review": "validating",
    "accepted": "done",
    "released": "done",
    "archived": "done",
}
#: Profil de risque du ledger → « Risk level » de l'enveloppe.
_RISK_LEVEL_BY_PROFILE: dict[str, str] = {
    "light": "low",
    "standard": "medium",
    "strict": "high",
    "security-critical": "critical",
    "release": "high",
}
#: Priorité du board (projection) → « Risk level », quand le ledger est absent.
_RISK_LEVEL_BY_PRIORITY: dict[str, str] = {"low": "low", "medium": "medium", "high": "high", "critical": "critical"}


@dataclass(frozen=True, slots=True)
class TaskFacts:
    """Ce que le kit sait d'une tâche sans rien demander à personne."""

    task_id: str
    title: str
    board_status: str
    acceptance: tuple[str, ...]
    owner: str = ""
    risk_level: str = ""
    source: str = "board"


@dataclass(frozen=True, slots=True)
class ScaffoldResult:
    """Ce que le scaffold a créé, et ce qu'il a laissé tel quel."""

    task_id: str
    profile: str
    dry_run: bool
    written: tuple[Path, ...] = ()
    skipped: tuple[Path, ...] = ()
    facts: TaskFacts | None = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "profile": self.profile,
            "dry_run": self.dry_run,
            "written": [path.as_posix() for path in self.written],
            "skipped": [path.as_posix() for path in self.skipped],
        }


def task_artifact_relpaths(task_id: str) -> dict[str, Path]:
    """Clé de gate → chemin relatif, pour tout artefact par tâche que le gate peut réclamer."""
    return {key: gate_artifact_relpath(key, task_id) for key in _TASK_ARTIFACT_ORDER}


def missing_task_artifacts(project_root: Path, task_id: str) -> list[str]:
    """Clés des artefacts par tâche absents du disque — six ``stat``, rien d'autre.

    C'est le chemin rapide du hook ``SessionStart`` : quand tout existe déjà,
    aucun YAML n'est lu, aucun template n'est rendu, le ledger n'est pas
    ouvert. Un test (``test_scaffold_noop_stays_under_the_session_start_budget``)
    borne ce chemin.
    """
    root = project_root.resolve()
    return [key for key, relpath in task_artifact_relpaths(task_id).items() if not (root / relpath).is_file()]


def scaffold_task_artifacts(
    project_root: Path,
    *,
    task_id: str,
    profile_id: str | None = None,
    dry_run: bool = False,
) -> ScaffoldResult:
    """Crée, s'ils manquent seulement, les artefacts que le profil actif exige pour *task_id*.

    Lève :class:`ValueError` quand la tâche n'est déclarée ni dans le Mission
    Ledger ni sur le board : scaffolder des preuves pour une tâche qui n'existe
    pas fabriquerait un dossier orphelin sous ``_grimoire-output/`` — le remède
    est ``grimoire task add``, pas un squelette de plus.

    *dry_run* rend le plan sans rien écrire — ni fichier, ni événement de
    journal : c'est le mode qu'un rejeu en lecture seule (par exemple sur un
    projet qu'on ne veut pas toucher) doit utiliser.
    """
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    relpaths = task_artifact_relpaths(normalized_task_id)
    missing = missing_task_artifacts(root, normalized_task_id)
    if not missing:
        # Chemin rapide du hook SessionStart : le profil vient du cache dérivé
        # de `standard_state` (un JSON, pas ruamel), et rien d'autre n'est lu.
        from grimoire.core.standard_state import active_profile_id

        return ScaffoldResult(
            task_id=normalized_task_id,
            profile=profile_id or active_profile_id(root),
            dry_run=dry_run,
            skipped=tuple(relpaths.values()),
        )
    # Importé ici, pas au sommet : `agentic_standard` est le module lourd
    # (ruamel, profile-map) que le chemin rapide ci-dessus ne doit jamais payer.
    from grimoire.core.agentic_standard import _selected_profile

    profile = _selected_profile(root, profile_id)

    facts = _task_facts(root, normalized_task_id)
    if facts is None:
        msg = (
            f"Tâche {normalized_task_id!r} inconnue du Mission Ledger et du board : "
            f'rien à scaffolder. Ouvrez-la d\'abord : grimoire task add "<titre>" '
            f'--acceptance "<critère>" --project-root {root}'
        )
        raise ValueError(msg)

    required = set(profile.required_artifacts)
    planned = [
        key for key in _TASK_ARTIFACT_ORDER if key in missing and (key not in _TEMPLATED_KEYS or key in required)
    ]
    written: list[Path] = []
    skipped = [relpath for key, relpath in relpaths.items() if key not in planned]
    if dry_run:
        return ScaffoldResult(
            task_id=normalized_task_id,
            profile=profile.id,
            dry_run=True,
            written=tuple(relpaths[key] for key in planned),
            skipped=tuple(skipped),
            facts=facts,
        )

    from grimoire.core.agentic_standard import (
        _append_runtime_event,
        _artifact_templates,
        _ensure_inside_root,
        build_context_bundle,
        build_decision_trace,
    )
    from grimoire.core.standard_generation import _render_template

    templates = _artifact_templates() if any(key in _TEMPLATED_KEYS for key in planned) else {}
    today = datetime.now(UTC).date().isoformat()
    context = _PrefillContext(
        facts=facts,
        profile_id=profile.id,
        today=today,
        git_head=_git_head(root),
        test_command=_test_command(root),
        relpaths=relpaths,
    )
    for key in planned:
        relpath = relpaths[key]
        destination = _ensure_inside_root(root, root / relpath, label=f"Task artifact {key!r}")
        if key == "context_bundle":
            build_context_bundle(root, task_id=normalized_task_id, profile_id=profile.id)
        elif key == "decision_trace":
            build_decision_trace(root, task_id=normalized_task_id, profile_id=profile.id)
        else:
            template = templates[key].read_text(encoding="utf-8")
            rendered = _render_template(template, project_name=root.name, profile=profile, generated_at=today)
            content = _PREFILL[key](rendered, context)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        written.append(relpath)
    _append_runtime_event(
        root,
        event_type="task.scaffolded",
        task_id=normalized_task_id,
        profile=profile.id,
        details={"written": [path.as_posix() for path in written], "source": facts.source},
    )
    return ScaffoldResult(
        task_id=normalized_task_id,
        profile=profile.id,
        dry_run=False,
        written=tuple(written),
        skipped=tuple(skipped),
        facts=facts,
    )


# ── Faits : ledger d'abord, board ensuite ───────────────────────────────────


def _task_facts(root: Path, task_id: str) -> TaskFacts | None:
    facts = _facts_from_ledger(root, task_id)
    if facts is not None:
        return facts
    return _facts_from_board(root, task_id)


def _facts_from_ledger(root: Path, task_id: str) -> TaskFacts | None:
    """Le ledger est la source (ADR-007) : titre, critères, risque et owner viennent de là.

    Ne lève jamais : un ledger absent ou illisible vaut « pas de faits ici »,
    et la lecture continue sur le board — même contrat que
    :func:`grimoire.core.standard_state.claimed_task_ids`.
    """
    from grimoire.core.standard_state import LEDGER_RELPATH

    events = root / LEDGER_RELPATH / "events.jsonl"
    if not events.is_file():
        return None
    try:
        from grimoire.missions.board import board_status_of
        from grimoire.missions.ledger import MissionLedger

        task = MissionLedger(events.parent).get_task(task_id)
    except Exception:  # frontière : un ledger cassé ne doit pas empêcher un squelette
        return None
    if task is None:
        return None
    owner = task.owner or (task.claim.actor_id if task.claim is not None else "")
    return TaskFacts(
        task_id=task.id,
        title=task.title,
        board_status=board_status_of(task.status),
        acceptance=tuple(task.acceptance),
        owner=owner,
        risk_level=_RISK_LEVEL_BY_PROFILE.get(task.risk_profile.value, ""),
        source="ledger",
    )


def _facts_from_board(root: Path, task_id: str) -> TaskFacts | None:
    from grimoire.core.standard_generation import STANDARD_DIR
    from grimoire.core.standard_state import _load_mapping, task_from_board

    task = task_from_board(_load_mapping(root / STANDARD_DIR / "task-board.yaml"), task_id)
    if not task:
        return None
    raw_acceptance = task.get("acceptance_criteria")
    acceptance = tuple(str(item) for item in raw_acceptance) if isinstance(raw_acceptance, list) else ()
    return TaskFacts(
        task_id=task_id,
        title=str(task.get("title") or task_id),
        board_status=str(task.get("status") or ""),
        acceptance=acceptance,
        owner=str(task.get("owner") or ""),
        risk_level=_RISK_LEVEL_BY_PRIORITY.get(str(task.get("priority") or ""), ""),
        source="board",
    )


def _git_head(root: Path) -> str:
    """``HEAD`` abrégé, ou ``""`` hors dépôt git — jamais une exception."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _test_command(root: Path) -> str:
    from grimoire.core.execution_needs import resolve_need

    need = resolve_need("test-runner", root)
    return need.command or "" if need.resolved else ""


# ── Pré-remplissage ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _PrefillContext:
    facts: TaskFacts
    profile_id: str
    today: str
    git_head: str
    test_command: str
    relpaths: dict[str, Path]


#: En-têtes des tableaux du template, pour ne remplacer que la ligne vide du
#: bon tableau (plusieurs tableaux partagent la même ligne vide à N colonnes).
_CONTEXT_TABLE_HEAD = "| Context item | Source | Reason selected | Freshness | Token budget |\n|---|---|---|---|---:|\n"
_INVENTORY_TABLE_HEAD = "| Evidence | Location | Produced by | Result |\n|---|---|---|---|\n"
_VALIDATION_TABLE_HEAD = "| Check | Command or method | Result | Notes |\n|---|---|---|---|\n"


def _test_run_relpath(task_id: str) -> Path:
    from grimoire.core.standard_checks.controls import acceptance_test_run_relpath

    return acceptance_test_run_relpath(task_id)


def _cell(value: str) -> str:
    """Une valeur sûre dans une cellule de tableau Markdown : ni barre verticale, ni retour ligne."""
    return " ".join(value.replace("|", "/").split())


def _summary(facts: TaskFacts) -> str:
    """Le résumé généré qui remplace « Outcome: » vide : titre puis critères numérotés."""
    if not facts.acceptance:
        return _cell(facts.title)
    criteria = "; ".join(f"AC-{index:03d} {_cell(item)}" for index, item in enumerate(facts.acceptance, start=1))
    return f"{_cell(facts.title)} — critères : {criteria}"


def _replace_once(text: str, needle: str, replacement: str) -> str:
    return text.replace(needle, replacement, 1)


def _prefill_task_envelope(text: str, ctx: _PrefillContext) -> str:
    facts = ctx.facts
    text = _replace_once(text, "- Task id:\n", f"- Task id: {facts.task_id}\n")
    text = _replace_once(text, "- Request:\n", f"- Request: {_cell(facts.title)}\n")
    if facts.owner:
        text = _replace_once(text, "- Owner agent:\n", f"- Owner agent: {_cell(facts.owner)}\n")
    text = _replace_once(text, "- Profile:\n", f"- Profile: {ctx.profile_id}\n")
    state = _ENVELOPE_STATE_BY_BOARD_STATUS.get(facts.board_status)
    if state:
        text = _replace_once(
            text,
            "- Current state: `intake | planned | executing | validating | blocked | done`\n",
            f"- Current state: `{state}`\n",
        )
    if facts.risk_level:
        text = _replace_once(
            text,
            "- Risk level: `low | medium | high | critical`\n",
            f"- Risk level: `{facts.risk_level}`\n",
        )
    bundle = ctx.relpaths["context_bundle"]
    text = _replace_once(
        text,
        _CONTEXT_TABLE_HEAD + "|  |  |  |  |  |\n",
        _CONTEXT_TABLE_HEAD
        + f"| Context bundle | {bundle} | généré par le kit depuis le Mission Ledger | {ctx.today} | n/a |\n",
    )
    if ctx.test_command:
        text = _replace_once(
            text,
            "|  | read-only |  |  |\n",
            f"| `{_cell(ctx.test_command)}` | execute | tests du projet (gate run-tests) | racine du projet |\n",
        )
    return text


def _prefill_evidence_pack(text: str, ctx: _PrefillContext) -> str:
    facts = ctx.facts
    text = _replace_once(text, "- Task id:\n", f"- Task id: {facts.task_id}\n")
    text = _replace_once(text, "- Profile:\n", f"- Profile: {ctx.profile_id}\n")
    text = _replace_once(text, "- Outcome:\n", f"- Outcome: {_summary(facts)}\n")
    if facts.board_status:
        text = _replace_once(text, "- Final state:\n", f"- Final state: {facts.board_status} ({ctx.today})\n")
    if ctx.git_head:
        text = _replace_once(
            text,
            _INVENTORY_TABLE_HEAD + "|  |  |  |  |\n",
            _INVENTORY_TABLE_HEAD
            + f"| Point de départ git | HEAD {ctx.git_head} | git rev-parse --short HEAD | référence ({ctx.today}) |\n",
        )
    if ctx.test_command:
        test_run = _test_run_relpath(facts.task_id)
        text = _replace_once(
            text,
            _VALIDATION_TABLE_HEAD + "|  |  |  |  |\n",
            _VALIDATION_TABLE_HEAD + f"| Tests | `{_cell(ctx.test_command)}` | non exécuté | "
            f"grimoire standard gate run-tests --task-id {facts.task_id} écrit {test_run} |\n",
        )
    return text


def _prefill_claim_ledger(text: str, ctx: _PrefillContext) -> str:
    text = _replace_once(text, "- Task id:\n", f"- Task id: {ctx.facts.task_id}\n")
    return _replace_once(text, "- Profile:\n", f"- Profile: {ctx.profile_id}\n")


def _prefill_acceptance_record(text: str, ctx: _PrefillContext) -> str:
    facts = ctx.facts
    text = _replace_once(text, "- Task id:\n", f"- Task id: {facts.task_id}\n")
    text = _replace_once(text, "- Profile:\n", f"- Profile: {ctx.profile_id}\n")
    text = _replace_once(text, "- Deliverable:\n", f"- Deliverable: {_cell(facts.title)}\n")
    if facts.owner:
        text = _replace_once(text, "- Validator:\n", f"- Validator: {_cell(facts.owner)}\n")
    if facts.acceptance:
        rows = "".join(
            f"| AC-{index:03d} | {_cell(item)} |  | à vérifier |\n"
            for index, item in enumerate(facts.acceptance, start=1)
        )
        text = _replace_once(text, "| AC-001 |  |  | à vérifier |\n", rows)
    if ctx.test_command:
        test_run = _test_run_relpath(facts.task_id)
        text = _replace_once(
            text,
            "| Tests |  |  |\n",
            f"| Tests |  | `{_cell(ctx.test_command)}` via grimoire standard gate run-tests --task-id "
            f"{facts.task_id} ({test_run}) |\n",
        )
    return text


_PREFILL = {
    "task_envelope": _prefill_task_envelope,
    "evidence_pack": _prefill_evidence_pack,
    "claim_ledger": _prefill_claim_ledger,
    "acceptance_record": _prefill_acceptance_record,
}
