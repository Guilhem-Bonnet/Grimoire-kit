"""Couverture critère -> preuve du dossier d'acceptance (W3-06, VER-10).

Avant ce module, ``_verify_acceptance_record`` ne regardait la cellule Preuve
que d'une ligne « passé » (``acceptance.passed_without_evidence``). Trois
trous restaient ouverts, tous silencieux :

- un livrable « accepté » dont un critère reste sans preuve (statut
  ``à vérifier`` ou ``partiel``, cellule Preuve vide) ;
- un critère déclaré sur le tableau de tâches (``acceptance_criteria``) qui n'a
  aucune ligne dans le dossier : il disparaît de la décision sans une trace ;
- une preuve sans critère : la ligne cite une preuve mais ne dit pas ce
  qu'elle démontre.

Deux identifiants, déclarés dans le registre des checks :

- ``acceptance.uncovered`` : un critère sans preuve. Erreur en profil
  gouverné/production quand le livrable est accepté (décision ``accepté`` dans
  le dossier, tâche ``accepted``/``released`` au tableau, ou gate évalué vers
  l'un de ces états), avertissement sinon, y compris pour un critère du tableau
  absent du dossier avant l'acceptation. Un critère ``à vérifier`` sans preuve avant cette décision est l'état
  normal d'une tâche en cours : rien n'est signalé. Un critère ``échoué`` est
  déjà porté par ``acceptance.accepted_with_failed_criterion``.
- ``evidence.orphan`` : une preuve sans critère. Avertissement : la preuve
  n'est pas fausse, elle est seulement détachée de ce qu'elle prouve.

Limite écrite : un critère du tableau est reconnu couvert si une ligne porte son
identifiant d'ordre (``AC-001`` pour le premier, la convention du scaffold) ou
son texte, comparé sans casse ni espaces multiples. Un critère reformulé sous un
autre identifiant est donc signalé absent : le message le dit, il ne devine pas.

Le module n'importe pas ``controls`` (qui l'appelle) : les lignes déjà découpées
lui sont passées.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from grimoire.core.standard_checks.base import StandardProfile, StandardVerificationResult, _add_check

__all__ = ["verify_criterion_coverage"]

_SETTLED_STATUSES = frozenset({"accepted", "released"})
_STRICT_PROFILES = frozenset({"governed", "production"})


def _normalise(text: str) -> str:
    """Texte comparable : la cellule d'un dossier remplace ``|`` par ``/`` (voir le scaffold)."""
    return " ".join(text.replace("|", "/").lower().split())


def _board_task(root: Path, task_id: str) -> dict[str, object]:
    from grimoire.core.standard_generation import STANDARD_DIR
    from grimoire.core.standard_state import _load_mapping, task_from_board

    return task_from_board(_load_mapping(root / STANDARD_DIR / "task-board.yaml"), task_id)


def _declared_criteria(task: dict[str, object]) -> list[str]:
    raw = task.get("acceptance_criteria")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item).strip()]


def verify_criterion_coverage(
    root: Path,
    profile: StandardProfile,
    task_id: str,
    rows: Sequence[Sequence[str]],
    *,
    accepted: bool,
    settled: bool = False,
    result: StandardVerificationResult,
    rel_path: Path,
) -> None:
    """Signale critères sans preuve et preuves sans critère.

    *rows* : les cellules (au moins quatre : id, critère, preuve, statut) des
    lignes de critères déjà reconnues valides par l'appelant, modèle exclu.
    *accepted* : le dossier porte une décision ``accepté``.
    *settled* : le gate évalue la transition vers ``accepted``/``released`` (état
    cible, pas encore celui du tableau).
    """
    task = _board_task(root, task_id)
    settled = accepted or settled or str(task.get("status", "")) in _SETTLED_STATUSES
    severity = "error" if settled and profile.id in _STRICT_PROFILES else "warning"

    for cells in rows:
        criterion_id, criterion, proof, status = cells[0], cells[1], cells[2], cells[3]
        if proof and not criterion:
            _add_check(
                result,
                "evidence.orphan",
                "warning",
                f"{criterion_id} cite une preuve ({proof[:60]!r}) sans dire quel critère elle démontre.",
                path=rel_path,
            )
        if settled and not proof and status != "échoué":
            _add_check(
                result,
                "acceptance.uncovered",
                severity,
                f"{criterion_id} reste sans preuve ({status or 'sans statut'}) alors que le livrable est accepté.",
                path=rel_path,
            )

    if not rows and not settled:
        # Dossier encore au modèle : `acceptance.empty` le dit déjà.
        return
    present_ids = {cells[0].strip().upper() for cells in rows}
    present_texts = {_normalise(cells[1]) for cells in rows if cells[1]}
    for index, declared in enumerate(_declared_criteria(task), start=1):
        if f"AC-{index:03d}" in present_ids or _normalise(declared) in present_texts:
            continue
        _add_check(
            result,
            "acceptance.uncovered",
            severity,
            f"Le critère AC-{index:03d} du tableau de tâches n'a aucune ligne dans le dossier d'acceptance "
            f"(reconnu par son identifiant d'ordre ou son texte) : {declared[:60]!r}.",
            path=rel_path,
        )
