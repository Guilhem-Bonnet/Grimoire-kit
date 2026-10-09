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

import re
from pathlib import Path

from grimoire.core.standard_checks.base import StandardProfile, StandardVerificationResult, _add_check, _text_file
from grimoire.core.standard_generation import EVIDENCE_DIR

__all__ = ["ClaimRowScan", "scan_claim_rows", "verify_claim_ledger"]

_TEMPLATE_CELLS = ["CL-001", "", "fait", "", "hypothèse", "faible", "vérifier"]
_ID_RE = re.compile(r"^CL-\d{3,}$")
_CANDIDATE_RE = re.compile(r"^\s*\|\s*cl-", re.IGNORECASE)
_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_STATUSES = frozenset({"prouvé", "hypothèse", "contredit", "réfuté"})
_DECISIONS = frozenset({"utiliser", "vérifier", "rejeter", "écarter"})


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


class ClaimRowScan:
    """Lignes candidates du registre : chacune est évaluée ou comptée non évaluée."""

    def __init__(self) -> None:
        self.evaluated: list[tuple[str, list[str]]] = []
        self.unevaluated: list[str] = []

    @property
    def candidate_count(self) -> int:
        return len(self.evaluated) + len(self.unevaluated)

    @property
    def unevaluated_count(self) -> int:
        return len(self.unevaluated)


def scan_claim_rows(text: str) -> ClaimRowScan:
    """Repère les lignes candidates sans aucune perte silencieuse.

    Candidate = toute ligne de tableau de la section ``## Claims`` (hors en-tête,
    séparateur et ligne modèle), ou, où que ce soit dans le fichier, toute ligne
    dont la première cellule commence par ``cl-`` (casse et espaces tolérés). Une ligne
    candidate dont l'identifiant n'est pas ``CL-NNN`` ou qui a moins de 7
    cellules est comptée non évaluée.
    """
    lines = text.splitlines()
    start = next((n for n, ln in enumerate(lines) if re.match(r"^##\s+Claims\s*$", ln.strip(), re.IGNORECASE)), None)
    in_section: set[int] = set()
    if start is not None:
        end = next((n for n in range(start + 1, len(lines)) if lines[n].startswith("## ")), len(lines))
        in_section = {n for n in range(start + 1, end) if lines[n].strip().startswith("|")}
    pool = [ln for n, ln in enumerate(lines) if n in in_section or _CANDIDATE_RE.match(ln)]
    scan = ClaimRowScan()
    for line in pool:
        cells = _cells(line)
        if cells[0].lower() == "id" or all(_SEPARATOR_CELL_RE.match(c) for c in cells):
            continue
        if cells == _TEMPLATE_CELLS:
            continue
        if len(cells) < 7 or not _ID_RE.match(cells[0]):
            scan.unevaluated.append(line)
        else:
            scan.evaluated.append((line, cells))
    return scan


def verify_claim_ledger(
    root: Path,
    profile: StandardProfile,
    task_id: str,
    result: StandardVerificationResult,
    *,
    suppress_v0: bool = False,
    rows_only: bool = False,
) -> None:
    """AG-QUA-002 : une affirmation critique sans preuve reste une hypothèse.

    Un registre vierge, vidé ou absent est un avertissement (starter,
    controlled, orchestrated) mais une erreur en governed et production dès
    ``review``. Ce qui est aussi une erreur, c'est une affirmation dite prouvée sans preuve, ou —
    en profil governed et production — une affirmation utilisée alors qu'elle
    n'est pas prouvée, et une synthèse laissée vide.

    ``suppress_v0`` (issue #582 lot I) éteint uniquement ``claims.empty`` :
    une tâche V0 en profil non gouverné n'a, par construction, aucune
    affirmation critique à consigner — voir :func:`grimoire.core.
    standard_checks.gate_review_checks.review_state_content_checks`, seul
    appelant qui le passe. Une affirmation réellement écrite reste vérifiée
    comme avant, quel que soit ce drapeau.

    ``rows_only`` (issue #614) : pendant ``in_progress``, seules les
    contradictions ligne à ligne comptent — « prouvé » sans preuve, « utiliser »
    sans « prouvé ». Le registre vierge (``claims.empty``) et la synthèse non
    remplie (``claims.summary_placeholder``) sont des constats de clôture,
    levés à partir de ``review`` seulement.
    """
    rel_path = EVIDENCE_DIR / task_id / "claim-ledger.md"
    text = _text_file(root, rel_path)
    strict = profile.id in {"governed", "production"}
    severity = "error" if strict else "warning"
    if not text.strip():
        if not suppress_v0 and not rows_only:
            _add_check(result, "claims.empty", severity, "Claim ledger is missing or empty.", path=rel_path)
        return
    scan = scan_claim_rows(text)
    if not scan.candidate_count and not suppress_v0 and not rows_only:
        _add_check(result, "claims.empty", severity, "Claim ledger still holds only the template row.", path=rel_path)
    if scan.candidate_count:
        _add_check(
            result,
            "claims.summary",
            "info",
            f"{scan.candidate_count} claim rows, {len(scan.evaluated)} evaluated, "
            f"unevaluated_count={scan.unevaluated_count}.",
            path=rel_path,
        )
    for line in scan.unevaluated:
        _add_check(
            result, "claims.row_invalid", severity, f"Claim row is malformed: {line.strip()[:60]}", path=rel_path
        )
    rows = scan.evaluated
    for _line, cells in rows:
        claim_id, _claim, _kind, proof, status, _confidence, decision = cells[:7]
        status, decision = status.lower(), decision.lower()
        if status not in _STATUSES:
            _add_check(
                result,
                "claims.status_invalid",
                severity,
                f"{claim_id} has an unknown status: {status or '(empty)'}.",
                path=rel_path,
            )
        if decision not in _DECISIONS:
            _add_check(
                result,
                "claims.decision_invalid",
                severity,
                f"{claim_id} has an unknown decision: {decision or '(empty)'}.",
                path=rel_path,
            )
        if status == "prouvé" and not proof:
            _add_check(
                result,
                "claims.proved_without_evidence",
                "error",
                f"{claim_id} is marked prouvé with no source or evidence.",
                path=rel_path,
            )
        if decision == "utiliser" and status != "prouvé":
            _add_check(
                result,
                "claims.used_unproved",
                severity,
                f"{claim_id} is used while its status is {status}.",
                path=rel_path,
            )
    if strict and rows and not rows_only and "| Affirmations bloquantes non prouvées |  |" in text:
        _add_check(result, "claims.summary_placeholder", "error", "Claim ledger summary is still empty.", path=rel_path)
