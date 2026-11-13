"""Section 10 de la référence agentique : chaque écart ouvert a un porteur.

La section 10 de ``framework/agentic-industry-reference.md`` est la carte de
positionnement Grimoire ↔ industrie. Un statut « à combler » ou « à cadrer »
sans issue liée ni décision « hors périmètre » est une promesse sans porteur :
elle ne se rouvre jamais et ne se ferme jamais. Ce test lit le document
lui-même et fait échouer la CI dans ce cas (critère de clôture de #637).

Il vérifie aussi que les lignes revérifiées le 2026-11-12 citent un fichier
qui existe réellement dans le dépôt — une preuve par un chemin mort n'est pas
une preuve.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "framework" / "agentic-industry-reference.md"

#: Statuts qui exigent une suite : une issue liée ou une décision explicite.
OPEN_STATUSES = ("à combler", "à cadrer")
ISSUE_REF = re.compile(r"#\d+")
SECTION_10 = re.compile(
    r"^## [^\n]*\b10\. Correspondance Grimoire[^\n]*\n(?P<body>.*?)(?=^## )",
    re.MULTILINE | re.DOTALL,
)

#: Preuves citées par les lignes revérifiées le 2026-11-12, en chemin de paquet.
#: Le chemin doit figurer dans la ligne et exister sous ``src/``.
REVIEWED_PROOFS = {
    "provider-routing-contract": "grimoire/core/standard_checks/dispatch_cost_slo.py",
    "Agent Skills du kit": "grimoire/hosts/skill_spec.py",
    "Hooks Forge via gateway": "grimoire/hosts/events.py",
    "Sécurité : garde des surfaces": "grimoire/core/standard_checks/plan_then_execute_check.py",
}


def _section_body() -> str:
    match = SECTION_10.search(REFERENCE.read_text(encoding="utf-8"))
    assert match, "section 10 (« Correspondance Grimoire ↔ industrie ») introuvable"
    return match.group("body")


def _table_rows(body: str) -> list[str]:
    return [line for line in body.splitlines() if line.lstrip().startswith("|")]


def _status(row: str) -> str:
    """Dernière cellule d'une ligne de tableau, sans le tube final."""
    return row.rstrip().rstrip("|").rsplit("|", 1)[-1].strip()


def _open_rows_without_follow_up(rows: list[str]) -> list[str]:
    """Lignes dont le statut ouvert ne nomme ni issue ``#NNN`` ni « hors périmètre »."""
    missing: list[str] = []
    for row in rows:
        status = _status(row)
        if not any(word in status for word in OPEN_STATUSES):
            continue
        if ISSUE_REF.search(row) or "hors périmètre" in row:
            continue
        missing.append(row)
    return missing


def test_the_section_10_table_is_present_and_populated():
    rows = _table_rows(_section_body())
    assert len(rows) > 15, f"section 10 : {len(rows)} ligne(s) de tableau seulement"
    assert any("Protocole ou mécanisme Grimoire" in row for row in rows), (
        "section 10 : l'en-tête du tableau de correspondance est introuvable"
    )


def test_every_open_ecart_names_an_issue_or_a_reason():
    missing = _open_rows_without_follow_up(_table_rows(_section_body()))
    assert not missing, (
        "lignes « à combler »/« à cadrer » sans issue liée ni mention « hors périmètre » :\n" + "\n".join(missing)
    )


def test_an_open_status_without_follow_up_is_rejected():
    rows = ["| a | b | écart | à combler |"]
    assert _open_rows_without_follow_up(rows) == rows


def test_an_open_status_with_an_issue_is_accepted():
    rows = ["| a | b | écart | à combler (#637) |"]
    assert _open_rows_without_follow_up(rows) == []


def test_an_out_of_scope_status_needs_no_issue():
    rows = ["| a | b | écart | hors périmètre (2026-10-25) |"]
    assert _open_rows_without_follow_up(rows) == []


def test_a_closed_status_needs_no_follow_up():
    rows = ["| a | b | écart | aligné |"]
    assert _open_rows_without_follow_up(rows) == []


def test_reviewed_rows_cite_a_living_proof():
    rows = _table_rows(_section_body())
    for marker, rel_path in REVIEWED_PROOFS.items():
        row = next((candidate for candidate in rows if marker in candidate), None)
        assert row is not None, f"ligne {marker!r} introuvable dans la section 10"
        assert rel_path in row, f"{marker} : la preuve {rel_path} n'est pas citée"
        assert (ROOT / "src" / rel_path).is_file(), f"{marker} : {rel_path} n'existe pas"
