"""#1158 : la fin d'un journal de session dit-elle une erreur transitoire du fournisseur ?"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grimoire.missions.provider_failure import transient_api_error


def _write(path: Path, *entries: dict[str, object]) -> Path:
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    return path


def _assistant(text: str, *, error: bool = False, sidechain: bool = False) -> dict[str, object]:
    entry: dict[str, object] = {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }
    if error:
        entry["isApiErrorMessage"] = True
    return entry


@pytest.mark.parametrize(
    "text",
    (
        "API Error: 500 Internal Server Error (server-side)",
        "API Error: 502 Bad Gateway",
        "API Error: 503 Service Unavailable",
        "API Error: 429 Too Many Requests",
        "API Error: 529 overloaded_error",
        "Error: 500",
        '{"type":"error","status":500}',
        "the model is overloaded, try again",
    ),
)
def test_une_erreur_transitoire_en_fin_de_journal_est_rendue(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path / "s.jsonl", _assistant("bonjour"), _assistant(text, error=True))
    assert transient_api_error(path) == text


@pytest.mark.parametrize("text", ("API Error: 400 Bad Request", "API Error: 401 Unauthorized", "tout va bien", "404"))
def test_une_erreur_definitive_ou_un_tour_normal_ne_declenche_rien(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path / "s.jsonl", _assistant(text, error=text.startswith("API Error: 40")))
    assert transient_api_error(path) == ""


def test_un_tour_repris_apres_l_erreur_efface_l_erreur(tmp_path: Path) -> None:
    """Seul le dernier tour compte : un outil relancé après l'erreur prouve que la session a repris."""
    path = _write(
        tmp_path / "s.jsonl",
        _assistant("API Error: 500 Internal Server Error", error=True),
        {"type": "user", "message": {"role": "user", "content": "reprends"}},
        _assistant("je reprends le travail"),
    )
    assert transient_api_error(path) == ""


def test_un_resultat_d_outil_en_dernier_ne_prouve_pas_une_erreur(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        _assistant("API Error: 500 Internal Server Error", error=True),
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
    )
    assert transient_api_error(path) == ""


def test_les_lignes_de_metadonnees_apres_l_erreur_ne_la_cachent_pas(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        _assistant("API Error: 500 Internal Server Error", error=True),
        {"type": "system", "subtype": "stop_hook_summary"},
        {"type": "file-history-snapshot"},
    )
    assert transient_api_error(path) == "API Error: 500 Internal Server Error"


def test_une_entree_de_sous_agent_ne_compte_pas(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        _assistant("travail principal"),
        _assistant("API Error: 500 Internal Server Error", error=True, sidechain=True),
    )
    assert transient_api_error(path) == ""


def test_journal_absent_ou_illisible_rend_vide(tmp_path: Path) -> None:
    assert transient_api_error(None) == ""
    assert transient_api_error(tmp_path / "absent.jsonl") == ""
    broken = tmp_path / "broken.jsonl"
    broken.write_bytes(b"\xff\xfe not json\n")
    assert transient_api_error(broken) == ""
