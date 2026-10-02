"""``gate check --strict`` exécute lui-même ``gate run-tests`` quand aucun run frais n'existe (issue #582 lot G1).

Depuis le lot B, ``gate run-tests`` enregistre un run réel dans
``_grimoire-output/evidence/<task>/test-run.json`` avec l'empreinte de l'arbre
de travail, et ``verify``/``gate check`` relisent ce fichier. Mais c'étaient
deux commandes, et la directive de session devait les nommer toutes les deux
dans l'ordre ; un agent qui n'appelait que ``gate check --strict`` (la commande
que le hook mandate) voyait un avertissement lui demandant d'aller lancer
l'autre. Ce module ferme la boucle : ``gate check --strict`` décide s'il doit
exécuter les tests, les exécute, puis évalue comme avant — ``--no-run``
restaure l'ancien comportement.

Un run n'est lancé que si trois conditions tiennent, dans cet ordre, la moins
chère d'abord :

1. l'état de la tâche doit une preuve d'exécution (:data:`AUTO_RUN_STATES` :
   le même ensemble que celui où le gate exige le context bundle — une tâche
   ``proposed`` ou ``ready`` n'a encore rien à tester) ;
2. le projet a une commande de test connue
   (:func:`grimoire.core.execution_needs.resolve_need`) ;
3. aucun run frais n'est enregistré — **quel que soit son verdict** (empreinte
   égale à celle de l'arbre courant, :mod:`grimoire.core.standard_checks.
   tree_fingerprint`) — ``--rerun`` force malgré tout (issue #582 lot I).

Le lot H (``_scratch/bench-h2/analyse-go-js-kit-gov.md``) a mesuré que le
fingerprint-skip d'origine (lot G1) ne couvrait que le cas vert : un run rouge
identique (PATH sans toolchain Go, suite JS absente) était rejoué à chaque
``gate check --strict`` — jusqu'à 10 fois sur un seul run — pour redire le même
diagnostic, sans jamais rien apprendre de plus. Le lot I généralise le
court-circuit à tout verdict frais (vert, rouge, ou « rien collecté », voir
:mod:`grimoire.core.standard_checks.no_tests_collected`) et rend l'ancien
comportement accessible via ``--rerun`` pour qui veut forcer une nouvelle
exécution malgré un arbre inchangé.

Le hook ``Stop`` n'appelle pas ce module : il évalue les gates via
:func:`grimoire.core.agentic_standard.check_evidence_gates` et ne doit jamais
lancer une suite de tests dans le dos d'une session.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grimoire.core.execution_needs import resolve_need
from grimoire.core.standard_checks.base import StandardVerificationResult, _add_check, _text_file
from grimoire.core.standard_checks.controls import _load_recorded_test_run, acceptance_test_run_relpath
from grimoire.core.standard_checks.no_tests_collected import has_no_test_justification
from grimoire.core.standard_generation import EVIDENCE_DIR, STANDARD_DIR, normalize_task_id

__all__ = [
    "AUTO_RUN_STATES",
    "GateTestRunOutcome",
    "board_state_of_task",
    "ensure_fresh_test_run",
    "resolve_gate_check_task_id",
    "verify_recorded_test_run_is_green",
]

#: États du board où ``gate check --strict`` exécute les tests s'il le faut.
AUTO_RUN_STATES: frozenset[str] = frozenset({"in_progress", "review", "accepted", "released"})

#: États où l'absence de test **sans justification** reste une erreur, même
#: pour un profil strict qui accepterait ailleurs le simple avertissement
#: (issue #582 lot I, point I-1) — une tâche qui entre en revue ou est
#: acceptée sans preuve d'exécution ni justification écrite n'a rien de plus
#: à montrer plus tard qu'aujourd'hui : matrice complète au docstring de
#: :func:`verify_recorded_test_run_is_green`.
_STRICT_NO_TEST_STATES: frozenset[str] = frozenset({"review", "accepted", "released"})
_STRICT_PROFILES: frozenset[str] = frozenset({"governed", "production"})


@dataclass(frozen=True, slots=True)
class GateTestRunOutcome:
    """Ce que ``gate check`` a décidé à propos des tests, et pourquoi.

    ``reason`` vaut ``state_owes_no_run``, ``no_test_command``, ``fresh_run``
    ou ``executed`` ; ``command`` est la commande résolue (vide si aucune) ;
    ``ok``/``exit_code`` ne sont renseignés que pour ``executed``.
    """

    ran: bool
    reason: str
    command: str = ""
    ok: bool | None = None
    exit_code: int | None = None
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": self.ran,
            "reason": self.reason,
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "path": str(self.path) if self.path is not None else None,
        }


def board_state_of_task(project_root: Path, task_id: str, *, target_state: str | None = None) -> str:
    """L'état que ``check_evidence_gates`` évaluera : *target_state* s'il est donné, sinon la colonne du board."""
    if target_state:
        return target_state
    from grimoire.core.standard_state import _load_mapping, task_from_board

    board = _load_mapping(project_root.resolve() / STANDARD_DIR / "task-board.yaml")
    return str(task_from_board(board, normalize_task_id(task_id)).get("status") or "")


def resolve_gate_check_task_id(project_root: Path, task_id: str) -> tuple[str, dict[str, Any] | None]:
    """Tâche que ``gate check`` doit évaluer quand ``--task-id`` est omis (issue #642 lot K).

    Délègue à :func:`grimoire.core.standard_state.resolve_active_task` — variable
    d'environnement, puis réclamation active du Mission Ledger, puis carte
    ``in_progress`` unique du board, puis ``bootstrap`` faute de tout signal.
    Quand ce dernier repli retombe sur un ``bootstrap`` qu'un board réel ne
    déclare pas, évaluer les gates reviendrait à juger une tâche fantôme
    (``rust/poker run2`` du lot J du banc kit-gov : 22 tours perdus sur
    exactement ce cas) — la fonction rend alors un check d'erreur au lieu
    d'un identifiant, et l'appelant doit s'arrêter là plutôt qu'appeler
    :func:`grimoire.core.agentic_standard.check_evidence_gates`.

    Rend ``(task_id, None)`` quand *task_id* est fourni explicitement ou que
    la résolution aboutit à une tâche réelle ; ``(task_id, check)`` sinon,
    *check* ayant la forme d'une entrée ``checks`` de ``gate check``.
    """
    if task_id:
        return task_id, None
    from grimoire.core.standard_state import (
        TASK_BOARD_RELPATH,
        _load_mapping,
        board_omits_task,
        resolve_active_task,
        task_from_board,
    )

    active = resolve_active_task(project_root)
    resolved = active.task_id
    if not active.is_fallback:
        return resolved, None
    board_path = project_root.resolve() / TASK_BOARD_RELPATH
    board = _load_mapping(board_path)
    if not board_omits_task(project_root, task_from_board(board, resolved)):
        return resolved, None
    message = (
        f"Aucun --task-id fourni, aucune carte in_progress sur {board_path}, et "
        f"{resolved!r} n'est pas une tâche du board : impossible d'évaluer les gates "
        "d'une tâche fantôme. Déclare la tâche (`grimoire task add ...`) et réclame-la "
        "(`grimoire task claim <task-id>`), ou passe `--task-id <task-id>` explicitement."
    )
    return resolved, {
        "id": "gate.no_task_to_evaluate",
        "severity": "error",
        "message": message,
        "path": str(board_path),
    }


def _recorded_run_matching_tree(root: Path, task_id: str) -> dict[str, Any] | None:
    """Le dernier run enregistré, *quel que soit son verdict*, si son empreinte colle à l'arbre courant.

    Généralisation, issue #582 lot I, de l'ancien ``_recorded_run_is_fresh_and_green`` :
    celui-ci ne court-circuitait que le cas vert, rejouant sans fin un rouge
    identique (voir le docstring du module). ``None`` sans distinguer
    « aucun run » de « empreinte périmée » : dans les deux cas, le gate doit
    (ré)exécuter — seul l'appelant a besoin du run pour rapporter son verdict.
    """
    run = _load_recorded_test_run(root, task_id)
    if run is None:
        return None
    stored = run.get("tree_fingerprint")
    if not stored:
        return None
    from grimoire.core.standard_checks.tree_fingerprint import compute_tree_fingerprint

    return run if stored == compute_tree_fingerprint(root) else None


def _recorded_run_is_fresh_and_green(root: Path, task_id: str) -> bool:
    """Compatibilité : le seul cas que l'ancien mécanisme court-circuitait."""
    run = _recorded_run_matching_tree(root, task_id)
    return run is not None and run.get("ok") is True


def ensure_fresh_test_run(project_root: Path, *, task_id: str, state: str, rerun: bool = False) -> GateTestRunOutcome:
    """Exécute et enregistre les tests si (et seulement si) le gate en a besoin — voir le module.

    ``rerun=True`` (``gate check --strict --rerun``, issue #582 lot I) ignore
    un run enregistré dont l'empreinte colle à l'arbre courant et force une
    nouvelle exécution — le seul moyen de sortir du cas où un diagnostic
    environnemental (rouge identique, rien collecté) resterait figé alors que
    la cause réelle (toolchain installée, suite ajoutée) a été corrigée sans
    changer l'arbre suivi par l'empreinte (ex. un ``$PATH`` mis à jour).
    """
    root = project_root.resolve()
    normalized_task_id = normalize_task_id(task_id)
    if state not in AUTO_RUN_STATES:
        return GateTestRunOutcome(ran=False, reason="state_owes_no_run")
    need = resolve_need("test-runner", root)
    if not need.resolved or need.command is None:
        return GateTestRunOutcome(ran=False, reason="no_test_command")
    if not rerun:
        matching = _recorded_run_matching_tree(root, normalized_task_id)
        if matching is not None:
            return GateTestRunOutcome(
                ran=False,
                reason="fresh_run",
                command=str(matching.get("command") or need.command),
                ok=matching.get("ok"),
                exit_code=matching.get("exit_code"),
                path=root / acceptance_test_run_relpath(normalized_task_id),
            )
    # Importé ici : `record_acceptance_test_run` tire `missions.dispatch`
    # (~60 ms d'imports mesurés), un coût que seule l'exécution réelle justifie.
    from grimoire.core.standard_checks.acceptance_test_run import record_acceptance_test_run

    result = record_acceptance_test_run(root, task_id=normalized_task_id)
    return GateTestRunOutcome(
        ran=True,
        reason="executed",
        command=result.command,
        ok=result.ok,
        exit_code=result.exit_code,
        path=result.path,
    )


def verify_recorded_test_run_is_green(
    root: Path, task_id: str, result: StandardVerificationResult, *, state: str
) -> None:
    """Issue #582 lot G1 (rouge) + lot I (rien collecté) : un run enregistré, frais, ferme ou dose le gate.

    Déplacé depuis :mod:`grimoire.core.standard_checks.controls` (issue #582
    lot I) : ce fichier est le point de rencontre naturel avec
    :func:`ensure_fresh_test_run`, qui produit exactement le run que cette
    fonction relit — ``controls.py`` approchait le seuil du ratchet de taille
    (R2, ``scripts/check-code-ratchet.py``) et n'avait plus de marge pour
    cette fonction ni pour la suivante.

    ``gate check --strict`` exécute désormais lui-même ``gate run-tests`` dès
    que la tâche doit une preuve d'exécution ; le verdict de ce run doit
    fermer le gate, sinon l'exécution intégrée ne serait qu'un fichier de
    plus. Seul un run *frais* (même empreinte d'arbre) compte : un run rouge
    périmé peut avoir été corrigé depuis (``acceptance.test_run_stale``, sur
    le chemin de l'acceptance). Aucun run enregistré : rien ici — c'est
    ``acceptance.passed_without_test_run`` qui porte ce cas.

    Trois verdicts possibles pour un run frais (``run["ok"]``) :

    - ``True`` (vert) : rien à dire, retour immédiat.
    - ``False`` (rouge) : ``acceptance.test_run_failed``, toujours une
      erreur — un code qui ne compile ou ne passe pas est un motif de fond,
      quel que soit le profil ou l'état visé.
    - ``None`` (rien collecté, :mod:`grimoire.core.standard_checks.
      no_tests_collected`) : ``acceptance.no_tests_collected``. Matrice
      état × profil pour ce seul cas :

      +-----------------------------+------------------+-------------------+
      | Justifié (``sans test :``)  | Profil non strict | Profil strict     |
      +=============================+==================+===================+
      | oui                         | silencieux       | silencieux        |
      +-----------------------------+------------------+-------------------+
      | non, état hors revue/accepté/released | avertissement | avertissement |
      +-----------------------------+------------------+-------------------+
      | non, état review/accepted/released    | avertissement | **erreur**     |
      +-----------------------------+------------------+-------------------+

      Un profil strict (``governed``/``production``) qui laisse une tâche
      entrer en revue ou être acceptée sans preuve d'exécution ni
      justification écrite n'a rien de plus à montrer plus tard — c'est le
      seul cas où l'absence de test doit bloquer, pas la simple absence en
      cours de travail (``in_progress``), où rien n'interdit encore d'écrire
      le test ou la justification avant de conclure.
    """
    run = _recorded_run_matching_tree(root, task_id)
    if run is None or run.get("ok") is True:
        return
    command = str(run.get("command") or "")
    exit_code = run.get("exit_code")
    path = acceptance_test_run_relpath(task_id)
    if run.get("ok") is None:
        record_path = EVIDENCE_DIR / task_id / "acceptance-record.md"
        if has_no_test_justification(_text_file(root, record_path)):
            return
        strict = result.profile in _STRICT_PROFILES and state in _STRICT_NO_TEST_STATES
        _add_check(
            result,
            "acceptance.no_tests_collected",
            "error" if strict else "warning",
            f"{command!r} n'a collecté aucun test (code de sortie {exit_code}). Ce n'est pas un échec de la "
            f"tâche. Si la suite existe sous une autre commande, déclare-la dans `needs.commands.test-runner` "
            f"(project-context.yaml).",
            path=path,
        )
        return
    _add_check(
        result,
        "acceptance.test_run_failed",
        "error",
        f"Le dernier run de test enregistré est rouge ({command!r}, code de sortie {exit_code}) "
        f"sur l'arbre courant : corrigez les tests puis relancez "
        f"`grimoire standard gate run-tests --task-id {task_id}` (ou `gate check --strict`, qui le relance).",
        path=path,
    )
