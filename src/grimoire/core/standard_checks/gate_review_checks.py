"""Constats de contenu que `gate check` évalue aux états review/accepted/released (issue #582 lot G3).

Extrait de `agentic_standard.check_evidence_gates` pour respecter le ratchet
de taille (ce fichier grandfathered ne peut plus grossir — même motif que
l'extraction de `standard_task_scaffold.py`/`gate_test_run.py` au lot G1).

Regroupe trois vérificateurs déjà utilisés par `standard verify`
(`run_verifiers`), réappliqués ici sur le chemin que la directive de session
mandate réellement (`gate check --strict`) — jamais `verify`, qu'un agent
gouverné n'a plus à appeler depuis ce lot :

- la projection du journal d'actions observées (lot G2) dans
  `evidence-pack.md`, puis `_verify_evidence_pack` ;
- `_verify_acceptance_record` (lot B) : un critère « passé » sans run de
  test réel enregistré ;
- `_verify_claim_ledger` (lot G3) : une affirmation marquée « utiliser »
  sans être « prouvée » (AG-QUA-002). `claim_ledger` est listé depuis le
  lot G1 dans `standard_checks.gate_remedy.GATE_ARTIFACT_KEYS` et scaffoldé
  pour toute tâche (chaque profil le requiert dans
  `framework/agentic-standard/profile-map.yaml`), mais `check_evidence_gates`
  n'en vérifiait ni la présence ni le contenu avant ce lot — seul
  `standard verify` le faisait, un chemin qu'un agent qui suit la directive
  au pied de la lettre ne prend jamais.

Issue #582 lot I (dosage V0) : une tâche classée V0 (:mod:`grimoire.missions.
verifiability`) dont le profil n'est pas ``governed`` n'a, par construction,
aucun jugement humain à consigner — le gate suffit. `_v0_non_governed` lit la
classe déjà projetée sur le board (`missions.board`, jamais recalculée ici :
une seule source de vérité) et éteint ``acceptance.decision_pending`` et
``claims.empty`` pour ce seul cas, jamais pour V1/V2, jamais en profil
gouverné.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_checks.base import (
    StandardCheck,
    StandardProfile,
    StandardVerificationResult,
)
from grimoire.core.standard_checks.claim_ledger_verify import verify_claim_ledger as _verify_claim_ledger
from grimoire.core.standard_checks.controls import _verify_acceptance_record
from grimoire.core.standard_checks.verifiers import _verify_evidence_pack

__all__ = ["in_progress_content_checks", "review_state_content_checks"]


def in_progress_content_checks(root: Path, profile: StandardProfile, task_id: str) -> tuple[StandardCheck, ...]:
    """Constats de contenu pendant ``in_progress`` (issue #614) : le claim-ledger, lignes seulement.

    Une affirmation « utiliser » non prouvée pèse sur les décisions pendant le
    travail, pas seulement à la revue ; avant ce lot elle passait
    ``gate check --strict`` sans un mot jusqu'au passage en ``review``. Les
    constats de clôture (registre vierge, synthèse vide) restent à la revue —
    les lever ici bloquerait toute tâche gouvernée dès sa première minute.
    """
    result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_claim_ledger(root, profile, task_id, result, rows_only=True)
    return tuple(result.checks)


def _v0_non_governed(root: Path, profile: StandardProfile, task_id: str) -> bool:
    """True quand *task_id* est classé V0 et le profil n'est pas ``governed``."""
    if profile.id == "governed":
        return False
    from grimoire.core.standard_generation import STANDARD_DIR
    from grimoire.core.standard_state import _load_mapping, task_from_board

    board = _load_mapping(root / STANDARD_DIR / "task-board.yaml")
    verifiability = task_from_board(board, task_id).get("verifiability")
    klass = verifiability.get("class") if isinstance(verifiability, dict) else None
    return klass == "V0"


def review_state_content_checks(root: Path, profile: StandardProfile, task_id: str) -> tuple[StandardCheck, ...]:
    """Constats de contenu pour *task_id*, mêmes règles que celles rendues par `standard verify`."""
    try:
        from grimoire.core.standard_checks.evidence_journal import (
            regenerate_observed_inventory_section,
        )

        regenerate_observed_inventory_section(root, task_id)
    except Exception:  # noqa: S110 — projection best-effort, jamais au prix du gate lui-même
        pass

    suppress_v0 = _v0_non_governed(root, profile, task_id)
    checks: list[StandardCheck] = []

    evidence_pack_result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_evidence_pack(root, task_id, evidence_pack_result)
    checks.extend(evidence_pack_result.checks)

    acceptance_result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_acceptance_record(root, profile, task_id, acceptance_result, suppress_v0=suppress_v0)
    checks.extend(acceptance_result.checks)

    claim_ledger_result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_claim_ledger(root, profile, task_id, claim_ledger_result, suppress_v0=suppress_v0)
    checks.extend(claim_ledger_result.checks)

    return tuple(checks)
