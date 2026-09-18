"""``_verify_claim_ledger`` (AG-QUA-002), extrait de ``verifiers.py`` (issue #582 lot I).

Extrait dans son propre module — pas ajouté à ``verifiers.py`` — pour
respecter le ratchet de taille (``scripts/check-code-ratchet.py``, R2 :
``src/**/*.py`` ne peut franchir 1500 lignes sans devenir grandfathered) :
``verifiers.py`` n'avait plus la marge pour porter le paramètre de dosage V0
ajouté ici. Importé à la fois par ``verifiers.run_verifiers`` (``standard
verify``) et par ``gate_review_checks.review_state_content_checks`` (``gate
check --strict``) — aucun des deux n'importe l'autre, pas de cycle.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.core.standard_checks.base import StandardProfile, StandardVerificationResult, _add_check, _text_file
from grimoire.core.standard_generation import EVIDENCE_DIR

__all__ = ["verify_claim_ledger"]


def verify_claim_ledger(
    root: Path, profile: StandardProfile, task_id: str, result: StandardVerificationResult, *, suppress_v0: bool = False
) -> None:
    """AG-QUA-002 : une affirmation critique sans preuve reste une hypothèse.

    Un registre encore vierge est un avertissement : il attend d'être rempli.
    Ce qui est une erreur, c'est une affirmation dite prouvée sans preuve, ou —
    en profil governed et production — une affirmation utilisée alors qu'elle
    n'est pas prouvée, et une synthèse laissée vide.

    ``suppress_v0`` (issue #582 lot I) éteint uniquement ``claims.empty`` :
    une tâche V0 en profil non gouverné n'a, par construction, aucune
    affirmation critique à consigner — voir :func:`grimoire.core.
    standard_checks.gate_review_checks.review_state_content_checks`, seul
    appelant qui le passe. Une affirmation réellement écrite reste vérifiée
    comme avant, quel que soit ce drapeau.
    """
    rel_path = EVIDENCE_DIR / task_id / "claim-ledger.md"
    text = _text_file(root, rel_path)
    if not text:
        return
    strict = profile.id in {"governed", "production"}
    template_row = "| CL-001 |  | fait |  | hypothèse | faible | vérifier |"
    rows = [line for line in text.splitlines() if line.startswith("| CL-") and line.strip() != template_row]
    if not rows and not suppress_v0:
        _add_check(result, "claims.empty", "warning", "Claim ledger still holds only the template row.", path=rel_path)
    for line in rows:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            _add_check(result, "claims.row_invalid", "warning", f"Claim row is malformed: {line[:60]}", path=rel_path)
            continue
        claim_id, _claim, _kind, proof, status, _confidence, decision = cells[:7]
        if status == "prouvé" and not proof:
            _add_check(
                result, "claims.proved_without_evidence", "error",
                f"{claim_id} is marked prouvé with no source or evidence.", path=rel_path,
            )
        if decision == "utiliser" and status != "prouvé":
            _add_check(
                result, "claims.used_unproved", "error" if strict else "warning",
                f"{claim_id} is used while its status is {status}.", path=rel_path,
            )
    if strict and rows and "| Affirmations bloquantes non prouvées |  |" in text:
        _add_check(result, "claims.summary_placeholder", "error", "Claim ledger summary is still empty.", path=rel_path)
