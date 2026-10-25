"""R34 (AG-ORC-003) : un ``handoffs:`` vers un rôle inexistant échoue ``standard verify``.

Contrôle négatif d'abord : chaque entrée fautive doit lever son check ; le
gabarit livré, lui, n'en lève aucun.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.core.agentic_standard import setup_standard_profile, verify_standard_profile


def _ids(root: Path) -> set[str]:
    return {c.id for c in verify_standard_profile(root).checks if c.id.startswith("orchestration.handoff")}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    setup_standard_profile(tmp_path, profile_id="orchestrated", project_name="Demo")
    return tmp_path


def _policy(root: Path) -> Path:
    return root / "_grimoire/standard/orchestration-policy.yaml"


def _set_handoffs(root: Path, block: str) -> None:
    path = _policy(root)
    text = path.read_text(encoding="utf-8")
    start = text.index("handoffs:")
    end = text.index("# Travail en cours borné")
    path.write_text(text[:start] + block + text[end:], encoding="utf-8")


def test_le_gabarit_livre_ne_declenche_aucun_check_handoff(project: Path) -> None:
    assert _ids(project) == set()


def test_cible_inexistante_echoue(project: Path) -> None:
    _set_handoffs(project, "handoffs:\n  - from: planner\n    to: fantome\n    requires: [task_id]\n")
    result = verify_standard_profile(project)
    bad = [c for c in result.checks if c.id == "orchestration.handoff_role_unknown"]
    assert bad
    assert all(c.severity == "error" for c in bad)
    assert "fantome" in bad[0].message


def test_source_inexistante_echoue(project: Path) -> None:
    _set_handoffs(project, "handoffs:\n  - from: fantome\n    to: planner\n")
    assert "orchestration.handoff_role_unknown" in _ids(project)


@pytest.mark.parametrize(
    "block",
    [
        "handoffs: planner\n",
        "handoffs:\n  - just-a-string\n",
        "handoffs:\n  - from: planner\n",
    ],
)
def test_forme_invalide_echoue(project: Path, block: str) -> None:
    _set_handoffs(project, block)
    assert "orchestration.handoff_invalid" in _ids(project)


def test_pas_de_handoffs_est_legitime(project: Path) -> None:
    _set_handoffs(project, "")
    assert _ids(project) == set()


def test_la_note_de_trou_de_verification_a_disparu() -> None:
    from grimoire.data import framework_path

    text = (framework_path() / "agentic-standard" / "traceability.yaml").read_text(encoding="utf-8")
    assert "ne lit que roles" not in text
    assert "Ne lit que roles" not in text


def test_cible_en_liste_est_refusee_explicitement(project: Path) -> None:
    """Le modèle déclare un rôle par bout : une liste n'est pas devinée, elle est refusée (et nommée)."""
    _set_handoffs(project, "handoffs:\n  - from: planner\n    to: [context_orchestrator, release_governor]\n")
    result = verify_standard_profile(project)
    bad = [c for c in result.checks if c.id == "orchestration.handoff_invalid"]
    assert bad
    assert "single role_id" in bad[0].message
