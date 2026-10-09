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

__all__ = ["ClaimRowScan", "scan_claim_rows", "v0_non_governed", "verify_claim_ledger"]

_TEMPLATE_CELLS = ["CL-001", "", "fait", "", "hypothèse", "faible", "vérifier"]
_TEMPLATE_CELLS_LOWER = [c.lower() for c in _TEMPLATE_CELLS]
_ID_RE = re.compile(r"^CL-\d{3,}$")
_CANDIDATE_RE = re.compile(r"^\s*\|\s*cl-", re.IGNORECASE)
_SECTION_RE = re.compile(r"^##\s+Claims\b", re.IGNORECASE)
_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_STATUSES = frozenset({"prouvé", "hypothèse", "contredit", "réfuté"})
_DECISIONS = frozenset({"utiliser", "vérifier", "rejeter", "écarter"})


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


class ClaimRowScan:
    """Lignes candidates du registre : chacune est évaluée ou comptée non évaluée.

    ``unevaluated_cells`` garde, pour les lignes non évaluées qui ont au moins 7
    cellules (identifiant hors ``CL-NNN``, affirmation vide), leurs cellules : la
    forme fautive est signalée, mais le fond (contradictions) est tout de même
    contrôlé — une ligne mal numérotée ne s'exonère pas d'une preuve manquante.
    """

    def __init__(self) -> None:
        self.evaluated: list[tuple[str, list[str]]] = []
        self.unevaluated: list[str] = []
        self.unevaluated_cells: list[tuple[str, list[str]]] = []

    @property
    def candidate_count(self) -> int:
        return len(self.evaluated) + len(self.unevaluated)

    @property
    def unevaluated_count(self) -> int:
        return len(self.unevaluated)


def scan_claim_rows(text: str) -> ClaimRowScan:
    """Repère les lignes candidates sans aucune perte silencieuse.

    Candidate = toute ligne de tableau de la section ``## Claims`` (tout titre de
    niveau 2 qui commence par « Claims », donc aussi ``## Claims (registre)``),
    hors en-tête, séparateur et ligne modèle, ou, où que ce soit dans le
    fichier, toute ligne dont la première cellule commence par ``cl-`` (casse
    et espaces tolérés). Une ligne candidate dont l'identifiant n'est pas
    ``CL-NNN``, dont la cellule Affirmation est vide, ou qui a moins de 7
    cellules est comptée non évaluée. La ligne modèle est reconnue sans égard à
    la casse.
    """
    lines = text.splitlines()
    start = next((n for n, ln in enumerate(lines) if _SECTION_RE.match(ln.strip())), None)
    in_section: set[int] = set()
    if start is not None:
        end = next((n for n in range(start + 1, len(lines)) if re.match(r"^##\s", lines[n])), len(lines))
        in_section = {n for n in range(start + 1, end) if lines[n].strip().startswith("|")}
    pool = [ln for n, ln in enumerate(lines) if n in in_section or _CANDIDATE_RE.match(ln)]
    scan = ClaimRowScan()
    for line in pool:
        cells = _cells(line)
        if cells[0].lower() == "id" or all(_SEPARATOR_CELL_RE.match(c) for c in cells):
            continue
        if [c.lower() for c in cells] == _TEMPLATE_CELLS_LOWER:
            continue
        if len(cells) < 7:
            scan.unevaluated.append(line)
        elif not _ID_RE.match(cells[0]) or not cells[1]:
            scan.unevaluated.append(line)
            scan.unevaluated_cells.append((line, cells))
        else:
            scan.evaluated.append((line, cells))
    return scan


def v0_non_governed(root: Path, profile: StandardProfile, task_id: str) -> bool:
    """True quand *task_id* est classé V0 et le profil n'est pas ``governed``.

    Seule source du dosage V0 : le gate de revue et ``standard verify`` la
    partagent, pour que le même registre rende le même constat dans les deux.
    """
    if profile.id == "governed":
        return False
    from grimoire.core.standard_generation import STANDARD_DIR
    from grimoire.core.standard_state import _load_mapping, task_from_board

    board = _load_mapping(root / STANDARD_DIR / "task-board.yaml")
    verifiability = task_from_board(board, task_id).get("verifiability")
    klass = verifiability.get("class") if isinstance(verifiability, dict) else None
    return klass == "V0"


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
    ``review`` — sauf pour une tâche V0 hors governed (``suppress_v0``, ci-dessous).
    Ce qui est aussi une erreur, c'est une affirmation dite prouvée sans preuve, ou —
    en profil governed et production — une affirmation utilisée alors qu'elle
    n'est pas prouvée, et une synthèse laissée vide.

    ``suppress_v0`` (issue #582 lot I) éteint uniquement ``claims.empty`` :
    une tâche V0 en profil non gouverné n'a, par construction, aucune
    affirmation critique à consigner. Le gate de revue (:func:`grimoire.core.
    standard_checks.gate_review_checks.review_state_content_checks`) et
    ``standard verify`` (``verifiers.run_verifiers``) le calculent tous deux par
    :func:`v0_non_governed`, donc rendent le même constat. Une affirmation
    réellement écrite reste vérifiée comme avant, quel que soit ce drapeau.

    Le fond se contrôle quelle que soit la forme : une ligne à identifiant hors
    ``CL-NNN`` ou sans affirmation est signalée ``claims.row_invalid``, et ses
    contradictions (« prouvé » sans preuve, « utiliser » sans « prouvé ») sont
    tout de même levées. Une ligne sans affirmation ne compte pas comme évaluée.

    ``rows_only`` (issue #614) : pendant ``in_progress``, seules les
    contradictions ligne à ligne bloquent — « prouvé » sans preuve, « utiliser »
    sans « prouvé ». Une ligne en cours d'écriture (``claims.row_invalid``,
    ``claims.status_invalid``, ``claims.decision_invalid``) reste signalée mais
    en avertissement : ``Stop``, ``SubagentStop`` et ``PreCompact`` ne doivent
    pas bloquer une affirmation à moitié rédigée, que la revue refusera en
    erreur. Le registre vierge (``claims.empty``) et la synthèse non remplie
    (``claims.summary_placeholder``) sont des constats de clôture, levés à
    partir de ``review`` seulement.
    """
    rel_path = EVIDENCE_DIR / task_id / "claim-ledger.md"
    text = _text_file(root, rel_path)
    strict = profile.id in {"governed", "production"}
    severity = "error" if strict else "warning"
    # Pendant le travail, une ligne mal formée avertit ; la revue la refuse.
    form_severity = "warning" if rows_only else severity
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
            result, "claims.row_invalid", form_severity, f"Claim row is malformed: {line.strip()[:60]}", path=rel_path
        )
    # Le fond se contrôle quelle que soit la forme : les lignes à identifiant
    # fautif ou sans affirmation, déjà signalées row_invalid, y passent aussi.
    rows = scan.evaluated + scan.unevaluated_cells
    for _line, cells in rows:
        claim_id, _claim, _kind, proof, status, _confidence, decision = cells[:7]
        status, decision = status.lower(), decision.lower()
        if status not in _STATUSES:
            _add_check(
                result,
                "claims.status_invalid",
                form_severity,
                f"{claim_id} has an unknown status: {status or '(empty)'}.",
                path=rel_path,
            )
        if decision not in _DECISIONS:
            _add_check(
                result,
                "claims.decision_invalid",
                form_severity,
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
    if strict and scan.evaluated and not rows_only and "| Affirmations bloquantes non prouvées |  |" in text:
        _add_check(result, "claims.summary_placeholder", "error", "Claim ledger summary is still empty.", path=rel_path)
