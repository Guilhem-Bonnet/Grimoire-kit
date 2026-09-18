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
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_checks.base import (
    StandardCheck,
    StandardProfile,
    StandardVerificationResult,
)
from grimoire.core.standard_checks.controls import _verify_acceptance_record
from grimoire.core.standard_checks.verifiers import _verify_claim_ledger, _verify_evidence_pack

__all__ = ["review_state_content_checks"]


def review_state_content_checks(root: Path, profile: StandardProfile, task_id: str) -> tuple[StandardCheck, ...]:
    """Constats de contenu pour *task_id*, mêmes règles que celles rendues par `standard verify`."""
    try:
        from grimoire.core.standard_checks.evidence_journal import (
            regenerate_observed_inventory_section,
        )

        regenerate_observed_inventory_section(root, task_id)
    except Exception:  # noqa: S110 — projection best-effort, jamais au prix du gate lui-même
        pass

    checks: list[StandardCheck] = []

    evidence_pack_result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_evidence_pack(root, task_id, evidence_pack_result)
    checks.extend(evidence_pack_result.checks)

    acceptance_result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_acceptance_record(root, profile, task_id, acceptance_result)
    checks.extend(acceptance_result.checks)

    claim_ledger_result = StandardVerificationResult(profile=profile.id, project_root=root)
    _verify_claim_ledger(root, profile, task_id, claim_ledger_result)
    checks.extend(claim_ledger_result.checks)

    return tuple(checks)
